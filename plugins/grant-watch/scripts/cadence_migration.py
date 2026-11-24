"""Build an additive migration plan from a private, already-fetched Notion snapshot.

No connector, crawler or sender. Existing pages/keys remain unchanged; unknown dates
remain null. The caller persists the backup, checks this plan, then writes bounded rows.
"""
import argparse
import copy
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import cadence_contract as c


def unescape(value):
    return c.notion_text(value)


def date_fact(raw, review):
    fact = copy.deepcopy(raw)
    value = raw.get("date")
    if not value:
        return dict(value=None, certainty="unknown", reason="Date not established in saved evidence")
    if raw.get("time") and re.fullmatch(r"\d\d:\d\d", raw["time"]):
        try:
            zone = re.sub(r"\s+\((?:PST|PDT|EST|EDT)\)$", "", raw.get("timezone", ""))
            tz = ZoneInfo(zone)
            value = datetime.fromisoformat(value + "T" + raw["time"]).replace(tzinfo=tz).isoformat()
        except (ZoneInfoNotFoundError, ValueError):
            pass  # Date-only retains the original unknown/ambiguous timezone.
    fact.update(value=value, source_url=review.get("official_source"),
                verified_at=review.get("verified_at"), original_text=raw.get("original_date_text") or raw.get("source_text"))
    return fact


def lifecycle(review, row, now):
    facts = {kind: dict(value=None, certainty="unknown", reason="Not recorded in existing source review") for kind in c.KINDS}
    dates = review.get("deadlines", [])
    openings = [d for d in dates if re.search(r"opening|פתיח", d.get("stage", ""), re.I)]
    deadlines = [d for d in dates if d not in openings and not
                 re.search(r"clarification|not entry requirement|optional|הבהר", d.get("stage", ""), re.I)]
    confirmed = sorted([d for d in deadlines if d.get("certainty") == "verified" and d.get("date")], key=lambda d: d["date"])
    if openings:
        facts["opening"] = date_fact(openings[0], review)
    if confirmed:
        facts["deadline"] = date_fact(confirmed[-1], review)
    for kind in ("announcement", "publication"):
        explicit = review.get(kind)
        if isinstance(explicit, dict):
            facts[kind] = date_fact(explicit, review)
    call_verified = review.get("verification_flags", {}).get("funder_call_verified") is True
    # Earlier reviews predate verification_flags. Keep them conservative unless the
    # retained evidence establishes this cycle and at least one verified funder date.
    if "verification_flags" not in review:
        call_verified = review.get("evidence_verified") is True and bool(confirmed)
    opening = c.fact_instant(facts["opening"])
    flags = dict(published_verified=call_verified,
                 open_verified=call_verified and row.get("Status") == "open" and (opening is None or opening <= now),
                 announced_verified=call_verified)
    return facts, flags, [date_fact(d, review) for d in confirmed]


def date_properties(name, value):
    if not value:
        return {}
    return {f"date:{name}:start": value, f"date:{name}:is_datetime": int("T" in value)}


