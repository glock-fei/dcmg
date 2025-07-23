import logging
import os
import shutil
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Depends, UploadFile, HTTPException
from sqlalchemy.orm import Session, selectinload

from models import Job
from models.detask import Picture
from models.session import get_database
from utils.cedoke import start_scm_job_container, get_container_status, generate_run_id, extract_files_from_zip, \
    JobStatus, JobRunner

router = APIRouter(prefix='/scm')


@router.post('/run_job_with_imgzip')
async def run_job_with_imgzip(
        image: UploadFile,
        job_name: Optional[str] = None,
        gsd_cm: float = 0.32,
        row_spacing_cm: float = 80.0,
        plant_spacing_cm: float = 10.0,
        ridge_spacing_cm: Optional[float] = None,
        big_threshold: float = 0.2,
        small_threshold: float = 0.3,
        db: Session = Depends(get_database)
):
    """
    This endpoint is used to run a task with an image ZIP file.

    ## args:

    - **image**: The ZIP file containing the images.Example structure:<br/>
        myzip.zip<br/>
        ├── folder_name/<br/>
        │   ├── 1.jpg<br/>
        │   └── 2.png<br/>
    - **job_name**: The name of the task.<br/>
    - **gsd_cm**: Ground sample distance in cm/pixel from DJI Mavic Pro, default: 0.32<br/>
    - **row_spacing_cm**: Row spacing in cm, default: 80.0<br/>
    - **plant_spacing_cm**: Plant spacing in cm, default: 10.0<br/>
    - **ridge_spacing_cm**: Ridge spacing in cm, default: None<br/>
    - **big_threshold**: Set the big seedling size threshold to filter out small plants, default: 0.2<br/>
    - **small_threshold**: Set the small seedling size threshold to filter out big plants, default: 0.3
    """
    if not image.filename.endswith(".zip"):
        raise HTTPException(400, "Only ZIP files are allowed")

    # Save the file to a temporary directory
    imgzip_path = Path("tmp/") / image.filename
    with open(imgzip_path, "wb") as buffer:
        shutil.copyfileobj(image.file, buffer)
        logging.info("Upload file successful to %s", imgzip_path)

    # input images directory
    run_id = generate_run_id()
    images_dir = Path("static") / run_id

    # output images directory
    output_dir = images_dir / "output"
    log_file = output_dir / "app.log"

    # Create the output directory if it doesn't exist
    if not output_dir.exists():
        output_dir.mkdir(parents=True)
        log_file.touch()

    # Extract the ZIP file to the output directory
    include_patterns = [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"]
    dst_files = extract_files_from_zip(imgzip_path, images_dir.absolute(), include_patterns=include_patterns)

    # remove the temporary file
    imgzip_path.unlink()

    # Start the Docker container
    container_id = start_scm_job_container(
        run_id=run_id,
        images_dir=images_dir.absolute(),
        output_dir=output_dir.absolute(),
        log_file=log_file.absolute(),
        plant_spacing_cm=plant_spacing_cm,
        row_spacing_cm=row_spacing_cm,
        ridge_spacing_cm=ridge_spacing_cm,
        gsd_cm=gsd_cm,
        big_threshold=big_threshold,
        small_threshold=small_threshold
    )

    # Create a new task in the database
    new_job = Job(
        job_name=job_name if job_name else run_id,
        run_id=run_id,
        gsd=gsd_cm,
        row_spacing=row_spacing_cm,
        plant_spacing=plant_spacing_cm,
        ridge_spacing=ridge_spacing_cm,
        big_threshold=big_threshold,
        small_threshold=small_threshold,
        images_dir=str(images_dir),
        output_dir=str(output_dir),
        log_file=str(log_file),
        images_total_num=len(dst_files),
        container_id=container_id
    )
    db.add(new_job)
    db.commit()
    db.refresh(new_job)

    return new_job


@router.get('/get_job_details')
async def get_job_details(
        job_id: str,
        db: Session = Depends(get_database)
):
    """
    This endpoint is used to get the details of a task.
    The task details include the input parameters, the progress, and the list of detected images.

    ## args:

    - **job_id**: The ID of the task.

    ## return
    - **job**: The details of the task.
    """
    job = db.query(Job).filter(Job.id == job_id).options(selectinload(Job.pictures)).first()
    if not job:
        raise HTTPException(404, "Job not found")

    return job


@router.get('/get_job_logs')
async def get_job_logs(
        job_id: str,
        db: Session = Depends(get_database)
):
    """
    This endpoint is used to get the logs of a task.

    ## args:

    - **job_id**: The ID of the task.

    ## return
    - **logs**: The logs of the task.
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found")

    with open(job.log_file, "r", encoding="utf-8") as f:
        logs = f.read()

    return logs


@router.get('/get_job_status')
async def get_job_status(
        job_id: str,
        db: Session = Depends(get_database)
) -> JobRunner:
    """
    This endpoint is used to query the status of a task.

    ## args:

    - **job_id**: The ID of the task.

    ## return
    - **is_running**: A boolean value indicating whether the task is running or not.
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found")

    # Check the container status
    is_running = get_container_status(job.container_id)
    if job.progress >= 100.00:
        sts = JobStatus.Completed
    else:
        sts = JobStatus.Running if is_running else JobStatus.Failed

    return JobRunner(status=sts, progress=job.progress, run_id=job.run_id)


@router.get('/get_jobs')
async def get_jobs(
        page: int = 1,
        limit: int = 10,
        db: Session = Depends(get_database)
):
    """
    This endpoint is used to get a list of all tasks.

    ## args:

    - **page**: The page number to retrieve, default: 1
    - **limit**: The number of tasks to retrieve per page, default: 10

    ## return
    - **jobs**: A list of all tasks.
    """
    jobs = db.query(Job).offset((page - 1) * limit).limit(limit).all()

    return jobs


@router.get('/update_job_progress')
async def update_job_progress(
        run_id: str,
        percent: float,
        db: Session = Depends(get_database)
):
    """
    This endpoint is used to update the progress of a task.

    only used by the container callback to update the progress.

    ## args:

    - **run_id**: The ID of the task.
    - **percent**: The progress of the task in percentage.

    ## return
    - **progress**: The progress of the task.
    """
    job = db.query(Job).filter(Job.run_id == run_id).first()
    if not job:
        raise HTTPException(404, "Job not found")

    job.progress = percent
    db.commit()
    db.refresh(job)

    return job.progress


@router.post('/save_report')
async def save_report(
        data: dict,
        db: Session = Depends(get_database)
):
    """
    This endpoint is used to save the report of a task.

    The report is a JSON object containing the detected images and their properties.

    only used by the container callback to save the report.
    """
    run_id = data.get("run_id")

    job = db.query(Job).filter(Job.run_id == run_id).first()
    if not job:
        raise HTTPException(404, "Job not found")

    # Update the job details
    for k, v in data.items():
        if k in Job.__table__.columns.keys():
            setattr(job, k, v)

    deteced_images = data.get("images", [])
    job.images_detect_num = len(deteced_images)
    # Save the pictures
    for image in deteced_images:
        picture = Picture(job_id=job.id, **{k: v for k, v in image.items() if k in Picture.__table__.columns.keys()})

        # Rewrite the output path to be relative to the designated output directory,
        # and utilize the forward slash / as the separator when constructing HTTP URLs.
        picture.output = os.path.normpath(Path(job.output_dir) / Path(picture.output).name).replace(os.sep, '/')
        db.add(picture)

    db.commit()
    db.refresh(job)

    return job
