"""No-I/O lifecycle, cadence and bounded selection contract for the desktop worker.

Dates here are observations supplied by the existing verifier, never new evidence.
The CLI reads a bounded private snapshot and emits a no-send plan.
"""
import argparse
import copy
import hashlib
import itertools
import json
import re
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

STAGES = ("no_next_dates", "announced_unpublished", "published_future", "open")
KINDS = ("announcement", "publication", "opening", "deadline")
RUN_DAYS = (0, 1, 2, 3, 6)  # Mon–Thu, Sun; delivery schedule remains unchanged.
TZ = ZoneInfo("Asia/Jerusalem")
SCHEMA = "grant-watch/cadence-v1"


def stamp(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Timezone-aware timestamp required")
    return dt


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def defaults():
    return {s: {"interval_days": 7 if i < 2 else 3.5} for i, s in enumerate(STAGES)}


def validate_settings(settings):
    if set(settings) != set(STAGES):
        raise ValueError("Require four separately keyed cadence settings")
    for value in settings.values():
        days = value.get("interval_days")
        if isinstance(days, bool) or not isinstance(days, (int, float)) or not 1 <= days <= 365:
            raise ValueError("Cadence interval_days must be between 1 and 365")
    return settings


def safe_url(url):
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Require an HTTP(S) URL without credentials")
    return url


def wake_at(day):
    return datetime.combine(day, time(8), TZ)


def next_wake(after, days=RUN_DAYS, inclusive=False):
    local = after.astimezone(TZ)
    for offset in range(370):
        candidate = wake_at(local.date() + timedelta(days=offset))
        if candidate.weekday() in days and (candidate > after or (inclusive and candidate == after)):
            return candidate
    raise ValueError("No scheduled wake found")


def previous_wake(at):
    for offset in range(8):
        candidate = wake_at(at.astimezone(TZ).date() - timedelta(days=offset))
        if candidate.weekday() in RUN_DAYS and candidate <= at:
            return candidate
    raise ValueError("No preceding scheduled wake found")


def regular_due(key, stage, checked_at, settings):
    """Stable weekday phase avoids Thu+84h becoming Monday then Sunday (6 days)."""
    validate_settings(settings)
    interval = settings[stage]["interval_days"]
    seed = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)
    if interval == 3.5:
        pair = ((6, 2), (0, 3), (6, 3))[seed % 3]  # 3/4, 3/4, or 4/3 days
        return next_wake(checked_at, pair)
    if interval == 7:
        return next_wake(checked_at, (RUN_DAYS[seed % len(RUN_DAYS)],))
    return next_wake(checked_at + timedelta(days=interval), inclusive=True)


def fact_instant(fact, end=False):
    """A date-only boundary is a planning proxy, never an asserted cutoff time."""
    if not fact:
        return None
    value = fact.get("value")
    if not value or fact.get("certainty") != "verified":
        return None
    if "T" in value:
        return stamp(value)
    day = datetime.fromisoformat(value).date()
    return wake_at(day + timedelta(days=1)) if end else datetime.combine(day, time.min, TZ)


def effective_state(target, now):
    timeline = target.get("timeline", {})
    opening = fact_instant(timeline.get("opening", {}))
    closing = fact_instant(timeline.get("deadline", {}), end=True)
    if closing and now >= closing:
        return "no_next_dates", "deadline_elapsed_reconfirm_next_cycle"
    if target.get("published_verified") is True:
        if opening and now < opening:
            return "published_future", "published_waiting_for_opening"
        if target.get("open_verified") is True:
            entry = fact_instant(target.get("entry_deadline", {}), end=True)
            if entry and now >= entry:
                return "open", "entry_deadline_elapsed_continuation_only_or_verify"
            return "open", "open_as_last_verified"
        # Crossing a clock boundary does not authenticate application availability.
        return "published_future", "opening_or_status_requires_verification"
    if target.get("announced_verified") is True:
        return "announced_unpublished", "announcement_only"
    return "no_next_dates", "next_call_dates_unknown"


