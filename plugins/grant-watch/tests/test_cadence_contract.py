import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).parents[1] / "scripts" / "cadence_contract.py"
spec = importlib.util.spec_from_file_location("cadence", SCRIPT)
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


class CadenceTests(unittest.TestCase):
    def row(self, key="program/round-A", **updates):
        return dict(dict(key=key, program_key="program", active=True,
                         initial_due="2026-11-18T06:00:00Z", timeline={}), **updates)

    def fact(self, value):
        return dict(value=value, source_url="https://funder.example/call", certainty="verified",
                    verified_at="2026-11-24T06:00:00Z", original_text=value)

    def test_independent_settings_and_rounds(self):
        settings = c.defaults()
        settings["announced_unpublished"]["interval_days"] = 14
        self.assertEqual(settings["no_next_dates"]["interval_days"], 7)
        a = self.row(published_verified=True, open_verified=True)
        b = self.row("program/round-B", announced_verified=True)
        p = c.plan_index([a, b], c.stamp("2026-11-24T06:00:00Z"), settings)
        self.assertEqual(len(p["selected"]), 2)
        self.assertEqual({r["schedule"]["stage"] for r in p["selected"]}, {"open", "announced_unpublished"})
        with self.assertRaises(ValueError):
            c.record_observation(a, dict(key=b["key"], observed_at="2026-11-25T06:00:00Z"))

    def test_twice_weekly_no_six_day_drift_or_weekend_runs(self):
        for key in ("a", "b", "c", "d", "e", "f"):
            first = c.regular_due(key, "open", c.stamp("2026-11-24T06:00:00Z"), c.defaults())
            for _ in range(20):
                nxt = c.regular_due(key, "open", first, c.defaults())
                self.assertIn((nxt.date() - first.date()).days, (3, 4))
                self.assertIn(nxt.weekday(), c.RUN_DAYS)
                self.assertEqual(nxt.astimezone(c.TZ).hour, 8)
                first = nxt

    def test_stage_boundary_weekend_and_no_false_open(self):
        row = self.row(published_verified=True, last_success="2026-11-25T06:00:00Z",
                       timeline={"opening": self.fact("2026-11-27T10:00:00+02:00"),
                                 "deadline": self.fact("2026-11-28T15:00:00+02:00")})
        thursday = c.stamp("2026-11-26T06:00:00Z")
        result = c.due_state(row, thursday, c.defaults())
        self.assertTrue(result["due"])
        self.assertEqual(result["reason"], "opening_boundary")
        self.assertEqual(result["boundaries"][0]["following_wake"], "2026-11-29T06:00:00Z")
        checked = c.complete_attempt(row, thursday, c.defaults(), success=True)
        friday = c.stamp("2026-11-27T09:00:00Z")
        self.assertEqual(c.effective_state(checked, friday)[1], "opening_or_status_requires_verification")
        sunday = c.stamp("2026-11-29T06:00:00Z")
        self.assertEqual(c.effective_state(checked, sunday)[0], "no_next_dates")
        self.assertTrue(c.due_state(checked, sunday, c.defaults())["due"])

    def test_failure_keeps_debt_and_cannot_starve_other_targets(self):
        now = c.stamp("2026-11-24T06:00:00Z")
        a = self.row("a")
        failed = c.complete_attempt(a, now, c.defaults(), success=False, error="403")
        self.assertNotIn("last_success", failed)
        self.assertGreater(failed["schedule"]["overdue_hours"], 0)
        p = c.plan_index([failed, self.row("b")], now, c.defaults(), call_limit=1)
        self.assertEqual(p["selected"][0]["key"], "b")

    def test_url_redirect_preserves_old_until_verified_and_rejects_unsafe(self):
        a = self.row(official_url="https://funder.example/old")
        b = c.observe_url(a, "https://new.example/call", "2026-11-24T06:00:00Z")
        self.assertEqual(a["official_url"], b["official_url"])
        self.assertEqual(len(b["url_history"]), 1)
        d = c.observe_url(b, b["candidate_url"], "2026-11-25T06:00:00Z",
                          replacement_verified=True, evidence_url="https://funder.example/moved")
        self.assertEqual(d["official_url"], "https://new.example/call")
        self.assertEqual(len(d["url_history"]), 2)
        for url in ("javascript:alert(1)", "file:///x", "https://secret:pass@example.org"):
            with self.assertRaises(ValueError): c.observe_url(a, url, "2026-11-24T06:00:00Z")

    def test_revision_provenance_replay_stale_and_round_isolation(self):
        a = self.row()
        b = self.row("program/round-B")
        observation = dict(key=a["key"], observed_at="2026-11-24T06:00:00Z",
                           timeline={k: self.fact("2026-12-01") for k in c.KINDS})
        updated = c.record_observation(a, observation)
        replay = c.record_observation(updated, observation)
        self.assertEqual(replay, updated)
        self.assertEqual(b["timeline"], {})
        self.assertEqual(a["timeline"], {})
        with self.assertRaises(ValueError):
            c.record_observation(updated, dict(observation, observed_at="2026-11-23T06:00:00Z"))
        bad = copy.deepcopy(observation)
        bad["timeline"]["deadline"].pop("source_url")
        with self.assertRaises(ValueError): c.record_observation(a, bad)

    def test_scan_limit_counts_rejected_items_without_lookahead(self):
        consumed = []
        def generator():
            for i in range(1000):
                consumed.append(i)
                yield self.row(str(i), active=False)
        result = c.plan_index(generator(), c.stamp("2026-11-24T06:00:00Z"), c.defaults(), scan_limit=7)
        self.assertEqual(len(consumed), 7)
        self.assertEqual(result["scanned_index_rows"], 7)
        self.assertEqual(result["held"], 7)
        self.assertTrue(result["unscanned_work_remains"])

    def test_cursor_and_boundary_reservation_under_backlog(self):
        now = c.stamp("2026-11-24T06:00:00Z")
        rows = [self.row(str(i)) for i in range(40)]
        urgent = self.row("urgent", published_verified=True, open_verified=True,
                          last_success="2026-11-23T06:00:00Z",
                          timeline={"deadline": self.fact("2026-11-24T17:00:00+01:00")})
        result = c.plan_index(rows + [urgent], now, c.defaults(), call_limit=3)
        self.assertEqual(result["selected"][0]["key"], "urgent")
        self.assertEqual(len(result["selected"]), 3)
        self.assertEqual(result["planned_successes"], 0)
        tied = c.plan_index([self.row("a"), self.row("b")], now, c.defaults(), call_limit=1, cursor="a")
        self.assertEqual(tied["selected"][0]["key"], "b")

    def test_actual_cli_snapshot_is_no_send_and_does_not_mutate_input(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "input.json"
            out = Path(folder) / "plan.json"
            data = dict(now="2026-11-24T06:00:00Z", settings=c.defaults(),
                        rows=[self.row(), self.row("program/round-B", announced_verified=True)],
                        limits=dict(call_limit=1, scan_limit=2, remaining_work=True))
            original = json.dumps(data)
            path.write_text(original, encoding="utf-8")
            result = subprocess.run([sys.executable, str(SCRIPT), str(path), "--output", str(out)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(out.read_text(encoding="utf-8"))
            self.assertTrue(plan["no_send"])
            self.assertEqual(len(plan["selected"]), 1)
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_fresh_setting_shortens_due_despite_cached_native_date(self):
        row = self.row(last_success="2026-11-01T06:00:00Z", schedule={"next_due":"2027-01-01T06:00:00Z"})
        settings = c.defaults()
        settings["no_next_dates"]["interval_days"] = 60
        now = c.stamp("2026-11-24T06:00:00Z")
        self.assertFalse(c.due_state(row, now, settings)["due"])
        settings["no_next_dates"]["interval_days"] = 7
        self.assertTrue(c.due_state(row, now, settings)["due"])

    def test_registered_source_budget_also_limits_selection(self):
        rows = [self.row(str(i), registered_sources=["source/"+str(i)]) for i in range(25)]
        result = c.plan_index(rows, c.stamp("2026-11-24T06:00:00Z"), c.defaults())
        self.assertEqual(len(result["selected"]), 20)
        self.assertEqual(result["registered_sources_selected"], 20)
        self.assertEqual(result["deferred_in_scanned_rows"], 5)

    def test_actual_outcome_cli_projects_failure_without_write_or_send(self):
        with tempfile.TemporaryDirectory() as folder:
            path, out = Path(folder)/"input.json", Path(folder)/"output.json"
            row = self.row(last_success="2026-11-01T06:00:00Z")
            data = dict(operation="complete_attempt", now="2026-11-24T06:00:00Z", target=row,
                        settings_rows=[{"Key":key,"Interval days":value["interval_days"]} for key,value in c.defaults().items()],
                        settings_pages={key:"https://notion.example/"+key for key in c.STAGES},
                        outcome=dict(success=False,error="source unavailable"))
            path.write_text(json.dumps(data),encoding="utf-8")
            result = subprocess.run([sys.executable,str(SCRIPT),str(path),"--output",str(out)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            plan=json.loads(out.read_text(encoding="utf-8"))
            self.assertTrue(plan["no_send"])
            self.assertFalse(plan["notion_write_performed"])
            self.assertEqual(plan["target"]["last_success"],row["last_success"])
            self.assertEqual(plan["index_properties"]["date:Last attempt:start"],"2026-11-24T06:00:00Z")

    def test_actual_cli_two_rounds_weekend_boundaries_outrank_backlog(self):
        rounds = [self.row("same-program/round-" + key, published_verified=True,
            last_success="2026-11-25T06:00:00Z", open_verified=False,
            timeline={"opening": self.fact("2026-11-27T10:00:00+02:00"),
                      "deadline": self.fact(deadline)}) for key, deadline in (
                ("A", "2026-11-28T15:00:00+02:00"), ("B", "2026-12-05T15:00:00+02:00"))]
        backlog = [self.row("backlog/" + str(i)) for i in range(40)]
        with tempfile.TemporaryDirectory() as folder:
            for label, now in (("thursday", "2026-11-26T06:00:00Z"), ("sunday", "2026-11-29T06:00:00Z")):
                if label == "sunday":
                    for row in rounds:
                        row["last_success"] = "2026-11-26T06:00:00Z"
                source, output = Path(folder)/(label+".json"), Path(folder)/(label+"-plan.json")
                original = json.dumps(dict(now=now, settings=c.defaults(), rows=backlog+rounds,
                                           limits=dict(call_limit=2, scan_limit=50, remaining_work=True)))
                source.write_text(original, encoding="utf-8")
                result = subprocess.run([sys.executable, str(SCRIPT), str(source), "--output", str(output)],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                plan = json.loads(output.read_text(encoding="utf-8"))
                self.assertTrue(plan["no_send"])
                self.assertEqual(plan["planned_successes"], 0)
                self.assertEqual(plan["scanned_index_rows"], 42)
                self.assertEqual(plan["deferred_in_scanned_rows"], 40)
                self.assertEqual(plan["boundary_deferred_in_slice"], 0)
                self.assertEqual({r["key"] for r in plan["selected"]}, {r["key"] for r in rounds})
                self.assertEqual(source.read_text(encoding="utf-8"), original)
                states = {r["key"][-1]: r["schedule"] for r in plan["selected"]}
                if label == "thursday":
                    self.assertEqual({s["stage"] for s in states.values()}, {"published_future"})
                else:
                    self.assertEqual(states["A"]["stage"], "no_next_dates")
                    self.assertEqual(states["B"]["calendar_status"], "opening_or_status_requires_verification")

    def test_overdue_discovery_does_not_hide_a_due_boundary(self):
        row = self.row("urgent", published_verified=True,
                       timeline={"opening": self.fact("2026-11-25T08:00:00+02:00")})
        plan = c.plan_index([self.row("old"), row], c.stamp("2026-11-25T06:00:00Z"), c.defaults(), call_limit=1)
        self.assertEqual(plan["selected"][0]["key"], "urgent")
        self.assertTrue(plan["selected"][0]["schedule"]["boundary_due"])


if __name__ == "__main__": unittest.main()
