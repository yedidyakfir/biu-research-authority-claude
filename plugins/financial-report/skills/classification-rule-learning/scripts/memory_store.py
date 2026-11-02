#!/usr/bin/env python3
"""Device-local decision history and narrowly scoped classification rules."""

import argparse
import calendar
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import unicodedata


SKILLS = Path(__file__).resolve().parents[2]
FOUNDATIONS = SKILLS / "fund-report-analysis" / "references" / "foundations"
DEFAULT_DB = Path.home() / ".local" / "share" / "fund-report" / "memory.sqlite3"


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def decision_time(item, override=None):
    raw = override or (item.get("classification") or {}).get("reviewed_at")
    if raw:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            raise ValueError("reviewed_at needs a timezone")
        moment = moment.astimezone(timezone.utc)
    else:
        moment = datetime.now(timezone.utc)
    return moment.isoformat(timespec="seconds")


def six_months_before(moment):
    month_index = moment.year * 12 + moment.month - 1 - 6
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    return moment.replace(year=year, month=month, day=min(moment.day, calendar.monthrange(year, month)[1]))


def normalized(value):
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def foundation_categories(foundation_id):
    if not re.fullmatch(r"[a-z0-9_-]{1,40}", foundation_id or ""):
        raise ValueError("invalid foundation id")
    path = FOUNDATIONS / f"{foundation_id}.json"
    if not path.is_file():
        raise ValueError(f"foundation reference missing: {foundation_id}")
    return {item["id"] for item in json.loads(path.read_text(encoding="utf-8"))["categories"]}


