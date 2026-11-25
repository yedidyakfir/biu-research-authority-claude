"""Resumable operational coverage accounting. No network, Notion writes or email.

Called through cadence_contract.py with operation=coverage. Each output is a
write-ahead checkpoint: persist/read back before executing a returned admission.
This contract cannot increase configured budgets or prove remote persistence.
"""
import copy
import hashlib
import json
from urllib.parse import urlsplit

import cadence_contract as cadence
import pilot_contract as pilot


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def positive(value, name):
    if type(value) is not int or value < 1:
        raise ValueError(name + " must be a positive integer")
    return value


def configured_context(data):
    """Live caller uses fresh configuration verbatim; no multiplication per batch."""
    config = pilot.validate_config(data["config"], data["actual_workspace"])
    catalog, monitoring = config["operational_catalog"], config["monitoring_cadence"]
    if catalog["state"] != "ready" or monitoring["mode"] != "native_index_v1" or monitoring["state"] != "ready":
        raise ValueError("Operational catalog and native monitoring index must be ready")
    budgets, reads, pacing = catalog["budgets"], monitoring["read_budgets"], config["source_pacing"]
    return dict(run_key=data["run_key"], now=data["now"], settings=cadence.settings_from_notion(data["settings_rows"]),
                view_url=monitoring["index_view_url"], unfiltered_view_verified=data["unfiltered_view_verified"],
                fair_cursor=monitoring["cursor"].get("after_key", ""),
                limits=dict(calls=budgets["calls"], sources=budgets["sources"], requests=budgets["requests"],
                    runtime_seconds=budgets["runtime_minutes"] * 60, index_rows=reads["index_rows"],
                    metadata_rows=reads["metadata_rows"], connector_reads=reads["connector_reads"],
                    request_timeout_seconds=budgets["request_timeout_seconds"],
                    min_host_interval_seconds=pacing["min_interval_seconds"],
                    max_parallel_per_host=pacing["max_parallel_per_host"],
                    max_retries=min(budgets["max_retries"], pacing["max_retries"])))


def start(context):
    cadence.validate_settings(context["settings"])
    now = cadence.stamp(context["now"])
    if context["run_key"] != "grant-watch:" + now.astimezone(cadence.TZ).date().isoformat():
        raise ValueError("Run date and census time disagree")
    if not context["view_url"].startswith("view://") or context.get("unfiltered_view_verified") is not True:
        raise ValueError("Use a verified unfiltered operational index view")
    required = {"calls", "sources", "requests", "runtime_seconds", "index_rows", "connector_reads",
                "request_timeout_seconds", "min_host_interval_seconds", "max_parallel_per_host", "max_retries", "metadata_rows"}
    if set(context["limits"]) != required:
        raise ValueError("Require explicit configured budgets and host pacing")
    for name, value in context["limits"].items():
        if name == "max_retries":
            if type(value) is not int or not 0 <= value <= 2:
                raise ValueError("At most two retries")
        else:
            positive(value, name)
    if context["limits"]["request_timeout_seconds"] > 30 or context["limits"]["max_parallel_per_host"] != 1:
        raise ValueError("Preserve 30 second timeout and one request per host")
    return dict(schema="grant-watch/coverage-v1", context=copy.deepcopy(context), context_hash=digest(context),
                rows={}, row_errors=[], conflicts=[], pages={}, scan_complete=False, next_cursor=None,
                used=dict(index_rows=0, metadata_rows=0, connector_reads=0, calls=0, requests=0), sources=[],
                metadata_receipts={}, leases={}, outcomes={}, requests={}, host_last={}, blocked_hosts=[], last_action_at=context["now"])


def check_time(state, value):
    at, start_at = cadence.stamp(value), cadence.stamp(state["context"]["now"])
    if at < cadence.stamp(state["last_action_at"]):
        raise ValueError("Checkpoint clock cannot move backwards")
    if (at - start_at).total_seconds() >= state["context"]["limits"]["runtime_seconds"]:
        raise ValueError("Runtime budget exhausted; preserve checkpoint, do not restart budgets")
    state["last_action_at"] = value
    return at


