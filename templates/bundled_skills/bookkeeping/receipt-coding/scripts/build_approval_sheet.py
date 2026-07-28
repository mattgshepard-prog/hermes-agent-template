#!/usr/bin/env python3
"""Render a coded receipt batch into an approval sheet.

Reads a JSON array of row objects on stdin, writes CSV always and XLSX when
openpyxl is importable. Prints the paths written, one per line, so the caller
knows what to attach.

Usage:
    echo '[{...}, {...}]' | python build_approval_sheet.py --out /tmp/approval_sheet

The --out value is a path WITHOUT extension. Extensions are appended.
"""

import argparse
import csv
import json
import sys

COLUMNS = [
    "Date",
    "Vendor",
    "Description",
    "Subtotal",
    "Sales Tax",
    "Tip",
    "Total",
    "Payment Method",
    "Last 4",
    "Suggested Account",
    "Schedule C Line",
    "Deductible %",
    "Confidence",
    "Needs Review",
    "Review Reason",
    "Source File",
]

MONEY_COLUMNS = {"Subtotal", "Sales Tax", "Tip", "Total"}


def normalize(rows):
    """Force every row to the canonical column set, in order.

    Unknown keys are dropped rather than silently reordering the sheet.
    Missing keys become empty strings rather than None, so the CSV is clean.
    """
    out = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("each row must be a JSON object")
        out.append({col: ("" if row.get(col) is None else row.get(col)) for col in COLUMNS})
    return out


def write_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def write_xlsx(rows, path):
    """Return the path on success, or None if openpyxl is unavailable.

    Never raises on a missing library. CSV is the guaranteed deliverable and
    XLSX is the upgrade, so an import failure must not lose the batch.
    """
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError:
        return None

    wb = Workbook()
    ws = wb.active
    ws.title = "Approval Sheet"

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="1F3864")
    flag_fill = PatternFill("solid", fgColor="FFF2CC")

    ws.append(COLUMNS)
    for cell in ws[1]:
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    review_idx = COLUMNS.index("Needs Review")
    for row in rows:
        ws.append([row[col] for col in COLUMNS])
        flagged = str(row[COLUMNS[review_idx]]).strip().lower() in ("yes", "true", "y", "1")
        if flagged:
            for cell in ws[ws.max_row]:
                cell.fill = flag_fill

    for i, col in enumerate(COLUMNS, start=1):
        letter = get_column_letter(i)
        width = max(len(col), *(len(str(r[col])) for r in rows)) if rows else len(col)
        ws.column_dimensions[letter].width = min(max(width + 2, 10), 45)
        if col in MONEY_COLUMNS:
            for cell in ws[letter][1:]:
                cell.number_format = '#,##0.00'

    ws.freeze_panes = "A2"
    wb.save(path)
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, help="output path without extension")
    args = parser.parse_args()

    raw = sys.stdin.read().strip()
    if not raw:
        print("ERROR: no JSON received on stdin", file=sys.stderr)
        return 2

    try:
        rows = json.loads(raw)
    except json.JSONDecodeError as exc:
        print("ERROR: stdin is not valid JSON: %s" % exc, file=sys.stderr)
        return 2

    if not isinstance(rows, list):
        print("ERROR: expected a JSON array of row objects", file=sys.stderr)
        return 2

    try:
        rows = normalize(rows)
    except ValueError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2

    written = [write_csv(rows, args.out + ".csv")]
    xlsx = write_xlsx(rows, args.out + ".xlsx")
    if xlsx:
        written.append(xlsx)
    else:
        print("NOTE: openpyxl unavailable, CSV only", file=sys.stderr)

    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
