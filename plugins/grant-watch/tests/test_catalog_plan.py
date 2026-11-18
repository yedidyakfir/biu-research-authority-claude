import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("catalog", Path(__file__).parents[1] / "scripts" / "catalog_plan.py")
catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalog)


class CatalogPlanTests(unittest.TestCase):
    def source(self, i):
        return dict(source_key=str(i), active=True, due=True)

    def call(self, i, **changes):
        return dict(dict(program_key=str(i), cycle="2027", active=True, due=True), **changes)

    def test_defaults_and_resumable_deferral_counts(self):
        p = catalog.plan_run([self.source(i) for i in range(25)], [self.call(i) for i in range(35)])
        self.assertEqual((p["discovery_sources_selected"],p["calls_selected"]), (20,30))
        self.assertEqual((p["deferred_sources"],p["deferred_calls"]), (5,5))
        self.assertEqual(p["budgets"]["requests"],100)
        self.assertEqual(p["planned_successes"],0)
        self.assertIsNone(p["new_discoveries_count"])

    def test_unique_program_cycles_not_observation_count(self):
        p = catalog.plan_run([self.source(1)]*3, [self.call(1)]*5+[self.call(1,cycle="2028")])
        self.assertEqual(p["discovery_sources_due"],1)
        self.assertEqual(p["known_unique_calls_due"],2)

    def test_review_and_skips_separate_from_lightweight_checks(self):
        p = catalog.plan_run([], [self.call(1,eligibility_review_due=False),self.call(2),
                                  self.call(3,confirmed_excluded_same_cycle=True)])
        self.assertEqual((p["lightweight_status_checks"],p["eligibility_reviews"],p["confirmed_excluded_skips"]),(2,1,1))

    def test_conflicts_and_ambiguity_not_silently_merged(self):
        p = catalog.plan_run([], [self.call(1),self.call(1,eligibility_review_due=False),self.call(2,identity_ambiguous=True)])
        self.assertEqual(p["conflicting_call_keys"],1)
        self.assertEqual(p["ambiguity_reviews"],1)
        self.assertEqual(p["calls_selected"],0)

    def test_inactive_not_due_and_configured_limit(self):
        p = catalog.plan_run([], [self.call(1,active=False),self.call(2,due=False),self.call(3),self.call(4)],dict(calls=1))
        self.assertEqual(p["calls_selected"],1)
        self.assertEqual(p["deferred_calls"],1)

    def test_invalid_configuration_or_identity_rejected(self):
        for b in (dict(calls=0),dict(requests=True),dict(extra=1),dict(max_retries=-1)):
            with self.assertRaises(ValueError):catalog.plan_run([],[],b)
        with self.assertRaises(ValueError):catalog.plan_run([],[self.call(1,cycle="")])


if __name__ == "__main__": unittest.main()
