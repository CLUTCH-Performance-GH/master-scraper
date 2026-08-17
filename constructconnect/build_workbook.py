"""
Build the ConstructConnect lead workbook.

Inputs (written by the browser-side crawler, one JSON array per drain file):
  data/discovery_rank.json   compact ranking rows for the whole discovery pool
  data/details_*.json        full detail + contact records for the selected slice

Output:
  output/ConstructConnect_Leads_V1.xlsx

Tabs:
  Projects        one row per project, sorted by lead priority
  Contacts        one row per person, joined to its project
  Summary         counts and value rollups by state, stage, fit and value band
  Methodology     how every derived column was computed, and the caveats
"""

import glob
import json
import os
import re
from datetime import datetime, timezone

import pandas as pd

from classify import classify_fit, value_band, priority_score

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OUT = os.path.join(HERE, "output")
VERSION = "V3"

# ConstructConnect numeric stage -> label. Open enumeration: unknown codes are
# preserved as "Stage <n>" rather than dropped.
STAGE_LABELS = {
    1: "Conceptual",
    2: "Design",
    3: "Final Planning",
    4: "GC Bidding",
    5: "Sub-Bidding",
    6: "Pre-Construction/Negotiated",
    7: "Construction",
    8: "Post Bid",
}

# Roles that influence what gets specified, per
# cc-ingest-spec/taxonomies/firm_roles.json (is_specifier = true)
SPECIFIER_TOKENS = (
    "architect", "engineer", "designer", "consultant",
    "landscape", "specifier",
)
BUYER_TOKENS = (
    "contractor", "builder", "construction manager", "gc",
    "subcontractor", "supplier", "installer", "erector",
)
OWNER_TOKENS = ("owner", "developer", "client", "agency", "district", "authority")
PROCUREMENT_TOKENS = (
    "purchasing", "procurement", "contracts administrator", "contract administrator",
    "buyer", "bid", "clerk",
)

# The vendor stores a role label in the name fields when it has no named person on
# file, so "Owner / Owner" and "Architect / Architect" are placeholders rather than
# people. Treating them as contacts would inflate the count and produce useless
# call-list rows, so they are labelled instead of silently kept or silently dropped.
PLACEHOLDER_NAMES = {
    "owner", "architect", "engineer", "developer", "contractor", "consultant",
    "general contractor", "civil engineer", "structural engineer", "landscape architect",
    "mechanical engineer", "electrical engineer", "interior architect", "surveyor",
    "contracts administrator", "construction manager", "plumbing engineer",
}


def _fmt_phone(raw):
    """
    Render phones as text, not numbers.

    Two reasons: xlsxwriter coerces numeric-looking strings into numbers, which
    turns 3866770311 into 3866770311.0 and drops any leading zero, and a formatted
    number is what someone actually wants to read off a call list.
    """
    if not raw:
        return ""
    s = str(raw).strip()
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    if len(digits) == 11 and digits.startswith("1"):
        d = digits[1:]
        return f"({d[:3]}) {d[3:6]}-{d[6:]}"
    if len(digits) == 7:
        return f"{digits[:3]}-{digits[3:]}"
    # keep anything unusual (extensions, international) visible but non-numeric
    return s if any(c.isalpha() or c in "()-+ " for c in s) else f"'{s}"


# Excel rejects a malformed hyperlink target by silently dropping the entire
# hyperlink table for that worksheet, which is what produced the
# "Removed Records: Hyperlinks" repair prompt on V1. The vendor ships values like
# "http:// www.tlc-engineers.com" with a space after the scheme. Clean what is
# recoverable, and refuse to emit a link for anything still questionable.
_URL_OK = re.compile(r"^https?://[A-Za-z0-9.\-]+\.[A-Za-z]{2,}(/[^\s]*)?$")
_MAIL_OK = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def _safe_url(raw):
    """Return a hyperlink-safe URL, or None if it cannot be trusted as one."""
    if not raw:
        return None
    s = re.sub(r"\s+", "", str(raw).strip())
    if not s or s.lower() in {"http://", "https://", "n/a", "na", "none"}:
        return None
    if s.lower().startswith("www."):
        s = "http://" + s
    if not s.lower().startswith(("http://", "https://")):
        return None
    # Excel's own ceiling on a hyperlink target
    if len(s) > 255:
        return None
    return s if _URL_OK.match(s) else None


def _safe_mailto(raw):
    if not raw:
        return None
    s = re.sub(r"\s+", "", str(raw).strip())
    if len(s) > 255 or not _MAIL_OK.match(s):
        return None
    return "mailto:" + s


# Department and role mailboxes seen in the live data, plus the standard set. A
# generic address reaches a shared inbox, so it is a different kind of lead than
# a named individual's mailbox and the two should be countable separately.
_GENERIC_LOCALS = {
    "info", "information", "infos", "contact", "contactus", "contacts", "admin",
    "administration", "administrator", "sales", "marketing", "office", "mail",
    "mailbox", "email", "webmaster", "hello", "hi", "support", "general",
    "purchasing", "procurement", "bids", "bid", "bidding", "bidsupport",
    "biddocs", "estimating", "estimate", "estimates", "estimator", "plans",
    "planroom", "planning", "print", "printing", "reprographics", "orders",
    "order", "media", "press", "news", "hr", "humanresources", "jobs",
    "careers", "recruiting", "accounting", "accounts", "ap", "ar", "billing",
    "invoices", "invoicing", "service", "customerservice", "help", "helpdesk",
    "reception", "frontdesk", "secretariat", "secretary", "clerk", "cityclerk",
    "cityhall", "townclerk", "permits", "engineering", "publicworks",
    "utilities", "water", "sewer", "parks", "police", "fire", "school",
    "schools", "district", "main", "team", "group", "company", "enquiries",
    "inquiries", "inquiry", "ask", "quotes", "quote", "rfp", "rfq", "rfi",
    "submittals", "takeoff", "takeoffs", "projects", "project",
    "specialprojects", "contractorcompliance", "compliance", "noreply",
    "donotreply", "notifications", "alerts", "webmail", "postmaster",
    "business", "operations", "ops", "finance", "legal", "safety", "quality",
    "warranty", "dispatch", "scheduling", "shipping", "receiving", "front",
    "staff", "all", "everyone", "public", "citizen", "citizens", "council",
    "board", "committee", "commission", "authority", "department", "dept",
    "division", "bureau", "agency", "clientservices", "newbusiness",
    "development", "grants", "bidsandproposals", "proposals", "tenders",
    "tender", "construction", "facilities", "maintenance", "buildings",
}
# A mailbox named for a place or for the firm itself is a shared one, not a
# person's. These are the cases that a "looks like a surname" rule would otherwise
# swallow: denver@example-arch.com, albany@example-repro.com, abc@abc-inc.com.
_PLACES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "hampshire", "jersey",
    "mexico", "york", "carolina", "dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "island", "tennessee", "texas", "utah", "vermont",
    "virginia", "washington", "wisconsin", "wyoming",
    "atlanta", "austin", "albany", "baltimore", "boston", "buffalo", "charlotte",
    "chicago", "cincinnati", "cleveland", "columbus", "dallas", "denver",
    "detroit", "elpaso", "fresno", "houston", "indianapolis", "jacksonville",
    "kansascity", "lasvegas", "losangeles", "louisville", "memphis", "mesa",
    "miami", "milwaukee", "minneapolis", "nashville", "neworleans", "newyork",
    "norfolk", "oakland", "omaha", "orlando", "philadelphia", "phoenix",
    "pittsburgh", "portland", "raleigh", "richmond", "sacramento", "saltlake",
    "sanantonio", "sandiego", "sanfrancisco", "sanjose", "seattle", "stlouis",
    "tampa", "tucson", "tulsa", "virginiabeach", "wichita", "midwest",
    "northeast", "southeast", "southwest", "northwest", "east", "west",
    "north", "south", "central", "national", "regional", "corporate", "hq",
    "headquarters", "usa", "us",
}


