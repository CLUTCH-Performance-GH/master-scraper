"""CMU / concrete masonry producer contact discovery.

Input is the LinkedIn company list built for the Region 1 CMU campaign: 176
producers across CT, DE, ME, MD, MA, NH, NJ, NY, PA, RI, VT and WV, each
carrying a website and an email domain.

Per company, climbing the cost ladder and stopping as soon as it has what it
needs:

  A. site scrape        free    homepage + /about + /team + /contact
  B. regex              free    company-domain emails, phone
  C. Claude extract     cents   real names and titles off those pages
  D. Serper LinkedIn    ~$0.0003/q  decision makers the site does not name
  E. email inference    free    pattern-deduced, MX-gated so nothing dead ships

Every contact carries an email_confidence of 'verified' (the address appeared
on the company's own page), 'linkedin', or 'inferred (<pattern>)'. Nothing is
asserted that cannot be traced back to a source_url.

Usage:
  PYTHONPATH=. python3 jobs/run_cmu_contacts.py [--limit N] [--no-llm]
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from jobs.contacts_lib import (build_email, clean_name, discover_email_pattern,
                               is_foreign_linkedin, parse_linkedin_result)
from msc import llm as llm_mod
from msc.extract import (CONTACT_PATHS, extract_domain, extract_emails,
                         extract_phone, is_generic_email)
from msc.net import Firecrawl, Http, Serper, fetch_smart

CSV_IN = Path("/Users/jacklumpe/Desktop/Construct Connect Scraper/cmu-linkedin/"
              "CMU_Producers_V2.csv")
OUT_DIR = Path("output/contacts/cmu")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Titles that actually buy or specify in this industry, taken from the title
# distribution in Kenni's own Region 1 list rather than a generic exec list.
# "Architectural Sales" and "Material Producer" are CMU-specific and would be
# missed by a standard leadership regex.
ROLE_BOOSTERS = [
    "",
    'president OR owner OR principal OR founder',
    '"vice president" OR VP OR "general manager" OR "plant manager"',
    'sales OR "sales manager" OR "director of sales" OR "architectural sales"',
    '"territory manager" OR "account manager" OR estimator OR "material producer"',
]

DECISION_TITLE = re.compile(
    r"\b(president|owner|principal|founder|ceo|coo|cfo|vice\s*president|vp\b|"
    r"general\s*manager|plant\s*manager|operations\s*manager|sales\s*manager|"
    r"director|architectural\s*sales|territory\s*manager|account\s*manager|"
    r"estimator|material\s*producer|partner|controller|manager)\b", re.I)

# Roles that are not a buying or specifying contact for a masonry ad.
ROLE_EXCLUDE = re.compile(
    r"\b(driver|laborer|apprentice|intern|student|retired|truck|yard\s*hand|"
    r"mechanic|operator|dispatcher|receptionist)\b", re.I)

_LEGAL = re.compile(
    r"\b(inc|llc|l\.l\.c|corp|corporation|company|co|ltd|limited|lp|llp|plc|"
    r"group|holdings|enterprises)\b\.?", re.I)
_CREDS = re.compile(
    r"\b(pmp|cpa|p\.?e\.?|mba|ph\.?d|aia|leed(\s*ap)?|cca|esq|csi|cdt|ms|bs)\b\.?", re.I)
# parse_linkedin_result strips a trailing "at <our company>" from the title. If
# an "at <employer>" survives, the employer named is somebody else, which means
# the surname simply collided with the company name (the "Justin Duchini,
# Senior Program Manager at Wabtec" case). Those are not our contacts.
_AT_OTHER = re.compile(r"\bat\s+[A-Z0-9]", re.I)


def _aliases(name: str) -> list[str]:
    """Full legal name plus a short brand token.

    The library's false-positive guards key on the SHORTEST alias, so passing
    only "A. Duchini, Inc." leaves them unable to spot that "Justin Duchini" is a
    surname match rather than an employee. Handing it "Duchini" as well restores
    that check.
    """
    full = " ".join(name.split())
    brand = _LEGAL.sub("", full)
    brand = re.sub(r"[.,]", " ", brand)
    brand = re.sub(r"^\s*[A-Za-z]\.?\s+(?=[A-Z])", "", brand)  # "A. Duchini" -> "Duchini"
    brand = re.sub(r"\s+", " ", brand).strip(" -&|")
    out = [full]
    if brand and brand.lower() != full.lower() and len(brand) >= 4:
        out.append(brand)
    return out


# Email-format directory sites publish worked examples ("format: flast, e.g.
# jdoe@acme.com"). Harvesting those as if they were real employees produced a
# pattern of 'firstlast' for a domain whose own documentation said 'flast', i.e.
# exactly backwards. Treat them as the documentation artifacts they are.
_PLACEHOLDER_LOCALS = {
    "jdoe", "johndoe", "janedoe", "john", "jane", "doe", "smith", "jsmith",
    "johnsmith", "janesmith", "first", "last", "firstlast", "first.last",
    "flast", "fllast", "lastf", "firstinitial", "name", "yourname", "username",
    "user", "email", "example", "sample", "test", "someone", "somebody",
    "abc", "xyz", "foo", "bar", "mail", "me", "you",
}
_MIN_PATTERN_EVIDENCE = 2


def _email_pattern(serper, http, domain: str) -> tuple[str, int]:
    """Deduce the local-part shape, but only from addresses that look like real
    people. Returns (pattern, evidence_count); evidence_count below
    _MIN_PATTERN_EVIDENCE means do not ship an inferred address at all, because a
    wrong address in a CRM costs more than a missing one."""
    pat, examples = discover_email_pattern(
        serper, http, domain,
        [f'"@{domain}"', f'"@{domain}" president OR sales OR manager'])
    real = [e for e in examples
            if e.lower() not in _PLACEHOLDER_LOCALS and not e.lower().startswith("test")]
    if len(real) < _MIN_PATTERN_EVIDENCE:
        return ("", len(real))
    # recount the shape over the surviving samples only
    shapes = Counter()
    for lp in real:
        if re.fullmatch(r"[a-z]+\.[a-z]+", lp):
            shapes["first.last"] += 1
        elif re.fullmatch(r"[a-z]+_[a-z]+", lp):
            shapes["first_last"] += 1
        elif re.fullmatch(r"[a-z]\.[a-z]+", lp):
            shapes["flast_dot"] += 1
        elif re.fullmatch(r"[a-z]{6,}", lp):
            shapes["firstlast"] += 1
    if not shapes:
        return ("", len(real))
    return (shapes.most_common(1)[0][0], len(real))


def _name_parts(name: str) -> tuple[str, str]:
    """First and last with credential suffixes removed, so an inferred address
    never comes out as justin.pmp@ instead of justin.duchini@."""
    # Enumerating every post-nominal is a losing game: PHR, SHRM-CP, CPSM and a
    # hundred others exist. A trailing ALL-CAPS token of 2 to 5 letters is a
    # credential, not a surname, so drop it structurally.
    raw = re.sub(r"[,;]\s*[A-Z][A-Za-z]*$", "", (name or "").strip())
    toks = raw.split()
    _CRED_TOK = re.compile(r"[A-Z]{2,5}([-.][A-Z]{1,5})*\.?")
    while len(toks) > 1 and _CRED_TOK.fullmatch(toks[-1]):
        toks.pop()
    cleaned = _CREDS.sub("", " ".join(toks))
    cleaned = re.sub(r"[^A-Za-z' -]", " ", cleaned)
    parts = [p for p in cleaned.split() if len(p) > 1]
    if len(parts) < 2:
        return ("", "")
    return (parts[0], parts[-1])


_CONTACTISH = re.compile(
    r"(contact|about|our-team|meet-the-team|team|leadership|staff|people|"
    r"management|who-we-are|locations)", re.I)


def _site_pages(domain: str, http: Http, max_pages: int = 4) -> str:
    """Homepage, then the contact-ish pages the homepage itself links to.

    Guessing paths does not work on these sites: /contact and /about 404 on most
    of them because they use /contact-us/, /about-us/ or a CMS permalink. Reading
    the homepage's own nav finds the real URLs. Firecrawl is deliberately not
    passed, since these render fine over plain HTTP and a credit per page across
    176 companies is not worth it.
    """
    if not domain:
        return ""
    # Small producer sites frequently have a certificate issued only for the www
    # host, or an expired/misconfigured one. Bare https fails with a hostname
    # mismatch and the whole company yields nothing, so try the obvious variants
    # before giving up. This recovers real producers (hagerstownblock.com,
    # karonmasonry.com), it is not just a speed fix.
    base, home = "", ""
    for cand in (f"https://{domain}", f"https://www.{domain}",
                 f"http://www.{domain}", f"http://{domain}"):
        try:
            method, text = fetch_smart(cand, http, firecrawl=None)
        except Exception:
            continue
        if method != "failed" and text and len(text) > 400:
            base, home = cand.rstrip("/"), text
            break
    if not home:
        return ""

    texts = [home]
    seen = {base, base + "/"}
    candidates = []
    for m in re.finditer(r'href=["\']([^"\'#?]+)["\']', home[:400000], re.I):
        href = m.group(1)
        if not _CONTACTISH.search(href):
            continue
        if href.startswith("//"):
            url = "https:" + href
        elif href.startswith("http"):
            url = href
        elif href.startswith("/"):
            url = base + href
        else:
            url = base + "/" + href
        if domain not in url or url in seen:
            continue
        seen.add(url)
        candidates.append(url)

    # contact pages first, they carry addresses; team pages carry names
    candidates.sort(key=lambda u: (0 if re.search(r"contact", u, re.I) else
                                   1 if re.search(r"team|staff|leadership|people", u, re.I)
                                   else 2))
    for url in candidates[: max_pages - 1]:
        try:
            method, text = fetch_smart(url, http, firecrawl=None)
        except Exception:
            continue
        if method != "failed" and text and len(text) > 400:
            texts.append(text)
    # Return the full text. Truncating here was silently dropping every result:
    # these homepages run to 700KB and the address and phone sit in the FOOTER,
    # well past any head-of-string cut. The LLM prompt is truncated instead.
    return "\n\n".join(texts)


def _from_site(company: str, domain: str, text: str, claude, today: str) -> list[dict]:
    """Named people off the company's own pages. Highest trust tier we have."""
    if not text:
        return []
    rows = []
    on_domain = {e.lower() for e in extract_emails(text) if extract_domain(e) == domain}
    page_emails = {e for e in on_domain if not is_generic_email(e)}
    # A shared inbox is still a real, deliverable route into a producer that
    # names nobody publicly, and Kenni's own Region 1 list is full of them. Keep
    # it, clearly labelled, rather than discarding a usable contact.
    generic_emails = sorted(on_domain - page_emails)
    company_phone = extract_phone(text) or ""

    if claude:
        data = claude.extract(
            prompt=f"Company: {company}\n\nWebsite text:\n\n{text[:60000]}",
            schema=llm_mod.CONTACT_SCHEMA,
            system=("You extract real human contacts from building-materials company "
                    "websites. Only include actual named people. Never return form "
                    "labels, testimonial authors, product names, or the company name "
                    "itself as a person. Decision makers here are owners, presidents, "
                    "VPs, general and plant managers, and sales leadership."),
            model=claude.fast_model,
        ) or {}
        for c in (data.get("contacts") or []):
            # clean_name returns (display, first, last, certs), not a string
            nm, nfirst, nlast, _certs = clean_name(c.get("name") or "")
            if not nm:
                continue
            title = (c.get("title") or "").strip()
            if ROLE_EXCLUDE.search(title):
                continue
            email = (c.get("email") or "").strip().lower()
            conf = ""
            if email and extract_domain(email) == domain:
                conf = "verified"
            else:
                email = ""
            # A surname is only needed to *infer* an address. Plenty of these
            # sites list staff as "Bruce - Sales, bruce@company.com", which is a
            # perfectly good contact; dropping it for want of a last name threw
            # away verified, deliverable people.
            if not (nfirst and nlast) and not conf:
                continue
            rows.append({
                "name": nm, "title": title, "email": email, "email_confidence": conf,
                "phone": (c.get("phone") or "").strip(),
                "linkedin": "", "source": "company website",
                "source_url": f"https://{domain}", "date": today,
                "_first": nfirst, "_last": nlast,
            })

    # An address on the page with no name attached is still a real, deliverable
    # contact point, so keep it rather than discarding it.
    claimed = {r["email"] for r in rows if r["email"]}
    for e in sorted(page_emails - claimed):
        rows.append({
            "name": "", "title": "", "email": e, "email_confidence": "verified",
            "phone": company_phone, "linkedin": "",
            "source": "company website (unattributed)",
            "source_url": f"https://{domain}", "date": today,
        })
    for e in generic_emails:
        rows.append({
            "name": "", "title": "Company mailbox", "email": e,
            "email_confidence": "verified (shared inbox)", "phone": company_phone,
            "linkedin": "", "source": "company website (shared inbox)",
            "source_url": f"https://{domain}", "date": today,
        })
    return rows


