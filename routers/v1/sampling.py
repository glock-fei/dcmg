import logging
from typing import Optional

from fastapi import APIRouter, Depends, status
from fastapi.exceptions import HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, selectinload

from models.session import get_database
import models
import utils
import worker.tasks as tasks

from utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)
router = APIRouter(prefix='/odm')


def _get_sampling_record(db: Session, sampling_id: int) -> models.OdmSamplingRecord:
    """
    Helper function to get a sampling record by its ID or raise 404
    """
    sampling_record = db.query(models.OdmSamplingRecord).filter(
        models.OdmSamplingRecord.id == sampling_id,
        models.OdmSamplingRecord.is_deleted == 0
    ).first()

    if not sampling_record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_("Sampling record not found"))

    return sampling_record


def _get_odm_report_and_job(db: Session, project_id: int, task_id: str) -> models.OdmReport:
    """
    Helper function to get an ODM job by project_id and task_id or raise 404
    """
    query = db.query(models.OdmReport)
    query = query.join(models.OdmJobs, models.OdmJobs.id == models.OdmReport.job_id)
    query = query.filter(models.OdmJobs.odm_project_id == project_id)
    query = query.filter(models.OdmJobs.odm_task_id == task_id)
    report: models.OdmReport = query.first()

    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_("ODM task not found"))

    if report.job.odm_job_type != utils.OdmType.multispectral.value:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=_("ODM task is not multispectral, cannot get sampling statistics"))

    return report


@router.get('/samplings')
async def list_sampling_records(project_id: int, task_id: str, db: Session = Depends(get_database)):
    """
    ## List sampling records for an ODM task

    Retrieve all sampling records associated with a given project_id and task_id.
    Results are ordered by creation time in descending order.

    ### Parameters
    - **project_id** (integer): The project ID of the ODM task
    - **task_id** (string): The task ID of the ODM task

    ### Returns
    - **List[OdmSamplingRecord]**: A list of sampling records

    ### Raises
    - **HTTPException**: 404 Not Found if no ODM task is found for the given project_id and task_id
    """
    report_job = _get_odm_report_and_job(db, project_id, task_id)
    # Query for all sampling records associated with this job
    sampling_query = db.query(models.OdmSamplingRecord)
    sampling_query = sampling_query.filter(
        models.OdmSamplingRecord.job_id == report_job.job_id,
        models.OdmSamplingRecord.is_deleted == 0
    )
    sampling_query = sampling_query.order_by(models.OdmSamplingRecord.create_at.desc())
    sampling_records = sampling_query.all()

    return sampling_records


@router.get('/samplings/{sampling_id}')
async def get_sampling_record_with_statistics(sampling_id: int, latest_only: bool = True,
                                              db: Session = Depends(get_database)):
    """
    ## Get detailed information for a specific sampling record including statistics

    Retrieve a specific sampling record by its ID, including all associated quadrats and their statistics.
    
    If `latest_only` is set to `True` (default), only the latest run's quadrats and their statistics will be returned.

    ### Parameters
    - **sampling_id** (integer): The ID of the sampling record
    - **latest_only** (boolean, optional): Whether to return only the latest run's data. Defaults to True.

    ### Returns
    - **OdmSamplingRecord**: The sampling record with its associated quadrats and statistics

    ### Raises
    - **HTTPException**: 404 Not Found if the sampling record doesn't exist
    """
    sampling_record = _get_sampling_record(db, sampling_id)

    # retrieve the latest sampling task status from the Redis Celery task.
    odm_state = tasks.get_current_state(sampling_record.celery_task_id)
    if odm_state:
        sampling_record.state = odm_state.state
        sampling_record.progress = odm_state.progress
        sampling_record.error = odm_state.error

    quadrat_query = db.query(models.OdmQuadrat).filter(models.OdmQuadrat.sampling_id == sampling_id)
    quadrat_query = quadrat_query.options(selectinload(models.OdmQuadrat.statistics))

    if latest_only:
        quadrat_query = quadrat_query.filter(models.OdmQuadrat.run_id == sampling_record.latest_run_id)

    quadrat_records = quadrat_query.all()
    sampling_record.quadrats = quadrat_records

    return sampling_record


@router.post('/samplings', status_code=status.HTTP_201_CREATED)
async def create_sampling_record(data: utils.Sampling, db: Session = Depends(get_database)):
    """
    ## Create a new sampling record

    Create a new sampling record for a given ODM task. This endpoint does not include quadrats creation.

    ### Parameters
    - **data** (Sampling): The sampling data including project_id, task_id, and optional title

    ### Returns
    - **OdmSamplingRecord**: The created sampling record

    ### Raises
    - **HTTPException**: 404 Not Found if the ODM task is not found
    - **HTTPException**: 400 Bad Request if the ODM task is not multispectral
    """
    report_job = _get_odm_report_and_job(db, data.project_id, data.task_id)
    sampling_record = models.OdmSamplingRecord.create_record(db, report_job.job_id, report_job.id, data.title)

    return sampling_record


