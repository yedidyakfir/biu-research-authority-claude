#!/usr/bin/env python3
"""Validate review JSON coverage and classification invariants."""

import argparse
from collections import Counter
import json
from pathlib import Path


def validate(report):
    errors = []
    if report.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    foundation = report.get("foundation") or {}
    categories = foundation.get("categories") or []
    category_ids = [item.get("id") for item in categories]
    if not category_ids or len(category_ids) != len(set(category_ids)) or any(not x for x in category_ids):
        errors.append("foundation categories must have unique nonempty ids")
    sheets = {sheet.get("name"): sheet for sheet in report.get("sheets", [])}
    source = report.get("source") or {}
    selected = sheets.get(source.get("sheet"))
    if not selected:
        errors.append("selected source sheet is absent")
        return errors, {}
    rows = selected.get("rows") or []
    first_record = source.get("first_record_row")
    header_row = source.get("header_row")
    if not isinstance(header_row, int) or not isinstance(first_record, int) or header_row < 1 or first_record <= header_row:
        errors.append("invalid header/first-record row")
        return errors, {}
    expected = {i for i in range(first_record, len(rows) + 1) if any(cell is not None for cell in rows[i - 1])}
    records = report.get("records") or []
    actual = [record.get("row") for record in records]
    if len(actual) != len(set(actual)):
        errors.append("duplicate record row")
    missing = sorted(expected - set(actual))
    extra = sorted(set(actual) - expected)
    if missing:
        errors.append(f"missing record rows: {missing[:20]}{'...' if len(missing) > 20 else ''}")
    if extra:
        errors.append(f"extra record rows: {extra[:20]}{'...' if len(extra) > 20 else ''}")

    counts = Counter()
    for record in records:
        row = record.get("row")
        kind = record.get("kind")
        classification = record.get("classification") or {}
        status = classification.get("status")
        category = classification.get("category_id")
        rationale = (classification.get("rationale") or "").strip()
        uncertainty = (classification.get("uncertainty_reason") or "").strip()
        counts[f"{kind}:{status}"] += 1
        if category is not None and category not in category_ids:
            errors.append(f"row {row}: unknown category {category}")
        if kind == "structural":
            if status != "not_applicable" or category is not None:
                errors.append(f"row {row}: structural row must be not_applicable with no category")
        elif kind == "transaction":
            if status == "classified":
                if category is None or not rationale:
                    errors.append(f"row {row}: classified row needs category and rationale")
            elif status == "needs_review":
                if not uncertainty:
                    errors.append(f"row {row}: uncertain row needs a specific uncertainty_reason")
            elif status == "not_expense":
                if category is not None or not rationale:
                    errors.append(f"row {row}: non-expense row needs no category and a rationale")
            else:
                errors.append(f"row {row}: invalid transaction status {status}")
        else:
            errors.append(f"row {row}: invalid kind {kind}")
    return errors, dict(counts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("json_file", type=Path)
    args = parser.parse_args()
    report = json.loads(args.json_file.read_text(encoding="utf-8"))
    errors, counts = validate(report)
    print(json.dumps({"ok": not errors, "counts": counts, "errors": errors}, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
