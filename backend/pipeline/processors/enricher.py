"""
Enricher — Stage 5.

For every restaurant with LocalScore >= 40, calls Claude to generate:
  - signature_dish   the dish people actually mention most
  - price_range      $  $$  $$$  inferred from context
  - cuisine_type     single category
  - why_it_ranks     ≤25-word sentence that references real signals

The why_it_ranks prompt is fed the score breakdown alongside mention texts
so Claude can write "Mentioned across 4 personal blogs and 2 Reddit threads for
its wood-fired lamb" rather than "A beloved local gem."

Design:
  - Idempotent: already-enriched restaurants are skipped unless force=True
  - Three-pass JSON parsing (same approach as extractor.py)
  - Claude API errors retried with exponential backoff
  - Token usage logged per call and as a running total
"""
import json
import re
import time
import logging
from typing import Optional

import anthropic
from sqlalchemy.orm import Session

from models.restaurant import Restaurant, PriceRange
from models.source import Mention, RawSource, SourceType

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
#  Prompt
# ------------------------------------------------------------------ #

ENRICH_PROMPT = """\
You are writing structured data for TastePassport, a food discovery platform \
that surfaces only genuine human recommendations.

Restaurant: {name}
City: {city}

--- SIGNAL SUMMARY ---
{signal_summary}

--- WHAT PEOPLE SAID (top mentions, most specific first) ---
{mention_texts}

---

Generate the following four fields. Be precise and literal — only use information \
actually present in the signals and mentions above.

1. signature_dish
   The single dish most associated with this place based on what people mention.
   Use the exact dish name as written in the mentions.
   If no specific dish is mentioned, return null.

2. price_range
   "$"  = under $10 USD per person (street food, hawker stall prices)
   "$$" = $10–25 USD per person (sit-down casual)
   "$$$"= $25+ USD per person (upscale, tasting menus)
   Infer strictly from price cues in the mentions or signal summary.
   If genuinely unclear, return null.

3. cuisine_type
   Single cuisine category. Examples: Thai, Japanese, Vietnamese, Italian, \
Mexican, Indian, Chinese, Korean, Middle Eastern, Mediterranean, Street Food, Fusion.
   Use the most specific accurate category.

4. why_it_ranks
   ONE sentence, maximum 25 words.
   Explain why TastePassport surfaces this place.
   Reference actual signals: how many sources, what source types, what dish, \
what made it stand out.

   GOOD: "Appeared in 4 personal food blogs and a 600-upvote Reddit thread, \
always mentioned for its hand-pulled noodles and cash-only policy."
   GOOD: "Three independent Reddit users in r/Bangkok called out the crab \
fried rice by name, alongside two blog posts from long-term expats."
   BAD: "A beloved local gem with amazing food and great atmosphere."
   BAD: "Highly recommended by locals and travelers alike."

Return valid JSON only. No preamble. No markdown fences.

{{
  "signature_dish": null,
  "price_range": null,
  "cuisine_type": "",
  "why_it_ranks": ""
}}"""


# ------------------------------------------------------------------ #
#  Enricher class
# ------------------------------------------------------------------ #

