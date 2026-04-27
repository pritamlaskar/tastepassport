"""
TastePassport Marketing Agent — Main Orchestrator.

Fetches real data from the TastePassport API, generates platform-specific
content via Claude, tracks what's been posted, and surfaces performance signals.

Workflow per run:
  1. Pull top cities and restaurant data from the TP API
  2. Select content pillars for today based on rotation schedule
  3. Generate content for each platform (Twitter, Instagram, LinkedIn)
  4. Save to output/ directory as dated JSON + plain text files
  5. Update post_history.json for performance tracking
  6. (Optional) Post directly via platform APIs if keys are configured
"""
import json
import logging
import os
import re
from datetime import datetime, date
from pathlib import Path
from typing import Optional

import httpx

from generators import ContentGenerator, Restaurant, GeneratedContent
from brand import CONTENT_PILLARS

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).parent / "output"
HISTORY_FILE = Path(__file__).parent / "post_history.json"


class TastePassportMarketingAgent:
    def __init__(
        self,
        anthropic_api_key: str,
        api_base_url: str = "http://localhost:8000",
        twitter_bearer_token: Optional[str] = None,
        instagram_access_token: Optional[str] = None,
        linkedin_access_token: Optional[str] = None,
    ):
        self.api_base = api_base_url.rstrip("/")
        self.generator = ContentGenerator(api_key=anthropic_api_key)
        self.twitter_token = twitter_bearer_token
        self.instagram_token = instagram_access_token
        self.linkedin_token = linkedin_access_token

        OUTPUT_DIR.mkdir(exist_ok=True)
        self._history = self._load_history()

    # ------------------------------------------------------------------ #
    #  Main entry points
    # ------------------------------------------------------------------ #

    def run_daily(self, cities: Optional[list[str]] = None) -> dict:
        """Generate a full day's content across all platforms."""
        logger.info("=== TastePassport Marketing Agent — Daily Run ===")

        available_cities = self._get_indexed_cities()
        if not available_cities:
            logger.error("No indexed cities found. Run the pipeline first.")
            return {}

        target_cities = cities or available_cities[:3]
        results = {}

        for city in target_cities:
            logger.info(f"Generating content for: {city}")
            restaurants = self._get_top_restaurants(city)
            if not restaurants:
                logger.warning(f"No restaurants found for {city}, skipping")
                continue

            city_content = self._generate_city_content(city, restaurants)
            results[city] = city_content
            self._save_content(city, city_content)

        # LinkedIn founder post (not city-specific, done once per day)
        linkedin_post = self._generate_linkedin_founder_post(target_cities, results)
        results["linkedin_founder"] = linkedin_post
        self._save_single(linkedin_post)

        self._update_history(results)
        self._print_summary(results)
        return results

    def run_for_city(self, city: str) -> dict:
        """Generate all content for a single city."""
        restaurants = self._get_top_restaurants(city)
        if not restaurants:
            raise ValueError(f"No restaurant data found for '{city}'. Run the pipeline first.")

        content = self._generate_city_content(city, restaurants)
        self._save_content(city, content)
        self._update_history({"city": content})
        return content

    def generate_reply(self, platform: str, post_summary: str, comment: str) -> str:
        """Generate a brand-voice reply to a comment."""
        return self.generator.reply_to_comment(platform, post_summary, comment)

    def performance_report(self) -> dict:
        """Summarize post history — what's been generated and engagement notes."""
        if not self._history:
            return {"status": "No history yet. Run the agent first."}

        total_posts = sum(
            len(day.get("posts", [])) for day in self._history.values()
        )
        cities_covered = set()
        platform_counts = {"twitter": 0, "instagram": 0, "linkedin": 0}

        for day_data in self._history.values():
            for post in day_data.get("posts", []):
                if post.get("city") and post["city"] != "general":
                    cities_covered.add(post["city"])
                plat = post.get("platform", "")
                if plat in platform_counts:
                    platform_counts[plat] += 1

        return {
            "total_posts_generated": total_posts,
            "days_active": len(self._history),
            "cities_covered": sorted(cities_covered),
            "posts_by_platform": platform_counts,
            "last_run": max(self._history.keys()) if self._history else None,
        }

    # ------------------------------------------------------------------ #
    #  Content generation
    # ------------------------------------------------------------------ #

    def _generate_city_content(self, city: str, restaurants: list[Restaurant]) -> list[GeneratedContent]:
        content = []
        top_restaurant = restaurants[0]

        insight = self._build_city_insight(city, restaurants)

        # Twitter: city thread + spotlight + hot take
        try:
            content.append(self.generator.twitter_city_thread(city, restaurants))
        except Exception as e:
            logger.warning(f"Twitter city thread failed for {city}: {e}")

        try:
            content.append(self.generator.twitter_restaurant_spotlight(city, top_restaurant))
        except Exception as e:
            logger.warning(f"Twitter spotlight failed for {city}: {e}")

        try:
            content.append(self.generator.twitter_hot_take(city, insight))
        except Exception as e:
            logger.warning(f"Twitter hot take failed for {city}: {e}")

        # Instagram: city guide + spotlight
        try:
            content.append(self.generator.instagram_city_guide(city, restaurants))
        except Exception as e:
            logger.warning(f"Instagram guide failed for {city}: {e}")

        try:
            content.append(self.generator.instagram_restaurant_spotlight(city, top_restaurant))
        except Exception as e:
            logger.warning(f"Instagram spotlight failed for {city}: {e}")

        # LinkedIn: data insight
        try:
            content.append(self.generator.linkedin_data_insight(city, restaurants, insight))
        except Exception as e:
            logger.warning(f"LinkedIn insight failed for {city}: {e}")

        logger.info(f"Generated {len(content)} pieces of content for {city}")
        return content

    def _generate_linkedin_founder_post(self, cities: list[str], results: dict) -> GeneratedContent:
        # Rotate through founder story angles based on day of week
        angles = [
            "Why we only use Reddit and personal food blogs — and why that's the right call",
            "How LocalScore works: the algorithm that ranks restaurants without a single paid placement",
            "We analyzed 500+ Reddit threads about food in one city. Here's what we found.",
            "Why Yelp and TripAdvisor will never give you the real answer — and what does",
            "Building a trust layer for food discovery: the technical approach behind TastePassport",
            "What happens when real people recommend restaurants vs. algorithms",
            "The SEO food content problem: why 90% of 'best restaurants in X' articles are useless",
        ]
        day_of_week = datetime.now().weekday()
        angle = angles[day_of_week % len(angles)]
        return self.generator.linkedin_founder_story(angle)

    def _build_city_insight(self, city: str, restaurants: list[Restaurant]) -> str:
        if not restaurants:
            return f"TastePassport indexed food spots in {city} from Reddit and personal food blogs."

        avg_score = sum(r.local_score for r in restaurants) / len(restaurants)
        top = restaurants[0]
        cuisines = [r.cuisine_type for r in restaurants if r.cuisine_type]
        most_common = max(set(cuisines), key=cuisines.count) if cuisines else "local"

        lines = [
            f"TastePassport indexed {len(restaurants)} places in {city}.",
            f"Average LocalScore: {avg_score:.0f}/100.",
            f"Top scorer: {top.name} at {top.local_score}/100",
        ]
        if top.why_it_ranks:
            lines.append(f"Why: {top.why_it_ranks}")
        lines.append(f"Most common cuisine in top results: {most_common}.")
        return " ".join(lines)

    # ------------------------------------------------------------------ #
    #  API calls to TastePassport backend
    # ------------------------------------------------------------------ #

    def _get_indexed_cities(self) -> list[str]:
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.get(f"{self.api_base}/api/cities")
                resp.raise_for_status()
                data = resp.json()
                return [c.get("slug") or c.get("name", "").lower() for c in data if c]
        except Exception as e:
            logger.error(f"Failed to fetch cities: {e}")
            return []

    def _get_top_restaurants(self, city_slug: str, limit: int = 10) -> list[Restaurant]:
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.get(f"{self.api_base}/api/city/{city_slug}?limit={limit}")
                resp.raise_for_status()
                data = resp.json()
                places = data if isinstance(data, list) else data.get("places", data.get("restaurants", []))
                return [self._parse_restaurant(p) for p in places if p]
        except Exception as e:
            logger.error(f"Failed to fetch restaurants for {city_slug}: {e}")
            return []

    def _parse_restaurant(self, data: dict) -> Restaurant:
        price = data.get("price_range")
        if price and isinstance(price, str):
            price = {"budget": "$", "mid": "$$", "upscale": "$$$"}.get(price, price)

        return Restaurant(
            name=data.get("name", "Unknown"),
            local_score=data.get("local_score", 0),
            cuisine_type=data.get("cuisine_type"),
            signature_dish=data.get("signature_dish"),
            why_it_ranks=data.get("why_it_ranks"),
            price_range=price,
            reddit_mention_count=data.get("reddit_mention_count", 0),
            blog_mention_count=data.get("blog_mention_count", 0),
        )

    # ------------------------------------------------------------------ #
    #  Output / persistence
    # ------------------------------------------------------------------ #

    def _save_content(self, city: str, content_list: list[GeneratedContent]):
        today = date.today().isoformat()
        city_slug = city.lower().replace(" ", "-")

        # Save as JSON
        json_path = OUTPUT_DIR / f"{today}_{city_slug}.json"
        payload = [self._content_to_dict(c) for c in content_list]
        json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))

        # Save human-readable text file
        txt_path = OUTPUT_DIR / f"{today}_{city_slug}.txt"
        lines = [f"TastePassport Content — {city} — {today}\n{'='*60}\n"]
        for c in content_list:
            lines.append(f"\n[{c.platform.upper()} / {c.content_type}]")
            lines.append(c.body)
            if c.hashtags:
                lines.append("\nHashtags: " + " ".join(c.hashtags))
            lines.append("\n" + "-"*40)
        txt_path.write_text("\n".join(lines), encoding="utf-8")

        logger.info(f"Saved: {json_path.name}, {txt_path.name}")

    def _save_single(self, content: GeneratedContent):
        today = date.today().isoformat()
        path = OUTPUT_DIR / f"{today}_linkedin_founder.txt"
        path.write_text(
            f"TastePassport LinkedIn Founder Post — {today}\n{'='*60}\n\n"
            + content.body + "\n\nHashtags: " + " ".join(content.hashtags),
            encoding="utf-8",
        )

    def _content_to_dict(self, c: GeneratedContent) -> dict:
        return {
            "platform": c.platform,
            "content_type": c.content_type,
            "city": c.city,
            "body": c.body,
            "hashtags": c.hashtags,
            "cta": c.cta,
            "restaurant_featured": c.restaurant_featured,
            "generated_at": datetime.now().isoformat(),
        }

    def _load_history(self) -> dict:
        if HISTORY_FILE.exists():
            try:
                return json.loads(HISTORY_FILE.read_text())
            except Exception:
                return {}
        return {}

    def _update_history(self, results: dict):
        today = date.today().isoformat()
        posts = []
        for key, value in results.items():
            if isinstance(value, list):
                for c in value:
                    posts.append(self._content_to_dict(c))
            elif isinstance(value, GeneratedContent):
                posts.append(self._content_to_dict(value))

        self._history[today] = {
            "generated_at": datetime.now().isoformat(),
            "posts": posts,
        }
        HISTORY_FILE.write_text(json.dumps(self._history, indent=2, ensure_ascii=False))

    def _print_summary(self, results: dict):
        total = sum(len(v) for v in results.values() if isinstance(v, list))
        cities = [k for k in results if k != "linkedin_founder"]
        print(f"\n✓ Generated {total} posts across {len(cities)} cities")
        print(f"✓ Platforms: Twitter, Instagram, LinkedIn")
        print(f"✓ Output saved to: {OUTPUT_DIR}/")
        print(f"✓ Cities covered: {', '.join(cities)}")
