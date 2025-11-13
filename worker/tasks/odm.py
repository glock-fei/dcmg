import json
import logging
import os
import shutil
import time
from datetime import datetime
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Union, Optional

import docker
import numpy as np
import rasterio
from celery.contrib.abortable import AbortableTask, AbortableAsyncResult
from fastapi import HTTPException

import models
import utils
from worker.app import app
from models import with_db_session, OdmJobs, OdmReport, OdmGeTask

logger = logging.getLogger(__name__)


class TaskAbortedException(Exception):
    """Exception raised when a Celery task is aborted by user"""
    pass


def _get_upload_progress(total_progress: dict) -> float:
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
    return float(percentage.quantize(Decimal('0.01'), rounding=ROUND_DOWN))


def _update_report_state(celery_task_id: str, upload_state: utils.OdmUploadState) -> float:
    """
    Update report state in database with caching to reduce frequent writes.
    Args:
        celery_task_id: The Celery task ID
        upload_state (OdmUploadState): The current state of the ODM report upload

    """
    current_progress = _get_upload_progress(upload_state.total_progress)
    with with_db_session() as db:
        report: OdmReport = db.query(OdmReport).filter(OdmReport.celery_task_id == celery_task_id).first()
        if report:
            report.state = upload_state.state
            report.err_msg = upload_state.error
            report.update_at = datetime.now()
            report.progress = current_progress

            db.commit()
            db.refresh(report)
            logger.info("Updated ODM report state in database: %s", upload_state.dict())

    return current_progress


def _update_odm_state(celery_task_id: str, odm_state: utils.OdmState):
    """
    Update task progress in database with caching to reduce frequent writes.

    Args:
        celery_task_id: The Celery task ID
        odm_state (OdmState): The current state of the ODM job
    """
    with with_db_session() as db:
        job: OdmJobs = db.query(OdmJobs).filter(OdmJobs.celery_task_id == celery_task_id).first()
        if job:
            job.state = odm_state.state
            job.odm_host = odm_state.host
            job.progress = odm_state.progress
            job.err_msg = odm_state.error
            job.update_at = datetime.now()

            db.commit()
            db.refresh(job)
            logger.info("Updated ODM task state in database: %s", odm_state.dict())


def _update_getate(celery_task_id: str, state_base: utils.StateBase):
    """
    Update task progress in database with caching to reduce frequent writes.

    Args:
        celery_task_id: The Celery task ID
        state_base (StateBase): The current state of the ODM job
    """
    with with_db_session() as db:
        getask: OdmGeTask = db.query(OdmGeTask).filter_by().filter(OdmGeTask.celery_task_id == celery_task_id).first()
        if getask:
            getask.state = state_base.state
            getask.err_msg = state_base.error
            getask.update_at = datetime.now()

            db.commit()
            db.refresh(getask)
            logger.info("Updated ODM generate task state in database: %s", state_base.dict())


