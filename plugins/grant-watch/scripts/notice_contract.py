"""Durable milestone notices: pure state transitions, no network or sending.

Caller persists native Notion rows/checkpoints and reconciles Gmail Sent evidence.
Deadlines are intentionally outside this contract. Render through existing templates.
"""
import argparse
import base64
import copy
import hashlib
import itertools
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

import cadence_contract as cadence
import eligibility_contract as eligibility
import pilot_contract as pilot
import publication_contract as publication

STAGES = ("announcement", "publication", "opening")
KINDS = ("discovery", "approaching")
UNRESOLVED = {"intent", "uncertain"}
STAGE_LABELS = {"announcement": "הודעה על הקול הקורא", "publication": "פרסום הקול הקורא", "opening": "פתיחת ההגשה"}
SCHEMA = "grant-watch/notices-v1"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def defaults():
    return {s: {"reminder_days": 7, "catch_up_days": 7} for s in STAGES}


def validate_settings(settings):
    if set(settings) != set(STAGES):
        raise ValueError("Require independent announcement, publication and opening settings")
    for row in settings.values():
        for key, maximum in (("reminder_days", 90), ("catch_up_days", 30)):
            if type(row.get(key)) is not int or not 0 <= row[key] <= maximum:
                raise ValueError(f"{key} must be an integer from 0 to {maximum}")
    return settings


def settings_from_notion(rows):
    result = {}
    for row in rows:
        key = cadence.notion_text(row["Key"])
        if not key.startswith("notice:") or key[7:] in result:
            raise ValueError("Unexpected or duplicate notice setting")
        result[key[7:]] = {"reminder_days": row["Reminder days"], "catch_up_days": row["Catch-up days"]}
    return validate_settings(result)


def milestone_day(fact):
    value = fact["value"]
    return cadence.stamp(value).astimezone(cadence.TZ).date() if "T" in value else date.fromisoformat(value)


def eligible_fact(fact):
    if not fact or not fact.get("value"):
        return False
    # Forecast is eligible only as an explicit, source-verified official announcement.
    if not (fact.get("certainty") == "verified" or
            (fact.get("certainty") == "forecast" and fact.get("officially_announced") is True
             and fact.get("source_verified") is True)):
        return False
    cadence.safe_url(fact["source_url"])
    cadence.stamp(fact["verified_at"])
    milestone_day(fact)
    return True


def excluded(round_state):
    review = round_state.get("review", {})
    return (review.get("call_id") == round_state.get("call_id") and
            review.get("cycle") == round_state.get("cycle") and eligibility.confirmed_exclusion(review))


def timing(notice, settings):
    day = milestone_day(notice["fact"])
    rule = settings[notice["stage"]]
    expiry = datetime.combine(day + timedelta(days=rule["catch_up_days"] + 1), time.min, cadence.TZ)
    due = (cadence.stamp(notice["first_seen"]) if notice["kind"] == "discovery" else
           datetime.combine(day - timedelta(days=rule["reminder_days"]), time.min, cadence.TZ))
    return due, expiry


