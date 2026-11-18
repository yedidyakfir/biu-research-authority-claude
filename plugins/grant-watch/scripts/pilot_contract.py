"""Deterministic guards for the Notion-backed Grant Watch pilot (no network I/O)."""
import argparse
import hashlib
import html
import json
import re
from datetime import date, datetime
from pathlib import Path
from uuid import UUID


def validate_config(config, actual_workspace):
    required = ("workspace_id", "root_page_id", "sources_database_id", "rounds_database_id", "events_database_id", "runs_database_id")
    for key in required:
        value = config.get(key, "")
        try:
            UUID(value)
        except (ValueError, TypeError, AttributeError):
            raise ValueError(f"Missing or invalid {key}") from None
    normalize = lambda value: value.replace("-", "").lower()
    if normalize(config["workspace_id"]) != normalize(actual_workspace):
        raise ValueError("Connected Notion workspace does not match approved BIU workspace")
    if config.get("workspace_verified_by_owner") is not True:
        raise ValueError("Department workspace requires owner confirmation")
    if config.get("timezone") != "Asia/Jerusalem":
        raise ValueError("Pilot timezone must be Asia/Jerusalem")
    for key in ("sources_database_id", "rounds_database_id", "events_database_id", "runs_database_id"):
        if normalize(config[key]) == normalize(config["root_page_id"]):
            raise ValueError("A database cannot be the root page")
    return config


def event_key(program, round_id, stage, kind, value):
    # Canonical JSON avoids delimiter collisions and keeps retries idempotent.
    payload = json.dumps([program, round_id, stage, kind, value], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def run_key(report_day):
    date.fromisoformat(report_day)
    return "grant-watch:" + report_day


def delivery_allowed(config, connected_sender):
    mail = config.get("email", {})
    recipients = mail.get("recipients", [])
    return bool(
        mail.get("enabled") is True
        and mail.get("owner_approved") is True
        and mail.get("sender") == connected_sender
        and isinstance(connected_sender, str)
        and re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", connected_sender)
        and isinstance(recipients, list) and recipients
        and all(isinstance(x, str) and re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", x) for x in recipients)
        and mail.get("approved_recipients") == recipients
    )



def prepare_email(config, connected_sender, report_day, digest, delivery_state):
    """Build connector arguments only; caller must persist intent before sending."""
    if not delivery_allowed(config, connected_sender):
        raise ValueError("Sender or recipient approval guard failed")
    if delivery_state != "ready":
        raise ValueError("Delivery must be ready; reconcile pending/uncertain/sent first")
    key = run_key(report_day)
    if not isinstance(digest, str) or not digest.strip():
        raise ValueError("A persisted report is required")
    return {
        "from_address": connected_sender,
        "to": ", ".join(config["email"]["approved_recipients"]),
        "subject": f"BIU Grant Watch | {report_day} | {key}",
        "payload": {
            "mime_type": "text/html",
            "charset": "UTF-8",
            "body": {"content": "<h2>BIU Grant Watch</h2><p>Run: "
                     + html.escape(key) + "</p><div style='white-space:pre-wrap'>"
                     + html.escape(digest) + "</div>"},
        },
    }


def run_summary(state, started_at, updated_at, due, checked, new_findings, failures=0):
    """Report observed coverage, never equate an interrupted run with an empty run."""
    if state not in {"running", "complete", "partial", "failed", "blocked"}:
        raise ValueError("Unknown run state")
    times = [datetime.fromisoformat(t) for t in (started_at, updated_at)]
    if any(t.tzinfo is None or t.utcoffset() is None for t in times) or times[1] < times[0]:
        raise ValueError("Require ordered, timezone-aware timestamps")
    if any(type(n) is not int or n < 0 for n in (due, checked, new_findings, failures)):
        raise ValueError("Counts must be nonnegative integers")
    if checked + failures > due:
        raise ValueError("Source counts exceed due coverage")
    if state == "complete" and (failures or checked != due):
        raise ValueError("Incomplete coverage cannot be reported as complete")
    if state != "complete":
        outcome = f"{state.upper()}: incomplete; findings so far: {new_findings}"
    elif due == 0:
        outcome = f"COMPLETE: no sources due; new calendar events: {new_findings}"
    elif new_findings:
        outcome = f"COMPLETE: {new_findings} new findings"
    else:
        outcome = "COMPLETE: no new findings in checked sources"
    return (f"{outcome} | sources {checked}/{due}, failed {failures}"
            f" | started {started_at} | updated {updated_at}")


def entry_status(mandatory_preproposal, deadline_passed, prior_stage_satisfied):
    """Inputs are source-verified facts, not guesses from a future final deadline."""
    if any(v is not True and v is not False and v is not None
           for v in (mandatory_preproposal, deadline_passed, prior_stage_satisfied)):
        raise ValueError("Stage facts must be true, false, or unknown")
    if mandatory_preproposal is None:
        return "verify_prerequisites"
    if not mandatory_preproposal:
        return "no_mandatory_preproposal"
    if prior_stage_satisfied is True:
        return "continuing_applicant_only_check_final_stage"
    if deadline_passed is True:
        return "closed_to_new_applicants"
    if deadline_passed is None:
        return "verify_prerequisites"
    return "preproposal_stage_open_check_remaining_conditions"


def institution_status(current_eligible, evidence_verified, criteria_changed=False):
    """Current eligibility requires current evidence, never historical carry-over."""
    if current_eligible is not True and current_eligible is not False and current_eligible is not None:
        raise ValueError("Eligibility must be true, false, or unknown")
    if type(evidence_verified) is not bool or type(criteria_changed) is not bool:
        raise ValueError("Evidence and change flags must be boolean")
    if not evidence_verified or current_eligible is None:
        return "review_changed_criteria" if criteria_changed else "verification_pending"
    return "eligible_current_evidence" if current_eligible else "ineligible_current_evidence"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()
    validate_config(json.loads(args.config.read_text(encoding="utf-8-sig")), args.workspace)
    print("Configuration checks passed; this does not verify connector access or source coverage.")


if __name__ == "__main__":
    main()
