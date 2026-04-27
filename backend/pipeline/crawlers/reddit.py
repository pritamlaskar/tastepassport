"""
Reddit crawler — Stage 1.

Uses PRAW (Python Reddit API Wrapper) with OAuth for reliable, rate-limit-safe access.
Reddit's public JSON endpoint returns 403 for datacenter IPs; OAuth avoids this entirely.

Setup: create a free Reddit app at https://www.reddit.com/prefs/apps (select "script"),
then set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET in .env.

Searches 8 subreddits with 5 high-signal queries each.
Only collects posts within REDDIT_RECENCY_DAYS (default 90).
Only fetches comments for posts above REDDIT_COMMENT_FETCH_MIN_UPVOTES (default 50).
DB writes are batched every 20 rows to reduce round-trips.
"""
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

import praw
from sqlalchemy.orm import Session

from models.source import RawSource, SourceType
from utils.text import clean_text

logger = logging.getLogger(__name__)

BASE_SUBREDDITS = [
    "travel",
    "solotravel",
    "food",
    "backpacking",
    "digitalnomad",
    "EatCheapAndHealthy",
]

SEARCH_QUERIES = [
    "{city} food recommendations",
    "{city} where to eat",
    "{city} best local food",
    "{city} street food",
    "eating in {city}",
]

MIN_POST_CHARS = 100
MIN_COMMENT_CHARS = 80


