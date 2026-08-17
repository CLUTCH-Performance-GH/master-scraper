"""Reusable Source factories — the common discovery patterns as one-liners.

Instead of hand-writing discover()/parse() for every scrape, compose these:

    sitemap_source("Acme", "dealer", "https://acme.com/sitemap_index.xml",
                   url_filter="/store/", ld_type="LocalBusiness")
    places_source("hardscape contractor", metros=["Portland OR", "Seattle WA"],
                  audience="contractor")
    serper_site_source("Belgard", "contractor", "belgard.com", states=[...])
"""

from __future__ import annotations

import re

from .extract import (crawl_sitemap, extract_domain, extract_json_ld,
                      extract_phone, parse_local_business)
from .pipeline import Context, Source

_STATE_TAIL = re.compile(r"\b([A-Z]{2})\b\s*$")


# ---------------------------------------------------------------------------
# Sitemap + JSON-LD (the CLS location pattern)
# ---------------------------------------------------------------------------

def sitemap_source(name: str, audience: str, sitemap_url: str,
                   url_filter: str = "", ld_type: str = "LocalBusiness") -> Source:
    """Discover pages from a sitemap, parse each via JSON-LD."""
    def discover(ctx: Context) -> list[dict]:
        urls = crawl_sitemap(sitemap_url, ctx.http)
        if url_filter:
            urls = [u for u in urls if url_filter in u]
        return [{"url": u} for u in urls]

    def parse(item: dict, text: str, ctx: Context) -> list[dict]:
        blocks = extract_json_ld(text, type_filter=ld_type)
        if not blocks:
            return []
        row = parse_local_business(blocks[0])
        row["company_name"] = row.pop("name", "")
        row["website"] = item["url"]
        return [row]

    return Source(name=name, audience=audience, source_url=sitemap_url,
                  discover=discover, parse=parse)


# ---------------------------------------------------------------------------
# Google Places via Serper (location discovery where no sitemap exists)
# ---------------------------------------------------------------------------

def places_source(query: str, metros: list[str], audience: str,
                  name: str = "", per_metro: int = 20) -> Source:
    """Discover local businesses by querying Google Places per metro via Serper.

    Uses your existing Serper credits (the `places` endpoint) — no new key.
    Returns rows directly from the API; no page fetch needed.
    """
    label = name or f"places:{query}"

    def discover(ctx: Context) -> list[dict]:
        rows = []
        for metro in metros:
            data = ctx.serper.query(f"{query} in {metro}", num=per_metro,
                                    endpoint="places")
            for p in data.get("places", []):
                state = ""
                m = _STATE_TAIL.search(p.get("address", ""))
                if m:
                    state = m.group(1)
                rows.append({
                    "company_name": p.get("title", ""),
                    "street": p.get("address", ""),
                    "state": state,
                    "phone": p.get("phoneNumber", ""),
                    "website": p.get("website", ""),
                    "rating": p.get("rating", ""),
                    "reviews": p.get("ratingCount", ""),
                    "category": p.get("category", ""),
                    "source_url": p.get("website", "") or p.get("cid", ""),
                    "metro": metro,
                })
        return rows

    def parse(item: dict, text: str, ctx: Context) -> list[dict]:
        return [item]  # already a full row

    return Source(name=label, audience=audience, source_url="https://google.com/maps",
                  discover=discover, parse=parse, fetch_items=False)


# ---------------------------------------------------------------------------
# Serper site-search (the HNA authorized-dealer pattern)
# ---------------------------------------------------------------------------

def serper_site_source(name: str, audience: str, domain: str,
                       states: list[str], query_template: str = '{state}') -> Source:
    """Discover companies from a JS-rendered locator by site-searching it on
    Google. query_template may use {state} and {domain}."""
    def discover(ctx: Context) -> list[dict]:
        seen, rows = set(), []
        for state in states:
            q = f"site:{domain} " + query_template.format(state=state, domain=domain)
            for item in ctx.serper.organic(q, num=15):
                link = item.get("link", "")
                title = item.get("title", "").split(" - ")[0].split(" | ")[0].strip()
                snippet = item.get("snippet", "")
                key = (title.lower(), state)
                if not title or key in seen:
                    continue
                seen.add(key)
                rows.append({
                    "company_name": title,
                    "state": state,
                    "phone": extract_phone(snippet) or "",
                    "website": link if extract_domain(link) != domain else "",
                    "source_url": link,
                })
        return rows

    def parse(item: dict, text: str, ctx: Context) -> list[dict]:
        return [item]

    return Source(name=name, audience=audience,
                  source_url=f"https://{domain}/", discover=discover,
                  parse=parse, fetch_items=False)
