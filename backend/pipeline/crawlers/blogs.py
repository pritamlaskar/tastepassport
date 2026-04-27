"""
Blog crawler — Stage 2.

Discovery:  DuckDuckGo HTML endpoint (html.duckduckgo.com/html/) — no API key needed.
Filter:     BlogClassifier pre-screens URLs before any scraping.
Scraping:   Playwright renders full pages (handles JS-heavy blogs).
Extraction: BeautifulSoup strips nav/footer/ads, returns article body only.
Robots:     urllib.robotparser — properly checks disallow rules before every domain.
Storage:    raw_sources table, source_type=blog.

Target: 15–25 quality blog posts per city crawl.
"""
import re
import time
import uuid
import logging
from datetime import datetime
from urllib.parse import urlparse, urljoin
from urllib.robotparser import RobotFileParser
from typing import Optional

import requests
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from models.source import RawSource, SourceType
from utils.text import clean_text
from pipeline.processors.classifier import BlogClassifier, REJECTED_DOMAINS

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
#  DuckDuckGo search queries
# ------------------------------------------------------------------ #

DDG_QUERIES = [
    '"{city}" food blog personal',
    '"eating in {city}" blog',
    '"what I ate in {city}"',
    '"{city}" food diary',
    '"my trip to {city}" food',
    'site:substack.com "{city}" food',
]

DDG_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Tags and class patterns to strip from pages before text extraction
STRIP_TAGS = [
    "nav", "footer", "header", "aside", "script", "style",
    "form", "noscript", "iframe", "figure", "figcaption",
    "button", "input", "select", "textarea",
]

STRIP_CLASS_PATTERNS = re.compile(
    r"(sidebar|widget|advertisement|advert|\bad\b|ad-|social|share|"
    r"comment|cookie|popup|modal|newsletter|subscribe|related|"
    r"recommended|trending|popular|menu|navigation|breadcrumb)",
    re.IGNORECASE,
)

# Selector priority order for finding the main article body
ARTICLE_SELECTORS = [
    "article",
    '[class*="post-content"]',
    '[class*="entry-content"]',
    '[class*="article-body"]',
    '[class*="blog-post"]',
    '[class*="post-body"]',
    '[itemprop="articleBody"]',
    "main",
]


