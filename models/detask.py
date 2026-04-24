# -*- coding: utf-8 -*-
import shutil
from datetime import datetime
from pathlib import Path

from .session import Base
from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime
from sqlalchemy.orm import relationship


from utils.cedoke import JobSettings, start_scm_job_container, calculate_healthy_score
from sqlalchemy.orm import Session


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

    # -------------------------------------------------------------------------
    # Create Job instance from settings
    # -------------------------------------------------------------------------
    @classmethod
    def from_settings(cls, data: JobSettings, run_id: str, images_dir: Path):
        """Create a new Job instance from settings."""

        output_dir, log_file = cls.prepare_output_dir(images_dir)

        return cls(
            job_name=data.job_name.strip() if data.job_name else run_id,
            run_id=run_id,
            gsd=data.gsd_cm,
            row_spacing=data.row_spacing_cm,
            plant_spacing=data.plant_spacing_cm,
            ridge_spacing=data.ridge_spacing_cm,
            big_threshold=data.big_threshold,
            small_threshold=data.small_threshold,
            images_dir=str(images_dir),
            output_dir=str(output_dir),
            log_file=str(log_file),
            images_total_num=len([f for f in images_dir.iterdir() if f.is_file()]),
        )

    def save(self, db: Session):
        """Save job to database."""
        db.add(self)
        db.commit()
        db.refresh(self)

        return self

    # -------------------------------------------------------------------------
    # Prepare output directory and log file
    # -------------------------------------------------------------------------
    @staticmethod
    def prepare_output_dir(images_dir: Path) -> tuple[Path, Path]:
        """Create output directory and log file, return paths."""
        output_dir = images_dir / "output"
        log_file = output_dir / "app.log"
        output_dir.mkdir(parents=True, exist_ok=True)
        log_file.touch(exist_ok=True)

        return output_dir, log_file

    # -------------------------------------------------------------------------
    # Start the job
    # -------------------------------------------------------------------------
    def start(self, db: Session):
        """Start the job by launching a container and updating status."""
        if self.status in ("running", "completed"):
            raise ValueError(f"Job {self.run_id} is already {self.status}")

        container_id = start_scm_job_container(
            run_id=self.run_id,
            images_dir=Path(self.images_dir).absolute(),
            output_dir=Path(self.output_dir).absolute(),
            log_file=Path(self.log_file).absolute(),
            plant_spacing_cm=self.plant_spacing,
            row_spacing_cm=self.row_spacing,
            ridge_spacing_cm=self.ridge_spacing,
            gsd_cm=self.gsd,
            big_threshold=self.big_threshold,
            small_threshold=self.small_threshold,
        )

        self.container_id = container_id

        return self.save(db)

    # -------------------------------------------------------------------------
    # Get job
    # -------------------------------------------------------------------------
    @classmethod
    def get_by_id(cls, db: Session, job_id: int) -> "Job | None":
        return db.query(cls).filter(cls.id == job_id).first()

    @classmethod
    def get_by_run_id(cls, db: Session, run_id: str) -> "Job | None":
        return db.query(cls).filter(cls.run_id == run_id).first()

    # -------------------------------------------------------------------------
    # Update progress by run_id
    # -------------------------------------------------------------------------
    @classmethod
    def update_progress_by_run_id(cls, db: Session, run_id: str, percent: float) -> float:
        """Update progress directly by run_id without querying first."""
        db.query(cls).filter(cls.run_id == run_id).update({
            cls.progress: percent,
            cls.status: "completed" if percent >= 100.00 else "running"
        })
        db.commit()
        return percent

    # -------------------------------------------------------------------------
    # Update status
    # -------------------------------------------------------------------------
    def update_status(self, db: Session, status: str) -> "Job":
        self.status = status
        return self.save(db)

    # -------------------------------------------------------------------------
    # Save report
    # -------------------------------------------------------------------------
    def save_report(self, db: Session, data: dict) -> "Job":
        for k, v in data.items():
            if k in self.__table__.columns.keys():
                setattr(self, k, v)

        self.score, self.level = calculate_healthy_score(self.vaild_ratio, self.total_ratio)
        self.progress = 100.00
        self.status = "completed"

        detected_images = data.get("images", [])
        self.images_detect_num = len(detected_images)
        for image in detected_images:
            picture = Picture(
                job_id=self.id,
                **{k: v for k, v in image.items() if k in Picture.__table__.columns.keys()}
            )
            output_path = Path(self.output_dir) / Path(picture.output).name
            picture.output = output_path.as_posix()
            db.add(picture)

        return self.save(db)

    # -------------------------------------------------------------------------
    # Remove job
    # -------------------------------------------------------------------------
    def remove(self, db: Session) -> None:
        db.delete(self)
        db.commit()
        images_dir = Path(self.images_dir)
        if images_dir.exists():
            shutil.rmtree(images_dir)


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
