"""CMC TAM and projections by Regional Advisory Council.

Builds a navigable Excel model. Every assumption is a live cell on the
Assumptions tab and every downstream number is a real Excel formula, so a
colleague can change an input and watch the model move. Nothing is hardcoded
into a value that looks authoritative but cannot be interrogated.

Tabs
  Read Me            what this is, how to drive it, and links to every tab
  Assumptions        every input, sourced and editable
  TAM by RAC         the headline: assessable units and checkoff dollars
  5-Year Projection  growth scenarios per region
  Projects by RAC    demand-side evidence from ConstructConnect
  Producers by RAC   supply-side evidence from the plant census
  State Mapping      the state-to-region table, editable
  Data Status        which allocation bases are loaded and which are not
  Sources & Method   provenance and the honest caveats

Allocation bases are registered in BASES below. A basis whose input file is
absent is written as an empty column, excluded from the picker's dropdown and
listed as "not loaded" on Data Status, rather than silently contributing zeros
to a share calculation. That distinction is the whole point: a zero share and
an unmeasured share are not the same claim.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import xlsxwriter

ROLLUP = Path("data/rac_rollup.json")
CONTACTS = Path("data/contacts_rac.json")
PRODUCERS = Path("data/producers_national.json")
CENSUS = Path("data/census_cbp_rac.json")
SHIPMENTS = Path("data/census_shipments_rac.json")
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

TABS = ["Read Me", "Assumptions", "TAM by RAC", "5-Year Projection",
        "Projects by RAC", "Contacts by RAC", "Target Companies", "Producers by RAC",
        "State Mapping", "Data Status", "Sources & Method"]

# Assumptions tab layout. Row numbers are referenced by formulas throughout, so
# the block and these constants must move together.
A = "Assumptions"
R_RATE, R_UNITS, R_COLL, R_RACSHARE, R_NATSHARE, R_GROWTH, R_EXEMPT, R_BASIS = 3, 4, 5, 6, 7, 8, 9, 10


def _load(path: Path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as e:
        sys.exit(f"{path} is present but not valid JSON: {e}")


def main() -> None:
    roll = _load(ROLLUP)
    if roll is None:
        sys.exit(f"missing {ROLLUP}\n"
                 f"  build it with: PYTHONPATH=. .venv/bin/python "
                 f"jobs/rollup_from_workbook.py <ConstructConnect_Leads_V3.xlsx>")
    racs = {r["rac"]: r for r in roll["racs"]}

    con = _load(CONTACTS)
    con_by_rac = {r["rac"]: r for r in con["racs"]} if con else {}

    cen = _load(CENSUS)
    cen_by_rac = {r["rac"]: r for r in cen["racs"]} if cen else {}
    ship = _load(SHIPMENTS)
    ship_by_rac = {r["rac"]: r for r in ship["racs"]} if ship else {}

    prod_by_rac, prod_by_state, n_prod = {}, {}, 0
    plist = _load(PRODUCERS)
    if plist:
        n_prod = len(plist)
        for p in plist:
            if p.get("rac"):
                prod_by_rac[p["rac"]] = prod_by_rac.get(p["rac"], 0) + 1
            if p.get("state"):
                prod_by_state[p["state"]] = prod_by_state.get(p["state"], 0) + 1

    # ---- the allocation-basis registry ------------------------------------
    # (label, per-rac value fn, number format, loaded?, source note)
    BASES = [
        ("Assessable Scope Value",
         lambda r: racs.get(r, {}).get("assessable_value", 0), "usdB", True,
         "ConstructConnect project value carrying a SPECIFIC assessable masonry "
         "scope code (CMU, single-wythe, fence/site wall). Excludes clay brick, "
         "SRW and paving, which the Order exempts, and excludes the 04 20 generic "
         "rollup, which appears on ~100% of projects and cannot discriminate."),
        ("Project Value",
         lambda r: racs.get(r, {}).get("project_value", 0), "usdB", True,
         "Total ConstructConnect project value, all scopes. Broadest demand "
         "signal, but most of it is not masonry."),
        ("CMU-Scope Value (legacy)",
         lambda r: racs.get(r, {}).get("cmu_scope_value", 0), "usdB", True,
         "The previous definition: Product Fit of SRW or Both. Retained only for "
         "comparison. It counts exempt hardscape as if it were assessable."),
        ("Projects",
         lambda r: racs.get(r, {}).get("projects", 0), "num", True,
         "Count of observed projects. Treats a $200M hospital and a $50k shed as "
         "equal, so it measures activity, not volume."),
        ("Producers",
         lambda r: prod_by_rac.get(r, 0), "num", bool(plist),
         "Producer locations from a business-listing sweep. Known to over-weight "
         "Region 1 by roughly 8 points because that region was searched harder. "
         "A search measures how hard you looked."),
        ("Census Employment",
         lambda r: cen_by_rac.get(r, {}).get("emp", 0), "num", bool(cen),
         "CBP 2022, NAICS 327331 employment by state. A census, not a search, so "
         "no search-effort bias. Employment is a proxy for output."),
        ("Census Establishments",
         lambda r: cen_by_rac.get(r, {}).get("estab", 0), "num", bool(cen),
         "CBP 2022, NAICS 327331 establishment counts. Treats a 200-person plant "
         "and a 10-person yard as equal."),
        ("Census Shipments",
         lambda r: ship_by_rac.get(r, {}).get("shipments", 0), "usdB", bool(ship),
         "2022 Economic Census value of shipments, NAICS 327331, by state. The "
         "closest published measure to actual output. Multiply by the assessable "
         "share to strip exempt brick and hardscape."),
    ]
    loaded = [b for b in BASES if b[3]]
    if not loaded:
        sys.exit("no allocation basis has data; nothing to build")
    default_basis = loaded[0][0]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    wb = xlsxwriter.Workbook(str(OUT), {"strings_to_urls": False})
    f = {
        "h1":    wb.add_format({"bold": True, "font_size": 16, "font_color": "#1F4E79"}),
        "h2":    wb.add_format({"bold": True, "font_size": 12, "font_color": "#1F4E79"}),
        "hdr":   wb.add_format({"bold": True, "font_color": "white", "bg_color": BLUE,
                                "border": 1, "text_wrap": True, "valign": "vcenter"}),
        "body":  wb.add_format({"text_wrap": True, "valign": "top"}),
        "txt":   wb.add_format({"valign": "top"}),
        "link":  wb.add_format({"font_color": BLUE, "underline": 1, "valign": "top"}),
        "num":   wb.add_format({"num_format": "#,##0", "valign": "top"}),
        "num1":  wb.add_format({"num_format": "#,##0.0", "valign": "top"}),
        "usd":   wb.add_format({"num_format": "$#,##0", "valign": "top"}),
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
        "ok":    wb.add_format({"font_color": "#1E7B34", "bold": True, "valign": "top"}),
        "no":    wb.add_format({"font_color": "#B00020", "bold": True, "valign": "top"}),
    }

    def nav(ws, row=0):
        """Write the tab index. Internal links only, so no URL validation risk."""
        ws.write(row, 0, "Go to:", f["note"])
        for i, t in enumerate(TABS):
            ws.write_url(row, 1 + i, f"internal:'{t}'!A1", f["link"], t)

    # ---------------- Read Me -------------------------------------------------
    ws = wb.add_worksheet("Read Me")
    ws.set_column(0, 0, 104)
    ws.set_column(1, len(TABS), 19)
    ws.hide_gridlines(2)
    ws.write(0, 0, "Concrete Masonry Checkoff - TAM and Projections by RAC", f["h1"])
    nav(ws, 1)
    r = 3
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
        "across regions. It is a dropdown listing only the bases that actually have data "
        "behind them in this build; see the Data Status tab for what is loaded and what is not.",
        "",
        "The honest health warning",
        "The national totals are solid: the assessment rate, the roughly $10M of actual annual "
        "collections and the industry unit volume are all published figures, cited on the "
        "Sources tab. The REGIONAL SPLIT is the modelled part. There is no published "
        "region-by-region unit volume, so the split is inferred from evidence we collected. "
        "Several bases are provided precisely because they disagree, and the spread between "
        "them is the honest error bar. Treat a single region's number as an estimate with "
        "real uncertainty, not a measurement.",
        "",
        "What this build is missing",
        "The Census bases are NOT loaded in this build, so the split currently rests on "
        "ConstructConnect project scope, which is a demand-side sample rather than a "
        "production census. That is the weaker evidence and the Data Status tab says so "
        "plainly. Loading Census shipments is the single biggest upgrade available and needs "
        "nothing but network access to census.gov.",
        "",
        "What would make this authoritative",
        "The CMCB receives quarterly assessment reports from every producer. Those reports "
        "contain actual units by producer, and therefore actual units by region. If the client "
        "can obtain even aggregate regional collections, the modelled split on this workbook "
        "can be replaced with fact, and the model becomes a forecasting tool rather than a "
        "sizing estimate. That is the single highest-value ask.",
    ]:
        head = para in ("What this is", "How to drive it", "The honest health warning",
                        "What this build is missing", "What would make this authoritative")
        ws.write(r, 0, para, f["h2"] if head else f["body"])
        ws.set_row(r, None if len(para) < 60 else 15 * (len(para) // 95 + 1))
        r += 1

    # ---------------- Assumptions --------------------------------------------
    ws = wb.add_worksheet("Assumptions")
    ws.set_column(0, 0, 46); ws.set_column(1, 1, 18); ws.set_column(2, 2, 74)
    ws.hide_gridlines(2)
    ws.write(0, 0, "Assumptions - yellow cells are editable inputs", f["h1"])
    nav(ws, 1)
    for c, h in enumerate(["Input", "Value", "Basis / source"]):
        ws.write(2, c, h, f["hdr"])
    ws.set_row(2, 26)

    RATE, UNITS, COLL = 0.01, 1_000_000_000, 10_000_000
    RACSHARE, GROWTH, EXEMPT = 0.50, 0.02, 0.87

    rows = [
        ("Assessment rate per unit", RATE, "inusd",
         "Published: $0.01 per assessable unit, unchanged since 1 Apr 2023."),
        ("US assessable units per year", UNITS, "in",
         "Derived: ~$10M annual collections / $0.01. Cross-checks against the "
         "1.15B units produced in 2018 cited in the rulemaking, less exempt product."),
        ("Actual annual collections", COLL, "in",
         "Published: CMCB confirmed collections tracking to roughly $10M/yr."),
        ("Minimum share returned to RACs", RACSHARE, "inpct",
         "Published: at least 50% of program dollars go to the region that generated them."),
        ("Maximum national share", 0.50, "inpct",
         "Published: no more than 50% is spent at national level."),
        ("Annual unit volume growth", GROWTH, "inpct",
         "Assumption. Used for the 5-year projection. Change per your own outlook."),
        ("Assessable share of 327331 output", EXEMPT, "inpct",
         "DERIVED, NOT PUBLISHED. NAICS 327331 includes clay brick and hardscape, which "
         "the Order exempts, so a shipments-based split must be haircut. 1.0B assessable "
         "units (collections / rate) over the 1.15B units cited in the rulemaking gives "
         "~87%. Applies to the Census Shipments basis only - the project-scope bases "
         "already exclude exempt scope codes. Replace with a published product-mix "
         "figure when one is found."),
        ("Allocation basis", default_basis, "intxt",
         "Which evidence splits national volume across regions. Dropdown lists only "
         "bases with data in this build. See Data Status and Sources."),
    ]
    for i, (label, val, fmt, basis) in enumerate(rows):
        rr = 3 + i
        ws.write(rr, 0, label, f["txt"])
        ws.write(rr, 1, val, f[fmt])
        ws.write(rr, 2, basis, f["body"])
        ws.set_row(rr, max(30, 13 * (len(basis) // 72 + 1)))

    ws.data_validation(R_BASIS, 1, R_BASIS, 1, {
        "validate": "list",
        "source": [b[0] for b in loaded],
        "error_title": "Basis not loaded",
        "error_message": "Only bases with data in this build can be selected. "
                         "See the Data Status tab.",
    })

    # Every formula is written WITH its computed value. xlsxwriter emits no cached
    # result, so a reader whose Excel has automatic calculation switched off (or any
    # non-Excel viewer) would otherwise open this to a grid of zeros.
    ws.write(13, 0, "Derived", f["h2"])
    ws.write(14, 0, "Implied units from collections", f["txt"])
    ws.write_formula(14, 1, f"=B{R_COLL+1}/B{R_RATE+1}", f["calc"], COLL / RATE)
    ws.write(14, 2, "Sanity check: should be close to the units input above.", f["note"])
    ws.write(15, 0, "Dollars available to RACs per year", f["txt"])
    ws.write_formula(15, 1, f"=B{R_COLL+1}*B{R_RACSHARE+1}", f["calcU"], COLL * RACSHARE)
    ws.write(15, 2, "The pool the five regions share.", f["note"])

    # ---------------- TAM by RAC ---------------------------------------------
    ws = wb.add_worksheet("TAM by RAC")
    ws.set_column(0, 0, 26); ws.set_column(1, 9, 16)
    ws.hide_gridlines(2)
    ws.write(0, 0, "TAM by Regional Advisory Council", f["h1"])
    nav(ws, 1)
    ws.write(2, 0, "Share is driven by the allocation basis on the Assumptions tab. "
                   "Change that dropdown to see the split move.", f["note"])

    heads = ["RAC", "States", "Producers", "Projects", "Assessable Scope $",
             "Share of Basis", "Assessable Units", "Checkoff $ Generated",
             "RAC Dollars (50%)", "$ per Project"]
    for c, h in enumerate(heads):
        ws.write(4, c, h, f["hdr"])
    ws.set_row(4, 30)

    # Hidden helper block: one column per basis, headed by the basis name, so the
    # picker can resolve by MATCH rather than a nest of IFs.
    H0 = 12
    for j, (label, fn, fmt, ok, _note) in enumerate(BASES):
        ws.write(4, H0 + j, label, f["hdr"])

    first = 5
    for i, rac in enumerate(RACS):
        rr = first + i
        d = racs.get(rac, {})
        ws.write(rr, 0, rac, f["txt"])
        ws.write(rr, 1, len(RAC_STATES[rac]), f["num"])
        ws.write(rr, 2, prod_by_rac.get(rac, 0), f["num"])
        ws.write(rr, 3, d.get("projects", 0), f["num"])
        ws.write(rr, 4, d.get("assessable_value", 0), f["usdB"])
        for j, (label, fn, fmt, ok, _note) in enumerate(BASES):
            if ok:
                ws.write(rr, H0 + j, fn(rac), f[fmt])
            else:
                ws.write_blank(rr, H0 + j, None)

    last = first + len(RACS) - 1
    hcol = lambda j: xlsxwriter.utility.xl_col_to_name(H0 + j)
    span = f"${hcol(0)}${first+1}:${hcol(len(BASES)-1)}${last+1}"
    hdr_span = f"${hcol(0)}${first}:${hcol(len(BASES)-1)}${first}"

    def_fn = dict((b[0], b[1]) for b in BASES)[default_basis]
    raw = {rac: def_fn(rac) for rac in RACS}
    tot_raw = sum(raw.values()) or 1

    shares, rac_dollars = [], []
    for i, rac in enumerate(RACS):
        rr = first + i
        row_span = f"${hcol(0)}{rr+1}:${hcol(len(BASES)-1)}{rr+1}"
        pick = f"INDEX({row_span},MATCH({A}!$B${R_BASIS+1},{hdr_span},0))"
        tot = f"SUM(INDEX({span},0,MATCH({A}!$B${R_BASIS+1},{hdr_span},0)))"
        sh = raw[rac] / tot_raw
        un = sh * UNITS
        ck = un * RATE
        rd = ck * RACSHARE
        pj = racs.get(rac, {}).get("projects", 0)
        shares.append(sh); rac_dollars.append(rd)
        ws.write_formula(rr, 5, f"=IFERROR({pick}/{tot},0)", f["calcP"], sh)
        ws.write_formula(rr, 6, f"=F{rr+1}*{A}!$B${R_UNITS+1}", f["calc"], un)
        ws.write_formula(rr, 7, f"=G{rr+1}*{A}!$B${R_RATE+1}", f["calcU"], ck)
        ws.write_formula(rr, 8, f"=H{rr+1}*{A}!$B${R_RACSHARE+1}", f["calcU"], rd)
        ws.write_formula(rr, 9, f"=IFERROR(I{rr+1}/D{rr+1},0)", f["calcU"], rd / pj if pj else 0)

    tr = last + 1
    ws.write(tr, 0, "TOTAL", f["tot"])
    for col, val in ((1, sum(len(RAC_STATES[x]) for x in RACS)),
                     (2, sum(prod_by_rac.get(x, 0) for x in RACS)),
                     (3, sum(racs.get(x, {}).get("projects", 0) for x in RACS))):
        ws.write_formula(tr, col,
                         f"=SUM({xlsxwriter.utility.xl_col_to_name(col)}{first+1}:"
                         f"{xlsxwriter.utility.xl_col_to_name(col)}{last+1})", f["tot"], val)
    ws.write_formula(tr, 4, f"=SUM(E{first+1}:E{last+1})", f["totU"],
                     sum(racs.get(x, {}).get("assessable_value", 0) for x in RACS))
    ws.write_formula(tr, 5, f"=SUM(F{first+1}:F{last+1})", f["totP"], sum(shares))
    ws.write_formula(tr, 6, f"=SUM(G{first+1}:G{last+1})", f["tot"], UNITS)
    ws.write_formula(tr, 7, f"=SUM(H{first+1}:H{last+1})", f["totU"], UNITS * RATE)
    ws.write_formula(tr, 8, f"=SUM(I{first+1}:I{last+1})", f["totU"], UNITS * RATE * RACSHARE)
    ws.set_column(H0, H0 + len(BASES) - 1, 15, None, {"hidden": True})

    # sensitivity: every basis, side by side, so the disagreement is visible
    sr = tr + 2
    ws.write(sr, 0, "Sensitivity: how the split changes by basis", f["h2"])
    ws.write(sr + 1, 0, "Blank column = that basis is not loaded in this build. "
                        "A wide spread means the bases disagree about that region and its "
                        "number should be treated as a range, not a point estimate.", f["note"])
    ws.set_row(sr + 1, 28)
    for c, h in enumerate(["RAC"] + [b[0] for b in BASES] + ["Spread (max-min)"]):
        ws.write(sr + 2, c, h, f["hdr"])
    ws.set_row(sr + 2, 44)
    tots = {b[0]: (sum(b[1](x) for x in RACS) or 1) for b in BASES}
    for i, rac in enumerate(RACS):
        rr = sr + 3 + i
        ws.write(rr, 0, rac, f["txt"])
        vals = []
        for j, (label, fn, fmt, ok, _n) in enumerate(BASES):
            if ok:
                v = fn(rac) / tots[label]
                vals.append(v)
                ws.write(rr, 1 + j, v, f["pct"])
            else:
                ws.write_blank(rr, 1 + j, None)
        ws.write(rr, 1 + len(BASES), (max(vals) - min(vals)) if vals else 0, f["pct"])

    # ---------------- 5-Year Projection --------------------------------------
    ws = wb.add_worksheet("5-Year Projection")
    ws.set_column(0, 0, 26); ws.set_column(1, 8, 16)
    ws.hide_gridlines(2)
    ws.write(0, 0, "Five-year projection of RAC dollars", f["h1"])
    nav(ws, 1)
    ws.write(2, 0, "Applies the growth rate from Assumptions to each region's share. "
                   "Year 1 equals the TAM tab.", f["note"])
    for c, h in enumerate(["RAC"] + [f"Year {i}" for i in range(1, 6)] + ["5-Yr Total", "CAGR"]):
        ws.write(4, c, h, f["hdr"])
    ws.set_row(4, 24)
    for i, rac in enumerate(RACS):
        rr = 5 + i
        src = first + i + 1  # matching row on TAM tab, 1-indexed
        ws.write(rr, 0, rac, f["txt"])
        yr_vals = [rac_dollars[i] * (1 + GROWTH) ** y for y in range(5)]
        for y in range(5):
            ws.write_formula(rr, 1 + y, f"='TAM by RAC'!I{src}*(1+{A}!$B${R_GROWTH+1})^{y}",
                             f["calcU"], yr_vals[y])
        ws.write_formula(rr, 6, f"=SUM(B{rr+1}:F{rr+1})", f["totU"], sum(yr_vals))
        ws.write_formula(rr, 7, f"=IFERROR((F{rr+1}/B{rr+1})^(1/4)-1,0)", f["calcP"], GROWTH)
    rr = 5 + len(RACS)
    ws.write(rr, 0, "TOTAL", f["tot"])
    col_tot = [sum(rac_dollars) * (1 + GROWTH) ** y for y in range(5)]
    for c in range(1, 7):
        L = xlsxwriter.utility.xl_col_to_name(c)
        ws.write_formula(rr, c, f"=SUM({L}6:{L}{rr})", f["totU"],
                         col_tot[c - 1] if c <= 5 else sum(col_tot))

    ws.write(rr + 2, 0, "Scenario view (total RAC dollars, Year 5)", f["h2"])
    for c, h in enumerate(["Scenario", "Growth rate", "Year 5 RAC dollars"]):
        ws.write(rr + 3, c, h, f["hdr"])
    for i, (nm, g) in enumerate([("Contraction", -0.03), ("Flat", 0.0),
                                 ("Base", 0.02), ("Expansion", 0.05)]):
        r2 = rr + 4 + i
        ws.write(r2, 0, nm, f["txt"])
        ws.write(r2, 1, g, f["inpct"])
        ws.write_formula(r2, 2, f"={A}!$B${R_COLL+1}*{A}!$B${R_RACSHARE+1}*(1+B{r2+1})^4",
                         f["calcU"], COLL * RACSHARE * (1 + g) ** 4)
    ws.write(rr + 9, 0, "Scenario growth rates are editable. They are independent of the "
                        "main growth input so you can compare without disturbing the model.",
             f["note"])

    # ---------------- Projects by RAC ----------------------------------------
    ws = wb.add_worksheet("Projects by RAC")
    ws.set_column(0, 0, 26); ws.set_column(1, 8, 17)
    ws.hide_gridlines(2)
    ws.write(0, 0, "Demand side: construction projects observed by RAC", f["h1"])
    nav(ws, 1)
    ws.write(2, 0, f"From {roll['total_projects']:,} ConstructConnect projects "
                   f"({roll.get('mapped_projects', 0):,} mapped to a region). This is a SAMPLE "
                   "selected for size and recency out of roughly 136,000 matching the saved "
                   "search, not a census, so treat the shape as indicative and the absolute "
                   "values as a floor. Assessable Scope excludes clay brick, SRW and paving.",
             f["note"])
    ws.set_row(2, 42)
    for c, h in enumerate(["RAC", "Projects", "Total Value", "Median Value",
                           "Assessable Projects", "Assessable Value", "Exempt-only Value",
                           "% of Assessable Value", "RAC Dollars (from TAM)"]):
        ws.write(4, c, h, f["hdr"])
    ws.set_row(4, 32)
    for i, rac in enumerate(RACS):
        d = racs.get(rac, {}); rr = 5 + i
        ws.write(rr, 0, rac, f["txt"])
        ws.write(rr, 1, d.get("projects", 0), f["num"])
        ws.write(rr, 2, d.get("project_value", 0), f["usd"])
        ws.write(rr, 3, d.get("median_value", 0), f["usd"])
        ws.write(rr, 4, d.get("assessable_projects", 0), f["num"])
        ws.write(rr, 5, d.get("assessable_value", 0), f["usd"])
        ws.write(rr, 6, d.get("exempt_value", 0), f["usd"])
        ws.write(rr, 7, d.get("share_of_assessable_value", 0), f["pct"])
        ws.write_formula(rr, 8, f"='TAM by RAC'!I{first + i + 1}", f["calcU"], rac_dollars[i])

    r2 = 5 + len(RACS) + 2
    ws.write(r2, 0, "By state", f["h2"])
    for c, h in enumerate(["State", "RAC", "Projects", "Project Value",
                           "Assessable Projects", "Assessable Value"]):
        ws.write(r2 + 1, c, h, f["hdr"])
    for i, s in enumerate(roll["states"]):
        rr = r2 + 2 + i
        ws.write(rr, 0, s["state"], f["txt"])
        ws.write(rr, 1, s["rac"], f["txt"])
        ws.write(rr, 2, s["projects"], f["num"])
        ws.write(rr, 3, s["project_value"], f["usd"])
        ws.write(rr, 4, s.get("assessable_projects", 0), f["num"])
        ws.write(rr, 5, s.get("assessable_value", 0), f["usd"])

    # ---------------- Contacts by RAC ----------------------------------------
    ws = wb.add_worksheet("Contacts by RAC")
    ws.set_column(0, 0, 26); ws.set_column(1, 11, 15)
    ws.hide_gridlines(2)
    ws.write(0, 0, "Who there is to call, by RAC", f["h1"])
    nav(ws, 1)
    if not con:
        ws.write(2, 0, "NOT LOADED. Build it with: PYTHONPATH=. .venv/bin/python "
                       "jobs/contacts_by_rac.py <ConstructConnect_Leads_V3.xlsx>", f["no"])
        ws.set_row(2, 30)
    else:
        ws.write(2, 0,
                 "ROWS are one per person-per-project; PEOPLE are deduplicated, because a "
                 "specifier on eleven projects is still one phone call. The two differ by "
                 "roughly half, so the distinction decides whether the callable universe "
                 "reads as 23,000 or 12,000. ASSESSABLE columns count only contacts on "
                 "projects carrying a specific assessable masonry scope code - the ones that "
                 "actually consume checkoff-bearing product.", f["note"])
        ws.set_row(2, 56)
        heads = ["RAC", "Contact Rows", "Distinct People", "Named Rows",
                 "Personal Email", "Generic Email", "With Phone", "Specifiers",
                 "Owners", "Procurement", "Assessable Rows", "Assessable People"]
        for c, h in enumerate(heads):
            ws.write(4, c, h, f["hdr"])
        ws.set_row(4, 34)
        keys = ["rows", "distinct_people", "named_rows", "personal_email",
                "generic_email", "with_phone", "specifier", "owner", "procurement",
                "a_rows", "a_distinct_people"]
        for i, rac in enumerate(RACS):
            d = con_by_rac.get(rac, {})
            rr = 5 + i
            ws.write(rr, 0, rac, f["txt"])
            for c, k in enumerate(keys):
                ws.write(rr, 1 + c, d.get(k, 0), f["num"])
        tr2 = 5 + len(RACS)
        ws.write(tr2, 0, "TOTAL (sum of rows)", f["tot"])
        for c, k in enumerate(keys):
            L = xlsxwriter.utility.xl_col_to_name(1 + c)
            ws.write_formula(tr2, 1 + c, f"=SUM({L}6:{L}{tr2})", f["tot"],
                             sum(con_by_rac.get(x, {}).get(k, 0) for x in RACS))
        # The distinct-people columns must NOT be summed: a person working across
        # two regions is distinct in each. The union is written as its own row.
        nr = tr2 + 1
        ws.write(nr, 0, "NATIONAL (deduplicated)", f["h2"])
        # column indices must track `keys`: Distinct People is keys[1] -> col 2,
        # Assessable People is keys[10] -> col 11. Writing past col 11 puts the
        # figure outside the table where nobody sees it.
        ws.write(nr, 1 + keys.index("distinct_people"),
                 con.get("national_distinct_people", 0), f["num"])
        ws.write(nr, 1 + keys.index("a_distinct_people"),
                 con.get("national_distinct_people_assessable", 0), f["num"])
        ws.write(nr + 1, 0,
                 f"The two distinct-people columns do not add up. Summing them gives "
                 f"{sum(con_by_rac.get(x, {}).get('distinct_people', 0) for x in RACS):,}, "
                 f"which double-counts the {con.get('cross_region_double_count', 0):,} people "
                 f"who appear in more than one region. The NATIONAL row is the union and is "
                 f"the number to quote.", f["note"])
        ws.set_row(nr + 1, 30)

    # ---------------- Target Companies ---------------------------------------
    ws = wb.add_worksheet("Target Companies")
    ws.set_column(0, 0, 26); ws.set_column(1, 1, 46); ws.set_column(2, 6, 15)
    ws.hide_gridlines(2)
    ws.write(0, 0, "Relationship targets: firms recurring across many projects", f["h1"])
    nav(ws, 1)
    if not con:
        ws.write(2, 0, "NOT LOADED. Run jobs/contacts_by_rac.py first.", f["no"])
    else:
        ws.write(2, 0, "Top 25 firms per region, ranked by ASSESSABLE-scope projects first, "
                       "then by total projects. A firm appearing on many jobs is a better "
                       "relationship target than the same number of unrelated contacts. "
                       "Firm names are the vendor's verbatim and are NOT entity-resolved, so "
                       "the same company can appear under name variants.", f["note"])
        ws.set_row(2, 44)
        for c, h in enumerate(["RAC", "Company", "HQ State", "Assessable Projects",
                               "Total Projects", "Contact Rows", "Named Contacts"]):
            ws.write(4, c, h, f["hdr"])
        ws.set_row(4, 30)
        rr = 5
        for rac in RACS:
            for e in con.get("top_companies", {}).get(rac, []):
                ws.write(rr, 0, rac, f["txt"])
                ws.write(rr, 1, e["company"], f["txt"])
                ws.write(rr, 2, e.get("state", ""), f["txt"])
                ws.write(rr, 3, e["assessable_projects"], f["num"])
                ws.write(rr, 4, e["projects"], f["num"])
                ws.write(rr, 5, e["contact_rows"], f["num"])
                ws.write(rr, 6, e["named"], f["num"])
                rr += 1
        ws.autofilter(4, 0, rr - 1, 6)
        ws.freeze_panes(5, 0)

    # ---------------- Producers by RAC ---------------------------------------
    ws = wb.add_worksheet("Producers by RAC")
    ws.set_column(0, 0, 26); ws.set_column(1, 4, 18)
    ws.hide_gridlines(2)
    ws.write(0, 0, "Supply side: concrete masonry producers by RAC", f["h1"])
    nav(ws, 1)
    if not plist:
        ws.write(2, 0, "NOT LOADED. data/producers_national.json is absent from this build, "
                       "so there is no supply-side plant count. Producing it needs a Serper / "
                       "Places sweep. Note that even when present it is a SEARCH, not a "
                       "census, and is known to over-weight Region 1 by roughly 8 points; "
                       "Census establishment counts are the better measure.", f["no"])
        ws.set_row(2, 60)
    else:
        ws.write(2, 0, f"{n_prod:,} producer locations identified via business-listing search. "
                       "Counts plant locations, not corporate parents, which is the right unit "
                       "for capacity.", f["note"])
        ws.set_row(2, 30)
        for c, h in enumerate(["RAC", "States", "Producer Locations", "% of US", "Per State"]):
            ws.write(4, c, h, f["hdr"])
        tot_p = sum(prod_by_rac.get(x, 0) for x in RACS) or 1
        for i, rac in enumerate(RACS):
            rr = 5 + i
            n = prod_by_rac.get(rac, 0)
            ws.write(rr, 0, rac, f["txt"])
            ws.write(rr, 1, len(RAC_STATES[rac]), f["num"])
            ws.write(rr, 2, n, f["num"])
            ws.write(rr, 3, n / tot_p, f["pct"])
            ws.write(rr, 4, n / max(len(RAC_STATES[rac]), 1), f["num1"])

    # ---------------- State Mapping ------------------------------------------
    ws = wb.add_worksheet("State Mapping")
    ws.set_column(0, 0, 10); ws.set_column(1, 1, 26); ws.set_column(2, 2, 60)
    ws.hide_gridlines(2)
    ws.write(0, 0, "State to RAC mapping", f["h1"])
    nav(ws, 1)
    ws.write(2, 0, "Region 5 is quoted verbatim from the Commerce Order. Region 1 matches the "
                   "twelve states in the client's own Region 1 list. Regions 2, 3 and 4 are "
                   "read off the CMC regional map. Correct anything here and re-run.", f["note"])
    ws.set_row(2, 30)
    for c, h in enumerate(["State", "RAC", "Note"]):
        ws.write(4, c, h, f["hdr"])
    i = 0
    for rac in RACS:
        for s in RAC_STATES[rac]:
            ws.write(5 + i, 0, s, f["intxt"])
            ws.write(5 + i, 1, rac, f["intxt"])
            ws.write(5 + i, 2,
                     "verbatim from the Order" if rac.startswith("Region 5")
                     else "matches client Region 1 list" if rac.startswith("Region 1")
                     else "read from CMC regional map", f["note"])
            i += 1

    # ---------------- Data Status --------------------------------------------
    ws = wb.add_worksheet("Data Status")
    ws.set_column(0, 0, 30); ws.set_column(1, 1, 14); ws.set_column(2, 2, 84)
    ws.hide_gridlines(2)
    ws.write(0, 0, "Data status: what is loaded and what is not", f["h1"])
    nav(ws, 1)
    ws.write(2, 0, "A basis that is not loaded is written as an empty column and excluded "
                   "from the Assumptions dropdown, rather than contributing a zero share. "
                   "A zero share and an unmeasured share are different claims.", f["note"])
    ws.set_row(2, 30)
    for c, h in enumerate(["Allocation basis", "Status", "What it is / what is needed"]):
        ws.write(4, c, h, f["hdr"])
    for i, (label, fn, fmt, ok, note) in enumerate(BASES):
        rr = 5 + i
        ws.write(rr, 0, label, f["txt"])
        ws.write(rr, 1, "LOADED" if ok else "not loaded", f["ok"] if ok else f["no"])
        ws.write(rr, 2, note, f["body"])
        ws.set_row(rr, max(28, 13 * (len(note) // 82 + 1)))

    rr = 5 + len(BASES) + 1
    ws.write(rr, 0, "Input files", f["h2"])
    for c, h in enumerate(["File", "Present", "Produced by"]):
        ws.write(rr + 1, c, h, f["hdr"])
    for i, (p, by) in enumerate([
        (ROLLUP, "jobs/rollup_from_workbook.py <ConstructConnect_Leads_V3.xlsx>"),
        (CONTACTS, "jobs/contacts_by_rac.py <ConstructConnect_Leads_V3.xlsx>"),
        (CENSUS, "jobs/census_cbp_rac.py  (needs www2.census.gov)"),
        (SHIPMENTS, "jobs/census_shipments_rac.py  (needs api.census.gov + CENSUS_API_KEY)"),
        (PRODUCERS, "jobs/discover_producers_national.py  (needs Serper)"),
    ]):
        r3 = rr + 2 + i
        ws.write(r3, 0, str(p), f["txt"])
        ws.write(r3, 1, "yes" if p.exists() else "no",
                 f["ok"] if p.exists() else f["no"])
        ws.write(r3, 2, by, f["body"])

    # ---------------- Sources & Method ---------------------------------------
    ws = wb.add_worksheet("Sources & Method")
    ws.set_column(0, 0, 34); ws.set_column(1, 1, 92)
    ws.hide_gridlines(2)
    ws.write(0, 0, "Sources and method", f["h1"])
    nav(ws, 1)
    notes = [
        ("MEASURED - Assessment rate", "$0.01 per assessable unit, effective 1 April 2023. "
                                       "concretemasonrycheckoff.org/assessments"),
        ("MEASURED - What is assessable", "Dry-cast units made on mechanised block machines, 3 "
                                          "inches or more in actual width, intended for masonry "
                                          "construction: gray, architectural, fence, lintel, "
                                          "screen and veneer units."),
        ("MEASURED - What is EXEMPT", "Pavers, segmental retaining wall units, clay brick, "
                                      "precast concrete lintels, and any unit under 3 inches. "
                                      "This matters: a hardscape-heavy producer or project "
                                      "contributes far less than its size suggests, and counting "
                                      "hardscape volume would overstate TAM."),
        ("MEASURED - Annual collections", "CMCB has confirmed collections tracking to roughly "
                                          "$10 million a year, consistent across reported quarters."),
        ("MEASURED - Unit volume", "1.15 billion units produced in 2018 per figures cited in the "
                                   "rulemaking. The model uses 1.0 billion ASSESSABLE units, "
                                   "derived from collections divided by rate, which is lower "
                                   "because of the exemptions."),
        ("MEASURED - 50/50 split", "At least half of program dollars return to the region that "
                                   "generated them; no more than half is spent nationally."),
        ("MODELLED - The regional split", "There is no published region-by-region unit volume. "
                                          "Every regional figure in this workbook is inferred "
                                          "from an allocation basis. The sensitivity block on the "
                                          "TAM tab is the honest error bar."),
        ("MODELLED - Assessable share", "The 87% haircut applied to shipments is derived from "
                                        "1.0B assessable over 1.15B total units, not from a "
                                        "published product-mix statistic. It is an editable cell."),
        ("SAMPLE - Demand-side data", "6,182 projects collected from ConstructConnect Insight "
                                      "with value, state, stage and CSI scope codes. A SAMPLE "
                                      "selected for size and recency out of roughly 136,000 "
                                      "matching the saved search, so its value distribution is "
                                      "skewed toward large projects. Use the shape, not the "
                                      "absolute totals. A stratified random sample would fix this."),
        ("Assessable scope definition", "Projects are counted as assessable scope when they carry "
                                        "a SPECIFIC masonry subsection code: Concrete unit masonry "
                                        "(CMU), Single-wythe unit masonry, or Fences/gates/site "
                                        "walls. Clay unit masonry, Retaining walls and Unit paving "
                                        "are counted as exempt. The generic 04 20 'Unit masonry' "
                                        "rollup is discarded: it appears on ~100% of projects and "
                                        "cannot discriminate between them."),
        ("A bias this exposed in our own data", "A business-listing producer sweep found 536 "
                                                "plants and put Region 1 at 27.2% of the US total. "
                                                "Census puts Region 1 at 18.1%. The gap is our own "
                                                "search effort: Region 1 had been swept far harder "
                                                "for an earlier piece of work, so it came out "
                                                "over-represented. A search finds what you looked "
                                                "for; a census does not have that problem. That is "
                                                "why a scraped producer count is never the default "
                                                "basis."),
        ("What is NOT in here", "Actual regional collections. The CMCB holds them via quarterly "
                                "producer reports. Obtaining even aggregate regional figures "
                                "would replace the modelled split with fact. Also absent from "
                                "this build: all Census bases, and the producer count. See Data "
                                "Status."),
        ("Rounding", "Round half up throughout. Percentages may not sum to exactly 100 in display."),
    ]
    for c, h in enumerate(["Item", "Detail"]):
        ws.write(3, c, h, f["hdr"])
    for i, (k, v) in enumerate(notes):
        ws.write(4 + i, 0, k, f["h2"])
        ws.write(4 + i, 1, v, f["body"])
        ws.set_row(4 + i, max(28, 13 * (len(v) // 88 + 1)))

    wb.close()

    print(f"wrote {OUT}")
    print(f"  default basis     : {default_basis}")
    print(f"  bases loaded      : {len(loaded)} of {len(BASES)} "
          f"({', '.join(b[0] for b in loaded)})")
    print(f"  bases NOT loaded  : {', '.join(b[0] for b in BASES if not b[3]) or 'none'}")
    print(f"\n  {'RAC':26s} {'share':>7s} {'units':>14s} {'RAC $':>12s}")
    for i, rac in enumerate(RACS):
        print(f"  {rac:26s} {shares[i]:6.1%} {shares[i]*UNITS:14,.0f} "
              f"${rac_dollars[i]:11,.0f}")


if __name__ == "__main__":
    main()
