from .session import Base
from sqlalchemy import Column, Integer, String, Float, DateTime, UniqueConstraint, Index, JSON, ForeignKey, Boolean
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
    sampling = relationship("OdmSamplingRecord", back_populates="job")


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
    sampling = relationship("OdmSamplingRecord", back_populates="report")

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


class OdmSamplingRecord(Base):
    __tablename__ = "odm_sampling_records"

    id = Column(Integer, primary_key=True)
    # Foreign key relationship to OdmJobs
    job_id = Column(Integer, ForeignKey("odm_jobs.id"), nullable=False, index=True)
    report_id = Column(Integer, ForeignKey("odm_reports.id"), nullable=False, index=True)

    title = Column(String(255), nullable=True)
    state = Column(String(16), nullable=False, default="PENDING")
    progress = Column(Float, nullable=True, default=0.0)
    celery_task_id = Column(String(64), nullable=True)
    is_deleted = Column(Boolean, nullable=False, default=False)
    cloud_id = Column(Integer, nullable=True)
    latest_run_id = Column(String(16), nullable=True, index=True)

    create_at: Column = Column(DateTime, nullable=False, default=datetime.now())
    update_at: Column = Column(DateTime, nullable=False, default=datetime.now())

    job = relationship("OdmJobs", back_populates="sampling")
    report = relationship("OdmReport", back_populates="sampling")
    quadrats = relationship("OdmQuadrat", back_populates="sampling")

    @classmethod
    def upsert(cls, db, job_id, report_id, title=None):
        """
        Create or update an OdmSamplingRecord record based on job_id and report_id.
        
        This method first queries for an existing record with the given job_id and report_id.
        If found, it updates the record. Otherwise, it creates a new one.
        
        Args:
            db: Database session
            job_id (int): The job ID to associate with the sampling record
            report_id (int): The report ID to associate with the sampling record
            title (str, optional): The title of the sampling record
            
        Returns:
            OdmSamplingRecord: The updated or newly created sampling record instance
        """
        # Query for existing record
        record = db.query(cls).filter(
            cls.job_id == job_id,
            cls.report_id == report_id
        ).first()

        # If no existing record found, create a new one
        if not record:
            record = cls(job_id=job_id, report_id=report_id)
            db.add(record)

        # Update fields
        if title is not None:
            record.title = title
            
        record.create_at = datetime.now()
        
        return record

    @classmethod
    def create_record(cls, db, job_id, report_id, title=None):
        """
        Create a default OdmSamplingRecord record.
        
        Args:
            db: Database session
            job_id (int): The job ID to associate with the sampling record
            report_id (int): The report ID to associate with the sampling record
            title (str, optional): The title of the sampling record
            
        Returns:
            OdmSamplingRecord: The newly created sampling record instance
        """
        # Create a new sampling record
        record = cls(
            job_id=job_id,
            report_id=report_id,
            title=title if title else datetime.now().strftime("S%Y%m%d_%H%M%S")
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        
        return record


class OdmQuadrat(Base):
    __tablename__ = "odm_quadrat"

    id = Column(Integer, primary_key=True)
    # Foreign key relationship to OdmSamplingRecord
    sampling_id = Column(Integer, ForeignKey("odm_sampling_records.id"), nullable=False, index=True)
    run_id = Column(String(16), nullable=False, index=True)

    name = Column(String(255), nullable=True)
    coords = Column(JSON, nullable=False)
    center = Column(JSON, nullable=False)
    is_deleted = Column(Boolean, nullable=False, default=False)

    sampling = relationship("OdmSamplingRecord", back_populates="quadrats")
    statistics = relationship("OdmQuadratStatistics", back_populates="quadrat")

    @classmethod
    def create_quadrats(cls, db, sampling_id, quadrats_data):
        """
        Create multiple OdmQuadrat records for a given sampling_id.
        
        Args:
            db: Database session
            sampling_id (int): The sampling ID to associate with the quadrats
            quadrats_data (list): List of quadrat data dictionaries
            
        Returns:
            list: List of created OdmQuadrat instances
        """
        quadrats = []
        for quad_data in quadrats_data:
            quadrat = cls(
                sampling_id=sampling_id,
                name=quad_data.get("name"),
                coords=quad_data.get("coords"),
                center=quad_data.get("center")
            )
            db.add(quadrat)
            quadrats.append(quadrat)
            
        return quadrats

    @classmethod
    def get_quadrats_with_sampling(cls, db, sampling_id, latest_only=True):
        """
        Get quadrats for a sampling record with optional filtering for latest data.
        
        Args:
            db: Database session
            sampling_id (int): The sampling ID to get quadrats for
            latest_only (bool): If True (default), only return quadrats matching 
                               the latest run_id from the sampling record
            
        Returns:
            List of OdmQuadrat instances
        """
        query = db.query(cls).filter(cls.sampling_id == sampling_id)
        
        if latest_only:
            # Join with OdmSamplingRecord to filter by latest_run_id
            query = query.join(OdmSamplingRecord).filter(
                OdmSamplingRecord.id == cls.sampling_id,
                OdmSamplingRecord.latest_run_id == cls.run_id
            )
            
        return query.all()


class OdmQuadratStatistics(Base):
    __tablename__ = "odm_quadrat_statistics"
    __table_args__ = (
        Index('idx_quadrat_algo', 'quadrat_id', 'algo_name', unique=True),
    )
    
    id = Column(Integer, primary_key=True)

    quadrat_id = Column(Integer, ForeignKey("odm_quadrat.id"), nullable=False, index=True)
    algo_name = Column(String(64), nullable=False)
    picture = Column(String(255), nullable=False)
    dn_min = Column(Float)
    dn_max = Column(Float)
    dn_mean = Column(Float)
    dn_std = Column(Float)

    quadrat = relationship("OdmQuadrat", back_populates="statistics")

    @classmethod
    def upsert(cls, db, quadrat_id, algo_name, **kwargs):
        """
        Create or update an OdmQuadratStatistics record based on quadrat_id and algo_name.
        
        This method first queries for an existing record with the given quadrat_id and algo_name.
        If found, it updates the record. Otherwise, it creates a new one.
        
        Args:
            db: Database session
            quadrat_id (int): The quadrat ID to associate with the statistics
            algo_name (str): The name of the algorithm used to generate the statistics
            **kwargs: Fields to update or create with their values
            
        Returns:
            OdmQuadratStatistics: The updated or newly created statistics instance
        """
        # Query for existing record
        statistics = db.query(cls).filter(cls.quadrat_id == quadrat_id, cls.algo_name == algo_name).first()

        # If no existing record found, create a new one
        if not statistics:
            statistics = cls(quadrat_id=quadrat_id, algo_name=algo_name)
            db.add(statistics)

        # Update all provided fields
        for key, value in kwargs.items():
            setattr(statistics, key, value)
            
        return statistics
    
    @classmethod
    def bulk_upsert(cls, db, statistics_objects):
        """
        Bulk create or update OdmQuadratStatistics records from model instances.
        
        This method takes a list of OdmQuadratStatistics instances and performs
        upsert operations for each one.
        
        Args:
            db: Database session
            statistics_objects (list): List of OdmQuadratStatistics instances
            
        Returns:
            list: List of upserted OdmQuadratStatistics instances
        """
        upserted_records = []
        for stat_obj in statistics_objects:
            # Perform upsert for each record using the existing upsert method
            upserted = cls.upsert(
                db, 
                stat_obj.quadrat_id, 
                stat_obj.algo_name,
                picture=stat_obj.picture,
                dn_min=stat_obj.dn_min,
                dn_max=stat_obj.dn_max,
                dn_mean=stat_obj.dn_mean,
                dn_std=stat_obj.dn_std
            )
            upserted_records.append(upserted)
            
        return upserted_records
