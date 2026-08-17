"""National CMU producer census via Google Places, for RAC-level supply data.

The Region 1 run already proved the method. This extends it to the other 38
states so the TAM model has a supply-side allocation driver (where the block
plants physically are) alongside the demand-side one (where the projects are).

Producer counts are a better proxy for assessable unit volume than project value
is: the checkoff is levied per unit manufactured, and a region's plant count
tracks its manufacturing capacity, whereas project value is skewed by a handful
of mega-projects that may consume no block at all.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from msc.net import Serper

OUT = Path("data/producers_national.json")
DONE = Path("data/producers_national_progress.json")

RAC = {
    "Region 1 - Northeast": ["CT", "DE", "ME", "MD", "MA", "NH", "NJ", "NY", "PA", "RI", "VT", "WV"],
    "Region 2 - Southeast": ["AL", "FL", "GA", "MS", "NC", "SC", "TN", "VA"],
    "Region 3 - Midwest":   ["IA", "IL", "IN", "KY", "MI", "MN", "NE", "ND", "OH", "SD", "WI"],
    "Region 4 - Central":   ["AR", "AZ", "KS", "LA", "MO", "NM", "OK", "TX"],
    "Region 5 - West":      ["AK", "CA", "CO", "HI", "ID", "MT", "NV", "OR", "UT", "WA", "WY"],
}
FULL = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
STATE2RAC = {s: r for r, ss in RAC.items() for s in ss}

# Narrowed to block/CMU manufacture. The checkoff exempts pavers and SRW units,
# so a pure hardscape yard is not an assessable producer and its inclusion would
# inflate the count.
QUERIES = [
    "concrete block manufacturer",
    "concrete masonry unit manufacturer",
    "concrete block plant",
    "masonry supply block",
]
KEEP = re.compile(r"\b(block|masonry|concrete|cmu|brick|precast|cement)\b", re.I)
DROP = re.compile(
    r"\b(contractor|contracting|landscap|lawn|garden|home depot|lowe'?s|menards|"
    r"rental|equipment|repair|cleaning|waterproof|chimney|driveway|patio|"
    r"restoration|remodel|foundation repair|storage|u-haul|ready ?mix delivery)\b", re.I)
ADDR = re.compile(r",\s*([A-Za-z .'-]+),\s*([A-Z]{2})\s+(\d{5})")


def _key(name):
    s = re.sub(r"\b(inc|llc|corp|corporation|company|co|ltd|the)\b", "", name.lower())
    return re.sub(r"[^a-z0-9]", "", s)


def main():
    serper = Serper()
    found = {}
    if OUT.exists():
        for r in json.loads(OUT.read_text()):
            found[_key(r["company"])] = r
    done = set(json.loads(DONE.read_text())) if DONE.exists() else set()
    print(f"resuming with {len(found)} producers, {len(done)} states already swept")

    for abbr, full in FULL.items():
        if abbr in done:
            continue
        before = len(found)
        for q in QUERIES:
            try:
                data = serper.query(f"{q} {full}", num=20, endpoint="places")
            except Exception:
                continue
            for p in data.get("places", []):
                title = (p.get("title") or "").strip()
                addr = (p.get("address") or "").strip()
                if not title or not addr or DROP.search(title) or not KEEP.search(title):
                    continue
                m = ADDR.search(addr)
                if not m or m.group(2).upper() != abbr:
                    continue
                k = _key(title)
                if not k or k in found:
                    continue
                found[k] = {
                    "company": title, "city": m.group(1).strip(), "state": abbr,
                    "zip": m.group(3), "rac": STATE2RAC.get(abbr, ""),
                    "phone": (p.get("phoneNumber") or "").strip(),
                    "website": (p.get("website") or "").strip(),
                }
        done.add(abbr)
        OUT.write_text(json.dumps(list(found.values()), indent=1))
        DONE.write_text(json.dumps(sorted(done)))
        print(f"  {abbr} {full:16s} +{len(found)-before:3d}  total={len(found):4d}  "
              f"serper={serper.queries_spent}")

    by_rac = Counter(v["rac"] for v in found.values() if v["rac"])
    print(f"\n{len(found)} producers nationally")
    for r in sorted(by_rac):
        print(f"  {r:26s} {by_rac[r]:4d}")
    print(f"serper spent: {serper.queries_spent}")


if __name__ == "__main__":
    main()
