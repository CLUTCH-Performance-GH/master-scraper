"""
Score the discovery pool and emit the ordered list of project IDs to pull in full.

Input:  data/discovery_rank.txt   id|value|lastUpdated|csiPrefixes|csiCount
Output: data/targets.txt          one project id per line, best lead first
        data/selection_report.txt human-readable summary of what was selected
"""

import os
import sys
from collections import Counter
from datetime import datetime, timezone

from classify import classify_fit, value_band, priority_score

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
TARGET_COUNT = int(sys.argv[1]) if len(sys.argv) > 1 else 2500

TODAY = datetime.now(timezone.utc)


def main():
    src = os.path.join(DATA, "discovery_rank.txt")
    rows = []
    with open(src) as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            parts = line.split("|")
            if len(parts) < 5:
                continue
            pid, value, last_upd, prefixes, csi_count = parts[:5]
            v = float(value) if value else None
            codes = [p for p in prefixes.split(",") if p]
            days = None
            if last_upd:
                try:
                    d = datetime.strptime(last_upd, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    days = max(0, (TODAY - d).days)
                except ValueError:
                    pass
            fit = classify_fit(codes)
            rows.append({
                "id": pid,
                "value": v,
                "days": days,
                "fit": fit,
                "score": priority_score(v, days, fit),
                "band": value_band(v)[0],
                "csi_count": int(csi_count) if csi_count.isdigit() else 0,
            })

    # Deep paging sorted by lastUpdatedDate is not stable: many projects share the
    # same update date, so records shift between pages as we walk the offset and a
    # small number get returned twice (and a similar number get missed). Dedupe on
    # project id before selecting, and report the rate so the miss rate is visible
    # rather than silent.
    seen, unique_rows = set(), []
    for r in rows:
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        unique_rows.append(r)
    dupes = len(rows) - len(unique_rows)
    if dupes:
        print(f"Deduped {dupes} duplicate rows from unstable deep pagination "
              f"({dupes / len(rows):.1%} of fetched rows); "
              f"a comparable number of projects were likely skipped by the same effect.")
    rows = unique_rows

    rows.sort(key=lambda r: (-r["score"], -(r["value"] or 0)))

    # Pure score ranking is dominated by project value, which pushes every small
    # job below the cutoff even when it was updated yesterday. The brief is
    # explicit that a small project can qualify on recency, so reserve a slice of
    # the budget for small-or-unvalued projects updated in the last 7 days, taking
    # the best-scoring of them. Without this the small-project population is
    # structurally invisible, not merely under-weighted.
    SMALL_RESERVE = int(TARGET_COUNT * 0.10)
    SMALL_CEILING = 250_000
    RECENT_DAYS = 7

    def is_small_recent(r):
        v = r["value"]
        small = (v is None) or (v < SMALL_CEILING)
        recent = r["days"] is not None and r["days"] <= RECENT_DAYS
        return small and recent and r["fit"]["in_scope"]

    main_quota = TARGET_COUNT - SMALL_RESERVE
    chosen = rows[:main_quota]
    chosen_ids = {r["id"] for r in chosen}

    small_pool = [r for r in rows if r["id"] not in chosen_ids and is_small_recent(r)]
    reserved = small_pool[:SMALL_RESERVE]
    chosen = chosen + reserved

    # If the small-recent population could not fill its reserve, backfill from
    # the next-best projects overall rather than shipping a short list.
    if len(chosen) < TARGET_COUNT:
        have = {r["id"] for r in chosen}
        for r in rows:
            if len(chosen) >= TARGET_COUNT:
                break
            if r["id"] not in have:
                chosen.append(r)
                have.add(r["id"])

    chosen.sort(key=lambda r: (-r["score"], -(r["value"] or 0)))
    print(f"Small-but-recent reserve: {len(reserved):,} of {SMALL_RESERVE:,} slots filled "
          f"(pool had {len(small_pool):,} candidates)")

    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "targets.txt"), "w") as fh:
        for r in chosen:
            fh.write(r["id"] + "\n")

    def dist(key, items):
        return Counter(i[key] if not isinstance(i[key], dict) else i[key]["fit"] for i in items)

    lines = []
    lines.append(f"Discovery pool: {len(rows):,} projects")
    lines.append(f"Selected for full pull: {len(chosen):,}")
    lines.append("")
    lines.append(f"Score range selected: {chosen[-1]['score']:.0f} to {chosen[0]['score']:.0f}")
    lines.append(f"Score range dropped:  "
                 f"{rows[-1]['score']:.0f} to {rows[len(chosen)]['score']:.0f}"
                 if len(rows) > len(chosen) else "Nothing dropped")
    lines.append("")
    lines.append("Selected by product fit:")
    for k, n in dist("fit", chosen).most_common():
        lines.append(f"  {n:6,}  {k}")
    lines.append("")
    lines.append("Selected by value band:")
    band_counts = Counter(r["band"] for r in chosen)
    for k, n in band_counts.most_common():
        lines.append(f"  {n:6,}  {k}")
    lines.append("")
    lines.append("Selected by recency of last update:")
    buckets = Counter()
    for r in chosen:
        d = r["days"]
        b = "unknown" if d is None else (
            "0-1 days" if d <= 1 else "2-7 days" if d <= 7 else
            "8-30 days" if d <= 30 else "31-90 days" if d <= 90 else "90+ days")
        buckets[b] += 1
    for k in ["0-1 days", "2-7 days", "8-30 days", "31-90 days", "90+ days", "unknown"]:
        if buckets.get(k):
            lines.append(f"  {buckets[k]:6,}  {k}")
    lines.append("")
    total_v = sum(r["value"] or 0 for r in chosen)
    lines.append(f"Total value of selected projects: ${total_v:,.0f}")
    small_recent = sum(1 for r in chosen
                       if (r["value"] or 0) < 250_000 and (r["days"] is not None and r["days"] <= 7))
    lines.append(f"Small (<$250K) but updated within 7 days: {small_recent:,}")
    lines.append(f"Requests this will cost: {len(chosen) * 2:,} "
                 f"(1 detail + 1 contacts per project)")

    report = "\n".join(lines)
    with open(os.path.join(DATA, "selection_report.txt"), "w") as fh:
        fh.write(report + "\n")
    print(report)


if __name__ == "__main__":
    main()
