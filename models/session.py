import os
import logging
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool
from contextlib import contextmanager

# Set up logging
logger = logging.getLogger(__name__)

# Set the database URL from the environment variable or use a default SQLite URL.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///database.db")

# Configure connection pool settings based on database type
pool_settings = {
    'poolclass': QueuePool,
    'pool_pre_ping': True,  # Validates connections before use
    'pool_recycle': 3600,  # Recycle connections after 1 hour
}

# Adjust settings based on database type
if DATABASE_URL.startswith('sqlite'):
    # SQLite-specific settings
    pool_settings.update({
        'connect_args': {"check_same_thread": False},
        'pool_recycle': -1,  # No need to recycle for SQLite
        'pool_size': 0,  # SQLite uses file-based locking
        'max_overflow': 0,
    })
else:
    # Settings for PostgreSQL, MySQL, etc.
    pool_settings.update({
        'pool_size': int(os.getenv('DB_POOL_SIZE', 10)),
        'max_overflow': int(os.getenv('DB_MAX_OVERFLOW', 20)),
        'pool_timeout': int(os.getenv('DB_POOL_TIMEOUT', 30)),
    })

# Create engine with optimized settings
engine = create_engine(DATABASE_URL, **pool_settings)

# Create SessionLocal with proper configuration
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    expire_on_commit=False  # Prevents unnecessary reloads after commit
)

Base = declarative_base()


def init_database():
    """
    Initialize the database by dropping all tables and creating them again.

    Warning: This will delete all existing data!
    """
    try:
        Base.metadata.reflect(bind=engine)
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error("Failed to initialize database: %s", str(e))
        raise


def get_database():
    """
    Dependency for FastAPI to provide database sessions.

    Provides a database session that is automatically closed after use.
    Usage: db: Session = Depends(get_database)

    Yields:
        Session: SQLAlchemy database session
    """
    db = SessionLocal()
    try:
        yield db
    except Exception as e:
        logger.error("Database session error: %s", str(e))
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def with_db_session():
    """
    Context manager for database sessions.

    Usage:
        with with_db_session() as db:
            # Use db session here
            pass
    """
    db = SessionLocal()
    try:
        yield db
    except Exception as e:
        db.rollback()
        logger.error("Database session error in context manager: %s", str(e))
        raise
    finally:
        db.close()
