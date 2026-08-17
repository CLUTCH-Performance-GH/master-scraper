"""Roll the ConstructConnect contact list up by Regional Advisory Council.

The TAM model sizes each region. This answers the operational question next to
it: who is there to call in each region, and how many of them sit on projects
that actually consume ASSESSABLE concrete masonry rather than exempt hardscape.

Two counts are reported everywhere and they are not the same number:

  contact ROWS      one per person-per-project. 23,420 in the V3 pull.
  DISTINCT people   deduplicated. A specifier on eleven projects is one call.

Reporting rows as if they were people inflates the callable universe by roughly
a third. Rows are the right unit for "how much project coverage do we have";
distinct people are the right unit for "how many calls is that".

Dedupe key, strongest signal first:
  1. email address, when present - an address is a person
  2. normalised name + normalised company, for named contacts without an email
Role placeholders ("Estimating Department") are never counted as people; they
are counted separately as unnamed rows.

Usage
-----
    PYTHONPATH=. .venv/bin/python jobs/contacts_by_rac.py <ConstructConnect_Leads_V3.xlsx>
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import openpyxl

OUT = Path("data/contacts_rac.json")

RAC = {
    "Region 1 - Northeast": ["CT", "DE", "ME", "MD", "MA", "NH", "NJ", "NY", "PA", "RI", "VT", "WV"],
    "Region 2 - Southeast": ["AL", "FL", "GA", "MS", "NC", "SC", "TN", "VA"],
    "Region 3 - Midwest":   ["IA", "IL", "IN", "KY", "MI", "MN", "NE", "ND", "OH", "SD", "WI"],
    "Region 4 - Central":   ["AR", "AZ", "KS", "LA", "MO", "NM", "OK", "TX"],
    "Region 5 - West":      ["AK", "CA", "CO", "HI", "ID", "MT", "NV", "OR", "UT", "WA", "WY"],
}
STATE2RAC = {s: r for r, ss in RAC.items() for s in ss}
RACS = list(RAC)

# Kept identical to jobs/rollup_from_workbook.py. If one changes, change both.
ASSESSABLE_SCOPE = {"Concrete unit masonry (CMU)", "Single-wythe unit masonry",
                    "Fences, gates and site walls"}
EXEMPT_SCOPE = {"Clay unit masonry", "Retaining walls", "Unit paving / hardscape"}
NON_DISCRIMINATING = {"Unit masonry (generic)"}

_nk = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _tokens(v):
    return {t.strip() for t in str(v or "").split(";") if t.strip()}


def _blank():
    return {"rows": 0, "named_rows": 0, "unnamed_rows": 0, "personal_email": 0,
            "generic_email": 0, "with_phone": 0, "specifier": 0, "owner": 0,
            "procurement": 0, "buyer": 0, "other": 0,
            "a_rows": 0, "a_named_rows": 0, "a_personal_email": 0, "a_with_phone": 0}


ROLE_KEY = {
    "Specifier (influences product spec)": "specifier",
    "Owner / decision maker": "owner",
    "Procurement / bid contact": "procurement",
    "Buyer / trade contractor": "buyer",
}


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("pass the ConstructConnect workbook path as the first argument")
    src = Path(sys.argv[1])
    if not src.exists():
        sys.exit(f"no such workbook: {src}")
    print(f"reading {src}")
    wb = openpyxl.load_workbook(src, read_only=True, data_only=True)

    # ---- pass 1: project id -> scope classification -------------------------
    ws = wb["Projects"]
    it = ws.iter_rows(values_only=True)
    hdr = list(next(it))
    px = {h: i for i, h in enumerate(hdr)}
    proj = {}
    for r in it:
        pid = r[px["Project ID"]]
        if pid is None:
            continue
        scope = _tokens(r[px["SRW Product Scope"]]) - NON_DISCRIMINATING
        proj[str(pid)] = {
            "state": str(r[px["State"]] or "").strip().upper(),
            "assessable": bool(scope & ASSESSABLE_SCOPE),
            "exempt_only": bool(scope & EXEMPT_SCOPE) and not (scope & ASSESSABLE_SCOPE),
        }
    print(f"projects indexed: {len(proj):,}")

    # ---- pass 2: contacts ---------------------------------------------------
    ws = wb["Contacts"]
    it = ws.iter_rows(values_only=True)
    hdr = list(next(it))
    cx = {h: i for i, h in enumerate(hdr)}

    by_rac = defaultdict(_blank)
    people = defaultdict(set)          # rac -> dedupe keys (all named)
    people_assessable = defaultdict(set)
    companies = defaultdict(lambda: defaultdict(
        lambda: {"projects": set(), "rows": 0, "named": 0, "state": "", "a_projects": set()}))
    rows = unmatched = unmapped = 0

    for r in it:
        rows += 1
        pid = str(r[cx["Project ID"]] or "")
        p = proj.get(pid)
        if p is None:
            unmatched += 1
            continue
        # prefer the project's own state; fall back to the contact row's copy
        state = p["state"] or str(r[cx["Project State"]] or "").strip().upper()
        rac = STATE2RAC.get(state)
        if not rac:
            unmapped += 1
            continue

        d = by_rac[rac]
        d["rows"] += 1
        named = str(r[cx["Named Person"]] or "").strip().lower().startswith("yes")
        d["named_rows" if named else "unnamed_rows"] += 1

        etype = str(r[cx["Email Type"]] or "")
        if etype == "Personal":
            d["personal_email"] += 1
        elif etype == "Generic / shared":
            d["generic_email"] += 1
        phone = bool(r[cx["Phone"]] or r[cx["Mobile"]])
        if phone:
            d["with_phone"] += 1
        d[ROLE_KEY.get(str(r[cx["Role Type"]] or ""), "other")] += 1

        email = str(r[cx["Email"]] or "").strip().lower()
        name = str(r[cx["Contact Name"]] or "")
        comp = str(r[cx["Company"]] or "")
        key = None
        if named:
            # an address identifies a person; otherwise fall back to name+company
            key = email if "@" in email else (f"{_nk(name)}|{_nk(comp)}" if _nk(name) else None)
        if key:
            people[rac].add(key)

        if p["assessable"]:
            d["a_rows"] += 1
            if named:
                d["a_named_rows"] += 1
            if etype == "Personal":
                d["a_personal_email"] += 1
            if phone:
                d["a_with_phone"] += 1
            if key:
                people_assessable[rac].add(key)

        if comp:
            c = companies[rac][comp]
            c["projects"].add(pid)
            c["rows"] += 1
            if named:
                c["named"] += 1
            if p["assessable"]:
                c["a_projects"].add(pid)
            if not c["state"]:
                c["state"] = str(r[cx["Company State"]] or "").strip().upper()

    out_racs = []
    for rac in RACS:
        d = dict(by_rac[rac])
        d["rac"] = rac
        d["distinct_people"] = len(people[rac])
        d["a_distinct_people"] = len(people_assessable[rac])
        out_racs.append(d)

    # A person working across two regions is distinct in each and would be
    # counted twice by summing the column. The national figure is the union,
    # never the sum, and the two are reported separately so the gap is visible.
    national = set().union(*people.values()) if people else set()
    national_a = set().union(*people_assessable.values()) if people_assessable else set()

    top = {}
    for rac in RACS:
        ranked = sorted(companies[rac].items(),
                        key=lambda kv: (-len(kv[1]["a_projects"]), -len(kv[1]["projects"])))
        top[rac] = [{"company": n, "projects": len(v["projects"]),
                     "assessable_projects": len(v["a_projects"]),
                     "contact_rows": v["rows"], "named": v["named"], "state": v["state"]}
                    for n, v in ranked[:25]]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "source_workbook": src.name,
        "racs": out_racs,
        "top_companies": top,
        "total_contact_rows": rows,
        "national_distinct_people": len(national),
        "national_distinct_people_assessable": len(national_a),
        "cross_region_double_count": sum(len(people[r]) for r in RACS) - len(national),
        "unmatched_to_project": unmatched,
        "unmapped_state": unmapped,
        "scope_definitions": {"assessable": sorted(ASSESSABLE_SCOPE),
                              "exempt": sorted(EXEMPT_SCOPE)},
    }, indent=1))

    tp = sum(r["distinct_people"] for r in out_racs)
    ta = sum(r["a_distinct_people"] for r in out_racs)
    print(f"contact rows {rows:,}   unmatched {unmatched:,}   unmapped state {unmapped:,}")
    print(f"\n{'RAC':26s} {'rows':>7s} {'people':>7s} {'pers.email':>10s} "
          f"{'assess.rows':>11s} {'assess.people':>13s}")
    for r in out_racs:
        print(f"{r['rac']:26s} {r['rows']:7,d} {r['distinct_people']:7,d} "
              f"{r['personal_email']:10,d} {r['a_rows']:11,d} {r['a_distinct_people']:13,d}")
    print(f"{'TOTAL':26s} {sum(r['rows'] for r in out_racs):7,d} {tp:7,d} "
          f"{sum(r['personal_email'] for r in out_racs):10,d} "
          f"{sum(r['a_rows'] for r in out_racs):11,d} {ta:13,d}")
    print(f"{'NATIONAL (union)':26s} {'':7s} {len(national):7,d} {'':10s} {'':11s} "
          f"{len(national_a):13,d}")
    print(f"\ndistinct people {len(national):,} from "
          f"{sum(r['rows'] for r in out_racs):,} rows "
          f"({len(national)/max(sum(r['rows'] for r in out_racs),1):.0%} - the rest are repeats)")
    print(f"summing the per-RAC column would give {tp:,}, double-counting "
          f"{tp-len(national):,} people who work across regions")
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
