"""Authenticated Notion cleanup adapter. Reads production; fails closed on writes.

The currently supported connector has no conditional update/fencing primitive.
No policy flag, snapshot, or Notion field can turn an unconditional update into one.
"""
import argparse
import json
from pathlib import Path
import re
import time

import cadence_contract as c
import cleanup_contract as cleanup
import notice_contract as n
from publication_adapter import ToolBridge, Hold

READ_METHODS = {"notion_self", "notion_fetch", "notion_query_view"}
MAX_PAGE_BYTES = 2 * 1024 * 1024


def entity_id(value):
    match = re.search(r"(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{32})", value)
    if not match:
        raise Hold("Expected exact native Notion entity ID")
    return match.group().replace("-", "")


def tagged_json(text, tag):
    match = re.search(r"<" + re.escape(tag) + r"(?:\s[^>]*)?>\s*([\s\S]*?)\s*</" + re.escape(tag) + r">", text)
    if not match:
        raise Hold("Missing native " + tag)
    return json.loads(match[1])


def json_blocks(text):
    values = []
    for match in re.finditer(r"(?m)^`{3,}json\s*\n", text):
        try:
            value, _ = json.JSONDecoder().raw_decode(text[match.end():].lstrip())
            if isinstance(value, dict):
                values.append(value)
        except ValueError:
            raise Hold("Malformed saved JSON block; do not guess or repair history")
    return values