def _from_linkedin(company: str, serper: Serper, today: str,
                   per_query: int = 10) -> tuple[list[dict], Counter]:
    """Decision makers the website does not name."""
    aliases = _aliases(company)
    seen, rows, dropped = set(), [], Counter()
    for boost in ROLE_BOOSTERS:
        q = f'site:linkedin.com/in "{company}" {boost}'.strip()
        try:
            hits = serper.organic(q, num=per_query)
        except Exception:
            break
        for it in hits:
            link = it.get("link", "")
            if not link or link in seen or is_foreign_linkedin(link):
                continue
            cand = parse_linkedin_result(it.get("title", ""), it.get("snippet", ""),
                                         link, aliases)
            if not cand:
                continue
            title = cand.get("title", "")
            if _AT_OTHER.search(title):
                dropped["employed elsewhere"] += 1
                continue
            if ROLE_EXCLUDE.search(title):
                dropped["non-buying role"] += 1
                continue
            if not DECISION_TITLE.search(title):
                dropped["not a decision title"] += 1
                continue
            first, last = _name_parts(cand["name"])
            if not first or not last:
                dropped["unparseable name"] += 1
                continue
            seen.add(link)
            rows.append({
                "name": cand["name"], "title": title, "email": "", "email_confidence": "",
                "phone": "", "linkedin": link, "source": "linkedin",
                "source_url": link, "date": today, "_first": first, "_last": last,
            })
    return rows, dropped


