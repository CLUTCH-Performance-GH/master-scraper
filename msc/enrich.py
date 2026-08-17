"""Contact enrichment ladder — cheapest source first, confidence tagged.

    1. JSON-LD / page regex on the company site      (free)
    2. Claude extraction of /about + /team + /contact pages (cents, cached)
    3. LinkedIn discovery via Serper site-search      (~$0.0003/query)
    4. Offline email inference (first.last@domain)    (free, tagged 'inferred')

Every email carries email_confidence: 'verified' (seen on a page),
'linkedin' (from a profile), or 'inferred' (pattern-constructed).
"""

from __future__ import annotations

import logging
import re

from . import llm as llm_mod
from .extract import (CONTACT_PATHS, LEADERSHIP_TITLES, extract_domain,
                      extract_emails, extract_phone, infer_emails,
                      is_generic_email)
from .net import Firecrawl, Http, fetch_smart

log = logging.getLogger("msc.enrich")

_LINKEDIN_TITLE_RE = re.compile(r"^(.{2,60}?)\s*[-–|]\s*(.{2,80}?)\s*[-–|]")


def enrich_company(row: dict, http: Http, serper, firecrawl: Firecrawl | None = None,
                   claude: llm_mod.Claude | None = None, max_pages: int = 3) -> dict:
    """Fill contact_name/title/email/phone/linkedin on a company row in place."""
    website = row.get("website", "")
    domain = extract_domain(website)

    # Rung 1+2: scrape the site's contact-ish pages once, regex first, LLM second
    if domain and not row.get("contact_name"):
        pages_text = []
        base = f"https://{domain}"
        urls = [base] + [base + p for p in CONTACT_PATHS]
        fetched = 0
        for url in urls:
            if fetched >= max_pages:
                break
            method, text = fetch_smart(url, http, firecrawl=None)  # don't burn FC credits here
            if method != "failed" and text:
                pages_text.append(text)
                fetched += 1

        combined = "\n\n".join(pages_text)[:60000]
        if combined:
            if not row.get("phone"):
                row["phone"] = extract_phone(combined) or ""
            page_emails = [e for e in extract_emails(combined)
                           if extract_domain(e) == domain and not is_generic_email(e)]
            if page_emails and not row.get("email"):
                row["email"], row["email_confidence"] = page_emails[0], "verified"

            if claude and not row.get("contact_name"):
                data = claude.extract(
                    prompt=f"Extract the people from this company website text:\n\n{combined}",
                    schema=llm_mod.CONTACT_SCHEMA,
                    system=("You extract real human contacts from business websites. "
                            "Only include actual named people, never form labels or "
                            "testimonial authors. Decision makers are owners, founders, "
                            "executives, and managers."),
                    model=claude.fast_model,
                )
                if data and data.get("contacts"):
                    best = next((c for c in data["contacts"] if c["is_decision_maker"]),
                                data["contacts"][0])
                    row["contact_name"] = best["name"]
                    row["title"] = best.get("title", "")
                    if best.get("email") and not row.get("email"):
                        row["email"], row["email_confidence"] = best["email"], "verified"

    # Rung 3: LinkedIn via Serper
    company = row.get("company_name", "")
    if company and serper and not row.get("contact_name"):
        q = f'site:linkedin.com/in "{company}"'
        for item in serper.organic(q, num=5):
            title = item.get("title", "")
            m = _LINKEDIN_TITLE_RE.match(title)
            if m and LEADERSHIP_TITLES.search(m.group(2)):
                row["contact_name"] = m.group(1).strip()
                row["title"] = m.group(2).strip()
                row["linkedin"] = item.get("link", "")
                if not row.get("email_confidence"):
                    row["email_confidence"] = "linkedin"
                break

    # Rung 4: offline inference
    name = row.get("contact_name", "")
    if name and domain and not row.get("email"):
        parts = name.split()
        if len(parts) >= 2:
            candidates = infer_emails(parts[0], parts[-1], domain)
            if candidates:
                row["email"], row["email_confidence"] = candidates[0], "inferred"
    return row