class NotionStore:
    """Actual supported tools only; the host supplies unmodified tool results.

    Native readback is evidence, not compare-and-set. Production capability flags
    are code properties, never read from editable policy/Notion data.
    """
    isolation = "production"
    atomic_family_compare_exchange = False
    shared_lock_verified = False

    def __init__(self, policy, bridge):
        self.policy, self.bridge = policy, bridge
        self.started = time.monotonic()
        self.calls, self.rows_read = 0, 0
        self.cap = policy.get("max_connector_calls", 100)
        self.row_cap = policy.get("max_rows_read", 5000)
        self.timeout = policy.get("runtime_seconds", 300)
        if any(type(v) is not int or v < 1 for v in (self.cap, self.row_cap, self.timeout)):
            raise Hold("Invalid adapter budget")
        if self.cap > 1000 or self.row_cap > 10000 or self.timeout > 1800:
            raise Hold("Adapter budget exceeds staged safety ceiling")

    def call(self, method, args):
        if method not in READ_METHODS:
            raise Hold("Production Notion mutation unavailable: no atomic conditional update")
        if self.calls >= self.cap or time.monotonic() - self.started >= self.timeout:
            raise Hold("Connector/time budget exhausted; coverage unknown, no archive")
        self.calls += 1
        result = self.bridge.call(method, args)
        if len(json.dumps(result, ensure_ascii=True).encode()) > MAX_PAGE_BYTES:
            raise Hold("Connector page byte budget exceeded")
        return result

    def fetch(self, identifier):
        return self.call("notion_fetch", {"id": identifier, "link_id": self.policy["link_id"]})

    def view(self, url, source):
        text = self.fetch(url)["text"]
        state = tagged_json(text, "view")
        if entity_id(state["dataSourceUrl"]) != entity_id(source):
            raise Hold("Saved view points to a different data source")
        # Any filter could conceal a request, terminal sibling or active grant run.
        if state.get("filter") or state.get("advancedFilter", {}).get("filters"):
            raise Hold("Require an unfiltered all-state view; no hidden archive/request rows")
        return n.digest(state)

    def scan(self, view_url, maximum):
        rows, cursor, seen = [], None, set()
        while True:
            allowance = min(100, maximum-len(rows), self.row_cap-self.rows_read)
            if allowance <= 0:
                raise Hold("Native row budget reached before continuation exhaustion")
            data = dict(mode="view", view_url=view_url, page_size=allowance, archive_status="all")
            if cursor:
                data["start_cursor"] = cursor
            response = self.call("notion_query_view", {"link_id": self.policy["link_id"], "data": data})
            batch = response.get("results")
            if not isinstance(batch, list) or len(batch) > allowance:
                raise Hold("Connector did not respect bounded page size")
            self.rows_read += len(batch)
            rows.extend(batch)
            if response.get("has_more") is False:
                return rows
            cursor = response.get("next_cursor")
            if response.get("has_more") is not True or not cursor or cursor in seen:
                raise Hold("Invalid/repeated native continuation; coverage unknown")
            seen.add(cursor)

    def read(self):
        p = self.policy
        actual = self.call("notion_self", {"link_id": p["link_id"]})
        if entity_id(actual.get("workspace", {}).get("id", "")) != entity_id(p["workspace_id"]):
            raise Hold("Connected Notion workspace mismatch")
        config_page = self.fetch(p["config_url"])
        candidates = [v for v in json_blocks(config_page["text"]) if "data_cleanup" in v and "workspace_id" in v]
        if len(candidates) != 1:
            raise Hold("Require one unambiguous saved Configuration JSON block")
        config = candidates[0]
        if (entity_id(config["workspace_id"]) != entity_id(p["workspace_id"]) or
                config.get("notion_link_id") != p["link_id"] or
                entity_id(config["milestone_notices"]["ledger_data_source_url"]) != entity_id(p["ledger_source"]) or
                config["milestone_notices"]["views"]["all"] != p["ledger_view"]):
            raise Hold("Configuration differs from pinned workspace/ledger/view")
        cfg = cleanup.settings(config["data_cleanup"])
        source = tagged_json(self.fetch(p["ledger_source"])["text"], "data-source-state")
        schema = source["schema"]
        for field, kind in {"Key": "text", "Payload": "text", "State": "select", "Delivered": "date",
                            "Sample": "checkbox", "Digest run": "relation", "Publish again": "checkbox",
                            "Publication control": "text", "Original notice": "relation"}.items():
            if schema.get(field, {}).get("type") != kind:
                raise Hold("Required native field missing or changed: " + field)
        if entity_id(schema["Digest run"]["dataSourceUrl"]) != entity_id(p["runs_source"]):
            raise Hold("Digest run relation points outside the pinned Runs source")
        missing = [field for field, kind in {"Cleanup archived": "checkbox", "Cleanup marker": "text",
                    "Cleanup archived at": "date"}.items()
                   if schema.get(field, {}).get("type") != kind or schema.get(field, {}).get("readOnly")]
        self.fetch(p["runs_source"])  # Schema fetched before native view query.
        views = {p["ledger_view"]: self.view(p["ledger_view"], p["ledger_source"]),
                 p["runs_view"]: self.view(p["runs_view"], p["runs_source"])}
        rows = self.scan(p["ledger_view"], cfg["max_scan_rows"])
        run_rows = self.scan(p["runs_view"], p.get("max_run_rows", 300))
        active = [c.notion_text(r.get("Key", "")) for r in run_rows
                  if r.get("State") == "running" or r.get("Email state") == "uncertain"]
        if any(r.get("State") not in {"running", "complete", "partial", "failed", "blocked"} for r in run_rows):
            raise Hold("Unknown durable grant run state; no archive")
        run_index = {c.notion_text(r["Key"]): r for r in run_rows}
        if len(run_index) != len(run_rows):
            raise Hold("Duplicate durable grant run keys")
        manifests, manifest_errors = {}, []
        notices = n.notices_from_notion(rows)
        for key in sorted({r.get("run_key", "") for r in notices if r["state"] == "delivered" and not r.get("sample")}):
            native_run = run_index.get(key)
            if not native_run:
                manifest_errors.append(dict(run_key=key, reason="missing_linked_run"))
                continue
            result = self.fetch(native_run["url"])
            text = result["text"]
            props = tagged_json(text, "properties")
            if (entity_id(p["runs_source"]) not in text.replace("-", "") or
                    c.notion_text(props.get("Key", "")) != key or props.get("Email state") != "sent"):
                raise Hold("Fetched manifest does not belong to the exact saved sent Run")
            intents = [v["intent"] for v in json_blocks(text)
                       if isinstance(v.get("intent"), dict) and v["intent"].get("state") == "sent"
                       and v["intent"].get("run_key") == key]
            unique = {n.digest(v): v for v in intents}
            if len(unique) != 1:
                manifest_errors.append(dict(run_key=key, reason="missing_or_conflicting_sent_manifest"))
                continue
            intent = next(iter(unique.values()))
            if props.get("Receipt") != intent.get("receipt", {}).get("message_id"):
                raise Hold("Native Run receipt and sent manifest disagree")
            manifests[key] = dict(intent, url=native_run["url"])
        # Detect view/config changes during traversal; NOT an atomic snapshot proof.
        for url, expected in views.items():
            source_url = p["ledger_source"] if url == p["ledger_view"] else p["runs_source"]
            if self.view(url, source_url) != expected:
                raise Hold("Saved view changed during traversal")
        if self.fetch(p["config_url"])["text"] != config_page["text"]:
            raise Hold("Configuration changed during traversal")
        snapshot = dict(scope="milestone_notices", now=c.iso(self.bridge.now()), settings=cfg,
            native_rows=rows, runs=manifests,
            traversal=dict(has_more=False, includes_logically_archived=True,
                           all_notice_states=True, rows_read=len(rows)))
        return dict(snapshot=snapshot, revision=None, active_runs=active,
                    source=p["ledger_source"], missing_archive_fields=missing,
                    manifest_errors=manifest_errors, config_enabled=cfg["enabled"])

    def compare_exchange(self, revision, key, properties):
        # Never manufacture a precondition argument unsupported by Notion.
        raise Hold("Notion connector has no atomic family/config/run compare-and-set; zero writes")