def ingest(round_state, notices, observation, settings, activated_at, *, baseline=False):
    """One round, partial stage updates; return additive notice upserts and checkpoint.

    Pass existing active/current-version rows only. Old terminal history stays in Notion.
    Date versions use a monotone per-stage revision, including A -> B -> A changes.
    """
    validate_settings(settings)
    if not round_state.get("key") or any(not round_state.get(k) for k in ("call_id", "cycle")):
        raise ValueError("A canonical round and explicit call/cycle identity are required")
    if observation.get("round_key") != round_state["key"]:
        raise ValueError("Different application rounds require separate checkpoints")
    observed = cadence.stamp(observation["observed_at"])
    result, rows = copy.deepcopy(round_state), copy.deepcopy(notices)
    if any(r["round_key"] != result["key"] for r in rows):
        raise ValueError("Ingest must not mix round ledgers")
    if len({r["key"] for r in rows}) != len(rows):
        raise ValueError("Duplicate notice keys require reconciliation")
    if observation.get("source_success") is not True:
        return {"round": result, "notices": rows, "ignored": "source_failure", "created": 0}
    checkpoints = result.setdefault("notice_checkpoint", {})
    if result.get("notice_observed_at") and observed < cadence.stamp(result["notice_observed_at"]):
        return {"round": result, "notices": rows, "ignored": "stale_observation", "created": 0}
    if "review" in observation:
        result["review"] = copy.deepcopy(observation["review"])
    baseline = baseline or observed < cadence.stamp(activated_at) or observation.get("origin") == "historical_import"
    created = 0
    for stage, fact in observation.get("milestones", {}).items():
        if stage not in STAGES:
            raise ValueError("Only announcement, publication and opening notices are allowed")
        old = checkpoints.get(stage, {})
        if old.get("observed_at") and observed < cadence.stamp(old["observed_at"]):
            continue
        valid = eligible_fact(fact)
        if valid and cadence.stamp(fact["verified_at"]) > observed:
            raise ValueError("Observation cannot predate its verification")
        token = digest({"value": fact.get("value"), "timezone": fact.get("timezone")}) if valid else None
        if old.get("observed_at") == cadence.iso(observed) and old.get("token") != token:
            raise ValueError("Conflicting same-time date change requires reconciliation")
        changed = not old or old.get("token") != token
        revision = old.get("revision", 0) + int(changed)
        version = digest([result["key"], stage, revision, token])
        checkpoint = dict(revision=revision, token=token, version=version, observed_at=cadence.iso(observed),
                          fact=copy.deepcopy(fact), eligible=valid,
                          first_seen=cadence.iso(observed) if changed else old["first_seen"])
        checkpoints[stage] = checkpoint
        for row in rows:
            if row["stage"] != stage:
                continue
            if row["date_version"] != version:
                row["superseded_by"] = version
                if row["state"] == "pending":
                    row.update(state="superseded", reason="date_version_changed")
            elif row["state"] == "pending":
                row["fact"] = copy.deepcopy(fact)
        if not valid:
            continue
        for kind in KINDS:
            key = digest([result["key"], stage, version, kind])
            present = next((r for r in rows if r["key"] == key), None)
            if present:
                # A corrected exclusion may re-enable an undelivered current version.
                if present.get("reason") == "confirmed_exclusion" and not excluded(result):
                    present.update(state="pending", reason="exclusion_no_longer_confirmed")
                continue
            row = dict(key=key, round_key=result["key"], stage=stage, date_version=version, kind=kind,
                       state="pending", fact=copy.deepcopy(fact), first_seen=checkpoint["first_seen"],
                       name=result.get("name", result["call_id"]), round_url=result.get("url"), sample=False)
            if baseline and (kind == "discovery" or milestone_day(fact) < cadence.stamp(activated_at).astimezone(cadence.TZ).date()):
                row.update(state="baseline", reason="historical_migration_no_retroactive_discovery")
            elif excluded(result):
                row.update(state="suppressed", reason="confirmed_exclusion")
            elif observed >= timing(row, settings)[1]:
                row.update(state="expired", reason="outside_catch_up_window")
            rows.append(row)
            created += 1
    result["notice_observed_at"] = cadence.iso(observed)
    return {"round": result, "notices": rows, "created": created, "no_send": True}


def current_notice(row, rounds):
    current = rounds.get(row["round_key"])
    checkpoint = (current or {}).get("notice_checkpoint", {}).get(row["stage"], {})
    return current, checkpoint, bool(current and checkpoint.get("eligible") and checkpoint.get("version") == row["date_version"])