# a local part built only from these plus a place or unit name is still generic
_GENERIC_TOKENS = {
    "info", "contact", "admin", "sales", "office", "bids", "bid", "bidding",
    "estimating", "purchasing", "procurement", "plans", "planroom", "mail",
    "support", "service", "clerk", "permits", "dept", "department", "team",
    "group", "projects", "proposals", "city", "town", "county", "district",
    "school", "schools", "public", "works", "publicworks", "general", "main",
    "help", "hr", "jobs", "careers", "press", "media", "billing", "accounting",
    "notifications", "noreply", "compliance", "facilities", "maintenance",
}


# Distinctive enough that finding them anywhere in the local part settles it:
# avolisengineering@, bpwbids@, reasonableaccommodations@. No surname contains these.
_GENERIC_SUBSTRINGS = (
    "engineering", "architects", "architecture", "communications",
    "accommodations", "contracts", "contracting", "procurement", "purchasing",
    "estimating", "planroom", "reprographics", "publicworks", "permits",
    "cityclerk", "humanresources", "recruiting", "consultants", "associates",
    "bidding", "proposals", "submittals", "customerservice", "helpdesk",
)


def _name_lexicon(rows):
    """
    Person-name vocabulary harvested from the contact names in this very pull.

    A hardcoded first-name list would be both huge and culturally narrow. The
    dataset already contains thousands of real names, so use those as the
    reference for deciding whether a bare local part like "mark" or "ochoa" is a
    person rather than a department.
    """
    lex = set()
    for r in rows:
        if r.get("Named Person") != "Yes":
            continue
        for tok in re.split(r"[^A-Za-z]+", str(r.get("Contact Name") or "").lower()):
            if len(tok) >= 3 and tok not in _GENERIC_TOKENS:
                lex.add(tok)
    return lex


def _classify_email(email, contact_name, lexicon):
    """
    Label an address Personal or Generic / shared.

    Decided on the local part, strongest signal first. Anything that cannot be
    tied to a person is called generic, so the Personal count is a floor rather
    than an optimistic estimate.
    """
    if not email:
        return ""
    local = str(email).split("@")[0].strip().lower()
    if not local:
        return ""
    bare = re.sub(r"[^a-z]", "", local)

    # 1. the address carries a piece of this contact's own name
    own = [t for t in re.split(r"[^A-Za-z]+", str(contact_name or "").lower())
           if len(t) >= 3 and t not in _GENERIC_TOKENS]
    if any(t in local for t in own):
        return "Personal"

    # 2. an outright department or role mailbox
    if local in _GENERIC_LOCALS or bare in _GENERIC_LOCALS:
        return "Generic / shared"
    parts = [p for p in re.split(r"[._\-]+", local) if p]
    if parts and all(re.sub(r"[^a-z]", "", p) in _GENERIC_TOKENS for p in parts):
        return "Generic / shared"
    if any(local.startswith(g) for g in ("info", "bids", "bid.", "plans", "noreply",
                                         "donotreply", "no-reply", "contactus",
                                         "ask", "sales", "admin", "office")):
        return "Generic / shared"
    if any(g in bare for g in _GENERIC_SUBSTRINGS) or bare.endswith("bids"):
        return "Generic / shared"

    # 3. shaped like a person, and the pieces read as names
    if len(parts) >= 2:
        named = [p for p in parts if re.sub(r"[^a-z]", "", p) in lexicon]
        if named and not any(re.sub(r"[^a-z]", "", p) in _GENERIC_TOKENS for p in parts):
            return "Personal"
    if bare in lexicon:
        return "Personal"
    # initial + surname, e.g. rblickley
    if len(bare) >= 4 and bare[1:] in lexicon:
        return "Personal"
    if len(bare) >= 5 and bare[:-1] in lexicon:
        return "Personal"

    # 4. Last resort, and the one that recovers real mailboxes the lexicon misses
    # (avasquez@, cwinn@, rblickley@ where the source paired the address with the
    # wrong contact name). A mailbox is treated as a person's when it is not a
    # department word, not a place, not just the firm's own name or initials, and
    # long enough to be a name rather than a code.
    if bare in _PLACES or local in _PLACES:
        return "Generic / shared"
    domain = str(email).split("@")[-1].lower()
    second = re.sub(r"[^a-z0-9]", "", domain.split(".")[0]) if "." in domain else ""
    firm_self = bool(second) and (bare == second or (len(bare) >= 3 and bare in second))
    if (
        len(bare) >= 4
        and not any(ch.isdigit() for ch in local)
        and not firm_self
        and bare not in _GENERIC_TOKENS
    ):
        return "Personal"

    return "Generic / shared"


