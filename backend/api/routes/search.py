from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import or_
from database import get_db
from models.restaurant import Restaurant
from models.city import City

router = APIRouter()


@router.get("/search")
def search_places(
    q: str = Query(..., min_length=2, description="Search query"),
    city: str = Query(None, description="City slug to scope search"),
    limit: int = Query(20, le=50),
    offset: int = Query(0),
    db: Session = Depends(get_db),
):
    query = db.query(Restaurant).filter(
        Restaurant.is_permanently_closed == False,
        or_(
            Restaurant.name.ilike(f"%{q}%"),
            Restaurant.signature_dish.ilike(f"%{q}%"),
            Restaurant.cuisine_type.ilike(f"%{q}%"),
            Restaurant.neighborhood.ilike(f"%{q}%"),
        ),
    )

    if city:
        city_record = db.query(City).filter(City.slug == city).first()
        if city_record:
            query = query.filter(Restaurant.city_id == city_record.id)

    total = query.count()
    results = (
        query.order_by(Restaurant.local_score.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return {
        "query": q,
        "total": total,
        "results": [
            {
                "id": r.id,
                "name": r.name,
                "neighborhood": r.neighborhood,
                "cuisine_type": r.cuisine_type,
                "signature_dish": r.signature_dish,
                "price_range": r.price_range,
                "local_score": r.local_score,
                "why_it_ranks": r.why_it_ranks,
                "mention_count": r.mention_count,
                "score_breakdown": r.score_breakdown or {},
            }
            for r in results
        ],
    }
