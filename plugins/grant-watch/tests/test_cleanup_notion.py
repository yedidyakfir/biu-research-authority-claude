import copy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import unittest

import test_cleanup_contract as fixtures
import cleanup_notion as adapter
import cleanup_transaction as tx
import cleanup_contract as cleanup
import notice_contract as n
from publication_adapter import Hold


class MockNotionBridge:
    """Synthetic supported-tool responses; does not claim actual owner authority."""
    def __init__(self, f):
        self.f = f
        self.receipts, self.methods = [], []
        self.policy = dict(link_id="synthetic-link", workspace_id="1"*32, config_url="https://notion.example/"+"2"*32,
            ledger_source="collection://"+"3"*32, ledger_view="view://"+"4"*32,
            runs_source="collection://"+"5"*32, runs_view="view://"+"6"*32)
        p = self.policy
        self.config = dict(workspace_id=p["workspace_id"], notion_link_id=p["link_id"], data_cleanup=cleanup.defaults(),
            milestone_notices=dict(ledger_data_source_url=p["ledger_source"], views={"all": p["ledger_view"]}))
        self.schema = {k: {"type": v} for k, v in {"Key": "text", "Payload": "text", "State": "select",
            "Delivered": "date", "Sample": "checkbox", "Digest run": "relation", "Publish again": "checkbox",
            "Publication control": "text", "Original notice": "relation"}.items()}
        self.schema["Digest run"]["dataSourceUrl"] = p["runs_source"]
        self.rows = copy.deepcopy(f.data["native_rows"])
        self.runs = [{"url": f.intent["url"], "State": "partial", "Key": f.intent["run_key"],
                      "Email state": "sent", "Receipt": f.intent["receipt"]["message_id"]}]
        self.filters, self.config_fetches = {}, 0
        self.edit_config_on_readback = False
        self.repeated_cursor = False

    def now(self):
        return n.cadence.stamp(self.f.data["now"])

    def call(self, method, args):
        self.methods.append(method)
        self.receipts.append(dict(method=method))
        p = self.policy
        if method == "notion_self":
            return {"workspace": {"id": p["workspace_id"]}}
        if method == "notion_fetch":
            target = args["id"]
            if target == p["config_url"]:
                self.config_fetches += 1
                if self.edit_config_on_readback and self.config_fetches > 1:
                    self.config["data_cleanup"]["retention_days"] += 1
                return {"text": '```json\n'+json.dumps(self.config)+'\n```'}
            if target == p["ledger_source"]:
                return {"text": '<data-source-state>'+json.dumps({"schema": self.schema})+'</data-source-state>'}
            if target == p["runs_source"]:
                return {"text": 'Runs schema, synthetic fixture'}
            if target in {p["ledger_view"], p["runs_view"]}:
                source = p["ledger_source"] if target == p["ledger_view"] else p["runs_source"]
                return {"text": '<view>'+json.dumps(dict(dataSourceUrl=source,
                    advancedFilter={"filters": self.filters.get(target, [])}))+'</view>'}
            if target == self.f.intent["url"]:
                return {"text": p["runs_source"]+'\n<properties>'+json.dumps(self.runs[0])+'</properties>\n'+
                    '```json\n'+json.dumps({"intent": self.f.intent})+'\n```'}
        if method == "notion_query_view":
            data = args["data"]
            if self.repeated_cursor:
                return dict(results=[], has_more=True, next_cursor="same")
            rows = self.rows if data["view_url"] == p["ledger_view"] else self.runs
            start = int(data.get("start_cursor", "0"))
            end = start + data["page_size"]
            return dict(results=copy.deepcopy(rows[start:end]), has_more=end < len(rows), next_cursor=str(end))
        raise AssertionError("Unexpected tool, especially mutation: " + method)