def add_page(state, event):
    """A replay is ingestion of the same saved response, never permission to refetch."""
    token = event["read_id"]
    fingerprint = digest(event)
    if token in state["pages"]:
        if state["pages"][token]["hash"] != fingerprint:
            raise ValueError("Conflicting page receipt")
        return
    if state["scan_complete"] or event.get("cursor") != state["next_cursor"]:
        raise ValueError("Wrong continuation; do not skip or restart index pages")
    if event.get("view_url") != state["context"]["view_url"]:
        raise ValueError("Mixed views cannot establish a full census")
    rows = event["rows"]
    if not isinstance(rows, list) or len(rows) > 100 or type(event.get("has_more")) is not bool:
        raise ValueError("Require a bounded native page and explicit has_more")
    if event["has_more"] and (not event.get("next_cursor") or event["next_cursor"] == event.get("cursor") or
                              event["next_cursor"] in [p["cursor"] for p in state["pages"].values()]):
        raise ValueError("Pagination loop or missing continuation")
    limits = state["context"]["limits"]
    if (state["used"]["index_rows"] + len(rows) > limits["index_rows"] or
        state["used"]["metadata_rows"] + len(rows) > limits["metadata_rows"] or
        state["used"]["connector_reads"] + 1 > limits["connector_reads"]):
        raise ValueError("Index enumeration budget exhausted; due total remains unknown")
    check_time(state, event["at"])
    state["used"]["index_rows"] += len(rows)
    state["used"]["metadata_rows"] += len(rows)
    state["used"]["connector_reads"] += 1
    for index, native in enumerate(rows):
        try:
            row = cadence.target_from_notion(native) if event.get("native_notion_rows", True) else native
            key = row["key"]
            if not isinstance(key, str) or not key:
                raise ValueError("Missing stable key")
            if row.get("active") is True:
                cadence.due_state(row, cadence.stamp(state["context"]["now"]), state["context"]["settings"])
            if key in state["rows"] and state["rows"][key] != row:
                if key not in state["conflicts"]:
                    state["conflicts"].append(key)
            else:
                state["rows"][key] = copy.deepcopy(row)
        except (ValueError, KeyError, TypeError) as exc:
            state["row_errors"].append(dict(read_id=token, offset=index, error=str(exc)))
    state["pages"][token] = dict(hash=fingerprint, cursor=event.get("cursor"))
    state["scan_complete"] = not event["has_more"]
    state["next_cursor"] = event.get("next_cursor") if event["has_more"] else None


def census(state):
    now, settings = cadence.stamp(state["context"]["now"]), state["context"]["settings"]
    stages = {s: dict(active=0, due=0, checked=0, failed=0, identity_only=0, in_flight=0, deferred=0,
                     not_due=0, overdue=0, boundary_due=0) for s in cadence.STAGES}
    due, held, skipped = [], 0, 0
    leased = {key for keys in state["leases"].values() for key in keys}
    for key, row in state["rows"].items():
        if key in state["conflicts"]:
            continue
        if row.get("active") is not True:
            held += 1
            continue
        if row.get("confirmed_excluded_same_cycle") is True:
            skipped += 1
            continue
        schedule = cadence.due_state(row, now, settings)
        counts = stages[schedule["stage"]]
        counts["active"] += 1
        if not schedule["due"]:
            counts["not_due"] += 1
            continue
        due.append(dict(row, schedule=schedule))
        counts["due"] += 1
        counts["overdue"] += int(schedule["overdue_hours"] > 0)
        counts["boundary_due"] += int(schedule["boundary_due"])
        outcome = state["outcomes"].get(key)
        counts[outcome["result"] if outcome else "in_flight" if key in leased else "deferred"] += 1
    known = state["scan_complete"] and not state["row_errors"] and not state["conflicts"]
    checked = sum(s["checked"] for s in stages.values())
    failed = sum(s["failed"] for s in stages.values())
    identity = sum(s["identity_only"] for s in stages.values())
    in_flight = sum(s["in_flight"] for s in stages.values())
    deferred = sum(s["deferred"] for s in stages.values())
    weekly = sum(v["active"] * 7 / settings[s]["interval_days"] for s, v in stages.items())
    return dict(by_stage=stages, observed_due=len(due), total_due=len(due) if known else None,
                unknown_unscanned=None if not state["scan_complete"] else 0,
                unclassifiable_records=len(state["row_errors"]) + len(state["conflicts"]),
                scanned_rows=state["used"]["index_rows"], unique_keys=len(state["rows"]), held=held,
                confirmed_cycle_skips=skipped, enumeration_complete=state["scan_complete"], census_exact=known,
                checked=checked, failed=failed, identity_only=identity, in_flight=in_flight, deferred=deferred,
                all_due_attempted=bool(known and not deferred and not in_flight and not identity),
                verified_coverage_complete=bool(known and checked == len(due)),
                weekly_target_demand=weekly if known else None,
                weekly_call_slots=state["context"]["limits"]["calls"] * 5,
                due=due)