def build(snapshot, settings_pages, now):
    settings = c.defaults()
    by_key, by_url, by_item = {}, {}, {}
    round_targets = {}
    for entry in snapshot["rounds"]:
        row = entry["row"]
        review = entry["reviews"][-1] if entry["reviews"] else {}
        key = unescape(row["Key"])
        by_key[key] = entry
        by_url[row["url"].split("?")[0]] = entry
        for old in entry["reviews"]:
            item = old.get("item_key") or old.get("source_candidate", {}).get("cycle_key")
            if item:
                by_item.setdefault(item, []).append(entry)
        timeline, flags, deadlines = lifecycle(review, row, now)
        target = dict(key="round:"+key, round_key=key, program_key=unescape(row["Program key"]),
            name=row["Name"], kind="round", active=True, initial_due=c.iso(c.next_wake(now)),
            official_url=review.get("official_source") or row.get("Evidence URL"),
            url_status="verified" if review.get("evidence_verified") is True else "candidate",
            timeline=timeline, final_deadline=deadlines[-1] if deadlines else None,
            entry_deadline=deadlines[0] if deadlines else None, deadline_milestones=deadlines,
            additional_deadlines=copy.deepcopy(review.get("deadlines", [])),
            round_url=row["url"], queue_refs=[], last_success=review.get("verified_at"),
            history=[], url_history=[], source_review_at=review.get("verified_at"), **flags)
        # Suppression remains exact-cycle detailed review policy; the program URL
        # still needs discovery checks. No new exclusion or skip is inferred here.
        target["eligibility_skip_reference"] = review.get("confirmed_excluded_cycle")
        round_targets[key] = target
    targets = {}
    cursor = snapshot["config"]["operational_catalog"]["queue_cursor"]
    upcoming_anchor = cursor["upcoming_next"]["cycle_key"]
    unknown_anchor = cursor["unknown_next"]["program_key"]
    queue_entries = sorted(snapshot["queues"], key=lambda b: b["row"]["Name"])
    all_records = [(batch, offset, item) for batch in queue_entries for offset, item in enumerate(batch["data"]["records"])]
    rank = {}
    for kind, anchor in (("upcoming", upcoming_anchor), ("unknown", unknown_anchor)):
        keys = [item.get("cycle_key") or item["program_key"] for b, _, item in all_records if b["data"]["kind"] == kind]
        start = keys.index(anchor) if anchor in keys else 0
        for n, key in enumerate(keys[start:]+keys[:start]):
            rank[key] = n*2 + (kind == "unknown")
    for batch, offset, item in all_records:
        item_key = item.get("cycle_key") or item["program_key"]
        last = item.get("last_run", {})
        match = by_key.get(last.get("round_key")) or by_url.get((last.get("round_url") or "").split("?")[0])
        if not match:
            choices = {x["row"]["url"]: x for x in by_item.get(item_key, [])}
            if len(choices) == 1:
                match = next(iter(choices.values()))
        if match:
            round_key = unescape(match["row"]["Key"])
            target = targets.setdefault("round:"+round_key, round_targets[round_key])
        else:
            key = "candidate:"+item_key
            target = targets.setdefault(key, dict(key=key, program_key=item["program_key"],
                name=item.get("title") or item_key, kind="candidate", active=item.get("automatic_eligible") is True,
                initial_due=c.iso(c.next_wake(now)), timeline={}, published_verified=False,
                open_verified=False, announced_verified=False, queue_refs=[], history=[], url_history=[],
                official_url=(item.get("urls") or [None])[0], url_status="candidate" if item.get("urls") else "missing"))
        target.setdefault("queue_rank", rank.get(item_key, 9999))
        target["queue_rank"] = min(target["queue_rank"], rank.get(item_key, 9999))
        target["queue_refs"].append(dict(item_key=item_key, batch_url=batch["row"]["url"], offset=offset,
                                        original_urls=item.get("urls", []), original_date=item.get("date")))
        checked = last.get("at") or last.get("checked_at")
        if checked and (not target.get("last_attempt") or checked > target["last_attempt"]):
            target["last_attempt"] = checked
            if last.get("outcome") == "error":
                target["last_error"] = last.get("reason")
        if checked and last.get("outcome", "").startswith(("checked_", "official_observation")):
            if not target.get("last_success") or checked > target["last_success"]:
                target["last_success"] = checked
        if not target["active"]:
            target["hold_reason"] = item.get("withheld_reason") or "No usable source URL; manual repair required"
    for key, target in round_targets.items():
        targets.setdefault("round:"+key, target)
    pages, updates = [], []
    for target in targets.values():
        if target.get("official_url"):
            try:
                c.safe_url(target["official_url"])
            except ValueError:
                target["unusable_url"] = target.pop("official_url")
                target["active"] = False
                target["hold_reason"] = "Unsafe or unusable candidate URL; manual repair required"
                target["url_status"] = "missing"
        target.setdefault("queue_rank", 10000)
        target["schema"] = c.SCHEMA
        target["migration_key"] = "grant-watch-cadence-2026-11-24-v1"
        target["schedule"] = c.due_state(target, now, settings)
        stage = target["schedule"]["stage"]
        target["url_history"].append(dict(event="migration_baseline", at=c.iso(now),
            current_url=target.get("official_url"), source_review_at=target.get("source_review_at"),
            prior_candidate_urls=sorted({u for ref in target["queue_refs"] for u in ref["original_urls"]}),
            note="Existing verified source retained; candidate links remain provenance, not newly promoted redirects."))
        p = {"Name": target["name"], "Key": target["key"], "Program key": target["program_key"],
             "Round key": target.get("round_key", ""), "Queue key": " | ".join(r["item_key"] for r in target["queue_refs"]),
             "Kind": target["kind"], "Stage": stage, "Cadence": [settings_pages[stage]],
             "Round": [target["round_url"]] if target.get("round_url") else [],
             "Queue batch": sorted({r["batch_url"] for r in target["queue_refs"]}),
             "Active": "__YES__" if target["active"] else "__NO__", "Queue rank": target["queue_rank"],
             "Source URL": target.get("official_url"), "URL status": target["url_status"],
             "Notes": target.get("hold_reason") or target["schedule"]["calendar_status"]}
        for name, key in (("Published verified", "published_verified"), ("Open verified", "open_verified"), ("Announced verified", "announced_verified")):
            p[name] = "__YES__" if target.get(key) is True else "__NO__"
        for name, value in (("Initial due", target["initial_due"]), ("Last success", target.get("last_success")),
                            ("Last attempt", target.get("last_attempt")), ("Next due", target["schedule"]["next_due"]),
                            ("Overdue since", target["schedule"]["overdue_since"])):
            p.update(date_properties(name, value))
        for name, key in (("Opening", "opening"), ("Final deadline", "deadline")):
            fact = target["timeline"].get(key, {})
            if fact.get("certainty") == "verified":
                p.update(date_properties(name, fact.get("value")))
        if target.get("entry_deadline"):
            p.update(date_properties("Entry deadline", target["entry_deadline"]["value"]))
        boundary = target["schedule"]["next_boundary_review"]
        p.update(date_properties("Boundary review", boundary))
        pages.append(dict(properties=p, content="Additive monitoring pointer. Original queue and source reviews are preserved. Unknown dates are not inferred from imports.\n\n```json\n"+json.dumps(target, ensure_ascii=False, indent=2)+"\n```"))
        if target.get("round_url"):
            props = {"Cadence": [settings_pages[stage]], "Lifecycle state": target["schedule"]["calendar_status"],
                     "Lifecycle evidence": target.get("official_url")}
            for name, kind in (("Announcement", "announcement"), ("Publication", "publication"), ("Opening", "opening")):
                fact = target["timeline"].get(kind, {})
                if fact.get("certainty") == "verified": props.update(date_properties(name, fact.get("value")))
            final = target.get("final_deadline")
            if final and final.get("certainty") == "verified": props.update(date_properties("Final deadline", final["value"]))
            props.update(date_properties("Lifecycle verified", target.get("source_review_at")))
            updates.append(dict(url=target["round_url"], properties=props, target_key=target["key"],
                                timeline=target["timeline"], additional_deadlines=target.get("additional_deadlines", [])))
    active = [t for t in targets.values() if t["active"]]
    stage_counts = {s: sum(t["schedule"]["stage"] == s for t in active) for s in c.STAGES}
    weekly_demand = sum(7/settings[t["schedule"]["stage"]]["interval_days"] for t in active)
    capacity = snapshot["config"]["operational_catalog"]["budgets"]["calls"] * len(c.RUN_DAYS)
    return dict(migration_key="grant-watch-cadence-2026-11-24-v1", no_send=True,
        targets=list(targets.values()), pages=pages, round_updates=updates,
        counts=dict(existing_queue_records=len(all_records), existing_rounds=len(snapshot["rounds"]),
            monitoring_rows=len(targets), active=len(active), held=len(targets)-len(active), stages=stage_counts,
            minimum_target_checks_per_week=weekly_demand, current_call_capacity_per_week=capacity,
            capacity_shortfall=max(0, weekly_demand-capacity), prior_cursor_preserved=cursor))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("settings_pages", type=Path)
    parser.add_argument("--now", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(json.loads(args.snapshot.read_text(encoding="utf-8-sig")),
                   json.loads(args.settings_pages.read_text(encoding="utf-8-sig")), c.stamp(args.now))
    with args.output.open("x", encoding="utf-8") as out: json.dump(result, out, ensure_ascii=False, indent=2)
    print(json.dumps(result["counts"]))


if __name__ == "__main__": main()
