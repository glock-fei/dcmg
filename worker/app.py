import os
from kombu import Queue
from celery import Celery


# @setup_logging.connect
# def config_loggers(*args, **kwargs):
#     logging.config.fileConfig('logs/conf.ini', disable_existing_loggers=False)


app = Celery(__name__)

# Configuration dictionary with commonly used settings
celery_config = {
    # "worker_hostname": __name__,
    # Broker and Backend settings
    "broker_url": os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0"),
    "result_backend": os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1"),

    # Serialization settings
    "task_serializer": "json",
    "result_serializer": "json",
    "accept_content": ["json"],

    # Timezone settings
    "timezone": "UTC",
    "enable_utc": True,

    # Task routing and naming
    "task_routes": {
        "worker.tasks.*": {"queue": "default"},
    },
    "task_queues": (
        Queue("default", routing_key="default"),
        Queue("odm_report", routing_key="odm_report"),
    ),

    # Worker settings
    "worker_concurrency": 1,
    "worker_pool": "threads",
    "worker_prefetch_multiplier": 0,
    "task_acks_late": True,

    # Result settings
    "result_expires": 24*3600,  # Results expire after 1 days
    "result_extended": True,  # Enable extended result attributes

    # Task tracking
    "task_track_started": True,
    "task_send_sent_event": True,

    # Include modules with tasks
    "include": ["worker.tasks"],

}

app.conf.update(**celery_config)
