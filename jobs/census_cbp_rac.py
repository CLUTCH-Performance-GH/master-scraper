"""US Census County Business Patterns -> RAC allocation driver.

NAICS 327331 is "Concrete Block and Brick Manufacturing", which is precisely the
industry the checkoff assesses. CBP publishes establishment counts, employment
and payroll for it by state, annually, from a mandatory survey.

This is a materially better allocation basis than anything scraped, for one
reason: it is a census rather than a search. A business-listing sweep finds what
is findable, and how hard you look varies by region. Our own Places producer
count over-weighted Region 1 by roughly 8 points simply because Region 1 had been
searched more thoroughly for an earlier piece of work. CBP has no such bias.

Employment is the default driver rather than establishment count, because the
assessment is per unit manufactured and a 200-person plant makes far more block
than a 10-person yard. Establishment count treats them as equal.

Source: https://www2.census.gov/programs-surveys/cbp/datasets/<year>/cbp<yy>st.zip
No API key required for the flat files.
"""
from __future__ import annotations

import csv
import io
import json
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path

NAICS = "327331"
YEAR = 2022
URL = f"https://www2.census.gov/programs-surveys/cbp/datasets/{YEAR}/cbp{str(YEAR)[2:]}st.zip"
OUT = Path("data/census_cbp_rac.json")

RAC = {
    "Region 1 - Northeast": ["CT", "DE", "ME", "MD", "MA", "NH", "NJ", "NY", "PA", "RI", "VT", "WV"],
    "Region 2 - Southeast": ["AL", "FL", "GA", "MS", "NC", "SC", "TN", "VA"],
    "Region 3 - Midwest":   ["IA", "IL", "IN", "KY", "MI", "MN", "NE", "ND", "OH", "SD", "WI"],
    "Region 4 - Central":   ["AR", "AZ", "KS", "LA", "MO", "NM", "OK", "TX"],
    "Region 5 - West":      ["AK", "CA", "CO", "HI", "ID", "MT", "NV", "OR", "UT", "WA", "WY"],
}
S2R = {s: r for r, ss in RAC.items() for s in ss}
FIPS = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT",
    "10": "DE", "12": "FL", "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN",
    "19": "IA", "20": "KS", "21": "KY", "22": "LA", "23": "ME", "24": "MD", "25": "MA",
    "26": "MI", "27": "MN", "28": "MS", "29": "MO", "30": "MT", "31": "NE", "32": "NV",
    "33": "NH", "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND", "39": "OH",
    "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD", "47": "TN",
    "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA", "54": "WV", "55": "WI",
    "56": "WY",
}


def main():
    print(f"fetching {URL}")
    raw = urllib.request.urlopen(URL, timeout=180).read()
    zf = zipfile.ZipFile(io.BytesIO(raw))
    name = [n for n in zf.namelist() if n.endswith(".txt")][0]
    text = zf.read(name).decode("latin-1")

    by_state, by_rac = {}, defaultdict(lambda: {"estab": 0, "emp": 0, "payroll": 0})
    suppressed = 0
    for row in csv.DictReader(io.StringIO(text)):
        if (row.get("naics") or "").strip() != NAICS:
            continue
        st = FIPS.get(row["fipstate"].zfill(2))
        if not st or st not in S2R:
            continue
        est = int(row.get("est") or 0)
        try:
            emp = int(row.get("emp") or 0)
        except ValueError:
            emp = 0
        try:
            pay = int(row.get("ap") or 0)
        except ValueError:
            pay = 0
        # CBP suppresses employment where it would disclose a single firm; the
        # establishment count is still published. Track it rather than treating
        # a suppressed zero as a real zero.
        if est and not emp:
            suppressed += 1
        by_state[st] = {"state": st, "rac": S2R[st], "estab": est, "emp": emp, "payroll": pay}
        r = by_rac[S2R[st]]
        r["estab"] += est
        r["emp"] += emp
        r["payroll"] += pay

    te = sum(v["estab"] for v in by_rac.values()) or 1
    tm = sum(v["emp"] for v in by_rac.values()) or 1
    tp = sum(v["payroll"] for v in by_rac.values()) or 1
    out = []
    for rac in RAC:
        v = by_rac[rac]
        out.append({
            "rac": rac, "estab": v["estab"], "emp": v["emp"], "payroll_k": v["payroll"],
            "share_estab": v["estab"] / te, "share_emp": v["emp"] / tm,
            "share_payroll": v["payroll"] / tp,
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "year": YEAR, "naics": NAICS, "source": URL,
        "racs": out, "states": sorted(by_state.values(), key=lambda r: (r["rac"], -r["estab"])),
        "total_estab": te, "total_emp": tm, "total_payroll_k": tp,
        "states_with_suppressed_employment": suppressed,
    }, indent=1))

    print(f"\nNAICS {NAICS} ({YEAR}) - {te:,} establishments, {tm:,} employees, "
          f"${tp/1e6:.2f}B payroll")
    print(f"{'RAC':26s} {'estab':>6s} {'share':>7s} {'emp':>8s} {'share':>7s}")
    for r in out:
        print(f"{r['rac']:26s} {r['estab']:6d} {r['share_estab']:7.1%} "
              f"{r['emp']:8,d} {r['share_emp']:7.1%}")
    if suppressed:
        print(f"\n{suppressed} states have employment suppressed for disclosure; "
              f"establishment counts there are still exact.")
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
