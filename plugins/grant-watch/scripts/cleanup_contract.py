"""Staged Grant Watch retention: advisory plan and local disposable-fixture archive.

No network, send, draft, deletion, or production-write adapter. Logical archive
adds sidecar fields; canonical notice payloads, keys and receipts remain in place.
"""
import argparse
from collections import Counter
from contextlib import contextmanager
import copy
from datetime import datetime, time, timedelta, timezone
import json
from pathlib import Path

import cadence_contract as c
import notice_contract as n
import publication_contract as p

SCHEMA = "grant-watch/cleanup-v1"
ARCHIVE_FIELDS = {"Cleanup archived", "Cleanup marker", "date:Cleanup archived at:start",
                  "date:Cleanup archived at:is_datetime"}
CONTROL_FIELDS = {"Pause publication", "Publish again", "Publication control", "Request status"}
OUTSTANDING = {"pending", "intent", "uncertain", "awaiting_confirmation", "approval_pending",
               "awaiting_owner_confirmation", "requested"}
TERMINAL = {"delivered", "superseded", "suppressed", "expired", "baseline"}


def defaults():
    return dict(mode="staged_logical_archive_v1", enabled=False, retention_days=7,
                interval_months=1, month_day=1, timezone="Asia/Jerusalem", local_time="21:30",
                max_scan_rows=3000, max_archive_rows=100, runtime_minutes=30)


def settings(value):
    result = dict(defaults(), **value)
    if result["mode"] != "staged_logical_archive_v1" or result["enabled"] is not False:
        raise ValueError("Production cleanup is not enabled by this staged entry point")
    for key, lo, hi in (("retention_days", 1, 3650), ("interval_months", 1, 12),
                        ("month_day", 1, 28), ("max_scan_rows", 1, 10000),
                        ("max_archive_rows", 1, 1000), ("runtime_minutes", 1, 60)):
        if type(result[key]) is not int or not lo <= result[key] <= hi:
            raise ValueError(f"Invalid cleanup {key}")
    clock = time.fromisoformat(result["local_time"])
    if (result["timezone"] != "Asia/Jerusalem" or clock.tzinfo or clock < time(20, 1) or
            clock.hour * 60 + clock.minute + result["runtime_minutes"] >= 24 * 60):
        raise ValueError("Cleanup window must remain after 20:00 and finish before midnight Israel")
    return result


def next_monthly(after, value, anchor_month):
    """Calendar months, not 30-day intervals. Anchor is YYYY-MM, never inferred."""
    cfg = settings(value)
    anchor = datetime.strptime(anchor_month, "%Y-%m")
    anchor_index = anchor.year * 12 + anchor.month - 1
    local = after.astimezone(c.TZ)
    start = max(local.year * 12 + local.month - 1, anchor_index)
    for index in range(start, start + 25):
        if (index - anchor_index) % cfg["interval_months"]:
            continue
        year, month = divmod(index, 12)
        candidate = datetime.combine(datetime(year, month + 1, cfg["month_day"]).date(),
                                     time.fromisoformat(cfg["local_time"]), c.TZ)
        if candidate > after:
            return c.iso(candidate)
    raise ValueError("No next cleanup occurrence in bounded calendar search")


def immutable(native):
    return {k: v for k, v in native.items() if k not in ARCHIVE_FIELDS | CONTROL_FIELDS}


def marker(native, notice):
    return dict(schema=SCHEMA, key=notice["key"], round_key=notice["round_key"],
                stage=notice["stage"], date_version=notice["date_version"], kind=notice["kind"],
                receipt_sha256=n.digest(notice["receipt"]), immutable_sha256=n.digest(immutable(native)),
                attempt_key=n.digest([SCHEMA, notice["key"], notice["receipt"]["message_id"]]))


def delivery_verified(native, notice, runs):
    receipt = notice.get("receipt", {})
    run = runs.get(notice.get("run_key"), {})
    delivered = native.get("date:Delivered:start")
    relation = native.get("Digest run", [])
    if isinstance(relation, str):
        relation = json.loads(c.notion_text(relation))
    body_hash = receipt.get("body_sha256", "")
    if (not delivered or "T" not in delivered or native.get("date:Delivered:is_datetime") != 1 or
            not receipt.get("sent_at") or c.stamp(delivered) != c.stamp(receipt["sent_at"]) or
            c.notion_text(native.get("Receipt ID", "")) != receipt.get("message_id") or
            c.notion_text(native.get("Run key", "")) != notice.get("run_key") or
            run.get("state") != "sent" or run.get("run_key") != notice.get("run_key") or
            run.get("receipt") != receipt or
            notice["key"] not in run.get("alert_keys", []) or
            not receipt.get("sender") or not receipt.get("recipients") or
            not isinstance(body_hash, str) or len(body_hash) != 64 or
            any(ch not in "0123456789abcdef" for ch in body_hash.lower()) or
            not run.get("url") or relation != [run["url"]]):
        return False
    n.reconcile([notice], dict(run, alert_keys=[notice["key"]]), {"kind": "sent", "receipt": receipt},
                c.stamp(receipt["sent_at"]))
    return True


