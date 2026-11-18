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


if __name__ == "__main__":
    unittest.main()
