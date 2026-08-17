"""Fold the Places discoveries into the producer list.

Matching is on email domain first and a normalised name second. Domain is the
stronger signal: "CarbonBuilt" and "CarbonBuilt Danielson" are one company on
carbonbuilt.com, and the V1 list carried both, which duplicated every contact
under them.
"""
from __future__ import annotations

import csv
import json
import re
from collections import Counter
from pathlib import Path

CSV = Path("/Users/jacklumpe/Desktop/Construct Connect Scraper/cmu-linkedin/"
           "CMU_LinkedIn_Companies_PRODUCERS_ONLY.csv")
PLACES = Path("data/cmu_places_discovery.json")
OUT = Path("/Users/jacklumpe/Desktop/Construct Connect Scraper/cmu-linkedin/"
           "CMU_Producers_V2.csv")

COLS = ["companyname", "companywebsite", "companyemaildomain", "linkedincompanypageurl",
        "stocksymbol", "industry", "city", "state", "companycountry", "zipcode",
        "phone", "source"]
_LEGAL = re.compile(r"\b(inc|llc|corp|corporation|company|co|ltd|the|group)\b\.?", re.I)


def nkey(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _LEGAL.sub("", (name or "").lower()))


def dom(url: str) -> str:
    if not url:
        return ""
    m = re.search(r"https?://([^/]+)", url)
    h = (m.group(1) if m else url).lower().replace("www.", "").strip("/ ")
    return h if "." in h else ""


def main():
    existing = list(csv.DictReader(open(CSV)))
    by_dom, by_name = {}, {}
    merged: list[dict] = []

    for r in existing:
        rec = {c: r.get(c, "") for c in COLS}
        rec["source"] = "Kenni list / CMHA"
        d = (r.get("companyemaildomain") or "").lower()
        k = nkey(r["companyname"])
        # collapse the duplicate company names V1 shipped
        prior = by_dom.get(d) if d else None
        if prior is not None:
            # keep the shorter, cleaner name; carry over any missing location
            if len(rec["companyname"]) < len(prior["companyname"]):
                prior["companyname"] = rec["companyname"]
            for f in ("city", "state", "companywebsite", "zipcode"):
                if not prior.get(f) and rec.get(f):
                    prior[f] = rec[f]
            continue
        merged.append(rec)
        if d:
            by_dom[d] = rec
        by_name[k] = rec

    collapsed = len(existing) - len(merged)
    places = json.loads(PLACES.read_text())
    added, matched = 0, 0
    for p in places:
        d = dom(p.get("website", ""))
        k = nkey(p["company"])
        hit = (by_dom.get(d) if d else None) or by_name.get(k)
        if hit is not None:
            matched += 1
            # Places knows where it physically is; fill gaps we could not resolve
            for src, dst in (("city", "city"), ("state", "state"),
                             ("zip", "zipcode"), ("phone", "phone")):
                if p.get(src) and not hit.get(dst):
                    hit[dst] = p[src]
            continue
        rec = {
            "companyname": p["company"],
            "companywebsite": p.get("website", ""),
            "companyemaildomain": d,
            "linkedincompanypageurl": "", "stocksymbol": "",
            "industry": "Building Materials",
            "city": p.get("city", ""), "state": p.get("state", ""),
            "companycountry": "US", "zipcode": p.get("zip", ""),
            "phone": p.get("phone", ""), "source": "Google Places discovery",
        }
        merged.append(rec)
        if d:
            by_dom[d] = rec
        by_name[k] = rec
        added += 1

    with open(OUT, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS)
        w.writeheader()
        w.writerows(merged)

    st = Counter(r["state"] for r in merged if r["state"])
    TARGET = {"CT", "DE", "ME", "MD", "MA", "NH", "NJ", "NY", "PA", "RI", "VT", "WV"}
    print(f"existing rows in           : {len(existing)}")
    print(f"  duplicate companies collapsed: {collapsed}")
    print(f"places candidates          : {len(places)}")
    print(f"  already known (enriched) : {matched}")
    print(f"  NEW producers added      : {added}")
    print(f"\ntotal producers now        : {len(merged)}")
    print(f"  with a website           : {sum(1 for r in merged if r['companywebsite'])}")
    print(f"  with an email domain     : {sum(1 for r in merged if r['companyemaildomain'])}")
    print(f"  with a phone             : {sum(1 for r in merged if r['phone'])}")
    print(f"  in the 12 target states  : {sum(v for k, v in st.items() if k in TARGET)}")
    print("  by state:", dict(sorted((k, v) for k, v in st.items() if k in TARGET)))
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
