"""V6 targeted enhancement (credits unconstrained):
  - aurora, chs: aggressively confirm states + fill cities, then fill phones
    (Places by city). More queries per person + explicit state-name parsing.
  - triton: broaden discovery (more LinkedIn angles + their website).

Run: PYTHONPATH=. python3 jobs/enhance_v6.py [keys...]   (default: aurora chs triton)
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date

from msc.extract import extract_phone
from msc.net import Firecrawl, Http, Serper
from jobs.contacts_lib import (ABBR_STATE, ALL_US_STATES, STATE_ABBR, TARGET_ABBR,
                               TARGET_STATES, _best_known_city, build_email, classify_role,
                               extract_location, is_foreign_linkedin, load_city_resolver,
                               parse_linkedin_result, places_phone, resolve_state)
from jobs.run_company import COMPANIES, VERIFIED_PATTERNS

GAZ = "data/us_cities.csv"
_nk = lambda s: re.sub(r"[^a-z]", "", (s or "").lower())
_STATE_NAME_RE = re.compile(
    r"\b(" + "|".join(re.escape(s) for s in sorted(ALL_US_STATES, key=len, reverse=True)) + r")\b", re.I)
_ABBR_RE = re.compile(r",\s*([A-Z]{2})\b")


def explicit_target_state(text):
    """A US state named in text, returned only if it's one of our 23 targets.
    Skips state names that are part of a university/college name (e.g. 'Kansas
    State University', 'South Dakota State') — that's a school, not a location."""
    t = text or ""
    for m in _STATE_NAME_RE.finditer(t):
        after = t[m.end():m.end() + 25].lower()
        if re.match(r"\s+(state\b|university|college|tech\b)", after):
            continue  # school name, not the person's location
        ab = ALL_US_STATES[m.group(1).lower()]
        if ab in TARGET_ABBR:
            return ABBR_STATE[ab]
    for m in _ABBR_RE.finditer(t):
        if m.group(1) in TARGET_ABBR:
            return ABBR_STATE[m.group(1)]
    return ""


def deep_corroborate(serper, name, cfg, city2states):
    """Multiple queries to pin a person's state/city/phone. Returns (state, city, phone)."""
    a, dom = cfg["aliases"][0], cfg["email_domain"]
    queries = [
        f'"{name}" "{a}"',
        f'"{name}" {a} agronomy OR sales OR manager OR agronomist OR location',
        f'"{name}" {dom}',
        f'"{name}" "{a}" city OR office OR based OR territory',
    ]
    phone = ""
    for q in queries:
        for it in serper.organic(q, num=10):
            if is_foreign_linkedin(it.get("link", "")):
                continue
            snip = it.get("snippet", "")
            blob = it.get("title", "") + " " + snip
            if not phone:
                phone = extract_phone(snip) or ""
            # 1) reliable location-context resolution (City, State / Location:)
            city, expl, country = extract_location(snip)
            if country == "foreign":
                continue
            s, conf, c = resolve_state(city, expl, "", city2states)
            if conf in ("confirmed", "corrected") and s in TARGET_STATES:
                return s, c, phone
            # 2) explicit state name anywhere in the result (weaker). Guard against
            #    the company-HQ-state trap: if a city is present it must be IN that
            #    state, else the state mention is unreliable (skip it).
            st = explicit_target_state(blob)
            if st:
                c2 = _best_known_city(city, city2states)
                if c2:
                    if STATE_ABBR[st] in city2states.get(c2.lower(), set()):
                        return st, c2, phone
                    continue  # city contradicts the named state -> not trustworthy
                return st, "", phone  # no city to contradict -> accept
    return "", "", phone


def enhance(key):
    cfg = COMPANIES[key]
    rows = json.loads(open(f"output/contacts/{key}.json").read())
    serper = Serper()
    city2states = load_city_resolver(GAZ)
    confd = cityd = phoned = cleaned = 0
    for r in rows:
        # --- clean inherited junk/mismatched cities (re-validate vs gazetteer) ---
        clean = _best_known_city(r["city"], city2states)
        ab = STATE_ABBR.get(r["state"], "")
        mismatch = bool(clean and ab and ab not in city2states.get(clean.lower(), set()))
        if (r["city"] and not clean) or mismatch:
            r["city"] = ""
            cleaned += 1
            if r["state_confidence"] == "confirmed":
                r["state_confidence"] = "unconfirmed"  # confirmation rested on a bad parse
        else:
            r["city"] = clean
        # --- corroborate anything not cleanly confirmed or missing a city ---
        if r["state_confidence"] != "confirmed" or not r["city"]:
            st, city, phone = deep_corroborate(serper, r["name"], cfg, city2states)
            if st and r["state_confidence"] != "confirmed":
                r["state"], r["state_confidence"] = st, "confirmed"
                r["notes"] = (r["notes"].replace("[state unconfirmed]", "").strip()
                              + " [confirmed via deep corroboration]").strip()
                confd += 1
            if city and not r["city"]:
                r["city"] = city
                cityd += 1
            if phone and not r["phone"]:
                r["phone"], r["phone_type"] = phone, "direct (web)"
                phoned += 1
    # phone fill via Places for anyone with a (state, city) but no phone
    pcache = {}
    for r in rows:
        if not r["phone"] and r["city"] and r["state"]:
            ph = places_phone(serper, cfg["aliases"], r["city"], r["state"], pcache)
            if ph:
                r["phone"], r["phone_type"] = ph, "branch (Places)"
                phoned += 1
    json.dump(rows, open(f"output/contacts/{key}.json", "w"), indent=1)
    nconf = sum(1 for r in rows if r["state_confidence"] == "confirmed")
    nph = sum(1 for r in rows if r["phone"])
    print(f"[{key}] cleaned {cleaned} junk/mismatch cities; +{confd} confirmed, +{cityd} cities, "
          f"+{phoned} phones | now {nconf}/{len(rows)} confirmed, {nph} phones | serper={serper.queries_spent}")


def expand_triton():
    cfg = COMPANIES["triton"]
    rows = json.loads(open("output/contacts/triton.json").read())
    http, serper, fc = Http(), Serper(), Firecrawl()
    city2states = load_city_resolver(GAZ)
    have = {_nk(r["name"]) for r in rows}
    today = date.today().isoformat()
    added = 0
    queries = [
        'site:linkedin.com/in "Triton Fumigation"',
        '"Triton Fumigation" (manager OR operations OR sales OR fumigation OR technician OR agronomist OR superintendent OR owner)',
        '"Triton Fumigation" Louisiana OR Arkansas OR Texas OR Mississippi OR California OR rice',
        '"Triton Fumigation" team OR staff OR employee OR "our team"',
        'site:linkedin.com/in "Triton" fumigation rice OR grain',
    ]
    for q in queries:
        for it in serper.organic(q, num=20):
            cand = parse_linkedin_result(it.get("title", ""), it.get("snippet", ""),
                                         it.get("link", ""), cfg["aliases"])
            if not cand or _nk(cand["name"]) in have:
                continue
            bucket, is_ag = classify_role(cand["title"], cfg["aliases"])
            if not is_ag:
                continue
            st, conf, city = resolve_state(cand["city"], cand["explicit_state"], "Louisiana", city2states)
            email, econf = build_email(cand["first"], cand["last"], cfg["email_domain"],
                                       VERIFIED_PATTERNS["triton"], http, {})
            rows.append({
                "company": cfg["company"], "state": st or "",
                "state_confidence": "confirmed" if conf in ("confirmed", "corrected") else "unconfirmed",
                "city": city, "name": cand["name"], "title": cand["title"], "role_category": bucket,
                "email": email, "email_confidence": "inferred (verified pattern)" if email else "",
                "phone": "", "phone_type": "", "linkedin": cand["linkedin"],
                "source_url": cand["linkedin"], "date": today,
                "notes": "added in V6 triton expansion",
            })
            have.add(_nk(cand["name"]))
            added += 1
    json.dump(rows, open("output/contacts/triton.json", "w"), indent=1)
    print(f"[triton] +{added} new people via broader queries | now {len(rows)} | serper={serper.queries_spent}")


def cleanup_only(key):
    """No-Serper cleanup: blank junk/mismatched cities (validate vs gazetteer).
    Used on companies we aren't deep-corroborating, to strip inherited V5 junk."""
    rows = json.loads(open(f"output/contacts/{key}.json").read())
    city2states = load_city_resolver(GAZ)
    n = 0
    for r in rows:
        clean = _best_known_city(r["city"], city2states)
        ab = STATE_ABBR.get(r["state"], "")
        mismatch = bool(clean and ab and ab not in city2states.get(clean.lower(), set()))
        if (r["city"] and not clean) or mismatch:
            r["city"] = ""
            n += 1
        else:
            r["city"] = clean
    json.dump(rows, open(f"output/contacts/{key}.json", "w"), indent=1)
    print(f"[{key}] cleanup: blanked {n} junk/mismatch cities")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--cleanup":
        for k in args[1:]:
            cleanup_only(k)
    else:
        for k in (args or ["aurora", "chs", "triton"]):
            expand_triton() if k == "triton" else enhance(k)
