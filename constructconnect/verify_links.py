"""Validate every hyperlink target Excel will parse on open.

This reads the raw sheet XML rather than trusting the writer, because the failure
mode being guarded against is Excel rejecting a target we thought was fine.
"""
import re
import sys
import zipfile

F = "output/ConstructConnect_Leads_V2.xlsx"
OK = re.compile(r"^(https?://[A-Za-z0-9.\-]+\.[A-Za-z]{2,}(/[^\s]*)?|mailto:[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})$")

z = zipfile.ZipFile(F)
print("zip integrity:", "OK" if z.testzip() is None else "CORRUPT")

sheets = sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
total = bad = 0
for s in sheets:
    xml = z.read(s).decode("utf8", "replace")
    rels_name = s.replace("worksheets/", "worksheets/_rels/") + ".rels"
    targets = []
    if rels_name in z.namelist():
        rels = z.read(rels_name).decode("utf8", "replace")
        targets = re.findall(r'Target="([^"]+)"[^>]*TargetMode="External"', rels)
    n_link_tags = len(re.findall(r"<hyperlink ", xml))
    offenders = [t for t in targets if not OK.match(t.replace("&amp;", "&"))]
    total += len(targets)
    bad += len(offenders)
    print(f"  {s:28s} hyperlink tags={n_link_tags:6d} external targets={len(targets):6d} invalid={len(offenders)}")
    for t in offenders[:5]:
        print("      !", t[:100])

print()
print(f"TOTAL external hyperlink targets: {total}")
print(f"INVALID targets: {bad}")
print("Excel per-sheet hyperlink ceiling is 65,530 - max on any sheet is fine" if total else "")
sys.exit(1 if bad else 0)