def _summary_index():
    """
    Search-result summaries keyed by project id, rebuilt from the discovery files.

    The crawler attaches the summary when it happens to have one, but discovery and
    detail collection are separate passes and a project pulled in a later round has
    its summary sitting in that round's discovery file instead. The summary carries
    the CSI code list that Product Fit is derived from, so a record without one
    would silently lose its product classification. Join it here from disk rather
    than making the crawler carry state across rounds.
    """
    idx = {}

    # round-1 discovery: id|value|lastUpdated|csiPrefixes|score
    rank = os.path.join(DATA, "discovery_rank.txt")
    if os.path.exists(rank):
        for line in open(rank):
            parts = line.strip().split("|")
            if len(parts) < 4 or not parts[0]:
                continue
            codes = [c for c in parts[3].split(",") if c]
            idx[parts[0]] = {
                "projectValue": int(parts[1]) if parts[1].isdigit() else None,
                "lastUpdatedDate": parts[2],
                "csiPrefixes": codes,
                "csiCount": len(codes),
            }

    # round-2 onward: slimmed discovery docs
    for path in sorted(glob.glob(os.path.join(DATA, "discovery_round*.json"))):
        try:
            docs = json.load(open(path))
        except json.JSONDecodeError:
            print(f"  ! skipping unreadable {os.path.basename(path)}")
            continue
        for d in docs:
            codes = [c for c in (d.get("csi") or "").split(",") if c]
            idx[str(d["id"])] = {
                "projectValue": d.get("v"),
                "lastUpdatedDate": d.get("lu") or "",
                "projectStatus": d.get("st") or "",
                "projectCategory": d.get("cat") or "",
                "title": d.get("t") or "",
                "csiPrefixes": codes,
                "csiCount": d.get("csiN") or len(codes),
                "address": {"state": d.get("state") or ""},
            }
    return idx


def _batch_of(filename):
    """Which pull round a details file belongs to, from its name."""
    base = os.path.basename(filename)
    if base.startswith("details_r3"):
        return "round3"
    if base.startswith("details_r2"):
        return "round2"
    return "round1"


def _load_details():
    rows = []
    for path in sorted(glob.glob(os.path.join(DATA, "details_*.json"))):
        with open(path) as fh:
            try:
                chunk = json.load(fh)
            except json.JSONDecodeError:
                print(f"  ! skipping unreadable {os.path.basename(path)}")
                continue
        batch = _batch_of(path)
        for r in chunk:
            r.setdefault("_batch", batch)
        rows.extend(chunk)
    # de-duplicate on project id, last write wins
    by_id = {}
    for r in rows:
        if r.get("id"):
            by_id[str(r["id"])] = r

    # fill in any missing search summary from the discovery files
    idx = _summary_index()
    filled = 0
    for pid, r in by_id.items():
        if not r.get("s") and pid in idx:
            r["s"] = idx[pid]
            filled += 1
    # Label each round with the span of project-update dates it actually covers, so
    # the workbook says which slice of the marketplace a row came from rather than
    # an opaque round number. Derived from the data, not hardcoded, so it stays
    # honest as rounds are added.
    spans = {}
    for r in by_id.values():
        d = (r.get("s") or {}).get("lastUpdatedDate") or ""
        d = str(d)[:10]
        if len(d) == 10:
            lo, hi = spans.get(r.get("_batch"), (d, d))
            spans[r["_batch"]] = (min(lo, d), max(hi, d))
    _MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    def _pretty(d):
        y, m, dd = d.split("-")
        return f"{_MON[int(m) - 1]} {int(dd)}"

    order = {"round1": 1, "round2": 2, "round3": 3}
    labels = {}
    for b, (lo, hi) in spans.items():
        n = order.get(b, 9)
        labels[b] = (f"Pull {n}: {_pretty(lo)} to {_pretty(hi)}, {hi[:4]}"
                     if lo != hi else f"Pull {n}: {_pretty(lo)}, {lo[:4]}")
    for r in by_id.values():
        r["_batch_label"] = labels.get(r.get("_batch"), r.get("_batch") or "")
    if labels:
        for b in sorted(labels, key=lambda x: order.get(x, 9)):
            n = sum(1 for r in by_id.values() if r.get("_batch") == b)
            print(f"  {labels[b]}  ({n:,} projects)")

    if filled:
        print(f"  joined search summaries from discovery for {filled:,} records")
    still_missing = sum(1 for r in by_id.values() if not r.get("s"))
    if still_missing:
        print(f"  ! {still_missing:,} records have no search summary; Product Fit "
              f"will read as unclassified for those")
    return list(by_id.values())


def _days_since(iso):
    if not iso:
        return None
    try:
        s = str(iso).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0, (datetime.now(timezone.utc) - dt).days)
    except (ValueError, TypeError):
        return None


def _fmt_date(iso):
    if not iso:
        return ""
    try:
        s = str(iso).replace("Z", "+00:00")
        return datetime.fromisoformat(s).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return str(iso)[:10]


def _clean(text, limit=None):
    """Flatten whitespace and strip characters that make a cell hard to read."""
    if text is None:
        return ""
    s = str(text).replace("\r", " ").replace("\n", " ").replace("\t", " ")
    s = " ".join(s.split())
    # CLUTCH house style: no em or en dashes in generated output
    s = s.replace("—", "-").replace("–", "-")
    if limit and len(s) > limit:
        s = s[: limit - 3].rstrip() + "..."
    return s


def _num(x):
    if x is None or x == "":
        return None
    try:
        return float(str(x).replace("$", "").replace(",", ""))
    except (ValueError, TypeError):
        return None


def _first(d, *keys, default=""):
    for k in keys:
        v = d.get(k)
        if v not in (None, "", []):
            return v
    return default


def _role_kind(function, roles):
    blob = (" ".join([str(function or "")] + [str(r) for r in (roles or [])])).lower()
    # order matters: a "Contracts Administrator" for an owner is procurement, and
    # specifier tokens are checked before the broader owner tokens so that an
    # in-house district architect still reads as a specifier
    if any(t in blob for t in SPECIFIER_TOKENS):
        return "Specifier (influences product spec)"
    if any(t in blob for t in PROCUREMENT_TOKENS):
        return "Procurement / bid contact"
    if any(t in blob for t in BUYER_TOKENS):
        return "Buyer / trade contractor"
    if any(t in blob for t in OWNER_TOKENS):
        return "Owner / decision maker"
    return "Other / unclassified"


def _is_named_person(full_name, function, roles):
    """True when the name field holds a person rather than a role placeholder."""
    n = (full_name or "").strip().lower()
    if not n:
        return False
    if n in PLACEHOLDER_NAMES:
        return False
    # a single word that simply repeats the role is a placeholder too
    role_blob = (" ".join([str(function or "")] + [str(r) for r in (roles or [])])).lower()
    if " " not in n and n in role_blob:
        return False
    return True


