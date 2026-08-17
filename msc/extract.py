"""Extraction library — every regex and parser from the three projects, in one place.

The HNA project had the email regex defined in 3 files, the phone regex in 3,
and the domain parser in 4. This module is now the single source of truth.
"""

import json
import re
import xml.etree.ElementTree as ET
from typing import Optional
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Contact info
# ---------------------------------------------------------------------------

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+?1[\s\-\.]?)?\(?([2-9]\d{2})\)?[\s\-\.]?(\d{3})[\s\-\.]?(\d{4})")
STATE_RE = re.compile(r"^[A-Z]{2}$")

GENERIC_MAILBOXES = {"info", "sales", "contact", "office", "admin", "hello",
                     "support", "service", "inquiries", "mail", "webmaster", "noreply"}

LEADERSHIP_TITLES = re.compile(
    r"\b(owner|owner-operator|operator|president|founder|co-?founder|ceo|cfo|coo|"
    r"principal|partner|managing director|managing partner|vice president|vp\b|"
    r"director|general manager|operations manager|sales manager|project manager|"
    r"estimator|landscape architect|design director|agronomist)\b", re.I)

CONTACT_PATHS = ("/about", "/about-us", "/team", "/our-team", "/meet-the-team",
                 "/staff", "/contact", "/contact-us", "/leadership", "/people")

_COMPANY_SUFFIX_RE = re.compile(
    r"\b(llc|l\.l\.c\.|inc\.?|incorporated|corp\.?|corporation|co\.?|company|"
    r"ltd\.?|limited|lp|llp|pllc|pc)\b\.?\s*$", re.I)


def extract_phone(text: str) -> Optional[str]:
    m = PHONE_RE.search(text or "")
    return f"({m.group(1)}) {m.group(2)}-{m.group(3)}" if m else None


def extract_emails(text: str) -> list[str]:
    return sorted({e.lower() for e in EMAIL_RE.findall(text or "")})


def is_generic_email(email: str) -> bool:
    return email.split("@")[0].lower() in GENERIC_MAILBOXES


def extract_domain(url_or_email: str) -> str:
    s = (url_or_email or "").strip().lower()
    if "@" in s and "://" not in s:
        return s.rsplit("@", 1)[-1]
    if "://" not in s:
        s = "https://" + s
    host = urlparse(s).netloc
    return host.removeprefix("www.")


def normalize_company(name: str) -> str:
    """Lowercase, strip legal suffixes and punctuation — for dedup keys."""
    key = _COMPANY_SUFFIX_RE.sub("", (name or "").lower())
    key = re.sub(r"[^a-z0-9 ]+", "", key)
    return re.sub(r"\s+", " ", key).strip()


def infer_emails(first: str, last: str, domain: str) -> list[str]:
    """Candidate patterns, most-likely first. Tag results email_confidence='inferred'."""
    f, l = first.lower().strip(), last.lower().strip()
    if not (f and l and domain):
        return []
    return [f"{f}.{l}@{domain}", f"{f}@{domain}", f"{f}{l}@{domain}", f"{f[0]}{l}@{domain}"]


# ---------------------------------------------------------------------------
# Addresses (ported from CLS build_workbooks.py — handles 4 wild formats)
# ---------------------------------------------------------------------------

_US_STATES = (
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO "
    "MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC").split()
_ADDR_TAIL_RE = re.compile(
    r"[,\s]+(" + "|".join(_US_STATES) + r")[,\s]+(\d{5})(?:-\d{4})?\b")


def split_address(addr: str) -> dict:
    """Split a free-text US address into street/city/state/zip columns."""
    out = {"street": "", "city": "", "state": "", "zip": ""}
    s = re.sub(r",?\s*(USA|United States)\s*$", "", (addr or "").strip(), flags=re.I)
    if not s:
        return out
    m = _ADDR_TAIL_RE.search(s)
    if not m:
        out["street"] = s
        return out
    out["state"], out["zip"] = m.group(1), m.group(2)
    head = s[: m.start()].strip(" ,")
    if "," in head:
        street, city = head.rsplit(",", 1)
        out["street"], out["city"] = street.strip(), city.strip()
    else:
        # "7980 E Hwy 30 Kearney" — split city on the last space run
        parts = head.rsplit(" ", 1)
        if len(parts) == 2 and not parts[1][0].isdigit():
            out["street"], out["city"] = parts[0].strip(), parts[1].strip()
        else:
            out["street"] = head
    return out


