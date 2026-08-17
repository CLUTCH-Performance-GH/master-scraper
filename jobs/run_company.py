"""Generic per-company ag-side contact discovery driver (V2).

Pipeline per company:
  A. discover via Serper: role-boosted + CITY-level LinkedIn site-searches
  B. merge candidates per person (by LinkedIn URL)
  C. CONFIRM state via gazetteer (drop foreign + out-of-23-state)
  D. corroboration pass for still-unconfirmed people (optional, --corroborate)
  E. confirmed branch phone by (state, city); recurring web phone otherwise
  F. inferred + MX-gated email

Usage:
  PYTHONPATH=. python3 jobs/run_company.py <key> [--corroborate] [STATE ...]
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from msc.extract import extract_phone
from msc.net import Firecrawl, Http, Serper
from jobs.contacts_lib import (STATE_ABBR, TARGET_STATES, build_email, build_places_scaffold,
                               classify_role, discover_email_pattern, extract_location,
                               is_foreign_linkedin, load_city_resolver,
                               load_nutrien_scaffold, match_branch_phone,
                               parse_linkedin_result, places_phone, resolve_state)

CLS = Path("/Users/jacklumpe/Desktop/CLS Additional Data")
GAZ = Path("data/us_cities.csv")

COMPANIES = {
    "nutrien": {
        "company": "Nutrien Ag Solutions",
        "aliases": ["Nutrien Ag Solutions", "Nutrien Ag", "Nutrien"],
        "email_domain": "nutrien.com",
        "li_anchor": '"Nutrien Ag Solutions"',
        "scaffold": "nutrien",
        "pattern_queries": ['"@nutrien.com" "Nutrien Ag Solutions"',
                            '"@nutrien.com" agronomist OR sales Nutrien'],
    },
    "chs": {
        "company": "CHS Inc.",
        "aliases": ["CHS Inc", "CHS Agronomy", "CHS"],
        "email_domain": "chsinc.com",
        "li_anchor": '"CHS Inc" OR "CHS Agronomy"',
        "scaffold": "",
        "pattern_queries": ['"@chsinc.com" CHS agronomy', '"@chsinc.com" CHS sales OR agronomist'],
    },
    "simplot": {
        "company": "J.R. Simplot Company", "aliases": ["J.R. Simplot", "Simplot Grower Solutions", "Simplot"],
        "email_domain": "simplot.com", "scaffold": "",
        "li_anchor": '"Simplot"',
        "pattern_queries": ['"@simplot.com" Simplot agronomy', '"@simplot.com" Simplot sales OR agronomist'],
    },
    "helena": {
        "company": "Helena Agri-Enterprises", "aliases": ["Helena Agri", "Helena Agri-Enterprises", "Helena Chemical"],
        "email_domain": "helenaagri.com", "scaffold": "",
        "li_anchor": '"Helena Agri" OR "Helena Chemical"',
        "pattern_queries": ['"@helenaagri.com" Helena agronomy', '"@helenaagri.com" Helena sales OR agronomist'],
    },
    "wilbur_ellis": {
        "company": "Wilbur-Ellis", "aliases": ["Wilbur-Ellis", "Wilbur Ellis"],
        "email_domain": "wilburellis.com", "scaffold": "",
        "li_anchor": '"Wilbur-Ellis"',
        "pattern_queries": ['"@wilburellis.com" agronomy', '"@wilburellis.com" sales OR agronomist'],
    },
    "mcgregor": {
        "company": "The McGregor Company", "aliases": ["The McGregor Company", "McGregor Company"],
        "email_domain": "mcgregor.com", "scaffold": "",
        "li_anchor": '"McGregor Company"',
        "pattern_queries": ['"@mcgregor.com" agronomy', '"@mcgregor.com" sales OR agronomist'],
    },
    "aurora": {
        "company": "Aurora Cooperative", "aliases": ["Aurora Cooperative", "Aurora Coop"],
        "email_domain": "auroracoop.com", "scaffold": "",
        "li_anchor": '"Aurora Cooperative" OR "Aurora Coop"',
        "pattern_queries": ['"@auroracoop.com" agronomy', '"@auroracoop.com" sales OR agronomist'],
    },
    "mkc": {
        "company": "MKC (Mid Kansas Cooperative)", "aliases": ["Mid Kansas Cooperative", "MKC"],
        "email_domain": "mkcoop.com", "scaffold": "",
        "li_anchor": '"Mid Kansas Cooperative" OR "MKC"',
        "pattern_queries": ['"@mkcoop.com" agronomy', '"@mkcoop.com" sales OR agronomist'],
    },
    "skyland": {
        "company": "Skyland Grain", "aliases": ["Skyland Grain"],
        "email_domain": "skylandgrain.com", "scaffold": "",
        "li_anchor": '"Skyland Grain"',
        "pattern_queries": ['"@skylandgrain.com" agronomy', '"@skylandgrain.com" sales OR agronomist'],
    },
    "riceland": {
        "company": "Riceland Foods", "aliases": ["Riceland Foods", "Riceland"],
        "email_domain": "riceland.com", "scaffold": "",
        "li_anchor": '"Riceland Foods" OR "Riceland"',
        "pattern_queries": ['"@riceland.com" agronomy OR grain', '"@riceland.com" sales OR merchandiser'],
    },
    "producers_rice": {
        "company": "Producers Rice Mill", "aliases": ["Producers Rice Mill"],
        "email_domain": "producersrice.com", "scaffold": "",
        "li_anchor": '"Producers Rice Mill"',
        "pattern_queries": ['"@producersrice.com" rice OR grain', '"@producersrice.com" sales OR field'],
    },
    "supreme_rice": {
        "company": "Supreme Rice", "aliases": ["Supreme Rice"],
        "email_domain": "supremerice.com", "scaffold": "",
        "li_anchor": '"Supreme Rice"',
        "pattern_queries": ['"@supremerice.com" rice OR grain', '"@supremerice.com" sales OR field'],
    },
    "farmers_rice": {
        "company": "Farmers' Rice Cooperative", "aliases": ["Farmers Rice Cooperative", "Farmers' Rice Cooperative"],
        "email_domain": "farmersrice.com", "scaffold": "",
        "li_anchor": '"Farmers Rice Cooperative"',
        "pattern_queries": ['"@farmersrice.com" rice', '"@farmersrice.com" sales OR field'],
    },
    "poinsett": {
        "company": "Poinsett Rice & Grain", "aliases": ["Poinsett Rice", "Poinsett Rice & Grain"],
        "email_domain": "poinsettrice.com", "scaffold": "",
        "li_anchor": '"Poinsett Rice"',
        "pattern_queries": ['"@poinsettrice.com" rice OR grain', 'Poinsett Rice Grain Arkansas'],
    },
    "triton": {
        "company": "Triton Fumigation", "aliases": ["Triton Fumigation"],
        "email_domain": "tritonfumigation.com", "scaffold": "",
        "li_anchor": '"Triton Fumigation"',
        "pattern_queries": ['"@tritonfumigation.com" fumigation', 'Triton Fumigation rice grain'],
    },
}

ROLE_BOOSTERS = [
    "",
    'agronomist OR "crop consultant" OR "crop advisor" OR "field agronomist"',
    'sales OR seller OR "account manager" OR "sales agronomist" OR "territory manager"',
    '"branch manager" OR "location manager" OR "operations manager" OR "facility manager"',
    'division OR regional OR district OR director OR "general manager"',
    'seed OR "crop protection" OR "plant nutrition" OR "precision ag" OR "digital"',
    '"CCA" OR "certified crop" OR "field sales" OR "field rep" OR "field representative"',
    'grain OR merchandiser OR originator OR feed OR fertilizer OR chemical',
]
# budget is ample (35k Serper) -> cover most branch towns, not just the top few
CITY_QUERIES_PER_STATE = 40

# Email patterns VERIFIED against real harvested addresses matched to names
# (jobs/verify_emails.py + Serper harvest, 2026-06-25). flast = jsmith.
VERIFIED_PATTERNS = {
    "nutrien": "first.last", "chs": "first.last", "skyland": "first.last",
    "mcgregor": "first.last", "supreme_rice": "first.last", "simplot": "first.last",
    "helena": "lastf", "farmers_rice": "last", "poinsett": "first",
    "wilbur_ellis": "flast", "mkc": "flast", "aurora": "flast",
    "producers_rice": "flast", "riceland": "flast", "triton": "flast",
}
# companies where no real sample was found -> pattern is a convention guess
PATTERN_ASSUMED = {"simplot"}


def _merge_candidates(raw):
    """Group raw candidates by LinkedIn URL into one record per person."""
    by_li = {}
    for c in raw:
        li = c["linkedin"]
        if li not in by_li:
            by_li[li] = {
                "name": c["name"], "first": c["first"], "last": c["last"],
                "certs": c["certs"], "title": c["title"], "linkedin": li,
                "city": c["city"], "explicit_state": c["explicit_state"],
                "query_states": [c["query_state"]], "snippets": [c["snippet"]],
            }
            continue
        m = by_li[li]
        m["query_states"].append(c["query_state"])
        m["snippets"].append(c["snippet"])
        if len(c["title"]) > len(m["title"]):
            m["title"] = c["title"]
        if not m["city"] and c["city"]:
            m["city"] = c["city"]
        if not m["explicit_state"] and c["explicit_state"]:
            m["explicit_state"] = c["explicit_state"]
    return list(by_li.values())


def _phone_for(state, city, snippets, city_phone):
    """Confirmed branch phone by city; else a web phone that recurs (>=2x)."""
    bp = match_branch_phone(state, city, city_phone)
    if bp:
        return (bp, "branch (locator)")
    found = Counter()
    for s in snippets:
        p = extract_phone(s or "")
        if p:
            found[p] += 1
    if found:
        p, n = found.most_common(1)[0]
        if n >= 2:
            return (p, "recurring (web)")
    return ("", "")


def run_company(key, states, corroborate=False):
    cfg = COMPANIES[key]
    http, serper, fc = Http(), Serper(), Firecrawl()
    today = date.today().isoformat()
    mx_cache = {}
    city2states = load_city_resolver(GAZ)
    if cfg["scaffold"] == "nutrien":
        city_counts, city_phone = load_nutrien_scaffold(CLS / "nutrien_all.json")
    else:
        city_counts, city_phone = build_places_scaffold(serper, cfg["aliases"], states)
        print(f"  built Places scaffold: {sum(len(v) for v in city_counts.values())} branch cities")

    if key in VERIFIED_PATTERNS:
        pattern = VERIFIED_PATTERNS[key]
        print(f"[{key}] email pattern (verified from real samples): {pattern}")
    else:
        pattern, examples = discover_email_pattern(serper, http, cfg["email_domain"],
                                                   cfg["pattern_queries"])
        print(f"[{key}] email pattern (auto): {pattern}  (examples: {examples})")

    # ---- A. discovery ----
    raw = []

    def gather(q, qstate):
        for it in serper.organic(q, num=20):
            cand = parse_linkedin_result(it.get("title", ""), it.get("snippet", ""),
                                         it.get("link", ""), cfg["aliases"])
            if cand:
                cand["query_state"] = qstate
                cand["snippet"] = it.get("snippet", "")
                raw.append(cand)

    for st in states:
        before = len(raw)
        for b in ROLE_BOOSTERS:
            gather(f'site:linkedin.com/in {cfg["li_anchor"]} {st} {b}'.strip(), st)
        for city, _ in city_counts.get(st, Counter()).most_common(CITY_QUERIES_PER_STATE):
            gather(f'site:linkedin.com/in {cfg["li_anchor"]} "{city}" {st}', st)
        print(f"  {st:14} +{len(raw)-before} raw hits")

    # ---- B. merge per person ----
    people = _merge_candidates(raw)

    # ---- C. classify + confirm state ----
    rows, unconfirmed = [], []
    dropped = Counter()
    for p in people:
        bucket, is_ag = classify_role(p["title"], cfg["aliases"])
        if not is_ag:
            dropped["non-ag role"] += 1
            continue
        qstate = Counter(p["query_states"]).most_common(1)[0][0]
        st, conf, city = resolve_state(p["city"], p["explicit_state"], qstate, city2states)
        if conf == "out_of_scope":
            dropped["out-of-23-states"] += 1
            continue
        row = _build_row(cfg, p, st or qstate, conf, city, today, pattern, http,
                         mx_cache, city_phone)
        (rows if conf in ("confirmed", "corrected") else unconfirmed).append(row)

    # ---- D. corroboration for unconfirmed ----
    if corroborate and unconfirmed:
        print(f"  corroborating {len(unconfirmed)} unconfirmed people...")
        for row in unconfirmed:
            st = _corroborate_state(serper, row["name"], cfg, city2states)
            if st:
                row["state"], row["state_confidence"] = st, "confirmed"
                row["notes"] = row["notes"].replace("[state unconfirmed]", "").strip()
                # try a branch phone now that we have a state + city
                if not row["phone"] and row["city"]:
                    bp = match_branch_phone(st, row["city"], city_phone)
                    if bp:
                        row["phone"], row["phone_type"] = bp, "branch (locator)"
        rows.extend([r for r in unconfirmed if r["state_confidence"] == "confirmed"])
        unconfirmed = [r for r in unconfirmed if r["state_confidence"] != "confirmed"]

    # keep unconfirmed but clearly flagged (don't assert a state we can't back up)
    rows.extend(unconfirmed)

    # ---- E1. label email confidence by pattern certainty ----
    label = "inferred (assumed pattern)" if key in PATTERN_ASSUMED else "inferred (verified pattern)"
    for r in rows:
        if r["email"]:
            r["email_confidence"] = label

    # ---- E2. phone enrichment via Google Places (any company, fills no-scaffold gaps) ----
    pcache, filled = {}, 0
    for r in rows:
        if r["phone"] or not r["city"] or r["state_confidence"] != "confirmed":
            continue
        ph = places_phone(serper, cfg["aliases"], r["city"], r["state"], pcache)
        if ph:
            r["phone"], r["phone_type"] = ph, "branch (Places)"
            filled += 1
    if filled:
        print(f"  places phone enrichment: +{filled} phones")

    out = Path("output/contacts") / f"{key}.json"
    out.write_text(json.dumps(rows, indent=1))
    _summary(key, rows, dropped, serper, fc, out)
    return rows


def _build_row(cfg, p, state, conf, city, today, pattern, http, mx_cache, city_phone):
    email, econf = build_email(p["first"], p["last"], cfg["email_domain"], pattern, http, mx_cache)
    phone, ptype = _phone_for(state, city, p["snippets"], city_phone)
    bucket, _ = classify_role(p["title"], cfg["aliases"])
    notes = ("certs: " + ",".join(p["certs"]) + " ") if p["certs"] else ""
    if conf == "unconfirmed":
        notes += "[state unconfirmed]"
    elif conf == "corrected":
        notes += "[state corrected from search via city]"
    return {
        "company": cfg["company"], "state": state,
        "state_confidence": "confirmed" if conf in ("confirmed", "corrected") else "unconfirmed",
        "city": city, "name": p["name"], "title": p["title"], "role_category": bucket,
        "email": email, "email_confidence": econf, "phone": phone, "phone_type": ptype,
        "linkedin": p["linkedin"], "source_url": p["linkedin"], "date": today,
        "notes": notes.strip(),
    }


def _corroborate_state(serper, name, cfg, city2states):
    """One extra query to pin a person's state from any public page."""
    for it in serper.organic(f'"{name}" {cfg["li_anchor"]}', num=10):
        if is_foreign_linkedin(it.get("link", "")):
            return ""  # corroboration says foreign -> leave unconfirmed (will be flagged)
        city, expl, country = extract_location(it.get("snippet", ""))
        if country == "foreign":
            return ""
        st, conf, _ = resolve_state(city, expl, "", city2states)
        if conf in ("confirmed", "corrected") and st:
            return st
    return ""


def _summary(key, rows, dropped, serper, fc, out):
    by_bucket = Counter(r["role_category"] for r in rows)
    by_state = Counter(r["state"] for r in rows)
    n_email = sum(1 for r in rows if r["email"])
    n_phone = sum(1 for r in rows if r["phone"])
    n_conf = sum(1 for r in rows if r["state_confidence"] == "confirmed")
    print(f"\n[{key}] -> {len(rows)} people  (dropped: {dict(dropped)})")
    print(f"  STATE CONFIRMED: {n_conf}/{len(rows)} ({round(100*n_conf/max(len(rows),1))}%) | "
          f"email: {n_email} | phone: {n_phone}")
    print("  by bucket:", dict(by_bucket))
    print("  by state:", dict(sorted(by_state.items())))
    print(f"  serper={serper.queries_spent}q firecrawl={fc.credits_spent}cr -> {out}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--corroborate"]
    corrob = "--corroborate" in sys.argv
    key = args[0] if args else "nutrien"
    states = [s.replace("_", " ") for s in args[1:]] or TARGET_STATES
    run_company(key, states, corroborate=corrob)
