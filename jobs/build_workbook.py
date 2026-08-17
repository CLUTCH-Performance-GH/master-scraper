"""Assemble the deliverable Excel workbook from output/contacts/*.json.

Layout:
  Tab 1  Coverage Audit  — per company x state: people, email%, phone%, state-confirmed%
  Tab 2  MASTER          — every person, all companies, autofilter (slice any way)
  Tab 3+ one per company — leaders sorted to top

Run:  PYTHONPATH=. python3 jobs/build_workbook.py [VERSION]
"""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from msc.workbook import write_workbook

_nk = lambda s: re.sub(r"[^a-z]", "", (s or "").lower())

# --- external list to de-duplicate AGAINST (Dennis's other search) ----------
EXTERNAL_CSV = Path("/Users/jacklumpe/Downloads/NEw Contacts For Dennis.csv")
_GENERIC_CO = {"ag", "solutions", "inc", "incorporated", "company", "co", "llc",
               "cooperative", "coop", "services", "service", "grower", "growers",
               "group", "enterprises", "the", "of", "and", "farms", "agri",
               "agronomy", "grain", "rice", "mill", "foods", "corp", "corporation",
               "capital", "international", "global", "us", "usa"}


def _brand_tokens(name):
    return {t for t in re.findall(r"[a-z]+", (name or "").lower())
            if t not in _GENERIC_CO and len(t) > 2}


def _norm_li(u):
    u = (u or "").split("?")[0].strip().lower().rstrip("/")
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^[a-z0-9-]+\.linkedin\.com", "linkedin.com", u)  # strip www/country sub
    return u if "linkedin.com/in/" in u else ""


def load_external_exclusions(path):
    """Build match keys from the external CSV: LinkedIn URLs, emails, name|brand."""
    li, emails, namebrand = set(), set(), set()
    if not path.exists():
        return li, emails, namebrand
    with open(path, newline="", encoding="utf-8", errors="ignore") as f:
        for row in csv.DictReader(f):
            u = _norm_li(row.get("Contact LI Profile URL", ""))
            if u:
                li.add(u)
            for col in ("Email 1", "Email 2", "Contact Email"):
                e = (row.get(col) or "").strip().lower()
                if "@" in e:
                    emails.add(e)
            nm = _nk((row.get("First Name") or "") + (row.get("Last Name") or ""))
            if not nm:
                nm = _nk(row.get("Contact Full Name", ""))
            for b in _brand_tokens(row.get("Company Name - Cleaned", "")):
                if nm:
                    namebrand.add(nm + "|" + b)
    return li, emails, namebrand


def in_external(r, li, emails, namebrand):
    if _norm_li(r.get("linkedin", "")) in li and li:
        return True
    e = (r.get("email") or "").strip().lower()
    if e and e in emails:
        return True
    nm = _nk(r.get("name", ""))
    return bool(nm) and any(nm + "|" + b in namebrand for b in _brand_tokens(r.get("company", "")))


def _richness(r):
    return (len(r.get("title", "")) +
            (50 if str(r.get("email_confidence", "")).startswith("verified") else 0) +
            (20 if r.get("state_confidence") == "confirmed" else 0) +
            (10 if r.get("phone") else 0))


def dedup(by_company):
    """Safety net: unique person per (company, normalized name) and globally
    unique LinkedIn URL. Keeps the richest record."""
    seen_li = set()
    for key, rows in by_company.items():
        out, seen_name = [], set()
        for r in sorted(rows, key=_richness, reverse=True):
            li, nk = r.get("linkedin", ""), _nk(r.get("name"))
            if (li and li in seen_li) or (nk and nk in seen_name):
                continue
            if li:
                seen_li.add(li)
            if nk:
                seen_name.add(nk)
            out.append(r)
        by_company[key] = out
    return by_company

CONTACTS = Path("output/contacts")
OUT = Path("output")

# nice column headers (display) mapped to row-dict keys
COLUMNS = [
    ("Company", "company"), ("State", "state"), ("State Confidence", "state_confidence"),
    ("City", "city"), ("Name", "name"), ("Title", "title"),
    ("Role Category", "role_category"), ("Employment", "employment_disp"),
    ("Current LinkedIn Title", "current_role"), ("Email", "email"),
    ("Email Confidence", "email_confidence"), ("Phone", "phone"),
    ("Phone Type", "phone_type"), ("LinkedIn", "linkedin"),
    ("Source URL", "source_url"), ("Date", "date"), ("Notes", "notes"),
]
EMP_DISP = {"current": "Current (verified)", "unverified": "Unconfirmed - verify",
            "former": "Former - departed"}
EMP_RANK = {"current": 0, "unverified": 1, "former": 2}
HEADERS = [c[0] for c in COLUMNS]
KEYS = [c[1] for c in COLUMNS]

