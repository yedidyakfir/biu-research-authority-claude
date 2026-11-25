import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import cadence_contract as cadence
import coverage_contract as coverage


class CoverageTests(unittest.TestCase):
    def context(self, **limits):
        return dict(run_key="grant-watch:2026-11-26", now="2026-11-26T06:00:00Z", settings=cadence.defaults(),
                    view_url="view://operational-index", unfiltered_view_verified=True, fair_cursor="",
                    limits=dict(dict(calls=30, sources=20, requests=100, runtime_seconds=2700,
                        index_rows=1000, metadata_rows=1200, connector_reads=100, request_timeout_seconds=30,
                        min_host_interval_seconds=2, max_parallel_per_host=1, max_retries=2), **limits))

    def row(self, key, **kw):
        return dict(dict(key=key, program_key="same-program", active=True,
                         initial_due="2026-11-25T06:00:00Z", timeline={}), **kw)

    def cli(self, data):
        # Exercise the entry point used by the scheduled desktop skill, not an alternate mock API.
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder)/"input.json", Path(folder)/"output.json"
            original = json.dumps(dict(operation="coverage", **data))
            source.write_text(original, encoding="utf-8")
            p = subprocess.run([sys.executable, str(SCRIPTS/"cadence_contract.py"), str(source), "--output", str(output)],
                               capture_output=True, text=True)
            if p.returncode:
                raise ValueError(p.stderr)
            self.assertEqual(source.read_text(encoding="utf-8"), original)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(result["no_send"])
            self.assertFalse(result["notion_write_performed"])
            self.assertNotIn("prepared", result)
            return result

    def act(self, state, action, **event):
        return self.cli(dict(action=action, state=state, context_hash=state["context_hash"], event=event))

    def page(self, state, rows, cursor=None, next_cursor=None, read_id="page-1", more=False):
        return self.act(state, "page", rows=rows, cursor=cursor, next_cursor=next_cursor, read_id=read_id,
                        has_more=more, native_notion_rows=False, view_url=state["context"]["view_url"],
                        at="2026-11-26T06:00:00Z")

    def ready(self, rows=None, **limits):
        state = self.cli(dict(action="start", context=self.context(**limits)))["state"]
        return self.page(state, rows if rows is not None else [self.row("a")])["state"]

    def test_full_traversal_crash_resume_and_no_limit_reset(self):
        state = self.cli(dict(action="start", context=self.context()))["state"]
        for n in range(6):
            result = self.page(state, [self.row(str(i)) for i in range(n*100, (n+1)*100)],
                cursor=None if n == 0 else str(n), next_cursor=str(n+1) if n < 5 else None,
                more=n < 5, read_id=str(n))
            state = result["state"]  # serialized/reloaded by a new CLI process every page
            self.assertEqual(result["coverage"]["total_due"], 600 if n == 5 else None)
            self.assertEqual(result["coverage"]["checked"], 0)
        self.assertEqual(result["coverage"]["unknown_unscanned"], 0)
        self.assertEqual(state["used"]["index_rows"], 600)
        first = self.act(state, "lease", lease_id="batch-1", batch_size=20, at="2026-11-26T06:00:01Z")
        replay = self.act(first["state"], "lease", lease_id="batch-1", batch_size=20, at="2026-11-26T06:00:01Z")
        self.assertFalse(replay["admission"]["execute"])
        second = self.act(replay["state"], "lease", lease_id="batch-2", batch_size=20, at="2026-11-26T06:00:02Z")
        self.assertEqual(len(second["admission"]["keys"]), 10)
        third = self.act(second["state"], "lease", lease_id="batch-3", batch_size=20, at="2026-11-26T06:00:03Z")
        self.assertFalse(third["admission"]["execute"])
        self.assertEqual(third["coverage"]["deferred"], 570)
        self.assertEqual(third["coverage"]["in_flight"], 30)
        self.assertFalse(third["coverage"]["verified_coverage_complete"])

    def test_duplicate_pages_count_scan_work_and_conflicts_are_unknown(self):
        state = self.cli(dict(action="start", context=self.context(index_rows=2)))["state"]
        a = self.row("a")
        one = self.page(state, [a], more=True, next_cursor="next")
        two = self.page(one["state"], [a], cursor="next", read_id="page-2")
        self.assertEqual(two["coverage"]["scanned_rows"], 2)
        self.assertEqual(two["coverage"]["total_due"], 1)
        self.assertEqual(two["coverage"]["unique_keys"], 1)
        # A repeated immutable saved response does not advance or dispatch twice.
        self.assertEqual(two, self.page(two["state"], [a], cursor="next", read_id="page-2"))
        conflict = self.page(one["state"], [self.row("a", announced_verified=True)], cursor="next", read_id="page-2")
        self.assertIsNone(conflict["coverage"]["total_due"])
        self.assertEqual(conflict["coverage"]["unclassifiable_records"], 1)
        with self.assertRaises(ValueError):
            self.act(conflict["state"], "lease", lease_id="bad", batch_size=1, at="2026-11-26T06:00:01Z")

    def test_budget_counts_finding_items_and_rejects_skipped_page(self):
        state = self.cli(dict(action="start", context=self.context(index_rows=1)))["state"]
        one = self.page(state, [self.row("held", active=False)], more=True, next_cursor="next")
        self.assertIsNone(one["next_index_read"])
        self.assertIsNone(one["coverage"]["total_due"])
        self.assertEqual(one["coverage"]["held"], 1)
        for cursor in (None, "next"):
            with self.assertRaises(ValueError):
                self.page(one["state"], [self.row("a")], cursor=cursor, read_id="page-2")
        with self.assertRaises(ValueError):
            self.page(state, [self.row("a")], more=True, next_cursor=None)

    def test_boundary_after_old_prefix_and_independent_rounds(self):
        rows = [self.row(str(i)) for i in range(99)]
        state = self.cli(dict(action="start", context=self.context(calls=2)))["state"]
        state = self.page(state, rows, more=True, next_cursor="next")["state"]
        rounds = [self.row("round-"+k, published_verified=True, last_success="2026-11-25T06:00:00Z",
                    timeline={"opening": {"value":"2026-11-27T10:00:00+02:00", "certainty":"verified"}}) for k in ("A", "B")]
        state = self.page(state, rounds, cursor="next", read_id="page-2")["state"]
        leased = self.act(state, "lease", lease_id="first", batch_size=2, at="2026-11-26T06:00:01Z")
        self.assertEqual(set(leased["admission"]["keys"]), {"round-A", "round-B"})
        self.assertEqual(leased["coverage"]["by_stage"]["published_future"]["boundary_due"], 2)
        self.assertEqual(leased["coverage"]["deferred"], 99)

    def test_outcome_readback_not_selection_proves_coverage(self):
        state = self.ready([self.row("a"), self.row("b"), self.row("excluded", active=False)])
        state = self.act(state,"lease",lease_id="work",batch_size=2,at="2026-11-26T06:00:01Z")["state"]
        event=dict(key="a",run_key=state["context"]["run_key"],result="checked",read_back=True,audit_url="https://notion.example/a")
        with self.assertRaises(ValueError):self.act(state,"complete",**dict(event,read_back=False))
        state=self.act(state,"complete",**event)["state"]
        state=self.act(state,"complete",**event)["state"]
        result=self.act(state,"complete",**dict(event,key="b",result="failed"))
        self.assertTrue(result["coverage"]["all_due_attempted"])
        self.assertFalse(result["coverage"]["verified_coverage_complete"])
        self.assertEqual(result["coverage"]["checked"],1)
        self.assertEqual(result["coverage"]["failed"],1)
        with self.assertRaises(ValueError):self.act(result["state"],"complete",**dict(event,key="b"))

    def test_requests_pacing_uncertainty_retry_and_limits_survive_resume(self):
        state=self.ready(requests=3)
        state=self.act(state,"lease",lease_id="one",batch_size=1,at="2026-11-26T06:00:00Z")["state"]
        event=dict(request_id="r1",key="a",url="https://funder.example/call",group="call",at="2026-11-26T06:00:01Z")
        result=self.act(state,"request",**event)
        self.assertEqual(result["admission"]["timeout_seconds"],30)
        state=result["state"]
        self.assertFalse(self.act(state,"request",**event)["admission"]["execute"])
        with self.assertRaises(ValueError):self.act(state,"request",**dict(event,request_id="r2",at="2026-11-26T06:00:03Z"))
        state=self.act(state,"finish_request",request_id="r1",at="2026-11-26T06:00:02Z",status=500)["state"]
        with self.assertRaises(ValueError):self.act(state,"request",**dict(event,request_id="r2",at="2026-11-26T06:00:02Z"))
        for i in (2,3):
            state=self.act(state,"request",**dict(event,request_id="r"+str(i),at=f"2026-11-26T06:00:0{i*2}Z"))["state"]
            state=self.act(state,"finish_request",request_id="r"+str(i),at=f"2026-11-26T06:00:0{i*2+1}Z",status=500)["state"]
        with self.assertRaises(ValueError):self.act(state,"request",**dict(event,request_id="r4",at="2026-11-26T06:00:09Z"))
        self.assertEqual(state["used"]["requests"],3)

    def test_host_block_metadata_budget_context_change_and_runtime(self):
        state=self.ready(metadata_rows=2)
        state=self.act(state,"metadata",read_id="notice",rows=1,reads=1,at="2026-11-26T06:00:00Z")["state"]
        with self.assertRaises(ValueError):self.act(state,"metadata",read_id="extra",rows=1,reads=1,at="2026-11-26T06:00:00Z")
        state=self.act(state,"lease",lease_id="one",batch_size=1,at="2026-11-26T06:00:00Z")["state"]
        event=dict(request_id="r1",key="a",url="https://funder.example/call",group="call",at="2026-11-26T06:00:01Z")
        state=self.act(state,"request",**event)["state"]
        state=self.act(state,"finish_request",request_id="r1",at="2026-11-26T06:00:02Z",status=429)["state"]
        with self.assertRaises(ValueError):self.act(state,"request",**dict(event,request_id="r2",at="2026-11-26T06:00:05Z"))
        with self.assertRaises(ValueError):self.act(state,"lease",lease_id="late",batch_size=1,at="2026-11-26T06:45:00Z")
        changed=copy.deepcopy(state);changed["context"]["limits"]["calls"]=300
        with self.assertRaises(ValueError):self.act(changed,"status")

    def test_source_budget_persists_and_does_not_hide_allowed_later_work(self):
        rows=[self.row("a",registered_sources=["s1"]), self.row("b",registered_sources=["s2"]),
              self.row("c",registered_sources=["s1"])]
        state=self.ready(rows,sources=1)
        first=self.act(state,"lease",lease_id="first",batch_size=1,at="2026-11-26T06:00:01Z")
        second=self.act(first["state"],"lease",lease_id="next",batch_size=1,at="2026-11-26T06:00:02Z")
        self.assertEqual(second["admission"]["keys"],["c"])
        self.assertEqual(second["state"]["sources"],["s1"])
        self.assertEqual(second["coverage"]["deferred"],1)

    def test_malformed_rows_identity_only_and_zero_due_cannot_fabricate_success(self):
        state=self.cli(dict(action="start",context=self.context()))["state"]
        result=self.page(state,[{}])
        self.assertIsNone(result["coverage"]["total_due"])
        self.assertEqual(result["coverage"]["unclassifiable_records"],1)
        self.assertFalse(result["coverage"]["verified_coverage_complete"])
        state=self.ready()
        state=self.act(state,"lease",lease_id="first",batch_size=1,at="2026-11-26T06:00:01Z")["state"]
        result=self.act(state,"complete",key="a",run_key=state["context"]["run_key"],result="identity_only",
                        audit_url="https://notion.example/audit",read_back=True)
        self.assertFalse(result["coverage"]["all_due_attempted"])
        self.assertFalse(result["coverage"]["verified_coverage_complete"])
        self.assertEqual(result["coverage"]["identity_only"],1)
        empty=self.page(self.cli(dict(action="start",context=self.context()))["state"],[])
        self.assertTrue(empty["coverage"]["verified_coverage_complete"])
        self.assertEqual(empty["coverage"]["total_due"],0)

    def test_configured_start_keeps_exact_caps_and_refuses_wrong_workspace(self):
        ids={k:str(i+1)*32 for i,k in enumerate(("workspace_id","root_page_id","sources_database_id",
                  "rounds_database_id","events_database_id","runs_database_id"))}
        monitoring=dict(mode="native_index_v1",state="ready",settings_data_source_url="collection://settings",
            index_data_source_url="collection://index",stage_view_urls={s:"view://"+s for s in cadence.STAGES},
            read_budgets=dict(index_rows=100,metadata_rows=400,connector_reads=100),cursor=dict(after_key="a"))
        monitoring.update({k:"view://"+k for k in ("settings_view_url","index_view_url","boundary_view_url",
            "overdue_view_url","index_key_lookup_view_url","round_key_lookup_view_url")})
        config=dict(ids,workspace_verified_by_owner=True,timezone="Asia/Jerusalem",monitoring_cadence=monitoring,
            source_pacing=dict(max_parallel_per_host=1,min_interval_seconds=2,max_retries=2),
            operational_catalog=dict(state="ready",budgets=dict(sources=20,calls=30,requests=100,
                runtime_minutes=45,request_timeout_seconds=30,max_retries=2)))
        data=dict(action="start",config=config,actual_workspace=config["workspace_id"],
            settings_rows=[dict(Key=k,**{"Interval days":v["interval_days"]}) for k,v in cadence.defaults().items()],
            run_key="grant-watch:2026-11-26",now="2026-11-26T06:00:00Z",unfiltered_view_verified=True)
        result=self.cli(data)
        self.assertEqual(result["state"]["context"]["limits"]["calls"],30)
        self.assertEqual(result["state"]["context"]["limits"]["index_rows"],100)
        self.assertEqual(result["state"]["context"]["limits"]["runtime_seconds"],2700)
        with self.assertRaises(ValueError):self.cli(dict(data,actual_workspace="a"*32))

    def test_native_checkpoint_resume_preserves_admissions_and_rejects_partial_writes(self):
        state=self.ready([self.row(str(i)) for i in range(100)])
        state=self.act(state,"lease",lease_id="already-reserved",batch_size=30,at="2026-11-26T06:00:01Z")["state"]
        result=self.act(state,"checkpoint",part_chars=256)
        checkpoint=result["admission"]
        self.assertTrue(all(len(p["payload"])<=256 for p in checkpoint["parts"]))
        data=dict(action="resume",checkpoint=checkpoint,context_hash=state["context_hash"])
        resumed=self.cli(data)
        self.assertEqual(resumed["state"],state)
        replay=self.act(resumed["state"],"lease",lease_id="already-reserved",batch_size=30,at="2026-11-26T06:00:02Z")
        self.assertFalse(replay["admission"]["execute"])
        self.assertEqual(replay["state"]["used"]["calls"],30)
        for variant in (dict(checkpoint,parts=checkpoint["parts"][:-1]),
                        dict(checkpoint,parts=checkpoint["parts"][:-1]+[checkpoint["parts"][0]])):
            with self.assertRaises(ValueError):self.cli(dict(data,checkpoint=variant))


if __name__ == "__main__": unittest.main()
