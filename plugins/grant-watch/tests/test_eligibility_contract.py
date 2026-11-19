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
        for expected in ["Confirmed BIU-ineligible calls", "Example institutional restriction", "full: 2027-01-15", "https://example.org/call", "personal: unknown"]:
            self.assertIn(expected, text)

    def test_forecast_stays_labeled_not_verified(self):
        text = module.restricted_footer([self.record(deadlines=[dict(date="2027-02-01", stage="forecast", certainty="forecast")])], "2026-11-18")
        self.assertIn("forecast", text)
        self.assertIn("Institution: excluded", text)

    def route(self, review, **flags):
        decisions = dict(funder_call_verified=True, funder_deadline_verified=True,
                         funder_entry_verified=True)
        return module.digest_section("example/call", "2027", review,
                                     **dict(decisions, **flags))

    def test_unknown_biu_terms_stay_in_main_with_verified_funder_facts(self):
        review = self.record(institution_status="unknown", personal_status="unknown", reason="",
                             biu_internal_deadline={"status": "unknown"},
                             biu_portal_available=False)
        self.assertEqual(self.route(review), "main")
        self.assertEqual(module.restricted_footer([review], "2026-11-19"), "")

    def test_lead_and_individual_restrictions_are_not_blanket_exclusions(self):
        for change in (dict(institution_status="restricted", reason="Partner route possible; lead restricted"),
                       dict(institution_status="eligible", personal_status="restricted"),
                       dict(institution_status="unknown", personal_status="restricted")):
            review = self.record(**change)
            self.assertEqual(self.route(review), "main")
            self.assertEqual(module.restricted_footer([review], "2026-11-19"), "")

    def test_only_confirmed_current_cycle_exclusion_routes_non_eligible(self):
        self.assertEqual(self.route(self.record()), "non_eligible")
        self.assertEqual(self.route(self.record(evidence_verified=False)), "unverified_lead")
        self.assertEqual(self.route(self.record(cycle="2026")), "unverified_lead")

    def test_unverified_funder_facts_do_not_become_open_calls(self):
        review = self.record(institution_status="unknown")
        for key in ("funder_call_verified", "funder_deadline_verified", "funder_entry_verified"):
            with self.subTest(key=key):
                self.assertEqual(self.route(review, **{key: False}), "unverified_lead")
                with self.assertRaises(ValueError):
                    self.route(review, **{key: None})
        self.assertEqual(self.route(self.record(institution_status="unknown", cycle="")), "unverified_lead")

    def test_unverified_institutional_label_is_not_enough_for_footer(self):
        self.assertEqual(module.restricted_footer([self.record(evidence_verified=False)], "2026-11-19"), "")

    def test_expired_irrelevant_and_fully_eligible_not_in_footer(self):
        for change in [dict(otherwise_relevant=False), dict(institution_status="eligible", personal_status="eligible"),
                       dict(deadlines=[dict(date="2020-01-01")]), dict(deadlines=[dict(date="unknown")])]:
            self.assertEqual(module.restricted_footer([self.record(**change)], "2026-11-18"), "")


if __name__ == "__main__":
    unittest.main()
