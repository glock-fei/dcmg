import logging
import os
import shutil
import uuid
import zipfile
from enum import Enum
from pathlib import Path
from typing import Optional, Union, BinaryIO

import docker
from docker.types import DeviceRequest
from docker.errors import DockerException
from fastapi import UploadFile
from pydantic import BaseModel


class JobStatus(Enum):
    Waiting = "waiting"
    Running = "running"
    Completed = "completed"
    Failed = "failed"
    Uploaded = "uploaded"


class JobRunner(BaseModel):
    """
    The status of a job runner.
    """
    status: JobStatus
    progress: float
    run_id: Optional[str] = None


class JobSettings(BaseModel):
    """
    The settings of a job.
    """
    images_dir: str
    run_id: Optional[str] = None
    job_name: Optional[str] = None
    gsd_cm: float = 0.32
    row_spacing_cm: float = 80.0
    plant_spacing_cm: float = 10.0
    ridge_spacing_cm: Optional[float] = None
    big_threshold: float = 0.2
    small_threshold: float = 0.3


def calculate_healthy_score(vaild_ratio: float, total_ratio: float):
    """
    Calculate the healthy score of a job.
    args:
        vaild_ratio: float, the ratio of valid objects.
        total_ratio: float, the ratio of total objects.
    returns:
        score: float, the healthy score of the job.
        level: str, the level of the score.
    """
    total_ratio = total_ratio * 0.01
    vaild_ratio = vaild_ratio * 0.01
    score = round(min(total_ratio / 0.7, 1) * 60 + min(vaild_ratio / 0.7, 1) * 40, 1)

    if score >= 90:
        level = 'great'
    elif score >= 70:
        level = 'good'
    elif score >= 40:
        level = 'general'
    else:
        level = 'bad'

    return score, level


def move_ownership(src_file: UploadFile) -> UploadFile:
    """
    Move ownership of a file to a new UploadFile object.
    issue:
        Passing UploadFile objects into a StreamingResponse closes it in v0.106.0 but not v0.105.0
        see https://github.com/fastapi/fastapi/issues/10857 @msehnout
    args:
        src_file: UploadFile, the file to move ownership.
    returns:
        new_file: UploadFile, a new UploadFile object with ownership of the file.
    """
    dst_file = UploadFile(
        file=src_file.file,
        size=src_file.size,
        filename=src_file.filename,
        headers=src_file.headers,
    )
    src_file.file = BinaryIO()

    return dst_file


def extract_files_from_zip(
        filezip_path: Union[str, Path],
        output_dir: Union[str, Path],
        include_patterns: Optional[list] = None,
        is_copy: bool = True
):
    """
    Extract files from a zip file to a directory.
    args:
        filezip_path: Union[str, Path], the path of the zip file.
        output_dir: Union[str, Path], the directory to store the extracted files.
        include_patterns: Optional[list], lowercase file extensions to include in the extraction.
        is_copy: bool, whether to copy the files from the zip to the output directory.
    """
    dst_files = []

    with zipfile.ZipFile(filezip_path, 'r') as zip_ref:
        # Extract all files with image extensions
        for imgfile in zip_ref.infolist():
            imgpath = Path(imgfile.filename)
            # Check if the file extension is included in the include_patterns
            if include_patterns and imgpath.suffix.lower() not in include_patterns:
                logging.info("Skipping file %s with extension %s", imgfile.filename, imgpath.suffix.lower())
                continue
            target_path = (output_dir / imgpath.name).resolve()
            # Check if the file is outside the output directory
            if not target_path.is_relative_to(output_dir.resolve()):
                logging.error("Skipping file %s outside of output directory", imgfile.filename)
                continue
            # Save the file path and the zip info object for later use
            dst_files.append([target_path, imgfile])

        if is_copy:
            # copy the files from the zip to the output directory
            for i, (dst_path, src_path) in enumerate(dst_files, 1):
                with zip_ref.open(src_path) as src, open(dst_path, 'wb') as dst:
                    # Copy the file from the zip to the output directory
                    shutil.copyfileobj(src, dst)
                    logging.info("Extracting %s to %s", src_path.filename, dst_path)

    return dst_files


def generate_run_id():
    """
    Generate a unique id for the job.
    """
    return str(uuid.uuid4().hex)[:16]


def get_container_status(container_id: str):
    """
    Get the status of a container.
    args:
        container_id: str, the id of the container.
        progress: float, the progress of the job.
    """
    is_running = False

    try:
        client = docker.from_env()
        container = client.containers.get(container_id)

        is_running = container.status == "running"
    except DockerException:
        pass
    finally:
        return is_running


