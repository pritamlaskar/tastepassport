from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    anthropic_api_key: str = ""
    database_url: str = "postgresql://tastepassport:password@postgres:5432/tastepassport"
    redis_url: str = "redis://redis:6379/0"

    crawl_recrawl_days: int = 7
    reddit_delay_seconds: float = 2.0
    max_blog_sources_per_city: int = 25
    min_reddit_upvotes: int = 10
    min_local_score: int = 25
    min_mentions: int = 2

    # Only index Reddit posts newer than this many days (0 = no limit)
    reddit_recency_days: int = 90
    # Only fetch comments for posts above this upvote count
    reddit_comment_fetch_min_upvotes: int = 50

    # Reddit OAuth app credentials (required for Reddit crawl)
    # Create a free app at https://www.reddit.com/prefs/apps (select "script")
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    # Format: "platform:app_name:v1.0 (by /u/your_reddit_username)"
    reddit_user_agent: str = "script:tastepassport:v1.0 (by /u/tastepassport_bot)"

    environment: str = "development"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