def due_state(target, now, settings):
    stage, status = effective_state(target, now)
    last = stamp(target["last_success"]) if target.get("last_success") else None
    due = regular_due(target["key"], stage, last, settings) if last else stamp(target["initial_due"])
    reason = "cadence" if last else "never_verified"
    boundaries = []
    pending_boundaries = []
    milestones = [(kind, target.get("timeline", {}).get(kind, {}))
                  for kind in ("publication", "opening", "deadline")]
    milestones += [("entry", fact) for fact in target.get("deadline_milestones", [])]
    if target.get("entry_deadline"):
        milestones.append(("entry", target["entry_deadline"]))
    for kind, fact in milestones:
        point = fact_instant(fact)
        if point is None:
            continue
        before = previous_wake(point)
        # Check once before a known transition and again at the first wake after it.
        boundary_due = before if last is None or last < before else None
        if last is None or last < point:
            boundary_due = min(boundary_due or next_wake(point, inclusive=True), next_wake(point, inclusive=True))
        if boundary_due is not None:
            pending_boundaries.append(boundary_due)
        if boundary_due is not None and (boundary_due < due or
                                        (boundary_due == due and not reason.endswith("_boundary"))):
            due, reason = boundary_due, kind + "_boundary"
        if point > now:
            boundaries.append({"kind": kind, "value": fact["value"],
                               "preceding_wake": iso(before),
                               "following_wake": iso(next_wake(point, inclusive=True)),
                               "date_only_proxy": "T" not in fact["value"]})
    cached_boundary = stamp(target["boundary_review"]) if target.get("boundary_review") else None
    if cached_boundary and (last is None or last < cached_boundary) and cached_boundary <= due:
        due, reason = cached_boundary, "milestone_boundary"
    boundary_due = bool((pending_boundaries and min(pending_boundaries) <= now) or
                        (cached_boundary and (last is None or last < cached_boundary) and cached_boundary <= now))
    # A failure may delay the next attempt, never advance last_success or erase overdue debt.
    overdue_since = due
    if target.get("retry_not_before"):
        due = max(due, stamp(target["retry_not_before"]))
    return {"stage": stage, "calendar_status": status, "next_due": iso(due),
            "due": due <= now, "overdue_since": iso(overdue_since),
            "overdue_hours": max(0, (now - overdue_since).total_seconds() / 3600),
            "reason": reason, "boundaries": boundaries,
            "boundary_due": boundary_due,
            "next_boundary_review": iso(min(pending_boundaries)) if pending_boundaries else None}


def record_observation(target, observation):
    """Append a sourced revision; never mutate another round or infer a new round key."""
    if observation.get("key") != target["key"]:
        raise ValueError("Different round requires its own stable record")
    observed_at = stamp(observation["observed_at"])
    if target.get("last_observed") and observed_at < stamp(target["last_observed"]):
        raise ValueError("Stale observation cannot overwrite newer facts")
    if target.get("last_observed") and observed_at == stamp(target["last_observed"]):
        if observation in target.get("history", []):
            return copy.deepcopy(target)
        raise ValueError("Conflicting observations at the same timestamp require review")
    result = copy.deepcopy(target)
    revision = copy.deepcopy(observation)
    for kind, fact in observation.get("timeline", {}).items():
        if kind not in KINDS:
            raise ValueError("Unknown milestone type")
        if fact.get("value") is not None:
            if not fact.get("source_url") or not fact.get("verified_at") or not fact.get("certainty"):
                raise ValueError("Dated facts require provenance, verification time and certainty")
            safe_url(fact["source_url"])
            stamp(fact["verified_at"])
            if fact["certainty"] == "verified":
                fact_instant(fact)
        result.setdefault("timeline", {})[kind] = copy.deepcopy(fact)
    for field in ("published_verified", "open_verified", "announced_verified"):
        if field in observation:
            if observation[field] is not None and type(observation[field]) is not bool:
                raise ValueError("Verification flags must be boolean or unknown")
            result[field] = observation[field]
    if "deadline_milestones" in observation:
        milestones = observation["deadline_milestones"]
        if not isinstance(milestones, list):
            raise ValueError("Deadline milestones must be a list")
        for fact in milestones:
            if not fact.get("source_url") or not fact.get("verified_at"):
                raise ValueError("Milestones require source and verification time")
            safe_url(fact["source_url"])
            stamp(fact["verified_at"])
            fact_instant(fact)
        result["deadline_milestones"] = copy.deepcopy(milestones)
        verified = sorted((f for f in milestones if f.get("certainty") == "verified" and f.get("value")), key=lambda f: f["value"])
        result["entry_deadline"] = copy.deepcopy(verified[0]) if verified else None
    result.setdefault("history", []).append(revision)
    result["last_observed"] = iso(observed_at)
    return result


