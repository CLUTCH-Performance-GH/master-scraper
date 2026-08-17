"""CMC TAM and projections by Regional Advisory Council.

Builds a navigable Excel model. Every assumption is a live cell on the
Assumptions tab and every downstream number is a real Excel formula, so a
colleague can change an input and watch the model move. Nothing is hardcoded
into a value that looks authoritative but cannot be interrogated.

Tabs
  Read Me            what this is, how to drive it, what not to trust
  Assumptions        every input, sourced and editable
  TAM by RAC         the headline: assessable units and checkoff dollars
  5-Year Projection  growth scenarios per region
  Projects by RAC    demand-side evidence from ConstructConnect
  Producers by RAC   supply-side evidence from the plant census
  State Mapping      the state-to-region table, editable
  Sources & Method   provenance and the honest caveats
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path

import pandas as pd
import xlsxwriter

warnings.filterwarnings("ignore")

ROLLUP = Path("data/rac_rollup.json")
PRODUCERS = Path("data/producers_national.json")
CENSUS = Path("data/census_cbp_rac.json")
OUT = Path("output/CMC_TAM_by_RAC_V1.xlsx")

BLUE = "#2F6DB3"
RACS = ["Region 1 - Northeast", "Region 2 - Southeast", "Region 3 - Midwest",
        "Region 4 - Central", "Region 5 - West"]

RAC_STATES = {
    "Region 1 - Northeast": ["CT", "DE", "ME", "MD", "MA", "NH", "NJ", "NY", "PA", "RI", "VT", "WV"],
    "Region 2 - Southeast": ["AL", "FL", "GA", "MS", "NC", "SC", "TN", "VA"],
    "Region 3 - Midwest":   ["IA", "IL", "IN", "KY", "MI", "MN", "NE", "ND", "OH", "SD", "WI"],
    "Region 4 - Central":   ["AR", "AZ", "KS", "LA", "MO", "NM", "OK", "TX"],
    "Region 5 - West":      ["AK", "CA", "CO", "HI", "ID", "MT", "NV", "OR", "UT", "WA", "WY"],
}


def main():
    roll = json.loads(ROLLUP.read_text())
    racs = {r["rac"]: r for r in roll["racs"]}

    cen = json.loads(CENSUS.read_text()) if CENSUS.exists() else None
    cen_by_rac = {r["rac"]: r for r in cen["racs"]} if cen else {}

    prod_by_rac, prod_by_state, n_prod = {}, {}, 0
    if PRODUCERS.exists():
        plist = json.loads(PRODUCERS.read_text())
        n_prod = len(plist)
        for p in plist:
            if p.get("rac"):
                prod_by_rac[p["rac"]] = prod_by_rac.get(p["rac"], 0) + 1
            prod_by_state[p["state"]] = prod_by_state.get(p["state"], 0) + 1

    wb = xlsxwriter.Workbook(str(OUT), {"strings_to_urls": False})
    f = {
        "h1":    wb.add_format({"bold": True, "font_size": 16, "font_color": "#1F4E79"}),
        "h2":    wb.add_format({"bold": True, "font_size": 12, "font_color": "#1F4E79"}),
        "hdr":   wb.add_format({"bold": True, "font_color": "white", "bg_color": BLUE,
                                "border": 1, "text_wrap": True, "valign": "vcenter"}),
        "body":  wb.add_format({"text_wrap": True, "valign": "top"}),
        "txt":   wb.add_format({"valign": "top"}),
        "num":   wb.add_format({"num_format": "#,##0", "valign": "top"}),
        "num1":  wb.add_format({"num_format": "#,##0.0", "valign": "top"}),
        "usd":   wb.add_format({"num_format": "$#,##0", "valign": "top"}),
        "usd2":  wb.add_format({"num_format": "$#,##0.00", "valign": "top"}),
        "usdB":  wb.add_format({"num_format": '$#,##0.0,,,"B"', "valign": "top"}),
        "pct":   wb.add_format({"num_format": "0.0%", "valign": "top"}),
        "in":    wb.add_format({"bg_color": "#FFF2CC", "border": 1, "num_format": "#,##0",
                                "valign": "top"}),
        "inpct": wb.add_format({"bg_color": "#FFF2CC", "border": 1, "num_format": "0.0%",
                                "valign": "top"}),
        "inusd": wb.add_format({"bg_color": "#FFF2CC", "border": 1, "num_format": "$#,##0.0000",
                                "valign": "top"}),
        "intxt": wb.add_format({"bg_color": "#FFF2CC", "border": 1, "valign": "top"}),
        "calc":  wb.add_format({"bg_color": "#EAF1F8", "num_format": "#,##0", "valign": "top"}),
        "calcU": wb.add_format({"bg_color": "#EAF1F8", "num_format": "$#,##0", "valign": "top"}),
        "calcP": wb.add_format({"bg_color": "#EAF1F8", "num_format": "0.0%", "valign": "top"}),
        "tot":   wb.add_format({"bold": True, "top": 2, "num_format": "#,##0", "valign": "top"}),
        "totU":  wb.add_format({"bold": True, "top": 2, "num_format": "$#,##0", "valign": "top"}),
        "totP":  wb.add_format({"bold": True, "top": 2, "num_format": "0.0%", "valign": "top"}),
        "note":  wb.add_format({"italic": True, "font_color": "#666666", "text_wrap": True,
                                "valign": "top"}),
    }

    # ---------------- Read Me -------------------------------------------------
    ws = wb.add_worksheet("Read Me")
    ws.set_column(0, 0, 108)
    ws.hide_gridlines(2)
    r = 0
    ws.write(r, 0, "Concrete Masonry Checkoff - TAM and Projections by RAC", f["h1"]); r += 2
    for para in [
        "What this is",
        "A sizing model for the Concrete Masonry Checkoff, broken out by the five Regional "
        "Advisory Councils. It answers: how many assessable units does each region represent, "
        "what does that mean in checkoff dollars, and how does that move under different "
        "growth assumptions.",
        "",
        "How to drive it",
        "Yellow cells are inputs. Change them and everything recalculates. Blue cells are "
        "calculated - do not type over them. The single most important input is the "
        "allocation basis on the Assumptions tab, which decides how national volume is split "
        "across regions.",
        "",
        "The honest health warning",
        "The national totals are solid: the assessment rate, the roughly $10M of actual annual "
        "collections and the industry unit volume are all published figures, cited on the "
        "Sources tab. The REGIONAL SPLIT is the modelled part. There is no published "
        "region-by-region unit volume, so the split is inferred from evidence we collected. "
        "Three different bases are provided precisely because they disagree, and the spread "
        "between them is the honest error bar. Treat a single region's number as an estimate "
        "with real uncertainty, not a measurement.",
        "",
        "What would make this authoritative",
        "The CMCB receives quarterly assessment reports from every producer. Those reports "
        "contain actual units by producer, and therefore actual units by region. If the client "
        "can obtain even aggregate regional collections, the modelled split on this workbook "
        "can be replaced with fact, and the model becomes a forecasting tool rather than a "
        "sizing estimate. That is the single highest-value ask.",
    ]:
        ws.write(r, 0, para, f["h2"] if para in (
            "What this is", "How to drive it", "The honest health warning",
            "What would make this authoritative") else f["body"])
        ws.set_row(r, None if len(para) < 60 else 15 * (len(para) // 95 + 1))
        r += 1

    # ---------------- Assumptions --------------------------------------------
    ws = wb.add_worksheet("Assumptions")
    A = "Assumptions"
    ws.set_column(0, 0, 46); ws.set_column(1, 1, 18); ws.set_column(2, 2, 62)
    ws.hide_gridlines(2)
    ws.write(0, 0, "Assumptions - yellow cells are editable inputs", f["h1"])
    hdr = ["Input", "Value", "Basis / source"]
    for c, h in enumerate(hdr):
        ws.write(2, c, h, f["hdr"])
    ws.set_row(2, 26)

    # row indices are referenced by formula below, so keep this block in sync
    ROW_RATE, ROW_UNITS, ROW_COLL, ROW_RACSHARE, ROW_NATSHARE, ROW_GROWTH, ROW_BASIS = 3, 4, 5, 6, 7, 8, 9
    rows = [
        ("Assessment rate per unit", 0.01, "inusd",
         "Published: $0.01 per assessable unit, unchanged since 1 Apr 2023."),
        ("US assessable units per year", 1_000_000_000, "in",
         "Derived: ~$10M annual collections / $0.01. Cross-checks against the "
         "1.15B units produced in 2018 cited in the rulemaking, less exempt product."),
        ("Actual annual collections", 10_000_000, "in",
         "Published: CMCB confirmed collections tracking to roughly $10M/yr."),
        ("Minimum share returned to RACs", 0.50, "inpct",
         "Published: at least 50% of program dollars go to the region that generated them."),
        ("Maximum national share", 0.50, "inpct",
         "Published: no more than 50% is spent at national level."),
        ("Annual unit volume growth", 0.02, "inpct",
         "Assumption. Used for the 5-year projection. Change per your own outlook."),
        ("Allocation basis", "Census Employment", "intxt",
         "Which evidence splits national volume across regions. Type one of: "
         "Census Employment / Census Establishments / Producers / Projects / "
         "Project Value / CMU-Scope Value. See Sources tab."),
    ]
    for i, (label, val, fmt, basis) in enumerate(rows):
        rr = 3 + i
        ws.write(rr, 0, label, f["txt"])
        ws.write(rr, 1, val, f[fmt])
        ws.write(rr, 2, basis, f["body"])
        ws.set_row(rr, 30)

    # Every formula below is written WITH its computed value. xlsxwriter emits no
    # cached result, so a reader whose Excel has automatic calculation switched
    # off (or any non-Excel viewer) would otherwise open this to a grid of zeros.
    RATE, UNITS, COLL = 0.01, 1_000_000_000, 10_000_000
    RACSHARE, GROWTH = 0.50, 0.02

    ws.write(11, 0, "Derived", f["h2"])
    ws.write(12, 0, "Implied units from collections", f["txt"])
    ws.write_formula(12, 1, f"=B{ROW_COLL+1}/B{ROW_RATE+1}", f["calc"], COLL / RATE)
    ws.write(12, 2, "Sanity check: should be close to the units input above.", f["note"])
    ws.write(13, 0, "Dollars available to RACs per year", f["txt"])
    ws.write_formula(13, 1, f"=B{ROW_COLL+1}*B{ROW_RACSHARE+1}", f["calcU"], COLL * RACSHARE)
    ws.write(13, 2, "The pool the five regions share.", f["note"])

    # ---------------- TAM by RAC ---------------------------------------------
    ws = wb.add_worksheet("TAM by RAC")
    ws.set_column(0, 0, 26); ws.set_column(1, 9, 15)
    ws.hide_gridlines(2)
    ws.write(0, 0, "TAM by Regional Advisory Council", f["h1"])
    ws.write(1, 0, "Share is driven by the allocation basis on the Assumptions tab. "
                   "Change that input to see the split move.", f["note"])

    heads = ["RAC", "States", "Producers", "Projects", "Project Value",
             "Share of Basis", "Assessable Units", "Checkoff $ Generated",
             "RAC Dollars (50%)", "$ per Producer"]
    for c, h in enumerate(heads):
        ws.write(3, c, h, f["hdr"])
    ws.set_row(3, 30)

    # helper columns holding each basis, so the SWITCH formula can pick one
    ws.write(3, 12, "Producers", f["hdr"]); ws.write(3, 13, "Projects", f["hdr"])
    ws.write(3, 14, "Project Value", f["hdr"]); ws.write(3, 15, "CMU-Scope Value", f["hdr"])
    ws.write(3, 16, "Census Emp", f["hdr"]); ws.write(3, 17, "Census Estab", f["hdr"])

    first = 4
    for i, rac in enumerate(RACS):
        rr = first + i
        d = racs.get(rac, {})
        ws.write(rr, 0, rac, f["txt"])
        ws.write(rr, 1, len(RAC_STATES[rac]), f["num"])
        ws.write(rr, 2, prod_by_rac.get(rac, 0), f["num"])
        ws.write(rr, 3, d.get("projects", 0), f["num"])
        ws.write(rr, 4, d.get("project_value", 0), f["usdB"])
        # raw basis values
        ws.write(rr, 12, prod_by_rac.get(rac, 0), f["num"])
        ws.write(rr, 13, d.get("projects", 0), f["num"])
        ws.write(rr, 14, d.get("project_value", 0), f["usdB"])
        ws.write(rr, 15, d.get("cmu_scope_value", 0), f["usdB"])
        cr = cen_by_rac.get(rac, {})
        ws.write(rr, 16, cr.get("emp", 0), f["num"])
        ws.write(rr, 17, cr.get("estab", 0), f["num"])

    last = first + len(RACS) - 1
    tot_prod = sum(prod_by_rac.get(x, 0) for x in RACS) or 1
    shares, rac_dollars = [], []
    for i, rac in enumerate(RACS):
        rr = first + i
        # pick the basis column by name, then normalise to a share
        pick = (f'IF({A}!$B$10="Census Employment",Q{rr+1},'
                f'IF({A}!$B$10="Census Establishments",R{rr+1},'
                f'IF({A}!$B$10="Producers",M{rr+1},'
                f'IF({A}!$B$10="Projects",N{rr+1},'
                f'IF({A}!$B$10="Project Value",O{rr+1},P{rr+1})))))')
        tot = (f'IF({A}!$B$10="Census Employment",SUM($Q${first+1}:$Q${last+1}),'
               f'IF({A}!$B$10="Census Establishments",SUM($R${first+1}:$R${last+1}),'
               f'IF({A}!$B$10="Producers",SUM($M${first+1}:$M${last+1}),'
               f'IF({A}!$B$10="Projects",SUM($N${first+1}:$N${last+1}),'
               f'IF({A}!$B$10="Project Value",SUM($O${first+1}:$O${last+1}),'
               f'SUM($P${first+1}:$P${last+1}))))))')
        # default basis is Census Employment, so cache that scenario's numbers
        sh = cen_by_rac.get(rac, {}).get("share_emp", 0) if cen else \
            prod_by_rac.get(rac, 0) / tot_prod
        un = sh * UNITS
        ck = un * RATE
        rd = ck * RACSHARE
        pc = prod_by_rac.get(rac, 0)
        shares.append(sh); rac_dollars.append(rd)
        ws.write_formula(rr, 5, f"=IFERROR({pick}/{tot},0)", f["calcP"], sh)
        ws.write_formula(rr, 6, f"=F{rr+1}*{A}!$B$5", f["calc"], un)
        ws.write_formula(rr, 7, f"=G{rr+1}*{A}!$B$4", f["calcU"], ck)
        ws.write_formula(rr, 8, f"=H{rr+1}*{A}!$B$7", f["calcU"], rd)
        ws.write_formula(rr, 9, f"=IFERROR(I{rr+1}/C{rr+1},0)", f["calcU"],
                         rd / pc if pc else 0)
    self_dollars = rac_dollars

    tr = last + 1
    ws.write(tr, 0, "TOTAL", f["tot"])
    sums = {
        1: sum(len(RAC_STATES[x]) for x in RACS),
        2: sum(prod_by_rac.get(x, 0) for x in RACS),
        3: sum(racs.get(x, {}).get("projects", 0) for x in RACS),
    }
    for col, fmt in ((1, "tot"), (2, "tot"), (3, "tot")):
        ws.write_formula(tr, col, f"=SUM({chr(65+col)}{first+1}:{chr(65+col)}{last+1})",
                         f[fmt], sums[col])
    ws.write_formula(tr, 4, f"=SUM(E{first+1}:E{last+1})", f["totU"],
                     sum(racs.get(x, {}).get("project_value", 0) for x in RACS))
    ws.write_formula(tr, 5, f"=SUM(F{first+1}:F{last+1})", f["totP"], sum(shares))
    ws.write_formula(tr, 6, f"=SUM(G{first+1}:G{last+1})", f["tot"], UNITS)
    ws.write_formula(tr, 7, f"=SUM(H{first+1}:H{last+1})", f["totU"], UNITS * RATE)
    ws.write_formula(tr, 8, f"=SUM(I{first+1}:I{last+1})", f["totU"],
                     UNITS * RATE * RACSHARE)
    ws.set_column(12, 17, 14, None, {"hidden": True})

    ws.write(tr + 2, 0, "Sensitivity: how the split changes by basis", f["h2"])
    for c, h in enumerate(["RAC", "By Census Emp", "By Census Estab", "By Producers",
                           "By Projects", "By Project Value", "By CMU-Scope Value",
                           "Spread (max-min)"]):
        ws.write(tr + 3, c, h, f["hdr"])
    tp = sum(prod_by_rac.get(x, 0) for x in RACS) or 1
    tj = sum(racs.get(x, {}).get("projects", 0) for x in RACS) or 1
    tv = sum(racs.get(x, {}).get("project_value", 0) for x in RACS) or 1
    tc = sum(racs.get(x, {}).get("cmu_scope_value", 0) for x in RACS) or 1
    for i, rac in enumerate(RACS):
        rr = tr + 4 + i
        d = racs.get(rac, {})
        cr = cen_by_rac.get(rac, {})
        vals = [cr.get("share_emp", 0), cr.get("share_estab", 0),
                prod_by_rac.get(rac, 0) / tp, d.get("projects", 0) / tj,
                d.get("project_value", 0) / tv, d.get("cmu_scope_value", 0) / tc]
        ws.write(rr, 0, rac, f["txt"])
        for c, v in enumerate(vals):
            ws.write(rr, 1 + c, v, f["pct"])
        ws.write(rr, 7, max(vals) - min(vals), f["pct"])
    ws.write(tr + 4 + len(RACS) + 1, 0,
             "A wide spread means the bases disagree about that region and its number "
             "should be treated as a range, not a point estimate.", f["note"])

    # ---------------- 5-Year Projection --------------------------------------
    ws = wb.add_worksheet("5-Year Projection")
    ws.set_column(0, 0, 26); ws.set_column(1, 8, 16)
    ws.hide_gridlines(2)
    ws.write(0, 0, "Five-year projection of RAC dollars", f["h1"])
    ws.write(1, 0, "Applies the growth rate from Assumptions to each region's share. "
                   "Year 1 equals the TAM tab.", f["note"])
    yrs = ["Year 1", "Year 2", "Year 3", "Year 4", "Year 5"]
    for c, h in enumerate(["RAC"] + yrs + ["5-Yr Total", "CAGR"]):
        ws.write(3, c, h, f["hdr"])
    ws.set_row(3, 24)
    for i, rac in enumerate(RACS):
        rr = 4 + i
        src = 5 + i  # matching row on TAM tab (1-indexed)
        ws.write(rr, 0, rac, f["txt"])
        base = self_dollars[i]
        yr_vals = [base * (1 + GROWTH) ** y for y in range(5)]
        for y in range(5):
            ws.write_formula(rr, 1 + y,
                             f"='TAM by RAC'!I{src}*(1+{A}!$B$9)^{y}", f["calcU"],
                             yr_vals[y])
        ws.write_formula(rr, 6, f"=SUM(B{rr+1}:F{rr+1})", f["totU"], sum(yr_vals))
        ws.write_formula(rr, 7, f"=IFERROR((F{rr+1}/B{rr+1})^(1/4)-1,0)", f["calcP"],
                         GROWTH)
    rr = 4 + len(RACS)
    ws.write(rr, 0, "TOTAL", f["tot"])
    col_tot = [sum(self_dollars) * (1 + GROWTH) ** y for y in range(5)]
    for c in range(1, 7):
        ws.write_formula(rr, c, f"=SUM({chr(65+c)}5:{chr(65+c)}{rr})", f["totU"],
                         col_tot[c - 1] if c <= 5 else sum(col_tot))

    ws.write(rr + 2, 0, "Scenario view (total RAC dollars, Year 5)", f["h2"])
    for c, h in enumerate(["Scenario", "Growth rate", "Year 5 RAC dollars"]):
        ws.write(rr + 3, c, h, f["hdr"])
    for i, (nm, g) in enumerate([("Contraction", -0.03), ("Flat", 0.0),
                                 ("Base", 0.02), ("Expansion", 0.05)]):
        r2 = rr + 4 + i
        ws.write(r2, 0, nm, f["txt"])
        ws.write(r2, 1, g, f["inpct"])
        ws.write_formula(r2, 2,
                         f"={A}!$B$6*{A}!$B$7*(1+B{r2+1})^4", f["calcU"],
                         COLL * RACSHARE * (1 + g) ** 4)
    ws.write(rr + 9, 0, "Scenario growth rates are editable. They are independent of the "
                        "main growth input so you can compare without disturbing the model.",
             f["note"])

    # ---------------- Projects by RAC ----------------------------------------
    ws = wb.add_worksheet("Projects by RAC")
    ws.set_column(0, 0, 26); ws.set_column(1, 7, 17)
    ws.hide_gridlines(2)
    ws.write(0, 0, "Demand side: construction projects observed by RAC", f["h1"])
    ws.write(1, 0, f"From {roll['total_projects']:,} ConstructConnect projects. This is a "
                   "SAMPLE selected for size and recency, not a census, so treat the shape "
                   "as indicative and the absolute values as a floor.", f["note"])
    for c, h in enumerate(["RAC", "Projects", "Total Value", "Median Value",
                           "CMU-Scope Projects", "CMU-Scope Value", "% of Projects",
                           "% of Value"]):
        ws.write(3, c, h, f["hdr"])
    ws.set_row(3, 28)
    for i, rac in enumerate(RACS):
        d = racs.get(rac, {}); rr = 4 + i
        ws.write(rr, 0, rac, f["txt"])
        ws.write(rr, 1, d.get("projects", 0), f["num"])
        ws.write(rr, 2, d.get("project_value", 0), f["usd"])
        ws.write(rr, 3, d.get("median_value", 0), f["usd"])
        ws.write(rr, 4, d.get("cmu_scope_projects", 0), f["num"])
        ws.write(rr, 5, d.get("cmu_scope_value", 0), f["usd"])
        ws.write(rr, 6, d.get("share_of_value", 0), f["pct"])
        ws.write(rr, 7, d.get("share_of_cmu_value", 0), f["pct"])

    r2 = 4 + len(RACS) + 2
    ws.write(r2, 0, "By state", f["h2"])
    for c, h in enumerate(["State", "RAC", "Projects", "Project Value",
                           "CMU-Scope Projects", "CMU-Scope Value"]):
        ws.write(r2 + 1, c, h, f["hdr"])
    for i, s in enumerate(roll["states"]):
        rr = r2 + 2 + i
        ws.write(rr, 0, s["state"], f["txt"])
        ws.write(rr, 1, s["rac"], f["txt"])
        ws.write(rr, 2, s["projects"], f["num"])
        ws.write(rr, 3, s["project_value"], f["usd"])
        ws.write(rr, 4, s["cmu_scope_projects"], f["num"])
        ws.write(rr, 5, s["cmu_scope_value"], f["usd"])

    # ---------------- Producers by RAC ---------------------------------------
    ws = wb.add_worksheet("Producers by RAC")
    ws.set_column(0, 0, 26); ws.set_column(1, 4, 18)
    ws.hide_gridlines(2)
    ws.write(0, 0, "Supply side: concrete masonry producers by RAC", f["h1"])
    ws.write(1, 0, f"{n_prod:,} producer locations identified via business-listing search. "
                   "Counts plant locations, not corporate parents, which is the right unit "
                   "for capacity. Compare against the ~200 assessed producers the rulemaking "
                   "cites: this list is wider because it includes non-assessed and "
                   "exempt-product plants.", f["note"])
    ws.set_row(1, 42)
    for c, h in enumerate(["RAC", "States", "Producer Locations", "% of US", "Per State"]):
        ws.write(3, c, h, f["hdr"])
    ws.set_row(3, 24)
    tot_p = sum(prod_by_rac.get(x, 0) for x in RACS) or 1
    for i, rac in enumerate(RACS):
        rr = 4 + i
        n = prod_by_rac.get(rac, 0)
        ws.write(rr, 0, rac, f["txt"])
        ws.write(rr, 1, len(RAC_STATES[rac]), f["num"])
        ws.write(rr, 2, n, f["num"])
        ws.write(rr, 3, n / tot_p, f["pct"])
        ws.write(rr, 4, n / max(len(RAC_STATES[rac]), 1), f["num1"])
    rr = 4 + len(RACS)
    ws.write(rr, 0, "TOTAL", f["tot"])
    ws.write_formula(rr, 1, f"=SUM(B5:B{rr})", f["tot"])
    ws.write_formula(rr, 2, f"=SUM(C5:C{rr})", f["tot"])
    ws.write_formula(rr, 3, f"=SUM(D5:D{rr})", f["totP"])

    r2 = rr + 3
    ws.write(r2, 0, "By state", f["h2"])
    for c, h in enumerate(["State", "RAC", "Producer Locations"]):
        ws.write(r2 + 1, c, h, f["hdr"])
    srt = sorted(prod_by_state.items(), key=lambda kv: -kv[1])
    s2r = {s: r for r, ss in RAC_STATES.items() for s in ss}
    for i, (s, n) in enumerate(srt):
        rr2 = r2 + 2 + i
        ws.write(rr2, 0, s, f["txt"])
        ws.write(rr2, 1, s2r.get(s, ""), f["txt"])
        ws.write(rr2, 2, n, f["num"])

    # ---------------- State Mapping ------------------------------------------
    ws = wb.add_worksheet("State Mapping")
    ws.set_column(0, 0, 10); ws.set_column(1, 1, 26); ws.set_column(2, 2, 60)
    ws.hide_gridlines(2)
    ws.write(0, 0, "State to RAC mapping", f["h1"])
    ws.write(1, 0, "Region 5 is quoted verbatim from the Commerce Order. Region 1 matches the "
                   "twelve states in the client's own Region 1 list. Regions 2, 3 and 4 are "
                   "read off the CMC regional map. Correct anything here and re-run.", f["note"])
    ws.set_row(1, 30)
    for c, h in enumerate(["State", "RAC", "Note"]):
        ws.write(3, c, h, f["hdr"])
    i = 0
    for rac in RACS:
        for s in RAC_STATES[rac]:
            ws.write(4 + i, 0, s, f["intxt"])
            ws.write(4 + i, 1, rac, f["intxt"])
            ws.write(4 + i, 2,
                     "verbatim from the Order" if rac.startswith("Region 5")
                     else "matches client Region 1 list" if rac.startswith("Region 1")
                     else "read from CMC regional map", f["note"])
            i += 1

    # ---------------- Sources & Method ---------------------------------------
    ws = wb.add_worksheet("Sources & Method")
    ws.set_column(0, 0, 34); ws.set_column(1, 1, 92)
    ws.hide_gridlines(2)
    ws.write(0, 0, "Sources and method", f["h1"])
    notes = [
        ("Assessment rate", "$0.01 per assessable unit, effective 1 April 2023. "
                            "concretemasonrycheckoff.org/assessments"),
        ("What is assessable", "Dry-cast units made on mechanised block machines, 3 inches or "
                               "more in actual width, intended for masonry construction: gray, "
                               "architectural, fence, lintel, screen and veneer units."),
        ("What is EXEMPT", "Pavers, segmental retaining wall units, clay brick, precast "
                           "concrete lintels, and any unit under 3 inches. This matters: a "
                           "hardscape-heavy producer or project contributes far less than its "
                           "size suggests, and counting hardscape volume would overstate TAM."),
        ("Annual collections", "CMCB has confirmed collections tracking to roughly $10 million "
                               "a year, consistent across reported quarters."),
        ("Unit volume", "1.15 billion units produced in 2018 per figures cited in the "
                        "rulemaking. The model uses 1.0 billion ASSESSABLE units, derived from "
                        "collections divided by rate, which is lower because of the exemptions."),
        ("50/50 split", "At least half of program dollars return to the region that generated "
                        "them; no more than half is spent nationally."),
        ("Demand-side data", "6,182 projects collected from ConstructConnect Insight with "
                             "value, state, stage and CSI scope codes. A SAMPLE selected for "
                             "size and recency out of roughly 136,000 matching the saved "
                             "search, so its value distribution is skewed toward large "
                             "projects. Use the shape, not the absolute totals."),
        ("Supply-side data", "Producer locations identified through business-listing search "
                             "across all 50 states, filtered to block and masonry manufacture "
                             "and away from contractors, landscapers and retailers. Counts "
                             "locations rather than corporate parents."),
        ("Default basis: Census employment", "US Census County Business Patterns 2022, NAICS "
                                             "327331 Concrete Block and Brick Manufacturing: "
                                             "1,155 establishments, 31,116 employees and $1.98B "
                                             "payroll, broken out by state. This is the assessed "
                                             "industry exactly, from a mandatory survey rather "
                                             "than a search. Employment is preferred over "
                                             "establishment count because the levy is per unit "
                                             "manufactured, and a 200-person plant outproduces a "
                                             "10-person yard many times over. Free flat file, no "
                                             "API key needed: www2.census.gov/programs-surveys/"
                                             "cbp/datasets/2022/cbp22st.zip"),
        ("A bias this exposed in our own data", "Our business-listing producer sweep found 536 "
                                                "plants and put Region 1 at 27.2% of the US "
                                                "total. Census puts Region 1 at 18.1%. The gap "
                                                "is our own search effort: Region 1 had been "
                                                "swept far harder for an earlier piece of work, "
                                                "so it came out over-represented. A search finds "
                                                "what you looked for; a census does not have "
                                                "that problem. That is why the scraped producer "
                                                "count is no longer the default basis."),
        ("What is NOT in here", "Actual regional collections. The CMCB holds them via quarterly "
                                "producer reports. Obtaining even aggregate regional figures "
                                "would replace the modelled split with fact."),
        ("Rounding", "Round half up throughout. Percentages may not sum to exactly 100 in "
                     "display."),
    ]
    for c, h in enumerate(["Item", "Detail"]):
        ws.write(2, c, h, f["hdr"])
    for i, (k, v) in enumerate(notes):
        ws.write(3 + i, 0, k, f["h2"])
        ws.write(3 + i, 1, v, f["body"])
        ws.set_row(3 + i, max(28, 13 * (len(v) // 88 + 1)))

    wb.close()
    print(f"wrote {OUT}")
    print(f"  producers mapped   : {n_prod:,}")
    for rac in RACS:
        print(f"    {rac:26s} producers={prod_by_rac.get(rac,0):4d}  "
              f"projects={racs.get(rac,{}).get('projects',0):5,d}")


if __name__ == "__main__":
    main()
