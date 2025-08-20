import logging
import os
import shutil
import time
from datetime import datetime
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from fractions import Fraction
from typing import Union
import oss2

import docker
from celery.contrib.abortable import AbortableTask, AbortableAsyncResult
from fastapi import HTTPException

from utils import (
    remove_odm_task,
    commit_odm_task,
    OdmJobStatus,
    OdmState,
    donwload_odm_all_zip,
    get_odm_report_output_files,
    OdmUploadState,
    get_content_length,
    commint_report
)
from worker.app import app
from models import with_db_session, RsdmJobs, OdmReport

logger = logging.getLogger(__name__)


class TaskAbortedException(Exception):
    """Exception raised when a Celery task is aborted by user"""
    pass


def _get_upload_progress(total_progress: dict):
    """
    Calculate the overall upload progress based on the total progress of each file.
    """
    uploaded_bytes_total = sum(value[0] for value in total_progress.values())
    total_bytes_total = sum(value[1] for value in total_progress.values())

    if total_bytes_total == 0:
        return 0.0
    if total_bytes_total == uploaded_bytes_total:
        return 100.00

    percentage = (Decimal(uploaded_bytes_total) / Decimal(total_bytes_total)) * 100
    return percentage.quantize(Decimal('0.01'), rounding=ROUND_DOWN)


def _update_report_state(celery_task_id: str, upload_state: OdmUploadState):
    """
    Update report state in database with caching to reduce frequent writes.
    Args:
        celery_task_id: The Celery task ID
        upload_state (OdmUploadState): The current state of the ODM report upload

    """
    with with_db_session() as db:
        report: OdmReport = db.query(OdmReport).filter(OdmReport.celery_task_id == celery_task_id).first()
        if report:
            report.state = upload_state.state
            report.err_msg = upload_state.error
            report.update_at = datetime.now()
            report.progress = _get_upload_progress(upload_state.total_progress)

            db.commit()
            db.refresh(report)
            logger.info("Updated ODM report state in database: %s", upload_state.dict())


def _update_odm_state(celery_task_id: str, odm_state: OdmState):
    """
    Update task progress in database with caching to reduce frequent writes.

    Args:
        celery_task_id: The Celery task ID
        odm_state (OdmState): The current state of the ODM job
    """
    with with_db_session() as db:
        job: RsdmJobs = db.query(RsdmJobs).filter(RsdmJobs.celery_task_id == celery_task_id).first()
        if job:
            job.state = odm_state.state
            job.odm_host = odm_state.host
            job.progress = odm_state.progress
            job.err_msg = odm_state.error
            job.update_at = datetime.now()

            db.commit()
            db.refresh(job)
            logger.info("Updated ODM task state in database: %s", odm_state.dict())


@app.task(bind=True, base=AbortableTask)
def copy_image_to_odm(
        self,
        project_id: int,
        task_id: str,
        base_url: str,
        odm_dest_folder: Union[Path, str],
        images: []
):
    """
    Copy images to the ODM media directory for processing.

    This function handles the transfer of images from their source location
    to the ODM server's media directory. It tracks progress and automatically
    triggers job commit upon completion.

    Args:
        self (AbortableTask): The Celery task instance
        project_id (int): The ID of the project the images belong to
        task_id (str): The task identifier for the ODM job
        base_url (str): The base URL of the odm host
        odm_dest_folder (Union[Path, str]): The destination folder on the ODM server
        images (list): List of image file paths to copy

    Process:
        1. Resolves the target directory path
        2. Creates directory structure if needed
        3. Copies each image while tracking progress
        4. Updates progress cache after each file
        5. Automatically commits job when complete
    """
    total_files = len(images)
    copied_step = 0
    odm_state = OdmState(state=OdmJobStatus.running.value, host=base_url)

    try:
        # copy each image to target directory
        for img in images:
            if self.is_aborted():
                msg = "Task aborted by user."
                logger.info(msg)
                # set celery task state to canceled and update progress cache
                odm_state.state = OdmJobStatus.canceled.value
                odm_state.error = msg
                break

            # copy image to target directory
            shutil.copy(img, odm_dest_folder)
            copied_step += 1

            percentage = (Decimal(copied_step) / Decimal(total_files)) * 100
            odm_state.progress = percentage.quantize(Decimal('0.01'), rounding=ROUND_DOWN)

            self.update_state(meta=odm_state.dict())
            if int(odm_state.progress) % 10 == 0:
                logger.info("Copying image %d/%d (%.2f%%)", copied_step, total_files, odm_state.progress)

            time.sleep(0.3)
    except Exception as e:
        logger.error("Error copying image: %s", e)
        odm_state.state = OdmJobStatus.failed.value
        odm_state.error = str(e)
    finally:
        if copied_step == total_files:
            logger.info("All images copied successfully.")
            # set celery task state to completed and update progress cache
            odm_state.state = OdmJobStatus.completed.value
            odm_state.progress = 100.00
            # callback to ODM to commit job
            commit_odm_task(project_id, task_id, base_url)

        # update odm state in database and return result to celery task
        _update_odm_state(self.request.id, odm_state)
        return odm_state.dict()


