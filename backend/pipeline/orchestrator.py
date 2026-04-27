"""
Pipeline orchestrator — chains all 5 stages for a city crawl.
Stages run sequentially. Each stage updates the crawl job record on completion.
A failure in one stage is logged but does not halt the pipeline
unless it is a prerequisite for all downstream stages.
"""
import logging
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

logger = logging.getLogger(__name__)


def run_pipeline(city_id: str, db: Session) -> dict:
    from models.city import City, CrawlStatus
    from models.crawl_job import CrawlJob, JobStatus

    city = db.query(City).filter(City.id == city_id).first()
    if not city:
        raise ValueError(f"City {city_id} not found")

    import uuid
    job = CrawlJob(
        id=str(uuid.uuid4()),
        city_id=city_id,
        status=JobStatus.running,
        sources_completed={},
        started_at=datetime.utcnow(),
    )
    db.add(job)
    city.crawl_status = CrawlStatus.running
    db.commit()
    db.refresh(job)

    logger.info(f"[{city.name}] Pipeline started — job {job.id}")

    try:
        _stage_reddit(city, job, db)
        _stage_blogs(city, job, db)
        _stage_extraction(city, job, db)
        _stage_scoring(city, job, db)
        _stage_enrichment(city, job, db)

        job.status = JobStatus.complete
        job.completed_at = datetime.utcnow()
        city.crawl_status = CrawlStatus.complete
        city.last_crawled_at = datetime.utcnow()

        from models.restaurant import Restaurant
        from config import get_settings
        settings = get_settings()
        city.total_places = (
            db.query(Restaurant)
            .filter(
                Restaurant.city_id == city_id,
                Restaurant.local_score >= settings.min_local_score,
                Restaurant.mention_count >= settings.min_mentions,
            )
            .count()
        )

        db.commit()
        logger.info(f"[{city.name}] Pipeline complete — {city.total_places} places indexed")
        return {"status": "complete", "city": city.name, "total_places": city.total_places}

    except Exception as e:
        logger.error(f"[{city.name}] Pipeline failed: {e}", exc_info=True)
        job.status = JobStatus.failed
        job.error_log = str(e)
        job.completed_at = datetime.utcnow()
        city.crawl_status = CrawlStatus.failed
        db.commit()
        raise


def _stage_reddit(city, job, db):
    from pipeline.crawlers.reddit import RedditCrawler
    logger.info(f"[{city.name}] Stage 1: Reddit crawling")
    crawler = RedditCrawler(city=city, job=job, db=db)
    count = crawler.run()
    job.total_raw_mentions = (job.total_raw_mentions or 0) + count
    job.sources_completed = {**job.sources_completed, "reddit": {"count": count, "completed_at": datetime.utcnow().isoformat()}}
    flag_modified(job, "sources_completed")
    db.commit()
    logger.info(f"[{city.name}] Stage 1 complete — {count} Reddit sources collected")


def _stage_blogs(city, job, db):
    from pipeline.crawlers.blogs import BlogCrawler
    logger.info(f"[{city.name}] Stage 2: Blog discovery and crawling")
    crawler = BlogCrawler(city=city, job=job, db=db)
    count = crawler.run()
    job.total_raw_mentions = (job.total_raw_mentions or 0) + count
    job.sources_completed = {**job.sources_completed, "blogs": {"count": count, "completed_at": datetime.utcnow().isoformat()}}
    flag_modified(job, "sources_completed")
    db.commit()
    logger.info(f"[{city.name}] Stage 2 complete — {count} blog sources collected")


def _stage_extraction(city, job, db):
    from pipeline.processors.extractor import EntityExtractor
    logger.info(f"[{city.name}] Stage 3: Entity extraction via Claude")
    extractor = EntityExtractor(city=city, job=job, db=db)
    count = extractor.run()
    job.total_entities_extracted = count
    job.sources_completed = {**job.sources_completed, "extraction": {"entities": count, "completed_at": datetime.utcnow().isoformat()}}
    flag_modified(job, "sources_completed")
    db.commit()
    logger.info(f"[{city.name}] Stage 3 complete — {count} entities extracted")


def _stage_scoring(city, job, db):
    from pipeline.processors.scorer import LocalScorer
    logger.info(f"[{city.name}] Stage 4: LocalScore calculation")
    scorer = LocalScorer(city=city, job=job, db=db)
    count = scorer.run()
    job.total_places_scored = count
    job.sources_completed = {**job.sources_completed, "scoring": {"places": count, "completed_at": datetime.utcnow().isoformat()}}
    flag_modified(job, "sources_completed")
    db.commit()
    logger.info(f"[{city.name}] Stage 4 complete — {count} places scored")


def _stage_enrichment(city, job, db):
    from pipeline.processors.enricher import Enricher
    logger.info(f"[{city.name}] Stage 5: Claude enrichment")
    enricher = Enricher(city=city, job=job, db=db)
    count = enricher.run()
    job.sources_completed = {**job.sources_completed, "enrichment": {"enriched": count, "completed_at": datetime.utcnow().isoformat()}}
    flag_modified(job, "sources_completed")
    db.commit()
    logger.info(f"[{city.name}] Stage 5 complete — {count} places enriched")
