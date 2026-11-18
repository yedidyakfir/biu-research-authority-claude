import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("pilot", Path(__file__).parents[1] / "scripts" / "pilot_contract.py")
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)


class PilotContractTests(unittest.TestCase):
    def config(self):
        keys = ["workspace_id", "root_page_id", "sources_database_id", "rounds_database_id", "events_database_id", "runs_database_id"]
        result = {key: str(i + 1) * 32 for i, key in enumerate(keys)}
        return dict(result, workspace_verified_by_owner=True, timezone="Asia/Jerusalem")

    def test_workspace_guard(self):
        config = self.config()
        self.assertEqual(pilot.validate_config(config, config["workspace_id"]), config)
        with self.assertRaises(ValueError):
            pilot.validate_config(config, "9" * 32)

    def test_missing_owner_confirmation(self):
        config = self.config()
        config["workspace_verified_by_owner"] = False
        with self.assertRaises(ValueError):
            pilot.validate_config(config, config["workspace_id"])

    def test_invalid_config(self):
        for key in ("timezone", "sources_database_id"):
            config = self.config()
            config.pop(key)
            with self.assertRaises(ValueError):
                pilot.validate_config(config, config["workspace_id"])

    def test_idempotent_events_and_changed_dates(self):
        parts = ["funder/program", "2027", "LOI", "deadline", "2027-02-01"]
        self.assertEqual(pilot.event_key(*parts), pilot.event_key(*parts))
        self.assertNotEqual(pilot.event_key(*parts), pilot.event_key(*parts[:-1], "2027-03-01"))

    def test_no_email_by_default(self):
        self.assertFalse(pilot.delivery_allowed(self.config(), "sender@example.org"))

    def test_email_guard_and_recipient_changes(self):
        config = self.config()
        config["email"] = dict(enabled=True, owner_approved=True, sender="sender@example.org", recipients=["admin@example.org"], approved_recipients=["admin@example.org"])
        self.assertTrue(pilot.delivery_allowed(config, "sender@example.org"))
        self.assertFalse(pilot.delivery_allowed(config, "other@example.org"))
        config["email"]["recipients"].append("new@example.org")
        self.assertFalse(pilot.delivery_allowed(config, "sender@example.org"))

    def test_run_key(self):
        self.assertEqual(pilot.run_key("2026-11-19"), "grant-watch:2026-11-19")
        with self.assertRaises(ValueError):
            pilot.run_key("tomorrow")


class ReportingRegressionTests(unittest.TestCase):
    # Synthetic cases only: no mailbox contents or real funder assumptions.
    def report(self, state="complete", **counts):
        return pilot.run_summary(state, "2026-11-18T08:00:00+02:00",
                                 "2026-11-18T08:05:00+02:00",
                                 **dict(dict(due=5, checked=5, new_findings=0), **counts))

    def test_successful_findings_and_empty_are_explicit(self):
        self.assertIn("3 new findings", self.report(new_findings=3))
        self.assertIn("no new findings in checked sources", self.report())
        self.assertIn("08:05:00+02:00", self.report())
        self.assertIn("sources 5/5", self.report())

    def test_failure_partial_and_running_never_claim_empty_success(self):
        for state in ("failed", "partial", "running", "blocked"):
            result = self.report(state, checked=2, failures=1)
            self.assertIn("incomplete", result)
            self.assertNotIn("no new findings", result)
            self.assertIn("started", result)
            self.assertIn("updated", result)

    def test_no_due_sources_is_not_no_changes(self):
        self.assertIn("no sources due", self.report(due=0, checked=0))

    def test_false_completion_rejected(self):
        for counts in (dict(checked=4), dict(checked=4, failures=1), dict(checked=6)):
            with self.assertRaises(ValueError):
                self.report(**counts)

    def test_bad_times_rejected(self):
        for start, end in (("2026-11-18T08:00:00", "2026-11-18T08:05:00"),
                           ("2026-11-18T08:00:00+02:00", "2026-11-18T07:59:00+02:00")):
            with self.assertRaises(ValueError):
                pilot.run_summary("complete", start, end, 1, 1, 0)

    def test_expired_mandatory_stage_not_rescued_by_future_full_deadline(self):
        self.assertEqual(pilot.entry_status(True, True, False), "closed_to_new_applicants")
        self.assertEqual(pilot.entry_status(True, True, None), "closed_to_new_applicants")
        self.assertEqual(pilot.entry_status(True, True, True), "continuing_applicant_only_check_final_stage")

    def test_unknown_stage_requires_verification(self):
        self.assertEqual(pilot.entry_status(None, False, False), "verify_prerequisites")
        self.assertEqual(pilot.entry_status(True, None, False), "verify_prerequisites")
        self.assertEqual(pilot.entry_status(False, True, False), "no_mandatory_preproposal")

    def test_changed_eligibility_never_inherits_historical_eligibility(self):
        self.assertEqual(pilot.institution_status(True, False, True), "review_changed_criteria")
        self.assertEqual(pilot.institution_status(False, True, True), "ineligible_current_evidence")
        self.assertEqual(pilot.institution_status(True, True, True), "eligible_current_evidence")
        self.assertEqual(pilot.institution_status(None, True), "verification_pending")


class EmailPreparationTests(unittest.TestCase):
    def config(self):
        return {"email": dict(enabled=True, owner_approved=True,
                             sender="sender@example.org",
                             recipients=["one@example.org", "two@example.org"],
                             approved_recipients=["one@example.org", "two@example.org"])}

    def test_exact_recipients_and_safe_html(self):
        result = pilot.prepare_email(self.config(), "sender@example.org", "2026-11-18",
                                     "Report <script>alert(1)</script>", "ready")
        self.assertEqual(result["to"], "one@example.org, two@example.org")
        self.assertNotIn("cc", result)
        self.assertNotIn("bcc", result)
        self.assertIn("&lt;script&gt;", result["payload"]["body"]["content"])
        self.assertIn("grant-watch:2026-11-18", result["subject"])

    def test_no_blind_resend(self):
        for state in ("sent", "pending", "uncertain", "disabled", "blocked"):
            with self.assertRaises(ValueError):
                pilot.prepare_email(self.config(), "sender@example.org", "2026-11-18", "Report", state)

    def test_wrong_sender_and_unapproved_recipient_blocked(self):
        c = self.config()
        with self.assertRaises(ValueError):
            pilot.prepare_email(c, "other@example.org", "2026-11-18", "Report", "ready")
        c["email"]["recipients"].append("extra@example.org")
        with self.assertRaises(ValueError):
            pilot.prepare_email(c, "sender@example.org", "2026-11-18", "Report", "ready")


if __name__ == "__main__":
    unittest.main()
