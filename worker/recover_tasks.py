import logging
from contextlib import asynccontextmanager
from typing import List, Type

from celery.contrib.abortable import AbortableAsyncResult
from fastapi import FastAPI

from models import with_db_session, OdmJobs, OdmReport
from utils import OdmJobStatus, OdmState, OdmUploadState

logger = logging.getLogger(__name__)


@asynccontextmanager
async def recover_job_failed(app: FastAPI):
    """
    Recover tasks that were interrupted by system shutdown.
    This function should be called when the application starts.
    """
    with with_db_session() as db:
        # Recover failed OdmJobs
        _recover_failed_tasks(
            db.query(OdmJobs).filter(
                OdmJobs.state == OdmJobStatus.failed.value,
            ).all(),
            OdmState
        )

        # Recover failed OdmReports
        _recover_failed_tasks(
            db.query(OdmReport).filter(
                OdmReport.state == OdmJobStatus.failed.value,
            ).all(),
            OdmUploadState
        )

    yield
    logger.info("Cleaning up...")


def _recover_failed_tasks(tasks: List, state_model: Type) -> None:
    """
    Recover failed tasks by checking their Celery status and updating accordingly.

    Args:
        tasks: List of database task objects
        state_model: The state model class to use for reconstruction
    """
    for task_obj in tasks:
        try:
            # Check if the Celery task still exists
            task = AbortableAsyncResult(task_obj.celery_task_id)

            # If task exists and has result data, reconstruct and store failure
            if all([task.name, task.worker]) and task.result is not None:
                task_state = state_model(**task.result)
                task_state.state = OdmJobStatus.failed.value
                task_state.error = task_obj.err_msg

                logger.info("Recovered interrupte task %s", task_obj.celery_task_id)

                task.backend.store_result(
                    task.id,
                    task_state.dict(),
                    "FAILURE"
                )
        except Exception as e:
            logger.error("Failed to recover task %s: %s", task_obj.celery_task_id, str(e))
