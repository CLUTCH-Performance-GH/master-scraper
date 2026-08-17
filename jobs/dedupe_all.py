"""Global de-duplication across all company rosters.

Two dup types (both real, seen in V3):
  1. within-company same person via slug-variant LinkedIn URLs / repeat snippets
     -> merge into the single richest record.
  2. cross-company: one LinkedIn URL in 2+ companies because the person's profile
     names a PAST employer. -> keep them only at their CURRENT employer, decided
     by re-reading their LinkedIn headline (Serper), and drop the rest.

Rewrites output/contacts/<key>.json in place. Run: PYTHONPATH=. python3 jobs/dedupe_all.py
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from msc.net import Serper
from jobs.run_company import COMPANIES

CONTACTS = Path("output/contacts")
_nk = lambda s: re.sub(r"[^a-z]", "", (s or "").lower())


def score(r):
    return ((4 if str(r.get("email_confidence", "")).startswith("verified") else
             1 if r.get("email") else 0)
            + (2 if r.get("state_confidence") == "confirmed" else 0)
            + (1 if r.get("phone") else 0)
            + len(r.get("title", "")) / 200.0)


def merge_group(rows):
    """Collapse same-person rows into the richest one, backfilling fields."""
    base = dict(max(rows, key=score))
    srcs = set()
    for r in rows:
        for s in str(r.get("source_url", "")).split(" ; "):
            if s.strip():
                srcs.add(s.strip())
        for f in ("email", "phone", "city", "state", "linkedin", "title"):
            if not base.get(f) and r.get(f):
                base[f] = r[f]
        # prefer a verified email / confirmed state / a real phone if base lacks
        if str(r.get("email_confidence", "")).startswith("verified") and not \
                str(base.get("email_confidence", "")).startswith("verified"):
            base["email"], base["email_confidence"] = r["email"], r["email_confidence"]
        if r.get("state_confidence") == "confirmed" and base.get("state_confidence") != "confirmed":
            base["state"], base["state_confidence"] = r["state"], "confirmed"
        if r.get("phone") and not base.get("phone"):
            base["phone"], base["phone_type"] = r["phone"], r.get("phone_type", "")
    base["source_url"] = " ; ".join(sorted(srcs))
    return base


def current_employer(serper, name, conflict_keys):
    """Read the person's LinkedIn headline; return the company key whose alias is
    in the CURRENT role (headline), else None."""
    for it in serper.organic(f'"{name}"', num=6):
        if "linkedin.com/in" not in it.get("link", ""):
            continue
        title = it.get("title", "")
        head = title.split(" - ", 1)[1] if " - " in title else title
        for key in conflict_keys:
            if any(a.lower() in head.lower() for a in COMPANIES[key]["aliases"]):
                return key
        # fall back to first employer named in the snippet
        snip = it.get("snippet", "").lower()
        best = min((snip.find(a.lower()) for key in conflict_keys
                    for a in COMPANIES[key]["aliases"] if a.lower() in snip), default=-1)
        if best >= 0:
            for key in conflict_keys:
                if any(a.lower() in snip and snip.find(a.lower()) == best
                       for a in COMPANIES[key]["aliases"]):
                    return key
        break
    return None


def main():
    serper = Serper()
    data = {}
    for f in sorted(CONTACTS.glob("*.json")):
        key = f.stem
        if key not in COMPANIES:
            continue
        rows = json.loads(f.read_text())
        if isinstance(rows, list) and rows and isinstance(rows[0], dict) and "name" in rows[0]:
            data[key] = rows

    # 1) within-company merge by linkedin URL, then by normalized name
    for key, rows in data.items():
        groups = defaultdict(list)
        for r in rows:
            gk = r.get("linkedin") or f"name:{_nk(r.get('name'))}"
            groups[gk].append(r)
        # second pass: also merge different-URL rows that share a name
        merged = [merge_group(g) for g in groups.values()]
        by_name = defaultdict(list)
        for r in merged:
            by_name[_nk(r["name"])].append(r)
        data[key] = [merge_group(g) for g in by_name.values()]

    # 2) cross-company: same LinkedIn URL in >1 company
    li_owner = defaultdict(list)
    for key, rows in data.items():
        for r in rows:
            if r.get("linkedin"):
                li_owner[r["linkedin"]].append(key)
    conflicts = {li: ks for li, ks in li_owner.items() if len(set(ks)) > 1}
    print(f"resolving {len(conflicts)} cross-company duplicates by current employer...")
    resolved = {}
    for li, keys in conflicts.items():
        keys = sorted(set(keys))
        name = next((r["name"] for k in keys for r in data[k] if r.get("linkedin") == li), "")
        keep = current_employer(serper, name, keys)
        if keep not in keys:
            # undecided -> keep where the row scores highest
            keep = max(keys, key=lambda k: max(
                (score(r) for r in data[k] if r.get("linkedin") == li), default=0))
        resolved[li] = keep
        for k in keys:
            if k != keep:
                data[k] = [r for r in data[k] if r.get("linkedin") != li]
            else:
                for r in data[k]:
                    if r.get("linkedin") == li and "also matched" not in r.get("notes", ""):
                        others = [x for x in keys if x != keep]
                        r["notes"] = (r.get("notes", "") + f" [also matched: {','.join(others)}; "
                                      f"kept at current employer]").strip()

    for key, rows in data.items():
        (CONTACTS / f"{key}.json").write_text(json.dumps(rows, indent=1))
    total = sum(len(v) for v in data.values())
    print(f"deduped -> {total} people across {len(data)} companies "
          f"(serper={serper.queries_spent}q)")


if __name__ == "__main__":
    main()
