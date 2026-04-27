"""
Entity extractor — Stage 3.

For every raw source collected in Stages 1–2, calls the Claude API to extract
restaurant/food-spot mentions as structured JSON.

Design decisions:
  - Sources already processed (mention already exists) are skipped — idempotent.
  - Claude response is parsed with a three-pass fallback:
      1. Direct JSON.loads()
      2. Strip markdown code fences, retry
      3. Regex extraction of first {...} block
  - Claude API errors are retried up to 3× with exponential backoff.
  - Token usage is logged per call and as a running total.
  - Each mention is linked to its source AND its restaurant entity.
  - Restaurant entity creation / deduplication is handled by normalization.py.
"""
import json
import re
import time
import uuid
import logging
from datetime import datetime
from typing import Optional

import anthropic
from sqlalchemy.orm import Session

from models.source import RawSource, Mention, SourceType, Sentiment
from utils.text import truncate_for_extraction

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
#  Prompts
# ------------------------------------------------------------------ #

SYSTEM_PROMPT = (
    "You are a food intelligence extraction engine. Your job is to extract restaurant "
    "and food spot mentions from text written by real people sharing genuine dining "
    "experiences. You are precise, literal, and never invent information not present "
    "in the text."
)

EXTRACT_PROMPT = """\
Extract all restaurant, food stall, café, bar, or food market mentions from the \
following text. This text is from {source_type} about food in {city}.

For each place mentioned, extract:
- name: exact name as written (include all variants if multiple spellings appear)
- neighborhood: specific area or district if mentioned, else null
- dish: the most specific dish or item mentioned alongside this place, else null
- mention_text: the exact sentence or sentences referencing this place (max 3 sentences)
- sentiment: positive / neutral / negative
- specificity: integer 1–5
    1 = vague ("great food there")
    2 = mild ("good pad thai")
    3 = some detail ("excellent khao soi, cash only")
    4 = high detail (dish + location + context)
    5 = exceptional (dish + location + personal story + why it matters)
- confidence: float 0.0–1.0 — how confident you are this is a genuine personal \
recommendation vs a generic or hypothetical mention

Only extract places where a human is clearly sharing a personal experience or \
recommendation. Do not extract:
- Places mentioned in passing or as negative examples
- Places in hypothetical or conditional sentences ("if you like X, try Y")
- Places only mentioned by name with no context

Return valid JSON only. No preamble. No explanation. No markdown fences.

{{
  "places": [
    {{
      "name": "",
      "neighborhood": null,
      "dish": null,
      "mention_text": "",
      "sentiment": "positive",
      "specificity": 3,
      "confidence": 0.8
    }}
  ]
}}

TEXT TO ANALYZE:
{text}"""


# ------------------------------------------------------------------ #
#  Main class
# ------------------------------------------------------------------ #

