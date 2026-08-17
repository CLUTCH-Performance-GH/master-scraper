"""Free OSINT sources — the layer that closes the gap with commercial tooling.

Everything in here costs $0 per call (all cached through msc.cache):

  wayback_snapshots()  Internet Archive CDX — how a competitor's messaging
                       changed over time (perfect for messaging audits)
  rdap_whois()         registry WHOIS via rdap.org — domain age, registrar
  mx_records()         Google DNS-over-HTTPS — does an inferred email's domain
                       actually accept mail? (kills bad inferred emails free)
  crtsh_subdomains()   certificate transparency — hidden portals, dealer
                       subdomains, staging sites a company never links to
  sec_edgar_search()   SEC full-text search — public-company filings mentioning
                       a product, brand, or competitor
  robots_sitemaps()    pull sitemap URLs straight out of robots.txt
"""

import json
import logging
from urllib.parse import quote_plus

from . import cache, settings
from .net import Http

log = logging.getLogger("msc.osint")


def wayback_snapshots(url: str, http: Http, from_year: int = 2018,
                      limit: int = 40) -> list[dict]:
    """Historical snapshots of a URL. Each: {timestamp, archive_url, status}."""
    cached = cache.get("wayback", url, from_year, limit, ttl_days=30)
    if cached is not None:
        return cached
    api = ("https://web.archive.org/cdx/search/cdx?url=" + url +
           f"&output=json&from={from_year}&filter=statuscode:200"
           f"&collapse=timestamp:6&limit={limit}")
    result = http.get(api, ttl_days=None)
    snaps = []
    if result and result["status"] == 200:
        try:
            rows = json.loads(result["text"])
            for row in rows[1:]:
                ts = row[1]
                snaps.append({"timestamp": ts,
                              "archive_url": f"https://web.archive.org/web/{ts}/{row[2]}",
                              "status": row[4]})
        except (ValueError, IndexError):
            pass
    cache.put("wayback", url, from_year, limit, value=snaps)
    return snaps


def rdap_whois(domain: str, http: Http) -> dict:
    """Domain registration facts: created date, registrar, status."""
    cached = cache.get("rdap", domain, ttl_days=90)
    if cached is not None:
        return cached
    result = http.get(f"https://rdap.org/domain/{domain}", ttl_days=None)
    out = {}
    if result and result["status"] == 200:
        try:
            data = json.loads(result["text"])
            for ev in data.get("events", []):
                if ev.get("eventAction") == "registration":
                    out["registered"] = ev.get("eventDate", "")[:10]
            for ent in data.get("entities", []):
                if "registrar" in ent.get("roles", []):
                    vcard = ent.get("vcardArray", [None, []])[1]
                    for f in vcard:
                        if f[0] == "fn":
                            out["registrar"] = f[3]
        except (ValueError, KeyError, IndexError):
            pass
    cache.put("rdap", domain, value=out)
    return out


def mx_records(domain: str, http: Http) -> list[str]:
    """MX hosts for a domain via Google DoH. Empty list = inferred emails to
    this domain are undeliverable — drop them before they hit the CRM."""
    cached = cache.get("mx", domain, ttl_days=30)
    if cached is not None:
        return cached
    result = http.get(f"https://dns.google/resolve?name={domain}&type=MX", ttl_days=None)
    hosts = []
    if result and result["status"] == 200:
        try:
            data = json.loads(result["text"])
            hosts = [a["data"].split()[-1].rstrip(".")
                     for a in data.get("Answer", []) if a.get("type") == 15]
        except (ValueError, KeyError, IndexError):
            pass
    cache.put("mx", domain, value=hosts)
    return hosts


def has_deliverable_mail(domain: str, http: Http) -> bool:
    """Quick gate for inferred emails — only keep them if the domain has MX."""
    return bool(mx_records(domain, http))


def crtsh_subdomains(domain: str, http: Http) -> list[str]:
    """Subdomains from certificate transparency logs (crt.sh). Surfaces dealer
    portals, staging hosts, and login pages a company never links publicly."""
    cached = cache.get("crtsh", domain, ttl_days=30)
    if cached is not None:
        return cached
    result = http.get(f"https://crt.sh/?q=%25.{domain}&output=json", ttl_days=None)
    subs: set[str] = set()
    if result and result["status"] == 200:
        try:
            for entry in json.loads(result["text"]):
                for name in str(entry.get("name_value", "")).splitlines():
                    name = name.strip().lstrip("*.").lower()
                    if name.endswith(domain) and "@" not in name:
                        subs.add(name)
        except (ValueError, KeyError):
            pass
    out = sorted(subs)
    cache.put("crtsh", domain, value=out)
    return out


