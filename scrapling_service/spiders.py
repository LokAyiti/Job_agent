"""Generic job-discovery spider powered by Scrapling."""
import os
import re
from typing import List, Optional, Set
from urllib.parse import urljoin, urlparse

from scrapling.fetchers import AsyncStealthySession, FetcherSession, ProxyRotator
from scrapling.spiders import Request, Response, Spider

from scrapling_service.proxy import as_dicts, as_strings, proxies_from_env


# Default job-link patterns for common ATS and career-page structures.
DEFAULT_JOB_PATTERNS = [
    r"/jobs?/\d+",
    r"/careers?/\d+",
    r"/postings?/\d+",
    r"/openings?/\d+",
    r"/job/[^/]+",
    r"/position/[^/]+",
    r"/opening/[^/]+",
    r"greenhouse\.io/[^/]+/jobs/\d+",
    r"lever\.co/[^/]+/\d+",
    r"jobs\.lever\.co/[^/]+/\d+",
    r"myworkdayjobs\.com/[^/]+/job/\d+",
    r"icims\.com/jobs/\d+",
    r"talent\.oracle\.com/[^/]+/job/[^/]+",
    r"successfactors\.com/[^/]+/job/[^/]+",
]

# Generic container selectors for job-card extraction.
JOB_CARD_SELECTORS = [
    ".job-listing",
    ".job-card",
    ".job",
    ".opening",
    ".position",
    ".posting",
    ".career-job",
    "[data-testid='job']",
    "[data-testid='job-card']",
    ".base-card",
]

# Title selectors tried inside a job card.
TITLE_SELECTORS = [
    "h1",
    "h2",
    "h3",
    "h4",
    ".title",
    ".job-title",
    ".posting-title",
    "[data-testid='job-title']",
    "a",
]


