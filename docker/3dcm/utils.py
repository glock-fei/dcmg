import os
from pathlib import Path
from tools import Logger

import requests

log = Logger().get_log()


def send_progress(percent: float):
    """
    Send progress to progress url.

    The main program calls this function to send progress to your server in a non-blocking manner
    args:
        percent: float, progress percentage.
    """
    try:
        with requests.patch(os.getenv("3DCM_PROGRESS_URL").replace("%job_no%", os.getenv("3DCM_NO")),
                            params={"percent": percent},
                            timeout=(1, 1)) as res:
            res.raise_for_status()
    except (requests.exceptions.RequestException, Exception) as e:
        log.error("Send progress failed: %s", e)


def postprocess(output_dir: Path, **kwargs):
    """
    Postprocess function.
    This function is called after the 3D conversion is complete.
    It will upload the converted files to OSS and run potree converter to generate a potree.
    args:
        output_dir: Path, the output directory of the 3D conversion. default is /app/images/output/
    """
    pass
