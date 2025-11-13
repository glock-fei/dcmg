import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, status, Header
from fastapi.exceptions import HTTPException
from sqlalchemy import desc
from sqlalchemy.orm import Session, selectinload

from models.session import get_database
import models
from utils.cedoke import generate_run_id
import utils
from worker.tasks import (
    copy_image_to_odm, abort_task, get_current_state,
    generate_odm_report, upload_odm_report_to_cloud,
    get_report_current_state
)
from utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)
router = APIRouter(prefix='/odm')


@router.post("/surface_reflectance")
async def get_surface_reflectance(data: utils.Radiometric):
    """
    ## Get Surface Reflectance

    The endpoint returns the surface reflectance of a given image and panel coordinates.
    """
    picture = utils.get_src_folder(data.picture)

    return utils.get_surface_reflectance(picture, data.coords, data.panel_reflectance)


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
    Returns `List[OdmJobs]` with fields:
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
    query = db.query(models.OdmJobs)
    data: list[models.OdmJobs] = query.options(
        selectinload(models.OdmJobs.generates)
    ).order_by(desc(models.OdmJobs.id)).offset((page - 1) * limit).limit(limit).all()

    result = []
    for job in data:
        # check if odm task exists
        odm_state = get_current_state(job.celery_task_id)
        if odm_state:
            job.state = odm_state.state
            job.progress = odm_state.progress
            job.error = odm_state.error

        # check if generates task exists
        if job.generates:
            gestate = get_current_state(job.generates.celery_task_id)
            if gestate:
                job.generates.state = gestate.state
                job.generates.progress = gestate.progress
                job.generates.error = gestate.error

        if not only_running:
            result.append(job)
        elif job.state == utils.OdmJobStatus.running.value:
            result.append(job)
    return result


@router.post('/create_odm_job')
async def create_odm_job(data: utils.OdmJob, db: Session = Depends(get_database)):
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
    Returns `OdmJobs` with fields:
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
    query = db.query(models.OdmJobs)
    query = query.filter(models.OdmJobs.odm_project_id == data.odm_project_id)
    query = query.filter(models.OdmJobs.odm_task_id == data.odm_task_id)
    job: models.OdmJobs = query.first()

    if job:
        raise HTTPException(
            status_code=400,
            detail=_("Task already exists, not allowed to duplicate one, please check odm server.")
        )

    # find images in the folder
    odm_src_folder = utils.get_src_folder(data.odm_src_folder)
    logger.info("Scanning %s for images", odm_src_folder)

    images = utils.find_images(src_folder=odm_src_folder, fot=data.odm_job_type)
    # if no images, return error message and remove the job from the database
    if len(images) == 0:
        logging.warning('No images found in %s', odm_src_folder)
        utils.remove_odm_task(data.odm_project_id, data.odm_task_id, data.odm_host)
        # raise HTTPException
        raise HTTPException(
            status_code=404,
            detail=_("No images found in the specified folder. and the job has been removed from the odm.")
        )
    else:
        # create a new RSDM job in the database
        run_id = generate_run_id()
        odm_dest_folder = utils.get_dest_folder(project_id=data.odm_project_id, task_id=data.odm_task_id)
        logging.info("Creating ODM task with run_id %s, %d images, from %s to %s",
                     run_id, len(images), data.odm_src_folder, odm_dest_folder)

        # initiate background task to copy images to the ODM server
        output_dir, log_file, reflector_dest_dir = utils.prepare_odm_output_structure(
            project_id=data.odm_project_id,
            task_id=data.odm_task_id,
            skip_creation=False
        )
        radiometric = [[rad.dict() for rad in row] for row in data.radiometric] if data.radiometric else None
        logging.info("Radiometric data: %s", radiometric)

        # Create a new Celery task to copy images to the ODM server
        task = copy_image_to_odm.delay(
            project_id=data.odm_project_id,
            task_id=data.odm_task_id,
            base_url=data.odm_host,
            odm_dest_folder=odm_dest_folder,
            images=images,
            reflector_dest_dir=str(reflector_dest_dir),
            radiometric=radiometric
        )
        logging.info("Created Celery task %s, status %s", task.id, task.status)

        # Create a new RSDM job record
        new_rsdm_job = models.OdmJobs(
            **data.to_dict(),
            run_id=run_id,
            odm_image_count=len(images),
            celery_task_id=task.id,
            state=utils.OdmJobStatus.pending.value,
            odm_dest_folder=odm_dest_folder
        )
        new_rsdm_job.odm_src_folder = odm_src_folder

        # Add the new job to the database
        db.add(new_rsdm_job)
        db.commit()
        db.refresh(new_rsdm_job)

        return new_rsdm_job


