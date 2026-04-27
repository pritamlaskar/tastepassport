"""Initial schema — all tables

Revision ID: 0001
Revises:
Create Date: 2026-04-26
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cities",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("country", sa.String(), nullable=True),
        sa.Column("last_crawled_at", sa.DateTime(), nullable=True),
        sa.Column(
            "crawl_status",
            sa.Enum("pending", "running", "complete", "failed", name="crawlstatus"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("total_places", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("slug"),
    )

    op.create_table(
        "crawl_jobs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("city_id", sa.String(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "running", "complete", "failed", name="jobstatus"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("sources_completed", JSON(), nullable=True),
        sa.Column("total_raw_mentions", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("total_entities_extracted", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("total_places_scored", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("error_log", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["city_id"], ["cities.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "restaurants",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("city_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("name_variants", JSON(), nullable=True),
        sa.Column("neighborhood", sa.String(), nullable=True),
        sa.Column("cuisine_type", sa.String(), nullable=True),
        sa.Column("signature_dish", sa.String(), nullable=True),
        sa.Column(
            "price_range",
            sa.Enum("$", "$$", "$$$", name="pricerange"),
            nullable=True,
        ),
        sa.Column("local_score", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("mention_count", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("reddit_mention_count", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("blog_mention_count", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("why_it_ranks", sa.Text(), nullable=True),
        sa.Column("score_breakdown", JSON(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(), nullable=True),
        sa.Column("last_mentioned_at", sa.DateTime(), nullable=True),
        sa.Column("is_verified", sa.Boolean(), nullable=True, server_default="false"),
        sa.Column("is_permanently_closed", sa.Boolean(), nullable=True, server_default="false"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["city_id"], ["cities.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "raw_sources",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("city_id", sa.String(), nullable=False),
        sa.Column("crawl_job_id", sa.String(), nullable=True),
        sa.Column(
            "source_type",
            sa.Enum("reddit", "blog", name="sourcetype"),
            nullable=False,
        ),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_domain", sa.String(), nullable=True),
        sa.Column("subreddit", sa.String(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("full_text", sa.Text(), nullable=True),
        sa.Column("author", sa.String(), nullable=True),
        sa.Column("upvotes", sa.Integer(), nullable=True),
        sa.Column("upvote_ratio", sa.Float(), nullable=True),
        sa.Column("comment_count", sa.Integer(), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("crawled_at", sa.DateTime(), nullable=True),
        sa.Column("authenticity_score", sa.Float(), nullable=True),
        sa.Column("word_count", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["city_id"], ["cities.id"]),
        sa.ForeignKeyConstraint(["crawl_job_id"], ["crawl_jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "mentions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("restaurant_id", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("city_id", sa.String(), nullable=False),
        sa.Column("mention_text", sa.Text(), nullable=True),
        sa.Column("dish_mentioned", sa.String(), nullable=True),
        sa.Column(
            "sentiment",
            sa.Enum("positive", "neutral", "negative", name="sentiment"),
            nullable=False,
            server_default="positive",
        ),
        sa.Column("specificity_score", sa.Float(), nullable=True),
        sa.Column("extracted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["city_id"], ["cities.id"]),
        sa.ForeignKeyConstraint(["restaurant_id"], ["restaurants.id"]),
        sa.ForeignKeyConstraint(["source_id"], ["raw_sources.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "feedback",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("restaurant_id", sa.String(), nullable=False),
        sa.Column(
            "feedback_type",
            sa.Enum("wrong_info", "closed", "add_dish", "general", name="feedbacktype"),
            nullable=False,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["restaurant_id"], ["restaurants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # Indexes for common query patterns
    op.create_index("ix_restaurants_city_id", "restaurants", ["city_id"])
    op.create_index("ix_restaurants_local_score", "restaurants", ["local_score"])
    op.create_index("ix_raw_sources_city_id", "raw_sources", ["city_id"])
    op.create_index("ix_mentions_restaurant_id", "mentions", ["restaurant_id"])
    op.create_index("ix_mentions_source_id", "mentions", ["source_id"])
    op.create_index("ix_crawl_jobs_city_id", "crawl_jobs", ["city_id"])


def downgrade() -> None:
    op.drop_table("feedback")
    op.drop_table("mentions")
    op.drop_table("raw_sources")
    op.drop_table("restaurants")
    op.drop_table("crawl_jobs")
    op.drop_table("cities")

    op.execute("DROP TYPE IF EXISTS feedbacktype")
    op.execute("DROP TYPE IF EXISTS sentiment")
    op.execute("DROP TYPE IF EXISTS sourcetype")
    op.execute("DROP TYPE IF EXISTS pricerange")
    op.execute("DROP TYPE IF EXISTS jobstatus")
    op.execute("DROP TYPE IF EXISTS crawlstatus")
