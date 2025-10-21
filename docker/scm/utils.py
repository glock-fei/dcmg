import json
import logging
import os
from pathlib import Path

import oss2
import requests
from oss2.credentials import EnvironmentVariableCredentialsProvider

log = logging.getLogger(__name__)


def send_progress(percent: float):
    """
    Send progress to progress url.

    The main program calls this function to send progress to your server in a non-blocking manner
    args:
        percent: float, progress percentage.
    """
    # todo please implement this function to send progress to your server.
    try:
        with requests.get(os.getenv("SCM_PROGRESS_URL"),
                          params={"percent": percent, "run_id": os.getenv("NOMAD_META_NO_ID")},
                          timeout=(1, 1)) as res:
            res.raise_for_status()

            log.info("Send progress successed")
    except (requests.exceptions.RequestException, Exception) as e:
        log.error("Send progress failed: %s", e)


def upload_image_to_oss(job_id: str):
    """
    Upload image to oss.
    The main program calls this function to upload an image to OSS in a blocking manner,
    meaning it will wait until the upload is complete.
    This function should handle exceptions independently to prevent the main program from crashing without being affected.

    The function returns a callback function.
    The callback function takes one argument, the image local path.
    args:
        image_local_path: Path
    returns:
        callback function, return image http url.
    """
    # todo please implement this function to upload image to oss.
    try:
        # initialize oss client
        auth = oss2.ProviderAuthV4(EnvironmentVariableCredentialsProvider())
        bucket = oss2.Bucket(
            auth,
            os.getenv("OSS_ENDPOINT"),
            os.getenv("OSS_BUCKET"),
            region=os.getenv("OSS_REGION")
        )
        upload_oss_key = os.getenv("OSS_UPLOAD_KEY")
        log.info("%s Initialize oss client %s", "▪ " * 15, "▪ " * 15)

        # upload to oss
        def callback(image_local_path: Path):
            try:
                oss_images_url = f"{upload_oss_key}/{job_id}/{image_local_path.name}"
                bucket.put_object_from_file(oss_images_url, image_local_path)
                log.info("Upload successed: %s", image_local_path)

                return os.getenv("OSS_DOMAIN") + oss_images_url

            except (oss2.exceptions.OssError, ValueError, FileNotFoundError, Exception) as ose:
                log.error("Upload image to oss failed: %s", ose)

        return callback
    except (oss2.exceptions.OssError, ValueError, Exception) as e:
        log.error("Initialize oss client failed: %s", e)


def send_detection_results(report_filename: Path):
    """
    Send detection results.

    The main program calls this function to send detection results to your server in a blocking manner.
    This function should handle exceptions independently to prevent the main program from crashing without being affected.

    args:
        report_filename: str, report file path.
    """
    # todo please implement this function to send detection results to your server.

    try:
        with open(report_filename, "r", encoding="utf-8") as report:
            data = json.load(report)

            with requests.post(os.getenv("SCM_REPORT_URL"), json=data, timeout=(2, 2)) as res:
                res.raise_for_status()

                log.info("Send detection results successed")
    except (requests.exceptions.RequestException, Exception) as e:
        log.error("Send detection results failed: %s", e)
