"""Last-resort state resolution via Google Places (through Serper).

After reading addresses off company websites, 56 producers still had no state.
Their contacts would land in an unassigned bucket, which defeats a deliverable
whose whole purpose is a per-state split.

Places knows where a business physically is even when its website never prints
an address. One query per company, cached, and the result is only trusted when
the Places title actually matches the company we asked about.
"""
from __future__ import annotations

import csv
import re
from collections import Counter
from pathlib import Path

from msc.net import Serper

CSV = Path("/Users/jacklumpe/Desktop/Construct Connect Scraper/cmu-linkedin/"
           "CMU_LinkedIn_Companies_PRODUCERS_ONLY.csv")

TARGET = {"CT", "DE", "ME", "MD", "MA", "NH", "NJ", "NY", "PA", "RI", "VT", "WV"}
ALL_STATES = set("""AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN
MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC""".split())
ADDR_RE = re.compile(r",\s*([A-Za-z .'-]+),\s*([A-Z]{2})\s+\d{5}")

_LEGAL = re.compile(r"\b(inc|llc|corp|corporation|company|co|ltd|group)\b\.?", re.I)


def _tokens(name: str) -> set[str]:
    s = _LEGAL.sub("", name.lower())
    return {t for t in re.split(r"[^a-z0-9]+", s) if len(t) > 2}


def main():
    rows = list(csv.DictReader(open(CSV)))
    need = [r for r in rows if not r["state"].strip()]
    print(f"{len(need)} producers still without a state\n")
    serper = Serper()
    got = Counter()

    for i, r in enumerate(need, 1):
        name = r["companyname"].strip()
        want = _tokens(name)
        if not want:
            continue
        try:
            data = serper.query(f"{name} concrete masonry", num=5, endpoint="places")
        except Exception:
            continue
        for p in data.get("places", []):
            title = (p.get("title") or "")
            addr = (p.get("address") or "")
            # only trust a result whose name genuinely overlaps ours, otherwise
            # Places happily returns the nearest unrelated masonry yard
            if len(want & _tokens(title)) < max(1, len(want) // 2):
                continue
            m = ADDR_RE.search(addr)
            if not m:
                continue
            city, st = m.group(1).strip(), m.group(2).upper()
            if st not in ALL_STATES:
                continue
            r["state"] = st
            if city and not r.get("city"):
                r["city"] = city
            got[st] += 1
            break
        if i % 15 == 0:
            print(f"  ...{i}/{len(need)}, {sum(got.values())} resolved, "
                  f"serper={serper.queries_spent}")

    with open(CSV, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)

    have = sum(1 for r in rows if r["state"].strip())
    in_t = sum(1 for r in rows if r["state"].strip() in TARGET)
    print(f"\nresolved {sum(got.values())} more from Places")
    print(f"producers with a state: {have}/{len(rows)}  (in target states: {in_t})")
    print("  added:", dict(got.most_common()))
    print(f"  serper spent: {serper.queries_spent}")


if __name__ == "__main__":
    main()
