# TastePassport

Human experience index for food discovery. No algorithms. No ads. No SEO.

## What it is

TastePassport finds the best food recommendations buried in Reddit threads and personal food blogs — not rankings, not sponsored content, not aggregators. It indexes genuine human experiences and surfaces them for anyone planning to eat well in an unfamiliar city.

Two sources only: **Reddit** and **personal food blogs**.

Every result links back to the original Reddit thread it was discovered in. Reddit is the source of truth — TastePassport just makes it searchable.

## How it works

When you search for a city, TastePassport runs a 5-stage pipeline:

1. **Reddit crawl** — searches subreddits like r/travel, r/solotravel, r/Bangkok, r/food using PRAW (official Reddit API, OAuth). Collects posts and top comments from the last 90 days only.
2. **Blog discovery** — DuckDuckGo search finds personal food blogs; a classifier filters out SEO content farms. Pages are rendered with Playwright.
3. **Entity extraction** — Claude API reads every source and extracts restaurant mentions with confidence scores.
4. **LocalScore** — proprietary algorithm scores each restaurant 0–100 based on upvotes, cross-source mentions, blog authenticity, and recency. No manual boosting.
5. **Enrichment** — Claude generates signature dish, price range, cuisine type, and a `why_it_ranks` explanation that references real signals.

Every result in the API includes a direct link to the highest-upvote Reddit thread that mentioned it (`top_reddit_source`). Full mention detail — including all source URLs — is available at `/api/place/{id}`.

## Reddit API usage

TastePassport uses Reddit's official Data API via [PRAW](https://praw.readthedocs.io/) in **read-only mode**:

- OAuth2 client credentials authentication (no user login)
- Read-only access to public subreddits only
- No access to private subreddits, user profiles, DMs, or non-public content
- Searches scoped to the last 90 days (`time_filter=year` + `created_utc` filter)
- All Reddit content displayed to end users includes attribution and a direct link to the original thread
- Reddit data is not used for AI/ML model training

Source code for the Reddit crawler: [`backend/pipeline/crawlers/reddit.py`](backend/pipeline/crawlers/reddit.py)

## Prerequisites

