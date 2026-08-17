"""Excel output with the Ground Truth Protocol sheet first — xlsxwriter only
(the BASF project learned the hard way that openpyxl produces repair prompts).
"""

from __future__ import annotations

from pathlib import Path

import xlsxwriter

from . import settings
from .validate import GTPAudit


def write_workbook(path: str | Path, sheets: dict[str, tuple[list[str], list[list]]],
                   audit: GTPAudit | None = None,
                   header_color: str = settings.CLUTCH_BLUE) -> Path:
    """sheets = {"Tab Name": (headers, rows)}. GTP audit sheet goes first."""
    path = Path(path)
    wb = xlsxwriter.Workbook(str(path), {"strings_to_urls": False})

    header_fmt = wb.add_format({"bold": True, "font_color": "white",
                                "bg_color": header_color, "text_wrap": True,
                                "valign": "vcenter", "border": 1})
    cell_fmt = wb.add_format({"valign": "top", "text_wrap": True})
    pass_fmt = wb.add_format({"bg_color": "#C6EFCE", "font_color": "#006100", "bold": True})
    review_fmt = wb.add_format({"bg_color": "#FFEB9C", "font_color": "#9C6500", "bold": True})
    link_fmt = wb.add_format({"font_color": "#0563C1", "underline": True})

    if audit and audit.checks:
        ws = wb.add_worksheet("Ground Truth Protocol")
        gtp_headers = ["#", "Tab", "Source URL", "Expected", "Actual",
                       "Missing Key Field", "Co-located Rows", "Verdict", "Notes"]
        for col, h in enumerate(gtp_headers):
            ws.write(0, col, h, header_fmt)
        for i, c in enumerate(audit.checks, start=1):
            ws.write(i, 0, i, cell_fmt)
            ws.write(i, 1, c.tab, cell_fmt)
            ws.write_url(i, 2, c.source_url or "", link_fmt, string=c.source_url or "")
            ws.write(i, 3, c.expected, cell_fmt)
            ws.write(i, 4, c.actual, cell_fmt)
            ws.write(i, 5, c.missing_key_field, cell_fmt)
            ws.write(i, 6, c.co_located, cell_fmt)
            ws.write(i, 7, c.verdict, pass_fmt if c.verdict == "PASS" else review_fmt)
            ws.write(i, 8, c.notes, cell_fmt)
        note_row = len(audit.checks) + 2
        ws.write(note_row, 0,
                 "PASS = actual >= expected AND <5% of rows missing the key field. "
                 "REVIEW means check the tab, not that it failed. Co-located rows are "
                 "flagged, never dropped — one address can host multiple operations.",
                 cell_fmt)
        ws.set_column(0, 0, 4)
        ws.set_column(1, 2, 38)
        ws.set_column(3, 7, 14)
        ws.set_column(8, 8, 50)
        ws.freeze_panes(1, 0)

    for tab, (headers, rows) in sheets.items():
        ws = wb.add_worksheet(tab[:31])
        for col, h in enumerate(headers):
            ws.write(0, col, h, header_fmt)
        widths = [len(h) for h in headers]
        for r, row in enumerate(rows, start=1):
            for col, val in enumerate(row):
                ws.write(r, col, "" if val is None else val, cell_fmt)
                widths[col] = min(max(widths[col], len(str(val or ""))), 60)
        for col, w in enumerate(widths):
            ws.set_column(col, col, w + 2)
        ws.freeze_panes(1, 0)
        if rows:
            ws.autofilter(0, 0, len(rows), len(headers) - 1)

    wb.close()
    return path


def rows_from_dicts(rows: list[dict], headers: list[str],
                    field_map: dict[str, str] | None = None) -> list[list]:
    """Convert dict rows to lists in header order. field_map: header -> dict key."""
    field_map = field_map or {}
    keys = [field_map.get(h, h.lower().replace(" ", "_")) for h in headers]
    return [[r.get(k, "") for k in keys] for r in rows]
