#!/usr/bin/env python3
"""Copy an XLSX ledger and create its foundation-aware review JSON skeleton."""

import argparse
from datetime import date, datetime, time, timezone
import hashlib
import json
from pathlib import Path
import shutil

try:
    from openpyxl import load_workbook
    from openpyxl.utils import column_index_from_string
    from openpyxl.utils.datetime import CALENDAR_WINDOWS_1900, to_excel
except ImportError as exc:
    raise SystemExit("openpyxl is required. Install this skill's requirements.txt in an isolated Python environment.") from exc


SKILL_ROOT = Path(__file__).resolve().parents[1]


def cell_json(cell):
    value = cell.value
    if value is None:
        return None
    if cell.data_type == "f":
        return {"t": "f", "v": str(value)}
    if isinstance(value, datetime):
        return {"t": "d", "v": to_excel(value, CALENDAR_WINDOWS_1900), "display": value.isoformat(sep=" ")}
    if isinstance(value, date):
        return {"t": "d", "v": to_excel(value, CALENDAR_WINDOWS_1900), "display": value.isoformat()}
    if isinstance(value, time):
        return {"t": "s", "v": value.isoformat()}
    if isinstance(value, bool):
        return {"t": "b", "v": value}
    if isinstance(value, (int, float)):
        return {"t": "n", "v": value}
    return {"t": "s", "v": str(value)}


def column_number(value):
    return column_index_from_string(value.upper()) - 1 if value else None


def is_nonempty(row):
    return any(cell is not None for cell in row)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--foundation", required=True)
    parser.add_argument("--foundation-ref", type=Path, help="Path to a future foundation reference JSON")
    parser.add_argument("--sheet", help="Transaction sheet name; defaults to the first sheet")
    parser.add_argument("--header-row", type=int, default=1)
    parser.add_argument("--first-record-row", type=int)
    parser.add_argument("--record-column", help="Rows without this column are marked structural")
    parser.add_argument("--description-column")
    parser.add_argument("--amount-column")
    parser.add_argument("--account-column")
    parser.add_argument("--date-column")
    parser.add_argument("--reference-column")
    args = parser.parse_args()

    source = args.input.resolve()
    if not source.is_file() or source.suffix.lower() != ".xlsx":
        parser.error("input must be an existing .xlsx workbook")
    if args.header_row < 1:
        parser.error("--header-row must be positive")

    reference_path = args.foundation_ref or SKILL_ROOT / "references" / "foundations" / f"{args.foundation}.json"
    if not reference_path.is_file():
        parser.error(f"foundation reference not found: {reference_path}")
    foundation = json.loads(reference_path.read_text(encoding="utf-8"))
    if foundation.get("id") != args.foundation:
        parser.error("foundation id does not match the reference file")

    workbook = load_workbook(source, read_only=True, data_only=False)
    selected = args.sheet or workbook.sheetnames[0]
    if selected not in workbook.sheetnames:
        parser.error(f"sheet not found: {selected}; available: {', '.join(workbook.sheetnames)}")
    digest = hashlib.sha256()
    with source.open("rb") as source_file:
        for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(chunk)
    source_sha = digest.hexdigest()
    first_record = args.first_record_row or args.header_row + 1
    if first_record <= args.header_row:
        parser.error("first record row must be after the header row")

    sheets = []
    records = []
    record_column = column_number(args.record_column)
    for sheet in workbook.worksheets:
        rows = [[cell_json(cell) for cell in row] for row in sheet.iter_rows()]
        sheets.append({"name": sheet.title, "rows": rows})
        if sheet.title != selected:
            continue
        for row_number in range(first_record, len(rows) + 1):
            row = rows[row_number - 1]
            if not is_nonempty(row):
                continue
            structural = record_column is not None and (record_column >= len(row) or row[record_column] is None)
            records.append({
                "row": row_number,
                "kind": "structural" if structural else "transaction",
                "classification": {
                    "category_id": None,
                    "status": "not_applicable" if structural else "needs_review",
                    "rationale": "",
                    "uncertainty_reason": "" if structural else "טרם סווגה תנועה זו",
                },
            })

    report = {
        "schema_version": 1,
        "report_id": source_sha[:16],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "foundation": foundation,
        "source": {
            "filename": source.name,
            "sha256": source_sha,
            "sheet": selected,
            "header_row": args.header_row,
            "first_record_row": first_record,
            "display_columns": {
                "description": column_number(args.description_column),
                "amount": column_number(args.amount_column),
                "account": column_number(args.account_column),
                "date": column_number(args.date_column),
                "reference": column_number(args.reference_column),
            },
        },
        "eligibility_context": {
            "grant_program": None,
            "award_year": None,
            "approved_budget_available": False,
            "written_exceptions_available": False,
            "eligibility_status": "unverified",
        },
        "sheets": sheets,
        "records": records,
    }

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    copy_path = output_dir / f"source-copy-{source.name}"
    output_json = output_dir / "review.json"
    if copy_path.resolve() == source:
        parser.error("source copy path would overwrite the input")
    if copy_path.exists() or output_json.exists():
        parser.error("output directory already contains a source copy or review.json; choose a new directory to preserve prior review work")
    shutil.copy2(source, copy_path)
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    workbook.close()
    print(json.dumps({"json": str(output_json), "source_copy": str(copy_path), "records": len(records), "transactions": sum(r["kind"] == "transaction" for r in records)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
