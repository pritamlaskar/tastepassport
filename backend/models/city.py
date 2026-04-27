import uuid
from datetime import datetime
from sqlalchemy import String, Integer, DateTime, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base
import enum


class CrawlStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    complete = "complete"
    failed = "failed"


class City(Base):
    __tablename__ = "cities"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    country: Mapped[str] = mapped_column(String, nullable=True)
    last_crawled_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    crawl_status: Mapped[CrawlStatus] = mapped_column(
        SAEnum(CrawlStatus), default=CrawlStatus.pending, nullable=False
    )
    total_places: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    restaurants = relationship("Restaurant", back_populates="city", lazy="dynamic")
    raw_sources = relationship("RawSource", back_populates="city", lazy="dynamic")
    crawl_jobs = relationship("CrawlJob", back_populates="city", lazy="dynamic")
