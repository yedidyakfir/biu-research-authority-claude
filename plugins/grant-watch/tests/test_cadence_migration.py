import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import cadence_contract as c
import cadence_migration as m


class MigrationTests(unittest.TestCase):
    def test_optional_question_date_does_not_close_round(self):
        review = dict(evidence_verified=True, official_source="https://example.org/call",
                      verified_at="2026-11-24T06:00:00Z", deadlines=[
                          dict(stage="Clarification questions (not entry requirement)", date="2026-10-01", certainty="verified"),
                          dict(stage="Proposal", date="2026-12-01", certainty="verified")])
        facts, flags, dates = m.lifecycle(review, {"Status": "open"}, c.stamp("2026-11-24T06:00:00Z"))
        self.assertEqual(facts["deadline"]["value"], "2026-12-01")
        self.assertEqual(len(dates), 1)
        self.assertIsNone(facts["publication"]["value"])

    def test_entry_closure_does_not_erase_invited_final_stage(self):
        target = dict(key="program/roundA", active=True, initial_due="2026-11-20T06:00:00Z",
                      published_verified=True, open_verified=True,
                      entry_deadline=dict(value="2026-11-20", certainty="verified"),
                      timeline={"deadline": dict(value="2027-02-01", certainty="verified")})
        self.assertEqual(c.effective_state(target, c.stamp("2026-11-24T06:00:00Z")),
                         ("open", "entry_deadline_elapsed_continuation_only_or_verify"))

    def test_same_program_two_rounds_survive_additive_migration(self):
        rounds = []
        for cycle in ("2027-A", "2027-B"):
            rounds.append(dict(row={"Key": "program/call/"+cycle, "Program key": "program", "Cycle": cycle,
                                    "Name": cycle, "Status": "verification_pending", "url": "https://notion.example/"+cycle},
                reviews=[dict(call_id="call", cycle=cycle, program_key="program", evidence_verified=True,
                              official_source="https://funder.example/"+cycle, verified_at="2026-11-24T06:00:00Z",
                              deadlines=[dict(stage="Full proposal",date="2027-02-01",certainty="verified")])]))
        snapshot = dict(rounds=rounds, queues=[], config=dict(operational_catalog=dict(
            queue_cursor=dict(upcoming_next=dict(cycle_key="x"),unknown_next=dict(program_key="y")), budgets=dict(calls=30))))
        original = copy.deepcopy(snapshot)
        result = m.build(snapshot, {s:"https://notion.example/"+s for s in c.STAGES}, c.stamp("2026-11-24T06:00:00Z"))
        self.assertEqual(len(result["targets"]), 2)
        self.assertEqual(len({r["key"] for r in result["targets"]}), 2)
        self.assertEqual(snapshot, original)
        self.assertEqual(result["counts"]["existing_rounds"], 2)

    def test_native_adapter_and_unusual_unknowns(self):
        row = {"Key": "round\\:A", "Program key": "p", "Active": "__YES__",
               "date:Initial due:start": "2026-11-24T06:00:00Z", "Published verified": "__YES__",
               "date:Final deadline:start": "2026-12-01", "date:Entry deadline:start": "2026-11-20"}
        target = c.target_from_notion(row)
        self.assertEqual(target["key"], "round:A")
        self.assertEqual(target["timeline"]["deadline"]["value"], "2026-12-01")
        self.assertIsNone(c.fact_instant(None))


if __name__ == "__main__": unittest.main()
