import logging
import os
import time
from enum import Enum
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

from pydantic import BaseModel
import requests


class OdmType(Enum):
    """
    Enumeration of supported ODM image types.
    """
    multispectral = "multispectral"
    rgb = "rgb"


class OdmJobStatus(Enum):
    """
    Model for ODM job status.
    """
    pending = "PENDING"
    running = "RUNNING"
    completed = "COMPLETED"
    failed = "FAILED"
    canceled = "CANCELED"


class OdmState(BaseModel):
    state: Optional[str] = None
    progress: Optional[float] = 0.00
    host: Optional[str] = None
    error: Optional[str] = None


class OdmJob(BaseModel):
    """
    Data model representing an ODM job configuration and metadata.

    This model contains all necessary information to create and manage
    an ODM (OpenDroneMap) processing job.
    """
    odm_project_id: int
    odm_task_id: str
    odm_job_name: str
    odm_job_type: OdmType
    odm_src_folder: str
    odm_samplinge_time: datetime
    odm_host: str
    odm_create_at: datetime

    def to_dict(self):
        """
        Convert the OdmJob instance to a dictionary representation.

        Returns:
            dict: Dictionary containing all OdmJob attributes with enum values converted to strings
        """
        return {
            "odm_project_id": self.odm_project_id,
            "odm_task_id": self.odm_task_id,
            "odm_job_name": self.odm_job_name,
            "odm_job_type": self.odm_job_type.value,
            "odm_src_folder": self.odm_src_folder,
            "odm_samplinge_time": self.odm_samplinge_time,
            "odm_host": self.odm_host,
            "odm_create_at": self.odm_create_at
        }


def find_images(src_folder: str, fot: OdmType):
    """
    Find all images in a folder matching the specified image type.

    This function searches recursively through the specified folder and
    identifies all files with extensions matching the requested image type.

    Args:
        src_folder (str): The source folder to search for images
        fot (OdmType): The image type to search for (rgb or multispectral)

    Returns:
        list: List of absolute file paths to matching images

    Example:
        >>> images = find_images("drone_photos/", OdmType.rgb)
        >>> print(len(images))
        42
    """
    # todo This is just a rough implementation that requires optimization in the future.
    extensions = {
        'rgb': [
            '.jpg', '.jpeg', '.png', '.bmp', '.webp', '.jp2'
        ],
        'multispectral': [
            '.tif', '.tiff', '.hdr', '.raw', '.cr2', '.nef', '.arw', '.dng', '.orf'
        ]
    }
    targer_folder = Path(src_folder)

    result = []
    for path in targer_folder.rglob('*'):
        if path.is_file():
            ext = path.suffix.lower()
            if ext in extensions.get(fot.value, []):
                result.append(str(path.resolve()))

    return result


def _callback_odm_api(project_id: int, task_id: str, api_url: str):
    """
    Callback function to interact with the ODM server API.

    Sends a POST request to the specified ODM server API endpoint.
    This function is typically used for various ODM job management operations
    such as removing/canceling/committing jobs or committing completed jobs.

    Args:
        project_id (int): The ID of the project containing the task
        task_id (str): The unique identifier of the task to operate on
        api_url (str): The API endpoint URL with placeholders for project_id and task_id

    Raises:
        requests.exceptions.RequestException: If the HTTP request fails
    """
    successfully = False
    # Replace placeholders in the API URL with actual values
    api_url = api_url.replace("#project_id#", str(project_id))
    api_url = api_url.replace("#task_id#", task_id)

    # todo: add retry logic for API calls
    # Retry the API call up to a maximum number of times
    # max_retries = int(os.getenv('ODM_API_MAX_RETRIES', 3))
    # retry_delay = int(os.getenv('ODM_API_RETRY_DELAY', 10))
    # for attempt in range(max_retries + 1):

    try:
        # if attempt > 0:
        #     logging.warning("Retrying ODM API callback.API URL: %s, Attempt: %s", api_url, attempt)
        #     # Wait for 10 seconds before retrying the API call
        #     time.sleep(retry_delay)

        # Send POST request to the ODM API endpoint
        res = requests.post(url=api_url, timeout=(3, 5))
        successfully = (res.status_code == 200)

        logging.info("ODM API callback successful. Task ID: %s, "
                     "API URL: %s, Response Status: %s, Response Body: %s",
                     task_id, api_url, res.status_code, res.text)
    except requests.exceptions.RequestException as e:
        # Log error details when the HTTP request fails
        logging.error("Failed to execute ODM API callback. Task ID: %s, API URL: %s, Error: %s",
                      task_id, api_url, str(e))

    return successfully


