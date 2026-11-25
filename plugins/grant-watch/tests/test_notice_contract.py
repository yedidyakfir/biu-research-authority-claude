import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import notice_contract as n


class NoticeTests(unittest.TestCase):
    def setUp(self):
        self.now = n.cadence.stamp("2026-11-24T08:00:00Z")
        self.settings = n.defaults()
        self.round = dict(key="program/round-A", call_id="call-A", cycle="2026-A", name="Example <grant>",
                          url="https://notion.example/round-A")

    def fact(self, value="2026-12-10", **extra):
        return dict(dict(value=value, certainty="verified", verified_at="2026-11-24T07:00:00Z",
                         source_url="https://funder.example/call?a=1&b=2", timezone="Asia/Jerusalem"), **extra)

    def ingest(self, milestones, state=None, rows=None, at="2026-11-24T08:00:00Z", **kwargs):
        return n.ingest(state or self.round, rows or [], dict(round_key=(state or self.round)["key"],
            observed_at=at, source_success=True, milestones=milestones), self.settings,
            "2026-11-20T06:00:00Z", **kwargs)

    def select(self, state, now=None, **kwargs):
        return n.select(state["notices"], {state["round"]["key"]: state["round"]}, self.settings,
                        now or self.now, unresolved_checked=True, **kwargs)

    def config(self):
        keys = ("workspace_id", "root_page_id", "sources_database_id", "rounds_database_id", "events_database_id", "runs_database_id")
        return dict({key: str(i+1)*32 for i, key in enumerate(keys)}, workspace_verified_by_owner=True,
                    timezone="Asia/Jerusalem", email=dict(enabled=True, owner_approved=True,
                    sender="owner@example.org", recipients=["admin@example.org"], approved_recipients=["admin@example.org"]))

    def prepare(self, state, plan=None):
        config = self.config()
        return n.prepare(state["notices"], {state["round"]["key"]: state["round"]}, plan or self.select(state),
                         dict(state="partial", summary="Bounded source coverage", main=[]), config,
                         config["workspace_id"], "owner@example.org", "2026-11-24", "ready", self.now)

    def receipt(self, intent, **changes):
        return dict(dict(verified_from_sent=True, label_ids=["SENT"], message_id="synthetic-message",
                         sender=intent["sender"], recipients=intent["recipients"], subject=intent["subject"],
                         body_sha256=intent["body_sha256"], sent_at="2026-11-24T08:10:00Z"), **changes)

    def test_each_stage_has_its_own_two_kinds_not_two_per_round(self):
        state = self.ingest({stage: self.fact("2026-11-28") for stage in n.STAGES})
        self.assertEqual(state["created"], 6)
        plan = self.select(state)
        self.assertEqual(len(plan["lines"]), 3)
        self.assertEqual(len(plan["alert_keys"]), 6)
        self.assertTrue(all(len(line["kinds"]) == 2 for line in plan["lines"]))
        self.assertEqual(self.ingest({stage: self.fact("2026-11-28") for stage in n.STAGES},
                                    state["round"], state["notices"])["created"], 0)

    def test_discovery_then_later_reminder_receipt_for_each(self):
        state = self.ingest({"opening": self.fact()})
        first = self.prepare(state)
        self.assertEqual(len(first["intent"]["alert_keys"]), 1)
        sent = n.reconcile(first["notices"], first["intent"],
                           dict(kind="sent", receipt=self.receipt(first["intent"])), self.now)
        state["notices"] = sent["notices"]
        self.assertEqual(self.select(state)["lines"], [])
        later = self.select(state, n.cadence.stamp("2026-12-03T06:00:00Z"))
        self.assertEqual(later["lines"][0]["kinds"], ["approaching"])

    def test_coincident_line_reconciles_both_and_keeps_renderer_escaping(self):
        state = self.ingest({"publication": self.fact("2026-11-28")})
        prepared = self.prepare(state)
        self.assertEqual(len(prepared["intent"]["alert_keys"]), 2)
        html = prepared["prepared"]["payload"]["body"]["content"]
        self.assertIn("Example &lt;grant&gt;", html)
        self.assertEqual(html.count("עדכון ראשון ותזכורת"), 1)
        sent = n.reconcile(prepared["notices"], prepared["intent"],
                           dict(kind="sent", receipt=self.receipt(prepared["intent"])), self.now)
        self.assertEqual({r["state"] for r in sent["notices"]}, {"delivered"})
        self.assertEqual(n.reconcile(sent["notices"], sent["intent"],
            dict(kind="sent", receipt=self.receipt(sent["intent"])), self.now), sent)

    def test_anticipated_publication_and_opening_are_independent(self):
        state = self.ingest({"publication": self.fact("2026-11-28", certainty="forecast", officially_announced=True,
                                                     source_verified=True), "opening": self.fact("2026-12-10")})
        plan = self.select(state)
        self.assertEqual(len(plan["lines"]), 2)
        self.assertIn("מועד צפוי", next(x["text"] for x in plan["lines"] if x["stage"] == "publication"))
        self.assertEqual({r["stage"] for r in state["notices"]}, {"publication", "opening"})
        bad = self.ingest({"publication": self.fact(certainty="forecast")})
        self.assertEqual(bad["notices"], [])

    def test_reprepare_saved_report_replaces_old_fragment_when_kinds_overlap(self):
        state = self.ingest({"opening": self.fact("2026-12-10")})
        discovery = self.select(state)
        report = dict(state="partial", main=[dict(round_key=self.round["key"], name="Existing call",
            summary="Source-backed ordinary facts", official_source="https://funder.example/call")])
        saved = n.compose(report, discovery)
        together = self.select(state, n.cadence.stamp("2026-12-03T06:00:00Z"))
        refreshed = n.compose(saved, together)
        self.assertEqual(len(refreshed["main"][0]["summary"]), 2)
        self.assertIn("עדכון ראשון ותזכורת", refreshed["main"][0]["summary"][1])
        self.assertEqual(len(refreshed["notice_keys"]), 2)
        self.assertEqual(n.compose(refreshed, together), refreshed)

    def test_source_failure_preserves_pending_and_settings_are_bounded(self):
        state = self.ingest({"opening": self.fact("2026-11-28")})
        failed = n.ingest(state["round"], state["notices"], dict(round_key=self.round["key"],
            observed_at="2026-11-25T08:00:00Z", source_success=False), self.settings, "2026-11-20T06:00:00Z")
        self.assertEqual(failed["notices"], state["notices"])
        self.assertEqual(self.select(failed)["selected_lines"], 1)
        for value in (-1, 31, True):
            settings = copy.deepcopy(self.settings)
            settings["opening"]["catch_up_days"] = value
            with self.assertRaises(ValueError): n.validate_settings(settings)

    def test_deadlines_closing_and_unknown_dates_never_create_notices(self):
        for stage in ("deadline", "closing", "final_deadline"):
            with self.assertRaises(ValueError): self.ingest({stage: self.fact()})
        self.assertEqual(self.ingest({"opening": {"value": None, "certainty": "unknown"}})["notices"], [])

    def test_skipped_days_and_expiry_with_fresh_stage_settings(self):
        state = self.ingest({"opening": self.fact("2026-11-27")})
        self.assertEqual(len(self.select(state, n.cadence.stamp("2026-11-30T06:00:00Z"))["alert_keys"]), 2)
        self.settings["opening"]["catch_up_days"] = 2
        plan = self.select(state, n.cadence.stamp("2026-11-30T06:00:00Z"))
        self.assertEqual(plan["lines"], [])
        self.assertEqual({r["state"] for r in plan["notices"]}, {"expired"})

    def test_migration_suppresses_discovery_and_past_but_keeps_future_reminder(self):
        state = self.ingest({"announcement": self.fact("2026-11-19"), "opening": self.fact("2026-12-10")}, baseline=True)
        self.assertEqual(sum(r["state"] == "baseline" for r in state["notices"]), 3)
        plan = self.select(state, n.cadence.stamp("2026-12-03T06:00:00Z"))
        self.assertEqual(plan["lines"][0]["kinds"], ["approaching"])
        stale = self.ingest({"opening": self.fact("2026-11-01")})
        self.assertEqual({r["state"] for r in stale["notices"]}, {"expired"})

    def test_changed_dates_back_to_original_parallel_round_and_stale_observation(self):
        a = self.ingest({"opening": self.fact("2026-11-28")})
        b = self.ingest({"opening": self.fact("2026-12-01")}, a["round"], a["notices"], at="2026-11-25T08:00:00Z")
        c = self.ingest({"opening": self.fact("2026-11-28")}, b["round"], b["notices"], at="2026-11-26T08:00:00Z")
        self.assertEqual(len({r["date_version"] for r in c["notices"]}), 3)
        self.assertEqual(sum(r["state"] == "pending" for r in c["notices"]), 2)
        other = dict(self.round, key="program/round-B", cycle="2026-B")
        parallel = self.ingest({"opening": self.fact("2026-11-28")}, other)
        self.assertFalse({r["key"] for r in a["notices"]} & {r["key"] for r in parallel["notices"]})
        stale = self.ingest({"opening": self.fact("2026-12-20")}, c["round"], c["notices"])
        self.assertEqual(stale["ignored"], "stale_observation")
        self.assertEqual(stale["round"], c["round"])

    def test_superseded_just_before_prepare_cannot_reach_digest_consumer(self):
        first = self.ingest({"opening": self.fact("2026-11-28")})
        plan = self.select(first)
        changed = self.ingest({"opening": self.fact("2026-12-10")}, first["round"], first["notices"],
                              at="2026-11-25T08:00:00Z")
        with self.assertRaises(ValueError): self.prepare(changed, plan)

    def test_uncertain_old_version_blocks_new_until_exact_receipt_reconciliation(self):
        first = self.ingest({"opening": self.fact("2026-11-28")})
        prepared = self.prepare(first)
        uncertain = n.reconcile(prepared["notices"], prepared["intent"], dict(kind="uncertain", evidence="timeout"), self.now)
        changed = self.ingest({"opening": self.fact("2026-12-10")}, first["round"], uncertain["notices"],
                              at="2026-11-25T08:00:00Z")
        self.assertTrue(self.select(changed)["unresolved_delivery_blocked"])
        sent = n.reconcile(changed["notices"], uncertain["intent"],
                           dict(kind="sent", receipt=self.receipt(uncertain["intent"])), self.now)
        self.assertEqual(sum(r["state"] == "delivered" for r in sent["notices"]), 2)
        self.assertEqual(sum(r["state"] == "pending" for r in sent["notices"]), 2)

    def test_known_failure_retries_without_marking_delivered(self):
        state = self.ingest({"opening": self.fact("2026-11-28")})
        prepared = self.prepare(state)
        failed = n.reconcile(prepared["notices"], prepared["intent"],
                             dict(kind="definitive_not_sent", evidence="connector rejected before acceptance"), self.now)
        state["notices"] = failed["notices"]
        self.assertEqual(self.select(state)["selected_lines"], 1)
        self.assertFalse(any(r.get("receipt") for r in state["notices"]))
        self.assertEqual(len(self.prepare(state)["intent"]["alert_keys"]), 2)

    def test_receipt_and_existing_sender_delivery_guards(self):
        state = self.ingest({"opening": self.fact("2026-11-28")})
        prepared = self.prepare(state)
        for changes in ({"body_sha256": "wrong"}, {"sender": "wrong@example.org"}, {"cc": ["extra@example.org"]},
                        {"label_ids": []}, {"recipients": ["wrong@example.org"]}, {"verified_from_sent": False}):
            with self.assertRaises(ValueError):
                n.reconcile(prepared["notices"], prepared["intent"],
                            dict(kind="sent", receipt=self.receipt(prepared["intent"], **changes)), self.now)
        config = self.config()
        with self.assertRaises(ValueError):
            n.prepare(state["notices"], {state["round"]["key"]: state["round"]}, self.select(state),
                      dict(state="partial"), config, config["workspace_id"], "wrong@example.org",
                      "2026-11-24", "ready", self.now)

    def test_confirmed_exclusion_only_and_new_round_not_excluded(self):
        excluded = dict(self.round, review=dict(call_id="call-A", cycle="2026-A", institution_status="excluded",
            evidence_verified=True, reason="Only domestic institutions", official_source="https://funder.example/terms",
            verified_at="2026-11-24T07:00:00Z"))
        blocked = self.ingest({"opening": self.fact("2026-11-28")}, excluded)
        self.assertEqual(self.select(blocked)["lines"], [])
        for change in ({"cycle": "2026-B"}, {"review": dict(excluded["review"], evidence_verified=False)},
                       {"review": dict(excluded["review"], institution_status="unknown")}):
            allowed = self.ingest({"opening": self.fact("2026-11-28")}, dict(excluded, **change))
            self.assertEqual(self.select(allowed)["selected_lines"], 1)

    def test_bounded_finding_work_samples_and_unresolved_preflight(self):
        row = self.ingest({"opening": self.fact("2026-11-28")})["notices"][0]
        consumed = []
        def rows():
            for i in range(1000):
                consumed.append(i)
                yield dict(row, key=str(i), sample=True)
        result = n.select(rows(), {}, self.settings, self.now, scan_limit=7, unresolved_checked=True)
        self.assertEqual(len(consumed), 7)
        self.assertEqual(result["lines"], [])
        self.assertTrue(result["remaining_work"])
        self.assertTrue(n.select([], {}, self.settings, self.now)["unresolved_delivery_blocked"])

    def test_actual_cli_native_pending_survives_no_source_selection_and_round_change(self):
        state = self.ingest({"publication": self.fact("2026-11-28"), "opening": self.fact("2026-11-29")})
        native = [n.properties(row, {s:"https://notion.example/"+s for s in n.STAGES}, self.settings) for row in state["notices"]]
        with tempfile.TemporaryDirectory() as folder:
            def cli(operation, **data):
                src, out = Path(folder)/(operation+".json"), Path(folder)/(operation+"-output.json")
                original = json.dumps(dict(operation=operation, **data), ensure_ascii=False)
                src.write_text(original, encoding="utf-8")
                result = subprocess.run([sys.executable, str(SCRIPTS/"notice_contract.py"), str(src), "--output", str(out)],
                                        capture_output=True, text=True, encoding="utf-8")
                self.assertEqual(src.read_text(encoding="utf-8"), original)
                return result, json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
            result, plan = cli("select", native_rows=native, rounds={state["round"]["key"]:state["round"]},
                settings_rows=[{"Key":"notice:"+s,"Reminder days":7,"Catch-up days":7} for s in n.STAGES],
                now="2026-11-30T06:00:00Z", unresolved_checked=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(plan["selected_lines"], 2)
            self.assertEqual(len(plan["alert_keys"]), 4)
            self.assertTrue(plan["no_send"])
            changed = self.ingest({"opening": self.fact("2026-12-15")}, state["round"], state["notices"],
                                  at="2026-11-30T07:00:00Z")
            config = self.config()
            result, output = cli("prepare", notices=changed["notices"], rounds={changed["round"]["key"]:changed["round"]},
                plan=plan, report=dict(state="partial"), config=config, actual_workspace=config["workspace_id"],
                connected_sender="owner@example.org", report_day="2026-11-30", delivery_state="ready", now="2026-11-30T07:00:00Z")
            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)

    def test_actual_cli_prepare_and_receipt_only_confirm_delivery(self):
        state = self.ingest({"publication": self.fact("2026-11-28"), "opening": self.fact("2026-11-29")})
        config = self.config()
        with tempfile.TemporaryDirectory() as folder:
            def cli(operation, **data):
                src, out = Path(folder)/(operation+".json"), Path(folder)/(operation+"-result.json")
                src.write_text(json.dumps(dict(operation=operation, **data)), encoding="utf-8")
                result = subprocess.run([sys.executable, str(SCRIPTS/"notice_contract.py"), str(src), "--output", str(out)],
                                        capture_output=True, text=True, encoding="utf-8")
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(out.read_text(encoding="utf-8"))
            prepared = cli("prepare", notices=state["notices"], rounds={state["round"]["key"]:state["round"]},
                plan=self.select(state), report=dict(state="partial"), config=config,
                actual_workspace=config["workspace_id"], connected_sender="owner@example.org",
                report_day="2026-11-24", delivery_state="ready", now="2026-11-24T08:00:00Z")
            self.assertEqual({r["state"] for r in prepared["notices"]}, {"intent"})
            sent = cli("reconcile", notices=prepared["notices"], intent=prepared["intent"],
                outcome=dict(kind="sent",receipt=self.receipt(prepared["intent"])), now="2026-11-24T08:10:00Z")
            self.assertTrue(sent["no_send"])
            self.assertEqual({r["state"] for r in sent["notices"]}, {"delivered"})
            self.assertEqual(len(sent["intent"]["alert_keys"]), 4)

    def test_reminder_edits_apply_per_stage_and_native_corruption_fails_closed(self):
        state = self.ingest({"publication":self.fact("2026-12-05"),"opening":self.fact("2026-12-10")})
        self.settings["publication"]["reminder_days"] = 2
        self.settings["opening"]["reminder_days"] = 20
        plan = self.select(state, n.cadence.stamp("2026-11-28T06:00:00Z"))
        self.assertEqual({line["stage"]:line["kinds"] for line in plan["lines"]},
                         {"publication":["discovery"],"opening":["approaching","discovery"]})
        native = n.properties(state["notices"][0], {s:"https://notion.example/"+s for s in n.STAGES}, self.settings)
        native["State"] = "delivered"
        with self.assertRaises(ValueError): n.notices_from_notion([native])

    def test_operational_capacity_keeps_all_three_phases_and_both_kinds(self):
        rounds, notices = {}, []
        for i in range(477):
            row = dict(self.round, key="fixture/round-"+str(i))
            observed = self.ingest({s:self.fact("2026-11-28") for s in n.STAGES}, row)
            rounds[row["key"]] = observed["round"]
            notices.extend(observed["notices"])
        plan = n.select(notices, rounds, self.settings, self.now, scan_limit=3000,
                        line_limit=1500, unresolved_checked=True)
        self.assertEqual(len(plan["lines"]), 1431)
        self.assertEqual(len(plan["alert_keys"]), 2862)
        self.assertTrue(all(len(line["kinds"]) == 2 for line in plan["lines"]))
        self.assertFalse(plan["unresolved_delivery_blocked"])


if __name__ == "__main__":
    unittest.main()