# leaders first
ROLE_ORDER = {
    "Leadership/Exec": 0, "Division/Regional Leadership": 1, "Sales": 2,
    "Agronomy": 3, "Retail/Branch Ops": 4, "Technical/Specialty": 5,
    "Corporate/HQ (review)": 6, "Other-Ag (review)": 7,
    "Unknown title (review)": 8, "Unclassified (review)": 9,
}

# pretty tab names per company-file key
TAB_NAMES = {
    "nutrien": "Nutrien", "chs": "CHS", "aurora": "Aurora Coop",
    "farmers_rice": "Farmers Rice", "helena": "Helena Agri", "mcgregor": "McGregor",
    "mkc": "MKC Grain", "poinsett": "Poinsett Rice & Grain",
    "producers_rice": "Producers Rice Mill", "riceland": "Riceland Co-op",
    "simplot": "Simplot", "skyland": "Skyland Grain", "supreme_rice": "Supreme Rice",
    "triton": "Triton Fumigation", "wilbur_ellis": "Wilbur-Ellis",
}


def sort_key(r):
    return (EMP_RANK.get(r.get("employment", "unverified"), 1),  # verified-current first
            ROLE_ORDER.get(r.get("role_category", ""), 99),
            r.get("state", ""), r.get("name", ""))


def to_rows(people):
    return [[p.get(k, "") for k in KEYS] for p in sorted(people, key=sort_key)]


def main(version="V1"):
    files = sorted(CONTACTS.glob("*.json"))
    by_company = {f.stem: json.loads(f.read_text()) for f in files if f.stem in TAB_NAMES}
    by_company = dedup(by_company)

    # main tabs = CONFIRMED CURRENT ONLY and NOT already in the external list.
    # Everything else (unconfirmed, departed, or overlap) goes to the Excluded tab.
    li_ext, email_ext, nb_ext = load_external_exclusions(EXTERNAL_CSV)
    excluded, overlap_n = [], 0
    for key in list(by_company):
        keep = []
        for r in by_company[key]:
            r["employment_disp"] = EMP_DISP.get(r.get("employment", "unverified"), "Unconfirmed - verify")
            if in_external(r, li_ext, email_ext, nb_ext):
                r["employment_disp"] = "Removed - in other (Dennis) list"
                r["notes"] = (r.get("notes", "") + " [in other search list]").strip()
                excluded.append(r)
                overlap_n += 1
            elif r.get("employment") == "current":
                keep.append(r)
            else:
                excluded.append(r)
        by_company[key] = keep
    all_people = [p for ppl in by_company.values() for p in ppl]
    print(f"external-list overlap removed: {overlap_n} (CSV keys: {len(li_ext)} LI, "
          f"{len(email_ext)} email, {len(nb_ext)} name|brand)")

    sheets = {}

    # ---- Coverage Audit (first) ----
    audit_rows = []
    grid = defaultdict(list)
    for p in all_people:
        grid[(p["company"], p["state"])].append(p)
    for (co, st), ppl in sorted(grid.items()):
        n = len(ppl)
        audit_rows.append([
            co, st, n,
            sum(1 for p in ppl if p.get("email")),
            sum(1 for p in ppl if p.get("phone")),
            sum(1 for p in ppl if p.get("state_confidence") == "confirmed"),
            f'{round(100*sum(1 for p in ppl if p.get("source_url"))/n)}%' if n else "0%",
        ])
    sheets["Coverage Audit"] = (
        ["Company", "State", "Confirmed Current", "With Email", "With Phone",
         "State Confirmed", "Source URL %"], audit_rows)

    # ---- MASTER ----
    sheets["MASTER (all companies)"] = (HEADERS, to_rows(all_people))

    # ---- per-company tabs ----
    for key in sorted(by_company, key=lambda k: TAB_NAMES.get(k, k)):
        if by_company[key]:
            sheets[TAB_NAMES.get(key, key)[:31]] = (HEADERS, to_rows(by_company[key]))

    # ---- Excluded (unconfirmed + departed; kept for transparency, not in main) ----
    if excluded:
        sheets["Excluded (unconfirmed+left)"[:31]] = (HEADERS, to_rows(excluded))

    path = OUT / f"Ag_Sales_Contacts_Master_{version}.xlsx"
    write_workbook(path, sheets)

    print(f"=== {path} ===")
    print(f"companies: {len(by_company)} | MAIN TABS (confirmed current only): {len(all_people)} "
          f"| excluded (unconfirmed+departed): {len(excluded)}")
    n_email = sum(1 for p in all_people if p.get("email"))
    n_phone = sum(1 for p in all_people if p.get("phone"))
    print(f"with email: {n_email}/{len(all_people)} | with phone: {n_phone}")
    return path


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "V1")
