"""Phase 0 smoke test + Nutrien recon. Confirms keys work and shows real data shape."""
import json, sys
from msc.net import Http, Serper, Firecrawl
from msc.extract import extract_phone, extract_emails, infer_emails, extract_domain
from msc.osint import mx_records, has_deliverable_mail
from msc import validate, workbook  # import-only check

http = Http()
serper = Serper()
fc = Firecrawl()
print("=== imports OK ===")

# 1) MX verification — establishes which email domains accept mail
print("\n=== MX check (email deliverability gate) ===")
for d in ("nutrien.com", "nutrienagsolutions.com", "chsinc.com", "chs.com"):
    mx = mx_records(d, http)
    print(f"  {d:26} deliverable={bool(mx)}  hosts={mx[:2]}")

# 2) Serper recon — see exactly what LinkedIn + web results look like for Nutrien people
recon = {}
queries = [
    ('linkedin_agronomist_IA', 'site:linkedin.com/in "Nutrien Ag Solutions" agronomist Iowa'),
    ('linkedin_sales_KS',      'site:linkedin.com/in "Nutrien Ag Solutions" sales Kansas'),
    ('web_branch_mgr_AR',      '"Nutrien Ag Solutions" Arkansas branch manager agronomist'),
]
for label, q in queries:
    items = serper.organic(q, num=10)
    recon[label] = [{"title": i.get("title",""), "link": i.get("link",""),
                     "snippet": i.get("snippet","")} for i in items]
    print(f"\n=== {label} :: {q}\n  ({len(items)} results)")
    for it in recon[label][:6]:
        print(f"  • {it['title']}")
        print(f"      {it['snippet'][:140]}")

with open("jobs/recon_nutrien.json", "w") as f:
    json.dump(recon, f, indent=2)
print(f"\n=== spend: serper={serper.queries_spent} queries, firecrawl={fc.credits_spent} credits ===")
print("=== raw results saved to jobs/recon_nutrien.json ===")
