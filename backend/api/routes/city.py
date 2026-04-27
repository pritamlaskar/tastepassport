from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models.city import City
from models.restaurant import Restaurant
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/cities")
def list_cities(db: Session = Depends(get_db)):
    cities = db.query(City).filter(City.crawl_status == "complete").all()
    return {
        "cities": [
            {
                "name": c.name,
                "slug": c.slug,
                "total_places": c.total_places,
                "last_updated": c.last_crawled_at.isoformat() if c.last_crawled_at else None,
            }
            for c in cities
        ]
    }


@router.get("/city/{slug}/status")
def city_status(slug: str, db: Session = Depends(get_db)):
    city = db.query(City).filter(City.slug == slug).first()
    if not city:
        raise HTTPException(status_code=404, detail=f"City '{slug}' not found in index")

    from models.crawl_job import CrawlJob
    job = (
        db.query(CrawlJob)
        .filter(CrawlJob.city_id == city.id)
        .order_by(CrawlJob.started_at.desc())
        .first()
    )

    stages_complete = list(job.sources_completed.keys()) if job and job.sources_completed else []
    all_stages = ["reddit", "blogs", "extraction", "scoring", "enrichment"]
    stages_pending = [s for s in all_stages if s not in stages_complete]

    return {
        "city": city.name,
        "status": city.crawl_status,
        "stages_complete": stages_complete,
        "stages_pending": stages_pending,
        "started_at": job.started_at.isoformat() if job and job.started_at else None,
        "estimated_completion": None,
    }


@router.get("/city/{slug}")
def get_city(
    slug: str,
    meal_type: str = "all",
    budget: str = "all",
    cuisine: str = "all",
    limit: int = 20,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    from config import get_settings
    from datetime import datetime, timedelta

    settings = get_settings()
    limit = min(limit, 50)

    city = db.query(City).filter(City.slug == slug).first()
    stale_threshold = datetime.utcnow() - timedelta(days=settings.crawl_recrawl_days)

    needs_crawl = (
        city is None
        or city.last_crawled_at is None
        or city.last_crawled_at < stale_threshold
    )

    if needs_crawl:
        if city is None:
            city = _create_city(slug, db)
        _trigger_pipeline(city, db)

    if city.crawl_status in ("pending", "running"):
        return {
            "city": city.name,
            "slug": city.slug,
            "status": city.crawl_status,
            "message": "Pipeline is running. Check /api/city/{slug}/status for progress.",
            "results": [],
        }

    query = (
        db.query(Restaurant)
        .filter(
            Restaurant.city_id == city.id,
            Restaurant.local_score >= settings.min_local_score,
            Restaurant.mention_count >= settings.min_mentions,
            Restaurant.is_permanently_closed == False,
        )
    )

    if budget != "all":
        query = query.filter(Restaurant.price_range == budget)
    if cuisine != "all":
        query = query.filter(Restaurant.cuisine_type.ilike(f"%{cuisine}%"))

    total = query.count()
    restaurants = (
        query.order_by(Restaurant.local_score.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return {
        "city": city.name,
        "slug": city.slug,
        "status": city.crawl_status,
        "last_updated": city.last_crawled_at.isoformat() if city.last_crawled_at else None,
        "total_places": total,
        "results": [_format_restaurant(r, db) for r in restaurants],
    }


def _create_city(slug: str, db: Session) -> City:
    name = slug.replace("-", " ").title()
    city = City(name=name, slug=slug)
    db.add(city)
    db.commit()
    db.refresh(city)
    logger.info(f"Created new city record: {name} ({slug})")
    return city


def _trigger_pipeline(city: City, db: Session):
    from models.city import CrawlStatus
    if city.crawl_status == "running":
        logger.info(f"Pipeline already running for {city.name}, skipping trigger")
        return

    city.crawl_status = CrawlStatus.running
    db.commit()

    try:
        from tasks.crawl_tasks import run_city_pipeline
        run_city_pipeline.delay(city.id)
        logger.info(f"Triggered pipeline for {city.name}")
    except Exception as e:
        logger.error(f"Failed to trigger pipeline for {city.name}: {e}")
        city.crawl_status = CrawlStatus.failed
        db.commit()


def _format_restaurant(r: Restaurant, db: Session) -> dict:
    from models.source import RawSource, SourceType, Mention

    sources_used = []
    if r.reddit_mention_count > 0:
        sources_used.append("reddit")
    if r.blog_mention_count > 0:
        sources_used.append("blog")

    # Surface the highest-upvote Reddit thread so every result links back to Reddit
    top_reddit_url = None
    top_reddit_upvotes = None
    top_reddit_subreddit = None
    if r.reddit_mention_count > 0:
        mention = (
            db.query(Mention)
            .filter(Mention.restaurant_id == r.id)
            .first()
        )
        if mention:
            source = (
                db.query(RawSource)
                .filter(
                    RawSource.id == mention.source_id,
                    RawSource.source_type == SourceType.reddit,
                )
                .order_by(RawSource.upvotes.desc())
                .first()
            )
            if source:
                top_reddit_url = source.source_url
                top_reddit_upvotes = source.upvotes
                top_reddit_subreddit = source.subreddit

    return {
        "id": r.id,
        "name": r.name,
        "neighborhood": r.neighborhood,
        "cuisine_type": r.cuisine_type,
        "signature_dish": r.signature_dish,
        "price_range": r.price_range,
        "local_score": r.local_score,
        "why_it_ranks": r.why_it_ranks,
        "mention_count": r.mention_count,
        "reddit_mention_count": r.reddit_mention_count,
        "blog_mention_count": r.blog_mention_count,
        "sources": sources_used,
        "top_reddit_source": {
            "url": top_reddit_url,
            "upvotes": top_reddit_upvotes,
            "subreddit": top_reddit_subreddit,
        } if top_reddit_url else None,
        "last_mentioned_at": r.last_mentioned_at.isoformat() if r.last_mentioned_at else None,
        "score_breakdown": r.score_breakdown or {},
    }