def observe_url(target, candidate_url, observed_at, *, replacement_verified=False, evidence_url=None, reason=""):
    """A redirect is a candidate. Only an explicit same-program verification promotes it."""
    safe_url(candidate_url)
    stamp(observed_at)
    result = copy.deepcopy(target)
    old = target.get("official_url")
    if replacement_verified and not evidence_url:
        raise ValueError("Replacement promotion requires authoritative identity evidence")
    if evidence_url:
        safe_url(evidence_url)
    result.setdefault("url_history", []).append({"old_url": old, "candidate_url": candidate_url,
        "observed_at": observed_at, "replacement_verified": replacement_verified,
        "evidence_url": evidence_url, "reason": reason})
    result["candidate_url"] = None if replacement_verified else candidate_url
    if replacement_verified:
        result["official_url"] = candidate_url
        result["url_status"] = "verified"
    return result


def complete_attempt(target, now, settings, *, success, observation=None, error=None):
    if type(success) is not bool or (not success and observation):
        raise ValueError("A failed retrieval cannot establish new dated observations")
    result = record_observation(target, observation) if observation else copy.deepcopy(target)
    result["last_attempt"] = iso(now)
    if success:
        result["last_success"] = iso(now)
        result.pop("retry_not_before", None)
        result.pop("last_error", None)
    else:
        stage, _ = effective_state(result, now)
        retry = regular_due(result["key"], stage, now, settings)
        facts = list(result.get("timeline", {}).values()) + result.get("deadline_milestones", [])
        for fact in facts:
            point = fact_instant(fact)
            if point:
                for boundary_wake in (previous_wake(point), next_wake(point, inclusive=True)):
                    if boundary_wake > now:
                        retry = min(retry, boundary_wake)
        result["retry_not_before"] = iso(retry)
        result["last_error"] = error or "source check failed"
    result["schedule"] = due_state(result, now, settings)
    return result


def notion_text(value):
    return re.sub(r"\\([\\_`*{}\[\]()#+.!|:>-])", r"\1", value or "")


def settings_from_notion(rows):
    settings = {}
    for row in rows:
        key = notion_text(row["Key"])
        if key in settings:
            raise ValueError("Duplicate cadence setting")
        settings[key] = {"interval_days": row["Interval days"]}
    return validate_settings(settings)


def index_projection(target, now, settings, settings_pages):
    """Typed native updates for a persisted actual outcome; no connector writes here."""
    state = due_state(target, now, settings)
    properties = {"Stage": state["stage"], "Cadence": [settings_pages[state["stage"]]],
                  "Notes": target.get("last_error") or state["calendar_status"]}
    if target.get("official_url"):
        properties["Source URL"] = safe_url(target["official_url"])
        properties["URL status"] = target.get("url_status", "candidate")
    dates = {"Last success": target.get("last_success"), "Last attempt": target.get("last_attempt"),
             "Next due": state["next_due"], "Overdue since": state["overdue_since"],
             "Boundary review": state["next_boundary_review"], "Retry not before": target.get("retry_not_before")}
    for name, fact in (("Opening", target.get("timeline", {}).get("opening")),
                       ("Entry deadline", target.get("entry_deadline")),
                       ("Final deadline", target.get("timeline", {}).get("deadline"))):
        dates[name] = fact.get("value") if fact and fact.get("certainty") == "verified" else None
    for name, value in dates.items():
        properties[f"date:{name}:start"] = value
        properties[f"date:{name}:is_datetime"] = int(bool(value and "T" in value))
    for name, key in (("Published verified", "published_verified"), ("Open verified", "open_verified"),
                      ("Announced verified", "announced_verified")):
        properties[name] = "__YES__" if target.get(key) is True else "__NO__"
    return properties


def target_from_notion(row):
    """Native index properties suffice for selection; read selected page evidence later."""
    result = {"key": notion_text(row["Key"]), "program_key": notion_text(row.get("Program key")),
              "active": row.get("Active") == "__YES__", "timeline": {},
              "index_url": row.get("url"), "queue_rank": row.get("Queue rank", 0)}
    for name, key in (("Initial due", "initial_due"), ("Last success", "last_success"),
                      ("Last attempt", "last_attempt"), ("Retry not before", "retry_not_before"),
                      ("Boundary review", "boundary_review")):
        value = row.get(f"date:{name}:start")
        if value:
            result[key] = value
    for name, key in (("Published verified", "published_verified"), ("Open verified", "open_verified"),
                      ("Announced verified", "announced_verified")):
        result[key] = row.get(name) == "__YES__"
    for name, key in (("Opening", "opening"), ("Final deadline", "deadline")):
        value = row.get(f"date:{name}:start")
        if value:
            result["timeline"][key] = {"value": value, "certainty": "verified"}
    if row.get("date:Entry deadline:start"):
        result["entry_deadline"] = {"value": row["date:Entry deadline:start"], "certainty": "verified"}
    if row.get("Source URL"):
        result["official_url"] = row["Source URL"]
    registered = row.get("Registered source") or []
    result["registered_sources"] = json.loads(registered) if isinstance(registered, str) else registered
    return result


