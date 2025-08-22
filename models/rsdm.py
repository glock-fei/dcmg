from .session import Base
from sqlalchemy import Column, Integer, String, Float, DateTime, UniqueConstraint, Index, JSON, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime


class OdmJobs(Base):
    __tablename__ = "odm_jobs"

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

    # Relationship to OdmReport
    reports = relationship("OdmReport", back_populates="job")


class OdmReport(Base):
    __tablename__ = "odm_reports"

    __table_args__ = (
        UniqueConstraint("job_id", "algo_name", name="uq_r_project_task_algo"),
        Index("idx_r_celery_task_id", "celery_task_id")
    )

    id = Column(Integer, primary_key=True)
    # Foreign key relationship to OdmJobs
    job_id = Column(Integer, ForeignKey("odm_jobs.id"), nullable=True, index=True)

    algo_name = Column(String(64), nullable=False)
    output_dir = Column(String(255), nullable=True)
    log_file = Column(String(255), nullable=True)
    orthophoto_tif = Column(String(255), nullable=True)
    area_mu = Column(Float)
    min_value = Column(Float)
    max_value = Column(Float)
    mean = Column(Float)
    stddev = Column(Float)
    output_files = Column(JSON)
    class_count = Column(JSON)
    create_at = Column(DateTime, nullable=False, default=datetime.now())

    progress = Column(Float, nullable=True, default=0.0)
    state = Column(String(16), nullable=False, default="PENDING")
    celery_task_id = Column(String(64), nullable=True)
    err_msg = Column(String(255), nullable=True)
    update_at = Column(DateTime, nullable=False, default=datetime.now())

    # Relationship back to OdmJobs
    job = relationship("OdmJobs", back_populates="reports")
