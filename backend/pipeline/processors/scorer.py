"""
LocalScore algorithm — Stage 4.
TastePassport's core ranking signal. Score range: 0–100.

Every scoring decision is logged. No scores are ever hardcoded or manually boosted.
Every result must earn its rank through the algorithm.

Runs in two passes:
  Pass 1 — Deduplication: merge restaurants that are the same place
            (uses fuzzy matching on top of what normalization.py already did live)
  Pass 2 — Scoring: calculate LocalScore for each surviving restaurant

Minimum thresholds (from config) filter results before they appear in the API:
  - mention_count >= MIN_MENTIONS  (default 2)
  - local_score   >= MIN_LOCAL_SCORE (default 25)
"""
import re
import logging
from datetime import datetime, timedelta
from typing import Optional

from fuzzywuzzy import fuzz
from sqlalchemy.orm import Session

from models.restaurant import Restaurant
from models.source import RawSource, Mention, SourceType, Sentiment

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
#  Known international chains — presence here gives a -15 penalty
# ------------------------------------------------------------------ #
KNOWN_CHAINS = frozenset({
    "mcdonald's", "mcdonalds", "mcdonald", "kfc", "starbucks", "subway",
    "pizza hut", "pizzahut", "dominos", "domino's", "burger king", "burgerking",
    "wendy's", "wendys", "taco bell", "tacobell", "popeyes", "popeye's",
    "chick-fil-a", "chickfila", "dunkin", "dunkin donuts", "dunkin' donuts",
    "krispy kreme", "five guys", "fiveguys", "shake shack", "shakeshack",
    "chipotle", "panda express", "pandaexpress", "hardee's", "hardees",
    "carl's jr", "carls jr", "white castle", "whitecastle", "dairy queen",
    "dairyqueen", "tim hortons", "timhortons", "costa coffee", "costacoffee",
    "baskin robbins", "baskinrobbins", "31 ice cream", "papa john's",
    "papajohns", "little caesars", "littlecaesars", "in-n-out", "innout",
    "sonic", "arbys", "arby's", "jack in the box", "jackinthebox",
    "café coffee day", "ccd", "barista coffee",
})


