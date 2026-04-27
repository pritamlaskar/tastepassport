"""
Blog authenticity classifier.

Scores a blog URL and its content on likelihood of being a genuine
personal food post vs SEO content, paid placement, or aggregator.

Score: 0.0 (reject) → 1.0 (authentic).
Threshold for scraping: >= 0.40
Threshold for storing:  >= 0.40 (re-evaluated post-scrape)

Two-pass design:
  classify_url()     — fast pre-filter before any HTTP request
  classify_content() — full content analysis after scraping
"""
import re
from urllib.parse import urlparse


# ------------------------------------------------------------------ #
#  Domain lists
# ------------------------------------------------------------------ #

PERSONAL_PLATFORMS = {
    "blogspot.com", "wordpress.com", "medium.com", "substack.com",
    "tumblr.com", "ghost.io", "beehiiv.com", "typepad.com",
    "squarespace.com", "wixsite.com",
}

REJECTED_DOMAINS = {
    "tripadvisor.com", "tripadvisor.co.uk", "tripadvisor.com.au",
    "yelp.com", "yelp.ca", "yelp.co.uk",
    "zomato.com", "thefork.com", "opentable.com",
    "eater.com", "timeout.com", "timeout.co.uk",
    "lonelyplanet.com", "fodors.com", "frommers.com",
    "viator.com", "booking.com", "expedia.com", "airbnb.com",
    "cntraveler.com", "forbes.com", "buzzfeed.com", "thrillist.com",
    "foodnetwork.com", "bonappetit.com", "seriouseats.com",
    "chowhound.com", "zagat.com", "infatuation.com",
    "restaurantguru.com", "happycow.net",
    "google.com", "maps.google.com", "wikipedia.org",
    "youtube.com", "instagram.com", "facebook.com",
    "twitter.com", "tiktok.com", "pinterest.com",
}

# ------------------------------------------------------------------ #
#  Regex patterns
# ------------------------------------------------------------------ #

_SEO_URL = re.compile(
    r"/(top-\d+|best-\d+|\d+-best|must-visit|travel-guide|"
    r"complete-guide|ultimate-guide|top-restaurants|best-restaurants"
    r"|where-to-eat-guide|food-guide)/",
    re.IGNORECASE,
)

_PERSONAL_URL = re.compile(
    r"/(20\d{2}/\d{2}|my-trip|i-ate|food-diary|ate-my-way|"
    r"eating-in|tasting|day-\d+|week-\d+|visited)/",
    re.IGNORECASE,
)

_SEO_TITLE = re.compile(
    r"^(top \d+|best \d+|\d+ best|must.?try|must.?visit|"
    r"ultimate guide|complete guide|where to eat in|"
    r"best restaurants in|top restaurants in)",
    re.IGNORECASE,
)

_FIRST_PERSON = re.compile(
    r"\b(I |I've |I went|I ate|I visited|I tried|I ordered|"
    r"we went|we ate|we visited|we tried|we ordered|"
    r"my trip|my meal|my visit|our trip|our meal)\b",
)

_NARRATIVE = re.compile(
    r"(we ordered|I had the|the waiter|the chef|the owner|"
    r"sitting at|we were seated|I remember|it was worth|"
    r"would (definitely )?recommend|go back|would return|"
    r"the line was|we waited|cash only|hidden in|tucked away|"
    r"locals only|no english menu|pointed at the menu|"
    r"the lady at the stall|the guy behind the counter)",
    re.IGNORECASE,
)

_PRICE_SIGNAL = re.compile(
    r"(\$\d+|£\d+|€\d+|¥\d+|\d+\s*(baht|yen|won|rupee|peso|ringgit|dong|dirham)"
    r"|\bprice\b|\bcost\b|\bcheap\b|\bexpensive\b|\bworth it\b|\bvalue\b)",
    re.IGNORECASE,
)

_TIME_OF_DAY = re.compile(
    r"\b(morning|afternoon|evening|night|breakfast|lunch|dinner|"
    r"late.?night|midnight|brunch|supper|street food hours)\b",
    re.IGNORECASE,
)

_LOCATION_DETAIL = re.compile(
    r"\b(alley|lane|street|road|district|neighbourhood|neighborhood|"
    r"market|station|corner|floor|building|upstairs|basement|counter)\b",
    re.IGNORECASE,
)


class BlogClassifier:

    def classify_url(self, url: str) -> float:
        """Fast pre-filter — no HTTP request needed. Returns 0.0–1.0."""
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace("www.", "")
        path = parsed.path.lower()

        # Hard reject — known bad domains
        base = ".".join(domain.split(".")[-2:])
        if base in REJECTED_DOMAINS or domain in REJECTED_DOMAINS:
            return 0.0

        # Hard reject — SEO URL patterns
        if _SEO_URL.search(path):
            return 0.1

        score = 0.5

        # Personal platform boost
        if any(domain.endswith(p) for p in PERSONAL_PLATFORMS):
            score += 0.2

        # Date-based URL path = personal post signal
        if _PERSONAL_URL.search(path):
            score += 0.15

        # Numeric list in URL slug (e.g. /10-restaurants-...)
        if re.search(r"/\d{1,2}-[a-z]", path):
            score -= 0.2

        return max(0.0, min(1.0, score))

    def classify_content(
        self, url: str, title: str, text: str, base_score: float
    ) -> float:
        """Full content analysis — called after scraping. Returns 0.0–1.0."""
        score = base_score
        title = title or ""

        # --- Title signals ---
        if _SEO_TITLE.search(title):
            score -= 0.35

        if re.search(r"(personal|diary|journal|my|our)\b", title, re.IGNORECASE):
            score += 0.1

        # --- First-person writing ---
        fp_count = len(_FIRST_PERSON.findall(text))
        if fp_count >= 8:
            score += 0.25
        elif fp_count >= 4:
            score += 0.15
        elif fp_count >= 1:
            score += 0.07
        else:
            score -= 0.20  # no first person at all — suspicious

        # --- Narrative / experiential language ---
        narr_count = len(_NARRATIVE.findall(text))
        if narr_count >= 5:
            score += 0.20
        elif narr_count >= 2:
            score += 0.10
        elif narr_count >= 1:
            score += 0.05

        # --- Word count depth ---
        word_count = len(text.split())
        if word_count >= 2000:
            score += 0.15
        elif word_count >= 1000:
            score += 0.08
        elif word_count >= 500:
            score += 0.03
        else:
            score -= 0.15

        # --- Specificity signals ---
        if len(_PRICE_SIGNAL.findall(text)) >= 2:
            score += 0.08
        if _TIME_OF_DAY.search(text):
            score += 0.05
        if len(_LOCATION_DETAIL.findall(text)) >= 2:
            score += 0.07

        # --- Spam / listicle signals ---
        h2_count = text.count("\n#") + text.count("\n##")
        if h2_count >= 8:
            score -= 0.15  # excessive headings = listicle

        return max(0.0, min(1.0, score))