@app.task(bind=True, base=AbortableTask, queue="odm_report")
def update_odm_report(
        self,
        project_id: int,
        task_id: str,
        odm_host: str,
        output_files: dict,
        output_dir: str,
        report_info: dict,
        report_no: str,
        envs: dict
):
    """
    Update ODM report by downloading from ODM server and uploading to OSS.

    This task downloads the ODM report files from the ODM server and uploads them
    to OSS (Object Storage Service) with progress tracking.

    Args:
        self: The Celery task instance
        project_id (int): ODM project ID
        task_id (str): ODM task ID
        odm_host (str): ODM server host URL
        output_files (dict): Dictionary of output files to upload
        output_dir (str): Local output directory path
        report_info (dict): Report metadata
        report_no (str): Report number
        envs (dict): Environment variables for OSS authentication

    Process:
        1. Initialize OSS client with credentials
        2. Get list of files to upload
        3. Download full report ZIP from ODM server
        4. Upload all files to OSS with progress tracking
    """
    odm_update_state = OdmUploadState(state=OdmJobStatus.running.value, total_progress={})

    try:
        OSS_DOMAIN = envs.get("OSS_DOAMIN").rstrip("/")
        OSS_UPLOAD_KEY = envs.get("OSS_UPLOAD_KEY").rstrip("/")
        # Initialize OSS client with credentials
        bucket = oss2.Bucket(
            oss2.AuthV4(
                envs.get("OSS_ACCESS_KEY_ID"),
                envs.get("OSS_ACCESS_KEY_SECRET")
            ),
            envs.get("OSS_ENDPOINT"),
            envs.get("OSS_BUCKET"),
            region=envs.get("OSS_REGION"),
        )
        logger.info("%s Initialize oss client %s", "▪ " * 15, "▪ " * 15)

        # Get list of files to upload
        upload_oss_files = get_odm_report_output_files(
            project_id=project_id,
            task_id=task_id,
            output_dir=output_dir,
            output_files=output_files
        )
        # Initialize progress tracking for all files
        odm_update_state.total_progress = {ftype: (0, file_size) for ftype, _, _, file_size in upload_oss_files}

        # Handle all.zip file progress tracking
        odm_all_zip_url = f"{odm_host}/api/projects/{project_id}/tasks/{task_id}/download/all.zip"
        odm_all_zip_size = get_content_length(odm_all_zip_url)

        # Add zip file progress tracking
        odm_update_state.total_progress["zip"] = (0, odm_all_zip_size)
        odm_update_state.total_progress["download_zip"] = (0, odm_all_zip_size)
        odm_update_state.total_progress["commit_report"] = (0, 5)

        def progress_callback(key):
            def percentage(consumed_bytes, total_bytes):
                # progress callback function for upload_progress_oss
                if self.is_aborted():
                    raise TaskAbortedException("Task aborted by user.")

                if total_bytes > 0:
                    odm_update_state.total_progress[key] = (consumed_bytes, total_bytes)
                    self.update_state(meta=odm_update_state.dict())

            return percentage

        # Download full report ZIP from ODM server
        local_write_zip = Path(output_dir) / "all.zip"
        odm_all_zip, odm_all_zip_size = donwload_odm_all_zip(
            odm_all_zip_url,
            local_write_zip,
            total_bytes=odm_all_zip_size,
            progress_callback=progress_callback("download_zip")
        )

        # Perform resumable upload with progress tracking for all files
        upload_to_oss_urls = {}
        for ftype, fpath, oss_url, file_size in upload_oss_files:
            oss_url = OSS_UPLOAD_KEY + "/" + oss_url

            logger.info("Starting resumable upload of %s to OSS path: %s", fpath, oss_url)
            # track progress for each file type
            bucket.put_object_from_file(oss_url, fpath, progress_callback=progress_callback(ftype))
            upload_to_oss_urls[ftype] = OSS_DOMAIN + "/" + oss_url

        # Upload full report ZIP to OSS
        zip_oss_url = OSS_UPLOAD_KEY + f"/{project_id}/{task_id}/all.zip"
        logger.info("Starting resumable upload of %s to OSS path: %s", odm_all_zip, zip_oss_url)
        bucket.put_object_from_file(zip_oss_url, odm_all_zip, progress_callback=progress_callback("zip"))

        # Commit report to online
        commint_report(
            commint_report_api=envs.get("DOM_COMMIT_REPORT_API"),
            report_info=report_info,
            token=envs.get("ODM_TOKEN"),
            cid=envs.get("ODM_CID"),
            report_no=report_no,
            all_zip_url=OSS_DOMAIN + "/" + zip_oss_url,
            output_files=upload_to_oss_urls
        )
        odm_update_state.total_progress["commit_report"] = (5, 5)

        # delete temporary zip file
        odm_all_zip.unlink()
    except TaskAbortedException as taex:
        logger.warning(taex)
        odm_update_state.state = OdmJobStatus.canceled.value
        odm_update_state.error = str(taex)
    except Exception as e:
        logger.error("Failed to upload ODM files to OSS: %s", e)
        odm_update_state.state = OdmJobStatus.failed.value
        odm_update_state.error = str(e)
    finally:
        # update odm state in database and return result to celery task
        _update_report_state(self.request.id, odm_update_state)

        return odm_update_state.dict()