def lease(state, event):
    token = event["lease_id"]
    if token in state["leases"]:
        return {"keys": state["leases"][token], "execute": False, "reason": "Saved lease replay; reconcile in-flight work"}
    summary = census(state)
    if not summary["census_exact"]:
        raise ValueError("Complete an unambiguous census before dispatch; unknown rows may contain boundaries")
    check_time(state, event["at"])
    limits = state["context"]["limits"]
    remaining = limits["calls"] - state["used"]["calls"]
    leased = {k for keys in state["leases"].values() for k in keys}
    pending = [r for r in summary["due"] if r["key"] not in leased]
    selected = []
    if remaining and pending:
        batch_size = min(positive(event["batch_size"], "batch_size"), remaining)
        plan = cadence.plan_index(pending, cadence.stamp(state["context"]["now"]), state["context"]["settings"],
                                  call_limit=len(pending),
                                  source_limit=max(1, sum(len(r.get("registered_sources", [])) for r in pending)), scan_limit=len(pending),
                                  cursor=state["context"].get("fair_cursor", ""))
        sources = set(state["sources"])
        for row in plan["selected"]:
            union = sources | set(row.get("registered_sources", []))
            if len(union) <= limits["sources"]:
                selected.append(row["key"])
                sources = union
                if len(selected) == batch_size:
                    break
        state["sources"] = sorted(sources)
    state["used"]["calls"] += len(selected)
    state["leases"][token] = selected
    return {"keys": selected, "execute": bool(selected), "reason": "Persist lease before work; limits are cumulative"}


def complete(state, event):
    key = event["key"]
    if key not in {k for keys in state["leases"].values() for k in keys}:
        raise ValueError("Outcome without admitted target")
    if event.get("run_key") != state["context"]["run_key"] or event.get("read_back") is not True or not event.get("audit_url"):
        raise ValueError("Require same-run persisted outcome and explicit read-back evidence")
    if event["result"] not in {"checked", "failed", "identity_only"}:
        raise ValueError("Unknown observed outcome")
    if any(r["key"] == key and r["state"] == "in_flight" for r in state["requests"].values()):
        raise ValueError("Reconcile in-flight requests before recording a final outcome")
    if key in state["outcomes"] and state["outcomes"][key] != event:
        raise ValueError("Conflicting outcome; reconcile rather than overwrite")
    state["outcomes"][key] = copy.deepcopy(event)


def request(state, event):
    token = event["request_id"]
    if token in state["requests"]:
        return {"execute": False, "reason": "Already admitted; reconcile uncertain request before any retry"}
    leased = {k for keys in state["leases"].values() for k in keys}
    if event["key"] not in leased or event["key"] in state["outcomes"]:
        raise ValueError("Request requires an unresolved admitted target")
    host = urlsplit(cadence.safe_url(event["url"])).hostname.lower()
    if host in state["blocked_hosts"]:
        raise ValueError("Host backpressure/access block is active")
    now = check_time(state, event["at"])
    limits = state["context"]["limits"]
    if state["used"]["requests"] >= limits["requests"]:
        raise ValueError("Cumulative request budget exhausted")
    if any(r["host"] == host and r["state"] == "in_flight" for r in state["requests"].values()):
        raise ValueError("One in-flight request per host")
    if host in state["host_last"] and (now - cadence.stamp(state["host_last"][host])).total_seconds() < limits["min_host_interval_seconds"]:
        raise ValueError("Host pacing interval not elapsed")
    prior = [r for r in state["requests"].values() if r["group"] == event["group"]]
    if prior and (any(r["state"] == "in_flight" for r in prior) or len(prior) > limits["max_retries"]):
        raise ValueError("Reconcile uncertain request or retry limit reached")
    if prior and any(r["key"] != event["key"] or r["url"] != event["url"] for r in prior):
        raise ValueError("Retry group changed target or URL; redirects use new counted groups")
    state["used"]["requests"] += 1
    state["requests"][token] = dict(event, host=host, state="in_flight")
    state["host_last"][host] = event["at"]
    remaining = limits["runtime_seconds"] - (now - cadence.stamp(state["context"]["now"])).total_seconds()
    return dict(execute=True, timeout_seconds=min(limits["request_timeout_seconds"], remaining))