def connect(db_path):
    db_path = Path(db_path).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(db_path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS decisions (
            decision_key TEXT PRIMARY KEY,
            foundation_id TEXT NOT NULL,
            program TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            row_number INTEGER NOT NULL,
            decided_at TEXT NOT NULL,
            status TEXT NOT NULL,
            category_id TEXT,
            rationale TEXT NOT NULL,
            evidence_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS decisions_scope ON decisions(foundation_id, program, decided_at);
        CREATE TABLE IF NOT EXISTS rules (
            rule_id TEXT PRIMARY KEY,
            foundation_id TEXT NOT NULL,
            program TEXT NOT NULL,
            condition_json TEXT NOT NULL,
            status TEXT NOT NULL,
            category_id TEXT,
            explanation TEXT NOT NULL,
            support_count INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS rules_scope ON rules(foundation_id, program, active);
    """)
    return db


def evidence_from_row(report, record):
    sheet = next(sheet for sheet in report["sheets"] if sheet["name"] == report["source"]["sheet"])
    cells = sheet["rows"][record["row"] - 1]
    display = report["source"].get("display_columns") or {}

    def value(field):
        index = display.get(field)
        if index is None or index >= len(cells):
            return ""
        cell = cells[index]
        return str((cell.get("display", cell.get("v", "")) if isinstance(cell, dict) else cell) or "")[:1000]

    return {field: value(field) for field in ("account", "description", "reference")}


def validate_decision(item):
    foundation_id = item.get("foundation_id")
    categories = foundation_categories(foundation_id)
    source_sha = item.get("source_sha256")
    row = item.get("row")
    classification = item.get("classification") or {}
    status = classification.get("status")
    category = classification.get("category_id")
    rationale = str(classification.get("rationale") or "").strip()
    program = str(item.get("program") or "").strip()
    evidence = item.get("evidence") or {}
    if not re.fullmatch(r"[a-fA-F0-9]{64}", source_sha or "") or not isinstance(row, int) or row < 1:
        raise ValueError("invalid source hash or row")
    if not program or len(program) > 200 or status not in ("classified", "not_expense"):
        raise ValueError("invalid program or decision status")
    if (status == "classified" and category not in categories) or (status == "not_expense" and category is not None):
        raise ValueError("invalid category for decision")
    if len(rationale) < 3 or len(rationale) > 2000:
        raise ValueError("a short human rationale is required")
    if not isinstance(evidence, dict) or not any(evidence.get(key) for key in ("account", "description", "reference")):
        raise ValueError("decision evidence is missing")
    clean_evidence = {key: str(evidence.get(key) or "")[:1000] for key in ("account", "description", "reference")}
    return foundation_id, program, source_sha.lower(), row, status, category, rationale, clean_evidence


def outcome_matches(rule, status, category):
    return rule["status"] == status and rule["category_id"] == category


def matches(condition, evidence):
    if "account_exact" in condition and normalized(evidence.get("account")) != normalized(condition["account_exact"]):
        return False
    for field in ("description", "reference"):
        for fragment in condition.get(f"{field}_contains_all", []):
            if normalized(fragment) not in normalized(evidence.get(field)):
                return False
    return True


def active_rules(db, foundation_id, program):
    return [dict(row) for row in db.execute(
        "SELECT * FROM rules WHERE foundation_id=? AND program=? AND active=1 ORDER BY created_at, rule_id",
        (foundation_id, program),
    )]


def ingest_decision(db, item, decided_at=None):
    if (item.get("classification") or {}).get("status") == "needs_review":
        foundation_id = item.get("foundation_id")
        foundation_categories(foundation_id)
        program = str(item.get("program") or "").strip()
        source_sha = item.get("source_sha256")
        row = item.get("row")
        evidence = item.get("evidence") or {}
        if not program or not re.fullmatch(r"[a-fA-F0-9]{64}", source_sha or "") or not isinstance(row, int) or row < 1:
            raise ValueError("invalid retracted decision")
        key = hashlib.sha256(f"{foundation_id}:{program}:{source_sha.lower()}:{row}".encode()).hexdigest()
        timestamp = decision_time(item, decided_at)
        if timestamp < six_months_before(datetime.now(timezone.utc)).isoformat(timespec="seconds"):
            return key
        with db:
            existing = db.execute("SELECT decided_at FROM decisions WHERE decision_key=?", (key,)).fetchone()
            if existing and existing["decided_at"] > timestamp:
                return key
            db.execute("DELETE FROM decisions WHERE decision_key=?", (key,))
            for rule in active_rules(db, foundation_id, program):
                if matches(json.loads(rule["condition_json"]), evidence):
                    db.execute("UPDATE rules SET active=0 WHERE rule_id=?", (rule["rule_id"],))
        return key
    foundation_id, program, source_sha, row, status, category, rationale, evidence = validate_decision(item)
    key = hashlib.sha256(f"{foundation_id}:{program}:{source_sha}:{row}".encode()).hexdigest()
    timestamp = decision_time(item, decided_at)
    if timestamp < six_months_before(datetime.now(timezone.utc)).isoformat(timespec="seconds"):
        return key
    with db:
        existing = db.execute("SELECT decided_at FROM decisions WHERE decision_key=?", (key,)).fetchone()
        if existing and existing["decided_at"] > timestamp:
            return key
        matched = [rule for rule in active_rules(db, foundation_id, program)
                   if matches(json.loads(rule["condition_json"]), evidence)]
        if matched and all(outcome_matches(rule, status, category) for rule in matched):
            db.execute("DELETE FROM decisions WHERE decision_key=?", (key,))
            return key
        db.execute("""INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(decision_key) DO UPDATE SET decided_at=excluded.decided_at,
            status=excluded.status, category_id=excluded.category_id,
            rationale=excluded.rationale, evidence_json=excluded.evidence_json""",
            (key, foundation_id, program, source_sha, row, timestamp, status, category, rationale,
             json.dumps(evidence, ensure_ascii=False)),
        )
        # A later human correction takes precedence over a learned rule.
        for rule in matched:
            if not outcome_matches(rule, status, category):
                db.execute("UPDATE rules SET active=0 WHERE rule_id=?", (rule["rule_id"],))
    return key


def prune(db, moment=None):
    cutoff = six_months_before(moment or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    with db:
        cursor = db.execute("DELETE FROM decisions WHERE decided_at < ?", (cutoff,))
    return {"deleted": cursor.rowcount, "cutoff": cutoff}


def history(db, foundation_id=None):
    query = "SELECT * FROM decisions"
    params = ()
    if foundation_id:
        query += " WHERE foundation_id=?"
        params = (foundation_id,)
    query += " ORDER BY foundation_id, program, decided_at, decision_key"
    return [{**{key: row[key] for key in row.keys() if key != "evidence_json"},
             "evidence": json.loads(row["evidence_json"])} for row in db.execute(query, params)]


def rules(db, foundation_id=None):
    query = "SELECT * FROM rules"
    params = ()
    if foundation_id:
        query += " WHERE foundation_id=?"
        params = (foundation_id,)
    query += " ORDER BY foundation_id, program, created_at, rule_id"
    return [{**{key: row[key] for key in row.keys() if key != "condition_json"},
             "condition": json.loads(row["condition_json"])} for row in db.execute(query, params)]


def validate_condition(condition):
    if not isinstance(condition, dict) or set(condition) - {"account_exact", "description_contains_all", "reference_contains_all"}:
        raise ValueError("unsupported rule condition")
    if not (condition.get("description_contains_all") or condition.get("reference_contains_all")):
        raise ValueError("a rule needs a description or reference phrase, not just an account")
    clean = {}
    if condition.get("account_exact"):
        clean["account_exact"] = str(condition["account_exact"]).strip()
    for field in ("description", "reference"):
        key = f"{field}_contains_all"
        phrases = condition.get(key) or []
        if not isinstance(phrases, list) or len(phrases) > 5:
            raise ValueError("invalid rule phrase list")
        if phrases:
            clean[key] = [str(phrase).strip() for phrase in phrases]
            if any(len(phrase) < 3 or len(phrase) > 100 for phrase in clean[key]):
                raise ValueError("rule phrases must contain 3–100 characters")
    return clean


def add_rule(db, candidate):
    foundation_id = candidate.get("foundation_id")
    categories = foundation_categories(foundation_id)
    program = str(candidate.get("program") or "").strip()
    condition = validate_condition(candidate.get("condition"))
    status = candidate.get("status")
    category = candidate.get("category_id")
    explanation = str(candidate.get("explanation") or "").strip()
    if not program or status not in ("classified", "not_expense") or len(explanation) < 10:
        raise ValueError("program, outcome, and rule explanation are required")
    if (status == "classified" and category not in categories) or (status == "not_expense" and category is not None):
        raise ValueError("invalid rule category")
    scoped = [row for row in history(db, foundation_id) if row["program"] == program]
    covered = [row for row in scoped if matches(condition, row["evidence"])]
    if len(covered) < 2 or len({(row["source_sha256"], row["row_number"]) for row in covered}) < 2:
        raise ValueError("a rule needs at least two independent human decisions")
    if any(not outcome_matches(row, status, category) for row in covered):
        raise ValueError("matching human decisions conflict with this rule")
    canonical = json.dumps([foundation_id, program, condition, status, category], ensure_ascii=False, sort_keys=True)
    rule_id = hashlib.sha256(canonical.encode()).hexdigest()[:24]
    with db:
        db.execute("INSERT INTO rules VALUES (?,?,?,?,?,?,?,?,?,1)",
                   (rule_id, foundation_id, program, json.dumps(condition, ensure_ascii=False, sort_keys=True),
                    status, category, explanation, len(covered), now_utc()))
        db.executemany("DELETE FROM decisions WHERE decision_key=?", [(row["decision_key"],) for row in covered])
    return {"rule_id": rule_id, "support_count": len(covered), "history_deleted": len(covered)}


def suggest_report(db, report):
    foundation_id = report["foundation"]["id"]
    program = report["foundation"].get("program") or ""
    category_ids = foundation_categories(foundation_id)
    available = active_rules(db, foundation_id, program)
    suggestions = []
    for record in report["records"]:
        if record["kind"] != "transaction" or record["classification"]["status"] != "needs_review":
            continue
        evidence = evidence_from_row(report, record)
        matched = [rule for rule in available if matches(json.loads(rule["condition_json"]), evidence)]
        outcomes = {(rule["status"], rule["category_id"]) for rule in matched}
        if len(outcomes) != 1:
            continue
        status, category = next(iter(outcomes))
        if status == "classified" and category not in category_ids:
            continue
        suggestions.append({"row": record["row"], "status": status, "category_id": category,
                            "rule_ids": [rule["rule_id"] for rule in matched],
                            "explanations": [rule["explanation"] for rule in matched]})
    return suggestions


def ingest_reviewed_report(db, report):
    foundation = report["foundation"]
    count = 0
    for record in report["records"]:
        classification = record.get("classification") or {}
        if record.get("kind") != "transaction" or not classification.get("reviewed_by_user"):
            continue
        ingest_decision(db, {"foundation_id": foundation["id"], "program": foundation.get("program"),
                             "source_sha256": report["source"]["sha256"], "row": record["row"],
                             "classification": classification, "evidence": evidence_from_row(report, record)})
        count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("prune")
    for name in ("history", "rules"):
        sub.add_parser(name).add_argument("--foundation")
    sub.add_parser("add-rule").add_argument("candidate", type=Path)
    sub.add_parser("suggest-report").add_argument("report", type=Path)
    sub.add_parser("ingest-reviewed-report").add_argument("report", type=Path)
    args = parser.parse_args()
    try:
        with connect(args.db) as db:
            if args.command == "init":
                result = {"database": str(args.db.resolve())}
            elif args.command == "prune":
                result = prune(db)
            elif args.command == "history":
                result = history(db, args.foundation)
            elif args.command == "rules":
                result = rules(db, args.foundation)
            elif args.command == "add-rule":
                result = add_rule(db, json.loads(args.candidate.read_text(encoding="utf-8")))
            elif args.command == "suggest-report":
                result = suggest_report(db, json.loads(args.report.read_text(encoding="utf-8")))
            else:
                result = {"ingested": ingest_reviewed_report(db, json.loads(args.report.read_text(encoding="utf-8")))}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, sqlite3.Error, KeyError) as exc:
        print(f"memory error: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
