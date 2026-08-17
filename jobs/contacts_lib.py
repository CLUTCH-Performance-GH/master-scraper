"""Shared engine for the ag-side contact-roster project.

One person = one row. The job is to find named ag-side people (leaders, branch
managers, agronomists, sales reps, specialists) at target ag companies across
23 states, with title / state / email / phone, every field confidence-tagged.

Division of labor:
  - this module does the MECHANICAL work (parse LinkedIn snippets, classify
    roles, infer + MX-gate emails, match branch phones, dedupe)
  - the agent does the JUDGMENT inline (QA the parser, resolve ambiguous roles,
    catch wrong-company contamination, confirm state attribution)

Compliance: we never log into or scrape LinkedIn directly. We only read public
result snippets that Serper returns from Google's index.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from msc.extract import (GENERIC_MAILBOXES, extract_domain, extract_emails,
                         extract_phone, infer_emails)
from msc.osint import mx_records

_EXTRA_GENERIC = {"careers", "cooperative", "resources", "media", "news", "team",
                  "agronomy", "sales", "support", "service", "info", "contact",
                  "recruiting", "hr", "marketing", "feedback", "help"}

# ---------------------------------------------------------------------------
# Geography
# ---------------------------------------------------------------------------

TARGET_STATES = [
    "Arizona", "Arkansas", "California", "Colorado", "Idaho", "Iowa", "Kansas",
    "Kentucky", "Louisiana", "Minnesota", "Mississippi", "Missouri", "Montana",
    "Nebraska", "New Mexico", "North Dakota", "Oklahoma", "Oregon",
    "South Dakota", "Tennessee", "Texas", "Washington", "Wyoming",
]
STATE_ABBR = {
    "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO",
    "Idaho": "ID", "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY",
    "Louisiana": "LA", "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO",
    "Montana": "MT", "Nebraska": "NE", "New Mexico": "NM", "North Dakota": "ND",
    "Oklahoma": "OK", "Oregon": "OR", "South Dakota": "SD", "Tennessee": "TN",
    "Texas": "TX", "Washington": "WA", "Wyoming": "WY",
}
ABBR_STATE = {v: k for k, v in STATE_ABBR.items()}
# region/division words that imply a state, for corroborating LinkedIn locations
_STATE_WORD_RE = re.compile(
    r"\b(" + "|".join(list(STATE_ABBR) + list(STATE_ABBR.values())) + r")\b")

# ALL 50 states + DC (need the non-target ones too, to DETECT + drop out-of-scope)
ALL_US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI",
    "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC", "washington dc": "DC", "washington, dc": "DC",
}
_ALL_ABBR = set(ALL_US_STATES.values())
TARGET_ABBR = set(STATE_ABBR.values())

# Foreign signals — these make a profile "not acceptable" per client rule.
_CA_PROVINCES = ("canada", "ontario", "quebec", "british columbia", "alberta",
                 "manitoba", "saskatchewan", "nova scotia", "new brunswick",
                 "newfoundland", "prince edward")
_FOREIGN_WORDS = _CA_PROVINCES + ("australia", "united kingdom", "england",
                                  "mexico", "brazil", "india", "costa rica",
                                  "new zealand", "philippines", "netherlands")
# a non-www, non-us country code in the linkedin host => localized foreign profile
_LI_HOST_RE = re.compile(r"https?://([a-z]{2,3})\.linkedin\.com", re.I)


def is_foreign_linkedin(url: str) -> bool:
    m = _LI_HOST_RE.search(url or "")
    return bool(m) and m.group(1).lower() not in ("www", "us")


def load_city_resolver(path):
    """city (lower) -> set(state_abbr) from the local US gazetteer."""
    import csv
    c2s = defaultdict(set)
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            c2s[row["CITY"].lower().strip()].add(row["STATE_CODE"])
    return c2s


def extract_location(snippet: str):
    """Return (city, state_abbr_or_'', country) parsed from a result snippet.

    country: 'foreign' if a non-US place is named, else ''.
    Handles: '<City>. View <name>...', 'Location: <City>', '<City>, <State>',
    '<City>, <State> Metropolitan Area', 'Greater <City> Area'.
    """
    s = " " + (snippet or "") + " "
    low = s.lower()
    if any(w in low for w in _FOREIGN_WORDS):
        return ("", "", "foreign")

    city, state = "", ""
    # "City, <Full State>" or "City, ST"
    m = re.search(r"\b([A-Z][A-Za-z.\-' ]{1,28}?),\s*"
                  r"([A-Z][a-z]+(?:\s[A-Z][a-z]+)?|[A-Z]{2})\b", s)
    if m:
        cand_state = m.group(2).strip()
        abbr = ALL_US_STATES.get(cand_state.lower()) or (
            cand_state.upper() if cand_state.upper() in _ALL_ABBR else "")
        if abbr:
            city, state = m.group(1).strip(" .,'-"), abbr
    # "Location: City"  (city only)
    if not city:
        m = re.search(r"Location:\s*([A-Z][A-Za-z.\-' ]{1,28}?)(?:\s*[·|.,]|\s+\d|$)", s)
        if m:
            city = m.group(1).strip(" .,'-")
    # "<City>. View <name>'s profile"  (very common LinkedIn snippet shape)
    if not city:
        m = re.search(r"(?:^|[·|]\s*)([A-Z][A-Za-z.\-' ]{1,28}?)\.\s+View\b", s)
        if m:
            city = m.group(1).strip(" .,'-")
    # "Greater <City> Area"
    if not city:
        m = re.search(r"Greater\s+([A-Z][A-Za-z.\-' ]{1,24}?)\s+(?:Area|Metropolitan)", s)
        if m:
            city = m.group(1).strip(" .,'-")
    # reject obvious non-city captures
    if city and (len(city) < 2 or re.search(r"\b(view|profile|experience|nutrien|"
                                            r"linkedin|connection)\b", city, re.I)):
        city = ""
    return (city, state, "")


# words that are in the gazetteer but are really schools/orgs, not a person's city
_NONCITY = {"university", "college", "institute", "academy", "school", "department",
            "company", "cooperative", "services", "district", "county", "center",
            "state university", "state college", "community college", "high school"}


def _ok_city(frag):
    f = frag.lower()
    return f not in _NONCITY and not f.endswith(("university", "college", "institute"))


def _best_known_city(cand, city2states):
    """Pull the real US city out of a noisy capture by validating n-grams against
    the gazetteer. 'Nutrien Ag Solutions - Glidden' -> 'Glidden'. '' if none real."""
    cand = (cand or "").strip()
    if not cand:
        return ""
    if cand.lower() in city2states and _ok_city(cand):
        return cand
    words = [w for w in re.split(r"[.\-,/]|\s", cand) if w]
    best = ""
    for n in (3, 2, 1):
        for i in range(len(words) - n + 1):
            frag = " ".join(words[i:i + n])
            if frag.lower() in city2states and _ok_city(frag) and len(frag) >= len(best):
                best = frag  # longest, latest wins (city usually trails the junk)
    return best


def resolve_state(city, explicit_state, query_state, city2states):
    """Confirm/correct the state. Returns (state, confidence, clean_city).

    confidence: 'confirmed' (explicit state or city resolves to query state),
                'corrected' (city uniquely resolves to a DIFFERENT state),
                'unconfirmed' (no location signal — do NOT assert query state).
    """
    city = _best_known_city(city, city2states)  # validate -> only real cities survive
    qabbr = STATE_ABBR.get(query_state, "")
    # 1) explicit state in the snippet — strongest, but the city (if any) must
    #    actually belong to it (else it's a school/typo like "Concordia, Nebraska")
    if explicit_state:
        if explicit_state not in TARGET_ABBR:
            return ("", "out_of_scope", city)  # real US state, just not one of our 23
        if city and explicit_state not in city2states.get(city.lower(), set()):
            return ("", "unconfirmed", "")  # city contradicts the named state -> distrust
        return (ABBR_STATE[explicit_state], "confirmed", city)
    # 2) city-based resolution against the gazetteer
    if city:
        states = city2states.get(city.lower(), set())
        if states:
            if qabbr in states:
                return (query_state, "confirmed", city)
            if len(states) == 1:
                only = next(iter(states))
                if only in TARGET_ABBR:
                    return (ABBR_STATE[only], "corrected", city)
                return ("", "out_of_scope", city)
            # ambiguous city, none is the query state -> can't confirm
            return ("", "unconfirmed", city)
    # 3) nothing to go on
    return ("", "unconfirmed", "")

# ---------------------------------------------------------------------------
# Role taxonomy — ag-side only. Priority order: first match wins the bucket.
# ---------------------------------------------------------------------------

# Hard excludes: corporate back-office. These people are NOT wanted.
ROLE_EXCLUDE = re.compile(
    r"\b(human resources|\bh\.?r\.?\b|talent|recruit|payroll|benefits|"
    r"finance|financial|accounting|accountant|controller|treasur|audit|tax\b|"
    r"information technology|\bi\.?t\.?\b|software|developer|programmer|"
    r"cyber|network admin|systems admin|help ?desk|data scientist|"
    r"legal|attorney|counsel|paralegal|compliance officer|"
    r"safety|sh&?e\b|\behs\b|environmental health|health (and|&) safety|"
    r"communications|public relations|\bpr\b|brand manager|social media|"
    r"marketing|accounts payable|accounts receivable|administrative coordinator|"
    r"truck driver|\bdriver\b|\bcdl\b|notary|laboratory|lab technician|"
    r"quality control|quality assurance|\bqal\b|\bqc\b|bank|banking|banker|"
    r"loan officer|employee development|talent development|architect|"
    r"administrative assistant|executive assistant|receptionist|office manager|"
    r"intern\b|student|retired|former|seeking|graduate trainee)\b", re.I)

# LinkedIn-headline fluff that isn't a real job title
_FLUFF_RE = re.compile(
    r"^(experience|bachelor|master of|associate of|advocate|helping|aspiring|"
    r"seeking|passionate|results[- ]|dedicated|motivated|hard[- ]working|"
    r"with \d|\d+\+? years|graduate of|degree|currently|looking)\b", re.I)
# "<role> at <OtherCompany>" — our company is stripped by the parser, so a
# remaining "at X" / company suffix means the person's employer is NOT ours.
_OTHER_EMPLOYER_RE = re.compile(
    r"(\bat\s+[A-Z][\w&.,'\- ]{2,}$|\b(LLC|L\.L\.C|Inc\.?|Incorporated|"
    r"Bank|University|College)\b)")

# (compiled pattern, bucket) — evaluated in order; first hit assigns the bucket.
_ROLE_RULES = [
    (re.compile(r"\b(pricing|procurement|supplier management|sourcing|category manager|"
                r"corporate development|investor relations|treasury|manufacturing|"
                r"continuous improvement|supply chain|\bibp\b|integrated business planning|"
                r"data scien|analytics manager)\b", re.I),
     "Corporate/HQ (review)"),
    (re.compile(r"\b(president|chief|\bceo\b|\bcoo\b|\bcfo\b|owner|founder|"
                r"vice president|\bvp\b|svp|evp|general manager|\bgm\b)\b", re.I),
     "Leadership/Exec"),
    (re.compile(r"\b(division manager|regional manager|region manager|area manager|"
                r"district manager|area sales manager|regional sales|division lead|"
                r"director)\b", re.I),
     "Division/Regional Leadership"),
    (re.compile(r"\b(branch manager|location manager|store manager|facilit|"
                r"operations|plant manager|warehouse|site manager|distribution center|"
                r"dispatch|logistics|fleet|yard manager|assistant manager|asst\.? manager|"
                r"branch operations)\b", re.I),
     "Retail/Branch Ops"),
    (re.compile(r"\b(agronomist|crop consultant|crop advisor|crop adviser|\bcca\b|"
                r"agronomy|precision ag|digital ag|field advisor|field agronom|"
                r"pest control advisor|\bpca\b|certified crop)\b", re.I),
     "Agronomy"),
    (re.compile(r"\b(seed|crop protection|plant nutrition|nutrient|fertili|chemical|"
                r"applicator|technician|proprietary product|"
                r"technical (sales|agronom|service|specialist)|"
                r"product manager|specialist|grain merchandis|merchandiser|originator)\b",
                re.I),
     "Technical/Specialty"),
    (re.compile(r"\b(sales|account manager|account executive|seller|salesperson|"
                r"sales rep|representative|business development|territory)\b", re.I),
     "Sales"),
]


def classify_role(title, company_aliases=()):
    """Return (bucket, is_ag_side). is_ag_side=False means drop the row."""
    t = (title or "").strip()
    if not t:
        # company-confirmed LinkedIn profile but no parseable title — keep, flag.
        return ("Unknown title (review)", True)
    low = t.lower()
    mentions_us = any(a.lower() in low for a in company_aliases)
    # LinkedIn-headline fluff -> not a usable title; keep person, flag for review.
    if _FLUFF_RE.match(t) or "graphic" in low:
        return ("Unknown title (review)", True)
    # cross-company contamination: title names a DIFFERENT employer -> drop.
    if not mentions_us and _OTHER_EMPLOYER_RE.search(t):
        return ("Excluded (other employer)", False)
    if ROLE_EXCLUDE.search(t):
        return ("Excluded (non-ag)", False)
    # "Owner of <other company>" at a large employer = likely independent/dealer,
    # not an employee — flag so the inferred company email isn't trusted blindly.
    if re.match(r"\s*(owner|co-?owner|proprietor)\b", t, re.I) and not re.search(
            r"\b(branch|location|territory|district|sales)\b", t, re.I):
        return ("Owner/Independent (review)", True)
    for pat, bucket in _ROLE_RULES:
        if pat.search(t):
            return (bucket, True)
    # A bare "Manager"/"Supervisor" with no back-office word: keep for review.
    if re.search(r"\b(manager|supervisor|lead|coordinator|consultant|advisor)\b", t, re.I):
        return ("Other-Ag (review)", True)
    return ("Unclassified (review)", True)


# ---------------------------------------------------------------------------
# LinkedIn result parsing  (compliant: public Google snippets only)
# ---------------------------------------------------------------------------

_CERT_RE = re.compile(r",?\s*\b(CCA|CPAg|PCA|CGCS|PhD|Ph\.D\.|MBA|CPA|PE)\b\.?", re.I)
_LI_TAIL_RE = re.compile(r"\s*[|\-–]\s*linkedin\s*$", re.I)
# "Title at Company", "Title - Company", "Title. Company"
_AT_CO_RE = None  # set per-company via make_company_matchers


_TITLE_TAIL_WORDS = re.compile(
    r"[\s,;:&/\-]+(for|at|with|in|of|and|the|to|on|a|an)\s*$", re.I)


def _clean_title(t, first="", last=""):
    """Strip snippet-mining artifacts: leading name leaks ('Lastname.',
    'First Last.'), dangling trailing prepositions, stray punctuation."""
    t = re.sub(r"\s+", " ", (t or "").strip())
    for lead in (f"{first} {last}", last, first):
        if lead.strip() and re.match(rf"{re.escape(lead.strip())}[.\s,]", t, re.I):
            t = t[len(lead.strip()):].lstrip(" .,-")
            break
    for _ in range(4):
        new = _TITLE_TAIL_WORDS.sub("", t).strip().rstrip(" .,;:&/-@")
        if new == t:
            break
        t = new
    return t.strip(" .,;:&/-@")


def clean_name(raw: str):
    """Return (display_name, first, last, certs). Strips trailing certs/punct."""
    certs = [m.group(1).upper() for m in _CERT_RE.finditer(raw or "")]
    name = _CERT_RE.sub("", raw or "").strip(" ,-|")
    name = re.sub(r"\s+", " ", name)
    parts = [p for p in name.split() if p]
    first = parts[0] if parts else ""
    last = parts[-1] if len(parts) >= 2 else ""
    return name, first, last, certs


def parse_linkedin_result(title: str, snippet: str, link: str, company_aliases):
    """Parse one Serper organic result from a linkedin.com/in profile.

    Returns a candidate dict (name, title, location_hint, linkedin) or None if
    it doesn't look like a real person at the target company.
    """
    if "linkedin.com/in" not in (link or ""):
        return None
    if is_foreign_linkedin(link):
        return None  # ca./au./cr. localized profile -> not US (client rule)
    raw = _LI_TAIL_RE.sub("", title or "").strip()
    # Split "Name - Title at Company" / "Name - Title - Company"
    if " - " not in raw:
        return None
    name_part, rest = raw.split(" - ", 1)
    name, first, last, certs = clean_name(name_part)
    if not (first and last) or len(name) > 60:
        return None
    brand = min(company_aliases, key=len)  # e.g. "Nutrien", "CHS"
    if brand.lower() in name.lower():
        return None  # a company/location page misparsed as a person

    # Title = rest, stripped of the trailing "... at <Company>" / "- <Company>".
    title_txt = rest
    low_rest = rest.lower()
    cut = len(title_txt)
    for alias in company_aliases:
        a = alias.lower()
        for sep in (" at " + a, " - " + a, ", " + a, " " + a):
            idx = low_rest.find(sep)
            if idx != -1:
                cut = min(cut, idx)
    title_txt = title_txt[:cut].strip(" -–,|")
    title_txt = re.sub(r"\.\.\.$|…$", "", title_txt).strip()

    # If the title got truncated/empty or is just the company, mine the snippet.
    if (not title_txt or len(title_txt) < 3
            or any(a.lower() in title_txt.lower() for a in company_aliases)):
        title_txt = _title_from_snippet(snippet, company_aliases) or title_txt

    title_txt = _clean_title(title_txt, first, last)
    # blank a title that is still just the company name -> classify as review.
    # normalize punctuation so "Nutrien Ag. Solutions" matches "Nutrien Ag Solutions".
    _norm_t = re.sub(r"[.\s]+", " ", title_txt.lower()).strip()
    for a in company_aliases:
        _norm_a = re.sub(r"[.\s]+", " ", a.lower()).strip()
        if _norm_t == _norm_a or (_norm_a in _norm_t and len(_norm_t) <= len(_norm_a) + 4):
            title_txt = ""
            break

    # confirm the result actually concerns the target company
    blob = (title + " " + snippet + " " + link).lower()
    if not any(a.lower() in blob for a in company_aliases):
        return None

    city, expl_state, country = extract_location(snippet)
    if country == "foreign":
        return None  # snippet names a non-US location
    return {
        "name": name, "first": first, "last": last, "certs": certs,
        "title": title_txt,
        "city": city, "explicit_state": expl_state,
        "linkedin": (link or "").split("?")[0],
    }


def _title_from_snippet(snippet: str, company_aliases):
    """Pull a role phrase from the snippet: '<Title> at Company' / '<Title>. Company'."""
    s = snippet or ""
    for alias in company_aliases:
        a = re.escape(alias)
        m = re.search(rf"([A-Z][A-Za-z/&,\-\. ]{{3,60}}?)\s+(?:at|[-.·])\s+{a}", s)
        if m:
            return m.group(1).strip(" -–,.|")
    # fallback: first capitalized role-ish phrase
    m = re.search(r"\b([A-Z][A-Za-z/&\- ]{4,50}(?:Manager|Agronomist|Sales|"
                  r"Consultant|Director|Specialist|Representative|Supervisor|Advisor))\b", s)
    return m.group(1).strip() if m else ""


def _location_from_snippet(snippet: str):
    s = snippet or ""
    m = re.search(r"Location:\s*([A-Za-z .,'\-]+?)(?:\s*[·|]|\s+\d|$)", s)
    if m:
        return m.group(1).strip(" .,")
    # "Greater X Area", "X, State"
    m = re.search(r"\b([A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+)?,\s*[A-Z][a-zA-Z]+)\b", s)
    return m.group(1).strip() if m else ""


def infer_state(location_hint, title, query_state, branch_cities):
    """Best-effort state attribution. Returns (state, confidence).

    confidence: 'confirmed' (state/known-city found in text) or
                'query'     (only the search-query state — unconfirmed).
    """
    text = f"{location_hint} {title}"
    m = _STATE_WORD_RE.search(text)
    if m:
        tok = m.group(1)
        st = ABBR_STATE.get(tok.upper(), tok if tok in STATE_ABBR else "")
        if st:
            return (st, "confirmed")
    # city in the query-state's known branch list?
    lh = (location_hint or "").lower()
    for city in branch_cities.get(query_state, ()):
        if city and city.lower() in lh:
            return (query_state, "confirmed")
    return (query_state, "query")


# ---------------------------------------------------------------------------
# Email pattern discovery + inference (MX-gated)
# ---------------------------------------------------------------------------

def discover_email_pattern(serper, http, email_domain, sample_queries):
    """Find real <name>@domain examples to deduce the dominant local-part shape.

    Returns (pattern_key, examples) where pattern_key in
    {'first.last','flast','firstlast','first','unknown'}.
    """
    found = []
    for q in sample_queries:
        for it in serper.organic(q, num=10):
            blob = it.get("title", "") + " " + it.get("snippet", "") + " " + it.get("link", "")
            for e in extract_emails(blob):
                if extract_domain(e) == email_domain:
                    found.append(e.split("@")[0].lower())
    # keep only person-like local parts (drop generic mailboxes + org names)
    brand = email_domain.split(".")[0]
    bp = brand[:3]
    persons = set()  # count UNIQUE local parts (repeated org footers must not win)
    for lp in found:
        base = re.sub(r"\d+$", "", lp)
        if (not base or base in GENERIC_MAILBOXES or base in _EXTRA_GENERIC
                or base.startswith(bp) or len(base) < 3):
            continue
        persons.add(base)
    pat = Counter()
    for lp in persons:
        if re.fullmatch(r"[a-z]+\.[a-z]+", lp):
            pat["first.last"] += 1
        elif re.fullmatch(r"[a-z]+_[a-z]+", lp):
            pat["first_last"] += 1
        elif re.fullmatch(r"[a-z]\.[a-z]+", lp):
            pat["flast"] += 1          # j.keifer
        elif re.fullmatch(r"[a-z]{5,}", lp):
            pat["firstlast"] += 1
    best = pat.most_common(1)
    # default to first.last (the dominant corporate convention) when ambiguous
    return (best[0][0] if best else "first.last", sorted(set(persons))[:8])


def build_email(first, last, email_domain, pattern, http, mx_cache):
    """Construct + MX-gate an email. Returns (email, confidence) or ('', '')."""
    if not (first and last and email_domain):
        return ("", "")
    if email_domain not in mx_cache:
        mx_cache[email_domain] = bool(mx_records(email_domain, http))
    if not mx_cache[email_domain]:
        return ("", "")  # domain won't accept mail — don't emit a dead address
    f = re.sub(r"[^a-z]", "", first.lower())
    l = re.sub(r"[^a-z]", "", last.lower())
    if len(f) < 2 or len(l) < 2:
        return ("", "")  # initials / incomplete name — can't infer a real address
    by_pat = {
        "first.last": f"{f}.{l}@{email_domain}",
        "first_last": f"{f}_{l}@{email_domain}",
        "flast": f"{f[0]}{l}@{email_domain}",       # jsmith (first initial + last)
        "flast_dot": f"{f[0]}.{l}@{email_domain}",  # j.smith
        "lastf": f"{l}{f[0]}@{email_domain}",       # smithj (last + first initial)
        "lastfirst": f"{l}.{f}@{email_domain}",
        "last": f"{l}@{email_domain}",
        "firstlast": f"{f}{l}@{email_domain}",
        "first": f"{f}@{email_domain}",
    }
    email = by_pat.get(pattern) or f"{f}.{l}@{email_domain}"
    return (email, "inferred")


def places_phone(serper, company_aliases, city, state, cache):
    """Google Places (via Serper) office phone for '<company> <city> <state>'.
    Works for any company, including those with no location scaffold. Cached
    per (company, state, city). Only trusts a result whose title is our company."""
    key = (company_aliases[0], state, city)
    if key in cache:
        return cache[key]
    ph = ""
    data = serper.query(f"{company_aliases[0]} {city} {state}", num=5, endpoint="places")
    for p in data.get("places", []):
        title = (p.get("title") or "").lower()
        addr = (p.get("address") or "").lower()
        # must be the LOCAL branch (city in address), not the HQ switchboard
        if (any(a.lower() in title for a in company_aliases)
                and city.lower() in addr and p.get("phoneNumber")):
            ph = p["phoneNumber"]
            break
    cache[key] = ph
    return ph


# ---------------------------------------------------------------------------
# Location scaffold (existing CLS data) -> branch phone matching
# ---------------------------------------------------------------------------

def load_nutrien_scaffold(path):
    """Return (city_counts, city_phone) for Nutrien from the store-locator dump.

    city_counts: {state_name: Counter(city)}   city_phone: {(state,city_lower): phone}
    Branch phones from the official locator = the 'confirmed, recurring' numbers.
    """
    data = json.loads(Path(path).read_text()).get("data", [])
    city_counts = defaultdict(Counter)
    city_phone = {}
    for r in data:
        st = r.get("state", "")
        if st not in STATE_ABBR:
            continue
        city = (r.get("city") or "").strip()
        if not city:
            continue
        city_counts[st][city] += 1
        ph = (r.get("phone") or "").strip()
        if ph and (st, city.lower()) not in city_phone:
            city_phone[(st, city.lower())] = ph
    return city_counts, city_phone


def match_branch_phone(state, city, city_phone):
    """Branch/office phone for a confirmed (state, city) — from the company locator."""
    if not (state and city):
        return ""
    return city_phone.get((state, city.lower()), "")


def build_places_scaffold(serper, company_aliases, states):
    """For companies with no CLS location file: discover branch cities + phones
    via Google Places (one query/state). Returns (city_counts, city_phone) in the
    same shape as load_nutrien_scaffold — seeds city queries AND local phones."""
    city_counts = defaultdict(Counter)
    city_phone = {}
    for st in states:
        data = serper.query(f"{company_aliases[0]} {st}", num=20, endpoint="places")
        for p in data.get("places", []):
            title = (p.get("title") or "").lower()
            if not any(a.lower() in title for a in company_aliases):
                continue
            m = re.search(r",\s*([A-Za-z .'\-]+?),\s*[A-Z]{2}\b", p.get("address") or "")
            city = m.group(1).strip() if m else ""
            if not city:
                continue
            city_counts[st][city] += 1
            ph = p.get("phoneNumber") or ""
            if ph and (st, city.lower()) not in city_phone:
                city_phone[(st, city.lower())] = ph
    return city_counts, city_phone


# ---------------------------------------------------------------------------
# Dedupe
# ---------------------------------------------------------------------------

def _norm(s):
    return re.sub(r"[^a-z]", "", (s or "").lower())


def dedupe_people(rows):
    """Merge by linkedin URL, else by (normalized name + company). Keeps the
    richest title, unions source URLs, prefers 'confirmed' state."""
    by_key = {}
    for r in rows:
        key = r.get("linkedin") or f'{_norm(r.get("name"))}|{_norm(r.get("company"))}'
        if key not in by_key:
            by_key[key] = dict(r)
            by_key[key]["_sources"] = set(filter(None, [r.get("source_url")]))
            continue
        cur = by_key[key]
        cur["_sources"].add(r.get("source_url"))
        if len(r.get("title", "")) > len(cur.get("title", "")):
            cur["title"] = r["title"]
        if r.get("state_confidence") == "confirmed" and cur.get("state_confidence") != "confirmed":
            cur["state"], cur["state_confidence"] = r["state"], "confirmed"
        for f in ("email", "phone", "location_hint"):
            if not cur.get(f) and r.get(f):
                cur[f] = r[f]
    out = []
    for r in by_key.values():
        r["source_url"] = " ; ".join(sorted(s for s in r.pop("_sources") if s))
        out.append(r)
    return out


# row schema (column order for the workbook)
ROW_FIELDS = ["company", "state", "city_branch", "name", "title", "role_category",
              "email", "email_confidence", "phone", "phone_type", "linkedin",
              "source_url", "date", "notes"]
