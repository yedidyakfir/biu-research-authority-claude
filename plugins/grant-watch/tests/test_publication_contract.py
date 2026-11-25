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
import publication_contract as p


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.now = n.cadence.stamp("2026-12-01T06:00:00Z")
        self.settings = n.defaults()
        self.sender, self.recipients = "owner@example.org", ["admin@example.org"]
        round_state = dict(key="example/round-A", call_id="example-A", cycle="2026-A", name="Example",
                           url="https://notion.example/round-A")
        fact = dict(value="2026-12-20", certainty="verified", source_url="https://funder.example/call",
                    verified_at="2026-11-30T06:00:00Z", timezone="Asia/Jerusalem")
        ingested = n.ingest(round_state, [], dict(round_key=round_state["key"], observed_at="2026-11-30T06:00:00Z",
            source_success=True, milestones={"opening": fact}), self.settings, "2026-11-20T06:00:00Z")
        self.rounds = {round_state["key"]: ingested["round"]}
        self.original = next(r for r in ingested["notices"] if r["kind"] == "discovery")
        self.original.update(state="delivered", run_key="grant-watch:2026-11-30", receipt=dict(
            message_id="historical-synthetic", sent_at="2026-11-30T07:00:00Z", verified_from_sent=True,
            label_ids=["SENT"], sender=self.sender, recipients=self.recipients, subject="Historical",
            body_sha256="historical-hash"))
        self.key = self.original["key"]
        self.controls = {self.key: dict(requested=True, paused=False, url="https://notion.example/original",
                                       checkpoint=dict(sequence=0, latched=False))}
        self.audience = p.audience(self.sender, self.recipients)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.calls = 0

    def sync(self, notices=None, controls=None):
        return p.sync(notices or [self.original], self.rounds, controls or self.controls,
                      self.sender, self.recipients, self.now)

    def latch(self, result, controls=None):
        controls = copy.deepcopy(controls or self.controls)
        for update in result["control_updates"]:
            controls[update["key"]]["checkpoint"] = n.decode_record(update["properties"]["Publication control"])
        return controls

    def grant(self, child, **changes):
        return dict(dict(scope=p.scope(child), principal="owner-id", channel="trusted_user_history",
                         message_ref="synthetic-owner-message", confirmed_at="2026-12-01T06:00:00Z",
                         expires_at="2026-12-08T06:00:00Z"), **changes)

    def context(self, child, **changes):
        return dict(schema=p.SCHEMA, owner_principal="owner-id", confirmations=[self.grant(child, **changes)])

    def select(self, rows, controls, context=None, **extra):
        return n.select(rows, self.rounds, self.settings, self.now, controls=controls,
                        confirmations=p.Confirmations(context) if context else None,
                        audience_key=self.audience, unresolved_checked=True, **extra)

    def config(self):
        keys = ("workspace_id", "root_page_id", "sources_database_id", "rounds_database_id", "events_database_id", "runs_database_id")
        return dict({key: str(i+1)*32 for i, key in enumerate(keys)}, workspace_verified_by_owner=True,
            publication_controls=dict(mode="operator_requests_v1"),
            timezone="Asia/Jerusalem", email=dict(enabled=True, owner_approved=True, sender=self.sender,
            recipients=self.recipients, approved_recipients=self.recipients))

    def native(self, rows, controls):
        result = []
        for row in rows:
            raw = n.properties(row, {s: "https://notion.example/"+s for s in n.STAGES}, self.settings)
            if row["key"] in controls:
                c = controls[row["key"]]
                raw.update({"url": c["url"], "Publish again": c["requested"], "Pause publication": c["paused"],
                            "Publication control": n.encode_record(c["checkpoint"])})
            result.append(raw)
        return result

    def cli(self, operation, context=None, fail=False, **data):
        self.calls += 1
        src = Path(self.folder.name)/f"{self.calls}-input.json"
        out = Path(self.folder.name)/f"{self.calls}-output.json"
        src.write_text(json.dumps(dict(operation=operation, **data)), encoding="utf-8")
        command = [sys.executable, "-X", "utf8", str(SCRIPTS/"notice_contract.py"), str(src), "--output", str(out)]
        if context is not None:
            trusted = Path(self.folder.name)/f"{self.calls}-trusted.json"
            trusted.write_text(json.dumps(context), encoding="utf-8")
            command += ["--trusted-confirmations", str(trusted)]
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        if fail:
            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse(out.exists())
            return
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(out.read_text(encoding="utf-8"))
        self.assertTrue(result["no_send"])
        return result

    def test_untrusted_request_latches_once_without_touching_original(self):
        result = self.sync()
        self.assertEqual(result["notices"][0], self.original)
        self.assertEqual(len(result["confirmation_requests"]), 1)
        controls = self.latch(result)
        again = self.sync(result["notices"], controls)
        self.assertEqual(again["notices"], result["notices"])
        plan = self.select(again["notices"], controls)
        self.assertEqual(plan["alert_keys"], [])
        child = plan["notices"][1]
        self.assertEqual(child["selection_hold"], "awaiting_owner_confirmation")
        child["approved"] = True
        child["last_edited_by"] = "owner-id"
        child["authorization"] = self.grant(child)
        self.assertEqual(self.select([self.original, child], controls)["alert_keys"], [])

    def test_child_write_before_checkpoint_crash_reuses_exact_key(self):
        result = self.sync()
        retry = self.sync(result["notices"], self.controls)
        self.assertEqual(retry["notices"], result["notices"])
        self.assertEqual(retry["control_updates"], result["control_updates"])

    def test_off_on_requests_new_one_time_key_but_keeps_history(self):
        result = self.sync()
        controls = self.latch(result)
        controls[self.key]["requested"] = False
        off = self.sync(result["notices"], controls)
        self.assertEqual(off["notices"][1]["reason"], "operator_cancelled_request")
        controls = self.latch(off, controls)
        controls[self.key]["requested"] = True
        second = self.sync(off["notices"], controls)
        self.assertEqual(len(second["notices"]), 3)
        self.assertNotEqual(second["notices"][1]["key"], second["notices"][2]["key"])
        self.assertEqual(second["notices"][0], self.original)
        old_grant = self.context(result["notices"][1])
        self.assertEqual(self.select(second["notices"], self.latch(second, controls), old_grant)["lines"], [])

    def test_scope_authority_audience_and_expiry_are_separate_gates(self):
        result = self.sync()
        controls, child = self.latch(result), result["notices"][1]
        self.assertEqual(len(self.select(result["notices"], controls, self.context(child))["alert_keys"]), 1)
        for changes in ({"principal": "unapproved-editor"}, {"message_ref": ""}, {"channel": "notion"},
                        {"expires_at": "2026-12-20T06:00:00Z"}, {"confirmed_at": "2026-12-02T06:00:00Z"},
                        {"expires_at": "2026-12-01T06:00:00Z"}, {"scope": dict(p.scope(child), stage="publication")}):
            self.assertEqual(self.select(result["notices"], controls, self.context(child, **changes))["lines"], [])
        self.audience = p.audience(self.sender, ["changed@example.org"])
        self.assertEqual(self.select(result["notices"], controls, self.context(child))["lines"], [])

    def test_current_exclusion_unknown_eligibility_and_date_version(self):
        result = self.sync()
        controls, child = self.latch(result), result["notices"][1]
        state = self.rounds[self.original["round_key"]]
        state["review"] = dict(call_id=state["call_id"], cycle=state["cycle"], institution_status="unknown")
        self.assertEqual(len(self.select(result["notices"], controls, self.context(child))["lines"]), 1)
        state["review"].update(institution_status="excluded", evidence_verified=True, reason="Domestic only",
                               official_source="https://funder.example/terms", verified_at="2026-12-01T05:00:00Z")
        self.assertEqual(self.select(result["notices"], controls, self.context(child))["lines"], [])
        state["review"] = {}
        state["notice_checkpoint"]["opening"]["version"] = "changed-date"
        self.assertEqual(self.select(result["notices"], controls, self.context(child))["lines"], [])

    def test_cli_request_prepare_uncertainty_reconciliation_and_no_second_send(self):
        result = self.cli("controls", native_rows=self.native([self.original], self.controls), rounds=self.rounds,
                          sender=self.sender, recipients=self.recipients, now=n.cadence.iso(self.now))
        controls, child = self.latch(result), result["notices"][1]
        raw = self.native(result["notices"], controls)
        plan = self.cli("select", native_rows=raw, rounds=self.rounds, settings=self.settings,
            now=n.cadence.iso(self.now), unresolved_checked=True, audience_key=self.audience)
        self.assertEqual(plan["lines"], [])
        context = self.context(child)
        plan = self.cli("select", context=context, native_rows=raw, rounds=self.rounds, settings=self.settings,
            now=n.cadence.iso(self.now), unresolved_checked=True, audience_key=self.audience)
        config = self.config()
        prepare_args = dict(native_rows=raw, rounds=self.rounds, plan=plan, report=dict(state="partial", main=[]),
            config=config, actual_workspace=config["workspace_id"], connected_sender=self.sender,
            report_day="2026-12-01", delivery_state="ready", now=n.cadence.iso(self.now))
        self.cli("prepare", fail=True, **prepare_args)  # The saved plan is not authorization.
        inactive = copy.deepcopy(prepare_args)
        inactive["config"].pop("publication_controls")
        self.cli("prepare", context=context, fail=True, **inactive)
        prepared = self.cli("prepare", context=context, **prepare_args)
        self.assertEqual(prepared["intent"]["alert_keys"], [child["key"]])
        self.assertEqual(len(prepared["intent"]["resend_authorizations"]), 1)
        self.assertEqual(prepared["notices"][1]["resend_confirmation"]["message_ref"], "synthetic-owner-message")
        uncertain = self.cli("reconcile", notices=prepared["notices"], intent=prepared["intent"],
            outcome=dict(kind="uncertain", evidence="synthetic timeout"), now=n.cadence.iso(self.now))
        self.assertTrue(self.select(uncertain["notices"], controls, context)["unresolved_delivery_blocked"])
        controls[self.key]["requested"] = False
        cancelled = self.sync(uncertain["notices"], controls)
        self.assertEqual(cancelled["notices"][1]["state"], "uncertain")
        intent = prepared["intent"]
        receipt = dict(verified_from_sent=True, label_ids=["SENT"], message_id="resend-synthetic",
            sender=intent["sender"], recipients=intent["recipients"], subject=intent["subject"],
            body_sha256=intent["body_sha256"], sent_at="2026-12-01T06:05:00Z")
        sent = self.cli("reconcile", notices=cancelled["notices"], intent=uncertain["intent"],
            outcome=dict(kind="sent", receipt=receipt), now=n.cadence.iso(self.now))
        self.assertEqual(sent["notices"][0], self.original)
        self.assertEqual(sent["notices"][1]["receipt"]["message_id"], "resend-synthetic")
        self.assertEqual(self.select(sent["notices"], controls, context)["lines"], [])
        self.cli("reconcile", fail=True, notices=sent["notices"], intent=sent["intent"],
            outcome=dict(kind="definitive_not_sent", evidence="late rejection"), now=n.cadence.iso(self.now))

    def test_cli_untrusted_snapshot_cannot_supply_confirmations_or_remove_resend_marker(self):
        result = self.sync()
        controls, child = self.latch(result), result["notices"][1]
        self.cli("select", fail=True, native_rows=self.native(result["notices"], controls), rounds=self.rounds,
            settings=self.settings, now=n.cadence.iso(self.now), confirmations=self.context(child))
        forged = copy.deepcopy(child)
        forged.pop("delivery_type")
        with self.assertRaises(ValueError):
            self.select([self.original, forged], controls)

    def test_cli_cancel_pause_or_date_change_after_selection_blocks_prepare(self):
        result = self.sync()
        controls, child = self.latch(result), result["notices"][1]
        context = self.context(child)
        plan = self.select(result["notices"], controls, context)
        config = self.config()
        for mode in ("cancel", "pause", "date"):
            changed, rounds = copy.deepcopy(controls), copy.deepcopy(self.rounds)
            if mode == "cancel": changed[self.key]["requested"] = False
            if mode == "pause": changed[self.key]["paused"] = True
            if mode == "date": rounds[self.original["round_key"]]["notice_checkpoint"]["opening"]["version"] = "new"
            self.cli("prepare", fail=True, context=context, native_rows=self.native(result["notices"], changed),
                rounds=rounds, plan=plan, report=dict(state="partial"), config=config,
                actual_workspace=config["workspace_id"], connected_sender=self.sender, report_day="2026-12-01",
                delivery_state="ready", now=n.cadence.iso(self.now))

    def test_failed_send_retries_same_key_and_overlap_stays_one_line(self):
        result = self.sync()
        controls, child = self.latch(result), result["notices"][1]
        reminder = copy.deepcopy(self.original)
        reminder.update(kind="approaching", state="pending")
        reminder.pop("receipt")
        reminder["key"] = n.digest([reminder["round_key"], reminder["stage"], reminder["date_version"], reminder["kind"]])
        controls[reminder["key"]] = dict(requested=False, paused=False, checkpoint=dict(sequence=0, latched=False))
        self.settings["opening"]["reminder_days"] = 30
        rows = result["notices"] + [reminder]
        context = self.context(child)
        plan = self.select(rows, controls, context)
        self.assertEqual(len(plan["lines"]), 1)
        self.assertEqual(set(plan["alert_keys"]), {reminder["key"], child["key"]})
        config = self.config()
        prepared = n.prepare(rows, self.rounds, plan, dict(state="partial"), config, config["workspace_id"],
            self.sender, "2026-12-01", "ready", self.now, controls=controls, confirmations=p.Confirmations(context))
        failed = n.reconcile(prepared["notices"], prepared["intent"],
                             dict(kind="definitive_not_sent", evidence="synthetic preaccept rejection"), self.now)
        self.assertEqual(self.select(failed["notices"], controls, context)["alert_keys"], plan["alert_keys"])
        self.assertEqual(failed["notices"][0], self.original)

    def test_bounds_and_independent_phase_round_keys(self):
        with self.assertRaises(ValueError):
            p.sync([self.original]*2, self.rounds, self.controls,
                   self.sender, self.recipients, self.now, scan_limit=1)
        for stage, round_key in (("publication", "example/round-A"), ("opening", "example/round-B")):
            other = copy.deepcopy(self.original)
            other.update(stage=stage, round_key=round_key)
            other["key"] = n.digest([other["round_key"], other["stage"], other["date_version"], other["kind"]])
            state = copy.deepcopy(next(iter(self.rounds.values())))
            state["key"] = round_key
            state["notice_checkpoint"][stage] = state["notice_checkpoint"]["opening"]
            controls = {other["key"]: copy.deepcopy(self.controls[self.key])}
            second = p.sync([other], {round_key: state}, controls, self.sender, self.recipients, self.now)
            self.assertNotEqual(second["notices"][1]["key"], self.sync()["notices"][1]["key"])

    def test_receipt_reset_is_rejected_and_native_samples_remain_safe(self):
        reset = dict(self.original, state="pending")
        with self.assertRaises(ValueError):
            self.select([reset], self.controls)
        sample = dict(self.original, key="sample:legacy", sample=True)
        raw = self.native([sample], {})
        plan = self.cli("select", native_rows=raw, rounds=self.rounds, settings=self.settings,
                        now=n.cadence.iso(self.now), unresolved_checked=True)
        self.assertEqual(plan["lines"], [])

    def test_bad_controls_fail_closed_and_operator_requires_explicit_delegation(self):
        for value in (0, 1, "yes", "approved"):
            with self.assertRaises(ValueError): p.checkbox(value)
        result = self.sync()
        controls, child = self.latch(result), result["notices"][1]
        bad = copy.deepcopy(controls)
        bad[self.key]["checkpoint"]["active_key"] = self.key
        with self.assertRaises(ValueError): self.sync(result["notices"], bad)
        context = self.context(child, principal="operator-id")
        self.assertEqual(self.select(result["notices"], controls, context)["lines"], [])
        context["operators"] = {"operator-id": {"owner_message_ref": "synthetic-owner-delegation"}}
        self.assertEqual(len(self.select(result["notices"], controls, context)["lines"]), 1)

    def test_cli_explicit_native_budget_survives_actual_prepare_entry(self):
        result = self.sync()
        controls, child = self.latch(result), result["notices"][1]
        context = self.context(child)
        plan = self.select(result["notices"], controls, context)
        rows = result["notices"] + [dict(self.original, key=f"sample:{i}", sample=True) for i in range(120)]
        config = self.config()
        data = dict(native_rows=self.native(rows, controls), rounds=self.rounds, plan=plan,
            report=dict(state="partial"), config=config, actual_workspace=config["workspace_id"],
            connected_sender=self.sender, report_day="2026-12-01", delivery_state="ready", now=n.cadence.iso(self.now))
        self.cli("prepare", fail=True, context=context, **data)
        prepared = self.cli("prepare", context=context, scan_limit=200, **data)
        self.assertEqual(prepared["intent"]["alert_keys"], [child["key"]])


if __name__ == "__main__":
    unittest.main()
