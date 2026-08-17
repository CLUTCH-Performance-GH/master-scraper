"""Firecrawl verbatim-contact pass — the paid-tool upgrade.

For each company: discover team/staff/leadership/contact/location pages (Serper
site-search + known paths + sitemap), render them (Firecrawl escalation for JS),
and pull VERBATIM emails + phones + name/title blocks straight off the page.

Then merge into the company's existing roster:
  - a harvested email that matches an existing person  -> email_confidence
    becomes 'verified (company site)', and a page phone is added if missing
  - a harvested email with no match -> NEW verified contact (name/title from the
    page block; email is real, not inferred)

This converts inferred -> verified and adds direct phones, exactly the fields a
line-by-line client review cares about. Run: PYTHONPATH=. python3 jobs/firecrawl_pass.py [key ...]
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

from msc.extract import (EMAIL_RE, crawl_sitemap, extract_domain, extract_emails,
                         extract_phone, is_generic_email)
from msc.net import Firecrawl, Http, Serper, fetch_smart
from jobs.contacts_lib import (STATE_ABBR, build_email, classify_role,
                               clean_name, load_city_resolver, resolve_state)
from jobs.run_company import COMPANIES, VERIFIED_PATTERNS

_REAL_BUCKETS = {"Leadership/Exec", "Division/Regional Leadership", "Sales",
                 "Agronomy", "Retail/Branch Ops", "Technical/Specialty"}
_NAMEY = {"Unknown title (review)", "Unclassified (review)"}
_TEAM_URL = ("team", "leader", "staff", "people", "/about", "management", "our-")
# nav/marketing local-parts that look like emails but aren't people
_BAD_LP = ("search", "product", "expertise", "insight", "career", "member",
           "featured", "enhanced", "about", "privacy", "legal", "example", "test",
           "noreply", "donotreply", "webmaster", "subscribe", "newsletter", "media")


def _personal_lp(e):
    lp = e.split("@")[0].lower()
    return len(lp) >= 3 and not any(b in lp for b in _BAD_LP)


# a real job title contains one of these role nouns (categories like "Crop
# Protection" / "Plant Nutrition" do NOT -> they get rejected)
_ROLE_NOUN = re.compile(
    r"\b(president|vice president|\bvp\b|svp|evp|chief|\bceo\b|\bcfo\b|\bcoo\b|"
    r"director|manager|agronomist|consultant|advisor|adviser|representative|"
    r"specialist|supervisor|\blead\b|officer|owner|sales|merchandiser|originator|"
    r"superintendent|foreman|buyer|partner)\b", re.I)
# nav/section/category words that masquerade as a first name
_NAME_STOP = {"our", "the", "product", "products", "about", "meet", "featured",
              "crop", "plant", "seed", "digital", "agronomic", "solutions",
              "resources", "enhanced", "expertise", "insights", "view", "learn",
              "read", "contact", "find", "home", "search", "news", "careers"}


def _name_ok(name):
    if not _NAME_RE.match(name or ""):
        return False
    if name.split()[0].lower() in _NAME_STOP:
        return False
    return classify_role(name)[0] in _NAMEY


def _is_person_name(name, aliases):
    if not _name_ok(name):
        return False
    return not any(a.split()[0].lower() in name.lower() for a in aliases)

GAZ = Path("data/us_cities.csv")
KNOWN_PATHS = ["team", "our-team", "meet-the-team", "staff", "our-staff", "leadership",
               "our-people", "people", "management", "about/leadership", "about/team",
               "about-us/leadership", "about-us/our-team", "agronomy", "locations",
               "contact", "contact-us", "employees", "directory", "our-locations"]

_NAME_RE = re.compile(r"^[#>*_\s|\-]*([A-Z][a-zA-Z.'\-]+(?:\s+[A-Z][a-zA-Z.'\-]+){1,2})[*_\s|]*$")
_NAME_KEY = lambda s: re.sub(r"[^a-z]", "", (s or "").lower())


def _to_text(s):
    """HTML -> newline-separated text so the line parser works on static pages.
    (Firecrawl already returns markdown; raw HTTP returns HTML.)"""
    if "<" in s and ">" in s and ("</" in s or "/>" in s):
        try:
            from bs4 import BeautifulSoup
            return BeautifulSoup(s, "lxml").get_text("\n")
        except Exception:
            return s
    return s


def discover_pages(serper, cfg):
    domain = cfg["email_domain"]
    urls = {f"https://{domain}/{p}" for p in KNOWN_PATHS}
    urls.add(f"https://{domain}/")
    for q in (f'site:{domain} team OR staff OR leadership OR "our people" OR employees OR directory',
              f'site:{domain} agronomist OR agronomy OR "sales" OR field staff',
              f'site:{domain} contact OR locations OR "our team"'):
        for it in serper.organic(q, num=20):
            link = (it.get("link") or "").split("?")[0]
            if domain in link:
                urls.add(link)
    # sitemap: add team/contact/people/location pages
    try:
        for u in crawl_sitemap(f"https://{domain}/sitemap.xml", Http(), max_sitemaps=8):
            if any(k in u.lower() for k in ("team", "staff", "people", "leader",
                                            "contact", "location", "agronom", "employee")):
                urls.add(u.split("?")[0])
    except Exception:
        pass
    # team/leadership/contact first (budget is ample -> go deep)
    ranked = sorted(urls, key=lambda u: (0 if any(t in u.lower() for t in _TEAM_URL)
                                         else 1 if "contact" in u.lower() else 2))
    return ranked[:60]


def _nearest_phone(lines, i):
    for k in range(max(i - 4, 0), min(i + 5, len(lines))):
        ph = extract_phone(lines[k])
        if ph:
            return ph
    return ""


def parse_blocks(md, domain, team_page=False):
    """Pull (name, title, email, phone) blocks from rendered markdown/text.

    Two modes: email-anchored (any page) and name+title-anchored (team/leadership
    pages only — adds leaders the company publishes without an email)."""
    lines = [l.strip() for l in _to_text(md or "").splitlines()]
    out, seen = [], set()
    # mode 1: anchored on a verbatim @domain email
    for i, line in enumerate(lines):
        for e in extract_emails(line):
            if extract_domain(e) != domain or is_generic_email(e):
                continue
            name, title = "", ""
            for j in range(i, max(i - 8, -1), -1):
                ctx = lines[j].strip("#>*_ |-")
                if not ctx or extract_emails(ctx):
                    continue
                if not title:
                    bucket, is_ag = classify_role(ctx)
                    if is_ag and bucket in _REAL_BUCKETS and len(ctx) < 60:
                        title = ctx
                if not name and _NAME_RE.match(ctx) and classify_role(ctx)[0] in _NAMEY:
                    name = ctx
            out.append({"name": name, "title": title, "email": e.lower(),
                        "phone": _nearest_phone(lines, i)})
            seen.add(_NAME_KEY(name))
    # mode 2: name + adjacent REAL job title (role noun required) on team pages
    if team_page:
        for i, line in enumerate(lines):
            title = line.strip("#>*_ |-")
            if not (2 < len(title) < 60 and _ROLE_NOUN.search(title)):
                continue
            bucket, is_ag = classify_role(title)
            if not (is_ag and bucket in _REAL_BUCKETS):
                continue
            for j in range(i - 1, max(i - 4, -1), -1):
                cand = lines[j].strip("#>*_ |-")
                if _name_ok(cand):
                    if _NAME_KEY(cand) in seen:
                        break
                    seen.add(_NAME_KEY(cand))
                    out.append({"name": cand, "title": title, "email": "",
                                "phone": _nearest_phone(lines, i)})
                    break
    return out


def reverse_name(localpart, pattern):
    """Best-effort display name from an email local part + known pattern."""
    lp = re.sub(r"\d+$", "", localpart)
    if pattern == "first.last" and "." in lp:
        a, b = lp.split(".", 1)
        return f"{a.title()} {b.title()}"
    if pattern == "first_last" and "_" in lp:
        a, b = lp.split("_", 1)
        return f"{a.title()} {b.title()}"
    if pattern == "first":
        return lp.title()
    return ""  # flast/lastf/last -> ambiguous, leave blank


def run(keys):
    http, serper, fc = Http(), Serper(), Firecrawl()
    city2states = load_city_resolver(GAZ)
    today = date.today().isoformat()
    for key in keys:
        cfg = COMPANIES[key]
        domain = cfg["email_domain"]
        pattern = VERIFIED_PATTERNS.get(key, "first.last")
        pf = Path(f"output/contacts/{key}.json")
        rows = json.loads(pf.read_text()) if pf.exists() else []
        by_email = {r["email"].lower(): r for r in rows if r.get("email")}
        by_name = {_NAME_KEY(r["name"]): r for r in rows}

        pages = discover_pages(serper, cfg)
        harvested, site_emails, fetched, fc_used0 = {}, {}, 0, fc.credits_spent
        for u in pages:
            team = any(t in u.lower() for t in _TEAM_URL)
            method, text = fetch_smart(u, http, firecrawl=fc)  # HTTP first, Firecrawl if JS
            if method == "failed" or not text:
                continue
            fetched += 1
            for e in extract_emails(text):  # raw incl. mailto: links
                if extract_domain(e) == domain and not is_generic_email(e):
                    site_emails.setdefault(e.lower(), u)
            for blk in parse_blocks(text, domain, team_page=team):
                k = blk["email"] or _NAME_KEY(blk["name"])
                if not k:
                    continue
                cur = harvested.get(k, {})
                harvested[k] = {**cur, **{kk: vv for kk, vv in blk.items() if vv}, "url": u}

        mx_cache = {}
        verified, new, phones = 0, 0, 0
        # position-independent: any site email matching an existing inferred email -> verified
        for e, u in site_emails.items():
            if not _personal_lp(e):
                continue
            tgt = by_email.get(e)
            if tgt and not str(tgt.get("email_confidence", "")).startswith("verified"):
                tgt["email_confidence"] = "verified (company site)"
                if u not in tgt.get("source_url", ""):
                    tgt["source_url"] = (tgt.get("source_url", "") + " ; " + u).strip(" ;")
                verified += 1
        for k, blk in harvested.items():
            name, email = blk.get("name", ""), blk.get("email", "")
            if email and not _personal_lp(email):
                continue  # nav/marketing mailbox, not a person
            tgt = (by_email.get(email) if email else None) or by_name.get(_NAME_KEY(name))
            if tgt:
                if email:
                    tgt["email"], tgt["email_confidence"] = email, "verified (company site)"
                    verified += 1
                if blk.get("phone") and (not tgt.get("phone") or "branch" in tgt.get("phone_type", "")):
                    tgt["phone"], tgt["phone_type"] = blk["phone"], "direct (site)"
                    phones += 1
                # site gives a real title for someone we had in a review bucket
                if blk.get("title") and str(tgt.get("role_category", "")).endswith("(review)"):
                    b2, _ = classify_role(blk["title"], cfg["aliases"])
                    if b2 in _REAL_BUCKETS:
                        tgt["title"], tgt["role_category"] = blk["title"], b2
                if blk["url"] not in tgt.get("source_url", ""):
                    tgt["source_url"] = (tgt.get("source_url", "") + " ; " + blk["url"]).strip(" ;")
            else:
                if not _is_person_name(name, cfg["aliases"]):
                    continue  # blank / company / section label -> not a real new contact
                bucket, is_ag = classify_role(blk.get("title", ""), cfg["aliases"])
                if not is_ag:
                    continue
                dn, first, last, certs = clean_name(name)
                if email:
                    ce, econf = email, "verified (company site)"
                else:
                    ce, _ = build_email(first, last, cfg["email_domain"], pattern, http, mx_cache)
                    econf = "inferred (verified pattern)" if ce else ""
                rows.append({
                    "company": cfg["company"], "state": "", "state_confidence": "unconfirmed (site)",
                    "city": "", "name": dn, "title": blk.get("title", ""), "role_category": bucket,
                    "email": ce, "email_confidence": econf,
                    "phone": blk.get("phone", ""), "phone_type": "direct (site)" if blk.get("phone") else "",
                    "linkedin": "", "source_url": blk["url"], "date": today,
                    "notes": "name/title from company website",
                })
                new += 1
                if blk.get("phone"):
                    phones += 1
        pf.write_text(json.dumps(rows, indent=1))
        print(f"[{key:14}] pages_fetched={fetched:3} fc_credits={fc.credits_spent - fc_used0:3} "
              f"| emails_harvested={len(harvested):3} -> verified_upgrades={verified:3} "
              f"new_contacts={new:3} phones_added={phones:3}")
    print(f"\nTOTAL firecrawl credits this pass: {fc.credits_spent} | serper: {serper.queries_spent}")


if __name__ == "__main__":
    keys = sys.argv[1:] or list(COMPANIES.keys())
    run(keys)
