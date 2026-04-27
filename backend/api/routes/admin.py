"""
Admin / development endpoints — not exposed in production docs.

POST /api/admin/reddit/{slug}         — trigger reddit crawl for a city
POST /api/admin/blogs/{slug}          — trigger blog discovery + scrape for a city
POST /api/admin/extract/{slug}        — trigger entity extraction (Claude) for a city
POST /api/admin/score/{slug}          — run LocalScore algorithm for a city
POST /api/admin/enrich/{slug}         — run Claude enrichment for top restaurants
GET  /api/admin/scores/{slug}         — list scored restaurants for a city
GET  /api/admin/sources/{slug}        — list raw sources collected for a city
GET  /api/admin/sources/{slug}/{id}   — view full text of one source
GET  /api/admin/mentions/{slug}       — list extracted mentions for a city
"""
import uuid
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models.city import City, CrawlStatus
from models.crawl_job import CrawlJob, JobStatus
from models.source import RawSource

router = APIRouter(prefix="/api/admin", tags=["admin"])
logger = logging.getLogger(__name__)


@router.post("/reddit/{slug}")
def trigger_reddit_crawl(slug: str, db: Session = Depends(get_db)):
    """
    Trigger the Reddit crawl stage only for a city.
    Creates the city and a crawl job if they don't exist.
    Runs synchronously so you can see results immediately in dev.
    """
    city = db.query(City).filter(City.slug == slug).first()
    if not city:
        name = slug.replace("-", " ").title()
        city = City(
            id=str(uuid.uuid4()),
            name=name,
            slug=slug,
            crawl_status=CrawlStatus.pending,
            created_at=datetime.utcnow(),
        )
        db.add(city)
        db.commit()
        db.refresh(city)
        logger.info(f"[Admin] Created city: {name}")

    job = CrawlJob(
        id=str(uuid.uuid4()),
        city_id=city.id,
        status=JobStatus.running,
        sources_completed={},
        started_at=datetime.utcnow(),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    logger.info(f"[Admin] Running Reddit crawl for {city.name} (job {job.id})")

    try:
        from pipeline.crawlers.reddit import RedditCrawler
        crawler = RedditCrawler(city=city, job=job, db=db)
        count = crawler.run()

        job.status = JobStatus.complete
        job.sources_completed = {"reddit": {"count": count}}
        job.completed_at = datetime.utcnow()
        job.total_raw_mentions = count
        db.commit()

        return {
            "status": "complete",
            "city": city.name,
            "job_id": job.id,
            "sources_collected": count,
            "message": f"Use GET /api/admin/sources/{slug} to inspect results",
        }

    except Exception as e:
        job.status = JobStatus.failed
        job.error_log = str(e)
        job.completed_at = datetime.utcnow()
        db.commit()
        logger.error(f"[Admin] Reddit crawl failed for {city.name}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/blogs/{slug}")
def trigger_blog_crawl(slug: str, db: Session = Depends(get_db)):
    """
    Trigger the blog discovery + scrape stage only for a city.
    Runs synchronously. Can take 2–5 minutes depending on how many blogs are found.
    """
    city = db.query(City).filter(City.slug == slug).first()
    if not city:
        name = slug.replace("-", " ").title()
        city = City(
            id=str(uuid.uuid4()),
            name=name,
            slug=slug,
            crawl_status=CrawlStatus.pending,
            created_at=datetime.utcnow(),
        )
        db.add(city)
        db.commit()
        db.refresh(city)

    job = CrawlJob(
        id=str(uuid.uuid4()),
        city_id=city.id,
        status=JobStatus.running,
        sources_completed={},
        started_at=datetime.utcnow(),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    logger.info(f"[Admin] Running blog crawl for {city.name} (job {job.id})")

    try:
        from pipeline.crawlers.blogs import BlogCrawler
        crawler = BlogCrawler(city=city, job=job, db=db)
        count = crawler.run()

        job.status = JobStatus.complete
        job.sources_completed = {"blogs": {"count": count}}
        job.completed_at = datetime.utcnow()
        job.total_raw_mentions = count
        db.commit()

        return {
            "status": "complete",
            "city": city.name,
            "job_id": job.id,
            "blog_sources_collected": count,
            "message": f"Use GET /api/admin/sources/{slug}?source_type=blog to inspect results",
        }

    except Exception as e:
        job.status = JobStatus.failed
        job.error_log = str(e)
        job.completed_at = datetime.utcnow()
        db.commit()
        logger.error(f"[Admin] Blog crawl failed for {city.name}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/extract/{slug}")
def trigger_extraction(slug: str, job_id: str = None, db: Session = Depends(get_db)):
    """
    Run entity extraction (Claude API) on all raw sources for a city.
    Optionally scope to a specific crawl job via ?job_id=...
    Runs synchronously — can take several minutes for a full city.
    """
    city = db.query(City).filter(City.slug == slug).first()
    if not city:
        raise HTTPException(status_code=404, detail=f"City '{slug}' not found")

    # Reuse most recent job or create a stub job for this standalone run
    from models.source import RawSource
    if job_id:
        job = db.query(CrawlJob).filter(CrawlJob.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    else:
        job = (
            db.query(CrawlJob)
            .filter(CrawlJob.city_id == city.id)
            .order_by(CrawlJob.started_at.desc())
            .first()
        )
        if not job:
            job = CrawlJob(
                id=str(uuid.uuid4()),
                city_id=city.id,
                status=JobStatus.running,
                sources_completed={},
                started_at=datetime.utcnow(),
            )
            db.add(job)
            db.commit()
            db.refresh(job)

    source_count = (
        db.query(RawSource)
        .filter(
            RawSource.city_id == city.id,
            RawSource.crawl_job_id == job.id,
        )
        .count()
    )
    if source_count == 0:
        return {
            "status": "skipped",
            "city": city.name,
            "message": "No sources found for this job. Run /reddit and /blogs first.",
        }

    logger.info(
        f"[Admin] Running extraction for {city.name} "
        f"({source_count} sources, job {job.id})"
    )

    try:
        from pipeline.processors.extractor import EntityExtractor
        extractor = EntityExtractor(city=city, job=job, db=db)
        total = extractor.run()

        return {
            "status": "complete",
            "city": city.name,
            "job_id": job.id,
            "sources_processed": extractor.sources_processed,
            "sources_skipped": extractor.sources_skipped,
            "sources_failed": extractor.sources_failed,
            "mentions_extracted": total,
            "tokens_used": extractor.total_input_tokens + extractor.total_output_tokens,
            "message": f"Use GET /api/admin/mentions/{slug} to inspect results",
        }

    except Exception as e:
        logger.error(f"[Admin] Extraction failed for {city.name}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/score/{slug}")
def trigger_scoring(slug: str, db: Session = Depends(get_db)):
    """
    Run the LocalScore algorithm for all restaurants in a city.
    Includes deduplication pass. Updates local_score and score_breakdown on each record.
    """
    city = db.query(City).filter(City.slug == slug).first()
    if not city:
        raise HTTPException(status_code=404, detail=f"City '{slug}' not found")

    job = (
        db.query(CrawlJob)
        .filter(CrawlJob.city_id == city.id)
        .order_by(CrawlJob.started_at.desc())
        .first()
    )
    if not job:
        job = CrawlJob(
            id=str(uuid.uuid4()),
            city_id=city.id,
            status=JobStatus.running,
            sources_completed={},
            started_at=datetime.utcnow(),
        )
        db.add(job)
        db.commit()
        db.refresh(job)

    from models.restaurant import Restaurant
    restaurant_count = (
        db.query(Restaurant).filter(Restaurant.city_id == city.id).count()
    )
    if restaurant_count == 0:
        return {
            "status": "skipped",
            "city": city.name,
            "message": "No restaurants found. Run /extract first.",
        }

    logger.info(
        f"[Admin] Running LocalScore for {city.name} "
        f"({restaurant_count} restaurants)"
    )

    try:
        from pipeline.processors.scorer import LocalScorer
        from config import get_settings
        settings = get_settings()

        scorer = LocalScorer(city=city, job=job, db=db)
        scored = scorer.run()

        qualifying = (
            db.query(Restaurant)
            .filter(
                Restaurant.city_id == city.id,
                Restaurant.local_score >= settings.min_local_score,
                Restaurant.mention_count >= settings.min_mentions,
            )
            .count()
        )

        return {
            "status": "complete",
            "city": city.name,
            "restaurants_scored": scored,
            "restaurants_merged": scorer.merged_count,
            "qualifying_for_api": qualifying,
            "thresholds": {
                "min_local_score": settings.min_local_score,
                "min_mentions": settings.min_mentions,
            },
            "message": f"Use GET /api/admin/scores/{slug} to inspect results",
        }

    except Exception as e:
        logger.error(f"[Admin] Scoring failed for {city.name}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/enrich/{slug}")
def trigger_enrichment(slug: str, force: bool = False, db: Session = Depends(get_db)):
    """
    Run Claude enrichment for all restaurants with LocalScore >= 40.
    Set ?force=true to re-enrich restaurants that already have data.
    """
    city = db.query(City).filter(City.slug == slug).first()
    if not city:
        raise HTTPException(status_code=404, detail=f"City '{slug}' not found")

    job = (
        db.query(CrawlJob)
        .filter(CrawlJob.city_id == city.id)
        .order_by(CrawlJob.started_at.desc())
        .first()
    )
    if not job:
        job = CrawlJob(
            id=str(uuid.uuid4()),
            city_id=city.id,
            status=JobStatus.running,
            sources_completed={},
            started_at=datetime.utcnow(),
        )
        db.add(job)
        db.commit()
        db.refresh(job)

    from models.restaurant import Restaurant
    eligible = (
        db.query(Restaurant)
        .filter(Restaurant.city_id == city.id, Restaurant.local_score >= 40)
        .count()
    )
    if eligible == 0:
        return {
            "status": "skipped",
            "city": city.name,
            "message": "No restaurants with score >= 40. Run /score first.",
        }

    logger.info(
        f"[Admin] Running enrichment for {city.name} "
        f"({eligible} eligible restaurants, force={force})"
    )

    try:
        from pipeline.processors.enricher import Enricher
        enricher = Enricher(city=city, job=job, db=db)
        count = enricher.run(force=force)

        return {
            "status": "complete",
            "city": city.name,
            "enriched": count,
            "skipped": enricher.skipped_count,
            "failed": enricher.failed_count,
            "tokens_used": enricher.total_input_tokens + enricher.total_output_tokens,
            "message": f"Use GET /api/city/{slug} to see final ranked results",
        }

    except Exception as e:
        logger.error(f"[Admin] Enrichment failed for {city.name}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/scores/{slug}")
def list_scores(
    slug: str,
    min_score: int = 0,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """List restaurants with their LocalScore and breakdown — audit the algorithm."""
    from models.restaurant import Restaurant

    city = db.query(City).filter(City.slug == slug).first()
    if not city:
        raise HTTPException(status_code=404, detail=f"City '{slug}' not found")

    total = (
        db.query(Restaurant)
        .filter(Restaurant.city_id == city.id, Restaurant.local_score >= min_score)
        .count()
    )
    restaurants = (
        db.query(Restaurant)
        .filter(Restaurant.city_id == city.id, Restaurant.local_score >= min_score)
        .order_by(Restaurant.local_score.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return {
        "city": city.name,
        "total": total,
        "results": [
            {
                "id": r.id,
                "name": r.name,
                "local_score": r.local_score,
                "mention_count": r.mention_count,
                "reddit_mentions": r.reddit_mention_count,
                "blog_mentions": r.blog_mention_count,
                "score_breakdown": r.score_breakdown,
                "name_variants": r.name_variants,
                "neighborhood": r.neighborhood,
                "last_mentioned_at": (
                    r.last_mentioned_at.isoformat() if r.last_mentioned_at else None
                ),
            }
            for r in restaurants
        ],
    }


@router.get("/mentions/{slug}")
def list_mentions(
    slug: str,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """List extracted mentions for a city — shows what Claude found."""
    from models.source import Mention, RawSource
    from models.restaurant import Restaurant

    city = db.query(City).filter(City.slug == slug).first()
    if not city:
        raise HTTPException(status_code=404, detail=f"City '{slug}' not found")

    total = db.query(Mention).filter(Mention.city_id == city.id).count()
    mentions = (
        db.query(Mention)
        .filter(Mention.city_id == city.id)
        .order_by(Mention.specificity_score.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    results = []
    for m in mentions:
        r = db.query(Restaurant).filter(Restaurant.id == m.restaurant_id).first()
        s = db.query(RawSource).filter(RawSource.id == m.source_id).first()
        results.append({
            "mention_id": m.id,
            "restaurant_name": r.name if r else None,
            "source_type": s.source_type if s else None,
            "source_url": s.source_url if s else None,
            "dish_mentioned": m.dish_mentioned,
            "sentiment": m.sentiment,
            "specificity_score": m.specificity_score,
            "mention_text": m.mention_text,
        })

    return {
        "city": city.name,
        "total_mentions": total,
        "results": results,
    }


@router.get("/sources/{slug}")
def list_sources(
    slug: str,
    source_type: str = "all",
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """List raw sources collected for a city with summary stats."""
    city = db.query(City).filter(City.slug == slug).first()
    if not city:
        raise HTTPException(status_code=404, detail=f"City '{slug}' not found")

    query = db.query(RawSource).filter(RawSource.city_id == city.id)
    if source_type != "all":
        query = query.filter(RawSource.source_type == source_type)

    total = query.count()
    sources = (
        query.order_by(RawSource.crawled_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    reddit_count = db.query(RawSource).filter(
        RawSource.city_id == city.id, RawSource.source_type == "reddit"
    ).count()
    blog_count = db.query(RawSource).filter(
        RawSource.city_id == city.id, RawSource.source_type == "blog"
    ).count()

    return {
        "city": city.name,
        "total_sources": total,
        "reddit_sources": reddit_count,
        "blog_sources": blog_count,
        "results": [
            {
                "id": s.id,
                "source_type": s.source_type,
                "source_url": s.source_url,
                "subreddit": s.subreddit,
                "title": s.title,
                "author": s.author,
                "upvotes": s.upvotes,
                "word_count": s.word_count,
                "authenticity_score": s.authenticity_score,
                "published_at": s.published_at.isoformat() if s.published_at else None,
                "crawled_at": s.crawled_at.isoformat() if s.crawled_at else None,
            }
            for s in sources
        ],
    }


@router.get("/sources/{slug}/{source_id}")
def get_source_text(slug: str, source_id: str, db: Session = Depends(get_db)):
    """Return the full text of a single raw source — useful for verifying extraction input."""
    city = db.query(City).filter(City.slug == slug).first()
    if not city:
        raise HTTPException(status_code=404, detail=f"City '{slug}' not found")

    source = db.query(RawSource).filter(
        RawSource.id == source_id,
        RawSource.city_id == city.id,
    ).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    return {
        "id": source.id,
        "source_type": source.source_type,
        "source_url": source.source_url,
        "subreddit": source.subreddit,
        "title": source.title,
        "author": source.author,
        "upvotes": source.upvotes,
        "upvote_ratio": source.upvote_ratio,
        "word_count": source.word_count,
        "published_at": source.published_at.isoformat() if source.published_at else None,
        "full_text": source.full_text,
    }