@app.task(queue="odm_report")
def generate_odm_report(envs: dict):
    """
    Generate an ODM report using a Docker container.

    This function handles the generation of an ODM (OpenDroneMap) report by running
    a Docker container with the RSDM image. It sets up the necessary directory
    structure, mounts volumes, and executes the report generation process.

    Args:
        envs (dict): Environment variables for the Docker container, including:
            - RSDM_IMAGE: The Docker image to use for report generation
            - ODM_PROJECT_ID: The ODM project ID
            - ODM_TASK_ID: The ODM task ID
            - ODM_ORTHOPHOTO_TIF: Path to the orthophoto TIF file
            - SERVICE_HOST_GATEWAY: Host gateway for container networking
            - Other RSDM-specific environment variables

    Process:
        1. Sets up directory structure for input/output files
        2. Removes any existing output directory
        3. Creates new output directory and log file
        4. Runs Docker container with appropriate volume mounts
        5. Executes report generation command inside container
        6. Container is automatically removed after execution

    Example:
        ./start.sh --input_dir ./images --output_dir ./output --algos ./algos.json --license ./license

    Note:
        The Docker container is configured to:
        - Copy media files to the container's images directory
        - Execute the start script
        - Mount necessary volumes for input, output, logs, and configuration files
        - Connect to the host network via extra_hosts configuration
    """
    work_dir = Path(os.getcwd())
    OUTPUT_DIR = Path(envs.get("OMD_OUTPUT_DIR"))
    logger.info("Starting ODM report generation, source directory: %s, output directory: %s",
                envs.get("ODM_ORTHOPHOTO_TIF"), OUTPUT_DIR)
    # Initialize Docker client
    client = docker.from_env()

    # Run the RSDM Docker container to generate the report
    client.containers.run(
        stderr=True,
        stdout=True,
        image=envs.get("RSDM_IMAGE"),
        command=["/bin/bash", "-c", "cp -r /media/* /app/images/ && ./start"],
        environment=envs,
        extra_hosts={
            envs.get("SERVICE_HOST_GATEWAY"): "host-gateway"
        },
        remove=True,  # Automatically remove container when it exits
        volumes={
            str(Path(envs.get("ODM_ORTHOPHOTO_TIF")).absolute()): {
                "bind": "/media/odm_orthophoto.tif",
                "mode": "ro"  # Read-only mount for input orthophoto
            },
            envs.get("ODM_LOG_FILE"): {
                "bind": "/app/app.log",
                "mode": "rw"  # Read-write mount for log file
            },
            str(OUTPUT_DIR.absolute()): {
                "bind": "/app/images/output",
                "mode": "rw"  # Read-write mount for output directory
            },
            str(work_dir / "docker/rsdm/algos.json"): {
                "bind": "/app/algos.json",
                "mode": "rw"  # Read-write mount for algorithms configuration
            },
            str(work_dir / "docker/rsdm/license"): {
                "bind": "/app/license",
                "mode": "ro"  # Read-only mount for license file
            },
            str(work_dir / "docker/rsdm/utils.py"): {
                "bind": "/app/utils.py",
                "mode": "rw"
            }
        }
    )


def abort_task(celery_task_id: str) -> bool:
    """
    Abort a running ODM task.

    Args:
        celery_task_id: The Celery task ID

    Returns:
        True if the task was successfully aborted, False otherwise.
    """
    task = AbortableAsyncResult(celery_task_id)
    is_aborted = False

    if task.name and task.worker:
        logger.info("Task %s aborted by user.", celery_task_id)
        task.abort()
        if not task.is_aborted():
            raise HTTPException(status_code=500, detail="Failed to abort task on Celery worker.")
        is_aborted = True
    else:
        logger.warning("Celery task %s not found, skipping abort.", celery_task_id)

    return is_aborted


def get_current_state(celery_task_id: str) -> Union[OdmState, None]:
    """
    Get the current state of an ODM job.
    Args:
        celery_task_id: The Celery task ID
    Returns:
        the current state of the ODM job as an OdmState object, or None if the task is not found.
    """
    current_state = None
    task = AbortableAsyncResult(celery_task_id)

    if task.name and task.worker:
        current_state = OdmState(**task.result) if task.result else OdmState(state=task.state)

    return current_state


def get_report_current_state(celery_task_id: str) -> Union[OdmUploadState, None]:
    """
    Get the current state of an ODM job.
    Args:
        celery_task_id: The Celery task ID
    Returns:
        the current state of the ODM job as an OdmState object, or None if the task is not found.
    """
    current_state = None
    task = AbortableAsyncResult(celery_task_id)

    if task.name and task.worker and task.result:
        current_state = OdmUploadState(**task.result)
        current_state.progress = _get_upload_progress(current_state.total_progress)

    return current_state
