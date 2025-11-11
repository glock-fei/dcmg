import hashlib
import logging
import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated

import docker
from celery.contrib.abortable import AbortableAsyncResult
from docker.errors import APIError
from fastapi import APIRouter, Depends, status, Header
from fastapi.exceptions import HTTPException
from sqlalchemy import desc
from sqlalchemy.orm import Session
import models
import utils
import worker.tasks as tasks

from utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)
router = APIRouter(prefix='/3dcm')


@router.post('/jobs', status_code=status.HTTP_201_CREATED)
async def create_3dcm_job(data: utils.VdcmCreate, db: Session = Depends(models.get_database)):
    """
    ### Create a new VDCM job

    This endpoint creates a new VDCM job by:
    1. Checking if a job with the same ID already exists
    2. Validating that the source file exists
    3. Creating destination folders for output
    4. Starting the reconstruction task in Celery
    5. Saving the job information to the database

    ### Parameters:
    - **data**: VdcmCreate model containing job information including task ID and source path
    - **db**: Database session dependency

    ### Special handling:
    - **no**: If not provided, a unique ID will be generated in the format PXXXXX_YY_SAMPLEBATCH_SAMPLE,
      where XXXXX is a 5-digit number and YY is a 2-digit number
    - **taken_at**: If not provided, it will be set to the creation time of the source file

    ### Returns:
    - **VdcmJobs**: The created job object with all details

    ### Raises:
    - **HTTPException**: 400 error if task with same ID already exists
    - **HTTPException**: 400 error if source file does not exist
    """
    if not data.no:
        # Generate a unique ID for the job
        hex_digest = hashlib.sha256(str(data.__dict__).encode()).hexdigest()
        data.no = "P{:05d}_{:02d}_{}_{}".format(
            int(hex_digest[:5], 16) % 100000,
            int(hex_digest[-3:], 16) % 100,
            data.sample_batch_number,
            data.sample_number
        )

    job: models.VdcmJobs = db.query(models.VdcmJobs).filter(models.VdcmJobs.no == data.no).first()
    # Check if job already exists
    if job:
        raise HTTPException(
            status_code=400,
            detail=_("Task with no %(no)s already exists. Duplicate tasks are not allowed.") % {"no": data.no}
        )

    # Check if source file exists
    if not Path(data.src_path).exists():
        raise HTTPException(status_code=400, detail=_("Source file does not exist."))

    # Set taken_at if not provided
    if not data.taken_at:
        stat = os.stat(data.src_path)
        data.taken_at = datetime.fromtimestamp(stat.st_ctime).date()

    # Create destination folder for reconstruction output
    dest_path = Path(os.getenv('STATIC_DIR', "static")) / "3dcm" / data.no
    log_output_dir = dest_path / "output"
    runtime_log_file = log_output_dir / "runtime.log"
    app_log_file = log_output_dir / "app.log"

    log_output_dir.mkdir(parents=True, exist_ok=True)
    runtime_log_file.touch()
    app_log_file.touch()

    # Create reconstruction task
    task = tasks.reconstruction.delay(
        no=data.no,
        src_path=data.src_path,
        runtime_log_file=str(runtime_log_file.resolve()),
        app_log_file=str(app_log_file.resolve()),
        dest_folder=str(dest_path)
    )
    logging.info("Created reconstruction task %s, status: %s", task.id, task.status)

    # Create a new RSDM job record
    new_3dcm_job = models.VdcmJobs(
        **data.dict(),
        dest_path=str(dest_path),
        celery_task_id=task.id,
        state=utils.OdmJobStatus.pending.value,
        log_file=str(runtime_log_file)
    )

    # Add the new job to the database
    db.add(new_3dcm_job)
    db.commit()
    db.refresh(new_3dcm_job)

    return new_3dcm_job


@router.patch('/jobs/{no}/progress')
async def update_job_progress(no: str, percent: float, db: Session = Depends(models.get_database)):
    """
    ### Update the progress of a VDCM job

    This endpoint updates the progress percentage of a running VDCM job:
    1. Finds the job in the database using the task ID
    2. Validates that the job exists
    3. Updates the progress field with the provided percentage
    4. Commits the changes to the database

    ### Parameters:
    - **no**: The ID of the task to update
    - **percent**: The progress of the task in percentage
    - **db**: Database session dependency

    ### Returns:
    - **float**: The updated progress value rounded to 2 decimal places

    ### Raises:
    - **HTTPException**: 404 error if task with specified ID does not exist
    """
    job: models.VdcmJobs = db.query(models.VdcmJobs).filter(models.VdcmJobs.no == no).first()

    # Check if job already exists
    if not job:
        raise HTTPException(status_code=404, detail=_("Task with no %(no)s does not exist.") % {"no": no})

    job.progress = round(percent, 2)
    job.state = utils.OdmJobStatus.running.value
    job.updated_at = datetime.now()
    db.commit()

    return job.progress