def plan(snapshot):
    cfg = settings(snapshot.get("settings", {}))
    now = c.stamp(snapshot["now"]).astimezone(timezone.utc)
    rows = snapshot["native_rows"]
    result = dict(schema=SCHEMA, no_send=True, advisory_only=True, production_writes=False,
                  status="planned", as_of=c.iso(now), scanned=min(len(rows), cfg["max_scan_rows"]),
                  supplied_rows=len(rows), unscanned=max(0, len(rows)-cfg["max_scan_rows"]),
                  eligible=[], held=[], already_archived=[], deferred=[])
    proof = snapshot.get("traversal", {})
    if (snapshot.get("scope") != "milestone_notices" or proof.get("has_more") is not False or
            proof.get("includes_logically_archived") is not True or
            proof.get("all_notice_states") is not True or result["unscanned"] or
            proof.get("rows_read") != len(rows)):
        return dict(result, status="blocked", reason="Complete bounded all-state ledger traversal required",
                    unknown_remaining=proof.get("has_more") is not False)
    try:
        notices = n.notices_from_notion(rows)
        keyed = {r["key"]: (raw, r) for raw, r in zip(rows, notices)}
        if len(keyed) != len(rows):
            raise ValueError("Duplicate notice keys require reconciliation")
        controls = {key: p.controls_from_notion(raw) for key, (raw, _) in keyed.items()}
        families = {}
        for raw, row in zip(rows, notices):
            resend = p.is_resend(row)
            parent = row["original_key"] if resend else row["key"]
            if parent not in keyed:
                raise ValueError("Missing original for resend; ledger graph incomplete")
            families.setdefault(parent, []).append((raw, row))
        for key, control in controls.items():
            active = control["checkpoint"].get("active_key")
            if active:
                if active not in keyed:
                    raise ValueError("Missing active resend request; reconcile exact key")
                child = keyed[active][1]
                if (not p.is_resend(child) or child.get("original_key") != key or
                        child.get("sequence") != control["checkpoint"]["sequence"]):
                    raise ValueError("Active request does not belong to this original and sequence")
        candidates = []
        for raw, row in zip(rows, notices):
            key = row["key"]
            reason = None
            parent = row.get("original_key", key)
            family = families[parent]
            if p.checkbox(raw.get("Sample")) or row.get("sample"):
                reason = "sample"
            elif row["state"] != "delivered":
                reason = "not_verified_delivered"
            elif any(controls[r["key"]]["requested"] or
                     (p.is_resend(r) and r["state"] not in TERMINAL) or
                     (r["state"] in OUTSTANDING) or
                     c.notion_text(v.get("Request status", "")) in OUTSTANDING
                     for v, r in family):
                reason = "outstanding_publication_request_or_delivery"
            else:
                try:
                    if not delivery_verified(raw, row, snapshot.get("runs", {})):
                        reason = "unverified_or_inconsistent_delivery"
                except (ValueError, KeyError, TypeError):
                    reason = "unverified_or_inconsistent_delivery"
            if not reason and now - c.stamp(raw["date:Delivered:start"]).astimezone(timezone.utc) <= timedelta(days=cfg["retention_days"]):
                reason = "within_retention"
            if reason:
                result["held"].append(dict(key=key, reason=reason))
                continue
            expected = marker(raw, row)
            saved = c.notion_text(raw.get("Cleanup marker", ""))
            if saved and n.decode_record(saved) != expected:
                raise ValueError("Archive marker disagrees with preserved historical record")
            if p.checkbox(raw.get("Cleanup archived")):
                if not saved or not raw.get("date:Cleanup archived at:start"):
                    raise ValueError("Archived row is missing verified marker/timestamp")
                result["already_archived"].append(key)
            else:
                candidates.append(dict(key=key, delivered=raw["date:Delivered:start"],
                                       marker=expected, precondition=n.digest(raw)))
        candidates.sort(key=lambda x: (c.stamp(x["delivered"]), x["key"]))
        result["eligible"] = candidates[:cfg["max_archive_rows"]]
        result["deferred"] = [x["key"] for x in candidates[cfg["max_archive_rows"]:]]
        result["held_by_reason"] = dict(Counter(x["reason"] for x in result["held"]))
        return result
    except (ValueError, KeyError, TypeError) as exc:
        return dict(result, status="blocked", reason=str(exc), eligible=[], deferred=[])


