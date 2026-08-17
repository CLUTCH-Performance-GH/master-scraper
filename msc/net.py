"""Network layer: cached HTTP, Serper, Firecrawl — with the cost-escalation ladder.

fetch_smart() is the workhorse. It tries the cheapest path that works:

    0. SQLite cache                          (free)
    1. plain requests + retries              (free)
    2. Firecrawl (JS rendering, 403 bypass)  (~1 credit)

Serper and Firecrawl clients both carry hard per-run budgets so a runaway loop
can never drain an account (ported from the HNA Firecrawl client, which was the
only one of the three projects that had this).
"""

import logging
import random
import threading
import time
from typing import Optional

import requests

from . import cache, settings

log = logging.getLogger("msc.net")


class BudgetExceeded(RuntimeError):
    pass


class _RateLimiter:
    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            delta = time.time() - self._last
            if delta < self.min_interval:
                time.sleep(self.min_interval - delta)
            self._last = time.time()


# ---------------------------------------------------------------------------
# Plain HTTP
# ---------------------------------------------------------------------------

class Http:
    """requests.Session with retries, backoff, rate limiting, and caching."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = settings.USER_AGENT
        self._limiter = _RateLimiter(settings.HTTP_RATE_LIMIT_SECONDS)

    def get(self, url: str, ttl_days: Optional[float] = settings.DEFAULT_CACHE_TTL_DAYS,
            **kwargs) -> Optional[dict]:
        """GET a URL. Returns {"status", "text", "url"} or None on hard failure."""
        cached = cache.get("http", url, ttl_days=ttl_days) if ttl_days else None
        if cached is not None:
            return cached

        for attempt in range(settings.HTTP_MAX_RETRIES):
            self._limiter.wait()
            try:
                resp = self.session.get(url, timeout=settings.HTTP_TIMEOUT, **kwargs)
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(f"retryable status {resp.status_code}")
                result = {"status": resp.status_code, "text": resp.text, "url": resp.url}
                cache.put("http", url, value=result)
                return result
            except (requests.RequestException, requests.HTTPError) as exc:
                wait = min(2 ** attempt + random.random(), 20)
                log.warning("GET %s failed (%s), retry %d in %.1fs", url, exc, attempt + 1, wait)
                time.sleep(wait)
        return None


# ---------------------------------------------------------------------------
# Serper (Google search — ~$0.0003 per query)
# ---------------------------------------------------------------------------

class Serper:
    """Serper.dev client with retries, budget, and 30-day result caching."""

    ENDPOINTS = {"search", "news", "places", "images", "maps"}

    def __init__(self, api_key: str = ""):
        self.api_key = api_key or settings.SERPER_API_KEY
        self.queries_spent = 0
        self._limiter = _RateLimiter(settings.SERPER_RATE_LIMIT_SECONDS)

    def query(self, q: str, num: int = 10, endpoint: str = "search",
              gl: str = "us", ttl_days: float = 30, **extra) -> dict:
        if endpoint not in self.ENDPOINTS:
            raise ValueError(f"unknown serper endpoint: {endpoint}")
        payload = {"q": q, "num": num, "gl": gl, "hl": "en", **extra}

        cached = cache.get("serper", endpoint, payload, ttl_days=ttl_days)
        if cached is not None:
            return cached

        if self.queries_spent >= settings.SERPER_RUN_BUDGET:
            raise BudgetExceeded(f"serper run budget {settings.SERPER_RUN_BUDGET} spent")

        for attempt in range(settings.HTTP_MAX_RETRIES):
            self._limiter.wait()
            try:
                resp = requests.post(
                    f"https://google.serper.dev/{endpoint}",
                    headers={"X-API-KEY": self.api_key, "Content-Type": "application/json"},
                    json=payload, timeout=20,
                )
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise requests.HTTPError(f"retryable status {resp.status_code}")
                resp.raise_for_status()
                data = resp.json()
                self.queries_spent += 1
                cache.put("serper", endpoint, payload, value=data)
                return data
            except (requests.RequestException, ValueError) as exc:
                wait = min(2 ** attempt + random.random(), 20)
                log.warning("serper %r failed (%s), retry in %.1fs", q, exc, wait)
                time.sleep(wait)
        return {}

    def organic(self, q: str, num: int = 10, **kw) -> list[dict]:
        return self.query(q, num=num, **kw).get("organic", [])

    def first_url(self, q: str, preferred_domain: str = "",
                  skip_domains: tuple = ("google.com", "youtube.com", "webcache.",
                                         "amazon.com", "ebay.com")) -> str:
        results = self.organic(q, num=8)
        if preferred_domain:
            for item in results:
                link = item.get("link", "")
                if preferred_domain in link:
                    return link
        for item in results:
            link = item.get("link", "")
            if link and not any(d in link for d in skip_domains):
                return link
        return ""


# ---------------------------------------------------------------------------
# Firecrawl (JS rendering + 403 bypass — ~1 credit per page, use last)
# ---------------------------------------------------------------------------

class Firecrawl:
    """Firecrawl v1 scrape client with account floor + per-run budget."""

    def __init__(self, api_key: str = ""):
        self.api_key = api_key or settings.FIRECRAWL_API_KEY
        self.credits_spent = 0
        self._limiter = _RateLimiter(settings.FIRECRAWL_RATE_LIMIT_SECONDS)

    def _check_budget(self) -> None:
        if self.credits_spent >= settings.FIRECRAWL_RUN_BUDGET:
            raise BudgetExceeded(f"firecrawl run budget {settings.FIRECRAWL_RUN_BUDGET} spent")

    def scrape(self, url: str, formats: tuple = ("markdown",), wait_for: int = 2000,
               ttl_days: Optional[float] = settings.DEFAULT_CACHE_TTL_DAYS) -> Optional[dict]:
        """Scrape a URL. Returns {"markdown", "html", "metadata"} subset or None."""
        cached = cache.get("firecrawl", url, list(formats), ttl_days=ttl_days) if ttl_days else None
        if cached is not None:
            return cached

        self._check_budget()
        for attempt in range(settings.HTTP_MAX_RETRIES):
            self._limiter.wait()
            try:
                resp = requests.post(
                    "https://api.firecrawl.dev/v1/scrape",
                    headers={"Authorization": f"Bearer {self.api_key}",
                             "Content-Type": "application/json"},
                    json={"url": url, "formats": list(formats), "waitFor": wait_for},
                    timeout=60,
                )
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise requests.HTTPError(f"retryable status {resp.status_code}")
                resp.raise_for_status()
                body = resp.json()
                self.credits_spent += 1
                data = body.get("data", {}) or {}
                result = {k: data.get(k) for k in ("markdown", "html", "metadata") if data.get(k)}
                if result:
                    cache.put("firecrawl", url, list(formats), value=result)
                return result or None
            except (requests.RequestException, ValueError) as exc:
                wait = min(2 ** attempt + random.random(), 20)
                log.warning("firecrawl %s failed (%s), retry in %.1fs", url, exc, wait)
                time.sleep(wait)
        return None


# ---------------------------------------------------------------------------
# The escalation ladder
# ---------------------------------------------------------------------------

_JS_SHELL_MARKERS = ("enable javascript", "you need to enable javascript",
                     "<div id=\"root\"></div>", "<div id=\"app\"></div>",
                     "cf-browser-verification", "just a moment...")


def looks_like_js_shell(html: str) -> bool:
    """True when a page is an empty SPA shell or a bot wall — needs Firecrawl."""
    if len(html) < 1500:
        return True
    lower = html.lower()
    return any(m in lower for m in _JS_SHELL_MARKERS)


def fetch_smart(url: str, http: Http, firecrawl: Optional[Firecrawl] = None,
                force_render: bool = False) -> tuple[str, str]:
    """Fetch a page as text via the cheapest working method.

    Returns (method, text) where method is 'requests' | 'firecrawl' | 'failed'.
    `force_render` skips straight to Firecrawl for sites known to need JS
    (the ADAMA/Gowan/Bayer lesson from the BASF project).
    """
    if not force_render:
        result = http.get(url)
        if result and result["status"] == 200 and not looks_like_js_shell(result["text"]):
            return "requests", result["text"]
    if firecrawl:
        fc = firecrawl.scrape(url)
        if fc and fc.get("markdown"):
            return "firecrawl", fc["markdown"]
    return "failed", ""
