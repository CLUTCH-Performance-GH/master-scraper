"""Turn the CMU contact run into the client deliverable.

Three tabs:
  Contacts   one row per person, sorted so the most actionable are at the top
  Companies  one row per producer with coverage counts, so gaps are visible
  Sources    how every field was obtained and what each confidence label means

Sorting puts a named decision maker with a verified email first and an
unattributed shared inbox last, because that is the order someone working the
list actually wants.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from msc.extract import is_generic_email
from msc.workbook import rows_from_dicts, write_workbook

RAW = Path("output/contacts/cmu_contacts_raw.json")
COMPANIES_CSV = Path("/Users/jacklumpe/Desktop/Construct Connect Scraper/cmu-linkedin/"
                     "CMU_Producers_V2.csv")
OUT = Path("output/CMU_Producer_Contacts_V3.xlsx")

# msc.extract ships only 12 generic mailboxes, which left questions@, feedback@,
# ap@, media@, people@, claims@ and jobs@ labelled plain "verified" as though a
# person were behind them. A shared inbox is a fine contact, but calling it a
# named one overstates the list.
EXTRA_GENERIC = {
    "questions", "question", "feedback", "ap", "ar", "accounting", "accounts",
    "payables", "receivables", "media", "press", "pr", "people", "hr", "careers",
    "jobs", "recruiting", "employment", "claims", "inquire", "inquiry",
    "enquiries", "help", "helpdesk", "customerservice", "cs", "orders", "order",
    "quotes", "quote", "estimating", "estimates", "bids", "bidding", "billing",
    "invoices", "marketing", "webmail", "postmaster", "noreply", "no-reply",
    "donotreply", "notifications", "alerts", "ir-info", "investor", "investors",
    "general", "main", "reception", "frontdesk", "team", "staff", "company",
    "corporate", "hq", "safety", "compliance", "legal", "purchasing",
    "procurement", "shipping", "receiving", "dispatch", "scheduling", "service",
    "support", "returns", "warranty", "training", "events", "newsletter",
    "subscribe", "unsubscribe", "privacy", "webmaster", "admin", "administrator",
}

SENIORITY = re.compile(
    r"\b(owner|president|ceo|founder|principal|partner)\b", re.I)
SENIOR2 = re.compile(
    r"\b(vice\s*president|vp|general\s*manager|director|plant\s*manager)\b", re.I)


_PARTICLES = {"de", "van", "von", "der", "la", "le", "mc", "mac", "st", "di", "da"}


def _clean_email(email: str) -> str:
    """Strip harvesting artifacts off an address.

    Emails scraped out of href="mailto:..." attributes arrive carrying URL
    encoding and stray punctuation: '%20jbmandes@...', '-recovery@...'. Those
    would bounce exactly as written, and they also defeat name recovery, so the
    local part is cleaned before anything else looks at it.
    """
    e = (email or "").strip().lower()
    if not e or "@" not in e:
        return ""
    e = re.sub(r"%[0-9a-f]{2}", "", e)          # %20 and friends
    e = e.replace("mailto:", "").strip()
    local, _, host = e.partition("@")
    local = local.strip(" .-_+")
    host = host.strip(" .-_")
    if not local or not host or "." not in host:
        return ""
    return f"{local}@{host}"


def _is_generic_local(local: str) -> bool:
    base = re.sub(r"[^a-z]", "", local.lower())
    return (local.lower() in EXTRA_GENERIC or base in EXTRA_GENERIC
            or is_generic_email(f"{local}@x.com"))


def _name_from_email(email: str, company: str, known: dict[str, str]) -> tuple[str, str]:
    """Recover a person's name from a person-shaped address.

    Returns (display_name, how). A verified address like annika.fuchs@ or
    johnbock@bockbrick.example plainly identifies a person; leaving those rows blank
    was throwing away the very contacts the client asked for. Nothing is invented:
    only what the local part literally spells is used, and an address that cannot
    be read as a name stays blank.
    """
    if not email or "@" not in email:
        return ("", "")
    local = email.split("@")[0]
    if _is_generic_local(local):
        return ("", "")
    low = re.sub(r"\d+$", "", local.lower())
    if len(low) < 4:
        return ("", "")   # initials only, e.g. mb@ or fb@

    cap = lambda s: s[:1].upper() + s[1:] if s else s

    # first.last / first_last / first-last: unambiguous
    m = re.fullmatch(r"([a-z]{2,})[._-]([a-z]{2,})(?:[._-]([a-z]{1,}))?", low)
    if m:
        first, last = m.group(1), m.group(2)
        if m.group(3) and len(m.group(3)) > 1 and last in _PARTICLES:
            last = f"{last} {m.group(3)}"
        return (f"{cap(first)} {cap(last)}", "derived from email (first.last)")

    # A name we already know for this company that the address matches, e.g.
    # LinkedIn gave us "John Bock" and the site gave johnbock@ -> same person.
    for full, flat in known.items():
        if flat and flat == low:
            return (full, "matched to a known contact")

    # initial + surname, where the surname is a word in the company name:
    # johnbock@bockbrick.example -> John Bock
    ctoks = [t for t in re.split(r"[^a-z]+", company.lower()) if len(t) > 2]
    for t in ctoks:
        if low.endswith(t) and len(low) > len(t) + 2:
            return (f"{cap(low[:-len(t)])} {cap(t)}",
                    "derived from email (name + company surname)")

    # jdockray@ -> J. Dockray. The surname is certain, the first name is not,
    # so it is labelled as partial rather than dressed up as a full name.
    m = re.fullmatch(r"([a-z])([a-z]{3,})", low)
    if m:
        return (f"{m.group(1).upper()}. {cap(m.group(2))}",
                "derived from email (initial + surname, partial)")
    return ("", "")


def _tier(r: dict) -> int:
    """Lower sorts higher. Named + verified email is the most useful row there is."""
    named = bool(r.get("name"))
    conf = r.get("email_confidence", "")
    verified = conf.startswith("verified") and "shared" not in conf
    has_email = bool(r.get("email"))
    if named and verified:
        return 0
    if named and has_email:
        return 1
    if named and r.get("linkedin"):
        return 2
    if named:
        return 3
    if verified:
        return 4
    return 5


def _rank(r: dict) -> tuple:
    t = r.get("title", "")
    sen = 0 if SENIORITY.search(t) else (1 if SENIOR2.search(t) else 2)
    return (_tier(r), sen, r.get("company", ""), r.get("name", ""))


_LEGAL = re.compile(
    r"\b(inc|llc|l\.l\.c|corp|corporation|company|co|ltd|limited|lp|llp|plc|"
    r"group|holdings|enterprises|products)\b\.?", re.I)


def _brand_key(name: str) -> str:
    """Collapse 'York Building' / 'York Building Products' / 'York Building
    Products Company' to one key. The company list carries the same producer
    under several legal-name variants because it was merged from two sources,
    and without this the same person appears three times on a call list."""
    s = _LEGAL.sub("", str(name))
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _attach_verified(rows: list[dict]) -> int:
    """Bind verified page emails to the people we found on LinkedIn.

    The site rung harvests real addresses but usually cannot say whose they are,
    while the LinkedIn rung finds names but no address. Matching the local part
    against a known person at the same company upgrades a guessed address to a
    confirmed one and removes an orphan row. This is the single biggest quality
    lift available: an inferred address might bounce, a verified one will not.
    """
    by_brand = defaultdict(list)
    for r in rows:
        by_brand[_brand_key(r["company"])].append(r)

    upgraded = 0
    for group in by_brand.values():
        people = [r for r in group if r.get("name")]
        orphans = [r for r in group
                   if not r.get("name") and r.get("email")
                   and r["email_confidence"].startswith("verified")
                   and "shared" not in r["email_confidence"]]
        if not people or not orphans:
            continue
        for orph in orphans:
            local = orph["email"].split("@")[0].lower()
            local_alpha = re.sub(r"[^a-z]", "", local)
            for p in people:
                parts = [x for x in re.split(r"[^A-Za-z]+", p["name"].lower()) if len(x) > 1]
                if len(parts) < 2:
                    continue
                f, l = parts[0], parts[-1]
                if local in {f"{f}.{l}", f"{f}_{l}", f"{f}{l}", f"{f[0]}{l}",
                             f"{f[0]}.{l}", f"{l}{f[0]}", f, l} or local_alpha == f"{f}{l}":
                    p["email"] = orph["email"]
                    p["email_confidence"] = "verified (matched to person)"
                    if not p.get("phone") and orph.get("phone"):
                        p["phone"] = orph["phone"]
                    orph["_drop"] = True
                    upgraded += 1
                    break
    return upgraded


def _dedupe_people(rows: list[dict]) -> tuple[list[dict], int]:
    """One row per person per producer, across company-name variants."""
    seen, out, dropped = {}, [], 0
    for r in rows:
        if r.get("_drop"):
            continue
        key = (_brand_key(r["company"]),
               re.sub(r"[^a-z]", "", r.get("name", "").lower()) or r.get("email", "").lower())
        if not key[1]:
            out.append(r)
            continue
        if key in seen:
            m = seen[key]
            for f in ("email", "phone", "linkedin", "title"):
                if not m.get(f) and r.get(f):
                    m[f] = r[f]
                    if f == "email":
                        m["email_confidence"] = r["email_confidence"]
            dropped += 1
            continue
        seen[key] = r
        out.append(r)
    return out, dropped


def main():
    rows = json.loads(RAW.read_text())

    # Re-join location from the company file rather than trusting what the crawl
    # stored: state resolution ran afterwards and filled 76 producers the crawl
    # had no location for, and a contact's state is only ever their employer's.
    import csv as _c
    loc = {}
    for c in _c.DictReader(open(COMPANIES_CSV)):
        loc[_brand_key(c["companyname"])] = (c.get("city", ""), c.get("state", ""))
    relocated = 0
    for r in rows:
        city, st = loc.get(_brand_key(r["company"]), ("", ""))
        if st and st != r.get("state"):
            relocated += 1
        if city:
            r["city"] = city
        if st:
            r["state"] = st
    print(f"  contact rows given a state from the company file: {relocated}")

    # Normalise addresses before anything reads them: an address carrying a %20
    # would be shipped undeliverable.
    fixed = 0
    for r in rows:
        raw = r.get("email") or ""
        clean = _clean_email(raw)
        if clean != raw.strip().lower():
            fixed += 1
        r["email"] = clean
        if not clean:
            r["email_confidence"] = ""
    print(f"  malformed addresses repaired: {fixed}")

    # Relabel shared inboxes the framework's short generic list missed, so
    # "verified" means a person and never a department.
    relabelled = 0
    for r in rows:
        em = r.get("email") or ""
        if em and r.get("email_confidence", "").startswith("verified") \
                and "shared" not in r["email_confidence"]:
            if _is_generic_local(em.split("@")[0]):
                r["email_confidence"] = "verified (shared inbox)"
                if not r.get("title"):
                    r["title"] = "Company mailbox"
                relabelled += 1
    print(f"  shared inboxes relabelled from 'verified': {relabelled}")

    # Recover names from person-shaped addresses.
    known_by_co: dict[str, dict[str, str]] = defaultdict(dict)
    for r in rows:
        if r.get("name"):
            known_by_co[r["company"]][r["name"]] = re.sub(r"[^a-z]", "", r["name"].lower())
    derived = Counter()
    for r in rows:
        if r.get("name") or not r.get("email"):
            continue
        nm, how = _name_from_email(r["email"], r["company"], known_by_co[r["company"]])
        if nm:
            r["name"] = nm
            r["name_source"] = how
            derived[how] += 1
    print(f"  names recovered from verified emails: {sum(derived.values())}")
    for k, v in derived.most_common():
        print(f"      {v:3d}  {k}")

    # Shared inboxes are out. Nobody works an info@ address, so a row that is
    # only a department mailbox is noise on a call list. A named person who
    # happens to sit behind one is a different case: keep the person and their
    # phone, and just drop the address so it never gets mailed.
    dropped_shared, cleared_shared = 0, 0
    kept = []
    for r in rows:
        is_shared = r.get("email_confidence") == "verified (shared inbox)"
        if not is_shared:
            kept.append(r)
            continue
        if r.get("name"):
            r["email"] = ""
            r["email_confidence"] = ""
            if r.get("title") == "Company mailbox":
                r["title"] = ""
            cleared_shared += 1
            kept.append(r)
        else:
            dropped_shared += 1
    rows = kept
    print(f"  shared-inbox rows dropped: {dropped_shared}")
    print(f"  named people kept with the shared address cleared: {cleared_shared}")

    # anything left with neither a name nor an email is not a contact
    before = len(rows)
    rows = [r for r in rows if r.get("name") or r.get("email")]
    if before - len(rows):
        print(f"  empty rows removed: {before - len(rows)}")

    upgraded = _attach_verified(rows)
    rows, dropped = _dedupe_people(rows)
    print(f"  verified emails matched to a named person: {upgraded}")
    print(f"  duplicate person rows collapsed          : {dropped}")
    for r in rows:
        r["seniority"] = ("Owner / President" if SENIORITY.search(r.get("title", ""))
                          else "VP / Director / Manager" if SENIOR2.search(r.get("title", ""))
                          else "Other" if r.get("title") else "")
    rows.sort(key=_rank)

    # ---- Companies tab: coverage per producer, including the misses ----------
    import csv
    with open(COMPANIES_CSV) as fh:
        companies = list(csv.DictReader(fh))
    by_co = defaultdict(list)
    for r in rows:
        by_co[_brand_key(r["company"])].append(r)

    # collapse the input list's own name variants so one producer is one row
    seen_brands = {}
    for c in companies:
        b = _brand_key(c["companyname"])
        prev = seen_brands.get(b)
        # keep the fullest name, and any city/state either variant carries
        if prev is None:
            seen_brands[b] = dict(c)
        else:
            if len(c["companyname"]) > len(prev["companyname"]):
                merged = dict(c)
                for k, v in prev.items():
                    if not merged.get(k) and v:
                        merged[k] = v
                seen_brands[b] = merged
            else:
                for k, v in c.items():
                    if not prev.get(k) and v:
                        prev[k] = v
    companies = list(seen_brands.values())

    co_rows = []
    for c in companies:
        nm = c["companyname"].strip()
        rs = by_co.get(_brand_key(nm), [])
        named = [x for x in rs if x["name"]]
        co_rows.append({
            "company": nm,
            "city": c.get("city", ""), "state": c.get("state", ""),
            "website": c.get("companywebsite", ""),
            "email_domain": c.get("companyemaildomain", ""),
            "contacts_found": len(rs),
            "named_people": len(named),
            "with_email": sum(1 for x in rs if x["email"]),
            "verified_emails": sum(1 for x in rs if x["email_confidence"].startswith("verified")),
            "with_phone": sum(1 for x in rs if x["phone"]),
            "with_linkedin": sum(1 for x in rs if x["linkedin"]),
            "top_contact": named[0]["name"] if named else "",
            "top_title": named[0]["title"] if named else "",
            "status": ("named contact" if named
                       else "email only" if rs
                       else "no contact found"),
        })
    co_rows.sort(key=lambda r: (-r["named_people"], -r["contacts_found"], r["company"]))

    # ---- Sources tab ---------------------------------------------------------
    notes = [
        ["Company universe", f"{len(companies)} CMU / concrete masonry producers across CT, DE, "
                             "ME, MD, MA, NH, NJ, NY, PA, RI, VT, WV. Built from Kenni's Region 1 "
                             "master list plus the CMHA producer directory."],
        ["How contacts were found", "Per company, cheapest source first: the company's own website "
                                    "(homepage plus the contact and team pages it links to), then "
                                    "LinkedIn profile search via Serper for decision makers the "
                                    "site does not name."],
        ["verified", "The address appeared on the company's own website. Highest trust."],
        ["Shared inboxes excluded", "Department mailboxes (info@, sales@, questions@, ap@, "
                                    "careers@ and similar) are deliberately not in this file. They "
                                    "are deliverable but nobody works them, so they inflate a count "
                                    "without adding a workable contact. Where a named person sat "
                                    "behind one, the person and their phone were kept and only the "
                                    "shared address was removed."],
        ["inferred (pattern, N samples)", "Constructed from the company's observed address format. "
                                          "Only emitted when at least 2 genuine employee addresses "
                                          "were found to establish the pattern, and only after the "
                                          "domain passed an MX check so it can actually receive "
                                          "mail. Treat as high-probability, not confirmed."],
        ["Why some rows have no email", "A pattern backed by fewer than 2 real samples is a guess. "
                                        "Email-format directory sites publish worked examples "
                                        "(jdoe@, flast@) that look like real staff and will produce "
                                        "confidently wrong addresses if trusted. Those are filtered "
                                        "out and no address is emitted rather than shipping one "
                                        "that bounces or reaches the wrong person."],
        ["Wrong-company guard", "LinkedIn results whose title reads 'at <some other employer>' are "
                                "dropped. Without this, a person who merely shares a surname with "
                                "the company (Justin Duchini at Wabtec vs A. Duchini, Inc.) is "
                                "attributed to the wrong firm."],
        ["Titles targeted", "Owners, presidents, VPs, general and plant managers, sales leadership, "
                            "architectural sales, territory and account managers, estimators. Taken "
                            "from the title mix in the Region 1 list rather than a generic "
                            "executive list, because 'Architectural Sales' and 'Material Producer' "
                            "are specific to this industry."],
        ["Not verified", "Job titles and employment are as published by the source at the date "
                         "shown and are not independently confirmed. People change roles."],
        ["Personal data", "Named individuals with business contact details. Business contact data "
                          "is still personal data under CCPA/CPRA. Treat distribution as "
                          "controlled and confirm permitted use before loading to a CRM or "
                          "outbound tool."],
        ["Generated", date.today().isoformat()],
    ]

    c_headers = ["Company", "City", "State", "Name", "Title", "Seniority", "Email",
                 "Email Confidence", "Phone", "LinkedIn", "Source", "Source URL", "Date"]
    c_map = {"Company": "company", "City": "city", "State": "state", "Name": "name",
             "Title": "title", "Seniority": "seniority", "Email": "email",
             "Email Confidence": "email_confidence", "Phone": "phone",
             "LinkedIn": "linkedin", "Source": "source", "Source URL": "source_url",
             "Date": "date"}

    co_headers = ["Company", "City", "State", "Status", "Named People", "Contacts Found",
                  "With Email", "Verified Emails", "With Phone", "With LinkedIn",
                  "Top Contact", "Top Title", "Website", "Email Domain"]
    co_map = {h: h.lower().replace(" ", "_") for h in co_headers}
    co_map.update({"Company": "company", "Website": "website", "Email Domain": "email_domain"})

    # ---- per-state tabs ------------------------------------------------------
    # A rep works one state at a time, so give each its own tab rather than
    # making them filter the master list. State comes from the company record,
    # which is why a contact inherits their employer's state.
    STATE_NAMES = {
        "CT": "Connecticut", "DE": "Delaware", "ME": "Maine", "MD": "Maryland",
        "MA": "Massachusetts", "NH": "New Hampshire", "NJ": "New Jersey",
        "NY": "New York", "PA": "Pennsylvania", "RI": "Rhode Island",
        "VT": "Vermont", "WV": "West Virginia",
    }
    by_state = defaultdict(list)
    for r in rows:
        st = (r.get("state") or "").strip().upper()
        if st in STATE_NAMES:
            by_state[st].append(r)

    sheets = {
        "Contacts (All)": (c_headers, rows_from_dicts(rows, c_headers, c_map)),
        "Companies": (co_headers, rows_from_dicts(co_rows, co_headers, co_map)),
    }
    # every target state gets a tab, including the empty ones, so a gap is
    # visible as a gap rather than looking like the state was never searched
    for st in ["CT", "DE", "ME", "MD", "MA", "NH", "NJ", "NY", "PA", "RI", "VT", "WV"]:
        srows = by_state.get(st, [])
        tab = f"{st} - {STATE_NAMES[st]}"[:31]  # Excel caps sheet names at 31
        sheets[tab] = (c_headers, rows_from_dicts(srows, c_headers, c_map))

    # Contacts that resolved to a state outside the twelve, and contacts whose
    # employer we could not place at all. Separating these matters: the first
    # group is a scoping question (a national brand sitting in a regional list),
    # the second is a data gap. Rolling them together would hide both.
    out_region = [r for r in rows
                  if (r.get("state") or "").upper() not in STATE_NAMES
                  and (r.get("state") or "").strip()]
    no_state = [r for r in rows if not (r.get("state") or "").strip()]
    sheets["Out of Region"] = (c_headers, rows_from_dicts(out_region, c_headers, c_map))
    sheets["State Unknown"] = (c_headers, rows_from_dicts(no_state, c_headers, c_map))
    sheets["Sources & Method"] = (["Item", "Detail"], notes)

    write_workbook(OUT, sheets)

    # ---- per-state CSVs ------------------------------------------------------
    import csv as _csv
    state_dir = OUT.parent / "cmu_by_state"
    state_dir.mkdir(exist_ok=True)
    for st in STATE_NAMES:
        srows = by_state.get(st, [])
        with open(state_dir / f"CMU_Contacts_{st}.csv", "w", newline="") as fh:
            w = _csv.writer(fh)
            w.writerow(c_headers)
            w.writerows(rows_from_dicts(srows, c_headers, c_map))

    print("\n  per-state contact counts:")
    for st in sorted(STATE_NAMES, key=lambda s: -len(by_state.get(s, []))):
        n = len(by_state.get(st, []))
        named_n = sum(1 for r in by_state.get(st, []) if r.get("name"))
        cos = len({r["company"] for r in by_state.get(st, [])})
        print(f"    {st} {STATE_NAMES[st]:16s} {n:4d} contacts  {named_n:4d} named  "
              f"{cos:3d} producers")
    in_region = sum(len(v) for k, v in by_state.items() if k in STATE_NAMES)
    print(f"\n    in the 12 target states : {in_region}")
    print(f"    out of region (HQ elsewhere): {len(out_region)}  -> 'Out of Region' tab")
    print(f"    state unknown           : {len(no_state)}  -> 'State Unknown' tab")
    print(f"  per-state CSVs -> {state_dir}")

    named = [r for r in rows if r["name"]]
    print(f"wrote {OUT}")
    print(f"  contacts            : {len(rows):,}")
    print(f"    named people      : {len(named):,}")
    print(f"    with email        : {sum(1 for r in rows if r['email']):,}")
    print(f"      verified        : {sum(1 for r in rows if r['email_confidence'].startswith('verified')):,}")
    print(f"      inferred        : {sum(1 for r in rows if r['email_confidence'].startswith('inferred')):,}")
    print(f"    with phone        : {sum(1 for r in rows if r['phone']):,}")
    print(f"    with LinkedIn     : {sum(1 for r in rows if r['linkedin']):,}")
    print(f"  companies           : {len(co_rows):,}")
    print(f"    with named contact: {sum(1 for r in co_rows if r['named_people']):,}")
    print(f"    no contact found  : {sum(1 for r in co_rows if r['status'] == 'no contact found'):,}")
    print("  seniority:", dict(Counter(r["seniority"] for r in named)))


if __name__ == "__main__":
    main()
