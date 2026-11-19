import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("audit", Path(__file__).parents[1] / "scripts" / "audit_contract.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class AuditTests(unittest.TestCase):
    def row(self, **changes):
        args = dict(run_key="grant-watch:2026-11-18", item_key="example/program", attempt=1,
                    kind="source", name="Example source", outcome="checked_unchanged",
                    started_at="2026-11-18T08:00:00+02:00", finished_at="2026-11-18T08:01:00+02:00",
                    source_url="https://example.org/program")
        return audit.audit_row(**dict(args, **changes))

    def test_stable_retry_key_but_new_attempt_distinct(self):
        self.assertEqual(self.row()["Key"], self.row()["Key"])
        self.assertNotEqual(self.row()["Key"], self.row(attempt=2)["Key"])
        self.assertNotEqual(self.row()["Key"], self.row(kind="grant")["Key"])

    def test_skips_and_unattempted_never_claim_search(self):
        for status in ("skipped", "not_attempted"):
            self.assertEqual(self.row(outcome=status, reason="Stored exclusion or time budget")["Actually attempted"], "__NO__")
        self.assertEqual(self.row(outcome="error", reason="HTTP 503")["Actually attempted"], "__YES__")

    def test_reasons_required_and_payloads_bounded(self):
        for change in [dict(outcome="error"), dict(outcome="skipped"), dict(outcome="not_attempted"),
                       dict(reason="x" * 1501), dict(source_url="x" * 2001)]:
            with self.assertRaises(ValueError): self.row(**change)

    def test_ordered_aware_timestamps(self):
        for finish in ("2026-11-18T07:00:00+02:00", "2026-11-18T08:01:00"):
            with self.assertRaises(ValueError): self.row(finished_at=finish)

    def test_small_pagination_without_dropping_rows(self):
        rows = [self.row(attempt=i) for i in range(1, 106)]
        pages = list(audit.audit_batches(rows))
        self.assertEqual(list(map(len, pages)), [40, 40, 25])
        self.assertEqual([r for p in pages for r in p], rows)
        with self.assertRaises(ValueError): list(audit.audit_batches(rows, 100))

    def test_audit_has_no_email_payload(self):
        self.assertTrue(set(self.row()).isdisjoint({"to", "subject", "payload", "digest", "attachment"}))


class CoverageReportingTests(unittest.TestCase):
    # November 19 regression counts only; no funder, workspace or recipient data.
    NOVEMBER_19 = dict(discovery_due=1, discovery_checked=1, discovery_failed=0,
                       call_checks=28, call_observations=20, call_failed=8,
                       support_checks=1, support_succeeded=0, support_failed=1,
                       identity_only_reviews=2, deferred_queue_records=514)

    def coverage(self, **changes):
        return audit.notion_run_coverage(**dict(self.NOVEMBER_19, **changes))

    def test_november_19_discovery_success_does_not_hide_other_failures(self):
        report = self.coverage()
        metrics = report["metrics"]
        self.assertEqual(metrics["discovery_sources"],
                         dict(due=1, checked=1, failed=0, unclassified_outcomes=0))
        self.assertEqual(metrics["program_calls"],
                         dict(attempted=28, with_observations=20, failed=8, unclassified_outcomes=0))
        self.assertEqual(metrics["internal_support_sources"],
                         dict(attempted=1, succeeded=0, failed=1, unclassified_outcomes=0))
        self.assertEqual(metrics["identity_only_reviews"], 2)
        self.assertEqual(metrics["deferred_queue_records"], 514)
        self.assertIn("Program/call checks: attempted 28; with observations 20; failed 8", report["summary"])
        self.assertIn("Internal support sources: attempted 1; succeeded 0; failed 1", report["summary"])
        self.assertIn("discovery sources only, not overall checks", report["summary"])

    def test_unresolved_checks_are_not_successes_or_empty_results(self):
        report = self.coverage(call_observations=18, support_failed=0)
        self.assertEqual(report["metrics"]["program_calls"]["unclassified_outcomes"], 2)
        self.assertEqual(report["metrics"]["internal_support_sources"]["unclassified_outcomes"], 1)
        self.assertNotIn("COMPLETE", report["summary"])
        self.assertNotIn("no new findings", report["summary"])

    def test_invalid_or_double_counted_outcomes_rejected(self):
        for change in (dict(discovery_failed=1), dict(call_observations=21),
                       dict(support_succeeded=1), dict(call_checks=-1),
                       dict(identity_only_reviews=True), dict(deferred_queue_records=1.5)):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.coverage(**change)

    def test_reporting_does_not_emit_operational_or_email_updates(self):
        report = self.coverage()
        self.assertEqual(set(report), {"metrics", "summary"})
        self.assertTrue(set(report["metrics"]).isdisjoint(
            {"Due", "Checked", "Failed", "Digest", "State", "Email state",
             "queue_cursor", "to", "subject", "payload", "attachment"}))


if __name__ == "__main__": unittest.main()
