"""Resolve a city and state for producers whose source row has none.

93 of the 176 producers arrived without a location, because Kenni's master list
only carried one where the company name happened to have a " - City, ST" suffix.
Splitting the deliverable by state on that basis would silently drop half the
contacts into an "unknown" bucket.

Their websites publish an address in the footer, and those pages are already in
the SQLite cache from the contact run, so this pass costs nothing. A mailing
address is read out of the page and the state taken from it, preferring an
address inside the twelve target states when a company lists several locations
(a producer with plants in four states should file under the one we care about).
"""
from __future__ import annotations

import csv
import re
from collections import Counter
from pathlib import Path

from msc.net import Http
from jobs.run_cmu_contacts import _site_pages

CSV_IN = Path("/Users/jacklumpe/Desktop/Construct Connect Scraper/cmu-linkedin/"
              "CMU_LinkedIn_Companies_PRODUCERS_ONLY.csv")
CSV_OUT = Path("/Users/jacklumpe/Desktop/Construct Connect Scraper/cmu-linkedin/"
               "CMU_LinkedIn_Companies_PRODUCERS_ONLY.csv")

TARGET = ["CT", "DE", "ME", "MD", "MA", "NH", "NJ", "NY", "PA", "RI", "VT", "WV"]
ALL_STATES = set("""AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN
MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC""".split())

# "Springdale, PA 15144" / "Chambersburg PA 17201"
ADDR_RE = re.compile(
    r"([A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){0,3})\s*,?\s+"
    r"\b([A-Z]{2})\b\s+(\d{5})(?:-\d{4})?")


def _locations(text: str) -> list[tuple[str, str]]:
    """(city, state) pairs that look like real mailing addresses."""
    out = []
    for m in ADDR_RE.finditer(text or ""):
        city, st = m.group(1).strip(), m.group(2).upper()
        if st not in ALL_STATES:
            continue
        # a "city" that is really a street or a stray capitalised phrase
        if re.search(r"\b(suite|ste|road|rd|street|st|ave|avenue|box|hwy|route)\b",
                     city, re.I):
            city = ""
        if len(city) > 40:
            city = ""
        out.append((city, st))
    return out


def main():
    rows = list(csv.DictReader(open(CSV_IN)))
    http = Http()
    fixed = Counter()
    need = [r for r in rows if not r["state"].strip()]
    print(f"{len(need)} of {len(rows)} producers need a state\n")

    for i, r in enumerate(need, 1):
        dom = (r.get("companyemaildomain") or "").strip()
        if not dom:
            continue
        text = _site_pages(dom, http)
        if not text:
            continue
        locs = _locations(text)
        if not locs:
            continue
        counts = Counter(locs)
        # prefer an address inside the twelve states this campaign covers
        in_target = [(c, s) for (c, s) in counts if s in TARGET]
        if in_target:
            city, st = max(in_target, key=lambda k: counts[k])
        else:
            city, st = counts.most_common(1)[0][0]
        r["state"] = st
        if city and not r.get("city"):
            r["city"] = city
        fixed[st] += 1
        if i % 20 == 0:
            print(f"  ...{i}/{len(need)} checked, {sum(fixed.values())} resolved")

    with open(CSV_OUT, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)

    have = sum(1 for r in rows if r["state"].strip())
    print(f"\nresolved {sum(fixed.values())} states from site addresses")
    print(f"producers with a state now: {have}/{len(rows)}")
    in_t = sum(1 for r in rows if r["state"].strip() in TARGET)
    print(f"  inside the 12 target states: {in_t}")
    print(f"  outside (HQ elsewhere)     : {have - in_t}")
    print("  newly resolved by state:", dict(fixed.most_common()))


if __name__ == "__main__":
    main()
