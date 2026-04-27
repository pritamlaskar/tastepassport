# TastePassport API

Human experience index for food discovery. No algorithms. No ads. No SEO.

## What it is

TastePassport finds the best food recommendations buried in Reddit threads and personal food blogs — not rankings, not sponsored content, not aggregators. It indexes genuine human experiences and surfaces them for anyone planning to eat well in an unfamiliar city.

Two sources only: Reddit and personal food blogs.

## Prerequisites

- Docker + Docker Compose
- An Anthropic API key

## Setup

```bash
# Clone and enter the project
cd tastepassport

# Copy env file and add your API key
cp .env.example .env
# Edit .env and set ANTHROPIC_API_KEY=your_key_here

# Build and start all 6 services
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

Database migrations run automatically on startup via Alembic. No manual setup needed.

**Verify the stack is live:**
```
GET http://localhost:8000/health
```

Expected: `{"status": "ok", "database": "ok", "redis": "ok"}`

## API Endpoints

### Health check
```
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

### Check pipeline status
```
GET /api/city/{slug}/status
```

### Get full place detail (with all mentions)
```
GET /api/place/{id}
```

### Search across places
```
GET /api/search?q=pad+thai&city=bangkok
```

### Submit feedback
```
POST /api/feedback
Content-Type: application/json
{"restaurant_id": "uuid", "feedback_type": "closed", "note": "Closed in March 2026"}
```

## Development / Admin endpoints

All admin endpoints run synchronously (useful for inspecting each stage in isolation without Celery):

```
POST /api/admin/reddit/{slug}         Run only the Reddit crawl stage
POST /api/admin/blogs/{slug}          Run only the blog discovery + scrape stage
POST /api/admin/extract/{slug}        Run only Claude entity extraction
POST /api/admin/score/{slug}          Run only the LocalScore algorithm
POST /api/admin/enrich/{slug}         Run only Claude enrichment

GET  /api/admin/sources/{slug}        List all raw sources collected
GET  /api/admin/sources/{slug}/{id}   Full text of one source
GET  /api/admin/mentions/{slug}       All extracted mentions (what Claude found)
GET  /api/admin/scores/{slug}         Restaurants with full score breakdowns
```

**Recommended workflow for testing a new city (e.g. Bangkok):**

```bash
# Run each stage individually and inspect output before proceeding
curl -X POST http://localhost:8000/api/admin/reddit/bangkok
curl http://localhost:8000/api/admin/sources/bangkok

curl -X POST http://localhost:8000/api/admin/blogs/bangkok
curl -X POST http://localhost:8000/api/admin/extract/bangkok
curl http://localhost:8000/api/admin/mentions/bangkok

curl -X POST http://localhost:8000/api/admin/score/bangkok
curl http://localhost:8000/api/admin/scores/bangkok

curl -X POST http://localhost:8000/api/admin/enrich/bangkok
curl http://localhost:8000/api/city/bangkok
```

## How the pipeline works

When you request a city, the pipeline runs 5 stages:

1. **Reddit crawling** — searches 8+ subreddits for food content, collects threads + comments
2. **Blog discovery** — DuckDuckGo scraping finds personal food blogs, classifier filters out SEO content
3. **Entity extraction** — Claude API reads every source and extracts restaurant mentions with confidence scores
4. **LocalScore** — proprietary algorithm scores 0-100 based on upvotes, authenticity, cross-source mentions, recency
5. **Enrichment** — Claude generates signature dish, price range, cuisine type, and why_it_ranks for top results

## LocalScore explained

The score (0–100) is calculated entirely from signal data. No manual boosting. Every restaurant must earn its rank.

| Signal | Points |
|--------|--------|
| Reddit upvotes 100–499 | +10 |
| Reddit upvotes 500–1999 | +20 |
| Reddit upvotes 2000+ | +35 |
| Reddit comment engagement | +5 |
| Multiple Reddit authors | +10 |
| Local city subreddit mention | +8 |
| Blog source present | +15 |
| High-authenticity blog (score ≥ 0.7) | +10 |
| Long-form blog (1000+ words) | +5 |
| Dish / location / price / time specificity | +3 each |
| Cross-source bonus (Reddit + blog) | +12 |
| Mentioned in last 6 months | +5 |
| Mentioned in last 6–18 months | +2 |
| Known chain restaurant | −20 |

Minimum to appear in results: `MIN_LOCAL_SCORE=25` and `MIN_MENTIONS=2` (configurable in `.env`).

Score breakdown is returned in every API response.

## Environment variables

See `.env.example` for full documentation of all variables.
