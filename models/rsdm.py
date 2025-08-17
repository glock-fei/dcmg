from .session import Base
from sqlalchemy import Column, Integer, String, Float, DateTime, UniqueConstraint, Index
from datetime import datetime


class RsdmJobs(Base):
    __tablename__ = "rsdm_jobs"

    # Add unique constraint for odm_project_id and odm_task_id combination
    __table_args__ = (
        UniqueConstraint("odm_project_id", "odm_task_id", name="uq_project_task"),
        Index('idx_project_task', 'odm_project_id', 'odm_task_id'),
        Index("idx_celery_task_id", "celery_task_id")
    )

    id = Column(Integer, primary_key=True)
    run_id = Column(String(16), nullable=False, unique=True, index=True)
    odm_project_id = Column(Integer, nullable=False)
    odm_task_id = Column(String(64), nullable=False)
    odm_job_name = Column(String(255), nullable=False)
    odm_job_type = Column(String(16), nullable=False)
    odm_src_folder = Column(String(255), nullable=False)
    odm_dest_folder = Column(String(255), nullable=False)
    odm_samplinge_time = Column(DateTime, nullable=True)
    odm_host = Column(String(255), nullable=False)
    odm_image_count = Column(Integer, nullable=False, default=0)
    odm_create_at = Column(DateTime, nullable=False, default=datetime.now())
    progress = Column(Float, nullable=True, default=0.0)
    state = Column(String(16), nullable=False, default="PENDING")
    celery_task_id = Column(String(64), nullable=True)
    err_msg = Column(String(255), nullable=True)
    update_at = Column(DateTime, nullable=False, default=datetime.now())
