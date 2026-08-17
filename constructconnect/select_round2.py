"""
Choose the next 2,500 projects to pull in full.

Candidate pool is two sources joined:
  1. round-2 discovery, offsets 5,000-9,900 of the lastUpdatedDate desc sort
  2. the 2,426 projects scored during round 1 that did not make that cut

Everything already pulled is excluded. Scoring is the same priority_score used in
round 1, so the two sources are directly comparable and the selection is a single
ranked list rather than "leftovers first".
"""
import glob
import json
import os

from classify import classify_fit, priority_score, value_band

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

TODAY = "2026-07-28"


def days_since(d):
    if not d:
        return 999
    try:
        from datetime import date
        y, m, dd = (int(x) for x in d.split("-")[:3])
        ty, tm, td = (int(x) for x in TODAY.split("-"))
        return (date(ty, tm, td) - date(y, m, dd)).days
    except Exception:
        return 999


# ---- what we already have -------------------------------------------------
pulled = set()
for f in glob.glob(os.path.join(DATA, "details_*.json")):
    with open(f) as fh:
        pulled |= {str(r["id"]) for r in json.load(fh)}
print(f"already pulled: {len(pulled):,}")

# ---- source 1: round-2 discovery -----------------------------------------
with open(os.path.join(DATA, "discovery_round2.json")) as fh:
    r2 = json.load(fh)

pool = {}
for d in r2:
    pid = str(d["id"])
    if pid in pulled:
        continue
    codes = [c for c in (d.get("csi") or "").split(",") if c]
    pool[pid] = {
        "id": pid,
        "value": d.get("v") or 0,
        "lu": d.get("lu") or "",
        "codes": codes,
        "csiN": d.get("csiN") or len(codes),
        "title": d.get("t") or "",
        "state": d.get("state") or "",
        "src": "round2",
    }
print(f"round-2 candidates after excluding pulled: {len(pool):,}")

# ---- source 2: round-1 leftovers ----------------------------------------
n_left = 0
for line in open(os.path.join(DATA, "discovery_rank.txt")):
    parts = line.strip().split("|")
    if len(parts) < 4:
        continue
    pid = parts[0]
    if pid in pulled or pid in pool:
        continue
    codes = [c for c in parts[3].split(",") if c]
    pool[pid] = {
        "id": pid,
        "value": int(parts[1] or 0),
        "lu": parts[2],
        "codes": codes,
        "csiN": len(codes),
        "title": "",
        "state": "",
        "src": "round1-leftover",
    }
    n_left += 1
print(f"round-1 leftovers added: {n_left:,}")
print(f"total candidate pool: {len(pool):,}")

# ---- score ---------------------------------------------------------------
for r in pool.values():
    fit = classify_fit(r["codes"])
    r["fit"] = fit["fit"]
    r["score"] = priority_score(r["value"], days_since(r["lu"]), fit)
    r["band"] = value_band(r["value"])[0]

ranked = sorted(pool.values(), key=lambda r: (-r["score"], -(r["value"] or 0)))
TAKE = 2500
sel = ranked[:TAKE]

# ---- report --------------------------------------------------------------
from collections import Counter

lines = []
lines.append(f"Candidate pool: {len(pool):,} projects")
lines.append(f"  from round-2 discovery: {sum(1 for r in pool.values() if r['src']=='round2'):,}")
lines.append(f"  from round-1 leftovers: {sum(1 for r in pool.values() if r['src']=='round1-leftover'):,}")
lines.append(f"Selected for full pull: {len(sel):,}")
lines.append("")
lines.append(f"Score range selected: {sel[-1]['score']} to {sel[0]['score']}")
if len(ranked) > TAKE:
    d = ranked[TAKE:]
    lines.append(f"Score range dropped:  {d[-1]['score']} to {d[0]['score']}")
lines.append("")
lines.append("Selected by source:")
for k, v in Counter(r["src"] for r in sel).most_common():
    lines.append(f"  {v:6,}  {k}")
lines.append("")
lines.append("Selected by product fit:")
for k, v in Counter(r["fit"] for r in sel).most_common():
    lines.append(f"  {v:6,}  {k}")
lines.append("")
lines.append("Selected by value band:")
for k, v in Counter(r["band"] for r in sel).most_common():
    lines.append(f"  {v:6,}  {k}")
lines.append("")
lines.append("Selected by recency:")
buckets = Counter()
for r in sel:
    ds = days_since(r["lu"])
    buckets["0-1 days" if ds <= 1 else "2-7 days" if ds <= 7 else "8-14 days" if ds <= 14 else "15+ days"] += 1
for k in ["0-1 days", "2-7 days", "8-14 days", "15+ days"]:
    if buckets[k]:
        lines.append(f"  {buckets[k]:6,}  {k}")
lines.append("")
lines.append(f"Total value of selected: ${sum(r['value'] or 0 for r in sel):,}")
lines.append(f"Requests this will cost: {len(sel)*2:,}")

report = "\n".join(lines)
print()
print(report)
with open(os.path.join(DATA, "selection_report_round2.txt"), "w") as fh:
    fh.write(report + "\n")

with open(os.path.join(DATA, "targets_round2.txt"), "w") as fh:
    for r in sel:
        fh.write(r["id"] + "\n")
with open(os.path.join(DATA, "targets_round2_js.txt"), "w") as fh:
    fh.write(json.dumps([r["id"] for r in sel]))

# remaining population accounting
print()
print(f"pool exhausted after this pull: {len(ranked) - len(sel):,} scored candidates left over")
