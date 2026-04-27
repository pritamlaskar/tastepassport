"""
Scheduler — checks for stale cities and queues recrawl jobs.
Called periodically by Celery beat (configured in crawl_tasks.py).
"""
import logging
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from database import SessionLocal
from models.city import City, CrawlStatus

logger = logging.getLogger(__name__)


def schedule_stale_recrawls():
    from config import get_settings
    settings = get_settings()
    threshold = datetime.utcnow() - timedelta(days=settings.crawl_recrawl_days)

    db: Session = SessionLocal()
    try:
        stale_cities = (
            db.query(City)
            .filter(
                City.crawl_status == CrawlStatus.complete,
                City.last_crawled_at < threshold,
            )
            .all()
        )

        if not stale_cities:
            logger.info("[Scheduler] No stale cities found")
            return

        logger.info(f"[Scheduler] Found {len(stale_cities)} stale cities — queuing recrawls")
        for city in stale_cities:
            from tasks.crawl_tasks import run_city_pipeline
            run_city_pipeline.delay(city.id)
            city.crawl_status = CrawlStatus.running
            logger.info(f"[Scheduler] Queued recrawl for {city.name}")

        db.commit()

    finally:
        db.close()