def get_container_logs(container_id: str):
    """
    Get the logs of a container.
    args:
        container_id: str, the id of the container.
    """
    client = docker.from_env()
    container = client.containers.get(container_id)
    return container.logs().decode("utf-8")


def start_scm_job_container(
        run_id: str,
        images_dir: Path,
        output_dir: Path,
        log_file: Path,
        plant_spacing_cm: float,
        row_spacing_cm: float,
        ridge_spacing_cm: Optional[float] = None,
        gsd_cm: float = 0.32,
        big_threshold: float = 0.3,
        small_threshold: float = 0.2,
):
    """
    Start a container to run the SCM job.
    args:
        run_id: str, the unique id of the job.
        images_dir: str, the directory to store the input images.
        output_dir: str, the directory to store the output images.
        log_file: str, the file to store the logs.
        plant_spacing_cm: float, the spacing between plants in cm.
        row_spacing_cm: float, the spacing between rows in cm.
        ridge_spacing_cm: Optional[float], the spacing between ridges in cm.
        gsd_cm: float, the ground sample distance in cm.
        big_threshold: float, the threshold for big objects in the image.
        small_threshold: float, the threshold for small objects in the image.
    envs:
        NOMAD_META_NO_ID: str, the unique id of the job.
        PROGRESS_URL: str, the url to update the progress of the job.
        REPORT_URL: str, the url to upload the report of the job.
        OSS_ACCESS_KEY_ID : str, the access key id of the oss.
        OSS_ACCESS_KEY_SECRET: str, the access key secret of the oss.
        OSS_BUCKET: str, the bucket of the oss.
        OSS_ENDPOINT: str, the endpoint of the oss.
        OSS_REGION: str, the region of the oss.
        OSS_DOMAIN: str, the domain of the oss.
        OSS_UPLOAD_KEY: str, the key to upload the output images to the oss.
    """
    CONTAINER_OUTPUT_DIR = "/app/output"
    # set up the command to run the SCM job
    command = [
        "./predictor",
        "--plant_spacing", str(plant_spacing_cm),
        "--row_spacing", str(row_spacing_cm),
        "--gsd", str(gsd_cm),
        "--big_threshold", str(big_threshold),
        "--small_threshold", str(small_threshold),
        "--output_dir", CONTAINER_OUTPUT_DIR,
        "--private_key", os.getenv("SCM_PRIVATE_KEY"),
    ]

    if ridge_spacing_cm:
        command.extend(["--ridge_spacing", str(ridge_spacing_cm)])

    # initialize docker client
    work_dir = Path(os.getcwd())
    client = docker.from_env()
    container = client.containers.run(
        image=os.getenv("SCM_IMAGE"),
        device_requests=[
            DeviceRequest(count=1, capabilities=[["gpu"]])
        ],
        command=command,
        tty=True,
        environment={
            "NOMAD_META_NO_ID": run_id,
            "SCM_PROGRESS_URL": os.getenv("SCM_PROGRESS_URL"),
            "SCM_REPORT_URL": os.getenv("SCM_REPORT_URL"),
            "OSS_ACCESS_KEY_ID": os.getenv("OSS_ACCESS_KEY_ID"),
            "OSS_ACCESS_KEY_SECRET": os.getenv("OSS_ACCESS_KEY_SECRET"),
            "OSS_BUCKET": os.getenv("OSS_BUCKET"),
            "OSS_ENDPOINT": os.getenv("OSS_ENDPOINT"),
            "OSS_REGION": os.getenv("OSS_REGION"),
            "OSS_DOMAIN": os.getenv("OSS_DOMAIN"),
            "OSS_UPLOAD_KEY": os.getenv("OSS_UPLOAD_KEY")
        },
        extra_hosts={
            os.getenv("SERVICE_HOST_GATEWAY"): "host-gateway"
        },
        remove=True,
        detach=True,
        volumes={
            str(images_dir): {
                "bind": "/app/images",
                "mode": "rw"
            },
            str(log_file): {
                "bind": "/app/app.log",
                "mode": "rw"
            },
            str(output_dir): {
                "bind": CONTAINER_OUTPUT_DIR,
                "mode": "rw"
            },
            str(work_dir / "docker/scm/license"): {
                "bind": "/app/license",
                "mode": "ro"
            },
            str(work_dir / "docker/scm/utils.py"): {
                "bind": "/app/utils.py",
                "mode": "rw"
            }
        }
    )

    return container.id
