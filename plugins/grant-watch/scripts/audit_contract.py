"""Bounded native-Notion audit rows, kept separate from the email digest."""
import hashlib
import json
from datetime import datetime


def audit_row(run_key, item_key, attempt, kind, name, outcome, started_at,
              finished_at, source_url="", reason=""):
    """Build properties for one observed source/grant attempt, not a planned search."""
    if kind not in {"source", "grant"}:
        raise ValueError("Audit kind must be source or grant")
    if outcome not in {"checked_changed", "checked_unchanged", "checked_no_findings",
                       "error", "skipped", "not_attempted"}:
        raise ValueError("Unknown audit outcome")
    if type(attempt) is not int or attempt < 1:
        raise ValueError("Attempt must be a positive integer")
    fields = dict(run_key=run_key, item_key=item_key, name=name)
    if any(not isinstance(v, str) or not v.strip() or len(v) > 500 for v in fields.values()):
        raise ValueError("Require bounded run/item keys and name")
    if not isinstance(source_url, str) or len(source_url) > 2000:
        raise ValueError("Source URL exceeds native property limit")
    if not isinstance(reason, str) or len(reason) > 1500:
        raise ValueError("Use a concise reason, not a raw response or log attachment")
    if outcome in {"error", "skipped", "not_attempted"} and not reason.strip():
        raise ValueError("Errors, skips and unattempted items require a reason")
    start, finish = (datetime.fromisoformat(t) for t in (started_at, finished_at))
    if start.utcoffset() is None or finish.utcoffset() is None or finish < start:
        raise ValueError("Require ordered timezone-aware audit timestamps")
    key = hashlib.sha256(json.dumps([run_key, kind, item_key, attempt],
                                   separators=(",", ":")).encode()).hexdigest()
    return {"Name": name, "Key": key, "Run key": run_key, "Item key": item_key,
            "Kind": kind, "Attempt": attempt, "Outcome": outcome,
            "Actually attempted": "__NO__" if outcome in {"skipped", "not_attempted"} else "__YES__",
            "Started": started_at, "Finished": finished_at,
            "Source URL": source_url, "Reason": reason}


def audit_batches(rows, page_size=40):
    """Small native row batches; no file artifact, attachment or email output."""
    if type(page_size) is not int or not 1 <= page_size <= 50:
        raise ValueError("Audit batch size must be 1..50")
    for i in range(0, len(rows), page_size):
        yield rows[i:i + page_size]
