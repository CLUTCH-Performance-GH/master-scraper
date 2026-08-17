"""Rebuild the RAC demand-side rollup from a ConstructConnect deliverable.

Replaces `jobs/rac_analysis.py` for the common case where all you have is the
shipped workbook. Three differences that matter:

1. It takes the workbook path as an argument. `rac_analysis.py` hardcodes an
   absolute path on one person's Desktop, so it cannot run anywhere else.
2. It reads with openpyxl in read-only streaming mode rather than loading the
   whole sheet into pandas. Same answer, a fraction of the memory.
3. It separates ASSESSABLE concrete masonry scope from EXEMPT hardscape scope.
   This is the substantive change - see below.

Why the scope change matters
----------------------------
`rac_analysis.py` flags CMU scope as `Product Fit contains "SRW" or "Both"`.
That bucket is labelled "retaining wall / hardscape / CMU", so it sweeps in
segmental retaining wall units, pavers and clay brick - all of which the
Commerce Order EXEMPTS from the assessment. Using it as an allocation basis
counts exempt volume as if it generated checkoff dollars, and it does so
unevenly across regions, because hardscape mix is regional.

Measured across the 6,182-project pull, the scope vocabulary splits cleanly:

    ASSESSABLE   Concrete unit masonry (CMU)         1,383
                 Fences, gates and site walls        1,145   (fence units are
                 Single-wythe unit masonry              66     named assessable)

    EXEMPT       Clay unit masonry                   1,422   (clay brick)
                 Retaining walls                       205   (SRW units)
                 Unit paving / hardscape               201   (pavers)

    UNUSABLE     Unit masonry (generic)              4,957   (the 04 20 rollup)

The generic rollup is discarded on the evidence already recorded in
docs/constructconnect.md section 4: it appears on ~100% of projects, so it
cannot discriminate between them.

Usage
-----
    PYTHONPATH=. .venv/bin/python jobs/rollup_from_workbook.py <workbook.xlsx>
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import openpyxl

OUT = Path("data/rac_rollup.json")

RAC = {
    "Region 1 - Northeast": ["CT", "DE", "ME", "MD", "MA", "NH", "NJ", "NY", "PA", "RI", "VT", "WV"],
    "Region 2 - Southeast": ["AL", "FL", "GA", "MS", "NC", "SC", "TN", "VA"],
    "Region 3 - Midwest":   ["IA", "IL", "IN", "KY", "MI", "MN", "NE", "ND", "OH", "SD", "WI"],
    "Region 4 - Central":   ["AR", "AZ", "KS", "LA", "MO", "NM", "OK", "TX"],
    "Region 5 - West":      ["AK", "CA", "CO", "HI", "ID", "MT", "NV", "OR", "UT", "WA", "WY"],
}
STATE2RAC = {s: r for r, ss in RAC.items() for s in ss}

# Scope tokens, exactly as the vendor writes them in the Product Scope columns.
ASSESSABLE_SCOPE = {
    "Concrete unit masonry (CMU)",
    "Single-wythe unit masonry",
    "Fences, gates and site walls",
}
EXEMPT_SCOPE = {
    "Clay unit masonry",
    "Retaining walls",
    "Unit paving / hardscape",
}
# Present on ~100% of projects; carries no information. Never counted.
NON_DISCRIMINATING = {"Unit masonry (generic)"}


def _tokens(value) -> set[str]:
    if not value:
        return set()
    return {t.strip() for t in str(value).split(";") if t.strip()}


def _find_workbook(argv: list[str]) -> Path:
    if len(argv) > 1:
        p = Path(argv[1])
        if not p.exists():
            sys.exit(f"no such workbook: {p}")
        return p
    for cand in sorted(Path(".").glob("**/ConstructConnect_Leads_V*.xlsx")):
        return cand
    sys.exit("pass the ConstructConnect workbook path as the first argument")


def main() -> None:
    src = _find_workbook(sys.argv)
    print(f"reading {src}")
    ws = openpyxl.load_workbook(src, read_only=True, data_only=True)["Projects"]

    it = ws.iter_rows(values_only=True)
    hdr = list(next(it))
    ix = {h: i for i, h in enumerate(hdr)}
    for col in ("State", "Project Value", "Product Fit", "SRW Product Scope"):
        if col not in ix:
            sys.exit(f"workbook is missing the {col!r} column; is this a V3 Projects sheet?")

    blank = {"projects": 0, "project_value": 0.0,
             "cmu_scope_projects": 0, "cmu_scope_value": 0.0,
             "assessable_projects": 0, "assessable_value": 0.0,
             "exempt_projects": 0, "exempt_value": 0.0}
    by_rac = defaultdict(lambda: dict(blank))
    by_state = defaultdict(lambda: dict(blank))
    values_for_median = defaultdict(list)

    total = unmapped = 0
    for row in it:
        total += 1
        state = str(row[ix["State"]] or "").strip().upper()
        rac = STATE2RAC.get(state)
        if not rac:
            unmapped += 1
            continue

        try:
            value = float(row[ix["Project Value"]] or 0)
        except (TypeError, ValueError):
            value = 0.0

        scope = _tokens(row[ix["SRW Product Scope"]]) - NON_DISCRIMINATING
        assessable = bool(scope & ASSESSABLE_SCOPE)
        exempt_only = bool(scope & EXEMPT_SCOPE) and not assessable
        # legacy flag, kept so the old and new bases can be compared side by side
        legacy = "SRW" in str(row[ix["Product Fit"]] or "") or \
                 "Both" in str(row[ix["Product Fit"]] or "")

        for bucket in (by_rac[rac], by_state[state]):
            bucket["projects"] += 1
            bucket["project_value"] += value
            if legacy:
                bucket["cmu_scope_projects"] += 1
                bucket["cmu_scope_value"] += value
            if assessable:
                bucket["assessable_projects"] += 1
                bucket["assessable_value"] += value
            if exempt_only:
                bucket["exempt_projects"] += 1
                bucket["exempt_value"] += value

        by_state[state]["rac"] = rac
        if value:
            values_for_median[rac].append(value)

    racs = []
    for rac in RAC:
        d = dict(by_rac[rac])
        d["rac"] = rac
        d["states"] = len(RAC[rac])
        vals = values_for_median.get(rac, [])
        d["median_value"] = float(statistics.median(vals)) if vals else 0.0
        racs.append(d)

    def _share(key: str):
        tot = sum(r[key] for r in racs)
        for r in racs:
            r[f"share_of_{key}"] = (r[key] / tot) if tot else 0.0

    _share("project_value")
    _share("cmu_scope_value")
    _share("assessable_value")

    # build_rac_tam.py reads these two names; keep them stable
    for r in racs:
        r["share_of_value"] = r["share_of_project_value"]
        r["share_of_cmu_value"] = r["share_of_cmu_scope_value"]

    states = []
    for s, d in by_state.items():
        d = dict(d)
        d["state"] = s
        states.append(d)
    states.sort(key=lambda r: (r["rac"], -r["project_value"]))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "source_workbook": src.name,
        "racs": racs,
        "states": states,
        "total_projects": total,
        "mapped_projects": total - unmapped,
        "unmapped_projects": unmapped,
        "scope_definitions": {
            "assessable": sorted(ASSESSABLE_SCOPE),
            "exempt": sorted(EXEMPT_SCOPE),
            "discarded_as_non_discriminating": sorted(NON_DISCRIMINATING),
        },
    }, indent=1))

    print(f"\nprojects {total:,}   mapped {total - unmapped:,}   unmapped {unmapped:,}")
    print(f"\n{'RAC':26s} {'proj':>6s} {'value':>12s} {'share':>7s} "
          f"{'assessable $':>13s} {'share':>7s}  {'legacy CMU $':>13s}")
    for r in racs:
        print(f"{r['rac']:26s} {r['projects']:6,d} ${r['project_value']/1e9:10.1f}B "
              f"{r['share_of_value']:6.1%} ${r['assessable_value']/1e9:11.1f}B "
              f"{r['share_of_assessable_value']:6.1%}  ${r['cmu_scope_value']/1e9:11.1f}B")
    ta = sum(r["assessable_value"] for r in racs)
    tl = sum(r["cmu_scope_value"] for r in racs)
    print(f"\nassessable-scope value ${ta/1e9:,.1f}B vs legacy SRW-or-Both "
          f"${tl/1e9:,.1f}B ({ta/tl if tl else 0:.0%} of it)")
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
