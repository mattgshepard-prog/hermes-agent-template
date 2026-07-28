#!/usr/bin/env python3
"""Approval sheet renderer and scheme reporter for the receipt-coding skill.

Two modes.

  Report the active coding scheme (counts come from the file, never estimated):

      python3 build_approval_sheet.py --scheme-info

  Render a coded batch into ONE approval sheet:

      python3 build_approval_sheet.py --rows-file /tmp/receipt_rows.json \\
                                      --out /tmp/approval_sheet

``--rows-file`` exists so callers never need a heredoc, a ``-c`` flag, or
``execute_code`` to feed data in. All three trip command-approval gates that
stop an email-driven agent dead. Passing a path does not.

Exactly ONE file is written: .xlsx when openpyxl is importable, otherwise .csv.
Earlier versions emitted both, and the agent dutifully attached both, which
arrived as two separate emails.

Rows may also be piped on stdin when no --rows-file is given.
"""

import argparse
import csv
import json
import os
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
    "Read Confidence",
    "Account Confidence",
    "Needs Review",
    "Review Reason",
    "Source File",
]

MONEY_COLUMNS = {"Subtotal", "Sales Tax", "Tip", "Total"}

DEFAULT_SCHEME = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "reference", "coding_scheme.json"
)


def load_scheme(path):
    return json.load(open(path, encoding="utf-8"))


def scheme_info(path):
    """Print scheme identity and counts derived from the file itself.

    This exists because an instruction to 'count the accounts' is not a
    mechanism for counting. v1.0.0 reported 40 accounts and v1.1.0 reported
    38 plus 2; the file has held 31 plus 2 the whole time. Numbers must come
    from len(), not from reading and estimating.
    """
    cfg = load_scheme(path)
    accounts = cfg.get("accounts", [])
    fallbacks = cfg.get("fallback_accounts", [])
    assignable = [a for a in accounts if a.get("auto_assign")]
    not_assignable = [a for a in accounts if not a.get("auto_assign")]

    print("scheme_id: %s" % cfg.get("scheme_id", ""))
    print("display_name: %s" % cfg.get("display_name", ""))
    print("version: %s" % cfg.get("version", ""))
    print("source: %s" % cfg.get("source", ""))
    print("accounts: %d" % len(accounts))
    print("  assignable_from_a_receipt: %d" % len(assignable))
    print("  not_assignable: %d" % len(not_assignable))
    print("fallback_accounts: %d" % len(fallbacks))
    for f in fallbacks:
        print("  - %s" % f.get("account", ""))
    print("total_named_accounts: %d" % (len(accounts) + len(fallbacks)))
    return 0


def normalize(rows):
    """Force every row to the canonical column set, in order."""
    out = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("each row must be a JSON object")
        out.append({c: ("" if row.get(c) is None else row.get(c)) for c in COLUMNS})
    return out


def write_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return path


def write_xlsx(rows, path):
    """Return the path, or None when openpyxl is unavailable."""
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

    review_col = COLUMNS.index("Needs Review")
    for row in rows:
        ws.append([row[c] for c in COLUMNS])
        flagged = str(row[COLUMNS[review_col]]).strip().lower() in ("yes", "true", "y", "1")
        if flagged:
            for cell in ws[ws.max_row]:
                cell.fill = flag_fill

    for i, col in enumerate(COLUMNS, start=1):
        letter = get_column_letter(i)
        width = max(len(col), *(len(str(r[col])) for r in rows)) if rows else len(col)
        ws.column_dimensions[letter].width = min(max(width + 2, 10), 45)
        if col in MONEY_COLUMNS:
            for cell in ws[letter][1:]:
                cell.number_format = "#,##0.00"

    ws.freeze_panes = "A2"
    wb.save(path)
    return path


def read_rows(args):
    if args.rows_file:
        raw = open(args.rows_file, encoding="utf-8").read().strip()
        origin = args.rows_file
    else:
        raw = sys.stdin.read().strip()
        origin = "stdin"
    if not raw:
        raise ValueError("no JSON received from %s" % origin)
    rows = json.loads(raw)
    if not isinstance(rows, list):
        raise ValueError("expected a JSON array of row objects from %s" % origin)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scheme-info", action="store_true", help="print scheme identity and counts")
    ap.add_argument("--scheme", default=DEFAULT_SCHEME, help="path to coding_scheme.json")
    ap.add_argument("--rows-file", help="path to a JSON array of row objects")
    ap.add_argument("--out", help="output path WITHOUT extension")
    args = ap.parse_args()

    if args.scheme_info:
        try:
            return scheme_info(args.scheme)
        except Exception as exc:
            print("ERROR: could not read scheme: %s" % exc, file=sys.stderr)
            return 2

    if not args.out:
        print("ERROR: --out is required when rendering a sheet", file=sys.stderr)
        return 2

    try:
        rows = normalize(read_rows(args))
    except (ValueError, json.JSONDecodeError) as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2
    except OSError as exc:
        print("ERROR: could not read rows file: %s" % exc, file=sys.stderr)
        return 2

    written = write_xlsx(rows, args.out + ".xlsx")
    if not written:
        written = write_csv(rows, args.out + ".csv")
        print("NOTE: openpyxl unavailable, wrote CSV instead", file=sys.stderr)

    print(written)
    return 0


if __name__ == "__main__":
    sys.exit(main())