def plan_index(rows, now, settings, *, call_limit=30, source_limit=20, scan_limit=100, cursor="", remaining_work=False):
    """Caller supplies server-filtered due rows; count examined items, not only selected ones.

    Never look ahead by one: that would exceed the scan budget. `remaining_work`
    is the connector's has_more flag, not an extra row read. Rows are stable targets,
    separate round keys are never collapsed merely because their program matches.
    """
    validate_settings(settings)
    if any(type(n) is not int or n < 1 for n in (call_limit, source_limit, scan_limit)):
        raise ValueError("Positive integer limits required")
    seen, due, held, skipped, conflicts = {}, [], 0, 0, set()
    scanned = 0
    for row in itertools.islice(rows, scan_limit):
        scanned += 1
        key = row.get("key")
        if not isinstance(key, str) or not key:
            raise ValueError("Missing stable monitoring key")
        if key in seen:
            if seen[key] != row:
                conflicts.add(key)
            continue
        seen[key] = row
        if row.get("active") is not True:
            held += 1
            continue
        state = due_state(row, now, settings)
        if row.get("confirmed_excluded_same_cycle") is True:
            skipped += 1
            continue
        if state["due"]:
            due.append(dict(row, schedule=state))
    due = [r for r in due if r["key"] not in conflicts]
    # Oldest due debt first. Cursor only breaks ties; failures keep their debt but
    # retry_not_before prevents immediate reselection and permits other due work.
    due.sort(key=lambda r: (r["schedule"]["next_due"], r.get("queue_rank", 0),
                            r["key"] <= cursor, r["key"]))
    # Every due boundary has priority while it fits the unchanged cap. A fractional
    # reservation can leave a known opening stale behind ordinary discovery work.
    urgent = [r for r in due if r["schedule"]["boundary_due"]]
    reserved = urgent[:call_limit]
    reserved_keys = {r["key"] for r in reserved}
    ordered = reserved + [r for r in due if r["key"] not in reserved_keys]
    selected, selected_sources = [], set()
    for row in ordered:
        sources = selected_sources | set(row.get("registered_sources", []))
        if len(sources) <= source_limit and len(selected) < call_limit:
            selected.append(row)
            selected_sources = sources
    return {"schema": SCHEMA, "no_send": True, "scanned_index_rows": scanned,
            "scan_limit": scan_limit, "call_limit": call_limit,
            "source_limit": source_limit, "registered_sources_selected": len(selected_sources),
            "due_in_scanned_rows": len(due), "selected": selected,
            "deferred_in_scanned_rows": len(due) - len(selected),
            "unscanned_work_remains": bool(remaining_work or scanned == scan_limit),
            "coverage": "bounded_partial" if remaining_work or scanned == scan_limit or len(due) > call_limit else "scanned_slice_only",
            "held": held, "confirmed_cycle_skips": skipped, "conflicting_keys": sorted(conflicts),
            "boundary_due_in_slice": len(urgent),
            "boundary_deferred_in_slice": len([r for r in urgent if r not in selected]),
            "oldest_overdue_hours": max((r["schedule"]["overdue_hours"] for r in due), default=0),
            "proposed_cursor": selected[-1]["key"] if selected else cursor,
            "cursor_commit_rule": "Advance only after attempted outcomes are persisted; selection alone is not progress.",
            "planned_successes": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data = json.loads(args.snapshot.read_text(encoding="utf-8-sig"))
    settings = settings_from_notion(data["settings_rows"]) if "settings_rows" in data else data["settings"]
    if data.get("operation") == "complete_attempt":
        target = complete_attempt(data["target"], stamp(data["now"]), settings, **data["outcome"])
        result = {"schema": SCHEMA, "no_send": True, "notion_write_performed": False,
                  "target": target, "index_properties": index_projection(target, stamp(data["now"]), settings, data["settings_pages"])}
    else:
        if len(data["rows"]) > data.get("limits", {}).get("scan_limit", 100):
            raise ValueError("Snapshot already exceeded the bounded index-read allowance")
        rows = [target_from_notion(r) for r in data["rows"]] if data.get("native_notion_rows") else data["rows"]
        result = plan_index(rows, stamp(data["now"]), settings, **data.get("limits", {}))
    with args.output.open("x", encoding="utf-8") as out:
        json.dump(result, out, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in result.items() if k not in {"selected", "target", "index_properties"}}))


if __name__ == "__main__":
    main()
