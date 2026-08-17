"""Determine each company's REAL email pattern by harvesting actual @domain
emails (company site pages + cached Serper) and matching their local-parts to
the names we already discovered. Matching a real address to a known person is
the only way to tell flast ('dculp') from firstlast ('dougculp') reliably.

Outputs a pattern recommendation + any verbatim emails (which we can mark
'verified' instead of 'inferred').
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from msc.extract import extract_domain, extract_emails
from msc.net import Firecrawl, Http, Serper, fetch_smart
from jobs.run_company import COMPANIES

http, serper, fc = Http(), Serper(), Firecrawl()
PAGES = ["", "/contact", "/contact-us", "/about", "/about-us", "/team",
         "/our-team", "/staff", "/leadership", "/people", "/locations",
         "/our-people", "/management"]

_PLACEHOLDER = {"jdoe", "johndoe", "john.doe", "janedoe", "jane.doe", "flast",
                "firstlast", "firstname", "lastname", "first.last", "name",
                "email", "username", "example", "yourname", "first", "last"}
_GEN_SUB = ("marketing", "service", "support", "info", "contact", "career",
            "sales", "admin", "office", "billing", "credit", "account", "help",
            "feedback", "news", "media", "webmaster", "noreply", "donotreply",
            "customer", "inquir", "hr", "team", "general", "mail")


def harvest(domain):
    emails = set()
    base = f"https://{domain}"
    for p in PAGES:
        method, text = fetch_smart(base + p, http, firecrawl=fc)
        if text:
            for e in extract_emails(text):
                if extract_domain(e) == domain:
                    emails.add(e.lower())
    return emails


def pattern_of(localpart, names):
    """Match a local-part to a known person's name -> the pattern that produced it."""
    lp = re.sub(r"\d+$", "", localpart)
    for n in names:
        parts = [re.sub(r"[^a-z]", "", x.lower()) for x in n.split() if x]
        parts = [p for p in parts if p]
        if len(parts) < 2:
            continue
        f, l = parts[0], parts[-1]
        if not (f and l):
            continue
        cands = {
            "first.last": f"{f}.{l}", "first_last": f"{f}_{l}",
            "firstlast": f"{f}{l}", "flast": f"{f[0]}.{l}",
            "flast_nodot": f"{f[0]}{l}", "lastfirst": f"{l}.{f}",
            "lastf": f"{l}{f[0]}",
        }
        for pat, cand in cands.items():
            if lp == cand:
                return pat, n
    return None, None


def main():
    for key, cfg in COMPANIES.items():
        domain = cfg["email_domain"]
        emails = harvest(domain)
        for q in cfg["pattern_queries"]:
            for it in serper.organic(q, num=10):
                blob = it.get("title", "") + " " + it.get("snippet", "") + " " + it.get("link", "")
                for e in extract_emails(blob):
                    if extract_domain(e) == domain:
                        emails.add(e.lower())
        # personal-looking only
        personal = []
        for e in sorted(emails):
            lp = re.sub(r"\d+$", "", e.split("@")[0])
            if (lp in _PLACEHOLDER or lp in ("",) or len(lp) < 3
                    or any(g in lp for g in _GEN_SUB) or lp.startswith(domain[:3])):
                continue
            personal.append(e)

        pf = Path(f"output/contacts/{key}.json")
        names = [p["name"] for p in json.loads(pf.read_text())] if pf.exists() else []
        votes = Counter()
        matched = []
        for e in personal:
            pat, who = pattern_of(e.split("@")[0], names)
            if pat:
                votes[pat] += 1
                matched.append(f"{e} <= {who}")
        best = votes.most_common(1)[0][0] if votes else "UNVERIFIED"
        print(f"\n[{key}] {domain}  ->  PATTERN: {best}   (current cfg default may differ)")
        print(f"   real emails found: {len(personal)} | name-matched: {len(matched)}")
        for m in matched[:6]:
            print(f"     {m}")
        if not matched and personal:
            print(f"     samples (unmatched): {personal[:6]}")
    print(f"\nspend: serper={serper.queries_spent} firecrawl={fc.credits_spent}")


if __name__ == "__main__":
    main()
