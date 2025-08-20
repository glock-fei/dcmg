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
    with open(report_filename, "r", encoding="utf-8") as report:
        data = json.load(report)

        for item in data.get("report"):
            file_name = item.get("file_name")
            area_mu = item.get("area_mu")

            # send report to server
            for algo_name, odm_rep in item.items():
                if algo_name == "ndvi":
                    try:
                        res = requests.post(
                            url=os.getenv("ODM_REPORT_API"),
                            json={
                                "project_id": os.getenv("ODM_PROJECT_ID"),
                                "task_id": os.getenv("ODM_TASK_ID"),
                                "algo_name": algo_name,
                                "file_name": file_name,
                                "odm_host": os.getenv("ODM_HOST"),
                                "output_dir": os.getenv("OMD_OUTPUT_DIR"),
                                "log_file": os.getenv("ODM_LOG_FILE"),
                                "area_mu": area_mu,
                                "report": odm_rep
                            }, timeout=(2, 3)
                        )

                        log.info("Send report to server, http status code:%d", res.status_code)
                    except requests.exceptions.RequestException as e:
                        log.error("Failed to send report to server, error:%s", e)
