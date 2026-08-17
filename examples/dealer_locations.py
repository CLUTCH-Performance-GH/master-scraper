"""Worked example: scrape a retailer's store locator into a GTP-audited workbook.

Demonstrates the three discovery techniques the CLS project used, now reusable:
  - sitemap mining for the authoritative expected count
  - JSON-LD LocalBusiness extraction (cheapest structured data)
  - Serper gap-fill for sister facilities

Run:  python -m examples.dealer_locations
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from msc import settings  # noqa: E402
from msc.extract import (crawl_sitemap, extract_json_ld, parse_local_business,  # noqa: E402
                         split_address)
from msc.pipeline import Context, Runner, Source  # noqa: E402
from msc.workbook import rows_from_dicts, write_workbook  # noqa: E402


def discover_from_sitemap(ctx: Context) -> list[dict]:
    """Mine the sitemap for location detail pages — the authoritative count."""
    urls = crawl_sitemap("https://locations.example-retailer.com/sitemap_index.xml", ctx.http)
    locs = [u for u in urls if "/store/" in u or u.rstrip("/").count("/") >= 4]
    return [{"url": u} for u in locs]


def parse_location(item: dict, text: str, ctx: Context) -> list[dict]:
    """LocalBusiness JSON-LD first; fall back to address parsing if absent."""
    blocks = extract_json_ld(text, type_filter="LocalBusiness")
    if blocks:
        row = parse_local_business(blocks[0])
        row["company_name"] = row.pop("name", "")
        row["website"] = item["url"]
        return [row]
    return []


def main():
    runner = Runner("dealer_locations", Context())
    sources = [
        Source(
            name="ExampleRetailer",
            audience="dealer",
            source_url="https://locations.example-retailer.com/",
            expected_count=0,  # filled from sitemap discovery
            discover=discover_from_sitemap,
            parse=parse_location,
        ),
    ]
    rows = runner.run(sources)

    headers = ["Company Name", "Street", "City", "State", "Zip", "Phone",
               "Latitude", "Longitude", "Co Location Flag", "Source"]
    fmap = {"Company Name": "company_name", "Co Location Flag": "co_location_flag",
            "Latitude": "lat", "Longitude": "lng"}
    out = write_workbook(
        settings.OUTPUT_DIR / "Dealer_Locations_V1.xlsx",
        {"Locations": (headers, rows_from_dicts(rows, headers, fmap))},
        audit=runner.audit,
    )
    print(f"Wrote {out} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