class AtomicMockStore:
    """Server transaction semantics for tests ONLY. Notion has no such API."""
    isolation = "disposable_fixture"
    atomic_family_compare_exchange = True
    shared_lock_verified = True

    def __init__(self, f):
        self.snapshot = copy.deepcopy(f.data)
        self.clock = n.cadence.stamp(self.snapshot["now"])
        self.revision, self.reads, self.attempts = 0, 0, 0
        self.active_runs = []
        self.mutex = threading.Lock()
        self.before_cas = None
        self.after_cas = None
        self.corrupt_readback = False

    def now(self):
        return self.clock

    def read(self):
        with self.mutex:
            self.reads += 1
            value = dict(snapshot=copy.deepcopy(self.snapshot), revision=self.revision, active_runs=list(self.active_runs),
                         source="synthetic-ledger", missing_archive_fields=[], config_enabled=False)
            if self.corrupt_readback and self.attempts:
                value["snapshot"]["native_rows"][0]["Cleanup marker"] = "not-the-written-marker"
            return value

    def edit(self, mutation):
        with self.mutex:
            mutation(self)
            self.revision += 1

    def compare_exchange(self, revision, key, properties):
        self.attempts += 1
        if self.before_cas:
            self.before_cas(self)
        with self.mutex:
            if revision != self.revision:
                raise Hold("Atomic revision rejected concurrent edit")
            row = next(r for r in self.snapshot["native_rows"] if r["Key"] == key)
            row.update(copy.deepcopy(properties))
            self.revision += 1
        if self.after_cas:
            self.after_cas(self)


class NotionCleanupTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.CleanupTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.bridge = MockNotionBridge(self.f)
        self.store = AtomicMockStore(self.f)
        self.journal = self.f.folder / "journal"
        self.lock_path = self.f.folder / "shared-grant-worker.lock"

    def lock(self):
        return tx.SharedRunLock(self.lock_path, socket.gethostname())

    def archive(self, **kwargs):
        return tx.archive(self.store, self.lock(), self.journal, **kwargs)

    def test_supported_notion_adapter_plans_but_no_flag_can_enable_unconditional_writes(self):
        for operation in ("plan", "archive"):
            result = adapter.execute(operation, dict(self.bridge.policy, atomic_family_compare_exchange=True,
                shared_lock_verified=True, production_enabled=True), self.bridge)
            self.assertEqual(result["writes"], 0)
            self.assertIn("notion_atomic_family_compare_exchange_unavailable", result["coordination_blockers"])
            self.assertIn("shared_production_run_lock_not_bound", result["coordination_blockers"])
            self.assertEqual(len(result["eligible"]), 1)
            self.assertEqual(result["status"], "planned" if operation == "plan" else "blocked")
            self.assertTrue(set(self.bridge.methods) <= adapter.READ_METHODS)
        store = adapter.NotionStore(self.bridge.policy, self.bridge)
        with self.assertRaisesRegex(Hold, "compare-and-set"):
            store.compare_exchange("invented", self.f.f.key, {"Cleanup archived": "__YES__"})

    def test_active_grant_run_filtered_view_and_config_edit_fail_closed(self):
        self.bridge.runs[0]["State"] = "running"
        result = adapter.execute("archive", self.bridge.policy, self.bridge)
        self.assertIn("active_or_uncertain_grant_run", result["coordination_blockers"])
        self.bridge.runs[0]["State"] = "partial"
        self.bridge.filters[self.bridge.policy["ledger_view"]] = [{"property": "Cleanup archived", "value": False}]
        self.assertIn("unfiltered", adapter.execute("plan", self.bridge.policy, self.bridge)["reason"])
        self.bridge.filters = {}
        self.bridge.config_fetches = 0
        self.bridge.edit_config_on_readback = True
        self.assertIn("Configuration changed", adapter.execute("plan", self.bridge.policy, self.bridge)["reason"])

    def test_read_budgets_count_finding_and_metadata_and_bad_continuations(self):
        result = adapter.execute("plan", dict(self.bridge.policy, max_rows_read=1), self.bridge)
        self.assertTrue(result["coverage_unknown"])
        self.assertEqual(self.bridge.cleanup_metrics["rows_read"], 1)
        result = adapter.execute("plan", dict(self.bridge.policy, max_connector_calls=3), self.bridge)
        self.assertIn("budget", result["reason"])
        self.assertEqual(self.bridge.cleanup_metrics["connector_calls"], 3)
        self.bridge.repeated_cursor = True
        result = adapter.execute("plan", self.bridge.policy, self.bridge)
        self.assertIn("continuation", result["reason"])

    def test_transaction_backup_readback_repeat_and_unchanged_receipts(self):
        before = copy.deepcopy(self.store.snapshot)
        result = self.archive()
        self.assertEqual(result["status"], "fixture_transaction_verified")
        self.assertEqual(result["writes_attempted"], 2)
        self.assertEqual(self.store.snapshot["runs"], before["runs"])
        self.assertEqual(cleanup.immutable(self.store.snapshot["native_rows"][0]),
                         cleanup.immutable(before["native_rows"][0]))
        journal = json.loads(next(self.journal.glob("*.json")).read_text())
        self.assertEqual(journal["stage"], "complete")
        self.assertEqual(journal["native_backup"], before["native_rows"][0])
        self.assertEqual(self.archive()["writes_attempted"], 0)

    def test_request_in_last_read_write_gap_is_rejected_atomically(self):
        def race(store):
            store.before_cas = None
            store.edit(lambda s: s.snapshot["native_rows"][0].update({"Publish again": "__YES__"}))
        self.store.before_cas = race
        result = self.archive()
        self.assertEqual(result["status"], "blocked")
        self.assertIn("Atomic revision", result["reason"])
        self.assertNotIn("Cleanup marker", self.store.snapshot["native_rows"][0])

    def test_new_child_run_start_or_settings_change_between_marker_and_archive_is_rejected(self):
        def new_child(store):
            controls = copy.deepcopy(self.f.f.controls)
            controls[self.f.f.key]["requested"] = True
            child = self.f.f.sync(controls=controls)["notices"][1]
            store.snapshot["native_rows"].append(self.f.f.native([child], {})[0])
            store.snapshot["traversal"]["rows_read"] += 1
        for index, mutation in enumerate((
            new_child,
            lambda s: s.snapshot["native_rows"][0].update({"Publish again": "__YES__"}),
            lambda s: s.active_runs.append("synthetic-active-run"),
            lambda s: s.snapshot["settings"].update(retention_days=9),
        )):
            self.store = AtomicMockStore(self.f)
            self.journal = self.f.folder / ("race-journal-"+str(index))
            def race(store):
                if store.attempts == 2:
                    store.edit(mutation)
            self.store.before_cas = race
            result = self.archive()
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(self.store.attempts, 2)
            self.assertNotIn("Cleanup archived", self.store.snapshot["native_rows"][0])

    def test_accepted_timeout_after_each_write_reconciles_without_duplicate(self):
        for which in (1, 2):
            self.store = AtomicMockStore(self.f)
            self.journal = self.f.folder / ("timeout-"+str(which))
            def timeout(store):
                if store.attempts == which:
                    store.after_cas = None
                    raise OSError("synthetic accepted write timeout")
            self.store.after_cas = timeout
            self.assertEqual(self.archive()["status"], "blocked")
            result = self.archive()
            self.assertEqual(result["status"], "fixture_transaction_verified")
            self.assertEqual(self.store.attempts, 2)

    def test_unaccepted_uncertain_write_never_retries_from_absent_readback(self):
        def timeout(store):
            store.before_cas = None
            raise OSError("synthetic uncertain write with no matching readback")
        self.store.before_cas = timeout
        self.assertEqual(self.archive()["status"], "blocked")
        retry = self.archive()
        self.assertIn("cannot authorize retry", retry["reason"])
        self.assertEqual(self.store.attempts, 1)

    def test_missing_readback_and_history_mutation_are_never_reported_successful(self):
        self.store.corrupt_readback = True
        result = self.archive()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(self.store.attempts, 1)
        self.assertNotIn("Cleanup archived", self.store.snapshot["native_rows"][0])

    def test_history_edit_after_marker_is_preserved_and_blocks_archive(self):
        def edit(store):
            store.after_cas = None
            store.edit(lambda s: s.snapshot["native_rows"][0].update(Name="external edit"))
        self.store.after_cas = edit
        result = self.archive()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(self.store.snapshot["native_rows"][0]["Name"], "external edit")
        self.assertNotIn("Cleanup archived", self.store.snapshot["native_rows"][0])

    def test_monotonic_timeout_and_unverified_lock_block_before_mutation(self):
        self.store.shared_lock_verified = False
        self.assertEqual(self.archive()["writes_attempted"], 0)
        self.assertEqual(self.store.reads, 0)
        self.store.shared_lock_verified = True
        ticks = iter([0, 1801])
        self.assertEqual(self.archive(monotonic=lambda: next(ticks))["writes_attempted"], 0)

    @unittest.skipUnless(os.name == "nt", "Windows production lock protocol")
    def test_windows_lock_excludes_second_process_and_does_not_delete_other_lock(self):
        source = "import sys,socket;sys.path.insert(0,sys.argv[1]);from cleanup_transaction import SharedRunLock;" + \
                 "x=SharedRunLock(sys.argv[2],socket.gethostname());x.__enter__();x.__exit__()"
        cmd = [sys.executable, "-c", source, str(fixtures.fixtures.SCRIPTS), str(self.lock_path)]
        with self.lock():
            child = subprocess.run(cmd, capture_output=True, text=True)
            self.assertNotEqual(child.returncode, 0)
            self.assertIn("Shared grant lock unavailable", child.stderr)
            self.assertEqual(self.archive()["writes_attempted"], 0)
        self.assertTrue(self.lock_path.exists())
        self.assertEqual(subprocess.run(cmd, capture_output=True).returncode, 0)
        self.assertTrue(self.lock_path.exists())

    @unittest.skipUnless(os.name == "nt", "Windows/.NET lock interoperability")
    def test_dotnet_fileshare_none_interoperates_with_shared_lock(self):
        escaped = str(self.lock_path).replace("'", "''")
        command = "$ErrorActionPreference='Stop'; $h=[System.IO.File]::Open('"+escaped+"',"+\
            "[System.IO.FileMode]::OpenOrCreate,[System.IO.FileAccess]::ReadWrite,[System.IO.FileShare]::None); $h.Dispose()"
        with self.lock():
            held = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True)
            self.assertNotEqual(held.returncode, 0)
        self.assertEqual(subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True).returncode, 0)
        with self.assertRaisesRegex(Hold, "verified Windows host"):
            with tx.SharedRunLock(self.lock_path, "not-this-host"):
                pass

    def test_actual_production_cli_tool_rendezvous_cannot_write_or_take_snapshot_authority(self):
        folder = self.f.folder
        policy, output, rpc = folder/"policy.json", folder/"adapter-result.json", folder/"rpc"
        policy.write_text(json.dumps(self.bridge.policy), encoding="utf-8")
        script = fixtures.fixtures.SCRIPTS/"cleanup_notion.py"
        process = subprocess.Popen([sys.executable, "-u", "-X", "utf8", str(script), "archive", "--policy", str(policy),
            "--output", str(output), "--rpc-dir", str(rpc)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8")
        try:
            for line in process.stdout:
                if not line.startswith("GW_RPC "):
                    continue
                request_path = Path(line.strip()[7:])
                request = json.loads(request_path.read_text(encoding="utf-8"))
                self.assertIn(request["method"], adapter.READ_METHODS)
                result = self.bridge.call(request["method"], request["args"])
                reply = request_path.with_suffix(".reply.json")
                reply.write_text(json.dumps(dict(id=request["id"], method=request["method"],
                    args_sha256=request["args_sha256"], result=result)), encoding="utf-8")
                process.stdin.write(str(reply)+"\n")
                process.stdin.flush()
            self.assertEqual(process.wait(timeout=20), 0, process.stderr.read())
            actual = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual((actual["status"], actual["writes"]), ("blocked", 0))
            self.assertIn("notion_atomic_family_compare_exchange_unavailable", actual["coordination_blockers"])
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()


if __name__ == "__main__":
    unittest.main()