- Docker + Docker Compose
- An Anthropic API key
- A Reddit API app (free — [create one here](https://www.reddit.com/prefs/apps), select "script")

## Setup

```bash
cd tastepassport

# Copy env file and fill in your keys
cp .env.example .env
# Required: ANTHROPIC_API_KEY, REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT

# Build and start all services
docker-compose up --build
```

Services started:

| Service | URL | Purpose |
|---------|-----|---------|
| `api` | http://localhost:8000 | FastAPI — main entry point |
| `celery` | — | Background pipeline worker (2 concurrent) |
| `celery-beat` | — | Daily stale-city recrawl scheduler |
| `postgres` | localhost:5432 | PostgreSQL 15 |
| `redis` | localhost:6379 | Task broker + result backend |
| `flower` | http://localhost:5555 | Celery task monitor |

Verify the stack is live:
```
GET http://localhost:8000/health
→ {"status": "ok", "database": "ok", "redis": "ok"}
```

## API Endpoints

### Health check
```
GET /health
GET /
```

### Get all indexed cities
```
GET /api/cities
```

### Get restaurants for a city
Triggers pipeline automatically if city not yet indexed or stale (>7 days).
```
GET /api/city/{slug}
GET /api/city/bangkok?budget=$&cuisine=Thai&limit=20
```

Every restaurant in this response includes `top_reddit_source` — a direct link back to the Reddit thread it was found in.

### Get full place detail (with all source mentions and URLs)
```
GET /api/place/{id}
```

Returns every Reddit thread and blog post that mentioned this restaurant, with direct URLs, upvote counts, and the exact text that was extracted.

### Check pipeline status
```
GET /api/city/{slug}/status
```

### Search across places
```
GET /api/search?q=pad+thai&city=bangkok
```

### Submit feedback
```
POST /api/feedback
{"restaurant_id": "uuid", "feedback_type": "closed", "note": "Closed in March 2026"}
```

## Running the crawl manually

Use admin endpoints to run each pipeline stage individually and inspect output at each step:

```bash
# Stage 1: Reddit crawl (requires REDDIT_CLIENT_ID + REDDIT_CLIENT_SECRET in .env)
curl -X POST http://localhost:8000/api/admin/reddit/bangkok
curl http://localhost:8000/api/admin/sources/bangkok

# Stage 2: Blog discovery + scrape
curl -X POST http://localhost:8000/api/admin/blogs/bangkok

# Stage 3: Claude entity extraction
curl -X POST http://localhost:8000/api/admin/extract/bangkok
curl http://localhost:8000/api/admin/mentions/bangkok

# Stage 4: LocalScore
curl -X POST http://localhost:8000/api/admin/score/bangkok
curl http://localhost:8000/api/admin/scores/bangkok

# Stage 5: Claude enrichment
curl -X POST http://localhost:8000/api/admin/enrich/bangkok

# Final result
curl http://localhost:8000/api/city/bangkok
```

## LocalScore explained

The score (0–100) is calculated entirely from signal data. No manual boosting. Every restaurant must earn its rank. Full score breakdown is returned in every API response.

| Signal | Points |
|--------|--------|
| Reddit upvotes 500+ | +20 |
| Reddit upvotes 100–499 | +15 |
| Reddit upvotes 50–99 | +10 |
| Reddit upvotes 10–49 | +5 |
| Mentioned in top-level Reddit comment | +8 |
| Multiple distinct Reddit authors | +5 |
| Mentioned in city's local subreddit | +3 |
| High-specificity mention (score ≥ 4) | +3 |
| Blog authenticity score > 0.8 | +15 |
| Blog authenticity score 0.6–0.8 | +10 |
| Blog authenticity score 0.4–0.6 | +5 |
| Long-form blog post (1000+ words) | +8 |
| Specific dish mentioned | +5 |
| Neighborhood/location detail in blog | +5 |
| Price mentioned in blog | +3 |
| Time-of-day reference in blog | +3 |
| Mentioned in both Reddit AND blog | +15 |
| 3+ unique sources | +10 |
| 2 unique sources | +5 |
| Mentioned within last 6 months | +8 |
| Mentioned within last 6–12 months | +5 |
| Mentioned within last 1–2 years | +2 |
| Older than 3 years | −5 |
| Known chain restaurant | −15 |
| Majority negative sentiment | −10 |
| All mentions are vague (specificity ≤ 1) | −8 |
| Only a single mention total | −5 |

Minimum to appear in results: `MIN_LOCAL_SCORE=25` and `MIN_MENTIONS=2` (configurable in `.env`).

## Environment variables

See [`.env.example`](.env.example) for full documentation of all variables.

Key variables:

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Claude API key for extraction and enrichment |
| `REDDIT_CLIENT_ID` | Yes | Reddit OAuth app client ID |
| `REDDIT_CLIENT_SECRET` | Yes | Reddit OAuth app client secret |
| `REDDIT_USER_AGENT` | Yes | Reddit API user agent string |
| `REDDIT_RECENCY_DAYS` | No | Only index posts from last N days (default: 90) |
| `REDDIT_COMMENT_FETCH_MIN_UPVOTES` | No | Min upvotes to fetch comments (default: 50) |
| `DATABASE_URL` | Auto | Set by docker-compose |
| `REDIS_URL` | Auto | Set by docker-compose |

## Marketing agent

`marketing/` contains an autonomous social media content generator that pulls real TastePassport data (LocalScores, mention counts, restaurant names) and generates brand-aligned posts for Twitter, Instagram, and LinkedIn using Claude.

```bash
cd marketing
pip install -r requirements.txt

python run.py city bangkok      # Generate content for one city
python run.py daily             # Generate content for all indexed cities
python run.py schedule          # Start daily scheduler (07:00 UTC)
python run.py report            # Show content generation history
```

## Tech stack

- **API**: FastAPI + Uvicorn
- **Database**: PostgreSQL 15 + SQLAlchemy + Alembic
- **Task queue**: Celery + Redis
- **Reddit**: PRAW (official Reddit API, read-only OAuth)
- **Blog scraping**: Playwright (Chromium) + BeautifulSoup
- **AI**: Anthropic Claude API (entity extraction, enrichment, marketing content)
- **Containerisation**: Docker + Docker Compose
