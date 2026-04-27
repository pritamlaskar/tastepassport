from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy.pool import NullPool
from config import get_settings

settings = get_settings()

engine = create_engine(
    settings.database_url,
    poolclass=NullPool,
    echo=(settings.environment == "development"),
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Run Alembic migrations to head on startup.
    Falls back to create_all if Alembic is not configured (e.g. fresh test env).
    """
    import logging
    logger = logging.getLogger(__name__)

    from models import restaurant, source, city, crawl_job  # noqa: F401 — register models

    try:
        from alembic.config import Config
        from alembic import command
        import os

        alembic_cfg = Config(os.path.join(os.path.dirname(__file__), "alembic.ini"))
        alembic_cfg.set_main_option("sqlalchemy.url", settings.database_url)
        alembic_cfg.set_main_option(
            "script_location", os.path.join(os.path.dirname(__file__), "alembic")
        )
        command.upgrade(alembic_cfg, "head")
        logger.info("Database migrations applied (alembic upgrade head)")
    except Exception as e:
        logger.warning(f"Alembic migration failed ({e}) — falling back to create_all")
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables created via SQLAlchemy create_all")


def check_db_connection() -> bool:
    """Return True if the database is reachable."""
    try:
        with engine.connect() as conn:
            conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        return True
    except Exception:
        return False