def select(notices, rounds, settings, now, *, scan_limit=100, line_limit=30, remaining_work=False,
           unresolved_checked=False, unresolved_count=0, controls=None, confirmations=None,
           audience_key=None):
    """Read pending notices independently of source selection. Caller checks unresolved view first."""
    validate_settings(settings)
    if any(type(n) is not int or n < 1 for n in (scan_limit, line_limit)):
        raise ValueError("Positive selection limits required")
    rows, groups, seen, blocked = [], {}, set(), not unresolved_checked or unresolved_count > 0
    for notice in itertools.islice(notices, scan_limit):
        row = copy.deepcopy(notice)
        rows.append(row)
        if row["key"] in seen:
            raise ValueError("Duplicate pending key requires reconciliation")
        seen.add(row["key"])
        if row.get("sample"):
            continue
        if row["state"] in UNRESOLVED:
            blocked = True
            continue
        if row["state"] not in {"pending", "awaiting_confirmation"}:
            continue
        hold = publication.guard(row, controls, confirmations, audience_key, now)
        if hold:
            row["selection_hold"] = hold
            continue
        row.pop("selection_hold", None)
        row["state"] = "pending"
        current, checkpoint, valid = current_notice(row, rounds)
        if current is None:
            row["selection_hold"] = "current_round_unavailable"
            continue
        if not valid:
            row.update(state="superseded", reason="no_longer_current_eligible_date")
            continue
        if excluded(current):
            row.update(state="suppressed", reason="confirmed_exclusion")
            continue
        row["fact"] = copy.deepcopy(checkpoint["fact"])
        due, expiry = (publication.timing(row, confirmations, now) if publication.is_resend(row)
                       else timing(row, settings))
        row.update(due_at=cadence.iso(due), expires_at=cadence.iso(expiry))
        if now >= expiry:
            row.update(state="expired", reason="outside_catch_up_window")
            continue
        if now < due:
            continue
        group_key = (row["round_key"], row["stage"], row["date_version"])
        groups.setdefault(group_key, []).append(row)
    ordered = sorted(groups.values(), key=lambda g: (min(r["due_at"] for r in g), g[0]["key"]))
    selected = [] if blocked else ordered[:line_limit]
    lines = [notice_line(group, rounds[group[0]["round_key"]], now) for group in selected]
    return dict(schema=SCHEMA, no_send=True, notices=rows, lines=lines,
                alert_keys=[r["key"] for group in selected for r in group],
                scanned=len(rows), due_lines=len(ordered), selected_lines=len(lines),
                deferred_lines=len(ordered)-len(lines), unresolved_delivery_blocked=blocked,
                remaining_work=bool(remaining_work or len(rows) == scan_limit), delivered_count=0)


def notice_line(group, current, now):
    row = group[0]
    kinds = sorted({r["kind"] for r in group})
    label = "עדכון ראשון ותזכורת" if len(kinds) == 2 else ("עדכון ראשון" if kinds == ["discovery"] else "תזכורת למועד")
    fact = row["fact"]
    qualification = "מועד צפוי שהוכרז רשמית" if fact.get("certainty") == "forecast" else "מועד לפי המקור"
    if milestone_day(fact) < now.astimezone(cadence.TZ).date():
        qualification += "; עדכון שהושלם לאחר המועד"
    line = f"{label} — {STAGE_LABELS[row['stage']]}: {fact['value']} ({qualification})"
    if fact.get("timezone"):
        line += " | " + fact["timezone"]
    return dict(round_key=row["round_key"], stage=row["stage"], date_version=row["date_version"],
                kinds=kinds, alert_keys=[r["key"] for r in group], text=line,
                name=current.get("name", row["name"]), source_url=fact["source_url"],
                caveat="אין בכך אישור שההגשה פתוחה או שהזכאות המוסדית אושרה.")