def remove_odm_task(project_id: int, task_id: str, base_url: str):
    """
    Remove an ODM job from the remote ODM server.

    Sends a request to the ODM server to remove/cancel a specific job.
    This is typically used when no images are found or when cleaning up failed jobs.

    Args:
        project_id (int): The ID of the project containing the task
        task_id (str): The unique identifier of the task to remove
        base_url (str): The base URL of the ODM server instance

    Raises:
        requests.exceptions.RequestException: If the HTTP request fails
    """
    odm_remove_api = os.getenv('ODM_REMOVE_API', "/api/projects/#project_id#/tasks/#task_id#/remove/")
    api_url = urljoin(base_url.rstrip("/") + "/", odm_remove_api)

    return _callback_odm_api(project_id, task_id, api_url)


def commit_odm_task(project_id: int, task_id: str, base_url: str):
    """
    Commit a completed ODM job by notifying the ODM server.

    When an ODM job reaches 100% completion, this function retrieves
    the job details from the database and sends a commit request to
    the ODM server to finalize the processing.

    Args:
        project_id (int): The ID of the project containing the task
        task_id (str): The unique identifier of the task to commit
        base_url (str): The base URL of the ODM server instance

    Note:
        The commit operation only executes when percent >= 100
    """
    odm_commit_api = os.getenv('ODM_COMMINT_API', "/api/projects/#project_id#/tasks/#task_id#/commit/")
    api_url = urljoin(base_url.rstrip("/") + "/", odm_commit_api)

    return _callback_odm_api(project_id, task_id, api_url)


def cancel_odm_task(project_id: int, task_id: str, base_url: str):
    """
    Cancel an ODM job by notifying the ODM server.
    This function retrieves the job details from the database and
    sends a cancel request to the ODM server to stop processing.
    Args:
        project_id (int): The ID of the project containing the task
        task_id (str): The unique identifier of the task to cancel
        base_url (str): The base URL of the ODM server instance

    Note:
        The cancel operation only executes when percent < 100
    """
    odm_cancel_api = os.getenv('ODM_CANCEL_API', "/api/projects/#project_id#/tasks/#task_id#/cancel/")
    api_url = urljoin(base_url.rstrip("/") + "/", odm_cancel_api)

    return _callback_odm_api(project_id, task_id, api_url)


def get_src_folder(folder: str) -> str:
    """
    Get the source folder path from the specified relative path.

    Args:
        folder (str): The relative path to the source folder.

    Returns:
        str: The source folder path
    """
    mount_usb_dir = os.getenv("MOUNT_USB_DIR", "static/")
    targer_dir = Path(mount_usb_dir) / folder.lstrip("/")

    return str(targer_dir.resolve())


def get_dest_folder(project_id: int, task_id: str) -> str:
    """
    Get the destination folder path for an ODM task.

    Args:
        project_id (int): The project ID
        task_id (str): The task ID

    Returns:
        str: The destination folder path
    """
    odm_media_dir = os.getenv('ODM_MEDIA_DIR', "tmp/#project_id#/#task_id#")
    odm_media_dir = odm_media_dir.replace("#project_id#", str(project_id))
    odm_media_dir = odm_media_dir.replace("#task_id#", task_id)
    dest_folder = Path(odm_media_dir)

    # create directory structure if needed
    if not os.path.exists(dest_folder):
        os.makedirs(dest_folder, exist_ok=True)

    return str(dest_folder.resolve())
