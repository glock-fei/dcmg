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
    radiometric = Column(JSON, nullable=True)
    update_at = Column(DateTime, nullable=False, default=datetime.now())

    # Relationship to OdmReport
    reports = relationship("OdmReport", back_populates="job")
    generates = relationship("OdmGeTask", back_populates="job", uselist=False)
    vegetation = relationship("OdmVegetation", back_populates="job")  # This should be a list (one-to-many)


class OdmGeTask(Base):
    __tablename__ = "odm_generate_task"

    id = Column(Integer, primary_key=True)
    # Foreign key relationship to OdmJobs
    job_id = Column(Integer, ForeignKey("odm_jobs.id"), nullable=False, unique=True, index=True)
    orthophoto_tif = Column(String(255), nullable=True)

    state = Column(String(16), nullable=False, default="PENDING")
    progress = Column(Float, nullable=True, default=0.0)
    celery_task_id = Column(String(64), nullable=True)
    err_msg = Column(String(255), nullable=True)
    update_at = Column(DateTime, nullable=False, default=datetime.now())

    # Relationship back to OdmJobs
    job = relationship("OdmJobs", back_populates="generates")


class OdmReport(Base):
    __tablename__ = "odm_reports"

    id = Column(Integer, primary_key=True)
    # Foreign key relationship to OdmJobs
    job_id = Column(Integer, ForeignKey("odm_jobs.id"), nullable=True, index=True, unique=True)
    output_dir = Column(String(255), nullable=True)
    log_file = Column(String(255), nullable=True)
    orthophoto_tif = Column(String(255), nullable=True)
    area_mu = Column(Float)
    create_at = Column(DateTime, nullable=False, default=datetime.now())
    progress = Column(Float, nullable=True, default=0.0)
    state = Column(String(16), nullable=False, default="PENDING")
    band = Column(JSON, nullable=True)
    celery_task_id = Column(String(64), nullable=True, index=True)
    err_msg = Column(String(255), nullable=True)
    update_at = Column(DateTime, nullable=False, default=datetime.now())

    # Relationship back to OdmJobs
    job = relationship("OdmJobs", back_populates="reports")
    
    @classmethod
    def upsert(cls, db, job_id, **kwargs):
        """
        Create or update an OdmReport record based on job_id.
        
        This method first queries for an existing record with the given job_id.
        If found, it updates the record. Otherwise, it creates a new one.
        
        Args:
            db: Database session
            job_id (int): The job ID to associate with the report
            **kwargs: Fields to update or create with their values
            
        Returns:
            OdmReport: The updated or newly created report instance
        """
        # Query for existing report
        report = db.query(cls).filter(cls.job_id == job_id).first()
        
        # If no existing report found, create a new one
        if not report:
            report = cls(job_id=job_id)
            db.add(report)
        
        # Update all provided fields
        for key, value in kwargs.items():
            setattr(report, key, value)
            
        # Always update the timestamp
        report.update_at = datetime.now()


class OdmVegetation(Base):
    __tablename__ = "odm_vegetation"
    __table_args__ = (
        UniqueConstraint("job_id", "algo_name", name="uq_r_project_task_algo"),
    )

    id = Column(Integer, primary_key=True)
    # Foreign key relationship to OdmJobs
    job_id = Column(Integer, ForeignKey("odm_jobs.id"), nullable=True, index=True)
    algo_name = Column(String(64), nullable=False)
    
    min_value = Column(Float)
    max_value = Column(Float)
    mean = Column(Float)
    stddev = Column(Float)

    output_files = Column(JSON, nullable=True)
    class_count = Column(JSON, nullable=True)

    # Relationship back to OdmJobs
    job = relationship("OdmJobs", back_populates="vegetation")

    @classmethod
    def upsert(cls, db, job_id, algo_name, **kwargs):
        """
        Create or update an OdmReport record based on job_id.

        This method first queries for an existing record with the given job_id.
        If found, it updates the record. Otherwise, it creates a new one.

        Args:
            db: Database session
            job_id (int): The job ID to associate with the report
            algo_name (str): The name of the algorithm used to generate the report
            **kwargs: Fields to update or create with their values

        Returns:
            OdmReport: The updated or newly created report instance
        """
        # Query for existing report
        vegetation = db.query(cls).filter(
            cls.job_id == job_id,
            cls.algo_name == algo_name
        ).first()

        # If no existing report found, create a new one
        if not vegetation:
            vegetation = cls(job_id=job_id)
            db.add(vegetation)

        # Update all provided fields
        vegetation.algo_name = algo_name
        for key, value in kwargs.items():
            setattr(vegetation, key, value)
