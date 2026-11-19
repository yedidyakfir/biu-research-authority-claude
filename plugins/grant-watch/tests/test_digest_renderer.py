"""Synthetic fixtures only; private reports and approved mail stay outside Git."""
import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


renderer = load("digest_renderer")
pilot = load("pilot_contract")
eligibility = load("eligibility_contract")


class Links(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.urls = []
        self.tags = []
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        if tag == "a":
            self.urls.append(dict(attrs)["href"])


def row(**values):
    return dict(dict(name="Example research call", official_source="https://example.org/call?a=1&b=2",
                     summary="Full proposal; personal conditions apply.",
                     caveats="BIU eligibility and internal deadline unknown.",
                     deadlines=[dict(stage="LOI", date="2030-02-02", time="17:00",
                                     timezone="CET", certainty="verified")]), **values)


def config():
    keys = ("workspace_id", "root_page_id", "sources_database_id", "rounds_database_id",
            "events_database_id", "runs_database_id")
    return dict({key: str(i + 1) * 32 for i, key in enumerate(keys)},
                workspace_verified_by_owner=True, timezone="Asia/Jerusalem",
                email=dict(enabled=True, owner_approved=True, sender="sender@example.org",
                           recipients=["admin@example.org"], approved_recipients=["admin@example.org"]))


class DigestRendererTests(unittest.TestCase):
    def test_all_sections_preserve_fields_links_and_exclude_audit(self):
        report = dict(state="partial", summary=["Partial: 3 calls checked; 1 source unavailable."],
                      main=[row()], unverified_lead=[row(name="Pending cycle", reason="Cycle uncertain")],
                      non_eligible=[row(name="Confirmed exclusion", reason="Institution excluded for this cycle")],
                      audit="PRIVATE_AUDIT", recipients=["PRIVATE_RECIPIENT"])
        report["main"][0]["request_evidence"] = "PRIVATE_REQUEST_LOG"
        report["main"][0]["source_urls"] = ["https://example.org/terms"]
        before = copy.deepcopy(report)
        rendered = renderer.render_digest(report, "2030-02-01")
        self.assertEqual(rendered, renderer.render_digest(report, "2030-02-01"))
        self.assertEqual(report, before)
        for text in ("מועדי מממנים שאומתו", "ממתינים לאימות מול המממן", "אי־זכאות מוסדית שאומתה",
                     "17:00", "CET", "BIU eligibility", "Cycle uncertain", "Institution excluded", "2 בפברואר"):
            self.assertIn(text, rendered)
        self.assertEqual(len(Links(rendered).urls), 4)
        self.assertIn("https://example.org/call?a=1&b=2", Links(rendered).urls)
        self.assertNotIn("PRIVATE_", rendered)

    def test_existing_eligibility_routing_survives_rendering(self):
        report = dict(state="partial", main=[], unverified_lead=[], non_eligible=[])
        for name, status, flags in (("Unknown BIU", "unknown", True),
                                    ("Confirmed excluded", "excluded", True),
                                    ("Funder date uncertain", "unknown", False)):
            review = row(name=name, call_id="example-call", cycle="2030", evidence_verified=True,
                         institution_status=status, reason="Current-cycle sourced restriction" if status == "excluded" else "Unknown terms",
                         verified_at="2030-02-01T08:00:00+02:00")
            section = eligibility.digest_section("example-call", "2030", review,
                        funder_call_verified=True, funder_deadline_verified=flags, funder_entry_verified=True)
            report[section].append(review)
        self.assertEqual([r["name"] for r in report["main"]], ["Unknown BIU"])
        content = renderer.render_digest(report, "2030-02-01")
        self.assertLess(content.index("Unknown BIU"), content.index("ממתינים לאימות"))
        self.assertGreater(content.index("Confirmed excluded"), content.index("אי־זכאות"))
        self.assertGreater(content.index("Funder date uncertain"), content.index("ממתינים לאימות"))

    def test_empty_complete_and_incomplete_are_distinct(self):
        complete = renderer.render_digest(dict(state="complete"), "2030-02-01")
        partial = renderer.render_digest(dict(state="partial"), "2030-02-01")
        self.assertIn("בבדיקה שהושלמה", complete)
        self.assertIn("הבדיקה לא הושלמה", partial)
        self.assertNotIn("בבדיקה שהושלמה", partial)
        self.assertNotIn("המועד הקרוב שאומת", complete)
        self.assertEqual(Links(complete).urls, [])
        for invalid in ({}, dict(state="running"), None):
            with self.assertRaises(ValueError):
                renderer.render_digest(invalid, "2030-02-01")

    def test_unsafe_input_is_escaped_or_rejected(self):
        text = '<script>alert("x")</script> & ${name} שלום\nSecond line'
        rendered = renderer.render_digest(dict(state="partial", summary=text,
                                             main=[row(name=text, summary=text, caveats=text)]), "2030-02-01")
        self.assertNotIn("script", Links(rendered).tags)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("${name}", rendered)
        self.assertIn('dir="rtl"', rendered)
        for url in ("javascript:alert(1)", "data:text/html,hi", "//example.org", "ftp://example.org",
                    "https://example.org/a b", "https://user:pass@example.org", "https://example.org/\n",
                    "https://example.org\\@evil.org", "https://example.org:bad", "https://example.org/\u202e", 7):
            with self.subTest(url=url), self.assertRaises(ValueError):
                renderer.render_digest(dict(state="partial", main=[row(official_source=url)]), "2030-02-01")

    def test_recurring_and_forecast_remain_pending_without_invented_year(self):
        report = dict(state="partial", unverified_lead=[row(deadlines=[dict(stage="Pre-proposal",
                            source_text="Every 1 May / 1 December; prior approval two weeks earlier",
                            certainty="recurring rule; occurrence unverified", timezone="Unknown")])])
        result = renderer.render_digest(report, "2030-02-01")
        self.assertIn("occurrence unverified", result)
        self.assertIn("prior approval two weeks earlier", result)
        self.assertNotIn("המועד הקרוב שאומת", result)
        self.assertNotIn("2030-05-01", result)

    def test_highlight_counts_rows_not_multiple_milestones_and_ignores_past(self):
        item = row()
        item["deadlines"] *= 2
        result = renderer.render_digest(dict(state="complete", main=[item, row()]), "2030-02-01")
        self.assertIn("שני קולות קוראים", result)
        later = renderer.render_digest(dict(state="complete", main=[item]), "2030-02-03")
        self.assertNotIn("המועד הקרוב שאומת", later)

    def test_structured_preparation_preserves_delivery_guards(self):
        report = dict(state="partial", main=[row()])
        result = pilot.prepare_email(config(), "sender@example.org", "2030-02-01", report, "ready")
        self.assertEqual(result["subject"], "BIU Grant Watch | 2030-02-01 | grant-watch:2030-02-01")
        self.assertEqual(set(result), {"from_address", "to", "subject", "payload"})
        self.assertEqual(result["to"], "admin@example.org")
        self.assertIn("עדכון מענקי מחקר", result["payload"]["body"]["content"])
        for state in ("sent", "pending", "uncertain", "blocked"):
            with self.assertRaises(ValueError):
                pilot.prepare_email(config(), "sender@example.org", "2030-02-01", report, state)
        for key, value in (("sender", "other@example.org"), ("recipients", ["other@example.org"])):
            altered = config()
            altered["email"][key] = value
            with self.assertRaises(ValueError):
                pilot.prepare_email(altered, "sender@example.org", "2030-02-01", report, "ready")

    def test_plain_fallback_still_escapes_without_restyling(self):
        content = pilot.prepare_email(config(), "sender@example.org", "2030-02-01",
                                       "Plain <b>report</b> & recurring 1 May", "ready")["payload"]["body"]["content"]
        self.assertIn("&lt;b&gt;report&lt;/b&gt;", content)
        self.assertIn("recurring 1 May", content)

    def test_cli_from_unrelated_directory_is_prepare_only_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "config.json").write_text(json.dumps(config()), encoding="utf-8")
            (root / "report.json").write_text(json.dumps(dict(state="partial", main=[row()])), encoding="utf-8")
            args = [sys.executable, str(SCRIPTS / "pilot_contract.py"), "config.json", "--workspace", "1" * 32,
                    "--report", "report.json", "--report-day", "2030-02-01", "--connected-sender", "sender@example.org",
                    "--delivery-state", "ready", "--output", "prepared.json"]
            run = subprocess.run(args, cwd=root, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(run.stdout)["sent"], False)
            prepared = json.loads((root / "prepared.json").read_text(encoding="utf-8"))
            self.assertEqual(prepared["payload"]["mime_type"], "text/html")
            self.assertIn("עדכון מענקי מחקר", prepared["payload"]["body"]["content"])
            self.assertNotEqual(subprocess.run(args, cwd=root, capture_output=True).returncode, 0)


if __name__ == "__main__":
    unittest.main()