class LocalScorer:
    def __init__(self, city, job, db: Session):
        self.city = city
        self.job = job
        self.db = db
        self.merged_count = 0
        self.scored_count = 0

    def run(self) -> int:
        restaurants = (
            self.db.query(Restaurant)
            .filter(Restaurant.city_id == self.city.id)
            .all()
        )
        logger.info(
            f"[Scorer] {self.city.name} — "
            f"{len(restaurants)} restaurants before dedup"
        )

        restaurants = self._deduplicate(restaurants)
        logger.info(
            f"[Scorer] {self.city.name} — "
            f"{len(restaurants)} after dedup ({self.merged_count} merged)"
        )

        for restaurant in restaurants:
            try:
                self._score(restaurant)
                self.scored_count += 1
            except Exception as e:
                logger.warning(
                    f"[Scorer] Failed to score '{restaurant.name}': {e}"
                )

        self.db.commit()
        logger.info(
            f"[Scorer] {self.city.name} — scored {self.scored_count} restaurants"
        )
        return self.scored_count

    # ------------------------------------------------------------------ #
    #  Pass 1 — Deduplication
    # ------------------------------------------------------------------ #

    def _deduplicate(self, restaurants: list) -> list:
        """
        Second-pass dedup after normalization.py's live dedup.
        Catches cases where similar names slipped through during concurrent extraction.
        Merges the lower-id record into the higher-id one (arbitrary but consistent).
        """
        merged_ids: set = set()

        for i, r1 in enumerate(restaurants):
            if r1.id in merged_ids:
                continue
            for j, r2 in enumerate(restaurants):
                if i >= j or r2.id in merged_ids:
                    continue

                similarity = fuzz.token_sort_ratio(
                    r1.name.lower(), r2.name.lower()
                )
                if similarity >= 86:
                    # Merge r2 into r1 (keep r1 — it was created first)
                    self._merge(target=r1, source=r2)
                    merged_ids.add(r2.id)
                    logger.info(
                        f"[Scorer] Merged '{r2.name}' → '{r1.name}' "
                        f"(similarity={similarity})"
                    )

        # Delete merged ghosts
        for rid in merged_ids:
            ghost = self.db.query(Restaurant).filter(Restaurant.id == rid).first()
            if ghost:
                self.db.delete(ghost)
        self.db.commit()
        self.merged_count = len(merged_ids)

        return [r for r in restaurants if r.id not in merged_ids]

    def _merge(self, target: Restaurant, source: Restaurant):
        """Reassign all mentions from source → target, carry over name variants."""
        self.db.query(Mention).filter(Mention.restaurant_id == source.id).update(
            {"restaurant_id": target.id}, synchronize_session=False
        )
        variants = list(target.name_variants or [])
        for v in [source.name] + list(source.name_variants or []):
            if v and v not in variants and v != target.name:
                variants.append(v)
        target.name_variants = variants
        self.db.flush()

    # ------------------------------------------------------------------ #
    #  Pass 2 — Scoring
    # ------------------------------------------------------------------ #

    def _score(self, restaurant: Restaurant):
        mentions = (
            self.db.query(Mention)
            .filter(Mention.restaurant_id == restaurant.id)
            .all()
        )
        if not mentions:
            restaurant.local_score = 0
            restaurant.mention_count = 0
            return

        source_ids = list({m.source_id for m in mentions})
        sources = (
            self.db.query(RawSource)
            .filter(RawSource.id.in_(source_ids))
            .all()
        )
        source_map = {s.id: s for s in sources}

        reddit_sources = [s for s in sources if s.source_type == SourceType.reddit]
        blog_sources   = [s for s in sources if s.source_type == SourceType.blog]

        breakdown = {
            "reddit_signals":       self._reddit_signals(mentions, source_map, reddit_sources),
            "blog_signals":         self._blog_signals(mentions, source_map, blog_sources),
            "cross_source_signals": self._cross_signals(reddit_sources, blog_sources, sources),
            "recency_signals":      self._recency_signals(sources, restaurant),
            "negative_signals":     self._negative_signals(restaurant, mentions, sources),
        }

        raw_score = sum(breakdown.values())
        clamped   = max(0, min(100, raw_score))

        # Update mention counts
        restaurant.mention_count        = len(mentions)
        restaurant.reddit_mention_count = sum(
            1 for m in mentions
            if source_map.get(m.source_id) and source_map[m.source_id].source_type == SourceType.reddit
        )
        restaurant.blog_mention_count   = sum(
            1 for m in mentions
            if source_map.get(m.source_id) and source_map[m.source_id].source_type == SourceType.blog
        )
        restaurant.local_score          = clamped
        restaurant.score_breakdown      = breakdown

        logger.debug(
            f"[Scorer] '{restaurant.name}' → score={clamped} | "
            + " | ".join(f"{k}={v:+d}" for k, v in breakdown.items())
        )

    # ------------------------------------------------------------------ #
    #  Signal calculators
    # ------------------------------------------------------------------ #

    def _reddit_signals(
        self, mentions: list, source_map: dict, reddit_sources: list
    ) -> int:
        points = 0

        # --- Upvote tiers (best single thread wins each tier) ---
        max_upvotes = max((s.upvotes or 0 for s in reddit_sources), default=0)
        if max_upvotes >= 500:
            points += 20
        elif max_upvotes >= 100:
            points += 15
        elif max_upvotes >= 50:
            points += 10
        elif max_upvotes >= 10:
            points += 5

        # --- Mentioned in a top-level comment (URL contains /_/) ---
        comment_sources = [
            s for s in reddit_sources if "/_/" in (s.source_url or "")
        ]
        if comment_sources:
            points += 8

        # --- Multiple distinct Reddit authors ---
        reddit_authors = {
            source_map[m.source_id].author
            for m in mentions
            if m.source_id in source_map
            and source_map[m.source_id].source_type == SourceType.reddit
            and source_map[m.source_id].author
        }
        if len(reddit_authors) >= 2:
            points += 5

        # --- From city's own local subreddit ---
        city_slug = self.city.name.lower().replace(" ", "")
        local_subreddit_hit = any(
            (s.subreddit or "").lower() == city_slug
            for s in reddit_sources
        )
        if local_subreddit_hit:
            points += 3

        # --- High-specificity mention (4 or 5) ---
        high_spec = any((m.specificity_score or 0) >= 4 for m in mentions)
        if high_spec:
            points += 3

        return points

    def _blog_signals(
        self, mentions: list, source_map: dict, blog_sources: list
    ) -> int:
        points = 0

        # --- Authenticity score tiers (per blog source, capped at best 3) ---
        auth_scores = sorted(
            [s.authenticity_score or 0 for s in blog_sources], reverse=True
        )[:3]
        for auth in auth_scores:
            if auth > 0.8:
                points += 15
            elif auth >= 0.6:
                points += 10
            elif auth >= 0.4:
                points += 5

        # --- Long-form post (depth signal) ---
        long_posts = [s for s in blog_sources if (s.word_count or 0) >= 1000]
        if long_posts:
            points += 8

        # --- Specific dish mentioned ---
        dish_mentioned = any(m.dish_mentioned for m in mentions)
        if dish_mentioned:
            points += 5

        # --- Neighborhood / street-level location in mention text ---
        location_pattern = re.compile(
            r"\b(alley|lane|soi|street|road|district|neighbourhood|neighborhood|"
            r"market|station|corner|floor|building|upstairs|basement|opposite|"
            r"next to|behind|off)\b",
            re.IGNORECASE,
        )
        location_in_text = any(
            location_pattern.search(m.mention_text or "")
            for m in mentions
            if source_map.get(m.source_id)
            and source_map[m.source_id].source_type == SourceType.blog
        )
        if location_in_text:
            points += 5

        # --- Price mentioned in blog source text ---
        price_pattern = re.compile(
            r"(\$\d+|£\d+|€\d+|¥\d+|\d+\s*(baht|yen|won|rupee|peso|ringgit|dong)"
            r"|\bprice\b|\bcost\b|\bcheap\b|\bexpensive\b)",
            re.IGNORECASE,
        )
        price_in_blog = any(
            price_pattern.search(s.full_text or "") for s in blog_sources
        )
        if price_in_blog:
            points += 3

        # --- Time-of-day reference in blog source ---
        time_pattern = re.compile(
            r"\b(morning|afternoon|evening|breakfast|lunch|dinner|"
            r"late.?night|midnight|brunch)\b",
            re.IGNORECASE,
        )
        time_in_blog = any(
            time_pattern.search(s.full_text or "") for s in blog_sources
        )
        if time_in_blog:
            points += 3

        return points

    def _cross_signals(
        self, reddit_sources: list, blog_sources: list, all_sources: list
    ) -> int:
        points = 0
        unique_source_count = len({s.id for s in all_sources})

        if reddit_sources and blog_sources:
            points += 15  # mentioned in BOTH Reddit AND a blog
        elif unique_source_count >= 3:
            points += 10
        elif unique_source_count >= 2:
            points += 5

        return points

    def _recency_signals(self, sources: list, restaurant: Restaurant) -> int:
        points = 0
        dates = [s.published_at for s in sources if s.published_at]
        if not dates:
            return 0

        most_recent = max(dates)
        restaurant.last_mentioned_at = most_recent

        age_days = (datetime.utcnow() - most_recent).days
        if age_days <= 180:
            points += 8
        elif age_days <= 365:
            points += 5
        elif age_days <= 730:
            points += 2
        elif age_days > 1095:
            points -= 5

        return points

    def _negative_signals(
        self, restaurant: Restaurant, mentions: list, sources: list
    ) -> int:
        points = 0

        # --- Known international chain ---
        name_lower = restaurant.name.lower()
        if any(chain in name_lower for chain in KNOWN_CHAINS):
            points -= 15
            logger.debug(
                f"[Scorer] '{restaurant.name}' penalised as known chain"
            )

        # --- Majority negative sentiment ---
        negative_count = sum(1 for m in mentions if m.sentiment == Sentiment.negative)
        if negative_count > 0:
            points -= 10

        # --- All mentions are vague (specificity ≤ 1) ---
        all_vague = all((m.specificity_score or 0) <= 1 for m in mentions)
        if all_vague:
            points -= 8

        # --- Low Claude confidence across the board ---
        # (Claude filters < 0.35 at extraction time, so this catches 0.35–0.50)
        # We don't have confidence stored on Mention; this was a spec signal.
        # Approximation: if all specificity scores are 1–2, apply small penalty.
        all_low_spec = all((m.specificity_score or 0) <= 2 for m in mentions)
        if all_low_spec and len(mentions) <= 2:
            points -= 5

        # --- Only a single mention total ---
        if len(mentions) == 1:
            points -= 5

        return points
