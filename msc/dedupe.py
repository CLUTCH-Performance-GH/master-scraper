"""Deduplication — the HNA pipeline's bucket-merge logic, generalized.

Strategy: bucket rows by the most specific key available
(company+state+contact > company+domain > company+state), keep the fullest
row in each bucket, backfill its empty fields from siblings, and concatenate
source attributions so provenance is never lost.
"""

from collections import defaultdict

from .extract import extract_domain, normalize_company

MERGE_FIELDS = ("city", "state", "zip", "street", "website", "phone", "email",
                "title", "linkedin", "contact_page_url")


def row_key(row: dict) -> str:
    company = normalize_company(row.get("company_name") or row.get("name", ""))
    state = (row.get("state") or "").lower().strip()
    contact = (row.get("contact_name") or "").lower().strip()
    domain = extract_domain(row.get("website", ""))
    if contact:
        return f"{company}|{state}|{contact}"
    if domain:
        return f"{company}|@{domain}"
    return f"{company}|{state}"


def merge_rows(rows: list[dict], key_fn=row_key,
               list_fields: tuple = ("source", "audience")) -> list[dict]:
    """Dedupe by key; keep fullest row; backfill fields; union list_fields."""
    buckets: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        key = key_fn(r)
        if key and key.strip("|"):
            buckets[key].append(r)

    merged = []
    for bucket in buckets.values():
        base = dict(max(bucket, key=lambda r: sum(1 for v in r.values() if v)))
        for f in MERGE_FIELDS:
            if not base.get(f):
                for r in bucket:
                    if r.get(f):
                        base[f] = r[f]
                        break
        for f in list_fields:
            values = sorted({v.strip() for r in bucket
                             for v in str(r.get(f, "")).split("|") if v.strip()})
            if values:
                base[f] = " | ".join(values)
        merged.append(base)
    return merged