def sec_edgar_search(query: str, http: Http, limit: int = 20) -> list[dict]:
    """SEC EDGAR full-text search. Each: {company, form, date, url}. Finds
    public-company filings naming a product, brand, or competitor."""
    cached = cache.get("edgar", query, limit, ttl_days=14)
    if cached is not None:
        return cached
    # Canonical full-text search JSON endpoint.
    result = http.get(f'https://efts.sec.gov/LATEST/search-index?q="{query}"', ttl_days=None)
    hits = []
    if result and result["status"] == 200:
        try:
            for h in json.loads(result["text"]).get("hits", {}).get("hits", [])[:limit]:
                src = h.get("_source", {})
                cik = (src.get("cik") or [""])[0] if isinstance(src.get("cik"), list) else src.get("cik", "")
                adsh = h.get("_id", "").split(":")[0]
                hits.append({
                    "company": (src.get("display_names") or [""])[0],
                    "form": src.get("file_type", ""),
                    "date": src.get("file_date", ""),
                    "url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}",
                })
        except (ValueError, KeyError, IndexError):
            pass
    cache.put("edgar", query, limit, value=hits)
    return hits


def opencorporates_search(name: str, http: Http, jurisdiction: str = "",
                          limit: int = 10) -> list[dict]:
    """Company registry search. Each: {name, company_number, jurisdiction,
    status, incorporation_date, opencorporates_url}. Authoritative legal-entity
    data — the automated version of the manual corporate-structure digging the
    CLS project did by hand to find sister facilities."""
    cached = cache.get("opencorp_search", name, jurisdiction, limit, ttl_days=30)
    if cached is not None:
        return cached
    url = (f"https://api.opencorporates.com/v0.4/companies/search"
           f"?q={quote_plus(name)}&per_page={limit}")
    if jurisdiction:
        url += f"&jurisdiction_code={jurisdiction}"
    if settings.OPENCORPORATES_API_TOKEN:
        url += f"&api_token={settings.OPENCORPORATES_API_TOKEN}"
    result = http.get(url, ttl_days=None)
    out = []
    if result and result["status"] == 200:
        try:
            for item in json.loads(result["text"]).get("results", {}).get("companies", []):
                c = item.get("company", {})
                out.append({
                    "name": c.get("name", ""),
                    "company_number": c.get("company_number", ""),
                    "jurisdiction": c.get("jurisdiction_code", ""),
                    "status": c.get("current_status", ""),
                    "incorporation_date": c.get("incorporation_date", ""),
                    "opencorporates_url": c.get("opencorporates_url", ""),
                })
        except (ValueError, KeyError):
            pass
    elif result and result["status"] == 401:
        log.warning("opencorporates needs an API token for this query "
                    "(set OPENCORPORATES_API_TOKEN in .env)")
    cache.put("opencorp_search", name, jurisdiction, limit, value=out)
    return out


def opencorporates_officers(jurisdiction: str, company_number: str,
                            http: Http) -> list[dict]:
    """Officers/directors for a company. Each: {name, position, start_date}.
    Pairs with opencorporates_search to map a company's people + parent/sub links."""
    cached = cache.get("opencorp_officers", jurisdiction, company_number, ttl_days=30)
    if cached is not None:
        return cached
    url = (f"https://api.opencorporates.com/v0.4/companies/"
           f"{jurisdiction}/{company_number}")
    if settings.OPENCORPORATES_API_TOKEN:
        url += f"?api_token={settings.OPENCORPORATES_API_TOKEN}"
    result = http.get(url, ttl_days=None)
    out = []
    if result and result["status"] == 200:
        try:
            company = json.loads(result["text"]).get("results", {}).get("company", {})
            for o in company.get("officers", []):
                off = o.get("officer", {})
                out.append({"name": off.get("name", ""),
                            "position": off.get("position", ""),
                            "start_date": off.get("start_date", "")})
        except (ValueError, KeyError):
            pass
    cache.put("opencorp_officers", jurisdiction, company_number, value=out)
    return out


def robots_sitemaps(domain: str, http: Http) -> list[str]:
    """Sitemap URLs declared in robots.txt — the fastest way to find the
    authoritative page index for GTP expected counts."""
    cached = cache.get("robots", domain, ttl_days=30)
    if cached is not None:
        return cached
    result = http.get(f"https://{domain}/robots.txt", ttl_days=None)
    sitemaps = []
    if result and result["status"] == 200:
        for line in result["text"].splitlines():
            if line.lower().startswith("sitemap:"):
                sitemaps.append(line.split(":", 1)[1].strip())
    cache.put("robots", domain, value=sitemaps)
    return sitemaps