class JobDiscoverySpider(Spider):
    """Crawl company career pages and job boards to discover job postings.

    The spider is deliberately generic: it follows links that look like job
    postings and extracts job cards from common container patterns. It uses
    Scrapling's adaptive selectors so small HTML changes do not break it.
    """

    name = "job_discovery"
    robots_txt_obey = True
    concurrent_requests = 4
    max_blocked_retries = 3

    def __init__(
        self,
        start_urls: List[str],
        link_patterns: Optional[List[str]] = None,
        title_patterns: Optional[List[str]] = None,
        max_depth: int = 1,
        allowed_domains: Optional[List[str]] = None,
        use_stealth: bool = False,
        proxy_list: Optional[str] = None,
        crawldir: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.start_urls = [u.strip() for u in start_urls if u.strip()]
        self.link_patterns = [re.compile(p, re.IGNORECASE) for p in (link_patterns or DEFAULT_JOB_PATTERNS)]
        self.title_patterns = [re.compile(p, re.IGNORECASE) for p in (title_patterns or [])] if title_patterns else []
        self.max_depth = max(0, max_depth)
        self.use_stealth = use_stealth
        self.crawldir = crawldir or os.environ.get("SCRAWLDIR", "./crawl_data")
        self._seen: Set[str] = set()

        proxies = proxies_from_env() if proxy_list is None else parse_proxy_list(proxy_list)
        self.proxies = proxies
        self._allowed_domains = set(allowed_domains or [urlparse(u).netloc for u in self.start_urls])

    def configure_sessions(self, manager):
        """Set up a fast HTTP session and a lazy stealth browser session."""
        if self.proxies:
            string_proxies = as_strings(self.proxies)
            dict_proxies = as_dicts(self.proxies)
            manager.add(
                "fast",
                FetcherSession(
                    impersonate="chrome",
                    proxy_rotator=ProxyRotator(string_proxies),
                ),
            )
            manager.add(
                "stealth",
                AsyncStealthySession(
                    headless=True,
                    solve_cloudflare=True,
                    proxy_rotator=ProxyRotator(dict_proxies),
                ),
                lazy=True,
            )
        else:
            manager.add("fast", FetcherSession(impersonate="chrome"))
            manager.add(
                "stealth",
                AsyncStealthySession(headless=True, solve_cloudflare=True),
                lazy=True,
            )

    async def is_blocked(self, response: Response) -> bool:
        """Detect anti-bot blocks and rate limits."""
        if response.status in {401, 403, 407, 429, 444, 500, 502, 503, 504}:
            return True
        try:
            body = (response.body or b"").decode("utf-8", errors="ignore").lower()
        except Exception:
            body = ""
        blocked_phrases = [
            "access denied",
            "rate limit",
            "blocked",
            "please verify you are a human",
            "cloudflare",
            "captcha",
            "sorry, you have been blocked",
        ]
        return any(phrase in body for phrase in blocked_phrases)

    async def retry_blocked_request(self, request: Request, response: Response) -> Request:
        """Retry blocked requests through the stealth browser session."""
        request.sid = "stealth"
        return request

    async def parse(self, response: Response):
        """Parse a page and yield job candidates plus follow-up requests."""
        depth = response.meta.get("depth", 0)
        base_url = response.url

        # 1. Extract job cards from the current page.
        for item in self._extract_jobs_from_page(response, base_url):
            yield item

        if depth >= self.max_depth:
            return

        # 2. Follow links that look like job postings or career pages.
        for href in response.css("a::attr(href)").getall():
            if not href:
                continue
            absolute = urljoin(base_url, href)
            if not self._should_follow(absolute):
                continue

            if self._looks_like_job(absolute):
                # Already a job URL; yield directly with a title if available.
                title = self._link_text(response, href)
                if absolute not in self._seen:
                    self._seen.add(absolute)
                    yield self._job_item(title, absolute, base_url)
            else:
                yield Request(
                    absolute,
                    callback=self.parse,
                    meta={"depth": depth + 1},
                )

    def _should_follow(self, url: str) -> bool:
        parsed = urlparse(url)
        if not parsed.scheme.startswith("http"):
            return False
        if parsed.netloc not in self._allowed_domains:
            return False
        path = parsed.path.lower()
        noise = [
            "login", "signin", "auth", "logout", "privacy", "terms", "legal",
            "cookie", "event", "webinar", "press", "news", "blog", "contact",
            "about", "media", ".pdf", ".zip", ".jpg", ".png",
        ]
        if any(n in path for n in noise):
            return False
        return True

    def _looks_like_job(self, url: str) -> bool:
        return any(pattern.search(url) for pattern in self.link_patterns)

    def _link_text(self, response: Response, href: str) -> Optional[str]:
        """Try to get the anchor text for a discovered job link."""
        for selector in [
            f'a[href="{href}"]::text',
            f'a[href*="{href}"]::text',
        ]:
            try:
                text = response.css(selector).get("")
                if text:
                    text = text.strip()
                    if text:
                        return text
            except Exception:
                continue
        return None

    def _extract_jobs_from_page(self, response: Response, base_url: str) -> List[dict]:
        items: List[dict] = []
        for card_selector in JOB_CARD_SELECTORS:
            try:
                cards = response.css(card_selector)
            except Exception:
                continue
            for card in cards:
                title = self._first_text(card, TITLE_SELECTORS)
                if not title:
                    continue
                link = self._first_link(card, ["a"])
                if not link:
                    continue
                absolute = urljoin(base_url, link)
                if not self._looks_like_job(absolute):
                    continue
                if absolute in self._seen:
                    continue
                self._seen.add(absolute)
                if self.title_patterns and not any(p.search(title) for p in self.title_patterns):
                    continue
                items.append(self._job_item(title, absolute, base_url))
        return items

    def _first_text(self, element, selectors: List[str]) -> Optional[str]:
        for selector in selectors:
            try:
                el = element.css(selector)
                if el:
                    text = getattr(el[0], "text", "") or ""
                    if not text and hasattr(el[0], "text_content"):
                        try:
                            text = el[0].text_content()
                        except Exception:
                            text = ""
                    text = text.strip()
                    if text:
                        return text
            except Exception:
                continue
        return None

    def _first_link(self, element, selectors: List[str]) -> Optional[str]:
        for selector in selectors:
            try:
                el = element.css(selector)
                if el:
                    href = el[0].attrib.get("href", "")
                    if href:
                        return href
            except Exception:
                continue
        return None

    def _job_item(self, title: Optional[str], url: str, source_url: str) -> dict:
        return {
            "title": title or "",
            "url": url,
            "source_url": source_url,
            "platform": self._detect_platform(url),
        }

    @staticmethod
    def _detect_platform(url: str) -> Optional[str]:
        lowered = url.lower()
        if "greenhouse" in lowered:
            return "greenhouse"
        if "lever" in lowered or "jobs.lever.co" in lowered:
            return "lever"
        if "myworkdayjobs" in lowered or "workday.com" in lowered:
            return "workday"
        if "icims" in lowered or "applicantpro" in lowered:
            return "icims"
        if "governmentjobs" in lowered:
            return "governmentjobs"
        if "oracle" in lowered or "talent.oracle" in lowered:
            return "oracle"
        if "successfactors" in lowered:
            return "successfactors"
        if "taleo" in lowered:
            return "taleo"
        return None