def _dedupe(rows: list[dict]) -> list[dict]:
    """One row per person. Website beats LinkedIn because it is first-party, but
    a LinkedIn URL found later still gets folded onto the website row."""
    out, by_key = [], {}
    for r in sorted(rows, key=lambda x: 0 if "website" in x["source"] else 1):
        key = re.sub(r"[^a-z]", "", r["name"].lower()) or f"__email__{r['email']}"
        if key in by_key:
            m = by_key[key]
            for f in ("email", "phone", "linkedin", "title"):
                if not m.get(f) and r.get(f):
                    m[f] = r[f]
                    if f == "email" and r.get("email_confidence"):
                        m["email_confidence"] = r["email_confidence"]
            if r["source"] not in m["source"]:
                m["source"] += " + " + r["source"]
            continue
        by_key[key] = r
        out.append(r)
    return out


def run(limit: int = 0, use_llm: bool = True):
    import csv
    with open(CSV_IN) as fh:
        companies = list(csv.DictReader(fh))
    if limit:
        companies = companies[:limit]

    http, serper, fc = Http(), Serper(), Firecrawl()
    claude = llm_mod.Claude() if use_llm else None
    today = date.today().isoformat()
    mx_cache: dict[str, bool] = {}
    all_rows: list[dict] = []
    stats = Counter()

    for i, co in enumerate(companies, 1):
        name = co["companyname"].strip()
        domain = (co.get("companyemaildomain") or "").strip().lower()
        ckpt = OUT_DIR / (re.sub(r"[^a-z0-9]+", "_", name.lower())[:60] + ".json")
        if ckpt.exists():
            rows = json.loads(ckpt.read_text())
            all_rows.extend(rows)
            stats["resumed"] += 1
            print(f"[{i}/{len(companies)}] {name[:44]:46s} cached {len(rows)}")
            continue

        text = _site_pages(domain, http)
        rows = _from_site(name, domain, text, claude, today)
        li_rows, dropped = _from_linkedin(name, serper, today)
        rows += li_rows
        for k, v in dropped.items():
            stats[k] += v
        rows = _dedupe(rows)

        # Email inference last, and only for people we still have no address for.
        named = [r for r in rows if r["name"] and not r["email"]]
        if named and domain:
            pattern, evidence = _email_pattern(serper, http, domain)
            if not pattern:
                stats["no_pattern_evidence"] += 1
            for r in (named if pattern else []):
                first = r.get("_first") or ""
                last = r.get("_last") or ""
                if not first or not last:
                    first, last = _name_parts(r["name"])
                if not first or not last:
                    continue
                email, conf = build_email(first, last, domain, pattern, http, mx_cache)
                if email:
                    r["email"] = email
                    r["email_confidence"] = f"inferred ({pattern}, {evidence} samples)"

        for r in rows:
            r["company"] = name
            r["company_website"] = co.get("companywebsite", "")
            r["city"] = co.get("city", "")
            r["state"] = co.get("state", "")
            r.pop("_first", None)
            r.pop("_last", None)

        ckpt.write_text(json.dumps(rows, indent=1))
        all_rows.extend(rows)
        stats["scraped"] += 1
        stats["with_name"] += sum(1 for r in rows if r["name"])
        print(f"[{i}/{len(companies)}] {name[:44]:46s} {len(rows):3d} contacts "
              f"({sum(1 for r in rows if r['name'])} named)  "
              f"serper={serper.queries_spent}")

    out = OUT_DIR.parent / "cmu_contacts_raw.json"
    out.write_text(json.dumps(all_rows, indent=1))

    named = [r for r in all_rows if r["name"]]
    verified = [r for r in all_rows if r["email_confidence"] == "verified"]
    print(f"\ncompanies processed : {len(companies)} ({stats['resumed']} from cache)")
    print(f"contact rows        : {len(all_rows):,}")
    print(f"  with a real name  : {len(named):,}")
    print(f"  with an email     : {sum(1 for r in all_rows if r['email']):,}")
    print(f"    verified on site: {len(verified):,}")
    print(f"  with a phone      : {sum(1 for r in all_rows if r['phone']):,}")
    print(f"  with LinkedIn     : {sum(1 for r in all_rows if r['linkedin']):,}")
    print(f"serper spent        : {serper.queries_spent} queries")
    print(f"firecrawl spent     : {fc.credits_spent} credits")
    print(f"-> {out}")
    return all_rows


if __name__ == "__main__":
    lim = 0
    if "--limit" in sys.argv:
        lim = int(sys.argv[sys.argv.index("--limit") + 1])
    run(limit=lim, use_llm="--no-llm" not in sys.argv)