def compose(report, plan):
    """Append compact plain lines to existing structured rows; no template changes."""
    result = copy.deepcopy(report)
    # Rebuild only our previous fragments when a saved report is re-prepared.
    # Otherwise a newly due reminder could leave both an old discovery line and
    # a second combined line in the same digest.
    for section in ("main", "unverified_lead", "non_eligible"):
        result[section] = [row for row in result.get(section, []) if not row.get("notice_only")]
        for row in result[section]:
            if "notice_base_summary" in row:
                row["summary"] = copy.deepcopy(row["notice_base_summary"])
    attached = set()
    for line in plan["lines"]:
        if set(line["alert_keys"]) <= attached:
            continue
        matches = [r for s in ("main", "unverified_lead", "non_eligible") for r in result.get(s, [])
                   if r.get("round_key") == line["round_key"]]
        if len(matches) > 1:
            raise ValueError("Ambiguous report round identity")
        if matches and matches[0] in result.get("non_eligible", []):
            raise ValueError("Excluded report row cannot receive eligible notices")
        if matches:
            row = matches[0]
            summary = row.get("summary", [])
            row.setdefault("notice_base_summary", copy.deepcopy(summary))
            row["summary"] = ([summary] if isinstance(summary, str) and summary else list(summary)) + [line["text"]]
            row["source_urls"] = list(dict.fromkeys(row.get("source_urls", []) + [line["source_url"]]))
        else:
            result.setdefault("unverified_lead", []).append(dict(round_key=line["round_key"], name=line["name"],
                summary=[line["text"]], official_source=line["source_url"], caveats=[line["caveat"]], notice_only=True))
        attached.update(line["alert_keys"])
    result["notice_keys"] = sorted(attached)
    return result


def body_hash(prepared):
    return hashlib.sha256(prepared["payload"]["body"]["content"].encode("utf-8")).hexdigest()


def begin_delivery(notices, rounds, plan, prepared, run_key, now, *, controls=None, confirmations=None):
    """Call only after normal pilot.prepare_email guards; persist returned intent first."""
    if plan.get("unresolved_delivery_blocked") or run_key not in prepared["subject"]:
        raise ValueError("Delivery unresolved or run identity mismatch")
    if any(r["state"] in UNRESOLVED for r in notices if not r.get("sample")):
        raise ValueError("Reconcile every prior send intent before preparing another")
    if prepared.get("cc") or prepared.get("bcc"):
        raise ValueError("Unexpected Cc/Bcc")
    rows = copy.deepcopy(notices)
    keys = set(plan["alert_keys"])
    if len(keys) != len(plan["alert_keys"]) or keys - {r["key"] for r in rows}:
        raise ValueError("Missing or duplicate notice manifest")
    for row in rows:
        if row["key"] in keys:
            current, _, valid = current_notice(row, rounds)
            hold = publication.guard(row, controls, confirmations,
                publication.audience(prepared["from_address"], [s.strip() for s in prepared["to"].split(",")]), now)
            if (row["state"] not in {"pending", "awaiting_confirmation"} or not valid or
                    excluded(current) or row.get("sample") or hold):
                raise ValueError("Notice changed before delivery; regenerate the digest")
            row.update(state="intent", run_key=run_key)
            if publication.is_resend(row):
                row["resend_confirmation"] = confirmations.match(row, now)
    intent = dict(run_key=run_key, state="intent", alert_keys=sorted(keys), created_at=cadence.iso(now),
                  subject=prepared["subject"], sender=prepared["from_address"],
                  recipients=[s.strip() for s in prepared["to"].split(",")], body_sha256=body_hash(prepared))
    intent["resend_authorizations"] = [confirmations.match(r, now) for r in rows
        if r["key"] in keys and publication.is_resend(r)]
    return {"notices": rows, "intent": intent, "no_send": True}


def prepare(notices, rounds, plan, report, config, actual_workspace, connected_sender,
            report_day, delivery_state, now, *, controls=None, confirmations=None):
    """Actual scheduled preparation entry: existing guards/renderer, then bound notice intent."""
    pilot.validate_config(config, actual_workspace)
    included = set(plan["alert_keys"])
    if (any(publication.is_resend(r) for r in notices if r["key"] in included) and
            config.get("publication_controls", {}).get("mode") != "operator_requests_v1"):
        raise ValueError("Publication controls are staged; explicit activation is required")
    if config.get("publication_controls", {}).get("mode") == "operator_requests_v1":
        required_controls = {r.get("original_key", r["key"]) for r in notices if r["key"] in included}
        if required_controls - set(controls or {}):
            raise ValueError("Fresh publication controls required for every selected notice")
    report = compose(report, plan)
    prepared = pilot.prepare_email(config, connected_sender, report_day, report, delivery_state)
    result = begin_delivery(notices, rounds, plan, prepared, pilot.run_key(report_day), now,
                            controls=controls, confirmations=confirmations)
    result.update(report=report, prepared=prepared)
    return result


