import logging
import os
import re
import shutil
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

import docker
from celery.contrib.abortable import AbortableTask
from docker.types import DeviceRequest
from celery.exceptions import TaskRevokedError

import utils
import models
from worker.app import app

logger = logging.getLogger(__name__)


def run_command(cmd, progress_callback=None):
    """
    Run a command in the terminal.
    args:
        cmd: list, command to run.
    """
    logger.info("? Running: %s", " ".join(cmd))

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
        encoding="utf-8",
        errors="replace"
    )
    # Poll processes for new output until finished
    oss_progress_pattern = re.compile(r'(\d+\.\d+)%')
    oss_success_pattern = re.compile(r'Success.*Upload done:.*avg')

    while True:
        line = process.stdout.readline()
        if not line:
            break

        line = line.strip()
        if line:
            logger.info("[OSS] %s", line)
            # update progress
            if progress_callback:
                match = oss_progress_pattern.search(line)
                # update progress callback
                if match:
                    progress_callback(float(match.group(1)))
                elif oss_success_pattern.search(line):
                    progress_callback(100.0)

    return_code = process.wait()

    if return_code == 0:
        logger.info("[OSS] ? upload to oss completed successfully")


def _check_required_files(dest_path: str, log_file: str) -> Dict[str, Path]:
    """
    Check if all required output files exist
    
    This function checks if all required output files from a successful task exist:
    1. Log file
    2. PLY report file
    3. JSON report file
    4. Potree JS file
    
    Args:
        dest_path (str): The destination directory path
        log_file (str): The log file path
    
    Returns:
        Dict[str, Path]: Dictionary mapping file type to Path objects
    
    Raises:
        Exception: If any required file is missing
    """
    images_dir = Path(dest_path)
    # Define required report files as a dictionary
    required_files = {
        "log_file": Path(log_file),
        "report_ply": images_dir / "output/report.ply",
        "report_json": images_dir / "output/report.json",
        "potree_js": images_dir / "output/potree/cloud.js"
    }

    # Check each required file
    for name, file_path in required_files.items():
        if not file_path.exists():
            raise Exception(f"reconstruction_task.error.missing_file: Required file is missing: {str(file_path)}")

    return required_files


def _parse_log_file(analyzer: utils.ErrorLogAnalyzer) -> list:
    """
    Parse log file and store entries in database
    
    Args:
        analyzer (utils.ErrorLogAnalyzer): The log analyzer instance
        
    Returns:
        list: List of error messages
    """
    # Parse log file
    err_msg = [
        {
            "level": entry.level.value,
            "message": entry.message,
            "raw_line": entry.raw_line
        }
        for entry in analyzer.entries
    ]

    return err_msg


def _update_cover_images(dest_path: str) -> Tuple[Optional[str], int]:
    """
    Update cover images and image count in database
    
    This function finds cover images and updates image count in the database:
    1. Determines which directory to search for images
    2. Finds the first image to use as cover
    3. Counts total images
    
    Args:
        dest_path (str): The destination directory path
    
    Returns:
        Tuple[Optional[str], int]: (cover image path, total image count)
    """
    images_dir = Path(dest_path)
    # Set cover image
    # Determine the directory to search for cover images
    cover_image_dir = images_dir / "extract" if (images_dir / "extract").exists() else images_dir

    # Get the first image file as cover image
    images = []
    for image_path in get_image_files(cover_image_dir):
        images.append(str(image_path))

    if images:
        return images[0], len(images)

    return None, 0


