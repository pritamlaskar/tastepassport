"""
Platform-specific content generators for TastePassport.
Each generator takes structured data (city, restaurants, insights)
and returns ready-to-post content for a specific platform.
"""
import json
import logging
from dataclasses import dataclass
from typing import Optional

import anthropic

from brand import SYSTEM_PROMPT, PLATFORM_CONFIGS

logger = logging.getLogger(__name__)


@dataclass
class Restaurant:
    name: str
    local_score: int
    cuisine_type: Optional[str]
    signature_dish: Optional[str]
    why_it_ranks: Optional[str]
    price_range: Optional[str]
    reddit_mention_count: int
    blog_mention_count: int


@dataclass
class GeneratedContent:
    platform: str
    content_type: str
    city: str
    body: str
    hashtags: list[str]
    cta: str
    restaurant_featured: Optional[str] = None


class ContentGenerator:
    def __init__(self, api_key: str):
        self.client = anthropic.Anthropic(api_key=api_key)

    def _call(self, prompt: str, max_tokens: int = 1000) -> str:
        response = self.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()

    # ------------------------------------------------------------------ #
    #  Twitter / X
    # ------------------------------------------------------------------ #

    def twitter_city_thread(self, city: str, restaurants: list[Restaurant]) -> GeneratedContent:
        cfg = PLATFORM_CONFIGS["twitter"]
        top = restaurants[:5]

        restaurant_data = "\n".join([
            f"- {r.name} | LocalScore {r.local_score}/100 | "
            f"{r.cuisine_type or 'Unknown cuisine'} | "
            f"{r.reddit_mention_count} Reddit + {r.blog_mention_count} blog mentions"
            f"{' | ' + r.signature_dish if r.signature_dish else ''}"
            f"{' | ' + r.why_it_ranks if r.why_it_ranks else ''}"
            for r in top
        ])

        prompt = f"""Write a Twitter thread about the best food spots in {city} based on TastePassport data.

CITY: {city}
TOP RESTAURANTS (real data):
{restaurant_data}

FORMAT:
- Tweet 1: Hook. State what we found and why it's different (no algorithms, real Reddit/blog data).
- Tweets 2–{min(len(top)+1, cfg['thread_limit']-1)}: One restaurant per tweet. Include name, LocalScore,
  what makes it stand out, and 1 specific detail (dish, upvotes, or signal count).
- Final tweet: CTA — "{cfg['cta']}"

RULES:
- Each tweet max 280 characters (leave room for numbering like "2/7")
- No generic superlatives. Every claim must reference a signal.
- Separate tweets with "---" on its own line.
- No more than 2 hashtags total, only in the last tweet.

Write the thread now:"""

        body = self._call(prompt, max_tokens=1200)
        return GeneratedContent(
            platform="twitter",
            content_type="city_thread",
            city=city,
            body=body,
            hashtags=["#TastePassport", f"#{city.replace(' ', '')}Food"],
            cta=cfg["cta"],
        )

    def twitter_restaurant_spotlight(self, city: str, restaurant: Restaurant) -> GeneratedContent:
        prompt = f"""Write a single tweet (max 280 chars) spotlighting this restaurant on TastePassport.

Restaurant: {restaurant.name}
City: {city}
LocalScore: {restaurant.local_score}/100
Why it ranks: {restaurant.why_it_ranks or 'N/A'}
Signature dish: {restaurant.signature_dish or 'N/A'}
Sources: {restaurant.reddit_mention_count} Reddit mentions, {restaurant.blog_mention_count} blog mentions

Write ONE punchy tweet. Lead with the restaurant name and LocalScore. Reference real signals. No hype words."""

        body = self._call(prompt, max_tokens=200)
        return GeneratedContent(
            platform="twitter",
            content_type="restaurant_spotlight",
            city=city,
            body=body,
            hashtags=["#TastePassport"],
            cta="",
            restaurant_featured=restaurant.name,
        )

    def twitter_hot_take(self, city: str, insight: str) -> GeneratedContent:
        prompt = f"""Write a hot take tweet about food discovery / travel food recommendations.

Context about {city}: {insight}

The take should be slightly provocative, reference why Yelp / TripAdvisor / Google Maps fail travelers,
and position TastePassport's approach (real Reddit + blog signals) as the better way.
Max 280 characters. No hashtags."""

        body = self._call(prompt, max_tokens=200)
        return GeneratedContent(
            platform="twitter",
            content_type="hot_take",
            city=city,
            body=body,
            hashtags=["#TastePassport"],
            cta=PLATFORM_CONFIGS["twitter"]["cta"],
        )

    # ------------------------------------------------------------------ #
    #  Instagram
    # ------------------------------------------------------------------ #

    def instagram_city_guide(self, city: str, restaurants: list[Restaurant]) -> GeneratedContent:
        cfg = PLATFORM_CONFIGS["instagram"]
        top = restaurants[:6]

        restaurant_data = "\n".join([
            f"- {r.name}: LocalScore {r.local_score}/100"
            f"{', ' + r.signature_dish if r.signature_dish else ''}"
            f" ({r.reddit_mention_count} Reddit + {r.blog_mention_count} blog sources)"
            f"{'. ' + r.why_it_ranks if r.why_it_ranks else ''}"
            for r in top
        ])

        prompt = f"""Write an Instagram caption for a city food guide post about {city}.

TOP PICKS (real TastePassport data):
{restaurant_data}

FORMAT:
- Opening line: bold hook about {city}'s food scene (no generic "hidden gems")
- Short paragraph explaining TastePassport's approach: we read Reddit + real food blogs,
  not sponsored lists. Every score is explainable.
- The top picks, listed cleanly with LocalScore and one concrete detail each
- Closing: "{cfg['cta']}"
- 3 relevant hashtags at the end

Keep it under 400 words. Use line breaks for readability. Voice is confident, data-backed, human."""

        body = self._call(prompt, max_tokens=800)
        return GeneratedContent(
            platform="instagram",
            content_type="city_guide",
            city=city,
            body=body,
            hashtags=["#TastePassport", "#RealReviews", f"#{city.replace(' ', '')}Food"],
            cta=cfg["cta"],
        )

    def instagram_restaurant_spotlight(self, city: str, restaurant: Restaurant) -> GeneratedContent:
        prompt = f"""Write an Instagram caption spotlighting one restaurant found by TastePassport.

Restaurant: {restaurant.name}
City: {city}
LocalScore: {restaurant.local_score}/100
Cuisine: {restaurant.cuisine_type or 'Local'}
Signature dish: {restaurant.signature_dish or 'not specified'}
Why it ranks: {restaurant.why_it_ranks or 'N/A'}
Reddit mentions: {restaurant.reddit_mention_count}
Blog mentions: {restaurant.blog_mention_count}
Price range: {restaurant.price_range or 'not specified'}

Write a caption that:
- Opens with the restaurant name and something specific about it
- Explains WHY TastePassport surfaces it (real signals, not sponsored ranking)
- Includes the LocalScore prominently
- Closes with "Link in bio to find your next meal."
- 2–3 hashtags at end

Under 300 words. Confident and specific."""

        body = self._call(prompt, max_tokens=600)
        return GeneratedContent(
            platform="instagram",
            content_type="restaurant_spotlight",
            city=city,
            body=body,
            hashtags=["#TastePassport", "#RealReviews", f"#{city.replace(' ', '')}Food"],
            cta=PLATFORM_CONFIGS["instagram"]["cta"],
            restaurant_featured=restaurant.name,
        )

    # ------------------------------------------------------------------ #
    #  LinkedIn
    # ------------------------------------------------------------------ #

    def linkedin_data_insight(self, city: str, restaurants: list[Restaurant], insight: str) -> GeneratedContent:
        top = restaurants[:3]
        restaurant_data = "\n".join([
            f"- {r.name}: score {r.local_score}/100, {r.reddit_mention_count + r.blog_mention_count} total sources"
            for r in top
        ])

        prompt = f"""Write a LinkedIn post for TastePassport sharing a data insight from our food discovery engine.

City analyzed: {city}
Insight: {insight}
Top results from our data:
{restaurant_data}

FORMAT:
- Opening hook (1 line): something counterintuitive or surprising about food recommendations in {city}
- 2–3 paragraphs explaining: what we found, how LocalScore works (Reddit + blog signals, no sponsorship),
  what this reveals about how people actually find good food
- Closing: what this means for travelers, + CTA: "TastePassport is live. Try it for free."

Voice: founder talking to other builders and food-curious professionals.
Confident, transparent, slightly nerdy about the data. No corporate-speak.
Under 500 words."""

        body = self._call(prompt, max_tokens=900)
        return GeneratedContent(
            platform="linkedin",
            content_type="data_insight",
            city=city,
            body=body,
            hashtags=["#TastePassport", "#FoodDiscovery", "#BuildingInPublic"],
            cta="TastePassport is live. Try it for free.",
        )

    def linkedin_founder_story(self, angle: str) -> GeneratedContent:
        prompt = f"""Write a LinkedIn founder post for TastePassport.

Angle: {angle}

Background:
- TastePassport indexes genuine food recommendations from Reddit and personal food blogs
- No sponsored content, no SEO farms, no algorithms optimizing for engagement
- Uses LocalScore (0–100): based on upvotes, cross-source mentions, blog authenticity, recency
- Built with FastAPI + Celery + Claude API for entity extraction and enrichment
- Every restaurant score is fully explainable — "mentioned in 4 personal blogs and a 600-upvote Reddit thread"

Write a post that:
- Opens with the frustration that inspired TastePassport (bad recs from mainstream platforms)
- Explains what we built and why it's different
- Includes a specific, real-feeling example of what our data surfaces vs. what Yelp/Google shows
- Closes with a question to engage the reader or a soft CTA

Voice: honest, builder-to-builder, passionate about food and trust. No buzzwords.
Under 400 words."""

        body = self._call(prompt, max_tokens=800)
        return GeneratedContent(
            platform="linkedin",
            content_type="founder_story",
            city="general",
            body=body,
            hashtags=["#TastePassport", "#BuildingInPublic", "#FoodTech"],
            cta="TastePassport is live. Try it for free.",
        )

    # ------------------------------------------------------------------ #
    #  Engagement reply generator
    # ------------------------------------------------------------------ #

    def reply_to_comment(self, platform: str, original_post: str, comment: str) -> str:
        prompt = f"""Write a reply to this comment on TastePassport's {platform} post.

Our post was about: {original_post}

Comment received: "{comment}"

Reply rules:
- Stay in TastePassport brand voice: data-backed, honest, not salesy
- If they ask how we find places: mention Reddit + personal food blogs, LocalScore
- If they suggest a restaurant: thank them, say we'd love to see it surface in our data
- If critical: acknowledge, don't get defensive, reference our transparency (every score is explainable)
- Max 2–3 sentences
- No emojis unless the commenter used them first

Write just the reply text, nothing else."""

        return self._call(prompt, max_tokens=200)
