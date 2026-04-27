import uuid
from datetime import datetime
from sqlalchemy import String, Integer, Float, Text, DateTime, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base
import enum


class SourceType(str, enum.Enum):
    reddit = "reddit"
    blog = "blog"


class Sentiment(str, enum.Enum):
    positive = "positive"
    neutral = "neutral"
    negative = "negative"


class RawSource(Base):
    __tablename__ = "raw_sources"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    city_id: Mapped[str] = mapped_column(String, ForeignKey("cities.id"), nullable=False)
    crawl_job_id: Mapped[str] = mapped_column(String, ForeignKey("crawl_jobs.id"), nullable=True)
    source_type: Mapped[SourceType] = mapped_column(SAEnum(SourceType), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_domain: Mapped[str] = mapped_column(String, nullable=True)
    subreddit: Mapped[str] = mapped_column(String, nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=True)
    full_text: Mapped[str] = mapped_column(Text, nullable=True)
    author: Mapped[str] = mapped_column(String, nullable=True)
    upvotes: Mapped[int] = mapped_column(Integer, nullable=True)
    upvote_ratio: Mapped[float] = mapped_column(Float, nullable=True)
    comment_count: Mapped[int] = mapped_column(Integer, nullable=True)
    published_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    crawled_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    authenticity_score: Mapped[float] = mapped_column(Float, nullable=True)
    word_count: Mapped[int] = mapped_column(Integer, nullable=True)

    city = relationship("City", back_populates="raw_sources")
    mentions = relationship("Mention", back_populates="source", lazy="dynamic")


class Mention(Base):
    __tablename__ = "mentions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    restaurant_id: Mapped[str] = mapped_column(String, ForeignKey("restaurants.id"), nullable=False)
    source_id: Mapped[str] = mapped_column(String, ForeignKey("raw_sources.id"), nullable=False)
    city_id: Mapped[str] = mapped_column(String, ForeignKey("cities.id"), nullable=False)
    mention_text: Mapped[str] = mapped_column(Text, nullable=True)
    dish_mentioned: Mapped[str] = mapped_column(String, nullable=True)
    sentiment: Mapped[Sentiment] = mapped_column(
        SAEnum(Sentiment), default=Sentiment.positive, nullable=False
    )
    specificity_score: Mapped[float] = mapped_column(Float, nullable=True)
    extracted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    restaurant = relationship("Restaurant", back_populates="mentions")
    source = relationship("RawSource", back_populates="mentions")
