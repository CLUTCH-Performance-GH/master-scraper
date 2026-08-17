"""Find CMU producers that neither Kenni's list nor the CMHA directory contains.

CMHA only lists paying members, and Kenni's list is whoever the team had already
touched. Independent block plants that belong to neither are invisible to both.
Google Places knows them because they are physical businesses with storefronts.

Queried per state across the vocabulary the industry actually uses, then filtered
hard: a result has to look like a producer, not a masonry *contractor*, a
landscaper, or a big-box retailer.
"""
from __future__ import annotations

import csv
import json
import re
from collections import Counter
from pathlib import Path

from msc.net import Serper

OUT = Path("data/cmu_places_discovery.json")
STATES = {
    "CT": "Connecticut", "DE": "Delaware", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "NH": "New Hampshire", "NJ": "New Jersey",
    "NY": "New York", "PA": "Pennsylvania", "RI": "Rhode Island",
    "VT": "Vermont", "WV": "West Virginia",
}
QUERIES = [
    "concrete block manufacturer",
    "concrete block supplier",
    "masonry supply",
    "concrete products manufacturer",
    "precast concrete manufacturer",
    "hardscape paver manufacturer",
    "brick and block supplier",
]

# Positive signal: the business makes or supplies the product.
KEEP = re.compile(
    r"\b(block|masonry|concrete|precast|brick|paver|hardscape|stone|cement|"
    r"builders? supply|building (supply|products|materials)|aggregate|quarry)\b", re.I)
# Negative: installers, landscapers, retailers and rental yards are not producers.
DROP = re.compile(
    r"\b(contractor|contracting|construction co|landscap|lawn|garden center|"
    r"home depot|lowe'?s|menards|84 lumber|rental|equipment|repair|cleaning|"
    r"waterproof|chimney|paving (company|contractor)|driveway|patio design|"
    r"restoration|remodel|foundation repair|storage|self storage|u-haul)\b", re.I)
ADDR = re.compile(r",\s*([A-Za-z .'-]+),\s*([A-Z]{2})\s+(\d{5})")


def _key(name: str) -> str:
    s = re.sub(r"\b(inc|llc|corp|corporation|company|co|ltd|the)\b", "", name.lower())
    return re.sub(r"[^a-z0-9]", "", s)


def main():
    serper = Serper()
    found: dict[str, dict] = {}
    per_state = Counter()

    for st, full in STATES.items():
        before = len(found)
        for q in QUERIES:
            try:
                data = serper.query(f"{q} {full}", num=20, endpoint="places")
            except Exception:
                continue
            for p in data.get("places", []):
                title = (p.get("title") or "").strip()
                addr = (p.get("address") or "").strip()
                if not title or not addr:
                    continue
                if DROP.search(title) or not KEEP.search(title):
                    continue
                m = ADDR.search(addr)
                if not m:
                    continue
                city, state, zipc = m.group(1).strip(), m.group(2).upper(), m.group(3)
                if state != st:
                    continue  # Places bleeds across borders; keep it honest
                k = _key(title)
                if not k or k in found:
                    continue
                site = (p.get("website") or "").strip()
                found[k] = {
                    "company": title, "city": city, "state": state, "zip": zipc,
                    "phone": (p.get("phoneNumber") or "").strip(),
                    "website": site, "address": addr,
                    "rating": p.get("rating"), "source": f"places:{q}",
                }
        per_state[st] = len(found) - before
        print(f"  {st} {full:16s} +{per_state[st]:3d} candidates "
              f"(running {len(found)}, serper={serper.queries_spent})")

    OUT.write_text(json.dumps(list(found.values()), indent=1))
    with_site = sum(1 for v in found.values() if v["website"])
    print(f"\n{len(found)} candidate producers, {with_site} with a website")
    print(f"serper spent: {serper.queries_spent}")
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
