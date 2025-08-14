import logging
import os
import shutil
import zipfile
from pathlib import Path
from fastapi import APIRouter, Depends, UploadFile, HTTPException, File
from sqlalchemy import desc
from sqlalchemy.orm import Session, selectinload
from starlette.responses import StreamingResponse

from models import Job
from models.detask import Picture
from models.session import get_database
from utils.cedoke import (
    start_scm_job_container,
    get_container_status,
    generate_run_id,
    extract_files_from_zip,
    JobStatus,
    JobRunner,
    move_ownership,
    JobSettings, calculate_healthy_score
)

router = APIRouter(prefix='/scm')


@router.get('/upload_imgzip_with_usb')
async def upload_imgzip_with_usb(image: str):
    """
    This function uploads the image file to the USB drive and returns a generator that yields the progress of the upload.

    ## args:

    - **image**: The name of the image file.

    ## return

    - **upload_progress**: The progress of the upload.
    """
    imgzip = Path(os.getenv("MOUNT_USB_PATH")).joinpath(image.strip("/"))
    if not imgzip.name.endswith(".zip"):
        raise HTTPException(400, "Only ZIP files are allowed")

    # input images directory
    run_id = generate_run_id()
    images_dir = Path("static") / run_id
    images_dir.mkdir(parents=True, exist_ok=True)

    def event_generator():
        # Extract the ZIP file to the output directory
        include_patterns = [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"]
        dst_files = extract_files_from_zip(
            imgzip,
            images_dir.absolute(),
            include_patterns=include_patterns,
            is_copy=False
        )

        file_count = len(dst_files)
        with zipfile.ZipFile(imgzip, 'r') as zip_ref:
            for i, (dst_path, src_path) in enumerate(dst_files, 1):
                with zip_ref.open(src_path) as src, open(dst_path, 'wb') as dst:
                    # Copy the file from the zip to the output directory
                    shutil.copyfileobj(src, dst)

                    # Calculate the progress
                    progress = (i / file_count) * 100
                    yield f"Progress: {progress:.2f}\n"

                    logging.info("Extracting %s to %s", src_path.filename, dst_path)

        yield f"Done: {run_id}, {str(images_dir.as_posix())}, {len(dst_files)}"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"}
    )


@router.post('/upload_imgzip_chunked')
async def upload_imgzip_chunked(image: UploadFile = File(...)):
    """
    This function chunks the uploaded image file and returns a generator that yields the progress of the upload.

    ## args:

    - **image**: The ZIP file containing the images.Example structure:<br/>
        myzip.zip<br/>
        ├── folder_name/<br/>
        │   ├── 1.jpg<br/>
        │   └── 2.png<br/>

    ## return
    - **upload_progress**: The progress of the upload.
    """
    if not image.filename.endswith(".zip"):
        raise HTTPException(400, "Only ZIP files are allowed")

    imgzip_path = Path("tmp/") / image.filename
    dst_file = move_ownership(image)
    logging.info("move ownership successful, file size: %s", dst_file.size)

    # closure function to yield the progress of the upload
    async def event_generator():
        try:
            total_size = 0
            with open(imgzip_path, 'wb') as f:
                while chunk := await dst_file.read(1024 * 1024 * 10):
                    # write the chunk to the file
                    f.write(chunk)
                    total_size += len(chunk)
                    progress = (total_size / dst_file.size) * 100

                    # extract images when the upload is complete
                    if progress >= 100.00:
                        # input images directory
                        run_id = generate_run_id()
                        images_dir = Path("static") / run_id

                        # Create the output directory if it doesn't exist
                        if not images_dir.exists():
                            images_dir.mkdir(parents=True)

                        # Extract the ZIP file to the output directory
                        include_patterns = [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"]
                        dst_files = extract_files_from_zip(
                            imgzip_path,
                            images_dir.absolute(),
                            include_patterns=include_patterns
                        )
                        # yield the result
                        yield f"{run_id}, {str(images_dir.as_posix())}, {len(dst_files)}"
        finally:
            dst_file.file.close()
            imgzip_path.unlink()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"}
    )


