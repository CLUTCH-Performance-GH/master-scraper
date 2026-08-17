"""V8 — verify CURRENT employment.

Discovery matched anyone whose LinkedIn mentions the company, which includes
FORMER employees (their past experience). This re-checks each person's current
LinkedIn headline: the company must be their PRESENT role, not just history.

For each person we locate their own profile result (matched by LinkedIn URL) and
read the headline ("Name - <Current Title> at <Current Company>"):
  - target company in the headline / first Experience entry -> 'current'
  - headline names a DIFFERENT employer                     -> 'former'
  - no company shown anywhere                               -> 'unverified'

Adds r['employment'] and r['current_role'] (the verbatim LinkedIn headline, so
the client can click the profile and confirm). Run:
  PYTHONPATH=. python3 jobs/verify_current.py [keys...]
"""
from __future__ import annotations

import json
import re
import sys

from msc.net import Serper
from jobs.run_company import COMPANIES

_LI_TAIL = re.compile(r"\s*[|\-]\s*linkedin.*$", re.I)


def _norm_url(u):
    return (u or "").split("?")[0].rstrip("/").lower()


def classify(it, aliases):
    """(status, current_role_headline) from one LinkedIn result."""
    title, snip = it.get("title", ""), it.get("snippet", "")
    headline = title.split(" - ", 1)[1] if " - " in title else title
    headline = _LI_TAIL.sub("", headline).strip()
    low = headline.lower()
    # 1) company in the current headline -> current
    if any(a.lower() in low for a in aliases):
        return ("current", headline)
    # 2) first 'Experience:' entry in the snippet = the person's CURRENT role
    me = re.search(r"Experience:\s*([^·|;]+)", snip)
    if me:
        first = me.group(1).strip()
        if any(a.lower() in first.lower() for a in aliases):
            return ("current", headline or first)
        return ("former", f"now: {first}")
    # 3) headline names a different employer, or signals they moved on
    if re.search(r"\bat\s+[A-Z][\w&.,'\-/ ]{2,}$", headline):
        return ("former", headline)
    if re.search(r"\b(self[- ]employed|retired|seeking|open to work|student)\b", low):
        return ("former", headline)
    # company appears in the snippet with a CURRENT marker ('... - Present')
    sl = snip.lower()
    for a in aliases:
        i = sl.find(a.lower())
        if i != -1 and ("present" in sl[i:i + 80]
                        or re.search(r"20\d\d\s*[-–]\s*present", sl[i:i + 80])):
            return ("current", headline or a)
    return ("unverified", headline)


def status_for(serper, name, li_url, aliases):
    target = _norm_url(li_url)
    # 1) find the person's OWN profile (match by URL) to avoid same-name people
    if target:
        for q in (f'"{name}" {aliases[0]}', f'"{name}"'):
            for it in serper.organic(q, num=10):
                if _norm_url(it.get("link", "")) == target:
                    return classify(it, aliases)
    # 2) fallback: first linkedin profile for the name
    for it in serper.organic(f'"{name}" {aliases[0]}', num=6):
        if "linkedin.com/in" in it.get("link", ""):
            return classify(it, aliases)
    return ("unverified", "")


def verify(key):
    cfg = COMPANIES[key]
    rows = json.loads(open(f"output/contacts/{key}.json").read())
    serper = Serper()
    cur = former = unver = 0
    for r in rows:
        if not r.get("linkedin"):
            r["employment"], r["current_role"] = "unverified", ""
            unver += 1
            continue
        st, role = status_for(serper, r["name"], r["linkedin"], cfg["aliases"])
        r["employment"], r["current_role"] = st, role
        if st == "current":
            cur += 1
        elif st == "former":
            former += 1
            r["notes"] = (r.get("notes", "") + " [LEFT COMPANY - verify]").strip()
        else:
            unver += 1
    json.dump(rows, open(f"output/contacts/{key}.json", "w"), indent=1)
    print(f"[{key}] current={cur} former={former} unverified={unver} | serper={serper.queries_spent}")


if __name__ == "__main__":
    for k in (sys.argv[1:] or list(COMPANIES.keys())):
        verify(k)
