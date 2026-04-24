"""
Common task utilities for Celery workers.

This module provides shared functionality for all task types including:
- Task cancellation handling
- State management and database updates
- Docker container log streaming
- Task status queries
"""
import logging
import os
import platform
from typing import Optional, Type, Callable, Any
from datetime import datetime

import docker
from docker.types import DeviceRequest
from celery.contrib.abortable import AbortableAsyncResult
from fastapi import HTTPException

import models
import utils

logger = logging.getLogger(__name__)


class TaskCanceledException(Exception):
    """Exception raised when a Celery task is canceled by user"""
    pass


def stream_container_logs(
    container,
    log_file: str,
    is_aborted_callback: Callable[[], bool],
    on_abort: Optional[Callable] = None
) -> None:
    """
    Stream container logs to file and check for abort signals.
    
    Args:
        container: Docker container object
        log_file: Path to log file
        is_aborted_callback: Function that returns True if task is aborted
        on_abort: Optional callback function to call before raising exception
    """
    with open(log_file, "w", encoding="utf-8") as f:
        for line in container.logs(stream=True, follow=True):
            decoded_line = line.decode("utf-8")
            f.write(decoded_line)
            f.flush()
            
            if is_aborted_callback():
                if on_abort:
                    on_abort()
                container.stop(timeout=5)
                raise TaskCanceledException("Task canceled by user.")


def run_docker_container(
    image: str,
    command: list,
    volumes: dict,
    environment: dict,
    detach: bool = True,
    remove: bool = False,
    use_gpu: bool = False,
    extra_hosts: Optional[dict] = None,
    user_id: Optional[int] = None,
    shm_size: Optional[str] = None,
    **kwargs
):
    """
    Run a Docker container with common configuration.
    
    Args:
        image: Docker image name
        command: Command to run in container
        volumes: Volume mappings
        environment: Environment variables
        detach: Run in background
        remove: Auto-remove container after exit
        use_gpu: Enable GPU support
        extra_hosts: Additional host mappings
        user_id: User ID for container (None = auto-detect based on platform)
        shm_size: Shared memory size
        **kwargs: Additional arguments passed to containers.run()
        
    Returns:
        Docker container object
    """
    # Auto-detect user ID based on platform
    if user_id is None:
        if platform.system() not in ["Windows"]:
            user_id = os.getuid()
    
    # Prepare container configuration
    container_kwargs = {
        "image": image,
        "command": command,
        "detach": detach,
        "remove": remove,
        "environment": environment,
        "volumes": volumes,
    }
    
    # Add GPU support if requested
    if use_gpu:
        container_kwargs["device_requests"] = [
            DeviceRequest(count=1, capabilities=[["gpu"]])
        ]
    
    # Add extra hosts if provided
    if extra_hosts:
        container_kwargs["extra_hosts"] = extra_hosts
    
    # Set user ID
    if user_id is not None:
        container_kwargs["user"] = user_id
    
    # Set shared memory size if provided
    if shm_size:
        container_kwargs["shm_size"] = shm_size
    
    # Merge additional kwargs
    container_kwargs.update(kwargs)
    
    client = docker.from_env()
    return client.containers.run(**container_kwargs)


def update_task_state_in_db(
    celery_task_id: str,
    model_class: Type,
    state: str,
    progress: Optional[float] = None,
    error: Optional[str] = None,
    extra_fields: Optional[dict] = None,
    filter_field: str = "celery_task_id"
) -> Optional[Any]:
    """
    Update task state in database.
    
    Args:
        celery_task_id: The Celery task ID
        model_class: SQLAlchemy model class
        state: Task state value
        progress: Optional progress value (0-100)
        error: Optional error message
        extra_fields: Additional fields to update
        filter_field: Field name to filter by (default: celery_task_id)
        
    Returns:
        Updated model instance or None if not found
    """
    with models.db_manager.with_db_session() as db:
        task = db.query(model_class).filter(
            getattr(model_class, filter_field) == celery_task_id
        ).first()
        
        if task:
            task.state = state
            task.update_at = datetime.now()
            
            if progress is not None:
                task.progress = progress
            
            if error is not None:
                task.err_msg = error
            
            if extra_fields:
                for field, value in extra_fields.items():
                    setattr(task, field, value)
            
            db.commit()
            db.refresh(task)
            logger.info("Updated %s state in database: %s", model_class.__name__, state)
            
        return task


def get_task_state_from_celery(
    celery_task_id: str,
    state_class: Type = utils.StateBase
) -> Optional[Any]:
    """
    Get the current state of a Celery task.
    
    Args:
        celery_task_id: The Celery task ID
        state_class: Pydantic model class to parse state
        
    Returns:
        Parsed state object or None if task not found
    """
    try:
        task = AbortableAsyncResult(celery_task_id)
        
        if task.name and task.worker and task.result:
            return state_class(**task.result)
    except ValueError as e:
        logger.error("Failed to get task result: %s", str(e))
    
    return None


def abort_celery_task(celery_task_id: str) -> bool:
    """
    Abort a running Celery task.
    
    Args:
        celery_task_id: The Celery task ID
        
    Returns:
        True if the task was successfully aborted, False otherwise
        
    Raises:
        HTTPException: If abort fails
    """
    task = AbortableAsyncResult(celery_task_id)
    
    if task.name and task.worker:
        logger.info("Task %s aborted by user.", celery_task_id)
        task.abort()
        if not task.is_aborted():
            raise HTTPException(
                status_code=500,
                detail="Failed to abort task on Celery worker."
            )
        return True
    else:
        logger.warning("Celery task %s not found, skipping abort.", celery_task_id)
        return False


def wait_container_and_check_exit(container, raise_on_error: bool = True) -> int:
    """
    Wait for container to finish and check exit code.
    
    Args:
        container: Docker container object
        raise_on_error: Raise exception if exit code != 0
        
    Returns:
        Exit code
        
    Raises:
        RuntimeError: If exit code != 0 and raise_on_error is True
    """
    run_status = container.wait()
    exit_code = run_status.get("StatusCode", -1)
    
    if exit_code != 0 and raise_on_error:
        raise RuntimeError(f"Container exited with non-zero exit code: {exit_code}")
    
    return exit_code
