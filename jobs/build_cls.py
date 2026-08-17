"""Assemble the two CLS workbooks (separate from the ag-roster):
  output/CLS/CLS_MO_KY_IL_Locations_V1.xlsx
  output/CLS/CLS_MO_KY_IL_Contacts_V1.xlsx

Sources:
  - output/CLS/regionals.json  (workflow: MFA, Mid-West Fert, Next Gen Ag,
    Hopkinsville, FS/GROWMARK, Premier, and United Prairie once appended)
  - reuse: Nutrien KY+IL locations (nutrien_all.json), Simplot KY locations
    (simplot_locations.json), Nutrien KY/IL + Simplot KY CURRENT contacts.
Contacts: main tabs = current-confirmed only; unconfirmed -> Excluded tab.
"""
import json
import re
import sys
from pathlib import Path

from msc.workbook import write_workbook

CLS = Path("output/CLS")
CON = Path("output/contacts")
NUT_ALL = "/Users/jacklumpe/Desktop/CLS Additional Data/nutrien_all.json"
SIM_LOC = "/Users/jacklumpe/Desktop/CLS Additional Data/simplot_locations.json"

LOC_COLS = ["Company", "Location Name", "Street", "City", "State", "Zip", "Phone", "Type",
            "Source", "FS Member"]
CON_COLS = ["Company", "State", "City", "Name", "Title", "Role Category", "Email",
            "Email Confidence", "Phone", "LinkedIn", "Current Title / Confirmation",
            "Source", "Notes", "FS Member"]
DISPLAY = {"mfa": "MFA Incorporated", "midwest_fertilizer": "Mid-West Fertilizer",
           "next_gen_ag": "Next Generation Ag", "hopkinsville_elevator": "Hopkinsville Elevator",
           "fs_growmark": "FS", "premier_coop": "Premier Cooperative",
           "united_prairie": "United Prairie", "simplot_ky": "Simplot"}

_SLUG2MEMBER = {
    "evergreenfs": "Evergreen FS", "westcentralfs": "West Central FS", "heritagefs": "Heritage FS",
    "conservfs": "Conserv FS", "gatewayfs": "Gateway FS", "prairielandfs": "Prairieland FS",
    "southcentralfs": "South Central FS", "sunrisefs": "Sunrise FS", "agviewfs": "Ag View FS",
    "goldstarfs": "Gold Star FS", "mmservice": "M&M Service Co (FS)", "aglandfs": "Ag-Land FS",
    "wabashvalleyfs": "Wabash Valley Service Co (FS)", "southernfs": "Southern FS",
    "stephensonfs": "Stephenson Service Co (FS)", "centralcommodityfs": "Central Commodity FS",
    "graincofs": "GRAINCO FS", "stclairfs": "St. Clair Service Co (FS)", "tricountyfs": "TriCounty FS",
    "piattfs": "Piatt County Service Co (FS)",
}
_MEMBER_RE = re.compile(r"([A-Z][A-Za-z.&'\- ]*?(?:FS|Service Co(?:mpany)?))\b")


def _canon(m):
    return re.sub(r"Service Co \(FS\)", "Service Company (FS)", (m or "").strip())


def loc_member(name):
    m = _MEMBER_RE.match((name or "").strip())
    return _canon(m.group(1)) if m else ""


def con_member(x):
    if x.get("fs_member"):
        return _canon(x["fs_member"])
    m = re.search(r"fscooperatives\.com/([a-z0-9\-]+)/", x.get("source", "") or "")
    return _canon(_SLUG2MEMBER.get(m.group(1), "")) if m else ""