class Enricher:
    def __init__(self, city, job, db: Session):
        self.city = city
        self.job = job
        self.db = db

        from config import get_settings
        settings = get_settings()
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

        self.enriched_count = 0
        self.skipped_count = 0
        self.failed_count = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    def run(self, force: bool = False) -> int:
        restaurants = (
            self.db.query(Restaurant)
            .filter(
                Restaurant.city_id == self.city.id,
                Restaurant.local_score >= 40,
            )
            .order_by(Restaurant.local_score.desc())
            .all()
        )

        logger.info(
            f"[Enricher] {self.city.name} — "
            f"{len(restaurants)} restaurants with score ≥ 40"
        )

        for restaurant in restaurants:
            if not force and self._already_enriched(restaurant):
                self.skipped_count += 1
                continue
            try:
                self._enrich(restaurant)
                self.enriched_count += 1
            except Exception as e:
                logger.warning(
                    f"[Enricher] Failed to enrich '{restaurant.name}': {e}"
                )
                self.failed_count += 1
                continue

        logger.info(
            f"[Enricher] Complete — "
            f"enriched={self.enriched_count} | "
            f"skipped={self.skipped_count} | "
            f"failed={self.failed_count} | "
            f"tokens={self.total_input_tokens + self.total_output_tokens}"
        )
        return self.enriched_count

    # ------------------------------------------------------------------ #
    #  Per-restaurant enrichment
    # ------------------------------------------------------------------ #

    def _already_enriched(self, restaurant: Restaurant) -> bool:
        return bool(restaurant.why_it_ranks and restaurant.cuisine_type)

    def _enrich(self, restaurant: Restaurant):
        # Top 3 mentions by specificity score
        mentions = (
            self.db.query(Mention)
            .filter(Mention.restaurant_id == restaurant.id)
            .order_by(Mention.specificity_score.desc())
            .limit(5)
            .all()
        )
        if not mentions:
            return

        mention_texts = self._format_mention_texts(mentions)
        signal_summary = self._build_signal_summary(restaurant, mentions)

        prompt = ENRICH_PROMPT.format(
            name=restaurant.name,
            city=self.city.name,
            signal_summary=signal_summary,
            mention_texts=mention_texts,
        )

        response = self._call_claude(prompt)
        if response is None:
            return

        self.total_input_tokens  += response.usage.input_tokens
        self.total_output_tokens += response.usage.output_tokens

        logger.debug(
            f"[Enricher] '{restaurant.name}' — "
            f"{response.usage.input_tokens}in / {response.usage.output_tokens}out tokens"
        )

        raw = response.content[0].text.strip()
        data = self._parse_json(raw, restaurant.name)
        if data is None:
            return

        self._apply(restaurant, data)
        self.db.commit()

        logger.info(
            f"[Enricher] '{restaurant.name}' — "
            f"dish={restaurant.signature_dish!r} | "
            f"cuisine={restaurant.cuisine_type!r} | "
            f"price={restaurant.price_range!r} | "
            f"score={restaurant.local_score}"
        )

    def _format_mention_texts(self, mentions: list) -> str:
        lines = []
        for i, m in enumerate(mentions, start=1):
            if not m.mention_text:
                continue
            src = self.db.query(RawSource).filter(RawSource.id == m.source_id).first()
            src_label = ""
            if src:
                if src.source_type == SourceType.reddit:
                    src_label = f" [Reddit r/{src.subreddit}, {src.upvotes or 0} upvotes]"
                else:
                    src_label = f" [Blog: {src.source_domain or 'personal blog'}]"
            dish_note = f" (dish: {m.dish_mentioned})" if m.dish_mentioned else ""
            lines.append(
                f"{i}.{src_label}{dish_note}\n   \"{m.mention_text.strip()}\""
            )
        return "\n\n".join(lines) if lines else "No mention texts available."

    def _build_signal_summary(self, restaurant: Restaurant, mentions: list) -> str:
        bd = restaurant.score_breakdown or {}
        lines = [
            f"LocalScore: {restaurant.local_score}/100",
            f"Total mentions: {restaurant.mention_count} "
            f"({restaurant.reddit_mention_count} Reddit, "
            f"{restaurant.blog_mention_count} blog)",
            f"Reddit signals:       {bd.get('reddit_signals', 0):+d}",
            f"Blog signals:         {bd.get('blog_signals', 0):+d}",
            f"Cross-source signals: {bd.get('cross_source_signals', 0):+d}",
            f"Recency signals:      {bd.get('recency_signals', 0):+d}",
            f"Negative signals:     {bd.get('negative_signals', 0):+d}",
        ]
        if restaurant.last_mentioned_at:
            lines.append(
                f"Most recent mention: {restaurant.last_mentioned_at.strftime('%B %Y')}"
            )
        dishes = [m.dish_mentioned for m in mentions if m.dish_mentioned]
        if dishes:
            # Show most frequently mentioned dish
            from collections import Counter
            top_dish = Counter(dishes).most_common(1)[0][0]
            lines.append(f"Most mentioned dish: {top_dish}")
        return "\n".join(lines)

    def _apply(self, restaurant: Restaurant, data: dict):
        """Write enrichment fields back to the restaurant record."""
        dish = (data.get("signature_dish") or "").strip() or None
        if dish:
            restaurant.signature_dish = dish

        price_raw = (data.get("price_range") or "").strip()
        price_map = {"$": PriceRange.budget, "$$": PriceRange.mid, "$$$": PriceRange.upscale}
        if price_raw in price_map:
            restaurant.price_range = price_map[price_raw]

        cuisine = (data.get("cuisine_type") or "").strip() or None
        if cuisine:
            restaurant.cuisine_type = cuisine

        why = (data.get("why_it_ranks") or "").strip() or None
        if why:
            # Enforce 25-word cap if Claude went over
            words = why.split()
            if len(words) > 30:
                why = " ".join(words[:28]) + "…"
            restaurant.why_it_ranks = why

    # ------------------------------------------------------------------ #
    #  Claude API call with retry
    # ------------------------------------------------------------------ #

    def _call_claude(self, prompt: str, retries: int = 3) -> Optional[object]:
        for attempt in range(retries):
            try:
                return self.client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=400,
                    messages=[{"role": "user", "content": prompt}],
                )
            except anthropic.RateLimitError:
                wait = 20 * (2 ** attempt)
                logger.warning(
                    f"[Enricher] Rate limited (attempt {attempt + 1}) — waiting {wait}s"
                )
                time.sleep(wait)
            except anthropic.APIStatusError as e:
                if e.status_code >= 500:
                    wait = 5 * (2 ** attempt)
                    logger.warning(
                        f"[Enricher] Server error {e.status_code} "
                        f"(attempt {attempt + 1}) — waiting {wait}s"
                    )
                    time.sleep(wait)
                else:
                    logger.error(f"[Enricher] Claude API error: {e}")
                    return None
            except Exception as e:
                logger.error(f"[Enricher] Unexpected error: {e}")
                return None
        logger.error(f"[Enricher] All {retries} retries exhausted")
        return None

    # ------------------------------------------------------------------ #
    #  JSON parsing — three-pass fallback
    # ------------------------------------------------------------------ #

    def _parse_json(self, raw: str, context: str) -> Optional[dict]:
        # Pass 1: direct
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        # Pass 2: strip markdown fences
        stripped = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        stripped = re.sub(r"\s*```$", "", stripped, flags=re.MULTILINE).strip()
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass

        # Pass 3: extract first {...} block
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        logger.warning(
            f"[Enricher] Could not parse JSON for '{context}'. "
            f"Raw (first 200 chars): {raw[:200]}"
        )
        return None
