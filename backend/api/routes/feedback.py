import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from database import get_db
from models.restaurant import Feedback, FeedbackType, Restaurant

router = APIRouter()


class FeedbackRequest(BaseModel):
    restaurant_id: str
    feedback_type: FeedbackType
    note: Optional[str] = None


@router.post("/feedback")
def submit_feedback(payload: FeedbackRequest, db: Session = Depends(get_db)):
    restaurant = db.query(Restaurant).filter(Restaurant.id == payload.restaurant_id).first()
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurant not found")

    feedback = Feedback(
        id=str(uuid.uuid4()),
        restaurant_id=payload.restaurant_id,
        feedback_type=payload.feedback_type,
        note=payload.note,
    )
    db.add(feedback)
    db.commit()
    db.refresh(feedback)

    return {"status": "received", "feedback_id": feedback.id}
