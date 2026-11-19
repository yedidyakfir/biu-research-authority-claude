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


def notion_run_coverage(*, discovery_due, discovery_checked, discovery_failed,
                        call_checks, call_observations, call_failed,
                        support_checks, support_succeeded, support_failed,
                        identity_only_reviews, deferred_queue_records):
    """Summarize reconciled outcomes by type, for Notion page content only.

    Counts are distinct checked items, not HTTP requests or retry audit rows.
    The caller must classify discovery/support and call/identity outcomes from
    evidence: legacy audit Kind alone does not distinguish those categories.
    No run properties, email payload, cursor or delivery decision is produced.
    """
    counts = (discovery_due, discovery_checked, discovery_failed, call_checks,
              call_observations, call_failed, support_checks, support_succeeded,
              support_failed, identity_only_reviews, deferred_queue_records)
    if any(type(n) is not int or n < 0 for n in counts):
        raise ValueError("Coverage counts must be nonnegative integers")

    def outcomes(total, observed, failed):
        unresolved = total - observed - failed
        if unresolved < 0:
            raise ValueError("Coverage outcomes exceed their category total")
        return unresolved

    discovery_unresolved = outcomes(discovery_due, discovery_checked, discovery_failed)
    call_unresolved = outcomes(call_checks, call_observations, call_failed)
    support_unresolved = outcomes(support_checks, support_succeeded, support_failed)
    metrics = {
        "schema": "grant-watch/notion-coverage-v1",
        "discovery_sources": {"due": discovery_due, "checked": discovery_checked,
                              "failed": discovery_failed, "unclassified_outcomes": discovery_unresolved},
        "program_calls": {"attempted": call_checks, "with_observations": call_observations,
                          "failed": call_failed, "unclassified_outcomes": call_unresolved},
        "internal_support_sources": {"attempted": support_checks, "succeeded": support_succeeded,
                                     "failed": support_failed, "unclassified_outcomes": support_unresolved},
        "identity_only_reviews": identity_only_reviews,
        "deferred_queue_records": deferred_queue_records,
    }
    summary = (
        f"Discovery sources: checked {discovery_checked}/{discovery_due}; "
        f"failed {discovery_failed}; unclassified outcomes {discovery_unresolved}.\n"
        f"Program/call checks: attempted {call_checks}; with observations {call_observations}; "
        f"failed {call_failed}; unclassified outcomes {call_unresolved}.\n"
        f"Internal support sources: attempted {support_checks}; succeeded {support_succeeded}; "
        f"failed {support_failed}; unclassified outcomes {support_unresolved}.\n"
        f"Identity-only reviews: {identity_only_reviews}. "
        f"Deferred queue records: {deferred_queue_records}.\n"
        "Runs Due/Checked/Failed columns count discovery sources only, not overall checks. "
        "Observations do not establish that a call is open or eligible. "
        "Deferred queue records are not verified unique calls."
    )
    return {"metrics": metrics, "summary": summary}