def finish_request(state, event):
    row = state["requests"][event["request_id"]]
    if row["state"] == "finished":
        if row["result"] != event:
            raise ValueError("Conflicting request result")
        return
    if cadence.stamp(event["at"]) < cadence.stamp(row["at"]):
        raise ValueError("Request finish precedes admission")
    # Completion can be recorded after the run limit; further admission cannot.
    row.update(state="finished", result=copy.deepcopy(event))
    if cadence.stamp(event["at"]) > cadence.stamp(state["last_action_at"]):
        state["last_action_at"] = event["at"]
    if event.get("status") in {403, 429} or event.get("access_challenge") is True:
        if row["host"] not in state["blocked_hosts"]:
            state["blocked_hosts"].append(row["host"])


def metadata(state, event):
    """Reserve non-index metadata reads before connector use, including notices."""
    token = event["read_id"]
    if token in state["metadata_receipts"]:
        if state["metadata_receipts"][token] != event:
            raise ValueError("Conflicting metadata reservation")
        return {"execute": False, "reason": "Already reserved; reconcile before repeating read"}
    check_time(state, event["at"])
    if type(event["rows"]) is not int or event["rows"] < 0:
        raise ValueError("Nonnegative metadata row reservation required")
    positive(event["reads"], "reads")
    for key, count in (("metadata_rows", event["rows"]), ("connector_reads", event["reads"])):
        if state["used"][key] + count > state["context"]["limits"][key]:
            raise ValueError("Cumulative " + key + " budget exhausted")
        state["used"][key] += count
    state["metadata_receipts"][token] = copy.deepcopy(event)
    return {"execute": True}


def export_checkpoint(state, part_chars=9000):
    """Bounded native-page payloads; publish/read back the manifest last."""
    if type(part_chars) is not int or not 256 <= part_chars <= 9000:
        raise ValueError("Native checkpoint parts must be 256..9000 characters")
    raw = json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    payloads = [raw[i:i + part_chars] for i in range(0, len(raw), part_chars)]
    return dict(manifest=dict(run_key=state["context"]["run_key"], context_hash=state["context_hash"],
                             state_hash=digest(state), parts=len(payloads)),
                parts=[dict(sequence=i, payload=part, hash=digest(part)) for i, part in enumerate(payloads)])


def restore_checkpoint(checkpoint, context_hash):
    manifest, parts = checkpoint["manifest"], checkpoint["parts"]
    if manifest["context_hash"] != context_hash or len(parts) != manifest["parts"]:
        raise ValueError("Incomplete or wrong-run checkpoint")
    if sorted(p["sequence"] for p in parts) != list(range(len(parts))):
        raise ValueError("Missing or duplicate checkpoint parts")
    if any(digest(p["payload"]) != p["hash"] or len(p["payload"]) > 9000 for p in parts):
        raise ValueError("Checkpoint part failed read-back integrity")
    state = json.loads("".join(p["payload"] for p in sorted(parts, key=lambda p: p["sequence"])))
    if (digest(state) != manifest["state_hash"] or digest(state["context"]) != context_hash or
        state["context"]["run_key"] != manifest["run_key"]):
        raise ValueError("Checkpoint manifest does not match persisted state")
    return state


def run(data):
    action = data["action"]
    if action == "start":
        context = configured_context(data) if "config" in data else data["context"]
        state, admission = start(context), None
    else:
        state = restore_checkpoint(data["checkpoint"], data["context_hash"]) if action == "resume" else copy.deepcopy(data["state"])
        if data["context_hash"] != state["context_hash"] or digest(state["context"]) != state["context_hash"]:
            raise ValueError("Run/settings/budget context changed; reconcile before continuing")
        actions = {"page": add_page, "lease": lease, "complete": complete, "request": request,
                   "finish_request": finish_request, "metadata": metadata, "status": lambda s, e: None,
                   "resume": lambda s, e: None, "checkpoint": lambda s, e: export_checkpoint(s, e.get("part_chars", 9000))}
        if action not in actions:
            raise ValueError("Unsupported coverage action")
        admission = actions[action](state, data.get("event", {}))
    summary = census(state)
    summary.pop("due")
    limits, used = state["context"]["limits"], state["used"]
    page_size = min(100, limits["index_rows"] - used["index_rows"], limits["metadata_rows"] - used["metadata_rows"])
    next_read = None if state["scan_complete"] or used["connector_reads"] >= limits["connector_reads"] or page_size < 1 else dict(
        view_url=state["context"]["view_url"], start_cursor=state["next_cursor"], page_size=page_size)
    return dict(no_send=True, notion_write_performed=False, state=state, admission=admission, coverage=summary,
                next_index_read=next_read)