@router.post('/run_job')
async def run_job(
        data: JobSettings,
        db: Session = Depends(get_database)
):
    """
    This endpoint is used to run a task with an image ZIP file.

    ## args:

    - **images_dir**: The directory containing the images.<br/>
    - **run_id**: The ID of the task.<br/>
    - **job_name**: The name of the task.<br/>
    - **gsd_cm**: The ground sampling distance in centimeters.<br/>
    - **row_spacing_cm**: The row spacing in centimeters.<br/>
    - **plant_spacing_cm**: The plant spacing in centimeters.<br/>
    - **ridge_spacing_cm**: The ridge spacing in centimeters.<br/>
    - **big_threshold**: The threshold for big objects in the image.<br/>
    - **small_threshold**: The threshold for small objects in the image.

    ## return

    - **job**: The details of the task.
    """

    # input images directory
    run_id = data.run_id
    if not run_id:
        run_id = generate_run_id()

    # output images directory
    images_dir = Path(data.images_dir.strip())
    output_dir = images_dir / "output"
    log_file = output_dir / "app.log"

    # Create the output directory if it doesn't exist
    if not output_dir.exists():
        output_dir.mkdir()
        log_file.touch()

    # Start the Docker container
    container_id = start_scm_job_container(
        run_id=run_id,
        images_dir=Path(images_dir).absolute(),
        output_dir=output_dir.absolute(),
        log_file=log_file.absolute(),
        plant_spacing_cm=data.plant_spacing_cm,
        row_spacing_cm=data.row_spacing_cm,
        ridge_spacing_cm=data.ridge_spacing_cm,
        gsd_cm=data.gsd_cm,
        big_threshold=data.big_threshold,
        small_threshold=data.small_threshold
    )

    # Create a new task in the database
    new_job = Job(
        job_name=data.job_name.strip() if data.job_name else run_id,
        run_id=run_id,
        gsd=data.gsd_cm,
        row_spacing=data.row_spacing_cm,
        plant_spacing=data.plant_spacing_cm,
        ridge_spacing=data.ridge_spacing_cm,
        big_threshold=data.big_threshold,
        small_threshold=data.small_threshold,
        images_dir=str(images_dir),
        output_dir=str(output_dir),
        log_file=str(log_file),
        images_total_num=len([f for f in images_dir.iterdir() if f.is_file()]),
        container_id=container_id
    )
    db.add(new_job)
    db.commit()
    db.refresh(new_job)

    return new_job


@router.get('/get_job_details')
async def get_job_details(
        job_id: int,
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
        job_id: int,
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


@router.put('/job_uploaded')
async def update_job_status(
        job_id: int,
        db: Session = Depends(get_database)
):
    """
    This endpoint is used to update the status of a task.

    ## args:

    - **job_id**: The ID of the task.

    ## return
    - **success**: A boolean value indicating whether the status is updated successfully or not.
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found")

    job.status = JobStatus.Uploaded.value
    db.commit()
    db.refresh(job)

    return True


@router.get('/get_job_status')
async def get_job_status(
        job_id: int,
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

    job.status = sts.value
    db.commit()
    db.refresh(job)

    return JobRunner(status=sts, progress=job.progress, run_id=job.run_id)


@router.get('/get_jobs')
async def get_jobs(
        page: int = 1,
        limit: int = 10,
        status: str = None,
        job_name: str = None,
        db: Session = Depends(get_database)
):
    """
    This endpoint is used to get a list of all tasks.

    ## args:

    - **page**: The page number to retrieve, default: 1
    - **limit**: The number of tasks to retrieve per page, default: 10
    - **status**: The status of the tasks to retrieve, comma-separated, default: None (all)
      - **waiting**: The task is waiting to be processed.
      - **running**: The task is currently running.
      - **completed**: The task has completed.
      - **failed**: The task has failed.
      - **uploaded**: The task has been uploaded.
    - **job_name**: The name of the tasks to retrieve, default: None (all)

    ## return
    - **jobs**: A list of all tasks.
    """
    query = db.query(Job).order_by(desc(Job.id))

    if status:
        query = query.filter(Job.status.in_(status.split(",")))
    if job_name:
        query = query.filter(Job.job_name.ilike(f"%{job_name}%"))

    jobs = query.offset((page - 1) * limit).limit(limit).all()

    return jobs


@router.delete('/remove_job_all_res_by_id')
async def remove_job_all_res_by_id(
        job_id: int,
        db: Session = Depends(get_database)
):
    """
    This endpoint is used to remove a task and all its related resources (images, logs, etc.).

    ## args:

    - **job_id**: The ID of the task.

    ## return

    - **success**: A boolean value indicating whether the task is removed successfully or not.
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found")

    db.delete(job)
    db.commit()

    # Remove the output directory and log file
    images_dir = Path(job.images_dir)
    if images_dir.exists():
        shutil.rmtree(images_dir)

    return True


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

    score, level = calculate_healthy_score(job.vaild_ratio, job.total_ratio)
    job.score = score
    job.level = level
    job.progress = 100.00

    # Save the detected images
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