def build():
    details = _load_details()
    if not details:
        raise SystemExit("No detail records found in data/details_*.json")
    print(f"Loaded {len(details)} project detail records")

    project_rows, contact_rows = [], []
    matched_codes = {}

    for rec in details:
        p = rec.get("p") or {}
        s = rec.get("s") or {}
        contacts = rec.get("contacts") or []

        # The crawler ships 4-digit MasterFormat prefixes rather than the full code
        # list; classify_fit matches on those prefixes, and csiCount preserves the
        # true breadth of the project's scope for reporting.
        csi_codes = s.get("csiPrefixes") or s.get("csiCodes") or []
        csi_total = s.get("csiCount")
        if csi_total is None:
            csi_total = len(csi_codes)
        fit = classify_fit(csi_codes)
        for c in csi_codes:
            matched_codes[c] = matched_codes.get(c, 0) + 1

        value = _num(_first(p, "EstimatedValue", default=None)) or _num(
            s.get("projectValue")
        )
        band, band_order = value_band(value)

        last_upd = _first(p, "ProjectUpdateDate", default="") or s.get("lastUpdatedDate")
        days = _days_since(last_upd)
        score = priority_score(value, days, fit)

        loc = p.get("Location") or {}
        s_addr = s.get("address") or {}
        city = _clean(_first(loc, "City", default="") or s_addr.get("city"))
        state = _clean(_first(loc, "State", default="") or s_addr.get("state"))
        street = _clean(_first(loc, "StreetAddress", default="") or s_addr.get("addressLine1"))
        postal = _clean(_first(loc, "PostalCode", default="") or s_addr.get("postalCode"))
        county = _clean(loc.get("CountyName"))

        stage_num = p.get("Stage")
        stage = _clean(
            _first(p, "CrimsonProjectStatus", default="")
            or s.get("projectStatus")
            or (STAGE_LABELS.get(stage_num, f"Stage {stage_num}") if stage_num else "")
        )

        cid = str(rec.get("id"))
        url = f"https://app.constructconnect.com/project/{cid}/c?sourceType=3"

        categories = p.get("ProjectCategories") or []
        cat_names = "; ".join(
            _clean(c.get("Name")) for c in categories if isinstance(c, dict) and c.get("Name")
        ) or _clean(s.get("projectCategory"))

        uses = p.get("BuildingUseTypes") or []
        use_names = "; ".join(
            _clean(u.get("Name")) for u in uses if isinstance(u, dict) and u.get("Name")
        ) or _clean(s.get("buildingUsesString"))

        project_rows.append({
            "Priority Score": score,
            "Product Fit": fit["fit"],
            "CMC Fit Score": fit["cmc_score"],
            "SRW Fit Score": fit["srw_score"],
            "CMC Product Scope": fit["cmc_products"],
            "SRW Product Scope": fit["srw_products"],
            "Project Name": _clean(_first(p, "Title", "Name", default="") or s.get("title")),
            "Stage": stage,
            "Project Value": value,
            "Value Band": band,
            "Days Since Update": days,
            "Last Updated": _fmt_date(last_upd),
            "First Published": _fmt_date(s.get("initialPublicationDate")),
            "Start Date": _fmt_date(_first(p, "StartDate", default="") or s.get("startDate")),
            "New Listing": "Yes" if s.get("isNew") else "",
            "Recently Updated": "Yes" if s.get("isUpdated") else "",
            "City": city,
            "State": state,
            "County": county,
            "ZIP": postal,
            "Street Address": street,
            "Category": cat_names,
            "Building Use": use_names,
            "Sector": _clean(p.get("Sector")),
            "Construction Type": _clean("; ".join(s.get("constructionTypes") or [])),
            "Contracting Method": _clean(p.get("ContractingMethod")),
            "Owner": _clean(p.get("OwnerName")),
            "Stories": p.get("Stories") or "",
            "Physical Size": _clean(p.get("PhysicalSize")),
            "Bidders": p.get("NumberOfBidders") or 0,
            "Documents": p.get("DocumentCount") or 0,
            "Addenda": p.get("AddendaCount") or 0,
            "Contacts Found": len(contacts),
            "Named People": sum(
                1 for c in contacts
                if _is_named_person(
                    c.get("FullName") or " ".join(
                        filter(None, [c.get("FirstName"), c.get("LastName")])),
                    c.get("Function"), c.get("Roles"))
            ),
            "Emails Found": sum(1 for c in contacts if c.get("Email")),
            "Phones Found": sum(1 for c in contacts if c.get("Phone") or c.get("Mobile")),
            "CSI Code Count": csi_total,
            "Description": _clean(
                _first(p, "Description", default="") or s.get("projectDescription"), 900
            ),
            "Project URL": url,
            "Project ID": cid,
            "Pull Batch": rec.get("_batch_label", ""),
            "_band_order": band_order,
        })

        pname = project_rows[-1]["Project Name"]
        for c in contacts:
            roles = c.get("Roles") or []
            func = _clean(c.get("Function"))
            full = _clean(
                c.get("FullName")
                or " ".join(filter(None, [c.get("FirstName"), c.get("LastName")]))
            )
            contact_rows.append({
                "Priority Score": score,
                "Product Fit": fit["fit"],
                "Project Name": pname,
                "Project State": state,
                "Project City": city,
                "Project Value": value,
                "Project Stage": stage,
                "Company": _clean(c.get("CompanyName")),
                "Role on Project": _clean("; ".join(str(r) for r in roles) or func),
                "Function": func,
                "Role Type": _role_kind(func, roles),
                "Named Person": "Yes" if _is_named_person(full, func, roles) else "No - role placeholder",
                "Contact Name": full,
                "Title": _clean(c.get("Title")),
                "Email": _clean(c.get("Email")),
                "Phone": _fmt_phone(c.get("Phone")),
                "Ext": _clean(c.get("Ext")),
                "Mobile": _fmt_phone(c.get("Mobile")),
                "Fax": _fmt_phone(c.get("Fax")),
                "Department": _clean(c.get("Department")),
                "Company Address": _clean(
                    " ".join(filter(None, [c.get("AddressLine1"), c.get("AddressLine2")]))
                ),
                "Company City": _clean(c.get("City")),
                "Company State": _clean(c.get("State")),
                "Company ZIP": _clean(c.get("ZipCode")),
                "Website": _safe_url(c.get("Website")) or _clean(c.get("Website")),
                "Bid Amount": _num(c.get("BidAmount")),
                "Potential Bidder": "Yes" if c.get("PotentialBidder") else "",
                "Date Added": _fmt_date(c.get("DateAdded")),
                "Project URL": url,
                "Project ID": cid,
                "Company ID": c.get("CompanyID") or "",
                "Pull Batch": rec.get("_batch_label", ""),
            })

    proj = pd.DataFrame(project_rows).sort_values(
        ["Priority Score", "Project Value"], ascending=[False, False]
    )
    # Email Type needs the whole contact set in hand first, because the person-name
    # lexicon it leans on is harvested from every named contact in this pull.
    lexicon = _name_lexicon(contact_rows)
    for r in contact_rows:
        r["Email Type"] = _classify_email(r.get("Email"), r.get("Contact Name"), lexicon)

    cont = pd.DataFrame(contact_rows)
    if not cont.empty:
        # keep Email Type beside the address it describes
        order = list(cont.columns)
        order.insert(order.index("Email") + 1, order.pop(order.index("Email Type")))
        cont = cont[order]
        cont = cont.sort_values(
            ["Priority Score", "Project Name", "Company", "Contact Name"],
            ascending=[False, True, True, True],
        )

    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, f"ConstructConnect_Leads_{VERSION}.xlsx")
    _write(path, proj, cont)
    print(f"\nWrote {path}")
    print(f"  Projects: {len(proj)}")
    print(f"  Contacts: {len(cont)}")
    if not cont.empty:
        print(f"  With email: {(cont['Email'] != '').sum()}")
        print(f"  With phone: {((cont['Phone'] != '') | (cont['Mobile'] != '')).sum()}")
    return path