@router.get('/jobs')
async def get_jobs(
        page: int = 1,
        limit: int = 1000,
        state: utils.OdmJobStatus = None,
        title: str = None,
        db: Session = Depends(models.get_database)
):
    """
    ### Retrieve VDCM jobs

    This endpoint retrieves a list of VDCM jobs with pagination support:
    1. Queries the database for VDCM jobs
    2. Order results by ID in descending order (the newest first)
    3. Applies pagination using offset and limit
    4. Optionally filters to show only running jobs
    5. Updates upload progress from Celery tasks if available

    ### Parameters:
    - **page**: Page number for pagination (default: 1)
    - **limit**: Number of items per page (default: 1000)
    - **only_running**: Flag to filter only running jobs (default: True)
    - **db**: Database session dependency

    ### Returns:
    - **List[VdcmJobs]**: List of VDCM job objects matching the criteria
    
    ### Upload Status:
    - If uploads is not null and State is COMPLETED and uploads.cloud_id > 0,
      it indicates that the upload has been completed and there is no need to upload again.
    """
    # Query VDCM jobs and join with uploads relation for complete information
    query = db.query(models.VdcmJobs).order_by(desc(models.VdcmJobs.id))
    query = query.outerjoin(models.VdcmUploads, models.VdcmUploads.job_id == models.VdcmJobs.id)

    # Filter by state and title if provided
    if state:
        query = query.filter(models.VdcmJobs.state == state.value)
    if title:
        query = query.filter(models.VdcmJobs.title.ilike(f'%{title}%'))

    # Apply pagination to limit the number of results
    data: list[models.VdcmJobs] = query.offset((page - 1) * limit).limit(limit).all()

    result = []
    # Process each job to filter based on running status and update upload progress
    for job in data:
        # If job has uploads, update progress from Celery task state
        if job.uploads:
            # Get the Celery task associated with the upload
            task = AbortableAsyncResult(job.uploads.celery_task_id)
            # Check if task exists and has result data
            if task.name and task.worker and task.result:
                # Parse task result to get current state and progress information
                current_state = utils.OdmUploadState(**task.result)
                # Calculate total progress and round to 2 decimal places
                job.uploads.progress = round(sum(current_state.total_progress.values()), 2)

        result.append(job)

    return result


@router.post('/jobs/{no}/cancel')
async def cancel_job_by_no(
        no: str,
        db: Session = Depends(models.get_database)
):
    """
    ### Cancel VDCM jobs

    This endpoint cancels a running VDCM job:
    1. Finds the job in the database using the task ID
    2. Validates that the job exists
    3. Validates that the job is running
    4. Cancels the Celery task
    5. Updates the job state to canceled in the database

    ### Parameters:
    - **no**: The ID of the task to cancel
    - **db**: Database session dependency

    ### Returns:
    - **VdcmJobs**: The updated job object with the canceled state

    ### Raises:
    - **HTTPException**: 404 error if task with specified ID does not exist
    - **HTTPException**: 400 error if task is not running or pending
    """
    job: models.VdcmJobs = db.query(models.VdcmJobs).filter(models.VdcmJobs.no == no).first()
    # Check if job already exists
    if not job:
        raise HTTPException(status_code=404, detail=_("Task with no %(no)s does not exist.") % {"no": no})

    if job.state not in [utils.OdmJobStatus.running.value, utils.OdmJobStatus.pending.value]:
        raise HTTPException(status_code=400, detail=_("Task with no %(no)s is not running.") % {"no": no})

    if not tasks.abort_task(celery_task_id=job.celery_task_id):
        raise HTTPException(status_code=500, detail=_("Failed to cancel celery task."))

    time.sleep(3)
    # Update the job state to canceled in the database
    job.state = utils.OdmJobStatus.canceled.value
    job.update_at = datetime.now()
    db.commit()
    db.refresh(job)

    return job


@router.delete('/jobs/{no}', status_code=status.HTTP_204_NO_CONTENT)
async def remove_job_by_no(
        no: str,
        db: Session = Depends(models.get_database)
):
    """
    ### Remove a VDCM job completely

    This endpoint removes a VDCM job completely from the system:
    1. Finds the job in the database using the task ID
    2. Validates that the job exists
    3. Validates that the job is not running
    4. Removes all files associated with the job
    5. Deletes the job record from the database

    ### Parameters:
    - **no**: The ID of the task to remove
    - **db**: Database session dependency

    ### Returns:
    - **None**: Returns 204 No Content on successful removal

    ### Raises:
    - **HTTPException**: 404 error if task with specified ID does not exist
    - **HTTPException**: 400 error if task is still running
    """
    job: models.VdcmJobs = db.query(models.VdcmJobs).filter(models.VdcmJobs.no == no).first()
    # Check if job already exists
    if not job:
        raise HTTPException(status_code=404, detail=_("Task with no %(no)s does not exist.") % {"no": no})

    if job.state in [utils.OdmJobStatus.running.value, utils.OdmJobStatus.pending.value]:
        raise HTTPException(status_code=400,
                            detail=_("Task with no %(no)s is still running, cannot remove.") % {"no": no})

    # Remove all files in the dest_path directory
    if job.dest_path:
        dest_path = Path(job.dest_path)
        if dest_path.exists() and dest_path.is_dir():
            shutil.rmtree(dest_path)
            logger.info("Removed all files in directory: %s", dest_path)

    # Remove the container
    if job.container_id:
        try:
            client = docker.from_env()
            container = client.containers.get(job.container_id)
            container.remove(force=True)
            logger.info("Removed container: %s", container.id)
        except APIError:
            logger.warning("Failed to remove container: %s", job.container_id)

    db.delete(job)
    db.commit()