@router.get('/get_odm_state')
async def get_odm_state(project_id: int, task_id: str, db: Session = Depends(get_database)) -> utils.OdmState:
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
    job: models.OdmJobs = db.query(models.OdmJobs).filter(
        models.OdmJobs.odm_project_id == project_id,
        models.OdmJobs.odm_task_id == task_id
    ).first()

    if not job:
        raise HTTPException(status_code=404, detail=_("Odm task not found"))

    # Get the current state of the Celery task
    celery_state = get_current_state(job.celery_task_id)
    if not celery_state:
        celery_state = utils.OdmState(state=job.state, progress=job.progress, host=job.odm_host, error=job.err_msg)

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
    Returns `OdmJobs` with fields:
    - **run_id**: Job unique identifier
    - **odm_project_id**: Related project ID
    - **odm_job_name**: Human-readable job name
    - **odm_src_folder**: Data storage path
    - ...other fields...

    ### Raises:
    - **HTTPException:** Raises 404 if the job is not found
    - **HTTPException:** Raises 400 if the job is not in a state that can be cancelled (pending, running, or waiting)
    - **HTTPException:** Raises 500 if th job is not removed from the celery task or the odm server.
    """
    # Query the database for the specified job
    job: models.OdmJobs = db.query(models.OdmJobs).filter(
        models.OdmJobs.odm_project_id == project_id,
        models.OdmJobs.odm_task_id == task_id
    ).first()

    # Raise 404 error if job is not found
    if not job:
        raise HTTPException(status_code=404, detail=_("Job not found"))

    # Check if job is in a state that cannot be cancelled
    if job.state in [utils.OdmJobStatus.completed.value, utils.OdmJobStatus.failed.value,
                     utils.OdmJobStatus.canceled.value]:
        warning_message = f"ODM task cannot be cancelled because it is in {job.state} state."
        logging.warning(warning_message)
        raise HTTPException(status_code=400, detail=_(warning_message))

    if not abort_task(celery_task_id=job.celery_task_id):
        raise HTTPException(status_code=500, detail=_("Failed to cancel celery task."))

    if not utils.remove_odm_task(project_id=project_id, task_id=task_id, base_url=job.odm_host):
        raise HTTPException(status_code=500, detail=_("Failed to remove ODM task from odm server."))

    # Update job status to canceled in database
    job.state = utils.OdmJobStatus.canceled.value
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
    job: models.OdmJobs = db.query(models.OdmJobs).filter(
        models.OdmJobs.odm_project_id == project_id,
        models.OdmJobs.odm_task_id == task_id
    ).first()

    # Raise 404 error if job is not found
    if not job:
        raise HTTPException(status_code=404, detail=_("Job not found"))

    if job.state not in [
        utils.OdmJobStatus.completed.value,
        utils.OdmJobStatus.failed.value,
        utils.OdmJobStatus.canceled.value
    ]:
        raise HTTPException(
            status_code=400,
            detail=_("ODM task must be completed, failed, or canceled before it can be removed.")
        )

    db.delete(job)
    db.commit()


@router.post('/generate_report', status_code=status.HTTP_204_NO_CONTENT)
async def generate_report(
        data: utils.OdmGenRep,
        db: Session = Depends(get_database)
) -> None:
    """
    ### Save the ODM report for a completed ODM task.

    This endpoint saves the ODM report for a completed ODM task by:
    1. Finding the job in the database using project_id and task_id
    2. Checking if the job is in a state that can have a report (completed)
    3. Saving the report to the database
    4. Updating the job status in the database to completed

    ### Parameters
    - **project_id**: The project ID of the ODM task
    - **task_id**: The task ID of the ODM task
    - **orthophoto_tif**: The path to the orthophoto tif file

    ### Returns:
    - **None:** Returns 204 No Content if the report was successfully saved to the database

    ### Raises:
    - **HTTPException:** Raises 400 if the orthophoto tif file is not provided
    - **HTTPException:** Raises 404 if the orthophoto tif file is not found
    - **HTTPException:** Raises 404 if the job is not found
    """
    # Check if the orthophoto tif file exists
    if not data.orthophoto_tif:
        raise HTTPException(status_code=400, detail=_("Orthophoto tif file not provided"))

    # Get the destination folder for the ODM task
    odm_dest_folder = utils.get_dest_folder(data.project_id, data.task_id)
    orthophoto_tif = Path(odm_dest_folder) / "assets" / data.orthophoto_tif
    if not orthophoto_tif.exists():
        logger.error("Orthophoto tif file not found: %s", orthophoto_tif)
        raise HTTPException(status_code=404, detail=_("Orthophoto tif file not found"))

    # Query the database for the specified job
    job: models.OdmJobs = db.query(models.OdmJobs).filter(
        models.OdmJobs.odm_project_id == data.project_id,
        models.OdmJobs.odm_task_id == data.task_id
    ).first()

    # Raise 404 error if job is not found
    if not job:
        raise HTTPException(status_code=404, detail=_("Job not found"))

    output_dir, log_file, reflector_dest_dir = utils.prepare_odm_output_structure(
        project_id=data.project_id,
        task_id=data.task_id
    )

    # start the report generation task
    celery_task = generate_odm_report.delay(
        project_id=data.project_id,
        task_id=data.task_id,
        odm_job_name=data.odm_job_name if data.odm_job_name else job.odm_job_name,
        output_dir=str(output_dir),
        reflector_dest_dir=str(reflector_dest_dir),
        orthophoto_tif=str(orthophoto_tif),
        log_file=str(log_file)
    )

    # Query the database for the specified task
    existing_task: models.OdmGeTask = db.query(models.OdmGeTask).filter(models.OdmGeTask.job_id == job.id).first()

    if existing_task:
        existing_task.celery_task_id = celery_task.id
        existing_task.orthophoto_tif = str(orthophoto_tif)
        existing_task.state = utils.OdmJobStatus.pending.value
        existing_task.progress = 0
        existing_task.update_at = datetime.now()
        existing_task.err_msg = None

        db.commit()
        db.refresh(existing_task)

        return existing_task

    # Create a new record in the database
    task = models.OdmGeTask(
        job_id=job.id,
        orthophoto_tif=str(orthophoto_tif),
        celery_task_id=celery_task.id
    )

    db.add(task)
    db.commit()
    db.refresh(task)

    return task


@router.put('/save_report/{project_id}/{task_id}', status_code=status.HTTP_204_NO_CONTENT)
async def save_report(
        project_id: int,
        task_id: str,
        db: Session = Depends(get_database)
):
    """
    ## Save the ODM report for a completed ODM task.

    This endpoint saves the ODM report for a completed ODM task by:
    1. Finding the job in the database using project_id and task_id
    2. Checking if the job is in a state that can have a report (completed)
    3. Saving the report to the database
    4. Updating the job status in the database to completed

    ### Parameters
    - **project_id**: The project ID of the ODM task
    - **task_id**: The task ID of the ODM task

    ### Returns:
    - **OdmReport:** Returns the saved report object

    """
    # Query the database for the specified job
    job: models.OdmJobs = db.query(models.OdmJobs).filter(
        models.OdmJobs.odm_project_id == project_id,
        models.OdmJobs.odm_task_id == task_id
    ).first()

    # Raise 404 error if job is not found
    if not job:
        raise HTTPException(status_code=404, detail=_("Job not found"))

    report_saved_folder, log_file, reflector_dest_dir = utils.prepare_odm_output_structure(project_id, task_id)
    report_json = utils.get_odm_report_json(report_saved_folder)
    # Check if the report exists
    if not report_json.exists():
        raise HTTPException(status_code=404, detail=_("The report has not been generated yet"))

    # Load the report
    with open(report_json, "r") as f:
        report_info = json.load(f)
        report = report_info.get("report", [])
        if len(report) == 0:
            raise HTTPException(
                status_code=404,
                detail=_("An error occurred during report generation, and it cannot be saved")
            )

    # Use upsert method to either update existing report or create new one
    models.OdmReport.upsert(
        db,
        job_id=job.id,
        output_dir=str(report_saved_folder),
        log_file=str(log_file),
        orthophoto_tif=report[0].get("file_name"),
        area_mu=report[0].get("area_mu"),
        band=report[0].get("band") if job.odm_job_type == utils.OdmType.multispectral.value else None,
        state=utils.OdmJobStatus.pending.value,
        progress=0,
        celery_task_id=None,
        err_msg=None
    )
    # Save the vegetation information
    for veg in report[0].get("vegetation", []):
        models.OdmVegetation.upsert(
            db,
            job_id=job.id,
            algo_name=veg.get("name"),
            min_value=veg.get("min"),
            max_value=veg.get("max"),
            mean=veg.get("mean"),
            stddev=veg.get("stddev"),
            output_files=veg.get("output"),
            class_count=veg.get("report")
        )

    db.commit()


@router.get('/get_report_detail')
def get_report_detail(
        project_id: int,
        task_id: str,
        db: Session = Depends(get_database)
):
    """
    ## Get the ODM report by project ID and task ID.

    This endpoint retrieves the ODM report by project ID and task ID with the following process:
    1. Querying the database for the report using project_id and task_id
    2. Loading the associated job and vegetation data
    3. Updating the report status with the current Celery task state if available

    ### Parameters
    - **project_id**: The project ID of the ODM task
    - **task_id**: The task ID of the ODM task

    ### Response:
        Returns `OdmReport`
    """
    query = db.query(models.OdmReport).options(selectinload(models.OdmReport.job).selectinload(models.OdmJobs.vegetation))
    query = query.join(models.OdmJobs, models.OdmJobs.id == models.OdmReport.job_id)
    query = query.filter(models.OdmJobs.odm_project_id == project_id)
    query = query.filter(models.OdmJobs.odm_task_id == task_id)
    orp: models.OdmReport = query.first()

    if not orp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_("Report not found, please try again later."))

    # Get the current state of the Celery task
    if orp.celery_task_id:
        report_state = get_report_current_state(orp.celery_task_id)
        if report_state:
            orp.state = report_state.state
            orp.progress = report_state.progress

    orp.output_dir = str(Path(orp.output_dir).relative_to(Path(os.getcwd())))
    orp.resource_files = utils.get_odm_resource_files(orp.output_dir) if orp.job.odm_job_type == utils.OdmType.multispectral.value else None
    orp.oss_url = os.getenv("OSS_DOMAIN").rstrip("/") + "/" + utils.format_oss_upload_prefix(project_id, task_id)

    return orp


@router.get('/get_reports')
def get_reports(
        page: int = 1,
        limit: int = 1000,
        only_running: bool = True,
        db: Session = Depends(get_database)
):
    """
    ## Get the list of ODM reports.

    This endpoint retrieves the list of ODM reports.

    ### Parameters
    - **page**: The page number to retrieve (default: 1)
    - **limit**: The number of records to retrieve (default: 1000)
    - **only_running**: Whether to only retrieve reports that are still running (default: True)

    ### Returns:
    - **List[OdmReport]:** Returns a list of OdmReport objects

    """
    query = db.query(models.OdmReport).options(selectinload(models.OdmReport.job))
    data: list[models.OdmReport] = query.order_by(desc(models.OdmReport.id)).offset((page - 1) * limit).limit(
        limit).all()

    result = []
    for rep in data:
        # Get the current state of the Celery task
        if rep.celery_task_id:
            report_state = get_report_current_state(rep.celery_task_id)
            if report_state:
                rep.state = report_state.state
                rep.progress = report_state.progress

        if not only_running:
            result.append(rep)
        elif rep.state == utils.OdmJobStatus.running.value:
            result.append(rep)

    return result


@router.post('/upload_report')
def upload_report(
        data: utils.UploadRepTask,
        token: Annotated[str, Header()],
        cid: Annotated[str, Header()],
        db: Session = Depends(get_database)
):
    """
    ## Upload the ODM report to OSS and commit to online system.

    This endpoint uploads the ODM report to OSS and commits the report information to the online system:
    1. Finding the report in the database using project_id, task_id
    2. Preparing environment variables for OSS upload
    3. Starting background task to upload report files to OSS
    4. Committing report metadata to the online system
    5. Updating the report status in the database to running

    ### Parameters
    - **data** (UploadRepTask): The upload report task data including project_id, task_id, report_no
    - **token** (str, Header): Authentication token for ODM server
    - **cid** (str, Header): Client ID for ODM server
    - **db** (Session): Database session dependency

    ### Returns:
    - **OdmReport:** Returns the updated report object with new celery task ID and running status

    ### Raises:
    - **HTTPException:** Raises 404 if the report is not found
    - **HTTPException:** Raises 400 if the report is already uploaded

    ### Process:
    1. Query database for the specified ODM report
    2. If report not found, raise 404 error
    3. Prepare environment variables for OSS upload and online commit
    4. Start Celery task to:
       A. Upload report files to OSS with progress tracking
       B. Commit report metadata to the online system
    5. Update report record with new Celery task ID and running status
    6. Commit changes to database
    7. Return updated report object

    ### Background Task Steps:
    The background task performs these operations:
    1. Download full report ZIP from ODM server
    2. Upload individual output files to OSS
    3. Upload full report ZIP to OSS
    4. Commit report information to online system with metadata
    5. Update database with final status
    """
    logger.info("Start uploading report for project_id: %s, task_id: %s, cid: %s, token: %s",
                data.project_id, data.task_id, cid, token)
    # Query the database for the specified report using project_id, task_id
    query = db.query(models.OdmReport).join(models.OdmJobs, models.OdmReport.job_id == models.OdmJobs.id)
    query = query.filter(models.OdmJobs.odm_project_id == data.project_id)
    query = query.filter(models.OdmJobs.odm_task_id == data.task_id)
    orp: models.OdmReport = query.first()

    # Raise 404 error if report is not found
    if not orp:
        raise HTTPException(status_code=404, detail=_("Report not found, please try again later."))

    if orp.state == utils.OdmJobStatus.completed.value:
        raise HTTPException(status_code=400, detail=_("Report is already uploaded."))

    odm_resource_files = utils.get_odm_resource_files(orp.output_dir)
    odm_radiometric = orp.job.radiometric

    # Start the Celery task to upload ODM report to OSS and commit to online system
    celery_task = upload_odm_report_to_cloud.delay(
        project_id=data.project_id,
        task_id=data.task_id,
        odm_host=orp.job.odm_host,
        odm_resource_files=odm_resource_files,
        odm_radiometric=odm_radiometric,
        output_dir=orp.output_dir,
        report_no=data.report_no,
        token=token,
        cid=cid
    )

    # Log the creation of the Celery task
    logging.info("Create Celery task[update_odm_report] %s, status %s", celery_task.id, celery_task.status)

    # Update the report record with the new Celery task ID and running status
    orp.celery_task_id = celery_task.id
    orp.state = utils.OdmJobStatus.running.value
    orp.update_at = datetime.now()
    orp.err_msg = None
    orp.progress = 0

    # Commit changes to database and refresh the report object
    db.commit()
    db.refresh(orp)

    return orp


@router.get('/cancel_upload_task', status_code=status.HTTP_204_NO_CONTENT)
async def cancel_upload_task(project_id: int, task_id: str, db: Session = Depends(get_database)):
    """
    ## Cancel the ODM report upload task.
    This endpoint cancels the ODM report upload task by:
    1. Finding the report in the database using project_id, task_id,
    2. Checking if the report is running
    3. Aborting the Celery task associated with the report
    4. Updating the report status in the database to cancelled

    ### Parameters
    - **project_id**: The project ID of the ODM task
    - **task_id**: The task ID of the ODM task
    - **db** (Session): Database session dependency

    ### Returns:
    - **None:** Returns 204 No Content if the report upload task was successfully cancelled

    ### Raises:
    - **HTTPException:** Raises 404 if the report is not found
    - **HTTPException:** Raises 400 if the report is not running. Cannot cancel.
    """
    # Query the database for the specified report using project_id, task_id
    query = db.query(models.OdmReport).join(models.OdmJobs, models.OdmReport.job_id == models.OdmJobs.id)
    query = query.filter(models.OdmJobs.odm_project_id == project_id)
    query = query.filter(models.OdmJobs.odm_task_id == task_id)
    orp: models.OdmReport = query.first()

    # Raise 404 error if report is not found
    if not orp or not orp.celery_task_id:
        raise HTTPException(status_code=404, detail=_("Report not found, please try again later."))

    # Check if the report is running
    current_state = orp.state
    report_state = get_report_current_state(orp.celery_task_id)
    if report_state:
        current_state = report_state.state

    # Raise 400 error if report is not running. Cannot cancel.
    if current_state != utils.OdmJobStatus.running.value:
        raise HTTPException(status_code=400, detail=_("Report is not running. Cannot cancel."))

    abort_task(celery_task_id=orp.celery_task_id)
