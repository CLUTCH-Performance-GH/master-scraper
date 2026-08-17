"""Infer + MX-verify emails for CLS regional contacts (they mostly lack emails —
their sites don't publish them). Single-domain companies use their one domain;
FS/GROWMARK members use the member domain derived from the source URL slug.
Writes emails + email_confidence back into output/CLS/regionals.json.
"""
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import jobs.contacts_lib as cl
from msc.net import Http, Serper


def derive_pattern(localpart, first, last):
    """Which pattern produced this local part for this name? (reverse-match)."""
    lp = re.sub(r"\d+$", "", (localpart or "").lower())
    f = re.sub(r"[^a-z]", "", (first or "").lower())
    l = re.sub(r"[^a-z]", "", (last or "").lower())
    if not (f and l):
        return None
    for pat, cand in {"first.last": f"{f}.{l}", "flast": f"{f[0]}{l}",
                      "firstlast": f"{f}{l}", "lastf": f"{l}{f[0]}",
                      "first_last": f"{f}_{l}"}.items():
        if lp == cand:
            return pat
    return None

REG = Path("output/CLS/regionals.json")
SINGLE_DOMAIN = {
    "mfa": "mfa-inc.com", "hopkinsville_elevator": "hopelevator.com",
    "premier_coop": "premiercooperative.net", "united_prairie": "unitedprairie.com",
    "midwest_fertilizer": "midwestfertilizerinc.com", "simplot_ky": "simplot.com",
}
# domains where a subagent verified the pattern against real addresses
PATTERN_OVERRIDE = {"unitedprairie.com": "flast"}  # jcorbin, jreese, droelfs


def main():
    reg = json.loads(REG.read_text())
    http, serper = Http(), Serper()
    mx_cache, pat_cache = {}, {}

    # derive each FS member's pattern from the verbatim emails its agent found
    fs_pat = {}
    votes = defaultdict(Counter)
    for c in reg["companies"]:
        for x in c["contacts"]["contacts"]:
            e = x.get("email", "") or ""
            if "@" not in e or str(x.get("email_confidence", "")).startswith("inferred"):
                continue
            dom = x.get("member_domain") or e.split("@", 1)[1].lower()
            _, first, last, _ = cl.clean_name(x.get("name", ""))
            p = derive_pattern(e.split("@")[0], first, last)
            if p and dom:
                votes[dom][p] += 1
    for dom, cnt in votes.items():
        fs_pat[dom] = cnt.most_common(1)[0][0]

    def pattern_for(dom):
        if dom in PATTERN_OVERRIDE:
            return PATTERN_OVERRIDE[dom]
        if dom in fs_pat:
            return fs_pat[dom]
        if dom not in pat_cache:
            p, _ = cl.discover_email_pattern(
                serper, http, dom, [f'"@{dom}"', f'"@{dom}" agronomy OR sales OR manager'])
            pat_cache[dom] = p or "first.last"
        return pat_cache[dom]

    added = 0
    for c in reg["companies"]:
        key = c["key"]
        for x in c["contacts"]["contacts"]:
            ec = str(x.get("email_confidence", ""))
            if x.get("email") and not ec.startswith("inferred"):
                x.setdefault("email_confidence", "found on source")
                continue  # keep verbatim / agent-provided emails as-is
            dn, first, last, _ = cl.clean_name(x.get("name", ""))
            if not (first and last):
                continue
            dom, conf_label = None, "inferred (pattern - verify)"
            if key in SINGLE_DOMAIN:
                dom = SINGLE_DOMAIN[key]
            elif key == "fs_growmark":
                if x.get("member_domain"):
                    dom = x["member_domain"]
                    conf_label = "inferred (FS member domain - verify)"
                else:
                    m = re.search(r"fscooperatives\.com/([a-z0-9\-]+)/", x.get("source", "") or "")
                    if m and m.group(1) not in ("about", "find", "www"):
                        dom = m.group(1) + ".com"
                        conf_label = "inferred (FS member domain - verify)"
            if not dom:
                continue
            # FS members overwhelmingly use flast; use derived pattern where known
            pat = (fs_pat.get(dom) or "flast") if key == "fs_growmark" else pattern_for(dom)
            email, _ = cl.build_email(first, last, dom, pat, http, mx_cache)
            if email:
                x["email"], x["email_confidence"] = email, conf_label
                added += 1
    REG.write_text(json.dumps(reg, indent=1))
    print(f"emails inferred + added: {added}")
    print("patterns discovered:", pat_cache)


if __name__ == "__main__":
    main()
