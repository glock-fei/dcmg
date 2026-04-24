import os
import logging
from typing import Optional

from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session
from sqlalchemy.engine import Engine
from sqlalchemy.pool import QueuePool

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


def _build_engine(database_url: str) -> Engine:
    """Build SQLAlchemy engine with pool settings based on database type."""
    pool_settings: dict = {
        'poolclass': QueuePool,
        'pool_pre_ping': True,
        'pool_recycle': 3600,
    }

    if database_url.startswith('sqlite'):
        pool_settings.update({
            'connect_args': {"check_same_thread": False},
            'pool_recycle': -1,
            'pool_size': 0,
            'max_overflow': 0,
        })
    else:
        pool_settings.update({
            'pool_size': int(os.getenv('DB_POOL_SIZE', 10)),
            'max_overflow': int(os.getenv('DB_MAX_OVERFLOW', 20)),
            'pool_timeout': int(os.getenv('DB_POOL_TIMEOUT', 30)),
        })

    return create_engine(database_url, **pool_settings)


class DatabaseManager:
    """
    Lazy-initialized database manager.

    Usage:
        # FastAPI dependency injection
        db: Session = Depends on(db_manager.get_database)

        # Context manager
        with db_manager.with_db_session() as db:
            db.add(obj)
            db.commit()
    """

    def __init__(self, database_url: Optional[str] = None):
        self.database_url = database_url or os.getenv("DATABASE_URL", "sqlite:///database.db")
        self._engine: Optional[Engine] = None
        self._session_factory: Optional[sessionmaker] = None

    # -------------------------------------------------------------------------
    # Core lazy accessors
    # -------------------------------------------------------------------------

    @property
    def engine(self) -> Engine:
        """Lazy-initialize and return the SQLAlchemy engine."""
        if self._engine is None:
            self._engine = _build_engine(self.database_url)
            logger.debug("Database engine created: %s", self.database_url)
        return self._engine

    @property
    def session_factory(self) -> sessionmaker:
        """Lazy-initialize and return the session factory."""
        if self._session_factory is None:
            self._session_factory = sessionmaker(
                bind=self.engine,
                autocommit=False,
                autoflush=False,
                expire_on_commit=False,
            )
        return self._session_factory

    # -------------------------------------------------------------------------
    # Session providers
    # -------------------------------------------------------------------------

    def get_database(self):
        """
        FastAPI dependency injection session provider.

        Usage:
            db: Session = Depends(db_manager.get_database)
        """
        db: Session = self.session_factory()
        try:
            yield db
        except Exception as e:
            logger.error("Database session error: %s", str(e))
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def with_db_session(self):
        """
        General-purpose context manager for database sessions.

        Usage:
            with db_manager.with_db_session() as db:
                db.add(obj)
                db.commit()
        """
        db: Session = self.session_factory()
        try:
            yield db
        except Exception as e:
            db.rollback()
            logger.error("Database session error: %s", str(e))
            raise
        finally:
            db.close()
            logger.debug("Database session closed")

    # -------------------------------------------------------------------------
    # Database lifecycle
    # -------------------------------------------------------------------------

    def init_database(self) -> None:
        """
        Initialize the database by dropping all tables and recreating them.

        Warning: This will delete all existing data!
        """
        try:
            Base.metadata.reflect(bind=self.engine)
            Base.metadata.drop_all(bind=self.engine)
            Base.metadata.create_all(bind=self.engine)
            logger.info("Database initialized successfully")
        except Exception as e:
            logger.error("Failed to initialize database: %s", str(e))
            raise

    def dispose(self) -> None:
        """
        Dispose the engine and reset all lazy state.
        Useful for testing or graceful shutdown.
        """
        if self._engine is not None:
            self._engine.dispose()
            logger.debug("Database engine disposed")
        self._engine = None
        self._session_factory = None


db_manager = DatabaseManager()