def reconcile(notices, intent, outcome, now):
    """Only exact Sent read-back marks delivery. Timeouts never authorize a retry."""
    rows, journal = copy.deepcopy(notices), copy.deepcopy(intent)
    keys = set(journal["alert_keys"])
    if keys - {r["key"] for r in rows}:
        raise ValueError("Missing intent notice rows")
    kind = outcome["kind"]
    if kind == "sent":
        receipt = outcome["receipt"]
        if (receipt.get("verified_from_sent") is not True or "SENT" not in receipt.get("label_ids", []) or
            not receipt.get("message_id") or receipt.get("sender") != journal["sender"] or
            sorted(receipt.get("recipients", [])) != sorted(journal["recipients"]) or
            receipt.get("cc") or receipt.get("bcc") or receipt.get("subject") != journal["subject"] or
            receipt.get("body_sha256") != journal["body_sha256"]):
            raise ValueError("Exact Gmail Sent identity, recipients, subject and body must reconcile")
        cadence.stamp(receipt["sent_at"])
        if journal.get("receipt") and journal["receipt"]["message_id"] != receipt["message_id"]:
            raise ValueError("Conflicting sent receipts")
        for row in rows:
            if row["key"] in keys:
                if row.get("receipt") and row["receipt"]["message_id"] != receipt["message_id"]:
                    raise ValueError("Notice already has another receipt")
                row.update(state="delivered", receipt=copy.deepcopy(receipt), run_key=journal["run_key"])
        journal.update(state="sent", receipt=copy.deepcopy(receipt))
    elif kind in {"uncertain", "definitive_not_sent"}:
        if journal.get("state") == "sent" or any(r.get("receipt") for r in rows if r["key"] in keys):
            raise ValueError("Confirmed delivery cannot be undone")
        if not outcome.get("evidence"):
            raise ValueError("Record the connector outcome evidence")
        state = "uncertain" if kind == "uncertain" else "pending"
        for row in rows:
            if row["key"] in keys:
                row.update(state=state, last_delivery_outcome=kind, last_delivery_evidence=outcome["evidence"])
        journal.update(state="uncertain" if kind == "uncertain" else "not_sent", evidence=outcome["evidence"])
    else:
        raise ValueError("Unknown delivery outcome")
    journal["reconciled_at"] = cadence.iso(now)
    return {"notices": rows, "intent": journal, "no_send": True}


def properties(row, settings_pages, settings, run_url=None):
    due, expiry = timing(row, settings)
    if publication.is_resend(row):
        due = cadence.stamp(row["requested_at"])
        expiry = cadence.stamp(row["expires_at"]) if row.get("expires_at") else None
    result = {"Name": row["name"], "Key": row["key"], "Round key": row["round_key"], "Stage": row["stage"],
              "Date version": row["date_version"], "Date value": row["fact"]["value"], "Kind": row["kind"],
              "State": row["state"], "Reason": row.get("reason", ""), "Source URL": row["fact"]["source_url"],
              "Settings": [settings_pages[row["stage"]]], "Round": [row["round_url"]] if row.get("round_url") else [],
              "Sample": "__YES__" if row.get("sample") else "__NO__", "Run key": row.get("run_key", ""),
              "Receipt ID": row.get("receipt", {}).get("message_id", ""),
              "Payload": encode_record(row)}
    if publication.is_resend(row):
        result.update({"Delivery type": "resend", "Original notice": [row["original_url"]]
                       if row.get("original_url") else []})
    if run_url:
        result["Digest run"] = [run_url]
    for name, value in (("Milestone", row["fact"]["value"]), ("First seen", row["first_seen"]),
                        ("Due", cadence.iso(due)), ("Expires", cadence.iso(expiry) if expiry else None),
                        ("Delivered", row.get("receipt", {}).get("sent_at"))):
        result[f"date:{name}:start"] = value
        result[f"date:{name}:is_datetime"] = int(bool(value and "T" in value))
    return result