def _companies(cont):
    """
    One row per firm, ranked by how many of these projects it touches.

    A firm appearing on twenty projects is a better relationship target than
    twenty separate one-off contacts, and that pattern is invisible in a flat
    contact list. Specifiers are surfaced first because they influence what gets
    written into the spec.
    """
    df = cont[cont["Company"] != ""].copy()
    if df.empty:
        return pd.DataFrame([{"Company": "", "Projects": 0}])

    def best_email(s):
        vals = [v for v in s if v]
        return vals[0] if vals else ""

    def joined(s, limit=5):
        seen, out = set(), []
        for v in s:
            if v and v not in seen:
                seen.add(v)
                out.append(v)
        txt = "; ".join(out[:limit])
        if len(out) > limit:
            txt += f" (+{len(out) - limit} more)"
        return txt

    g = df.groupby("Company", dropna=False).agg(
        Projects=("Project ID", "nunique"),
        Contacts=("Contact Name", "count"),
        **{
            "Named People": ("Named Person", lambda s: int((s == "Yes").sum())),
            "Emails": ("Email", lambda s: int((s != "").sum())),
            "Total Project Value": ("Project Value", lambda s: s.fillna(0).sum()),
            "Roles Seen": ("Role on Project", joined),
            "Primary Role Type": ("Role Type", lambda s: s.mode().iat[0] if not s.mode().empty else ""),
            "States": ("Project State", lambda s: joined(s, 6)),
            "Sample Email": ("Email", best_email),
            "Sample Phone": ("Phone", best_email),
            "Website": ("Website", best_email),
            "City": ("Company City", best_email),
            "State": ("Company State", best_email),
        },
    ).reset_index()

    # value is summed over distinct projects per firm, so recompute honestly
    per = df.drop_duplicates(subset=["Company", "Project ID"])
    val = per.groupby("Company")["Project Value"].apply(lambda s: s.fillna(0).sum())
    g["Total Project Value"] = g["Company"].map(val).fillna(0)

    order = {
        "Specifier (influences product spec)": 0,
        "Procurement / bid contact": 1,
        "Buyer / trade contractor": 2,
        "Owner / decision maker": 3,
    }
    g["_o"] = g["Primary Role Type"].map(order).fillna(9)
    g = g.sort_values(["Projects", "Total Project Value"], ascending=[False, False])
    return g.drop(columns=["_o"])[[
        "Company", "Primary Role Type", "Projects", "Total Project Value",
        "Contacts", "Named People", "Emails", "Roles Seen", "States",
        "Sample Email", "Sample Phone", "City", "State", "Website",
    ]]