class RedditCrawler:
    def __init__(self, city, job, db: Session):
        self.city = city
        self.job = job
        self.db = db
        self.city_name = city.name

        from config import get_settings
        settings = get_settings()
        self.min_upvotes = settings.min_reddit_upvotes
        self._comment_min_upvotes = settings.reddit_comment_fetch_min_upvotes

        recency_days = settings.reddit_recency_days
        self._oldest_allowed: Optional[datetime] = (
            datetime.now(timezone.utc) - timedelta(days=recency_days)
            if recency_days > 0 else None
        )

        self.collected = 0
        self._seen_urls: set = set()
        self._pending_commits = 0

        if not settings.reddit_client_id or not settings.reddit_client_secret:
            raise ValueError(
                "REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET must be set in .env.\n"
                "Create a free Reddit app at https://www.reddit.com/prefs/apps (select 'script')."
            )

        self._reddit = praw.Reddit(
            client_id=settings.reddit_client_id,
            client_secret=settings.reddit_client_secret,
            user_agent=settings.reddit_user_agent,
        )
        # Read-only mode — no login needed
        self._reddit.read_only = True

    def run(self) -> int:
        city_slug = self.city_name.lower().replace(" ", "")
        dynamic_subreddits = [city_slug]
        if self.city.country:
            dynamic_subreddits.append(self.city.country.lower().replace(" ", ""))

        all_subreddits = BASE_SUBREDDITS + dynamic_subreddits

        for subreddit_name in all_subreddits:
            queries = [q.format(city=self.city_name) for q in SEARCH_QUERIES]
            for query in queries:
                try:
                    self._search_subreddit(subreddit_name, query)
                except Exception as e:
                    logger.warning(f"[Reddit] Skipping r/{subreddit_name} '{query}': {e}")
                    continue

        self._flush(force=True)
        logger.info(
            f"[Reddit] {self.city_name} — collected {self.collected} sources "
            f"across {len(all_subreddits)} subreddits"
        )
        return self.collected

    # ------------------------------------------------------------------
    # Core search + collection
    # ------------------------------------------------------------------

    def _search_subreddit(self, subreddit_name: str, query: str):
        try:
            subreddit = self._reddit.subreddit(subreddit_name)
            results = list(
                subreddit.search(query, sort="top", time_filter="year", limit=15)
            )
        except Exception as e:
            logger.debug(f"[Reddit] r/{subreddit_name} search failed: {e}")
            return

        logger.debug(f"[Reddit] r/{subreddit_name} '{query}' → {len(results)} posts")

        for submission in results:
            upvotes = submission.score
            if upvotes < self.min_upvotes:
                continue

            # Recency gate
            if self._oldest_allowed:
                created = datetime.fromtimestamp(submission.created_utc, tz=timezone.utc)
                if created < self._oldest_allowed:
                    continue

            self._store_post(submission, subreddit_name)

            # Only fetch comments for high-signal posts
            if upvotes >= self._comment_min_upvotes:
                self._collect_top_comments(submission, subreddit_name)

    def _collect_top_comments(self, submission, subreddit_name: str):
        try:
            submission.comments.replace_more(limit=0)  # skip "load more" stubs
            top_comments = sorted(
                submission.comments.list(),
                key=lambda c: getattr(c, "score", 0),
                reverse=True,
            )[:20]
        except Exception as e:
            logger.debug(f"[Reddit] Failed to fetch comments for {submission.id}: {e}")
            return

        for comment in top_comments:
            body = getattr(comment, "body", "")
            if not body or body in ("[deleted]", "[removed]"):
                continue
            if len(body) < MIN_COMMENT_CHARS:
                continue
            self._store_comment(comment, subreddit_name, submission.id)

    # ------------------------------------------------------------------
    # Storage helpers
    # ------------------------------------------------------------------

    def _store_post(self, submission, subreddit: str):
        url = f"https://reddit.com{submission.permalink}"
        if url in self._seen_urls:
            return
        if self.db.query(RawSource).filter(RawSource.source_url == url).first():
            self._seen_urls.add(url)
            return

        title = submission.title or ""
        body = submission.selftext or ""
        combined = clean_text(f"{title}\n\n{body}")
        if len(combined) < MIN_POST_CHARS:
            return

        source = RawSource(
            id=str(uuid.uuid4()),
            city_id=self.city.id,
            crawl_job_id=self.job.id,
            source_type=SourceType.reddit,
            source_url=url,
            subreddit=subreddit,
            title=title[:500],
            full_text=combined,
            author=str(submission.author) if submission.author else None,
            upvotes=submission.score,
            upvote_ratio=submission.upvote_ratio,
            comment_count=submission.num_comments,
            published_at=datetime.fromtimestamp(submission.created_utc, tz=timezone.utc).replace(tzinfo=None),
            crawled_at=datetime.utcnow(),
            word_count=len(combined.split()),
        )
        self.db.add(source)
        self._seen_urls.add(url)
        self.collected += 1
        self._flush(force=False)
        logger.debug(
            f"[Reddit] Stored post [{submission.score} ups] r/{subreddit}: {title[:60]}"
        )

    def _store_comment(self, comment, subreddit: str, post_id: str):
        comment_id = comment.id
        url = f"https://reddit.com/r/{subreddit}/comments/{post_id}/_/{comment_id}/"
        if url in self._seen_urls:
            return
        if self.db.query(RawSource).filter(RawSource.source_url == url).first():
            self._seen_urls.add(url)
            return

        text = clean_text(comment.body)
        if len(text) < MIN_COMMENT_CHARS:
            return

        published_at = None
        if hasattr(comment, "created_utc"):
            published_at = datetime.fromtimestamp(comment.created_utc, tz=timezone.utc).replace(tzinfo=None)

        source = RawSource(
            id=str(uuid.uuid4()),
            city_id=self.city.id,
            crawl_job_id=self.job.id,
            source_type=SourceType.reddit,
            source_url=url,
            subreddit=subreddit,
            title=f"Comment in r/{subreddit} on post {post_id}",
            full_text=text,
            author=str(comment.author) if comment.author else None,
            upvotes=comment.score,
            upvote_ratio=None,
            comment_count=0,
            published_at=published_at,
            crawled_at=datetime.utcnow(),
            word_count=len(text.split()),
        )
        self.db.add(source)
        self._seen_urls.add(url)
        self.collected += 1
        self._flush(force=False)
        logger.debug(
            f"[Reddit] Stored comment [{comment.score} ups] r/{subreddit}/{post_id}/{comment_id}"
        )

    # ------------------------------------------------------------------
    # Batch commit helper
    # ------------------------------------------------------------------

    def _flush(self, force: bool = False):
        self._pending_commits += 1
        if force or self._pending_commits >= 20:
            self.db.commit()
            self._pending_commits = 0
