from .session import Base
from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime


class Job(Base):
    __tablename__ = 'job'

    id = Column(Integer, primary_key=True)
    run_id = Column(String(16), nullable=False, unique=True, index=True)
    job_name: Column = Column(String(255), nullable=False)
    images_dir = Column(String(255), nullable=False)
    output_dir = Column(String(255), nullable=False)
    log_file = Column(String(255), nullable=False)
    images_total_num = Column(Integer, nullable=False)
    images_detect_num = Column(Integer, nullable=True, default=0)
    gsd = Column(Float, nullable=False)
    row_spacing = Column(Float, nullable=False)
    plant_spacing = Column(Float, nullable=False)
    ridge_spacing = Column(Float, nullable=True)
    big_threshold = Column(Float, nullable=False)
    small_threshold = Column(Float, nullable=False)
    progress = Column(Float, default=0.0)
    status: Column = Column(String(16), default='waiting')

    area_mu = Column(Float, default=0.0)
    num_big = Column(Integer, default=0)
    num_medium = Column(Integer, default=0)
    num_small = Column(Integer, default=0)
    num_total = Column(Integer, default=0)
    num_of_per_mu = Column(Integer, default=0)
    num_of_picture_mu = Column(Integer, default=0)
    big_ratio = Column(Float, default=0.0)
    medium_ratio = Column(Float, default=0.0)
    samll_ratio = Column(Float, default=0.0)
    total_ratio = Column(Float, default=0.0)
    vaild_ratio = Column(Float, default=0.0)
    avg_pixel_area = Column(Float, default=0.0)
    score = Column(Float, default=0.0)
    level = Column(String(8), nullable=True)

    container_id = Column(String(64), nullable=True)
    create_at = Column(DateTime, nullable=False, default=datetime.now())

    pictures = relationship("Picture", back_populates="job", cascade="all, delete-orphan")


class Picture(Base):
    __tablename__ = 'pictures'

    id = Column(Integer, primary_key=True)
    job_id = Column(Integer, ForeignKey('job.id'), nullable=False)
    output = Column(String(255), nullable=False)
    output_image_url = Column(String(255), nullable=False)
    filename = Column(String(255), nullable=False)
    filemd5 = Column(String(32))
    width = Column(Integer)
    height = Column(Integer)
    area_mu = Column(Float)
    num_of_picture_mu = Column(Integer)
    num_of_per_mu = Column(Integer)
    num_total = Column(Integer)
    num_big = Column(Integer)
    num_medium = Column(Integer)
    num_small = Column(Integer)
    big_ratio = Column(Float)
    medium_ratio = Column(Float)
    samll_ratio = Column(Float)
    total_ratio = Column(Float)
    vaild_ratio = Column(Float)
    longitude = Column(Float)
    latitude = Column(Float)
    create_at = Column(DateTime, nullable=False, default=datetime.now())

    job = relationship("Job", back_populates="pictures")
