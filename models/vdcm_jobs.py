from .session import Base
from sqlalchemy import Column, Integer, String, Float, DateTime, JSON, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime


class VdcmJobs(Base):
    __tablename__ = "vdcm_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    no = Column(String(32), nullable=False, unique=True, index=True)
    title:  Column = Column(String(255), nullable=True)
    job_type = Column(String(16), nullable=False)
    src_path = Column(String(255), nullable=False)
    dest_path = Column(String(255), nullable=False)
    frame_count = Column(Integer, nullable=False, default=150)

    sample_number = Column(String(16), nullable=False)
    sample_batch_number = Column(String(16), nullable=False)
    image_total = Column(Integer, nullable=False, default=0)
    taken_at = Column(DateTime, nullable=False)

    potree_js = Column(String(255), nullable=True)
    report_ply = Column(String(255), nullable=True)
    err_msg = Column(JSON, nullable=True)
    log_file = Column(String(255), nullable=True)
    cover = Column(String(255), nullable=True)
    reports = Column(JSON, nullable=True)

    progress = Column(Float, nullable=True, default=0.0)
    state: Column = Column(String(16), nullable=False, default="PENDING")
    celery_task_id = Column(String(64), nullable=True)
    container_id = Column(String(64), nullable=True)
    update_at = Column(DateTime, nullable=False, default=datetime.now())
    create_at = Column(DateTime, nullable=False, default=datetime.now())

    uploads = relationship("VdcmUploads", back_populates="job", uselist=False)


class VdcmUploads(Base):
    __tablename__ = "vdcm_jobs_uploads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, ForeignKey("vdcm_jobs.id"), nullable=True, index=True)

    cloud_id = Column(Integer, nullable=True)
    source_ply = Column(String(255), nullable=True)
    ply_cloud_js = Column(String(255), nullable=True)
    cover = Column(String(255), nullable=True)
    log_file = Column(String(255), nullable=True)

    update_at = Column(DateTime, nullable=False, default=datetime.now())
    create_at = Column(DateTime, nullable=False, default=datetime.now())
    progress = Column(Float, nullable=True, default=0.0)
    state = Column(String(16), nullable=False, default="PENDING")
    celery_task_id = Column(String(64), nullable=True)

    job = relationship("VdcmJobs", back_populates="uploads")