def blockers(store, readback):
    result = []
    if not readback.get("config_enabled"):
        result.append("cleanup_disabled")
    if not store.shared_lock_verified:
        result.append("shared_production_run_lock_not_bound")
    if not store.atomic_family_compare_exchange:
        result.append("notion_atomic_family_compare_exchange_unavailable")
    if readback.get("missing_archive_fields"):
        result.append("archive_schema_not_installed")
    if readback.get("active_runs"):
        result.append("active_or_uncertain_grant_run")
    return result


def execute(operation, policy, bridge):
    store = None
    try:
        if operation not in {"plan", "archive"}:
            raise Hold("Unsupported cleanup adapter operation")
        store = NotionStore(policy, bridge)
        readback = store.read()
        result = cleanup.plan(readback["snapshot"])
        reasons = blockers(store, readback)
        result.update(adapter="supported_notion_tools", coordination_blockers=reasons,
                      active_runs=readback["active_runs"], manifest_errors=readback["manifest_errors"],
                      missing_archive_fields=readback["missing_archive_fields"],
                      snapshot_isolation="non_atomic_advisory_only", writes=0)
        if operation == "archive":
            result.update(status="blocked", reason="; ".join(reasons) or "Production activation not implemented")
        return result
    except (Hold, ValueError, KeyError, TypeError, OSError) as exc:
        return dict(status="blocked", reason=str(exc), no_send=True, production_writes=False,
                    writes=0, coverage_unknown=True)
    finally:
        # Evidence counts include schema/view/config reads, pagination and manifests.
        bridge.cleanup_metrics = dict(connector_calls=store.calls if store else 0, rows_read=store.rows_read if store else 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["plan", "archive"])
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--rpc-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; inspect before repeating any connector work")
    policy = cleanup.read_snapshot(args.policy)
    bridge = ToolBridge(args.rpc_dir, max_calls=policy.get("max_connector_calls", 100))
    result = execute(args.operation, policy, bridge)
    result.update(adapter_metrics=bridge.cleanup_metrics, adapter_receipts=bridge.receipts)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print(json.dumps({k: result[k] for k in ("status", "no_send", "writes") if k in result}))


if __name__ == "__main__":
    main()