@router.post('/samplings/retrieve_or_create')
async def retrieve_or_create_sampling_records(data: utils.Sampling, db: Session = Depends(get_database)):
    """
    ## Get or create sampling record for an ODM task

    For a given project_id and task_id combination, there can be multiple sampling records
    created at different times. However, when retrieving sampling records, we typically 
    want the most recently created one. If no record exists, we create a new one.

    This endpoint checks if any sampling records exist for the given project_id and task_id.
    If they exist, it returns the most recently created record.
    If none exist, it creates a new sampling record and returns it.

    ### Parameters
    - **data** (Sampling): The sampling data including project_id, task_id, and optional title

    ### Returns
    - **OdmSamplingRecord**: The most recent sampling record for the specified project_id and task_id

    ### Raises
    - **HTTPException**: 404 Not Found if the ODM task is not found
    - **HTTPException**: 400 Bad Request if the ODM task is not multispectral
    """
    report_job = _get_odm_report_and_job(db, data.project_id, data.task_id)
    # Query for sampling record associated with this job, ordered by creation time
    sampling_record = db.query(models.OdmSamplingRecord).filter(
        models.OdmSamplingRecord.job_id == report_job.job_id,
        models.OdmSamplingRecord.is_deleted == 0
    ).order_by(models.OdmSamplingRecord.create_at.desc()).first()

    if sampling_record:
        return sampling_record

    # If no sampling record exists, create a new one using the model method
    return models.OdmSamplingRecord.create_record(db, report.job_id, report.id, data.title)


@router.post('/samplings/{sampling_id}/statistics', status_code=status.HTTP_202_ACCEPTED)
async def initiate_sampling_statistics(
        sampling_id: int,
        data: list[utils.QuadratBase],
        db: Session = Depends(get_database)
):
    """
    ## Initiate sampling statistics calculation

    Start a background task to calculate statistics for the quadrats in a sampling record.
    Returns immediately with status 202 (Accepted) while the calculation runs in the background.

    ### Parameters
    - **sampling_id** (integer): The ID of the sampling record
    - **data** (list[QuadratBase]): List of quadrat data to analyze

    ### Returns
    - **OdmSamplingRecord**: The sampling record with updated task information

    ### Raises
    - **HTTPException**: 404 Not Found if the sampling record doesn't exist
    """
    sampling_record = _get_sampling_record(db, sampling_id)
    resource_files = utils.get_odm_resource_files(sampling_record.report.output_dir)

    if len(resource_files) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_(
                'The report does not contain the necessary'
                ' band or vegetation index files; therefore, '
                'statistical calculations cannot be performed.'
            )
        )

    latest_run_id = utils.generate_run_id()

    quadrats = []
    # Create quadrats associated with the sampling record
    for quadrat in data:
        quad = models.OdmQuadrat(
            sampling_id=sampling_record.id,
            run_id=latest_run_id,
            name=quadrat.name,
            coords=quadrat.coords,
            center=quadrat.center
        )
        db.add(quad)
        db.flush()

        quadrats.append([quad.id, quadrat.coords])

    # Start the sampling statistics task
    task = tasks.sampling_statistics.delay(
        output_dir=sampling_record.report.output_dir,
        resource_files=resource_files,
        quadrats=quadrats
    )
    sampling_record.celery_task_id = task.id
    sampling_record.latest_run_id = latest_run_id
    sampling_record.cloud_id = None

    db.commit()
    db.refresh(sampling_record)

    return sampling_record


@router.delete('/samplings/{sampling_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_sampling_record(
        sampling_id: int,
        db: Session = Depends(get_database)
):
    """
    ## Delete a sampling record

    Mark a specific sampling record as deleted.

    ### Parameters
    - **sampling_id** (integer): The ID of the sampling record to delete

    ### Returns
    - **None**: 204 No Content on successful deletion

    ### Raises
    - **HTTPException**: 404 Not Found if the sampling record doesn't exist
    """
    sampling_record = _get_sampling_record(db, sampling_id)
    sampling_record.is_deleted = True
    db.commit()


@router.get('/samplings/{sampling_id}/export_to_excel')
async def export_sampling_record_to_excel(sampling_id: int, filename: Optional[str] = None, db: Session = Depends(get_database)):
    """
    Export sampling record statistics to Excel file and return as streaming response

    ### Parameters
    - **sampling_id** (integer): The ID of the sampling record
    - **filename** (string, optional): The filename for the exported file. Defaults to the title of the sampling record.

    ### Returns
    - **StreamingResponse**: The Excel file as a streaming response.

    ### Raises
    - **HTTPException**: 404 Not Found if the sampling record doesn't exist
    - **HTTPException**: 400 Bad Request if the report does not contain the necessary band or vegetation index files.

    """
    sampling_record = _get_sampling_record(db, sampling_id)

    # Get all quadrats associated with the sampling record and their latest statistics
    quadrat_query = db.query(models.OdmQuadrat).options(selectinload(models.OdmQuadrat.statistics))
    quadrat_query = quadrat_query.filter(models.OdmQuadrat.sampling_id == sampling_id)
    quadrat_query = quadrat_query.filter(models.OdmQuadrat.run_id == sampling_record.latest_run_id)
    quadrat_records = quadrat_query.all()

    from utils.sampling_data_exporter import generate_excel
    output = generate_excel(quadrat_records)
    
    filename = utils.clean_filename(filename)
    headers = {
        'Content-Disposition': f'attachment; filename="{filename if filename else sampling_record.title}.xlsx"'
    }
    
    return StreamingResponse(
        output,
        headers=headers,
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
