# -*- coding: utf-8 -*-
# @Time    : 2026/4/24 14:50
# @Author  : feidu
# @File    : yoscm.py
import logging
import os

from pathlib import Path
from typing import Optional

from celery.contrib.abortable import AbortableTask

import utils
from worker.app import app
from worker.tasks.common import (
    TaskCanceledException,
    stream_container_logs,
    run_docker_container,
    wait_container_and_check_exit
)

logger = logging.getLogger(__name__)


@app.task(bind=True, base=AbortableTask)
def start_yoscm_task(
        self,
        run_id: str,
        images_dir: str,
        output_dir: str,
        log_file: str,
        plant_spacing_cm: float,
        row_spacing_cm: float,
        ridge_spacing_cm: Optional[float] = None,
        gsd_cm: float = 0.32,
        big_threshold: float = 0.3,
        small_threshold: float = 0.2,
):
    """
    Start a container to run the yoscm job as a Celery task.
    """
    CONTAINER_OUTPUT_DIR = "/app/output"

    scm_state = utils.StateBase(state=utils.OdmJobStatus.running.value)

    try:
        logger.info("Starting YOSCM job container. run_id: %s, images_dir: %s, output_dir: %s",
                    run_id, images_dir, output_dir)

        # build predictor command
        command = [
            "./start",
            "--plant_spacing", str(plant_spacing_cm),
            "--row_spacing", str(row_spacing_cm),
            "--gsd", str(gsd_cm),
            "--big_threshold", str(big_threshold),
            "--small_threshold", str(small_threshold),
            "--output_dir", CONTAINER_OUTPUT_DIR
        ]
        if ridge_spacing_cm:
            command.extend(["--ridge_spacing", str(ridge_spacing_cm)])

        logger.debug("SCM command: %s", command)

        # Run Docker container using common function
        container = run_docker_container(
            image=os.getenv("SCM_IMAGE"),
            command=command,
            use_gpu=True,
            environment={
                "NOMAD_META_NO_ID": run_id,
                "SCM_PROGRESS_URL": os.getenv("SCM_PROGRESS_URL"),
                "SCM_REPORT_URL": os.getenv("SCM_REPORT_URL"),
                "OSS_ACCESS_KEY_ID": os.getenv("OSS_ACCESS_KEY_ID"),
                "OSS_ACCESS_KEY_SECRET": os.getenv("OSS_ACCESS_KEY_SECRET"),
                "OSS_BUCKET": os.getenv("OSS_BUCKET"),
                "OSS_ENDPOINT": os.getenv("OSS_ENDPOINT"),
                "OSS_REGION": os.getenv("OSS_REGION"),
                "OSS_DOMAIN": os.getenv("OSS_DOMAIN"),
                "OSS_UPLOAD_KEY": os.getenv("OSS_UPLOAD_KEY"),
            },
            extra_hosts={
                os.getenv("SERVICE_HOST_GATEWAY"): "host-gateway"
            },
            volumes={
                str(Path(images_dir).absolute()): {"bind": "/app/images", "mode": "rw"},
                str(Path(output_dir).absolute()): {"bind": CONTAINER_OUTPUT_DIR, "mode": "rw"},
                str(Path("docker/scm/license").absolute()): {"bind": "/app/license", "mode": "ro"},
                str(Path("docker/scm/utils.py").absolute()): {"bind": "/app/utils.py", "mode": "rw"},
            }
        )

        logger.info("YOSCM container started. container_id: %s", container.id)

        # Stream container logs and check for abort signals
        stream_container_logs(
            container=container,
            log_file=log_file,
            is_aborted_callback=self.is_aborted
        )

        # Wait for container to finish and check exit code
        wait_container_and_check_exit(container)
        
        logger.info("YOSCM job completed successfully. run_id: %s", run_id)
        scm_state.state = utils.JobStatus.Completed.value

    except TaskCanceledException as e:
        logger.warning(e)
        scm_state.state = utils.JobStatus.Canceled.value
        scm_state.error = str(e)
    except Exception as e:
        logger.error("Failed to start SCM job container: %s", e, exc_info=True)
        scm_state.state = utils.JobStatus.Failed.value
        scm_state.error = str(e)
    finally:
        return scm_state.dict()
