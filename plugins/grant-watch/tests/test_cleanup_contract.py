import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from datetime import timedelta
from unittest.mock import patch

import test_publication_contract as fixtures
import cleanup_contract as cleanup
import notice_contract as n
import publication_contract as p


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.PublicationTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.folder = Path(self.f.folder.name)
        self.f.original["receipt"]["body_sha256"] = n.digest("synthetic historical body")
        self.f.controls[self.f.key]["requested"] = False
        self.raw = self.f.native([self.f.original], self.f.controls)[0]
        self.raw.update(Fixture="__YES__", **{"Digest run": ["https://notion.example/synthetic-run"]})
        receipt = self.f.original["receipt"]
        self.intent = dict(state="sent", run_key=self.f.original["run_key"], alert_keys=[self.f.key],
                           url="https://notion.example/synthetic-run",
                           receipt=receipt, **{k: receipt[k] for k in
                           ("sender", "recipients", "subject", "body_sha256")})
        self.data = dict(scope="milestone_notices", isolation="synthetic_disposable", fixture_id="cleanup-test",
            now="2026-12-08T19:30:00Z", settings=cleanup.defaults(),
            traversal=dict(has_more=False, includes_logically_archived=True, all_notice_states=True, rows_read=1),
            native_rows=[self.raw], runs={self.intent["run_key"]: self.intent})
        self.input = self.folder / "fixture.json"
        self.count = 0

    def write(self):
        self.input.write_text(json.dumps(self.data), encoding="utf-8")

    def cli(self, operation="plan", reset=True, **kw):
        if reset:
            self.write()
        self.count += 1
        output = self.folder / f"output-{self.count}.json"
        command = [sys.executable, "-X", "utf8", str(fixtures.SCRIPTS / "cleanup_contract.py"),
                   operation, str(self.input), "--output", str(output)]
        for key, value in kw.items():
            command.extend(["--" + key.replace("_", "-"), value])
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(output.read_text(encoding="utf-8"))
        self.assertTrue(value["no_send"])
        return value

    def rows(self):
        return json.loads(self.input.read_text(encoding="utf-8"))

    def test_exact_boundary_timezone_and_retention_are_elapsed_time(self):
        self.data["now"] = "2026-12-07T09:00:00+02:00"
        result = self.cli()
        self.assertEqual(result["held_by_reason"], {"within_retention": 1})
        self.data["now"] = "2026-12-07T07:00:00.000001Z"
        self.assertEqual(len(self.cli()["eligible"]), 1)
        self.data["settings"]["retention_days"] = 8
        self.assertEqual(self.cli()["held_by_reason"], {"within_retention": 1})

    def test_only_actual_verified_delivered_timestamp_counts(self):
        for mutation in (
            {"date:Delivered:start": "2020-01-01T00:00:00Z"},
            {"date:Delivered:start": "2026-11-30", "date:Delivered:is_datetime": 0},
            {"Receipt ID": "other"}, {"Run key": "other"}, {"Digest run": []}):
            self.data["native_rows"] = [dict(self.raw, **mutation)]
            self.assertEqual(self.cli()["held_by_reason"], {"unverified_or_inconsistent_delivery": 1})
        self.data["native_rows"] = [self.raw]
        self.data["runs"] = {}
        self.assertEqual(self.cli()["eligible"], [])

    def test_pending_uncertain_approval_and_samples_never_archive(self):
        for state in ("pending", "intent", "uncertain", "awaiting_confirmation", "baseline", "expired"):
            notice = dict(self.f.original, state=state)
            self.data["native_rows"] = [dict(self.raw, State=state, Payload=n.encode_record(notice))]
            self.assertEqual(self.cli()["eligible"], [])
        self.data["native_rows"] = [dict(self.raw, Sample="__YES__")]
        self.assertEqual(self.cli()["held_by_reason"], {"sample": 1})

    def test_pending_resend_blocks_old_original_even_if_checkbox_off(self):
        controls = copy.deepcopy(self.f.controls)
        controls[self.f.key]["requested"] = True
        synced = self.f.sync(controls=controls)
        controls = self.f.latch(synced, controls)
        controls[self.f.key]["requested"] = False
        native = self.f.native(synced["notices"], controls)
        native[0]["Digest run"] = self.raw["Digest run"]
        self.data["native_rows"] = native
        self.data["traversal"]["rows_read"] = 2
        result = self.cli()
        self.assertEqual(result["eligible"], [])
        self.assertEqual(result["held_by_reason"]["outstanding_publication_request_or_delivery"], 1)
        self.data["native_rows"] = native[:1]
        self.data["traversal"]["rows_read"] = 1
        self.assertEqual(self.cli()["status"], "blocked")

    def test_requested_control_blocks_even_before_child_created(self):
        self.raw["Publish again"] = "__YES__"
        self.assertEqual(self.cli()["held_by_reason"], {"outstanding_publication_request_or_delivery": 1})

    def test_empty_complete_ledger_is_distinct_from_unknown_and_missing_receipt(self):
        self.data["native_rows"] = []
        self.data["traversal"]["rows_read"] = 0
        self.data["runs"] = {}
        result = self.cli("fixture-archive")
        self.assertEqual((result["status"], result["writes"]), ("fixture_verified", 0))
        self.data["traversal"]["has_more"] = True
        result = self.cli()
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(result["unknown_remaining"])

    def test_sent_evidence_and_body_hash_must_be_valid_and_cannot_be_undone(self):
        for mutation in ({"verified_from_sent": False}, {"label_ids": []}, {"body_sha256": "invalid"}):
            original = copy.deepcopy(self.f.original)
            original["receipt"].update(mutation)
            self.data["native_rows"] = [dict(self.raw, Payload=n.encode_record(original))]
            self.data["runs"][self.intent["run_key"]] = dict(self.intent, receipt=original["receipt"],
                body_sha256=original["receipt"]["body_sha256"])
            self.assertEqual(self.cli()["eligible"], [])
        with self.assertRaisesRegex(ValueError, "Confirmed delivery"):
            n.reconcile([self.f.original], self.intent, dict(kind="uncertain", evidence="synthetic timeout"), self.f.now)

    def test_incomplete_scan_cap_unknown_duplicates_fail_closed(self):
        for key, value in (("has_more", True), ("includes_logically_archived", False),
                           ("all_notice_states", False), ("rows_read", 2)):
            original = copy.deepcopy(self.data["traversal"])
            self.data["traversal"][key] = value
            self.assertEqual(self.cli()["status"], "blocked")
            self.data["traversal"] = original
        self.data["native_rows"].append(copy.deepcopy(self.raw))
        self.data["traversal"]["rows_read"] = 2
        self.assertIn("Duplicate", self.cli()["reason"])
        self.data["settings"]["max_scan_rows"] = 1
        result = self.cli()
        self.assertEqual((result["scanned"], result["unscanned"], result["eligible"]), (1, 1, []))

    def test_marker_archive_restarts_and_repeat_attempt_are_idempotent(self):
        for stop in ("marker", "archive"):
            before = copy.deepcopy(self.data)
            first = self.cli("fixture-archive", stop_after=stop)
            self.assertEqual(first["status"], "interrupted_after_" + stop)
            final = self.cli("fixture-archive", reset=False)
            self.assertEqual(final["status"], "fixture_verified")
            after = self.rows()
            self.assertEqual(after["runs"], before["runs"])
            self.assertEqual(cleanup.immutable(after["native_rows"][0]), cleanup.immutable(self.raw))
            self.assertEqual(final["final_plan"]["already_archived"], [self.f.key])
            repeat = self.cli("fixture-archive", reset=False)
            self.assertEqual(repeat["writes"], 0)
            self.assertEqual(self.rows(), after)

    def test_request_arriving_between_marker_and_archive_keeps_record_in_place(self):
        self.cli("fixture-archive", stop_after="marker")
        self.data = self.rows()
        self.data["native_rows"][0]["Publish again"] = "__YES__"
        result = self.cli("fixture-archive")
        self.assertEqual(result["writes"], 0)
        self.assertFalse(p.checkbox(self.rows()["native_rows"][0].get("Cleanup archived")))

    def test_tampered_marker_and_history_hold_without_overwriting(self):
        self.cli("fixture-archive", stop_after="marker")
        self.data = self.rows()
        self.data["native_rows"][0]["Name"] = "changed after intent"
        result = self.cli("fixture-archive")
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(p.checkbox(self.rows()["native_rows"][0].get("Cleanup archived")))

    def test_production_scope_enabled_setting_and_overlap_are_blocked(self):
        for mutation in ({"isolation": "production"}, {"scope": "runs"}):
            old = copy.deepcopy(self.data)
            self.data.update(mutation)
            self.assertEqual(self.cli("fixture-archive")["status"], "blocked")
            self.data = old
        self.data["settings"]["enabled"] = True
        self.assertEqual(self.cli()["status"], "blocked")
        self.data["settings"]["enabled"] = False
        self.assertEqual(self.cli("fixture-archive", now="2026-12-08T06:00:00Z")["status"], "blocked")
        self.write()
        lock = self.input.with_suffix(".worker.lock")
        lock.write_text("another worker", encoding="utf-8")
        self.assertEqual(self.cli("fixture-archive", reset=False)["status"], "blocked")
        self.assertEqual(lock.read_text(), "another worker")

    def test_calendar_month_interval_and_dst_not_thirty_days(self):
        self.data.update(now="2026-12-01T19:30:00Z", anchor_month="2026-12")
        self.assertEqual(self.cli("schedule")["next_occurrence"], "2027-01-01T19:30:00Z")
        self.data["settings"]["interval_months"] = 3
        self.assertEqual(self.cli("schedule")["next_occurrence"], "2027-03-01T19:30:00Z")
        self.data["now"] = "2027-03-01T19:30:00Z"
        self.assertEqual(self.cli("schedule")["next_occurrence"], "2027-06-01T18:30:00Z")

    def notice_cli(self, operation, **data):
        self.count += 1
        source, output = self.folder / f"notice-{self.count}.json", self.folder / f"notice-{self.count}-out.json"
        source.write_text(json.dumps(dict(operation=operation, **data)), encoding="utf-8")
        result = subprocess.run([sys.executable, "-X", "utf8", str(fixtures.SCRIPTS / "notice_contract.py"),
            str(source), "--output", str(output)], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(output.read_text(encoding="utf-8"))

    def test_archived_lookup_discovery_reconcile_and_publish_again_use_actual_notice_cli(self):
        self.cli("fixture-archive")
        raw = self.rows()["native_rows"][0]
        archived = n.notices_from_notion([raw])[0]
        self.assertEqual(archived, self.f.original)
        # Exact Key and Round lookup must include the sidecar-archived native row.
        self.assertEqual([r for r in [raw] if r["Key"] == self.f.key], [raw])
        self.assertEqual([r for r in [raw] if r["Round key"] == archived["round_key"]], [raw])
        discovery = self.notice_cli("ingest", native_rows=[raw], round_state=self.f.rounds[archived["round_key"]],
            observation=dict(round_key=archived["round_key"], observed_at=self.data["now"], source_success=True,
                             milestones={"opening": archived["fact"]}),
            settings=self.f.settings, activated_at="2026-11-20T06:00:00Z")
        self.assertEqual(sum(r["key"] == archived["key"] for r in discovery["notices"]), 1)
        self.assertEqual(next(r for r in discovery["notices"] if r["key"] == archived["key"]), archived)
        reconciled = self.notice_cli("reconcile", native_rows=[raw], intent=self.intent,
            outcome=dict(kind="sent", receipt=archived["receipt"]), now=self.data["now"])
        self.assertEqual(reconciled["notices"], [archived])
        passive = self.notice_cli("controls", native_rows=[raw], rounds=self.f.rounds,
            sender=self.f.sender, recipients=self.f.recipients, now=self.data["now"])
        self.assertEqual(passive["notices"], [archived])
        # Synthetic owner request only, never actual authorization or a live edit.
        raw["Publish again"] = "__YES__"
        requested = self.notice_cli("controls", native_rows=[raw], rounds=self.f.rounds,
            sender=self.f.sender, recipients=self.f.recipients, now=self.data["now"])
        self.assertEqual(requested["notices"][0], archived)
        child = requested["notices"][1]
        self.assertNotEqual(child["key"], archived["key"])
        self.assertEqual(child["state"], "awaiting_confirmation")
        raw.update(requested["control_updates"][0]["properties"])
        child_native = self.f.native([child], {})[0]
        repeat = self.notice_cli("controls", native_rows=[raw, child_native], rounds=self.f.rounds,
            sender=self.f.sender, recipients=self.f.recipients, now=self.data["now"])
        self.assertEqual(len(repeat["notices"]), 2)
        selection = self.notice_cli("select", native_rows=[raw, child_native], rounds=self.f.rounds,
            settings=self.f.settings, now=self.data["now"], audience_key=self.f.audience,
            unresolved_checked=True)
        self.assertEqual(selection["alert_keys"], [])

    def test_archive_capacity_reports_deferred_not_deleted(self):
        second = copy.deepcopy(self.f.original)
        second["round_key"] = "second/round"
        second["key"] = n.digest([second["round_key"], second["stage"], second["date_version"], second["kind"]])
        raw = n.properties(second, {s: "https://notion.example/settings" for s in n.STAGES}, self.f.settings,
                           "https://notion.example/synthetic-run")
        raw.update(Fixture="__YES__")
        self.data["native_rows"].append(raw)
        self.intent["alert_keys"].append(second["key"])
        self.data["traversal"]["rows_read"] = 2
        self.data["settings"]["max_archive_rows"] = 1
        result = self.cli()
        self.assertEqual((len(result["eligible"]), len(result["deferred"])), (1, 1))


if __name__ == "__main__":
    unittest.main()
