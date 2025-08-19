import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc
from sqlalchemy.orm import Session

from models import RsdmJobs
from models.session import get_database
from utils.cedoke import generate_run_id
from utils import OdmJob, find_images, remove_odm_task, OdmState, OdmJobStatus, get_dest_folder, get_src_folder
from worker.tasks.odm import copy_image_to_odm, abort_task, get_current_state

logger = logging.getLogger(__name__)
router = APIRouter(prefix='/odm')


@router.get('/get_odm_jobs')
async def get_odm_jobs(
        page: int = 1,
        limit: int = 1000,
        only_running: bool = True,
        db: Session = Depends(get_database)
):
    """
    ## Get ODM tasks List
    The endpoint returns a list of ODM tasks.

    ### This endpoint performs the following operations:
    1. Fetches a list of RSDM jobs from the database
    2. Filters running jobs if `only_running` is set to `True`
    3. Returns the filtered list of jobs

    ### Parameters
    - **page**: Page number (default: 1)
    - **limit**: Items per page (default: 1000)
    - **only_running**: Filter running jobs only (default: True)

    ### State
    - **PENDING**: The job is waiting to be processed
    - **RUNNING**: The job is currently processing
    - **COMPLETED**: The job has completed successfully
    - **FAILED**: The job has failed
    - **CANCELED**: The job has been canceled by the user or system

    ### Response
    Returns `List[RsdmJobs]` with fields:
    - **run_id**: Job unique identifier
    - **odm_project_id**: Related project ID
    - **odm_job_name**: Human-readable job name
    - **odm_src_folder**: Data storage path
    - ...other fields...

    ### Example
    ```json
    [{
      "id": 1,
      "odm_project_id": 1,
      "odm_job_name": "test",
      "odm_src_folder": "images/",
      "odm_samplinge_time": "2025-08-17T09:04:40.311000",
      "odm_image_count": 56,
      "progress": 0,
      "celery_task_id": "66ac0298-e3c0-4744-ae5c-62567f292ae0",
      "update_at": "2025-08-17T17:00:41.479989",
      "odm_task_id": "8671d618-6cd7-40ce-aba4-45e85eab19fb",
      "run_id": "daccd58c51e94960",
      "odm_job_type": "multispectral",
      "odm_dest_folder": "E:\\python\\dcmg\\tmp\\1\\8671d618-6cd7-40ce-aba4-45e85eab19fb",
      "odm_host": "http://192.168.3.194:7000/",
      "odm_create_at": "2025-08-17T09:04:40.311000",
      "state": "PENDING",
      "err_msg": null
    }]
    ```
    """
    query = db.query(RsdmJobs)
    data: list[RsdmJobs] = query.order_by(desc(RsdmJobs.id)).offset((page - 1) * limit).limit(limit).all()

    result = []
    for job in data:
        odm_state = get_current_state(job.celery_task_id)
        if odm_state:
            job.state = odm_state.state
            job.progress = odm_state.progress

        if not only_running:
            result.append(job)
        elif odm_state.progress < 100:
            result.append(job)
    return result


