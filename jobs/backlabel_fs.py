"""V4: back-label the ~133 FS contacts that came from a generic 'FS' LinkedIn
search (no member co-op known). For each, look up their current LinkedIn
headline to identify WHICH FS member they work at, plus city + phone. Then their
email can be inferred from the member domain (flast). Writes back to regionals.json.
"""
import json
import re

import jobs.contacts_lib as cl
from msc.extract import extract_phone
from msc.net import Http, Serper

REG = "output/CLS/regionals.json"

# ordered most-specific-first so 'south central' / 'central commodity' win over 'southern'
MATCH = [
    ("Evergreen FS", r"evergreen"), ("Heritage FS", r"heritage"),
    ("West Central FS", r"west\s*central"), ("South Central FS", r"south\s*central"),
    ("Central Commodity FS", r"central\s*commodity"), ("Gateway FS", r"gateway"),
    ("Conserv FS", r"conserv"), ("Prairieland FS", r"prairieland"),
    ("Sunrise FS", r"sunrise"), ("Ag View FS", r"ag\s*view"),
    ("Gold Star FS", r"gold\s*star"), ("M&M Service Co (FS)", r"m\s*&\s*m|m and m service"),
    ("Ag-Land FS", r"ag[\-\s]?land"), ("Wabash Valley Service Co (FS)", r"wabash"),
    ("Stephenson Service Co (FS)", r"stephenson"), ("GRAINCO FS", r"grainco|grain\s*co\s*fs"),
    ("St. Clair Service Co (FS)", r"st\.?\s*clair"), ("TriCounty FS", r"tri[\-\s]?county"),
    ("Piatt County Service Co (FS)", r"piatt"), ("Southern FS", r"southern\s*fs|southern illinois"),
]
MEMBER2DOMAIN = {
    "Evergreen FS": "evergreenfs.com", "Heritage FS": "heritagefs.com",
    "West Central FS": "westcentralfs.com", "South Central FS": "southcentralfs.com",
    "Central Commodity FS": "centralcommodityfs.com", "Gateway FS": "gatewayfs.com",
    "Conserv FS": "conservfs.com", "Prairieland FS": "prairielandfs.com",
    "Sunrise FS": "sunrisefs.com", "Ag View FS": "agviewfs.com", "Gold Star FS": "goldstarfs.com",
    "M&M Service Co (FS)": "mmservicecompany.com", "Ag-Land FS": "aglandfs.com",
    "Wabash Valley Service Co (FS)": "wabashvalleyfs.com", "Southern FS": "southernfs.com",
    "Stephenson Service Co (FS)": "stephensonservicecompany.com",
    "GRAINCO FS": "graincofs.com", "St. Clair Service Co (FS)": "stclairservice.com",
    "TriCounty FS": "tricountyfs.com", "Piatt County Service Co (FS)": "piattservice.com",
}


def match_member(text):
    t = (text or "").lower()
    for member, pat in MATCH:
        if re.search(pat, t):
            return member
    return ""


def lookup(serper, name, li):
    target = (li or "").split("?")[0].rstrip("/").lower()
    for q in (f'"{name}" FS Illinois', f'"{name}"'):
        for it in serper.organic(q, num=10):
            if target and it.get("link", "").split("?")[0].rstrip("/").lower() == target:
                return it
    for it in serper.organic(f'"{name}" FS', num=8):
        if "linkedin.com/in" in it.get("link", ""):
            return it
    return None


def main():
    reg = json.loads(open(REG).read())
    serper, http = Serper(), Http()
    c2s = cl.load_city_resolver("data/us_cities.csv")
    fs = next(c for c in reg["companies"] if c["key"] == "fs_growmark")
    unl = [x for x in fs["contacts"]["contacts"]
           if not x.get("fs_member") and not re.search(r"fscooperatives\.com/[a-z0-9\-]+/", x.get("source", "") or "")]
    print(f"back-labeling {len(unl)} FS contacts...")
    labeled = cityf = phonef = 0
    for x in unl:
        it = lookup(serper, x["name"], x.get("linkedin", ""))
        if not it:
            continue
        blob = it.get("title", "") + " " + it.get("snippet", "")
        member = match_member(blob)
        if member:
            x["fs_member"] = member
            x["member_domain"] = MEMBER2DOMAIN.get(member, "")
            x["source"] = (x.get("source", "") + f" | member via headline: {it.get('title','')[:60]}").strip(" |")
            labeled += 1
        if not x.get("city"):
            city, expl, _ = cl.extract_location(it.get("snippet", ""))
            city = cl._best_known_city(city, c2s)
            if city:
                x["city"] = city
                cityf += 1
        if not x.get("phone"):
            ph = extract_phone(it.get("snippet", ""))
            if ph:
                x["phone"] = ph
                phonef += 1
    json.dump(reg, open(REG, "w"), indent=1)
    print(f"labeled member: {labeled}/{len(unl)} | +cities: {cityf} | +phones: {phonef} | serper={serper.queries_spent}")


if __name__ == "__main__":
    main()
