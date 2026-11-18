"""Deterministic guards for the Notion-backed Grant Watch pilot (no network I/O)."""
import argparse
import hashlib
import json
import re
from datetime import date
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()
    validate_config(json.loads(args.config.read_text(encoding="utf-8-sig")), args.workspace)
    print("Configuration checks passed; this does not verify connector access or source coverage.")


if __name__ == "__main__":
    main()
