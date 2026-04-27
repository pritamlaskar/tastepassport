import uuid
from datetime import datetime
from sqlalchemy import String, Integer, Float, Text, Boolean, DateTime, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import JSON
from database import Base
import enum


class PriceRange(str, enum.Enum):
    budget = "$"
    mid = "$$"
    upscale = "$$$"


class FeedbackType(str, enum.Enum):
    wrong_info = "wrong_info"
    closed = "closed"
    add_dish = "add_dish"
    general = "general"


class Restaurant(Base):
    __tablename__ = "restaurants"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    city_id: Mapped[str] = mapped_column(String, ForeignKey("cities.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    name_variants: Mapped[list] = mapped_column(JSON, default=list)
    neighborhood: Mapped[str] = mapped_column(String, nullable=True)
    cuisine_type: Mapped[str] = mapped_column(String, nullable=True)
    signature_dish: Mapped[str] = mapped_column(String, nullable=True)
    price_range: Mapped[PriceRange] = mapped_column(SAEnum(PriceRange), nullable=True)
    local_score: Mapped[int] = mapped_column(Integer, default=0)
    mention_count: Mapped[int] = mapped_column(Integer, default=0)
    reddit_mention_count: Mapped[int] = mapped_column(Integer, default=0)
    blog_mention_count: Mapped[int] = mapped_column(Integer, default=0)
    why_it_ranks: Mapped[str] = mapped_column(Text, nullable=True)
    score_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_mentioned_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    is_permanently_closed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    city = relationship("City", back_populates="restaurants")
    mentions = relationship("Mention", back_populates="restaurant", lazy="dynamic")
    feedback = relationship("Feedback", back_populates="restaurant", lazy="dynamic")


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    restaurant_id: Mapped[str] = mapped_column(String, ForeignKey("restaurants.id"), nullable=False)
    feedback_type: Mapped[FeedbackType] = mapped_column(SAEnum(FeedbackType), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    restaurant = relationship("Restaurant", back_populates="feedback")
