from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models.restaurant import Restaurant
from models.source import Mention, RawSource

router = APIRouter()


@router.get("/place/{place_id}")
def get_place(place_id: str, db: Session = Depends(get_db)):
    restaurant = db.query(Restaurant).filter(Restaurant.id == place_id).first()
    if not restaurant:
        raise HTTPException(status_code=404, detail="Place not found")

    from models.city import City
    city = db.query(City).filter(City.id == restaurant.city_id).first()

    mentions = (
        db.query(Mention)
        .filter(Mention.restaurant_id == restaurant.id)
        .order_by(Mention.specificity_score.desc())
        .limit(20)
        .all()
    )

    mention_details = []
    for m in mentions:
        source = db.query(RawSource).filter(RawSource.id == m.source_id).first()
        if source:
            mention_details.append({
                "source_type": source.source_type,
                "source_url": source.source_url,
                "mention_text": m.mention_text,
                "dish_mentioned": m.dish_mentioned,
                "sentiment": m.sentiment,
                "specificity_score": m.specificity_score,
                "upvotes": source.upvotes,
                "published_at": source.published_at.isoformat() if source.published_at else None,
            })

    return {
        "id": restaurant.id,
        "name": restaurant.name,
        "neighborhood": restaurant.neighborhood,
        "city": city.name if city else None,
        "cuisine_type": restaurant.cuisine_type,
        "signature_dish": restaurant.signature_dish,
        "price_range": restaurant.price_range,
        "local_score": restaurant.local_score,
        "why_it_ranks": restaurant.why_it_ranks,
        "score_breakdown": restaurant.score_breakdown or {},
        "mention_count": restaurant.mention_count,
        "mentions": mention_details,
        "first_seen_at": restaurant.first_seen_at.isoformat() if restaurant.first_seen_at else None,
        "last_mentioned_at": (
            restaurant.last_mentioned_at.isoformat() if restaurant.last_mentioned_at else None
        ),
    }
