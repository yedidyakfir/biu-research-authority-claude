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


if __name__ == "__main__": unittest.main()