class EntityExtractor:
    def __init__(self, city, job, db: Session):
        self.city = city
        self.job = job
        self.db = db

        from config import get_settings
        settings = get_settings()
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

        self.total_extracted = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.sources_processed = 0
        self.sources_skipped = 0
        self.sources_failed = 0

    def run(self) -> int:
        sources = (
            self.db.query(RawSource)
            .filter(
                RawSource.city_id == self.city.id,
                RawSource.crawl_job_id == self.job.id,
            )
            .all()
        )

        logger.info(
            f"[Extractor] {self.city.name} — {len(sources)} sources to process"
        )

        batch_size = 10
        for batch_num, i in enumerate(range(0, len(sources), batch_size), start=1):
            batch = sources[i : i + batch_size]
            for source in batch:
                if self._already_processed(source):
                    self.sources_skipped += 1
                    continue
                try:
                    count = self._extract_from_source(source)
                    self.total_extracted += count
                    self.sources_processed += 1
                except Exception as e:
                    logger.warning(
                        f"[Extractor] Failed on source {source.id[:8]}: {e}"
                    )
                    self.sources_failed += 1
                    continue

            logger.info(
                f"[Extractor] Batch {batch_num} done — "
                f"{self.total_extracted} entities so far | "
                f"tokens: {self.total_input_tokens}in / {self.total_output_tokens}out"
            )

        logger.info(
            f"[Extractor] Complete — "
            f"extracted={self.total_extracted} | "
            f"processed={self.sources_processed} | "
            f"skipped={self.sources_skipped} | "
            f"failed={self.sources_failed} | "
            f"total_tokens={self.total_input_tokens + self.total_output_tokens}"
        )

        return self.total_extracted

    # ------------------------------------------------------------------ #
    #  Per-source extraction
    # ------------------------------------------------------------------ #

    def _already_processed(self, source: RawSource) -> bool:
        """Return True if we already have mentions from this source."""
        return (
            self.db.query(Mention)
            .filter(Mention.source_id == source.id)
            .first()
            is not None
        )

    def _extract_from_source(self, source: RawSource) -> int:
        text = source.full_text or ""
        if len(text.strip()) < 80:
            return 0

        text = truncate_for_extraction(text, max_chars=8000)

        if source.source_type == SourceType.reddit:
            source_label = (
                "a Reddit comment" if "/_/" in (source.source_url or "")
                else "a Reddit post"
            )
        else:
            source_label = "a personal food blog post"

        prompt = EXTRACT_PROMPT.format(
            source_type=source_label,
            city=self.city.name,
            text=text,
        )

        response = self._call_claude(prompt)
        if response is None:
            return 0

        self.total_input_tokens += response.usage.input_tokens
        self.total_output_tokens += response.usage.output_tokens

        logger.debug(
            f"[Extractor] source={source.id[:8]} "
            f"({source.source_type}) — "
            f"{response.usage.input_tokens}in / {response.usage.output_tokens}out tokens"
        )

        raw = response.content[0].text.strip()
        data = self._parse_json(raw, source.id)
        if data is None:
            return 0

        places = data.get("places", [])
        if not isinstance(places, list):
            logger.warning(f"[Extractor] 'places' is not a list in response for {source.id[:8]}")
            return 0

        stored = 0
        for place in places:
            if not isinstance(place, dict):
                continue
            name = (place.get("name") or "").strip()
            if not name or len(name) < 2:
                continue
            confidence = float(place.get("confidence", 0))
            if confidence < 0.35:
                logger.debug(f"[Extractor] Skipping low-confidence place '{name}' ({confidence:.2f})")
                continue
            try:
                self._store_mention(place, source)
                stored += 1
            except Exception as e:
                logger.warning(f"[Extractor] Failed to store mention '{name}': {e}")

        return stored

    # ------------------------------------------------------------------ #
    #  Mention storage
    # ------------------------------------------------------------------ #

    def _store_mention(self, place: dict, source: RawSource):
        from utils.normalization import normalize_restaurant_name, find_or_create_restaurant

        raw_name = (place.get("name") or "").strip()
        normalized = normalize_restaurant_name(raw_name)

        restaurant = find_or_create_restaurant(
            db=self.db,
            city_id=self.city.id,
            name=normalized,
            raw_name=raw_name,
        )

        sentiment_raw = (place.get("sentiment") or "positive").strip().lower()
        sentiment_map = {
            "positive": Sentiment.positive,
            "neutral":  Sentiment.neutral,
            "negative": Sentiment.negative,
        }
        sentiment = sentiment_map.get(sentiment_raw, Sentiment.positive)

        specificity = float(place.get("specificity") or 1)
        specificity = max(1.0, min(5.0, specificity))

        dish = (place.get("dish") or "").strip() or None
        neighborhood = (place.get("neighborhood") or "").strip() or None

        # Back-fill neighborhood on restaurant if not set yet
        if neighborhood and not restaurant.neighborhood:
            restaurant.neighborhood = neighborhood
            self.db.commit()

        mention = Mention(
            id=str(uuid.uuid4()),
            restaurant_id=restaurant.id,
            source_id=source.id,
            city_id=self.city.id,
            mention_text=(place.get("mention_text") or "").strip()[:2000],
            dish_mentioned=dish,
            sentiment=sentiment,
            specificity_score=specificity,
            extracted_at=datetime.utcnow(),
        )
        self.db.add(mention)
        self.db.commit()

    # ------------------------------------------------------------------ #
    #  Claude API call with retry
    # ------------------------------------------------------------------ #

    def _call_claude(self, prompt: str, retries: int = 3):
        for attempt in range(retries):
            try:
                return self.client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=2048,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                )
            except anthropic.RateLimitError:
                wait = 20 * (2 ** attempt)
                logger.warning(
                    f"[Extractor] Claude rate limited (attempt {attempt + 1}) — "
                    f"waiting {wait}s"
                )
                time.sleep(wait)
            except anthropic.APIStatusError as e:
                if e.status_code >= 500:
                    wait = 5 * (2 ** attempt)
                    logger.warning(
                        f"[Extractor] Claude server error {e.status_code} "
                        f"(attempt {attempt + 1}) — waiting {wait}s"
                    )
                    time.sleep(wait)
                else:
                    logger.error(f"[Extractor] Claude API error: {e}")
                    return None
            except Exception as e:
                logger.error(f"[Extractor] Unexpected Claude error: {e}")
                return None
        logger.error(f"[Extractor] All {retries} Claude retries exhausted")
        return None

    # ------------------------------------------------------------------ #
    #  JSON parsing — three-pass fallback
    # ------------------------------------------------------------------ #

    def _parse_json(self, raw: str, source_id: str) -> Optional[dict]:
        # Pass 1: direct parse
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        # Pass 2: strip markdown code fences
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
            f"[Extractor] Could not parse JSON from Claude for source {source_id[:8]}. "
            f"Raw (first 200 chars): {raw[:200]}"
        )
        return None