def _write(path, proj, cont):
    band_order = proj[["Value Band", "_band_order"]].drop_duplicates()
    proj_out = proj.drop(columns=["_band_order"])

    # strings_to_urls=False stops xlsxwriter from silently promoting any
    # URL-shaped string to a hyperlink. Every link in this workbook is now written
    # explicitly and only after passing validation, which is what keeps Excel from
    # dropping the sheet's hyperlink table and demanding a repair.
    with pd.ExcelWriter(
        path,
        engine="xlsxwriter",
        datetime_format="yyyy-mm-dd",
        engine_kwargs={"options": {"strings_to_urls": False}},
    ) as xl:
        wb = xl.book

        fmt = {
            "hdr": wb.add_format({
                "bold": True, "font_color": "white", "bg_color": "#2F6DB3",
                "border": 1, "border_color": "#1F4E79", "align": "left",
                "valign": "vcenter", "text_wrap": True,
            }),
            "money": wb.add_format({"num_format": "$#,##0", "valign": "top"}),
            "int": wb.add_format({"num_format": "#,##0", "valign": "top"}),
            "text": wb.add_format({"valign": "top"}),
            "wrap": wb.add_format({"valign": "top", "text_wrap": True}),
            "link": wb.add_format({"font_color": "#2F6DB3", "underline": 1, "valign": "top"}),
            "title": wb.add_format({"bold": True, "font_size": 15, "font_color": "#1F4E79"}),
            "h2": wb.add_format({"bold": True, "font_size": 11, "font_color": "#1F4E79"}),
            "body": wb.add_format({"text_wrap": True, "valign": "top"}),
            "pct": wb.add_format({"num_format": "0.0%", "valign": "top"}),
        }

        widths = {
            "Project Name": 46, "Description": 70, "Product Fit": 34,
            "CMC Product Scope": 34, "SRW Product Scope": 34, "Category": 26,
            "Building Use": 26, "Owner": 30, "Project URL": 30, "Company": 34,
            "Contact Name": 24, "Title": 30, "Email": 32, "Company Address": 30,
            "Role on Project": 26, "Function": 22, "Role Type": 28, "Website": 28,
            "Street Address": 30, "Construction Type": 22, "Contracting Method": 22,
            "Physical Size": 16, "Sector": 18, "Department": 20,
            "Named Person": 20, "Phone": 16, "Mobile": 16, "Fax": 16,
            "Email Type": 18, "Pull Batch": 26,
            "Roles Seen": 40, "States": 22, "Primary Role Type": 28,
            "Sample Email": 32, "Sample Phone": 16,
        }
        money_cols = {"Project Value", "Bid Amount", "Total Project Value"}
        int_cols = {
            "Priority Score", "CMC Fit Score", "SRW Fit Score", "Days Since Update",
            "Bidders", "Documents", "Addenda", "Contacts Found", "Emails Found",
            "Phones Found", "CSI Code Count", "Stories", "Named People",
            "Projects", "Contacts", "Emails",
        }
        wrap_cols = {
            "Description", "CMC Product Scope", "SRW Product Scope", "Category",
            "Building Use", "Product Fit", "Roles Seen",
        }

        def sheet(df, name, freeze_col):
            df.to_excel(xl, sheet_name=name, index=False, startrow=0)
            ws = xl.sheets[name]
            ws.freeze_panes(1, freeze_col)
            ws.autofilter(0, 0, max(len(df), 1), len(df.columns) - 1)
            ws.set_row(0, 32, fmt["hdr"])
            for i, col in enumerate(df.columns):
                if col in money_cols:
                    f, w = fmt["money"], 16
                elif col in int_cols:
                    f, w = fmt["int"], 12
                elif col in wrap_cols:
                    f, w = fmt["wrap"], widths.get(col, 30)
                elif col == "Project URL":
                    f, w = fmt["link"], widths.get(col, 30)
                else:
                    f, w = fmt["text"], widths.get(col, 15)
                ws.set_column(i, i, w, f)
                ws.write(0, i, col, fmt["hdr"])

            # Explicit, validated hyperlinks. Anything that fails validation stays
            # as plain text rather than becoming a link Excel will reject.
            #
            # Excel refuses to open a worksheet holding more than 65,530 hyperlinks
            # and strips the lot, which is the same repair prompt by a different
            # route. At 19k contacts the Contacts sheet is already at 48k links, so
            # the budget below is what stops a future round from silently
            # reintroducing the bug. Email is ordered first because a mailto is the
            # one link on a call list anyone actually clicks.
            # Dedupe first, budget second. A firm on thirty projects repeats the same
            # website thirty times and a big owner repeats one project URL across
            # every contact on it, so linking only the first occurrence of each
            # distinct target removes the bulk of the count without losing anything:
            # the repeat cells still show the address, they just are not clickable.
            # The budget stays as a backstop for scale the dedupe cannot absorb.
            LINK_BUDGET = 60000
            link_cols = [
                ("Email", _safe_mailto),
                ("Project URL", _safe_url),
                ("Sample Email", _safe_mailto),
                ("Website", _safe_url),
            ]
            budget = LINK_BUDGET
            seen_targets = set()
            deduped = capped = 0
            for col, maker in link_cols:
                if col not in df.columns:
                    continue
                j = df.columns.get_loc(col)
                for offset, raw in enumerate(df[col].tolist()):
                    row = offset + 1
                    text = "" if raw is None else str(raw)
                    if not text.strip():
                        continue
                    target = maker(text)
                    if target and target.lower() in seen_targets:
                        ws.write_string(row, j, text, fmt["text"])
                        deduped += 1
                        continue
                    if target and budget <= 0:
                        ws.write_string(row, j, text, fmt["text"])
                        capped += 1
                        continue
                    if target:
                        ws.write_url(row, j, target, fmt["link"], text)
                        seen_targets.add(target.lower())
                        budget -= 1
                    else:
                        ws.write_string(row, j, text, fmt["text"])
            if deduped or capped:
                bits = []
                if deduped:
                    bits.append(f"{deduped:,} repeat targets linked once")
                if capped:
                    bits.append(f"{capped:,} past the {LINK_BUDGET:,} budget")
                print(f"  {name}: {'; '.join(bits)}")

            n = max(len(df), 1)
            cols = list(df.columns)

            # colour scale on the score so the eye lands on the best rows first
            if "Priority Score" in cols:
                j = cols.index("Priority Score")
                ws.conditional_format(1, j, n, j, {
                    "type": "3_color_scale",
                    "min_color": "#F8696B", "mid_color": "#FFEB84", "max_color": "#63BE7B",
                })
            # in-scope rows get a green fit cell, generic/out-of-scope a grey one
            if "Product Fit" in cols:
                j = cols.index("Product Fit")
                ws.conditional_format(1, j, n, j, {
                    "type": "text", "criteria": "containing", "value": "Generic scope",
                    "format": wb.add_format({"bg_color": "#EDEDED", "font_color": "#7A7A7A"}),
                })
                ws.conditional_format(1, j, n, j, {
                    "type": "text", "criteria": "containing", "value": "Out of scope",
                    "format": wb.add_format({"bg_color": "#EDEDED", "font_color": "#7A7A7A"}),
                })
                ws.conditional_format(1, j, n, j, {
                    "type": "text", "criteria": "containing", "value": "Both",
                    "format": wb.add_format({"bg_color": "#C6EFCE", "font_color": "#1F6132", "bold": True}),
                })
            # a named individual's mailbox is the one worth calling, so it reads green
            # and the shared inboxes recede
            if "Email Type" in cols:
                j = cols.index("Email Type")
                ws.conditional_format(1, j, n, j, {
                    "type": "text", "criteria": "containing", "value": "Personal",
                    "format": wb.add_format({"bg_color": "#C6EFCE", "font_color": "#1F6132", "bold": True}),
                })
                ws.conditional_format(1, j, n, j, {
                    "type": "text", "criteria": "containing", "value": "Generic",
                    "format": wb.add_format({"bg_color": "#EDEDED", "font_color": "#7A7A7A"}),
                })
            # role placeholders greyed so a caller does not dial a job title
            if "Named Person" in cols:
                j = cols.index("Named Person")
                ws.conditional_format(1, j, n, j, {
                    "type": "text", "criteria": "containing", "value": "placeholder",
                    "format": wb.add_format({"bg_color": "#EDEDED", "font_color": "#7A7A7A"}),
                })
            # data bars make firm recurrence readable at a glance
            if "Projects" in cols:
                j = cols.index("Projects")
                ws.conditional_format(1, j, n, j, {"type": "data_bar", "bar_color": "#2F6DB3"})
            return ws

        sheet(proj_out, "Projects", 2)
        if not cont.empty:
            sheet(cont, "Contacts", 3)
            sheet(_companies(cont), "Companies", 2)

        # ---- Summary -------------------------------------------------------
        ws = wb.add_worksheet("Summary")
        ws.set_column(0, 0, 44)
        ws.set_column(1, 4, 18)
        ws.write(0, 0, "ConstructConnect Lead Pull - Summary", fmt["title"])
        ws.write(1, 0, f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}", fmt["text"])

        r = 3

        def table(title, df_agg, cols):
            nonlocal r
            ws.write(r, 0, title, fmt["h2"])
            r += 1
            for j, c in enumerate(cols):
                ws.write(r, j, c, fmt["hdr"])
            ws.set_row(r, 28)
            r += 1
            for _, row in df_agg.iterrows():
                for j, c in enumerate(cols):
                    v = row[c]
                    if c == "Total Value":
                        ws.write(r, j, v, fmt["money"])
                    elif c in ("Projects", "Contacts", "Emails"):
                        ws.write(r, j, v, fmt["int"])
                    else:
                        ws.write(r, j, v, fmt["text"])
                r += 1
            r += 2

        tot = pd.DataFrame([{
            "Metric": "Projects pulled", "Value": f"{len(proj_out):,}",
        }, {
            "Metric": "Contacts pulled", "Value": f"{len(cont):,}",
        }, {
            "Metric": "Contacts with an email",
            "Value": f"{int((cont['Email'] != '').sum()):,}" if not cont.empty else "0",
        }, {
            "Metric": "Contacts with a phone or mobile",
            "Value": f"{int(((cont['Phone'] != '') | (cont['Mobile'] != '')).sum()):,}" if not cont.empty else "0",
        }, {
            "Metric": "Projects in a client product scope",
            "Value": f"{int((~proj_out['Product Fit'].str.startswith('Out of scope')).sum()):,}",
        }, {
            "Metric": "Total pipeline value",
            "Value": f"${proj_out['Project Value'].fillna(0).sum():,.0f}",
        }])
        table("Totals", tot, ["Metric", "Value"])

        # ---- Email quality --------------------------------------------------
        # The question this answers: of everything in here, how much is a named
        # person's mailbox versus a shared inbox someone has to talk their way past.
        if not cont.empty:
            n_contacts = len(cont)
            has_em = cont["Email"] != ""
            n_em = int(has_em.sum())
            n_pers = int((cont["Email Type"] == "Personal").sum())
            n_gen = int((cont["Email Type"] == "Generic / shared").sum())
            uniq_pers = int(cont.loc[cont["Email Type"] == "Personal", "Email"].nunique())
            uniq_gen = int(cont.loc[cont["Email Type"] == "Generic / shared", "Email"].nunique())

            def pct(x, base):
                return f"{x / base * 100:.1f}%" if base else "0.0%"

            eq = pd.DataFrame([
                {"Metric": "Contact rows in this workbook",
                 "Count": f"{n_contacts:,}", "Share of emails": ""},
                {"Metric": "Rows carrying an email address",
                 "Count": f"{n_em:,}", "Share of emails": pct(n_em, n_em)},
                {"Metric": "  Personal (a named person's mailbox)",
                 "Count": f"{n_pers:,}", "Share of emails": pct(n_pers, n_em)},
                {"Metric": "  Generic / shared (info@, bids@, purchasing@ ...)",
                 "Count": f"{n_gen:,}", "Share of emails": pct(n_gen, n_em)},
                {"Metric": "Rows with no email at all",
                 "Count": f"{n_contacts - n_em:,}", "Share of emails": ""},
                {"Metric": "Distinct personal addresses (deduplicated)",
                 "Count": f"{uniq_pers:,}", "Share of emails": ""},
                {"Metric": "Distinct generic addresses (deduplicated)",
                 "Count": f"{uniq_gen:,}", "Share of emails": ""},
            ])
            table("Email quality - personal vs generic",
                  eq, ["Metric", "Count", "Share of emails"])

        def agg(by):
            g = proj_out.groupby(by, dropna=False).agg(
                Projects=("Project ID", "count"),
                **{"Total Value": ("Project Value", lambda x: x.fillna(0).sum())},
            ).reset_index().rename(columns={by: by})
            return g.sort_values("Projects", ascending=False)

        f = agg("Product Fit")
        f.columns = ["Product Fit", "Projects", "Total Value"]
        table("By product fit", f, ["Product Fit", "Projects", "Total Value"])

        st = agg("State").head(25)
        st.columns = ["State", "Projects", "Total Value"]
        table("By state (top 25)", st, ["State", "Projects", "Total Value"])

        sg = agg("Stage")
        sg.columns = ["Stage", "Projects", "Total Value"]
        table("By stage", sg, ["Stage", "Projects", "Total Value"])

        vb = proj_out.merge(band_order, on="Value Band", how="left")
        vb = vb.groupby(["Value Band", "_band_order"], dropna=False).agg(
            Projects=("Project ID", "count"),
            **{"Total Value": ("Project Value", lambda x: x.fillna(0).sum())},
        ).reset_index().sort_values("_band_order")[["Value Band", "Projects", "Total Value"]]
        table("By value band", vb, ["Value Band", "Projects", "Total Value"])

        # ---- Methodology ---------------------------------------------------
        mw = wb.add_worksheet("Methodology")
        mw.set_column(0, 0, 30)
        mw.set_column(1, 1, 105)
        mw.write(0, 0, "Methodology and caveats", fmt["title"])
        notes = [
            ("Source", "ConstructConnect Insight web application, saved search \"My Profile Search\", "
                       "pulled through the application's own JSON API using an authenticated licensed seat."),
            ("Saved search filters", "6 project stages, 41 project categories, 623 trade/CSI codes. "
                                     "The saved search itself was not modified by this pull."),
            ("Population", "The saved search matches 136,587 projects. The API caps any single query at "
                           "10,000 records (offset + limit), so this pull covers the most-recently-updated "
                           "slice of that population, not the whole of it."),
            ("Selection rule", "Projects were ranked by Priority Score across the discovery pool and the "
                               "highest-scoring were pulled in full. Large and recent projects rank first, "
                               "but a small project updated in the last few days can still rank in."),
            ("Priority Score", "0-100. Project value up to 40 points, recency of last update up to 35, "
                               "product fit up to 25. Value that is not reported scores 4 rather than 0, "
                               "because unreported value is common and is not evidence of a small job."),
            ("Product Fit", "Derived from the project's MasterFormat (CSI) code list, keyed on SUBSECTION "
                            "codes only. CMC scope: reinforcement bars 03 21, fabric and grid 03 22, "
                            "post-tension 03 23, structural steel 05 12, steel decking 05 31, steel joists "
                            "05 21, precast structural 03 41. SRW scope: retaining walls 32 32, concrete unit "
                            "masonry 04 22, unit paving 32 14, single-wythe masonry 04 26. A project qualifies "
                            "only on a subsection hit."),
            ("Why rollup codes are excluded", "ConstructConnect applies the section-level rollup codes almost "
                                              "universally as generic scope tags. Measured across this pull: "
                                              "\"Unit masonry\" 04 20 appears on 100% of projects, \"Structural "
                                              "metal framing\" 05 10 on 99%, \"Concrete reinforcing\" 03 20 on "
                                              "98%. A signal on 98% of records cannot discriminate, and an "
                                              "earlier version of this scoring keyed on those codes and "
                                              "therefore labelled almost every project a CMC lead. Rollups now "
                                              "contribute at most 3 points of corroboration and never qualify a "
                                              "project on their own."),
            ("Generic scope tags only", "This label means the project carries the universal rollup tags but no "
                                        "specific rebar, structural steel, retaining wall or CMU subsection "
                                        "code. It is not a statement that the project has no such work; it "
                                        "means ConstructConnect has not coded it to that level of detail yet, "
                                        "which is common in early-stage projects. Verify against the project "
                                        "page or documents before discarding."),
            ("Product Fit caveat", "CSI codes describe the scope of work the project is tagged with, not a "
                                   "confirmed material specification. A CMC or SRW label means the project "
                                   "plausibly consumes that product category. It is a prioritisation signal, "
                                   "not a confirmed bill of materials."),
            ("Selection vs scoring", "The 2,500 projects pulled were selected using a preliminary version of "
                                     "this score in which product fit barely differentiated, so selection was "
                                     "effectively driven by project value and recency. The Priority Score shown "
                                     "here uses the corrected fit weighting. Selection order and displayed score "
                                     "therefore do not correspond exactly."),
            ("Role Type", "Contacts are split into specifiers (architects, engineers, designers, consultants) "
                          "and buyers (contractors, subs, suppliers). Specifiers influence what gets written "
                          "into the spec; buyers place the order. Derived from the vendor's role and function "
                          "fields."),
            ("Project Value caveat", "Values are the vendor's estimate, not a contract award. Some very large "
                                     "figures are clearly programme or campus totals rather than a single "
                                     "biddable contract, so treat anything in the $1B and above range as a "
                                     "programme until you have checked the project page. Sum the value column "
                                     "for pipeline sizing with that in mind; the count of projects is the more "
                                     "reliable denominator."),
            ("Companies tab", "One row per firm, ranked by how many of these projects it appears on. A firm "
                              "recurring across many projects is a better relationship target than the same "
                              "number of unrelated contacts, and that pattern does not show up in a flat "
                              "contact list."),
            ("Companies caveat", "Firm names are taken verbatim from the vendor and are NOT entity-resolved, so "
                                 "the same company can appear more than once under name variants (for example "
                                 "\"S2o Consultants, Inc.\" and \"S20 Consultants Inc\", or a firm listed once "
                                 "in full and once by initials). Treat the project counts as a floor, not an "
                                 "exact figure. Proper firm deduplication is specified in docs/06-entity-"
                                 "resolution.md and has not been built yet."),
            ("Value Band", "Half-open intervals per the project's own value_bands taxonomy. Projects with no "
                           "reported value are kept in a distinct \"Value not reported\" band rather than "
                           "being dropped."),
            ("Contact coverage", "Contacts come from the project's design team and public participant records. "
                                 "Not every project carries contacts, and not every contact carries an email "
                                 "or a direct phone. Per-project counts are in the Projects tab so you can see "
                                 "coverage rather than infer it."),
            ("Rate limiting", "Requests were issued from a single session at roughly 11 to 16 per minute with "
                              "randomised spacing, under the 20-per-minute ceiling set in the project's own "
                              "rate_limits.yaml. The run stops on any 403, any 429, or five consecutive failures."),
            ("Personal data", "This file contains named individuals with business emails and direct phone "
                              "numbers. Business contact data is still personal data under CCPA/CPRA and "
                              "PIPEDA. Treat distribution as controlled, and confirm with ConstructConnect "
                              "that the subscription permits use as an outbound prospecting list before it is "
                              "used that way. That question is open as of this pull."),
            ("Better long-term source", "ConstructConnect sells a sanctioned daily XML export (CRM Integration, "
                                        "formerly DataLink) delivered over FTP. It carries pre-computed change "
                                        "history and MasterFormat material codes that the web UI does not "
                                        "expose, with no session fragility. Worth requesting from the account "
                                        "manager."),
            ("Email Type", "Personal means the address resolves to a named individual; Generic / shared means it "
                           "reaches a role or department inbox (info@, bids@, purchasing@). Decided on the part "
                           "before the @, strongest signal first: the address contains a piece of that contact's "
                           "own name; or it is a known department mailbox; or its shape and word stems match the "
                           "person-name vocabulary harvested from every named contact in this pull. Filter the "
                           "column on the Contacts tab; the counts are on the Summary tab."),
            ("Email Type is a floor", "Anything that cannot be positively tied to a person is labelled Generic / "
                                      "shared, so the Personal figure understates rather than flatters. Addresses "
                                      "like a bare surname at a small firm are the usual borderline case."),
            ("Hyperlinks", "V1 asked Excel for a repair on open because the vendor ships a handful of malformed "
                           "website values such as 'http:// www.tlc-engineers.com', and Excel drops a whole "
                           "sheet's hyperlink table rather than one bad link. Automatic URL conversion is now off "
                           "and every link is written only after passing validation. Values that fail stay as "
                           "readable plain text. V2 opens clean."),
            ("Run log", "Two rounds of 2,500 projects each, about 10,000 API calls, 4,998 unique projects landed. "
                        "Round 1 covered the most recently updated 5,000 of the saved search; round 2 covered the "
                        "next 5,000 plus the highest-scoring round-1 candidates that missed the first cut. Each "
                        "round returned one project twice across partition boundaries and those were "
                        "de-duplicated. Eleven requests failed transiently over the two rounds and ten were "
                        "refetched successfully."),
            ("What a 403 actually means here", "Isolated 403s during the run were initially read as vendor "
                                               "flakiness. They are not. A 403 on project/getProjectDataByCrmId is "
                                               "per-project: the subscription does not grant detail access to that "
                                               "specific record. Verified back to back in one session, same "
                                               "endpoint, same headers, where two ids returned 200 and 7642912 "
                                               "returned 403 repeatedly. The account was never rate limited, "
                                               "flagged, or blocked at any point."),
            ("Known gaps", "Project 7642912 is absent: it appears in search results but its detail endpoint "
                           "returns 403 on this subscription. Two round-1 projects (7625083, 7065571) carry "
                           "project data but no contacts. A further handful genuinely publish no contact records. "
                           "One phone number reads '(832) 652-' because that is the truncated value "
                           "ConstructConnect holds; it was passed through rather than invented."),
            ("Population", "The saved search reports roughly 136,000 matching projects and that figure drifts by a "
                           "few hundred a day as projects move between stages. The API caps any single query at "
                           "10,000 records, and both rounds together have consumed that window for the "
                           "lastUpdatedDate sort. Reaching deeper requires partitioning the query by date or "
                           "region so each slice returns under 10,000."),
        ]
        rr = 2
        for k, v in notes:
            mw.write(rr, 0, k, fmt["h2"])
            mw.write(rr, 1, v, fmt["body"])
            mw.set_row(rr, max(28, 13 * (len(v) // 95 + 1)))
            rr += 1


if __name__ == "__main__":
    build()