def _update_state(
        celery_task_id: str,
        state_base: utils.StateBase,
        report_info: Optional[dict] = None
) -> None:
    """
    Update VDCM task state in the database
    
    This function updates the state of a VDCM task in the database:
    1. Retrieves the task from the database using the Celery task ID
    2. If the task completed successfully, validates that all required output files exist
    3. Updates task properties including report files, cover image, and error logs
    4. Commits the changes to the database

    Args:
        celery_task_id (str): The Celery task ID
        state_base (utils.StateBase): The StateBase object containing the current state of the task
        report_info (Optional[dict]): Optional dictionary containing report information to update

    Returns:
        None
    """
    with models.with_db_session() as db:
        vdtask: models.VdcmJobs = db.query(models.VdcmJobs).filter(
            models.VdcmJobs.celery_task_id == celery_task_id).first()

        if vdtask:
            # Update task with report information if provided
            if report_info:
                # Define fields to update
                fields_to_update = [
                    "report_ply", "potree_js", "cover",
                    "image_total", "reports", "err_msg"
                ]
                # Update each field if it exists in report_info
                for field in fields_to_update:
                    if field in report_info:
                        setattr(vdtask, field, report_info[field])

            vdtask.state = state_base.state
            # Update error message if present in state_base
            vdtask.update_at = datetime.now()

            db.commit()
            db.refresh(vdtask)
            logger.info("Updated reconstruction task state in database: %s", state_base.dict())


@app.task(bind=True, base=AbortableTask, queue="reconstruction")
def reconstruction(
        self,
        no: str,
        src_path: str,
        dest_folder: str,
        runtime_log_file: str,
        app_log_file: str,
        frame_count: int = 150,
) -> Dict[str, Any]:
    """
    Reconstruct media using 3DCM Docker container

    This task performs 3D reconstruction from either a single video file or directory of images:
    1. Sets up paths and directories for processing
    2. Prepares the command for the 3DCM reconstruction tool
    3. Processes input files (video or images)
    4. Runs the 3DCM reconstruction in a Docker container
    5. Updates task state upon completion

    Args:
        self: Celery task object
        no (str): Unique identifier for the job
        src_path (str): Path to source file or directory containing input media
        dest_folder (str): Path to destination folder for output files
        runtime_log_file (str): Path to runtime log file for Celery task
        app_log_file (str): Path to app log file for Docker container
        frame_count (int): Number of frames to extract from input video (default: 150)

    Returns:
        dict: Status dictionary containing state and potential error information
    """
    # Initialize status tracking object with running state
    work_dir = Path(os.getcwd())
    report_info = {}
    state_base = utils.StateBase(state=utils.OdmJobStatus.uploading.value)
    log_analyzer = utils.ErrorLogAnalyzer(str(work_dir / "docker/3dcm/patterns.json"))
    container = None

    try:
        # Setup paths and directories
        src_file = Path(src_path)
        images_dir = Path(dest_folder)

        # Prepare command for 3DCM reconstruction
        command = [
            "./3dcm",
            "--rmbg",  # Enable background removal
            "--extract_phenotype",  # Enable phenotype extraction
            "--birefnet_use_enhance",  # Use enhanced background removal
            "--resize_scale", "0.5"  # Scale images to 50% for processing
        ]

        # Process input based on whether it's a single file or directory
        if src_file.is_file():
            # Handle single video file input
            shutil.copy(src_file, images_dir)
            command.extend([
                "--video_file",
                "/app/images/" + src_file.name,  # Path inside Docker container
                "--frame_count", str(frame_count)
            ])
        else:
            # Handle directory of image files input
            # Copy all supported image files to destination directory using generator
            for image_path in get_image_files(src_file):
                shutil.copy(image_path, images_dir)

        # Get required environment variables with validation
        dc_image = os.getenv("3DCM_IMAGE")
        progress_url = os.getenv("3DCM_PROGRESS_URL")
        service_host_gateway = os.getenv("SERVICE_HOST_GATEWAY")

        # Initialize Docker client for container management
        client = docker.from_env()
        # Execute 3DCM reconstruction in Docker container
        container = client.containers.run(
            image=dc_image,
            command=command,
            shm_size=os.getenv("3DCM_SHM_SIZE", "8G"),
            device_requests=[DeviceRequest(count=1, capabilities=[["gpu"]])],
            environment={"3DCM_PROGRESS_URL": progress_url, "3DCM_NO": no},
            extra_hosts={service_host_gateway: "host-gateway"},
            user=os.getuid(),  # Run container with current user's UID and GID
            detach=True,
            remove=False,
            volumes={
                str(app_log_file): {"bind": "/app/app.log", "mode": "rw"},
                str(images_dir.resolve()): {"bind": "/app/images", "mode": "rw"},
                str(work_dir / "docker/3dcm/config.json"): {"bind": "/app/config.json", "mode": "rw"},
                str(work_dir / "docker/3dcm/license"): {"bind": "/app/license", "mode": "ro"},
                str(work_dir / "docker/3dcm/utils.py"): {"bind": "/app/utils.py", "mode": "rw"}
            }
        )

        # Update container ID in database using update statement
        with models.with_db_session() as db:
            db.query(models.VdcmJobs).filter(models.VdcmJobs.celery_task_id == self.request.id).update(
                {
                    models.VdcmJobs.container_id: container.id,
                    models.VdcmJobs.state: utils.OdmJobStatus.running.value
                 }
            )
            db.commit()
            logger.info("Updated container ID %s for task %s", container.id, no)

        # Stream logs from container and write to local log file
        with open(runtime_log_file, "w", encoding="utf-8") as f:
            for line in container.logs(stream=True, follow=True):
                decoded_line = line.decode("utf-8")
                f.write(decoded_line)
                f.flush()  # Ensure immediate write to file

                # Check if task is aborted
                if self.is_aborted():
                    container.stop(timeout=5)
                    raise TaskRevokedError("Task was canceled by user")

        # Wait for container to finish and check exit code
        run_status = container.wait()
        if run_status.get("StatusCode") != 0:
            raise Exception("reconstruction_task.error.docker_error: Docker container exited with non-zero exit code.")

        # Check for required files and validate their existence
        required_files = _check_required_files(dest_path=str(images_dir), log_file=runtime_log_file)
        logger.info("Reconstruction completed for job %s", no)

        # Find cover image and count total images
        cover, image_total = _update_cover_images(dest_path=str(images_dir))

        # Parse report JSON to extract report data
        with open(required_files.get("report_json"), "r", encoding="utf-8") as f:
            reports = json.load(f).get("report", {})

        # Get paths for report files
        report_ply, potree_js = str(required_files.get("report_ply")), str(required_files.get("potree_js"))

        # Prepare report information dictionary for updating task state
        report_info = {
            "report_ply": report_ply,
            "potree_js": potree_js,
            "cover": cover,
            "image_total": image_total,
            "reports": reports,
        }
        state_base.state = utils.OdmJobStatus.completed.value
        state_base.progress = 100.0
    except TaskRevokedError as e:
        logger.warning("Reconstruction task for job %s was canceled", no, exc_info=True)
        state_base.state = utils.OdmJobStatus.canceled.value
        state_base.error = str(e)
        # Parse log line for error messages
        log_analyzer.parse_log_line(str(e))
        report_info["err_msg"] = _parse_log_file(analyzer=log_analyzer)
    except Exception as e:
        # Handle any errors during processing
        logger.error("Reconstruction failed for job %s: %s", no, str(e), exc_info=True)
        state_base.state = utils.OdmJobStatus.failed.value
        state_base.error = str(e)
        # Parse log file for error messages
        log_analyzer.parse_log_file(runtime_log_file)
        log_analyzer.parse_log_line(str(e))
        report_info["err_msg"] = _parse_log_file(analyzer=log_analyzer)
    finally:
        # Return final status regardless of success or failure
        _update_state(
            celery_task_id=self.request.id,
            state_base=state_base,
            report_info=report_info
        )
        # Clean up Docker container if it exists
        if container:
            container.remove(force=True)
        return state_base.dict()


