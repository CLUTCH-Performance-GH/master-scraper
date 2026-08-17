"""RAC (Regional Advisory Council) rollup of the ConstructConnect project data.

Produces the demand-side allocation driver for the CMC TAM model: how much
masonry-relevant construction value sits in each of the five checkoff regions,
based on projects actually observed rather than an assumed population split.
"""
from __future__ import annotations

import json
import warnings
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore")

CC = Path("/Users/jacklumpe/Desktop/Construct Connect Scraper/cc-pull/output/"
          "ConstructConnect_Leads_V3.xlsx")
OUT = Path("data/rac_rollup.json")

# Region 5 is quoted verbatim from the Commerce Order. Region 1 is the twelve
# states the client's own "Region 1 Master List" covers. Regions 2, 3 and 4 are
# read off the CMC regional map. Kept in one place, and mirrored onto an
# editable tab in the workbook, so a correction is a one-line change.
RAC = {
    "Region 1 - Northeast": ["CT", "DE", "ME", "MD", "MA", "NH", "NJ", "NY", "PA", "RI", "VT", "WV"],
    "Region 2 - Southeast": ["AL", "FL", "GA", "MS", "NC", "SC", "TN", "VA"],
    "Region 3 - Midwest":   ["IA", "IL", "IN", "KY", "MI", "MN", "NE", "ND", "OH", "SD", "WI"],
    "Region 4 - Central":   ["AR", "AZ", "KS", "LA", "MO", "NM", "OK", "TX"],
    "Region 5 - West":      ["AK", "CA", "CO", "HI", "ID", "MT", "NV", "OR", "UT", "WA", "WY"],
}
STATE2RAC = {s: r for r, ss in RAC.items() for s in ss}

# CSI subsections that indicate a project consumes concrete masonry UNITS.
# Deliberately narrow: the checkoff assesses dry-cast units 3in+ for masonry
# construction and explicitly exempts pavers, SRW units and clay brick, so
# counting hardscape scope here would overstate the assessable base.
CMU_CODES = {"0422", "0421", "0423", "0426", "0427", "0420"}
HARDSCAPE_EXEMPT = {"3214", "3232"}


def main():
    p = pd.read_excel(CC, "Projects").fillna("")
    print(f"projects loaded: {len(p):,}")

    p["RAC"] = p["State"].astype(str).str.upper().map(STATE2RAC).fillna("Unmapped")
    p["Project Value"] = pd.to_numeric(p["Project Value"], errors="coerce").fillna(0)

    # CSI Code Count is a breadth measure; Product Fit already encodes whether a
    # masonry subsection code was present. Use it rather than re-deriving.
    fit = p["Product Fit"].astype(str)
    p["has_cmu_scope"] = fit.str.contains("SRW|Both", case=False, na=False)

    rows = []
    for rac in list(RAC) + ["Unmapped"]:
        sub = p[p["RAC"] == rac]
        cmu = sub[sub["has_cmu_scope"]]
        rows.append({
            "rac": rac,
            "states": len(RAC.get(rac, [])),
            "projects": len(sub),
            "project_value": float(sub["Project Value"].sum()),
            "cmu_scope_projects": len(cmu),
            "cmu_scope_value": float(cmu["Project Value"].sum()),
            "median_value": float(sub["Project Value"].replace(0, pd.NA).median() or 0),
        })

    tot_v = sum(r["project_value"] for r in rows if r["rac"] != "Unmapped")
    tot_c = sum(r["cmu_scope_value"] for r in rows if r["rac"] != "Unmapped")
    for r in rows:
        r["share_of_value"] = (r["project_value"] / tot_v) if tot_v and r["rac"] != "Unmapped" else 0.0
        r["share_of_cmu_value"] = (r["cmu_scope_value"] / tot_c) if tot_c and r["rac"] != "Unmapped" else 0.0

    # state detail, for the drill-down tab
    st = []
    for s, g in p[p["RAC"] != "Unmapped"].groupby(p["State"].astype(str).str.upper()):
        if not s or s not in STATE2RAC:
            continue
        cmu = g[g["has_cmu_scope"]]
        st.append({
            "state": s, "rac": STATE2RAC[s], "projects": len(g),
            "project_value": float(g["Project Value"].sum()),
            "cmu_scope_projects": len(cmu),
            "cmu_scope_value": float(cmu["Project Value"].sum()),
        })
    st.sort(key=lambda r: (r["rac"], -r["project_value"]))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"racs": rows, "states": st,
                               "total_projects": len(p),
                               "mapped_projects": int((p["RAC"] != "Unmapped").sum())}, indent=1))

    print(f"\n{'RAC':26s} {'projects':>9s} {'value':>16s} {'share':>7s} {'CMU-scope $':>16s}")
    for r in rows:
        print(f"{r['rac']:26s} {r['projects']:9,d} ${r['project_value']/1e9:14.1f}B "
              f"{r['share_of_value']*100:6.1f}% ${r['cmu_scope_value']/1e9:14.1f}B")
    print(f"\n-> {OUT}")


if __name__ == "__main__":
    main()