@router.post('/create_odm_job')
async def create_odm_job(data: OdmJob, db: Session = Depends(get_database)):
    """
    ## Create ODM Task
    Create a new ODM task and initiate the image processing pipeline.

    ### This endpoint performs the following operations:
    1. Scans the specified folder for images matching the job type
    2. Validates that images are found; removes ODM task if none exist
    3. Creates a new RSDM job record in the database
    4. Initiates background task to copy images to the ODM server
    5. Returns the created job information

    ### Parameters
    - **data (OdmJob)**: The ODM task configuration including folder path, project ID, task ID, and job type

    ### Response:
    Returns `RsdmJobs` with fields:
    - **run_id**: Job unique identifier
    - **odm_project_id**: Related project ID
    - **odm_job_name**: Human-readable job name
    - **odm_src_folder**: Data storage path
    - ...other fields...

    ### Example
    ```json
    {
      "id": 1,
      "odm_project_id": 1,
      "odm_job_name": "test",
      "odm_src_folder": "images/",
      "odm_samplinge_time": "2025-08-17T09:04:40.311000",
      "odm_image_count": 56,
      "progress": 0,
      "celery_task_id": "66ac0298-e3c0-4744-ae5c-62567f292ae0",
      "update_at": "2025-08-17T17:00:41.479989",
      "odm_task_id": "8671d618-6cd7-40ce-aba4-45e85eab19fb",
      "run_id": "daccd58c51e94960",
      "odm_job_type": "multispectral",
      "odm_dest_folder": "E:\\python\\dcmg\\tmp\\1\\8671d618-6cd7-40ce-aba4-45e85eab19fb",
      "odm_host": "http://192.168.3.194:7000/",
      "odm_create_at": "2025-08-17T09:04:40.311000",
      "state": "PENDING",
      "err_msg": null
    }
    ```

    ### Raises
    HTTPException
    - **400**: Task already exists, not allowed to duplicate one, please check odm server.
    - **404**: No images found in the specified folder
    - **500**: Internal server error during job creation
    """
    job: RsdmJobs = db.query(RsdmJobs).filter(RsdmJobs.odm_project_id == data.odm_project_id,
                                              RsdmJobs.odm_task_id == data.odm_task_id).first()
    if job:
        raise HTTPException(status_code=400, detail="Task already exists,"
                                                    " not allowed to duplicate one,"
                                                    " please check odm server.")

    # find images in the folder
    odm_src_folder = get_src_folder(data.odm_src_folder)
    images = find_images(src_folder=odm_src_folder, fot=data.odm_job_type)
    # if no images, return error message and remove the job from the database
    if len(images) == 0:
        logging.warning('No images found in %s', data.odm_src_folder)
        remove_odm_task(data.odm_project_id, data.odm_task_id, data.odm_host)
        # raise HTTPException
        raise HTTPException(status_code=404,
                            detail="No images found in the specified folder. and the job has been removed from the odm.")
    else:
        # create a new RSDM job in the database
        run_id = generate_run_id()
        odm_dest_folder = get_dest_folder(project_id=data.odm_project_id, task_id=data.odm_task_id)
        logging.info("Creating ODM task with run_id %s, %d images, from %s to %s",
                     run_id, len(images), data.odm_src_folder, odm_dest_folder)

        # initiate background task to copy images to the ODM server
        task = copy_image_to_odm.delay(
            project_id=data.odm_project_id,
            task_id=data.odm_task_id,
            base_url=data.odm_host,
            odm_dest_folder=odm_dest_folder,
            images=images
        )
        logging.info("Created Celery task %s, status %s", task.id, task.status)

        # Create a new RSDM job record
        new_rsdm_job = RsdmJobs(
            **data.to_dict(),
            run_id=run_id,
            odm_image_count=len(images),
            celery_task_id=task.id,
            state=OdmJobStatus.pending.value,
            odm_dest_folder=odm_dest_folder
        )
        new_rsdm_job.odm_src_folder = odm_src_folder

        # Add the new job to the database
        db.add(new_rsdm_job)
        db.commit()
        db.refresh(new_rsdm_job)

        return new_rsdm_job


