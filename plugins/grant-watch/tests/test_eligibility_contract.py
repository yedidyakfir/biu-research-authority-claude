import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("eligibility", Path(__file__).parents[1] / "scripts" / "eligibility_contract.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class EligibilityTests(unittest.TestCase):
    def record(self, **changes):
        return dict(dict(call_id="example/call", cycle="2027", name="Example grant",
                         institution_status="excluded", personal_status="unknown",
                         reason="Example institutional restriction", official_source="https://example.org/call",
                         evidence_verified=True, verified_at="2026-11-18T10:00:00+02:00",
                         otherwise_relevant=True,
                         deadlines=[dict(date="2027-01-15", stage="full", certainty="verified", timezone="Asia/Jerusalem")]), **changes)

    def test_only_same_confirmed_cycle_skips(self):
        self.assertFalse(module.should_recheck("example/call", "2027", self.record()))
        self.assertTrue(module.should_recheck("example/call", "2028", self.record()))
        self.assertTrue(module.should_recheck("other/call", "2027", self.record()))
        self.assertTrue(module.should_recheck("example/call", "2027", self.record(), True))

    def test_unknown_or_missing_evidence_never_skips(self):
        for change in [dict(institution_status="unknown"), dict(evidence_verified=False),
                       dict(reason=""), dict(official_source=""), dict(verified_at="2026-11-18"), dict(cycle="")]:
            self.assertTrue(module.should_recheck("example/call", "2027", self.record(**change)))

    def test_personal_restriction_does_not_exclude_all_biu(self):
        self.assertTrue(module.should_recheck("example/call", "2027", self.record(institution_status="eligible", personal_status="restricted")))

    def test_skipped_cycle_keeps_dates_in_footer(self):
        text = module.restricted_footer([self.record()], "2026-11-18")
        for expected in ["Upcoming calls", "Example institutional restriction", "full: 2027-01-15", "https://example.org/call", "personal: unknown"]:
            self.assertIn(expected, text)

    def test_forecast_stays_labeled_not_verified(self):
        text = module.restricted_footer([self.record(institution_status="unknown", deadlines=[dict(date="2027-02-01", stage="forecast", certainty="forecast")])], "2026-11-18")
        self.assertIn("forecast", text)
        self.assertIn("Institution: unknown", text)

    def test_expired_irrelevant_and_fully_eligible_not_in_footer(self):
        for change in [dict(otherwise_relevant=False), dict(institution_status="eligible", personal_status="eligible"),
                       dict(deadlines=[dict(date="2020-01-01")]), dict(deadlines=[dict(date="unknown")])]:
            self.assertEqual(module.restricted_footer([self.record(**change)], "2026-11-18"), "")


if __name__ == "__main__":
    unittest.main()
