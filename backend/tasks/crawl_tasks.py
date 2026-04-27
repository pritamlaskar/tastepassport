from celery import Celery
from celery.schedules import crontab
from config import get_settings

settings = get_settings()

celery_app = Celery(
    "tastepassport",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    beat_schedule={
        "check-stale-cities-daily": {
            "task": "tasks.crawl_tasks.check_stale_cities",
            "schedule": crontab(hour=3, minute=0),
        },
    },
)


@celery_app.task(bind=True, name="tasks.crawl_tasks.run_city_pipeline", max_retries=2)
def run_city_pipeline(self, city_id: str):
    from database import SessionLocal
    from pipeline.orchestrator import run_pipeline
    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"[Task] Starting pipeline for city_id={city_id}")

    db = SessionLocal()
    try:
        result = run_pipeline(city_id=city_id, db=db)
        logger.info(f"[Task] Pipeline complete: {result}")
        return result
    except Exception as e:
        logger.error(f"[Task] Pipeline failed for city_id={city_id}: {e}")
        raise self.retry(exc=e, countdown=60)
    finally:
        db.close()


@celery_app.task(name="tasks.crawl_tasks.check_stale_cities")
def check_stale_cities():
    from pipeline.scheduler import schedule_stale_recrawls
    schedule_stale_recrawls()