@app.task(bind=True, base=AbortableTask)
def copy_image_to_odm(
        self,
        project_id: int,
        task_id: str,
        base_url: str,
        odm_dest_folder: str,
        images: [],
        reflector_dest_dir: str,
        radiometric: Optional[list[list[dict]]]
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
        odm_dest_folder (str): The destination folder on the ODM server
        images (list): List of image file paths to copy
        reflector_dest_dir (str): The reflector directory on the ODM server
        radiometric (Optional[list[list[dict]]]): List of radiometric data for each image

    Process:
        1. Resolves the target directory path
        2. Creates directory structure if needed
        3. Copies each image while tracking progress
        4. Updates progress cache after each file
        5. Automatically commits job when complete
    """
    total_files = len(images)
    celery_task_id = self.request.id
    copied_step = 0
    odm_state = utils.OdmState(state=utils.OdmJobStatus.running.value, host=base_url)

    try:
        # Process radiometric data if present
        _process_radiometric_data(celery_task_id, radiometric, Path(reflector_dest_dir))
        # copy each image to target directory
        for img in images:
            if self.is_aborted():
                msg = "Task aborted by user."
                logger.info(msg)
                # set celery task state to canceled and update progress cache
                odm_state.state = utils.OdmJobStatus.canceled.value
                odm_state.error = msg
                break

            # copy image to target directory
            shutil.copy(img, odm_dest_folder)
            copied_step += 1

            percentage = (Decimal(copied_step) / Decimal(total_files)) * 100
            odm_state.progress = float(percentage.quantize(Decimal('0.01'), rounding=ROUND_DOWN))

            self.update_state(meta=odm_state.dict())
            if int(odm_state.progress) % 10 == 0:
                logger.info("Copying image %d/%d (%.2f%%)", copied_step, total_files, odm_state.progress)

            time.sleep(0.3)
    except Exception as e:
        logger.error("Error copying image: %s", e)
        odm_state.state = utils.OdmJobStatus.failed.value
        odm_state.error = str(e)
    finally:
        if copied_step == total_files:
            logger.info("All images copied successfully.")
            # set celery task state to completed and update progress cache
            odm_state.state = utils.OdmJobStatus.completed.value
            odm_state.progress = 100.00
            # callback to ODM to commit job
            utils.commit_odm_task(project_id, task_id, base_url)

        # update odm state in database and return result to celery task
        _update_odm_state(celery_task_id, odm_state)
        return odm_state.dict()


@app.task(bind=True, base=AbortableTask, queue="upload_odm_report")
def upload_odm_report_to_cloud(
        self,
        project_id: int,
        task_id: str,
        odm_resource_files: dict,
        odm_radiometric: list,
        output_dir: str,
        odm_host: str,
        report_no: str,
        cid: str,
        token: str
):
    """
    Update ODM report by downloading from ODM server and uploading to OSS.

    This task downloads the ODM report files from the ODM server and uploads them
    to OSS (Object Storage Service) with progress tracking.

    Args:
        self: The Celery task instance
        project_id (int): The ID of the project the images belong to
        task_id (str): The task identifier for the ODM job
        odm_resource_files (dict): The ODM report files to upload
        odm_radiometric (list): The radiometric data for the ODM report
        output_dir (str): The output directory for the ODM report
        odm_host (str): ODM server host URL
        report_no (str): Report number
        cid (str): company ID
        token (str): clound service token

    Process:
        1. Initialize OSS client with credentials
        2. Get list of files to upload
        3. Download full report ZIP from ODM server
        4. Upload all files to OSS with progress tracking
    """
    odm_update_state = utils.OdmUploadState(state=utils.OdmJobStatus.running.value, total_progress={})

    try:
        import oss2
        oss_upload_key = utils.format_oss_upload_prefix(project_id, task_id)
        # Initialize OSS client with credentials
        bucket = oss2.Bucket(
            oss2.AuthV4(
                os.getenv("OSS_ACCESS_KEY_ID"),
                os.getenv("OSS_ACCESS_KEY_SECRET")
            ),
            os.getenv("OSS_ENDPOINT"),
            os.getenv("OSS_BUCKET"),
            region=os.getenv("OSS_REGION"),
        )
        logger.info("%s Initialize oss client %s", "▪ " * 15, "▪ " * 15)

        # Handle all.zip file progress tracking
        odm_all_zip_url = f"{odm_host}/api/projects/{project_id}/tasks/{task_id}/download/all.zip"
        odm_all_zip_size = utils.get_content_length(odm_all_zip_url)

        # Initialize progress tracking for fixed entries and all files
        odm_update_state.total_progress = {
            utils.ProgressKeys.DOWNLOAD_ALL_ZIP.value: (0, odm_all_zip_size),
            utils.ProgressKeys.UPLOAD_ALL_ZIP.value: (0, odm_all_zip_size),
            utils.ProgressKeys.COMMIT_REPORT.value: (0, 5)
        }
        output_files = utils.get_odm_report_output_files(output_dir, odm_resource_files, odm_radiometric)
        logger.info("Ready to upload %d files:", len(output_files))

        # Initialize progress tracking for all files with 3-digit sequential numbers
        odm_update_state.total_progress.update({
            f"{i + 4:03d}": (0, file_size)
            for i, (_, file_size) in enumerate(output_files)
        })

        # Upload progress callback function
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
        odm_all_zip, odm_all_zip_size = utils.donwload_odm_all_zip(
            odm_all_zip_url,
            local_write_zip,
            total_bytes=odm_all_zip_size,
            progress_callback=progress_callback(utils.ProgressKeys.DOWNLOAD_ALL_ZIP.value)
        )

        # Perform resumable upload with progress tracking for all files
        for i, (fstr, _) in enumerate(output_files):
            fpath = Path(fstr)
            oss_url = oss_upload_key + "/" + Path(fpath).parent.name + "/" + Path(fpath).name
            # upload file to OSS
            logger.info("Start uploading %s to %s", fpath, oss_url)
            bucket.put_object_from_file(oss_url, fpath, progress_callback=progress_callback(f"{i + 4:03d}"))

        # Upload full report ZIP to OSS
        zip_oss_url = oss_upload_key + "/all.zip"
        logger.info("Start uploading %s to %s", odm_all_zip, zip_oss_url)
        bucket.put_object_from_file(
            zip_oss_url,
            odm_all_zip,
            progress_callback=progress_callback(utils.ProgressKeys.UPLOAD_ALL_ZIP.value)
        )

        # Commit report to online
        utils.commint_report(
            commint_report_api=os.getenv("DOM_COMMIT_REPORT_API"),
            token=token,
            cid=cid,
            report_no=report_no,
            all_zip_url=os.getenv("OSS_DOMAIN").rstrip("/") + "/" + zip_oss_url
        )
        odm_update_state.total_progress[utils.ProgressKeys.COMMIT_REPORT.value] = (5, 5)
        # # delete temporary zip file
        local_write_zip.unlink()
    except TaskAbortedException as taex:
        logger.warning(taex)
        odm_update_state.state = utils.OdmJobStatus.canceled.value
        odm_update_state.error = str(taex)
    except Exception as e:
        logger.error("Failed to upload ODM files to OSS: %s", e)
        odm_update_state.state = utils.OdmJobStatus.failed.value
        odm_update_state.error = str(e)
    finally:
        # update odm state in database and return result to celery task
        current_progress = _update_report_state(self.request.id, odm_update_state)
        if current_progress == 100.00:
            odm_update_state.state = utils.OdmJobStatus.completed.value

        return odm_update_state.dict()


@app.task(bind=True, base=AbortableTask, queue="generate_odm_report")
def generate_odm_report(
        self,
        project_id,
        task_id,
        odm_job_name,
        output_dir,
        reflector_dest_dir,
        orthophoto_tif,
        log_file
):
    """
    Generate an ODM report using a Docker container.

    This function handles the generation of an ODM (OpenDroneMap) report by running
    a Docker container with the RSDM image. It sets up the necessary directory
    structure, mounts volumes, and executes the report generation process.

    Args:
        self: The Celery task instance
        project_id (int): ODM project ID
        task_id (str): ODM task ID
        odm_job_name (str): ODM job name
        output_dir (str): Local output directory path
        reflector_dest_dir (str): Destination directory for reflector images
        orthophoto_tif (str): Path to orthophoto TIF file
        log_file (str): Path to log file

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
    state_base = utils.StateBase(state=utils.OdmJobStatus.running.value)

    try:
        import platform
        work_dir = Path(os.getcwd())
        output_dir = Path(output_dir)
        logger.info("Starting ODM report generation from %s to %s", orthophoto_tif, output_dir)
        # Initialize Docker client
        client = docker.from_env()
        # set en variables for docker container
        envs = {
            "ODM_SAVE_REPORT_API": os.getenv("ODM_SAVE_REPORT_API"),
            "ODM_PROJECT_ID": project_id,
            "ODM_TASK_ID": task_id
        }
        if filename_base := utils.clean_filename(odm_job_name):
            envs["FILENAME_BASE"] = filename_base

        # Update state
        self.update_state(meta=state_base.dict())
        # Run the RSDM Docker container to generate the report
        client.containers.run(
            detach=False,
            remove=True,
            image=os.getenv("RSDM_IMAGE"),
            command=["./start"],
            user=os.getuid(),
            environment=envs,
            extra_hosts={os.getenv("SERVICE_HOST_GATEWAY"): "host-gateway"},
            volumes={
                str(Path(orthophoto_tif).absolute()): {"bind": "/app/images/odm_orthophoto.tif", "mode": "ro"},
                log_file: {"bind": "/app/app.log", "mode": "rw"},
                str(Path(reflector_dest_dir).absolute()): {"bind": "/app/reflector", "mode": "rw"},
                str(output_dir.absolute()): {"bind": "/app/images/output", "mode": "rw"},
                str(work_dir / "docker/rsdm/algos.json"): {"bind": "/app/algos.json", "mode": "rw"},
                str(work_dir / "docker/rsdm/license"): {"bind": "/app/license", "mode": "ro"},
                str(work_dir / "docker/rsdm/utils.py"): {"bind": "/app/utils.py", "mode": "rw"}
            }
        )
        logger.info("ODM report generation complete.")
        state_base.state = utils.OdmJobStatus.completed.value
    except Exception as e:
        logger.error("Failed to generate ODM report: %s", e)
        state_base.state = utils.OdmJobStatus.failed.value
        state_base.error = str(e)
    finally:
        # update odm state in database and return result to celery task
        _update_getate(self.request.id, state_base)

        return state_base.dict()


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


def get_current_state(celery_task_id: str) -> Union[utils.OdmState, None]:
    """
    Get the current state of an ODM job.
    Args:
        celery_task_id: The Celery task ID
    Returns:
        the current state of the ODM job as an OdmState object, or None if the task is not found.
    """
    current_state = None
    try:
        task = AbortableAsyncResult(celery_task_id)

        if task.name and task.worker:
            current_state = utils.OdmState(**task.result) if task.result else utils.OdmState(state=task.state)
    except ValueError as e:
        logger.error("Failed to get task result: %s", str(e))
        pass
    finally:
        return current_state


def get_report_current_state(celery_task_id: str) -> Union[utils.OdmUploadState, None]:
    """
    Get the current state of an ODM job.
    Args:
        celery_task_id: The Celery task ID
    Returns:
        the current state of the ODM job as an OdmState object, or None if the task is not found.
    """
    current_state = None
    try:
        task = AbortableAsyncResult(celery_task_id)

        if task.name and task.worker and task.result:
            current_state = utils.OdmUploadState(**task.result)
            current_state.progress = _get_upload_progress(current_state.total_progress)
    except ValueError as e:
        logger.error("Failed to get task result: %s", str(e))
        pass
    finally:
        return current_state


def _process_radiometric_data(
        celery_task_id: str,
        radiometric: Optional[list[list[dict]]],
        reflector_dest_dir: Path
) -> None:
    """
    Process radiometric data and save to config.json file.

    This function handles the processing of radiometric calibration data by:
    1. Copying image files to the reflector directory
    2. Updating picture paths to relative paths
    3. Saving the updated data to a config.json file

    Args:
        celery_task_id: The Celery task ID
        radiometric: List of radiometric data rows, each containing items with picture info
        reflector_dest_dir: Destination directory for reflector images

    Returns:
        None
    """
    if not radiometric or all(not row for row in radiometric):
        return

    # Process each radiometric item
    for row in radiometric:
        for item in row:
            rad = utils.Radiometric(**item)
            picture = utils.get_src_folder(rad.picture)
            # Calculate surface reflectance for each picture
            utils.get_surface_reflectance(picture, rad.coords, rad.panel_reflectance)
            # Copy picture to reflector directory
            shutil.copy(picture, reflector_dest_dir)
            item["picture"] = os.path.join(reflector_dest_dir.name, os.path.basename(rad.picture))

    # Save updated radiometric data to config.json
    config_path = reflector_dest_dir / "config.json"
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(radiometric, f, indent=4, ensure_ascii=False)

    with with_db_session() as db:
        db.query(OdmJobs).filter(OdmJobs.celery_task_id == celery_task_id).update({OdmJobs.radiometric: radiometric})
        db.commit()

    logger.info("Saved radiometric data to %s", config_path)


@app.task(bind=True, base=AbortableTask)
def sampling_statistics(
        self,
        output_dir: str,
        resource_files,
        quadrats: list[tuple[int, list[tuple[float, float]]]]
):
    """
    Calculate sampling statistics for each quadrat in each algorithm.

    This function handles the calculation of sampling statistics for each quadrat in each algorithm
    """
    sampling_state = utils.OdmState(state=utils.OdmJobStatus.running.value)
    total_progress = len(quadrats) * len(resource_files) + 1  # +1 for database commit step
    current_progress = 0

    try:
        resource_dir = Path(output_dir)
        records = []

        logger.info(
            "Starting sampling statistics calculation. Output dir: %s, Number of algorithms: %d, Number of quadrats: %d",
            output_dir, len(resource_files), len(quadrats))

        for algo_name, files in resource_files.items():
            tif_file = resource_dir / files.get("tif")
            logger.info("Opening TIF file: %s | Algorithm: %s", tif_file, algo_name)

            with rasterio.open(tif_file) as src:
                logger.info("Successfully opened TIF file. Image dimensions: %s", src.shape)

                for quadrat_id, coords in quadrats:
                    # check if task is aborted
                    if self.is_aborted():
                        raise TaskAbortedException("Task aborted by user.")

                    logger.debug("Processing quadrat ID: %d with %d coordinates", quadrat_id, len(coords))

                    # get dn values in polygon
                    dn_values, sorted_coords = utils.get_dn_values_in_polygon(coords, src)
                    stat_record = models.OdmQuadratStatistics(
                        quadrat_id=quadrat_id,
                        algo_name=algo_name,
                        picture=files.get("tif"),
                        dn_min=float(np.min(dn_values)),
                        dn_max=float(np.max(dn_values)),
                        dn_mean=float(np.mean(dn_values)),
                        dn_std=float(np.std(dn_values))
                    )

                    records.append(stat_record)
                    logger.info("Quadrat %d stats - Min: %.2f, Max: %.2f, Mean: %.2f, Std: %.2f",
                                quadrat_id, stat_record.dn_min, stat_record.dn_max, stat_record.dn_mean,
                                stat_record.dn_std)

                    current_progress += 1
                    # Update progress more accurately
                    sampling_state.progress = round(current_progress / total_progress * 100, 2)
                    self.update_state(meta=sampling_state.dict())
                    time.sleep(1)

        logger.info("Completed processing all quadrats for all algorithms. Total records: %d", len(records))
        # save sampling statistics to database
        if len(records) > 0:
            with with_db_session() as db:
                models.OdmQuadratStatistics.bulk_upsert(db, records)
                db.commit()
                logger.info("Saved %d records to database", len(records))
        # dont forget to update progress to 100%
        current_progress += 1
    except TaskAbortedException as taex:
        logger.warning(taex)
        sampling_state.state = utils.OdmJobStatus.canceled.value
        sampling_state.error = str(taex)
    except Exception as e:
        logger.error("Failed to calculate sampling statistics: %s", e, exc_info=True)
        sampling_state.state = utils.OdmJobStatus.failed.value
        sampling_state.error = str(e)
    finally:
        if current_progress == total_progress:
            sampling_state.progress = 100.00
            sampling_state.state = utils.OdmJobStatus.completed.value
            logger.info("Sampling statistics task completed successfully")

        # Lastly, update the sampling record with the latest progress.
        with with_db_session() as db:
            samprd = db.query(models.OdmSamplingRecord).filter(
                models.OdmSamplingRecord.celery_task_id == self.request.id
            ).first()

            if samprd:
                samprd.state = sampling_state.state
                samprd.error = sampling_state.error
                samprd.progress = sampling_state.progress
                samprd.update_at = datetime.now()

                db.commit()

        return sampling_state.dict()