def encode_record(value):
    # Native rich-text query output can rewrite URLs as Markdown links. A small
    # versioned encoding preserves machine round-trips; readable fields remain native.
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    if len(raw) > 12000:
        raise ValueError("Native record too large; keep full history on bounded source pages")
    return "v1:" + base64.urlsafe_b64encode(raw).decode()


def decode_record(value):
    value = cadence.notion_text(value)
    if not value.startswith("v1:"):
        raise ValueError("Unsupported native record encoding")
    return json.loads(base64.b64decode(value[3:], altchars=b"-_", validate=True))


def round_from_notion(row):
    result = decode_record(row["Notice checkpoint"])
    if result["key"] != cadence.notion_text(row["Key"]):
        raise ValueError("Native round identity does not match notice checkpoint")
    return result


def project(round_state, notices, settings, settings_pages):
    return dict(no_send=True, round_properties={"Notice checkpoint": encode_record(round_state)},
                notice_properties=[properties(row, settings_pages, settings) for row in notices])


def notices_from_notion(rows):
    result = []
    for native in rows:
        row = decode_record(native["Payload"])
        if row["key"] != cadence.notion_text(native["Key"]) or row["state"] != native["State"]:
            raise ValueError("Native notice properties and payload disagree; reconcile before delivery")
        result.append(row)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--trusted-confirmations", type=Path,
        help="Separate private caller-verified owner evidence; never a path or approval from Notion")
    args = parser.parse_args()
    data = json.loads(args.snapshot.read_text(encoding="utf-8-sig"))
    operation = data.pop("operation")
    if any(k in data for k in ("confirmations", "trusted_confirmations", "authorization_context")):
        raise ValueError("Authorization cannot come from the ordinary/Notion snapshot")
    if args.trusted_confirmations:
        if operation not in {"select", "prepare"}:
            raise ValueError("Trusted confirmations apply only to selection/preparation")
        data["confirmations"] = publication.Confirmations(json.loads(
            args.trusted_confirmations.read_text(encoding="utf-8-sig")))
    if "settings_rows" in data:
        data["settings"] = settings_from_notion(data.pop("settings_rows"))
    if "now" in data:
        data["now"] = cadence.stamp(data["now"])
    if "native_rows" in data:
        if len(data["native_rows"]) > data.get("scan_limit", 100):
            raise ValueError("Native notice read bound exceeded")
        native = data.pop("native_rows")
        data["notices"] = notices_from_notion(native)
        if operation in {"controls", "select", "prepare"}:
            if "controls" in data:
                raise ValueError("Use one fresh native source for operator controls")
            data["controls"] = {r["key"]: publication.controls_from_notion(raw)
                for raw, r in zip(native, data["notices"]) if not publication.is_resend(r)}
        if operation == "prepare":
            data.pop("scan_limit", None)  # The native snapshot bound was checked above.
    if operation == "select" and len(data["notices"]) > data.get("scan_limit", 100):
        raise ValueError("Snapshot already exceeded pending notice read bound")
    functions = {"ingest": ingest, "select": select, "compose": compose,
                 "prepare": prepare, "reconcile": reconcile, "project": project,
                 "controls": publication.sync}
    if operation not in functions:
        raise ValueError("Unsupported no-send operation")
    result = functions[operation](**data)
    with args.output.open("x", encoding="utf-8") as out:
        json.dump(result, out, ensure_ascii=False, indent=2)
    print(json.dumps({"operation": operation, "no_send": True, "output_written": True}))


if __name__ == "__main__":
    main()