@router.get('/get_odm_state')
async def get_odm_state(project_id: int, task_id: str, db: Session = Depends(get_database)) -> OdmState:
    """
    ## Get the current state of an ODM task.

    This endpoint retrieves the current state of an ODM task.

    ### Parameters
    - **project_id**: The project ID of the ODM task
    - **task_id**: The task ID of the ODM task

    ### Response:
    Returns `OdmState` with fields:
    - **state**: The current state of the ODM task
    - **progress**: The progress of the ODM task (0-100)
    - **host**: The base URL of the ODM server
    - **error**: The error message if the job failed, otherwise null

    ### Example
    ```json
    {
      "state": "COMPLETED",
      "progress": 100,
      "host": "http://127.0.0.1:8000",
      "error": null
    }
    ```
    """
    job: RsdmJobs = db.query(RsdmJobs).filter(RsdmJobs.odm_project_id == project_id,
                                              RsdmJobs.odm_task_id == task_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Odm task not found")

    # Get the current state of the Celery task
    celery_state = get_current_state(job.celery_task_id)
    if not celery_state:
        celery_state = OdmState(state=job.state, progress=job.progress, host=job.odm_host, error=job.err_msg)

    return celery_state


@router.get('/cancel_odm_job')
async def cancel_odm_job(project_id: int, task_id: str, db: Session = Depends(get_database)):
    """
    ## Cancel an ODM task that is pending, running, or waiting.

    This endpoint attempts to cancel an ODM task by:
    1. Finding the job in the database using project_id and task_id
    2. Checking if the job is in a state that can be cancelled (pending, running, or waiting)
    3. Initiating the cancellation of the Celery task
    4. ODM server will remove the task
    5. Updating the job status in the database to canceled

    ### Parameters
    - **project_id**: The project ID of the ODM task
    - **task_id**: The task ID of the ODM task

    ### Response:
    Returns `RsdmJobs` with fields:
    - **run_id**: Job unique identifier
    - **odm_project_id**: Related project ID
    - **odm_job_name**: Human-readable job name
    - **odm_src_folder**: Data storage path
    - ...other fields...

    ### Raises:
    - **HTTPException:** Raises 404 if the job is not found
    - **HTTPException:** Raises 400 if the job is not in a state that can be cancelled (pending, running, or waiting)
    - **HTTPException:** Raises 500 if the ODM server is not available or the job could not be cancelled
    """
    # Query the database for the specified job
    job: RsdmJobs = db.query(RsdmJobs).filter(RsdmJobs.odm_project_id == project_id,
                                              RsdmJobs.odm_task_id == task_id).first()

    # Raise 404 error if job is not found
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Check if job is in a state that cannot be cancelled
    if job.state in [OdmJobStatus.completed.value, OdmJobStatus.failed.value, OdmJobStatus.canceled.value]:
        warning_message = f"ODM task cannot be cancelled because it is in {job.state} state."
        logging.warning(warning_message)
        raise HTTPException(status_code=400, detail=warning_message)

    if not abort_task(celery_task_id=job.celery_task_id, project_id=project_id, task_id=task_id, base_url=job.odm_host):
        raise HTTPException(status_code=500, detail="Failed to cancel ODM task, because odm server is not available.")

    # Update job status to canceled in database
    job.status = OdmJobStatus.canceled.value
    job.update_at = datetime.now()

    db.commit()
    db.refresh(job)

    return job


@router.get('/remove_odm_job', status_code=status.HTTP_204_NO_CONTENT)
async def remove_odm_job(project_id: int, task_id: str, db: Session = Depends(get_database)) -> None:
    """
    ### Remove an ODM task that is completed, failed, or canceled.

    This endpoint attempts to remove an ODM task by:
    1. Finding the job in the database using project_id and task_id
    2. Checking if the job is in a state that can be removed (completed, failed, or canceled)
    3. Deleting the job from the database

    ### Returns:
    - **None:** Returns 204 No Content if the job was successfully removed from the database

    ### Raises:
    - **HTTPException:** Raises 404 if the job is not found
    - **HTTPException:** Raises 400 if the job is not in a state that can be removed (completed, failed, or canceled)

    """
    # Query the database for the specified job
    job: RsdmJobs = db.query(RsdmJobs).filter(RsdmJobs.odm_project_id == project_id,
                                              RsdmJobs.odm_task_id == task_id).first()

    # Raise 404 error if job is not found
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.state not in [OdmJobStatus.completed.value, OdmJobStatus.failed.value, OdmJobStatus.canceled.value]:
        raise HTTPException(status_code=400,
                            detail="ODM task must be completed, failed, or canceled before it can be removed.")

    db.delete(job)
    db.commit()