@router.post('/jobs/{no}/upload')
def upload_report(
        no: str,
        token: Annotated[str, Header()],
        cid: Annotated[str, Header()],
        terminal_id: Annotated[str, Header()],
        terminal_type: Annotated[str, Header()],
        db: Session = Depends(models.get_database)
):
    """
    ### Initiate report upload process for a completed VDCM job
    
    This endpoint initiates the process of uploading a completed VDCM job's report:
    1. Validates that the job exists and is completed
    2. Creates or reuses an existing upload record
    3. Starts the upload task in Celery
    4. Resets upload-related fields for re-upload if applicable
    
    ### Parameters:
    - **no**: The ID of the task to upload report for
    - **token**: Authentication token for API requests
    - **cid**: Client ID for API requests
    - **terminal_id**: Terminal ID for API requests
    - **terminal_type**: Terminal type for API requests
    - **db**: Database session dependency
    
    ### Returns:
    - **VdcmUploads**: The upload task object with initial state
    
    ### Raises:
    - **HTTPException**: 404 error if task doesn't exist
    - **HTTPException**: 400 error if task isn't completed
    - **HTTPException**: 400 error if upload is already in progress
    """
    # Query job with its uploads relation
    query = db.query(models.VdcmJobs).outerjoin(models.VdcmUploads, models.VdcmUploads.job_id == models.VdcmJobs.id)
    query = query.filter(models.VdcmJobs.no == no)
    job: models.VdcmJobs = query.first()

    # Validate that job exists
    if not job:
        raise HTTPException(status_code=404, detail=_("Task with no %(no)s does not exist.") % {"no": no})

    # Validate that job is completed before allowing upload
    if job.state != utils.OdmJobStatus.completed.value:
        raise HTTPException(status_code=400, detail=_("Task with no %(no)s is not completed,"
                                                      " cannot upload report.") % {"no": no})

    if job.uploads and job.uploads.state in [utils.OdmJobStatus.running.value, utils.OdmJobStatus.pending.value]:
        raise HTTPException(status_code=400, detail=_("Report upload for task with no %(no)s is "
                                                      "already in progress, cannot upload again.") % {"no": no})

    # Start the upload task in Celery
    upload_task = tasks.update_phenotype_report.delay(job_id=job.id, token=token, cid=cid, terminal_id=terminal_id,
                                                      terminal_type=terminal_type)

    # If upload record already exists, reset it for re-upload
    if job.uploads:
        # Reset all upload-related fields
        job.uploads.state = utils.OdmJobStatus.pending.value
        job.uploads.celery_task_id = upload_task.id
        job.uploads.progress = 0.0
        job.uploads.cloud_id = None
        job.uploads.source_ply = None
        job.uploads.ply_cloud_js = None
        job.uploads.cover = None
        job.uploads.log_file = None

        # Commit changes to database
        db.commit()
        db.refresh(job.uploads)

        return job.uploads

    # Create new upload record if one doesn't exist
    new_upload_task = models.VdcmUploads(
        celery_task_id=upload_task.id,
        job_id=job.id,
        state=utils.OdmJobStatus.pending.value,
    )

    # Add to database and return
    db.add(new_upload_task)
    db.commit()
    db.refresh(new_upload_task)

    return new_upload_task


@router.put('/jobs/{no}')
async def update_job(
        no: str,
        job_update: utils.VdcmBase,
        db: Session = Depends(models.get_database)
):
    """
    ### Update a VDCM job

    This endpoint updates an existing VDCM job:
    1. Finds the job in the database using the task ID
    2. Validates that the job exists
    3. Updates only the allowed job fields with the provided data
    4. Commits the changes to the database

    ### Parameters:
    - **no**: The ID of the task to update
    - **job_update**: VdcmUpdate model containing updated job information (only title and frame_count can be updated)
    - **db**: Database session dependency

    ### Returns:
    - **VdcmJobs**: The updated job object

    ### Raises:
    - **HTTPException**: 404 error if task with specified ID does not exist
    """
    job: models.VdcmJobs = db.query(models.VdcmJobs).filter(models.VdcmJobs.no == no).first()

    # Check if job exists
    if not job:
        raise HTTPException(status_code=404, detail=_("Task with no %(no)s does not exist.") % {"no": no})

    job.title = job_update.title
    job.sample_number = job_update.sample_number
    job.sample_batch_number = job_update.sample_batch_number

    # Update the updated_at timestamp
    job.update_at = datetime.now()
    
    # Commit changes to database
    db.commit()
    db.refresh(job)

    return job
