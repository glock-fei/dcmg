import os
from pathlib import Path
import json

import requests
from tools.logger import Logger

log = Logger().get_log()


def send_progress(percent: float):
    """
    Send progress to progress url.

    The main program calls this function to send progress to your server in a non-blocking manner
    args:
        percent: float, progress percentage.
    """
    # todo please implement this function to send progress to your server.
    pass


def send_report_to_server(report_filename: Path):
    """
    send detection results to server.

    The main program calls this function to send detection results to your server in a blocking manner.
    This function should handle exceptions independently to prevent the main program from crashing without being affected.

    args:
        report_filename: Path, report file path.
    """
    try:
        res = requests.put(
            url="{}/{}/{}".format(
                os.getenv("ODM_SAVE_REPORT_API"),
                os.getenv("ODM_PROJECT_ID"),
                os.getenv("ODM_TASK_ID")
            ), timeout=(2, 3)
        )

        log.info("Send report to server, http status code:%d", res.status_code)
    except requests.exceptions.RequestException as e:
        log.error("Failed to send report to server, error:%s", e)