def addr_key(street: str, city: str, state: str, zipc: str) -> str:
    """Stable dedup key for a physical address (for co-location flagging)."""
    st = re.sub(r"[^a-z0-9]", "", (street or "").lower())
    return f"{st}|{(city or '').lower().strip()}|{(state or '').upper().strip()}|{(zipc or '')[:5]}"


# ---------------------------------------------------------------------------
# Structured data: JSON-LD, sitemaps
# ---------------------------------------------------------------------------

_LD_RE = re.compile(
    r"<script[^>]*type\s*=\s*['\"]application/ld\+json['\"][^>]*>(.*?)</script>",
    re.S | re.I)


def extract_json_ld(html: str, type_filter: str = "") -> list[dict]:
    """All JSON-LD blocks in a page, flattened (@graph expanded), optionally
    filtered by @type. The cheapest structured data on the web — check it first."""
    found = []
    for raw in _LD_RE.findall(html or ""):
        try:
            data = json.loads(raw.strip())
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and "@graph" in item:
                items.extend(g for g in item["@graph"] if isinstance(g, dict))
                continue
            if isinstance(item, dict):
                found.append(item)
    if type_filter:
        found = [d for d in found
                 if type_filter.lower() in str(d.get("@type", "")).lower()]
    return found


def parse_local_business(ld: dict) -> dict:
    """Flatten a schema.org LocalBusiness block into a row."""
    addr = ld.get("address") or {}
    geo = ld.get("geo") or {}
    return {
        "name": ld.get("name", ""),
        "street": addr.get("streetAddress", ""),
        "city": addr.get("addressLocality", ""),
        "state": addr.get("addressRegion", ""),
        "zip": addr.get("postalCode", ""),
        "phone": ld.get("telephone", ""),
        "lat": geo.get("latitude", ""),
        "lng": geo.get("longitude", ""),
        "hours": ld.get("openingHours", ""),
        "url": ld.get("url", ""),
    }


def sitemap_urls(sitemap_xml: str) -> tuple[list[str], list[str]]:
    """Parse a sitemap. Returns (page_urls, child_sitemap_urls).

    Sitemaps are the authoritative 'expected count' source for GTP — a company's
    sitemap is its own public claim of what exists.
    """
    pages, children = [], []
    try:
        root = ET.fromstring(sitemap_xml)
    except ET.ParseError:
        return pages, children
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    tag = root.tag.lower()
    for loc in root.findall(".//sm:loc", ns) or root.iter("loc"):
        url = (loc.text or "").strip()
        if not url:
            continue
        (children if "sitemapindex" in tag else pages).append(url)
    return pages, children


def crawl_sitemap(start_url: str, http, max_sitemaps: int = 50) -> list[str]:
    """Fetch a sitemap (or sitemap index) recursively; return all page URLs."""
    seen, queue, pages = set(), [start_url], []
    while queue and len(seen) < max_sitemaps:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        result = http.get(url)
        if not result or result["status"] != 200:
            continue
        page_urls, children = sitemap_urls(result["text"])
        pages.extend(page_urls)
        queue.extend(children)
    return pages


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

_META_DATE_RES = [
    re.compile(r'(?:property|name)\s*=\s*["\'](?:article:published_time|og:published_time|'
               r'date|dc\.date|publish-date|parsely-pub-date)["\'][^>]*content\s*=\s*["\']([^"\']+)',
               re.I),
    re.compile(r'content\s*=\s*["\']([^"\']+)["\'][^>]*(?:property|name)\s*=\s*'
               r'["\'](?:article:published_time|og:published_time)', re.I),
    re.compile(r'"datePublished"\s*:\s*"([^"]+)"'),
]
_TEXT_DATE_RE = re.compile(
    r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
    r"Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+"
    r"(\d{1,2}),?\s+(20\d{2})\b")


def extract_publish_date(html: str) -> str:
    """Best-effort publish date from meta tags, JSON-LD, then body text."""
    for pattern in _META_DATE_RES:
        m = pattern.search(html or "")
        if m:
            return m.group(1)[:10]
    m = _TEXT_DATE_RE.search(html or "")
    return f"{m.group(1)} {m.group(2)}, {m.group(3)}" if m else ""
