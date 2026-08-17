"""
Choose the next 2,500 projects to pull in full (round 3).

Round 3 is the first date-partitioned round. Rounds 1 and 2 between them consumed
the API's entire 10,000-record window for the lastUpdatedDate sort, so reaching
older projects requires slicing the query by date. Five weekly LastUpdatedDate
windows covering 2026-06-12 through 2026-07-16 were each fully paged, and every
window's returned count matched its advertised numFound, so coverage of that span
is complete rather than sampled.

Candidate pool is every project discovered in any round that has not yet been
pulled. Scoring is the same priority_score throughout, so a leftover from round 1
competes on equal terms with a fresh find from round 3.
"""
import glob
import json
import os
from collections import Counter
from datetime import date

from classify import classify_fit, priority_score, value_band

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
TODAY = "2026-07-29"
TAKE = 2500


def days_since(d):
    if not d:
        return 999
    try:
        y, m, dd = (int(x) for x in str(d).split("-")[:3])
        ty, tm, td = (int(x) for x in TODAY.split("-"))
        return (date(ty, tm, td) - date(y, m, dd)).days
    except Exception:
        return 999


# ---- everything already pulled -------------------------------------------
pulled = set()
for f in glob.glob(os.path.join(DATA, "details_*.json")):
    with open(f) as fh:
        pulled |= {str(r["id"]) for r in json.load(fh)}
print(f"already pulled: {len(pulled):,}")

pool = {}

# ---- slimmed discovery docs, any round ----------------------------------
for path in sorted(glob.glob(os.path.join(DATA, "discovery_round*.json"))):
    tag = os.path.basename(path).replace("discovery_", "").replace(".json", "")
    for d in json.load(open(path)):
        pid = str(d["id"])
        if pid in pulled or pid in pool:
            continue
        codes = [c for c in (d.get("csi") or "").split(",") if c]
        pool[pid] = {
            "id": pid, "value": d.get("v") or 0, "lu": d.get("lu") or "",
            "codes": codes, "csiN": d.get("csiN") or len(codes),
            "win": d.get("win") or "", "src": tag,
        }

# ---- round-1 rank file ---------------------------------------------------
for line in open(os.path.join(DATA, "discovery_rank.txt")):
    parts = line.strip().split("|")
    if len(parts) < 4 or not parts[0]:
        continue
    pid = parts[0]
    if pid in pulled or pid in pool:
        continue
    codes = [c for c in parts[3].split(",") if c]
    pool[pid] = {
        "id": pid, "value": int(parts[1]) if parts[1].isdigit() else 0,
        "lu": parts[2], "codes": codes, "csiN": len(codes),
        "win": "", "src": "round1-leftover",
    }

for k, v in Counter(r["src"] for r in pool.values()).most_common():
    print(f"  pool from {k}: {v:,}")
print(f"total candidate pool: {len(pool):,}")

# ---- score --------------------------------------------------------------
for r in pool.values():
    fit = classify_fit(r["codes"])
    r["fit"] = fit["fit"]
    r["score"] = priority_score(r["value"], days_since(r["lu"]), fit)
    r["band"] = value_band(r["value"])[0]

# Take every in-scope project first, then fill on score.
#
# Rounds 1 and 2 drew on recently-updated records, where 16.2% carry a specific
# MasterFormat subsection code. These older windows carry one on only 4.0% -
# ConstructConnect fills in trade detail as a job approaches bidding, so an
# older record usually has just the generic rollup tags. Selecting on score alone
# therefore let big untagged projects crowd out genuinely in-scope ones: it took
# 315 of the 586 available. Since a project carrying an actual rebar, structural
# steel, retaining wall or CMU code is the whole point of this exercise, those are
# taken first and the remaining slots go to the highest scores.
def in_scope(r):
    return "Generic" not in r["fit"] and "Out of scope" not in r["fit"]


ranked = sorted(
    pool.values(),
    key=lambda r: (0 if in_scope(r) else 1, -r["score"], -(r["value"] or 0)),
)
sel = ranked[:TAKE]

# ---- report -------------------------------------------------------------
L = []
L.append("ROUND 3 - date-partitioned selection")
L.append("Windows paged (LastUpdatedDate): 2026-06-12 to 2026-07-16, five weekly slices")
L.append("")
L.append(f"Candidate pool: {len(pool):,} projects not yet pulled")
for k, v in Counter(r["src"] for r in pool.values()).most_common():
    L.append(f"  {v:6,}  {k}")
L.append("")
L.append(f"Selected for full pull: {len(sel):,}")
L.append(f"Score range selected: {sel[-1]['score']} to {sel[0]['score']}")
if len(ranked) > TAKE:
    d = ranked[TAKE:]
    L.append(f"Score range dropped:  {d[-1]['score']} to {d[0]['score']}")
L.append("")
L.append("Selected by product fit:")
for k, v in Counter(r["fit"] for r in sel).most_common():
    L.append(f"  {v:6,}  {k}")
L.append("")
L.append("Selected by value band:")
for k, v in Counter(r["band"] for r in sel).most_common():
    L.append(f"  {v:6,}  {k}")
L.append("")
L.append("Selected by discovery window:")
for k, v in Counter(r["win"] or "(earlier round)" for r in sel).most_common():
    L.append(f"  {v:6,}  {k}")
L.append("")
L.append(f"Total value of selected: ${sum(r['value'] or 0 for r in sel):,}")
L.append(f"Requests this will cost: {len(sel)*2:,}")
L.append("")
L.append(f"Pool remaining after this round: {len(ranked) - len(sel):,}")

report = "\n".join(L)
print()
print(report)
open(os.path.join(DATA, "selection_report_round3.txt"), "w").write(report + "\n")
with open(os.path.join(DATA, "targets_round3.txt"), "w") as fh:
    for r in sel:
        fh.write(r["id"] + "\n")
open(os.path.join(DATA, "targets_round3_js.txt"), "w").write(
    json.dumps([r["id"] for r in sel]))