class BlogCrawler:
    def __init__(self, city, job, db: Session):
        self.city = city
        self.job = job
        self.db = db
        self.city_name = city.name
        self.classifier = BlogClassifier()
        self.collected = 0
        self._seen_urls: set = set()
        self._robots_cache: dict = {}

        from config import get_settings
        self.max_sources = get_settings().max_blog_sources_per_city
        self._pending_commits = 0
        self._playwright = None
        self._browser = None

    def run(self) -> int:
        candidate_urls = self._discover_urls()
        logger.info(
            f"[Blogs] Discovered {len(candidate_urls)} candidate URLs for {self.city_name}"
        )

        # Pre-filter with URL classifier, sort by score descending
        scored = []
        for url in candidate_urls:
            score = self.classifier.classify_url(url)
            if score >= 0.4:
                scored.append((url, score))
        scored.sort(key=lambda x: x[1], reverse=True)

        logger.info(
            f"[Blogs] {len(scored)} URLs passed pre-filter "
            f"(rejected {len(candidate_urls) - len(scored)})"
        )

        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            self._playwright = pw
            self._browser = pw.chromium.launch(headless=True)
            try:
                for url, pre_score in scored:
                    if self.collected >= self.max_sources:
                        break
                    try:
                        self._scrape_and_store(url, pre_score)
                    except Exception as e:
                        logger.warning(f"[Blogs] Skipping {url[:80]}: {e}")
                        continue
            finally:
                self._browser.close()
                self._browser = None
                self._playwright = None

        self._flush(force=True)
        logger.info(
            f"[Blogs] {self.city_name} — stored {self.collected} blog sources"
        )
        return self.collected

    # ------------------------------------------------------------------ #
    #  URL discovery via DuckDuckGo HTML
    # ------------------------------------------------------------------ #

    def _discover_urls(self) -> list:
        seen = set()
        results = []

        for query_template in DDG_QUERIES:
            query = query_template.format(city=self.city_name)
            try:
                urls = self._ddg_search(query)
                for url in urls:
                    if url not in seen:
                        seen.add(url)
                        results.append(url)
                time.sleep(2)
            except Exception as e:
                logger.warning(f"[Blogs] DDG search failed for '{query}': {e}")
                continue

        return results

    def _ddg_search(self, query: str) -> list:
        """
        Scrape DuckDuckGo's HTML endpoint.
        Returns up to 10 result URLs per query.

        DDG HTML page structure (html.duckduckgo.com/html/):
          <div class="result">
            <a class="result__a" href="https://...">Title</a>
            <a class="result__url" href="...">domain.com</a>
          </div>
        """
        url = "https://html.duckduckgo.com/html/"
        params = {"q": query, "kl": "us-en"}

        try:
            resp = requests.post(url, data=params, headers=DDG_HEADERS, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.warning(f"[Blogs] DDG request failed: {e}")
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        found = []

        # Primary: result__a contains the actual destination URL
        for a in soup.select("a.result__a"):
            href = a.get("href", "")
            if href.startswith("http") and "duckduckgo.com" not in href:
                found.append(href)

        # Fallback: result__url text nodes (sometimes cleaner)
        if not found:
            for a in soup.select("a.result__url"):
                href = a.get("href", "") or a.get_text(strip=True)
                if href.startswith("http") and "duckduckgo.com" not in href:
                    found.append(href)

        logger.debug(f"[Blogs] DDG '{query[:60]}' → {len(found)} URLs")
        return found[:10]

    # ------------------------------------------------------------------ #
    #  Scraping
    # ------------------------------------------------------------------ #

    def _scrape_and_store(self, url: str, pre_score: float):
        if url in self._seen_urls:
            return
        if self.db.query(RawSource).filter(RawSource.source_url == url).first():
            self._seen_urls.add(url)
            return

        parsed = urlparse(url)
        domain = parsed.netloc.replace("www.", "")

        # Hard domain reject (second check — DDG can return unexpected results)
        base = ".".join(domain.split(".")[-2:])
        if base in REJECTED_DOMAINS or domain in REJECTED_DOMAINS:
            return

        if not self._robots_allows(url):
            logger.info(f"[Blogs] robots.txt disallows {url[:80]} — skipping")
            return

        html, title = self._fetch_with_playwright(url)
        if not html:
            return

        # Reject SEO titles found after scraping
        from pipeline.processors.classifier import _SEO_TITLE
        if _SEO_TITLE.search(title or ""):
            logger.debug(f"[Blogs] Rejected by title: '{title}'")
            return

        text = self._extract_article_text(html)
        if not text or len(text) < 300:
            logger.debug(f"[Blogs] Too little text ({len(text or '')} chars): {url[:80]}")
            return

        auth_score = self.classifier.classify_content(url, title, text, pre_score)
        if auth_score < 0.40:
            logger.debug(
                f"[Blogs] Auth score {auth_score:.2f} below threshold: {url[:80]}"
            )
            return

        source = RawSource(
            id=str(uuid.uuid4()),
            city_id=self.city.id,
            crawl_job_id=self.job.id,
            source_type=SourceType.blog,
            source_url=url,
            source_domain=domain,
            title=title,
            full_text=text,
            authenticity_score=auth_score,
            word_count=len(text.split()),
            crawled_at=datetime.utcnow(),
        )
        self.db.add(source)
        self._seen_urls.add(url)
        self.collected += 1
        self._flush(force=False)
        logger.info(
            f"[Blogs] Stored (auth={auth_score:.2f}, "
            f"words={source.word_count}): {url[:80]}"
        )

    def _flush(self, force: bool = False):
        self._pending_commits += 1
        if force or self._pending_commits >= 10:
            self.db.commit()
            self._pending_commits = 0

    def _fetch_with_playwright(self, url: str) -> tuple[Optional[str], Optional[str]]:
        """Render page using the shared Playwright browser. Returns (html, page_title)."""
        try:
            from playwright.sync_api import TimeoutError as PWTimeout
            context = self._browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="en-US",
            )
            page = context.new_page()

            # Block images/media to speed up load
            page.route(
                "**/*.{png,jpg,jpeg,gif,webp,mp4,mp3,woff,woff2}",
                lambda route: route.abort(),
            )

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=12000)
                page.wait_for_timeout(500)  # let JS settle
            except PWTimeout:
                logger.warning(f"[Blogs] Page timeout: {url[:80]}")
                context.close()
                return None, None

            html = page.content()
            title = page.title()
            context.close()
            return html, title

        except Exception as e:
            logger.warning(f"[Blogs] Playwright error for {url[:80]}: {e}")
            return None, None

    # ------------------------------------------------------------------ #
    #  Article text extraction
    # ------------------------------------------------------------------ #

    def _extract_article_text(self, html: str) -> str:
        soup = BeautifulSoup(html, "lxml")

        # Remove noisy structural tags
        for tag in soup(STRIP_TAGS):
            tag.decompose()

        # Remove elements whose class/id suggests non-content
        for el in soup.find_all(True):
            cls = " ".join(el.get("class", []))
            eid = el.get("id", "")
            if STRIP_CLASS_PATTERNS.search(cls) or STRIP_CLASS_PATTERNS.search(eid):
                el.decompose()

        # Try article-specific selectors in priority order
        article = None
        for selector in ARTICLE_SELECTORS:
            article = soup.select_one(selector)
            if article:
                break

        target = article or soup.find("body") or soup
        text = target.get_text(separator=" ", strip=True)
        return clean_text(text)

    # ------------------------------------------------------------------ #
    #  Robots.txt
    # ------------------------------------------------------------------ #

    def _robots_allows(self, url: str) -> bool:
        parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"

        if base_url in self._robots_cache:
            rp = self._robots_cache[base_url]
        else:
            rp = RobotFileParser()
            rp.set_url(f"{base_url}/robots.txt")
            try:
                rp.read()
            except Exception:
                # If robots.txt is unreachable, assume allowed
                self._robots_cache[base_url] = None
                return True
            self._robots_cache[base_url] = rp

        if rp is None:
            return True

        allowed = rp.can_fetch("TastePassportBot", url)
        if not allowed:
            allowed = rp.can_fetch("*", url)
        return allowed
