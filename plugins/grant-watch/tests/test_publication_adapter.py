import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

import test_publication_contract as fixtures
import publication_adapter as a
import publication_contract as p
import notice_contract as n


class MockToolServer:
    """Synthetic tool boundary only; never represents an actual owner approval."""
    def __init__(self, fixture):
        self.receipts, self.methods, self.created, self.updated = [], [], 0, 0
        self.rows = fixture.native([fixture.original], fixture.controls)
        self.rows[0]["Fixture"] = "__YES__"
        self.messages = {}
        self.after_create_failure = False
        self.after_update_failure = False
        self.hide_child = False
        self.cancel_on_child_read = False
        self.clock = fixture.now

    def now(self):
        return self.clock

    def call(self, method, args):
        self.methods.append(method)
        self.receipts.append(dict(method=method, args_sha256=n.digest(args)))
        if method == "dot_read_message":
            return copy.deepcopy(self.messages[args["message_id"]])
        if method == "notion_self": return {"workspace": {"id": "fixture-workspace"}}
        if method == "notion_fetch": return {"text": "fixture-marker fixture-source"}
        if method == "notion_query_view":
            if self.cancel_on_child_read and len(self.rows) > 1:
                self.rows[0]["Publish again"] = "__NO__"
            rows = self.rows[:1] if self.hide_child else self.rows
            return dict(results=copy.deepcopy(rows), has_more=False)
        if method == "notion_create_rows":
            self.created += 1
            self.rows.append(dict(copy.deepcopy(args["pages"][0]["properties"]),
                                  url="https://notion.example/child"))
            if self.after_create_failure:
                self.after_create_failure = False
                raise a.Hold("synthetic accepted-create timeout")
            return {"pages": [{"url": self.rows[-1]["url"]}]}
        if method == "notion_update_properties":
            self.updated += 1
            self.rows[0].update(copy.deepcopy(args["properties"]))
            if self.after_update_failure:
                self.after_update_failure = False
                raise a.Hold("synthetic accepted-update timeout")
            return {"success": True}
        raise AssertionError(method)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.PublicationTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.path = Path(self.f.folder.name)
        self.transition = self.f.sync()
        self.child = self.transition["notices"][1]
        self.policy = dict(dot=dict(room_id="private-room", owner_principal="synthetic-owner",
                                   anchor_message_id="anchor"),
            notion=dict(isolation="disposable_fixture", fixture_marker="fixture-marker", link_id="fixture-link",
                        workspace_id="fixture-workspace", data_source_id="fixture-source", view_url="view://fixture"))
        self.server = MockToolServer(self.f)
        self.data = dict(request=self.child, approval_message_id="approval", sender=self.f.sender,
                         recipients=self.f.recipients, now=n.cadence.iso(self.f.now))
        manifest = a.approval_manifest(self.child, self.f.sender, self.f.recipients, "2026-12-08T06:00:00Z")
        for key, text in (("anchor", "Synthetic owner identity anchor, not approval"), ("approval", json.dumps(manifest))):
            self.server.messages[key] = dict(room_id="private-room", partial=False, message=dict(message_id=key,
                channel="chatgpt", author="user", author_id="synthetic-owner", sent_at="2026-12-01T06:00:00Z",
                deleted_at=None, content={"text": text}))

    def execute(self, operation, data=None):
        return a.execute(operation, data or self.data, self.policy, self.server, self.path)

    def persistence(self, **extra):
        return self.execute("persist_controls", dict(original_key=self.f.key, rounds=self.f.rounds,
            sender=self.f.sender, recipients=self.f.recipients, settings=self.f.settings,
            now=n.cadence.iso(self.f.now), **extra))

    def test_exact_owner_scope_and_audience_from_retrieved_message(self):
        result = self.execute("authorize")
        self.assertEqual(result["status"], "authorized")
        self.assertEqual(self.server.methods, ["dot_read_message", "dot_read_message"])
        grant = result["context"]["confirmations"][0]
        self.assertEqual(grant["scope"], p.scope(self.child))
        self.assertEqual(grant["message_ref"], "approval")
        self.assertTrue(p.Confirmations(result["context"]).match(self.child, self.f.now))

    def test_authenticated_general_feature_request_is_not_send_permission(self):
        self.server.messages["approval"]["message"]["content"]["text"] = "Build a way to request a resend."
        result = self.execute("authorize")
        self.assertEqual(result["status"], "awaiting_owner_confirmation")
        self.assertTrue(result["authenticated_owner"])
        self.assertEqual(result["confirmations"], [])

    def test_wrong_room_sender_channel_deleted_partial_and_quoted_approval_fail(self):
        original = copy.deepcopy(self.server.messages["approval"])
        for part, key, value in (("root", "room_id", "other-room"), ("root", "partial", True),
                ("message", "author_id", "editor"), ("message", "channel", "email"),
                ("message", "author", "aeon"), ("message", "deleted_at", "2026-12-01T06:00:00Z"),
                ("message", "message_id", "not-the-requested-message")):
            self.server.messages["approval"] = copy.deepcopy(original)
            target = self.server.messages["approval"] if part == "root" else self.server.messages["approval"]["message"]
            target[key] = value
            self.assertEqual(self.execute("authorize")["status"], "blocked")
        self.server.messages["approval"] = copy.deepcopy(original)
        self.server.messages["approval"]["message"]["content"]["text"] = "> " + original["message"]["content"]["text"]
        self.assertEqual(self.execute("authorize")["status"], "awaiting_owner_confirmation")

    def test_changed_scope_audience_extra_fields_and_stale_approval_fail(self):
        original = self.server.messages["approval"]["message"]["content"]["text"]
        for key, value in (("sender", "other@example.org"), ("recipients", ["extra@example.org"]),
                           ("bcc", ["hidden@example.org"]), ("action", "maybe"), ("expires_at", "2027-01-01T00:00:00Z")):
            manifest = json.loads(original)
            manifest[key] = value
            self.server.messages["approval"]["message"]["content"]["text"] = json.dumps(manifest)
            self.assertEqual(self.execute("authorize")["status"], "blocked")
        self.server.messages["approval"]["message"]["content"]["text"] = original
        self.server.messages["approval"]["message"]["sent_at"] = "2026-11-30T06:00:00Z"
        self.assertEqual(self.execute("authorize")["status"], "blocked")

    def test_actual_consumer_retrieves_authorization_again_and_rejects_revocation(self):
        controls = self.f.latch(self.transition)
        approved = self.execute("authorize")
        plan = self.f.select(self.transition["notices"], controls, approved["context"])
        config = self.f.config()
        args = dict(native_rows=self.f.native(self.transition["notices"], controls), rounds=self.f.rounds,
            plan=plan, report=dict(state="partial"), config=config, actual_workspace=config["workspace_id"],
            connected_sender=self.f.sender, report_day="2026-12-01", delivery_state="ready", now=n.cadence.iso(self.f.now))
        data = dict(authorization=self.data, arguments=args)
        prepared = self.execute("authorized_prepare", data)
        self.assertEqual(prepared["intent"]["alert_keys"], [self.child["key"]])
        self.server.messages["approval"]["message"]["deleted_at"] = "2026-12-01T06:00:00Z"
        blocked = self.execute("authorized_prepare", data)
        self.assertEqual(blocked["status"], "blocked")
        self.assertNotIn("prepared", blocked)

    def test_restart_after_child_then_parent_preserves_history_and_creates_once(self):
        before = copy.deepcopy(self.server.rows[0])
        first = self.persistence(stop_after="child_readback")
        self.assertEqual(first["status"], "interrupted_after_child_readback")
        self.assertEqual(self.server.created, 1)
        self.assertEqual(self.server.rows[0], before)
        second = self.persistence(stop_after="parent_write")
        self.assertEqual(second["status"], "interrupted_after_parent_write")
        third = self.persistence()
        self.assertEqual(third["status"], "persisted_and_read_back")
        self.assertFalse(third["parent_written"])
        self.assertEqual(self.server.created, 1)
        self.assertEqual(self.server.updated, 1)
        self.assertEqual(self.server.rows[0]["Payload"], before["Payload"])
        self.assertEqual(self.server.rows[0]["Receipt ID"], before["Receipt ID"])

    def test_accepted_create_timeout_no_visible_result_never_retries_create(self):
        self.server.after_create_failure = True
        self.assertEqual(self.persistence()["status"], "blocked")
        self.server.hide_child = True
        blocked = self.persistence()
        self.assertEqual(blocked["status"], "blocked")
        self.assertIn("unresolved", blocked["reason"])
        self.assertEqual(self.server.created, 1)
        self.server.hide_child = False
        self.assertEqual(self.persistence()["status"], "persisted_and_read_back")
        self.assertEqual(self.server.created, 1)

    def test_accepted_parent_timeout_recovers_without_rewriting_history(self):
        self.server.after_update_failure = True
        self.assertEqual(self.persistence()["status"], "blocked")
        result = self.persistence()
        self.assertEqual(result["status"], "persisted_and_read_back")
        self.assertEqual(self.server.updated, 1)

    def test_cancel_between_writes_and_live_target_are_blocked(self):
        self.server.cancel_on_child_read = True
        result = self.persistence()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(self.server.updated, 0)
        self.policy["notion"]["isolation"] = "live"
        before = len(self.server.methods)
        self.assertEqual(self.persistence()["status"], "blocked")
        self.assertEqual(len(self.server.methods), before)

    def test_cli_rpc_retrieval_not_an_ordinary_snapshot_approval(self):
        actual = datetime.now(timezone.utc)
        self.data["request"]["requested_at"] = n.cadence.iso(actual)
        self.server.messages["approval"]["message"]["sent_at"] = n.cadence.iso(actual)
        self.server.messages["approval"]["message"]["content"]["text"] = json.dumps(a.approval_manifest(
            self.data["request"], self.f.sender, self.f.recipients, n.cadence.iso(actual + timedelta(days=7))))
        src, policy, out, rpc = (self.path/x for x in ("input.json", "policy.json", "output.json", "rpc"))
        src.write_text(json.dumps(dict(operation="authorize", **self.data)), encoding="utf-8")
        policy.write_text(json.dumps(self.policy), encoding="utf-8")
        command = [sys.executable, "-X", "utf8", "-u", str(fixtures.SCRIPTS/"notice_contract.py"), str(src),
                   "--output", str(out), "--tool-policy", str(policy), "--rpc-dir", str(rpc)]
        with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, encoding="utf-8") as process:
            for line in process.stdout:
                if not line.startswith("GW_RPC "): continue
                path = Path(line[7:].strip())
                request = json.loads(path.read_text(encoding="utf-8"))
                result = self.server.call(request["method"], request["args"])
                response = path.with_name(request["id"] + ".response.json")
                response.write_text(json.dumps(dict(id=request["id"], method=request["method"],
                    args_sha256=request["args_sha256"], result=result)), encoding="utf-8")
                process.stdin.write(str(response)+"\n")
                process.stdin.flush()
            self.assertEqual(process.wait(timeout=10), 0, process.stderr.read())
        result = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "authorized")
        self.assertEqual(len(result["adapter_receipts"]), 2)
        self.assertTrue(all(r["method"] == "dot_read_message" for r in result["adapter_receipts"]))

    def test_backdating_snapshot_cannot_revive_expired_approval(self):
        self.server.clock += timedelta(days=8)
        self.data["now"] = "2026-12-01T06:00:00Z"
        self.assertEqual(self.execute("authorize")["status"], "blocked")

    def test_deadline_request_is_rejected_before_identity_lookup(self):
        self.data["request"]["stage"] = "deadline"
        self.assertEqual(self.execute("authorize")["status"], "blocked")
        self.assertEqual(self.server.methods, [])

    def test_concurrent_create_claim_is_atomic_and_does_not_call_connector(self):
        original_open = Path.open
        def race(path, mode="r", *args, **kwargs):
            if str(path).endswith(".journal.json") and mode == "x":
                with original_open(path, "w", encoding="utf-8") as other:
                    other.write('{"state":"other_worker_claim"}')
            return original_open(path, mode, *args, **kwargs)
        with patch.object(Path, "open", race):
            result = self.persistence()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(self.server.created, 0)


if __name__ == "__main__": unittest.main()