def main(version="V2"):
    reg = json.loads((CLS / "regionals.json").read_text())["companies"]
    loc_by, con_by, excluded, loc_all, con_all = {}, {}, [], [], []

    def add_loc(disp, row):
        loc_by.setdefault(disp, []).append(row)
        loc_all.append(row)

    seen_con = set()

    def add_con(disp, row, current):
        if not current:
            excluded.append(row)
            return
        li = (row[9] or "").strip().lower()
        key = li or disp + "|" + re.sub(r"[^a-z]", "", (row[3] or "").lower())
        if key in seen_con:
            return  # dedupe by LinkedIn URL, else company+name
        seen_con.add(key)
        con_by.setdefault(disp, []).append(row)
        con_all.append(row)

    # ---- regionals (workflow) ----
    for c in reg:
        disp = DISPLAY.get(c["key"], c["name"])
        loc = c.get("loc", {})
        src = loc.get("locator_url") or loc.get("primary_domain", "")
        for L in loc.get("locations", []):
            if str(L.get("name", "")).startswith("NOTE") or not (
                    L.get("city") or L.get("street") or L.get("phone")):
                continue  # skip placeholder/NOTE rows
            add_loc(disp, [disp, L.get("name", ""), L.get("street", ""), L.get("city", ""),
                           L.get("state", ""), L.get("zip", ""), L.get("phone", ""),
                           L.get("type", ""), src,
                           loc_member(L.get("name", "")) if disp.startswith("FS") else ""])
        for x in c.get("contacts", {}).get("contacts", []):
            cur = bool(x.get("current_confirmed"))
            add_con(disp, [disp, x.get("state", ""), x.get("city", ""), x.get("name", ""),
                           x.get("title", ""), x.get("role_category", ""), x.get("email") or "",
                           x.get("email_confidence", "") if x.get("email") else "",
                           x.get("phone") or "", x.get("linkedin") or "",
                           "Current (verified)" if cur else "UNVERIFIED",
                           x.get("source", "") or "LinkedIn/company site", "",
                           con_member(x) if disp.startswith("FS") else ""], cur)

    # ---- reuse: Nutrien locations (KY + IL) ----
    for r in json.loads(Path(NUT_ALL).read_text()).get("data", []):
        if r.get("state") in ("Kentucky", "Illinois") and r.get("type") != "Operationally Closed":
            add_loc("Nutrien Ag Solutions", ["Nutrien Ag Solutions", r.get("location", ""),
                    r.get("address", ""), r.get("city", ""), r.get("state", ""),
                    r.get("zipcode", ""), r.get("phone", ""), r.get("type", ""), "Nutrien locator", ""])
    # ---- reuse: Simplot locations (KY) ----
    sd = json.loads(Path(SIM_LOC).read_text())
    sd = sd if isinstance(sd, list) else sd.get("data", [])
    for r in sd:
        if str(r.get("state", "")).upper() in ("KY", "KENTUCKY"):
            add_loc("Simplot", ["Simplot", r.get("name", ""), r.get("street", ""), r.get("city", ""),
                    r.get("state", ""), r.get("zip", ""), r.get("phone", ""),
                    "Simplot Grower Solutions", "Simplot locator", ""])

    # ---- reuse: Nutrien contacts (KY from V8, IL from cls run) + Simplot KY ----
    def reuse_contacts(disp, path, states):
        for r in json.loads((CON / path).read_text()):
            if r.get("employment") == "current" and r.get("state") in states:
                add_con(disp, [disp, r.get("state", ""), r.get("city", ""), r.get("name", ""),
                        r.get("title", ""), r.get("role_category", ""), r.get("email", ""),
                        r.get("email_confidence", ""), r.get("phone", ""), r.get("linkedin", ""),
                        r.get("current_role", "") or "Current (verified)",
                        r.get("source_url", ""), r.get("notes", ""), ""], True)
    reuse_contacts("Nutrien Ag Solutions", "nutrien.json", ["Kentucky"])
    reuse_contacts("Nutrien Ag Solutions", "nutrien_il.json", ["Illinois"])
    reuse_contacts("Simplot", "simplot.json", ["Kentucky"])

    # ---- write both workbooks (MASTER first) ----
    loc_sheets = {"MASTER (all locations)": (LOC_COLS, loc_all)}
    for d in sorted(loc_by):
        loc_sheets[d[:31]] = (LOC_COLS, loc_by[d])
    con_sheets = {"MASTER (all contacts)": (CON_COLS, con_all)}
    for d in sorted(con_by):
        con_sheets[d[:31]] = (CON_COLS, con_by[d])
    if excluded:
        con_sheets["Excluded (unverified)"] = (CON_COLS, excluded)

    write_workbook(CLS / f"CLS_MO_KY_IL_Locations_{version}.xlsx", loc_sheets)
    write_workbook(CLS / f"CLS_MO_KY_IL_Contacts_{version}.xlsx", con_sheets)
    print(f"LOCATIONS: {len(loc_all)} across {len(loc_by)} companies")
    print(f"CONTACTS (current): {len(con_all)} across {len(con_by)} companies | excluded(unverified): {len(excluded)}")
    from collections import Counter
    print("locations/co:", dict(Counter(r[0] for r in loc_all)))
    print("contacts/co:", dict(Counter(r[0] for r in con_all)))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "V2")