@contextmanager
def exclusive_lock(path):
    """Never remove another worker's lock or infer stale from elapsed time."""
    token = n.digest([str(path), datetime.now(timezone.utc).isoformat()])
    with path.open("x", encoding="utf-8") as handle:
        handle.write(token)
    try:
        yield
    finally:
        if path.read_text(encoding="utf-8") == token:
            path.unlink()


def atomic_json(path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=True, indent=2), encoding="utf-8")
    temporary.replace(path)


def read_snapshot(path):
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Snapshot byte budget exceeded; keep rows in place")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def fixture_archive(path, now, stop_after=None):
    """Actual CLI transaction, isolated local native-row fixture only.

    No executable production write path exists. Re-read after each write; stable
    markers make interrupted marker/archive writes safe to reconcile on restart.
    """
    with exclusive_lock(path.with_suffix(".worker.lock")):
        def read():
            value = read_snapshot(path)
            if (value.get("isolation") != "synthetic_disposable" or
                    not value.get("fixture_id") or
                    any(not p.checkbox(r.get("Fixture")) for r in value["native_rows"])):
                raise ValueError("Archive adapter permits synthetic disposable fixtures only")
            value["now"] = now
            return value
        snapshot = read()
        cfg = settings(snapshot.get("settings", {}))
        local = c.stamp(now).astimezone(c.TZ)
        clock = time.fromisoformat(cfg["local_time"])
        start = datetime.combine(local.date(), clock, c.TZ)
        if not start <= local < start + timedelta(minutes=cfg["runtime_minutes"]):
            raise ValueError("Outside cleanup window; no overlap or catch-up during grant processing")
        original = copy.deepcopy(snapshot)
        initial = plan(snapshot)
        if initial["status"] != "planned":
            return initial
        writes = 0
        for item in initial["eligible"]:
            current = read()
            current_plan = plan(current)
            if current_plan["status"] != "planned":
                raise ValueError(current_plan["reason"])
            fresh = next((r for r in current_plan["eligible"] if r["key"] == item["key"]), None)
            if fresh is None or fresh["marker"] != item["marker"]:
                raise ValueError("Eligibility changed before archive; keep record in place")
            raw = next(r for r in current["native_rows"] if r["Key"] == item["key"])
            if not raw.get("Cleanup marker"):
                raw["Cleanup marker"] = n.encode_record(item["marker"])
                atomic_json(path, current)
                writes += 1
            verified = read()
            raw = next(r for r in verified["native_rows"] if r["Key"] == item["key"])
            if n.decode_record(raw["Cleanup marker"]) != item["marker"]:
                raise ValueError("Marker readback failed")
            if stop_after == "marker":
                return dict(no_send=True, status="interrupted_after_marker", writes=writes)
            replan = plan(verified)
            if replan["status"] != "planned" or item["key"] not in [r["key"] for r in replan["eligible"]]:
                raise ValueError("Eligibility changed after marker; keep record in place")
            raw.update({"Cleanup archived": "__YES__", "date:Cleanup archived at:start": now,
                        "date:Cleanup archived at:is_datetime": 1})
            atomic_json(path, verified)
            writes += 1
            if stop_after == "archive":
                return dict(no_send=True, status="interrupted_after_archive", writes=writes)
            after = read()
            actual = next(r for r in after["native_rows"] if r["Key"] == item["key"])
            if not p.checkbox(actual["Cleanup archived"]) or n.digest(immutable(actual)) != item["marker"]["immutable_sha256"]:
                raise ValueError("Archive readback failed; reconcile before any retry")
        final = read()
        if (final["runs"] != original["runs"] or
                [immutable(r) for r in final["native_rows"]] != [immutable(r) for r in original["native_rows"]]):
            raise ValueError("Historical data changed; stop and reconcile")
        return dict(no_send=True, status="fixture_verified", production_writes=False, writes=writes,
                    preserved_history_sha256=n.digest(final["runs"]), final_plan=plan(final))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["plan", "fixture-archive", "schedule"])
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--now", help="Explicit fixture clock; never production permission")
    parser.add_argument("--stop-after", choices=["marker", "archive"])
    args = parser.parse_args()
    if args.output.exists() or args.snapshot.resolve() == args.output.resolve():
        parser.error("Output must be new and separate from the input")
    try:
        data = read_snapshot(args.snapshot)
        if args.operation == "fixture-archive":
            result = fixture_archive(args.snapshot, args.now or data["now"], args.stop_after)
        elif args.operation == "schedule":
            result = dict(no_send=True, next_occurrence=next_monthly(c.stamp(data["now"]),
                data.get("settings", {}), data["anchor_month"]), schedule_created=False)
        else:
            result = plan(data)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        result = dict(no_send=True, status="blocked", production_writes=False, reason=str(exc))
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print(json.dumps({k: result[k] for k in ("no_send", "status", "production_writes") if k in result}))


if __name__ == "__main__":
    main()
