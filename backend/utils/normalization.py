"""
Entity name normalization and restaurant deduplication.

find_or_create_restaurant() is called for every mention Claude extracts.
It must be fast, consistent, and never create duplicate restaurant records
for the same physical place.

Deduplication strategy:
  1. Exact match on normalized name (fastest path)
  2. Fuzzy match on all existing restaurants in the city (fuzz.token_sort_ratio > 85)
  3. If no match — create new restaurant record

Normalization strips common suffixes and leading articles so that:
  "The Pad Thai Place" → "Pad Thai Place"
  "Jay Fai Restaurant" → "Jay Fai"
  "Thip Samai"         → "Thip Samai"  (unchanged — correct)
"""
import re
import uuid
import logging
from datetime import datetime
from typing import Optional

from fuzzywuzzy import fuzz
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Suffixes that add no identifying information
_STRIP_SUFFIXES = re.compile(
    r"\s+(restaurant|café|cafe|coffee|bar|bistro|kitchen|eatery|"
    r"grill|diner|house|place|shop|stall|stand|cart|food|"
    r"street food|noodles|noodle|bbq|barbeque|barbecue)\.?$",
    re.IGNORECASE,
)

# Leading articles
_STRIP_ARTICLES = re.compile(r"^(the|a|an)\s+", re.IGNORECASE)

# Collapse internal whitespace
_WHITESPACE = re.compile(r"\s+")


def normalize_restaurant_name(name: str) -> str:
    """Return a cleaned canonical form of a restaurant name."""
    if not name:
        return name
    name = name.strip()
    name = _STRIP_ARTICLES.sub("", name).strip()
    name = _STRIP_SUFFIXES.sub("", name).strip()
    name = _WHITESPACE.sub(" ", name)
    return name


def find_or_create_restaurant(
    db: Session,
    city_id: str,
    name: str,
    raw_name: str,
) -> "Restaurant":  # type: ignore[name-defined]
    """
    Look up an existing restaurant in this city by name.
    Creates one if no sufficiently similar record exists.
    Returns the restaurant ORM object (existing or new).
    """
    from models.restaurant import Restaurant

    if not name:
        name = raw_name

    # --- Fast path: exact match on normalized name ---
    exact = (
        db.query(Restaurant)
        .filter(
            Restaurant.city_id == city_id,
            Restaurant.name == name,
        )
        .first()
    )
    if exact:
        _add_variant(exact, raw_name, db)
        return exact

    # --- Fuzzy match across all restaurants in this city ---
    all_restaurants = (
        db.query(Restaurant)
        .filter(Restaurant.city_id == city_id)
        .all()
    )

    best_match: Optional["Restaurant"] = None
    best_score = 0

    for r in all_restaurants:
        score = fuzz.token_sort_ratio(name.lower(), r.name.lower())
        if score > best_score:
            best_score = score
            best_match = r

        # Also check against known variants
        for variant in (r.name_variants or []):
            v_score = fuzz.token_sort_ratio(name.lower(), variant.lower())
            if v_score > best_score:
                best_score = v_score
                best_match = r

    if best_score >= 86:
        logger.debug(
            f"[Norm] Matched '{name}' → '{best_match.name}' "
            f"(score={best_score})"
        )
        _add_variant(best_match, raw_name, db)
        return best_match

    # --- No match — create new restaurant ---
    restaurant = Restaurant(
        id=str(uuid.uuid4()),
        city_id=city_id,
        name=name,
        name_variants=[raw_name] if raw_name and raw_name != name else [],
        first_seen_at=datetime.utcnow(),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(restaurant)
    db.commit()
    db.refresh(restaurant)
    logger.debug(f"[Norm] Created new restaurant: '{name}' (city={city_id[:8]})")
    return restaurant


def _add_variant(restaurant, raw_name: str, db: Session):
    """Add raw_name to name_variants if not already present."""
    if not raw_name or raw_name == restaurant.name:
        return
    variants = list(restaurant.name_variants or [])
    if raw_name not in variants:
        variants.append(raw_name)
        restaurant.name_variants = variants
        db.commit()