def _update_upload_state(
        job_id: str,
        upload_info: Optional[dict] = None
) -> None:
    """
    Update VDCM upload task state in the database
    
    This function updates the state of a VDCM upload task in the database:
    1. Retrieves the task from the database using the job number
    2. Updates upload properties 
    3. Commits the changes to the database

    Args:
        job_id (str): The job number
        upload_info (Optional[dict]): Optional dictionary containing upload information to update

    Returns:
        None
    """
    with models.with_db_session() as db:
        job_upload: models.VdcmUploads = db.query(models.VdcmUploads).filter(models.VdcmUploads.job_id == job_id).first()

        if job_upload:
            # Update task with upload information if provided
            fields_to_update = [
                "cloud_id", "source_ply", "ply_cloud_js", "cover",
                "log_file", "progress", "state", "err_msg"
            ]
            # Update each field if it exists in upload_info
            for field in fields_to_update:
                if field in upload_info:
                    setattr(job_upload, field, upload_info[field])

        db.commit()
        db.refresh(job_upload)
        logger.info("Updated upload task state in database for job: %s", job_id)


@app.task(bind=True, base=AbortableTask)
def update_phenotype_report(
        self,
        job_id: int,
        token: str,
        cid: str,
        terminal_id: str,
        terminal_type: str,
):
    """
    Update phenotype report and upload files to OSS

    This task performs the following operations:
    1. Retrieves the VDCM job from the database
    2. Uploads generated files (potree, ply, cover image, log) to OSS
    3. Sends scene result data to the server API

    Args:
        self: Celery task object
        job_id (int): The job number
        token (str): Authentication token for API requests
        cid (str): Client ID for API requests
        terminal_id (str): Terminal ID for API requests
        terminal_type (str): Terminal type for API requests

    Returns:
        dict: Status dictionary containing state and potential error information
    """
    # Initialize status tracking object with running state
    state_base = utils.OdmUploadState(state=utils.OdmJobStatus.running.value)
    upload_info = {}

    # Retrieve the VDCM job from the database
    with models.with_db_session() as db:
        query = db.query(models.VdcmJobs).outerjoin(models.VdcmUploads, models.VdcmUploads.job_id == models.VdcmJobs.id)
        query = query.filter(models.VdcmJobs.id == job_id)
        job: models.VdcmJobs = query.first()

        job.uploads.state = state_base.state

        db.commit()
        db.refresh(job.uploads)

        logger.info("Retrieved VDCM job from database: %s, state: %s", job.no, job.uploads.state)

    # Get required environment variables
    OSS_BUCKET = "oss://{OSS_BUCKET}/".format(OSS_BUCKET=os.getenv("OSS_BUCKET"))
    UPLOAD_KEY = "tgyn/3d/{}/".format(job.no)
    DOMAIN = os.getenv("OSS_DOMAIN")
    WORK_DIR = Path(os.getcwd())
    
    logger.info("Uploading files to OSS: %s, %s", UPLOAD_KEY, DOMAIN)

    try:
        # Set up paths
        OUTPUT_DIR = Path(job.dest_path) / "output"
        ossutil_path = WORK_DIR / "docker/3dcm/ossutil"
        
        # Check if ossutil exists
        if not ossutil_path.exists():
            raise Exception("ossutil not found in docker/3dcm")

        def progress_callback(key, weighed_progress):
            """
            Create a progress callback function for upload progress tracking
            
            Args:
                key (str): Key for the upload step
                weighed_progress (float): Weighted progress factor for this upload step
                
            Returns:
                function: Callback function that updates progress
            """
            def percentage(progress):
                # Progress callback function for upload_progress_oss
                if self.is_aborted():
                    raise TaskRevokedError("Task aborted by user.")

                state_base.total_progress[key] = round(progress * weighed_progress, 2)
                self.update_state(meta=state_base.dict())

            return percentage

        # Upload potree files to OSS (40% of total progress)
        logger.info("Uploading potree files to OSS")
        cmd = [
            str(ossutil_path), "cp", "-r", "-f",
            str((OUTPUT_DIR / "potree").resolve()),
            OSS_BUCKET + UPLOAD_KEY
        ]
        run_command(cmd, progress_callback=progress_callback(0, 0.4))

        # Upload report ply to OSS (40% of total progress)
        logger.info("Uploading report PLY to OSS")
        to_oss_source_ply = UPLOAD_KEY + job.no + ".ply"
        cmd = [
            str(ossutil_path), "cp", "-f",
            str(Path(job.report_ply).resolve()),
            OSS_BUCKET + to_oss_source_ply
        ]
        run_command(cmd, progress_callback=progress_callback(1, 0.4))

        # Upload cover image to OSS (10% of total progress)
        logger.info("Uploading cover image to OSS")
        cover = Path(job.cover)
        to_oss_cover = UPLOAD_KEY + cover.name
        cmd = [
            str(ossutil_path), "cp",
            str(cover.resolve()),
            OSS_BUCKET + to_oss_cover
        ]
        run_command(cmd, progress_callback=progress_callback(2, 0.1))

        # Upload log file to OSS (5% of total progress)
        logger.info("Uploading log file to OSS")
        to_oss_log_file = UPLOAD_KEY + "runtime.log"
        cmd = [
            str(ossutil_path), "cp", "-f",
            str(Path(job.log_file).resolve()),
            OSS_BUCKET + to_oss_log_file
        ]
        run_command(cmd, progress_callback=progress_callback(3, 0.05))

        # Generate scene_result dict and send to server API
        import urllib.parse
        import requests

        http_source_ply = urllib.parse.urljoin(DOMAIN, to_oss_source_ply)
        http_ply_cloud_js = urllib.parse.urljoin(DOMAIN, UPLOAD_KEY + "cloud.js")
        http_log_file = urllib.parse.urljoin(DOMAIN, to_oss_log_file)
        http_cover = urllib.parse.urljoin(DOMAIN, to_oss_cover)

        logger.info("Sending scene result to server API")
        res = requests.post(
            os.getenv("3DCM_UPLOAD_TO_CLOUD"),
            headers={
                "Content-Type": "application/json",
                "token": token,
                "cid": cid,
                "terminal-id": terminal_id,
                "terminal-type": terminal_type
            },
            json={
                "filter_v2": {"logic":"AND","conditions":[{"field":"no","type":"text","operator":"eq","value": job.no}]},
                "run_progress": 100.00,
                "title": "" if job.title is None else job.title,
                "result_status": 2,
                "run_status": 2,
                "scene_result": {
                    "source_ply": http_source_ply,
                    "ply_cloud_js": http_ply_cloud_js,
                    "report": job.reports,
                },
                "log_file": http_log_file,
                "end_time": job.update_at.strftime("%Y-%m-%d %H:%M:%S"),
                "take_time": job.taken_at.strftime("%Y-%m-%d"),
                "start_time": job.create_at.strftime("%Y-%m-%d %H:%M:%S"),
                "sample_batch_number": job.sample_batch_number,
                "sample_number": job.sample_number,
                "no": job.no,
                "cid": cid,
                "terminal_type": terminal_type,
                "cover": http_cover,
                "image_total": job.image_total,
            })
        logger.info("☁ Sending scene_result to server API: status_code: %s, text: %s",
                    res.status_code,
                    res.text)

        # Mark tasks as completed if everything went well
        context = res.json()
        if res.status_code != 200 or len(context) == 0 or context[0].get("code") != 1:
            raise Exception("Server API returned error: %s", res.text)

        # Update task state in database
        state_base.state = utils.OdmJobStatus.completed.value
        state_base.total_progress[4] = 5.0

        # Update upload information using the new method
        upload_info = {
            "cloud_id": context[0].get("data").get("id"),
            "source_ply": http_source_ply,
            "ply_cloud_js": http_ply_cloud_js,
            "cover": http_cover,
            "log_file": http_log_file
        }
    except TaskRevokedError as e:
        logger.warning("Task %s was canceled", job.no, exc_info=True)
        state_base.state = utils.OdmJobStatus.canceled.value
        state_base.error = str(e)
    except Exception as e:
        # Handle any errors during processing
        logger.error("Job %s failed: %s", job.no, str(e), exc_info=True)
        state_base.state = utils.OdmJobStatus.failed.value
        state_base.error = str(e)
    finally:
        upload_info["state"] = state_base.state
        upload_info["progress"] = sum(state_base.total_progress.values())
        upload_info["err_msg"] = state_base.error
        # Update task state in database
        _update_upload_state(job.id, upload_info)

        return state_base.dict()


def get_image_files(src_file: Path):
    """
    Generator for supported image files
    
    Generator function that yields supported image files from a directory.
    
    Args:
        src_file (Path): Path to the source directory
        
    Yields:
        Path: Path to each supported image file
    """
    # Supported image file extensions
    extensions = {'.jpg', '.jpeg', '.png', '.bmp', '.webp', '.jp2'}
    # Copy all supported image files to destination directory
    for path in src_file.glob('*'):
        if path.is_file() and path.suffix.lower() in extensions:
            yield path
