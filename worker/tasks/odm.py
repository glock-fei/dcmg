import logging
import shutil
import time
from datetime import datetime
from pathlib import Path
from fractions import Fraction
from typing import Union

from celery.contrib.abortable import AbortableTask, AbortableAsyncResult
from fastapi import HTTPException

from utils import remove_odm_task, commit_odm_task, OdmJobStatus, OdmState
from worker.app import app
from models import with_db_session, RsdmJobs

logger = logging.getLogger(__name__)


def _update_odm_state(celery_task_id: str, odm_state: OdmState):
    """
    Update task progress in database with caching to reduce frequent writes.

    Args:
        celery_task_id: The Celery task ID
        odm_state (OdmState): The current state of the ODM job
    """
    with with_db_session() as db:
        job: RsdmJobs = db.query(RsdmJobs).filter(RsdmJobs.celery_task_id == celery_task_id).first()
        if job:
            job.state = odm_state.state
            job.odm_host = odm_state.host
            job.progress = odm_state.progress
            job.err_msg = odm_state.error
            job.update_at = datetime.now()

            db.commit()
            db.refresh(job)
            logger.info("Updated ODM task state in database: %s", odm_state.dict())


@app.task(bind=True, base=AbortableTask)
def copy_image_to_odm(
        self,
        project_id: int,
        task_id: str,
        base_url: str,
        odm_dest_folder: Union[Path, str],
        images: []
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
        odm_dest_folder (Union[Path, str]): The destination folder on the ODM server
        images (list): List of image file paths to copy

    Process:
        1. Resolves the target directory path
        2. Creates directory structure if needed
        3. Copies each image while tracking progress
        4. Updates progress cache after each file
        5. Automatically commits job when complete
    """
    total_files = len(images)
    copied_step = 0
    odm_state = OdmState(state=OdmJobStatus.running.value, host=base_url)

    try:
        # copy each image to target directory
        for img in images:
            if self.is_aborted():
                msg = "Task aborted by user."
                logger.info(msg)
                # set celery task state to canceled and update progress cache
                odm_state.state = OdmJobStatus.canceled.value
                odm_state.error = msg
                break

            # copy image to target directory
            shutil.copy(img, odm_dest_folder)
            copied_step += 1
            odm_state.progress = round(100.00 * Fraction(copied_step, total_files), 2)

            self.update_state(meta=odm_state.dict())
            if int(odm_state.progress) % 10 == 0:
                logger.info("Copying image %d/%d (%.2f%%)", copied_step, total_files, odm_state.progress)

            time.sleep(0.3)
    except Exception as e:
        logger.error("Error copying image: %s", e)
        odm_state.state = OdmJobStatus.failed.value
        odm_state.error = str(e)
    finally:
        if copied_step == total_files:
            logger.info("All images copied successfully.")
            # set celery task state to completed and update progress cache
            odm_state.state = OdmJobStatus.completed.value
            odm_state.progress = 100.00
            # callback to ODM to commit job
            commit_odm_task(project_id, task_id, base_url)

        # update odm state in database and return result to celery task
        _update_odm_state(self.request.id, odm_state)
        return odm_state.dict()


def abort_task(celery_task_id: str, project_id: int, task_id: str, base_url: str) -> bool:
    """
    Abort a running ODM task.

    Args:
        celery_task_id: The Celery task ID
        project_id: The ID of the project the task belongs to
        task_id: The task identifier for the ODM job
        base_url: The base URL of the odm host

    Returns:
        True if the task was successfully aborted, False otherwise.
    """
    task = AbortableAsyncResult(celery_task_id)

    if task.name and task.worker:
        logger.info("Task %s aborted by user.", celery_task_id)
        task.abort()
        if not task.is_aborted():
            raise HTTPException(status_code=500, detail="Failed to abort task on Celery worker.")
    else:
        logger.warning("Celery task %s not found, skipping abort.", celery_task_id)

    return remove_odm_task(project_id, task_id, base_url)


def get_current_state(celery_task_id: str) -> Union[OdmState, None]:
    """
    Get the current state of an ODM job.
    Args:
        celery_task_id: The Celery task ID
    Returns:
        the current state of the ODM job as an OdmState object, or None if the task is not found.
    """
    current_state = None
    task = AbortableAsyncResult(celery_task_id)

    if task.name and task.worker:
        current_state = OdmState(**task.result) if task.result else OdmState(state=task.state)

    return current_state
