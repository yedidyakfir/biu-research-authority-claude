"""Checks for human-only learning, rule compaction, conflict handling, and retention."""

from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from memory_store import (add_rule, connect, history, ingest_decision, ingest_reviewed_report,
                          prune, rules, suggest_report)  # noqa: E402
from memory_server import handler_for  # noqa: E402


PROGRAM = "Personal research grants"


def decision(row, description, category="services", status="classified"):
    return {
        "foundation_id": "isf", "program": PROGRAM, "source_sha256": "a" * 64,
        "row": row, "classification": {"status": status, "category_id": category,
                                      "rationale": "Reviewer checked the invoice"},
        "evidence": {"account": "400 - lab services", "description": description, "reference": "invoice"},
    }


class MemoryTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.db = connect(Path(self.folder.name) / "memory.sqlite3")

    def tearDown(self):
        self.db.close()
        self.folder.cleanup()

    def test_rule_compacts_history_and_human_conflict_deactivates_it(self):
        ingest_decision(self.db, decision(2, "sample analysis batch A"))
        ingest_decision(self.db, decision(3, "sample analysis batch B"))
        ingest_decision(self.db, decision(4, "software subscription", "computing"))
        candidate = {"foundation_id": "isf", "program": PROGRAM,
                     "condition": {"account_exact": "400 - lab services",
                                   "description_contains_all": ["sample analysis"]},
                     "status": "classified", "category_id": "services",
                     "explanation": "Sample analysis is a billed research service."}
        result = add_rule(self.db, candidate)
        self.assertEqual(result["history_deleted"], 2)
        self.assertEqual([item["row_number"] for item in history(self.db)], [4])
        self.assertEqual(rules(self.db)[0]["active"], 1)

        report = {"foundation": {"id": "isf", "program": PROGRAM},
                  "source": {"sheet": "Ledger", "display_columns": {"account": 0, "description": 1, "reference": 2}},
                  "sheets": [{"name": "Ledger", "rows": [[], [{"v": "400 - lab services"},
                      {"v": "sample analysis batch C"}, {"v": "invoice"}]]}],
                  "records": [{"row": 2, "kind": "transaction", "classification": {"status": "needs_review"}}]}
        self.assertEqual(suggest_report(self.db, report)[0]["category_id"], "services")
        ingest_decision(self.db, decision(5, "sample analysis batch D", "computing"))
        self.assertEqual(rules(self.db)[0]["active"], 0)
        self.assertEqual(suggest_report(self.db, report), [])
        self.assertEqual([item["row_number"] for item in history(self.db)], [4, 5])

    def test_retention_and_only_explicit_reviewed_json(self):
        report = {"foundation": {"id": "isf", "program": PROGRAM},
                  "source": {"sha256": "a" * 64, "sheet": "Ledger",
                             "display_columns": {"account": 0, "description": 1, "reference": 2}},
                  "sheets": [{"name": "Ledger", "rows": [[],
                      [{"v": "400 - lab services"}, {"v": "sample analysis A"}, {"v": "invoice"}],
                      [{"v": "400 - lab services"}, {"v": "sample analysis B"}, {"v": "invoice"}]]}],
                  "records": [
                      {"row": 2, "kind": "transaction", "classification": {"status": "classified",
                          "category_id": "services", "rationale": "Reviewer confirmed service", "reviewed_by_user": True}},
                      {"row": 3, "kind": "transaction", "classification": {"status": "classified",
                          "category_id": "services", "rationale": "Agent proposed service"}},
                  ]}
        self.assertEqual(ingest_reviewed_report(self.db, report), 1)
        self.assertEqual([item["row_number"] for item in history(self.db)], [2])
        self.db.execute("UPDATE decisions SET decided_at=?", ("2025-01-01T00:00:00+00:00",))
        self.db.commit()
        self.assertEqual(prune(self.db, datetime(2026, 11, 2, tzinfo=timezone.utc))["deleted"], 1)
        self.assertEqual(history(self.db), [])

    def test_rule_rejected_with_one_example_or_conflict(self):
        ingest_decision(self.db, decision(2, "sample analysis A"))
        candidate = {"foundation_id": "isf", "program": PROGRAM,
                     "condition": {"description_contains_all": ["sample analysis"]},
                     "status": "classified", "category_id": "services",
                     "explanation": "Sample analysis is a service."}
        with self.assertRaisesRegex(ValueError, "two independent"):
            add_rule(self.db, candidate)
        ingest_decision(self.db, decision(3, "sample analysis B", "computing"))
        with self.assertRaisesRegex(ValueError, "conflict"):
            add_rule(self.db, candidate)

    def test_receiver_requires_token_and_saves_locally(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(Path(self.folder.name) / "memory.sqlite3", "secret"))
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/decisions"
            body = json.dumps(decision(2, "sample analysis A")).encode()
            with self.assertRaises(HTTPError) as denied:
                urlopen(Request(url, data=body, headers={"Content-Type": "application/json"}), timeout=5)
            self.assertEqual(denied.exception.code, 401)
            with urlopen(Request(url, data=body, headers={"Content-Type": "application/json",
                                                 "Authorization": "Bearer secret"}), timeout=5) as response:
                self.assertEqual(response.status, 200)
            self.assertEqual([item["row_number"] for item in history(self.db)], [2])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
