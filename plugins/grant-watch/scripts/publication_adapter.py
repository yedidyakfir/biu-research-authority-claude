"""Hosted-tool adapter for the staged publication path. Never sends or drafts.

The agent host services allowlisted RPC requests through its authenticated tools.
No credentials, direct HTTP workaround, or approval from a Notion field/file.
"""
import copy
import json
from pathlib import Path
import secrets
from datetime import datetime, timedelta, timezone

import cadence_contract as cadence
import notice_contract as n
import publication_contract as p

PROTOCOL = "grant-watch/publication-rpc-v1"
METHODS = {"dot_read_message", "notion_self", "notion_fetch", "notion_query_view",
           "notion_create_rows", "notion_update_properties"}


class Hold(ValueError):
    pass


def unpack(result):
    if result.get("isError"):
        raise Hold("Connector error; reconcile outcome before retry")
    if result.get("structuredContent") is not None:
        return result["structuredContent"]
    if "content" in result:
        return json.loads(next(x["text"] for x in result["content"] if x.get("type") == "text"))
    return result


def write_json(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=True, indent=2), encoding="utf-8")
    temp.replace(path)


class ToolBridge:
    """RPC transport only. The hosting agent must call the exact named tool.

    Responses are not accepted from the ordinary snapshot. A nonce binds each
    direct authenticated tool result to its requested operation. This is not a
    cryptographic attestation against a compromised host or writable policy.
    """
    def __init__(self, directory, max_calls=24):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.max_calls, self.calls, self.receipts = max_calls, 0, []

    def call(self, method, args):
        if method not in METHODS or self.calls >= self.max_calls:
            raise Hold("Unsupported tool or adapter connector budget exhausted")
        self.calls += 1
        request_id = secrets.token_hex(16)
        request = dict(protocol=PROTOCOL, id=request_id, method=method, args=args, args_sha256=n.digest(args))
        path = self.directory / (request_id + ".request.json")
        write_json(path, request)
        print("GW_RPC " + str(path), flush=True)
        response_path = Path(input()).resolve()
        if response_path.parent != self.directory or response_path.suffix != ".json":
            raise Hold("RPC response must stay in the private rendezvous directory")
        reply = json.loads(response_path.read_text(encoding="utf-8-sig"))
        if (reply.get("id") != request_id or reply.get("method") != method or
                reply.get("args_sha256") != n.digest(args)):
            raise Hold("RPC response does not match exact request")
        self.receipts.append(dict(method=method, args_sha256=n.digest(args),
                                  result_sha256=n.digest(reply.get("result"))))
        return unpack(reply["result"])

    def now(self):
        return datetime.now(timezone.utc)


def exact_message(bridge, message_id, policy):
    response = bridge.call("dot_read_message", {"message_id": message_id, "before": 0, "after": 0})
    message = response.get("message") or {}
    if (response.get("partial") is not False or response.get("room_id") != policy["room_id"] or
            message.get("message_id") != message_id or message.get("channel") != "chatgpt" or
            message.get("author") != "user" or message.get("author_id") != policy["owner_principal"] or
            message.get("deleted_at") is not None):
        raise Hold("Exact private Dot owner message unavailable or identity mismatch")
    return message


def approval_manifest(request, sender, recipients, expires_at):
    if request["audience"] != p.audience(sender, recipients):
        raise Hold("Request audience differs from verified sender/recipient policy")
    return dict(action="approve_grant_watch_resend_once", scope=p.scope(request),
                sender=sender, recipients=sorted(recipients), cc=[], bcc=[], expires_at=expires_at)


def authorize(data, policy, bridge):
    """Strict explicit owner message; no NLP inference or synthetic real approval."""
    request, now = data["request"], bridge.now()
    if request.get("sample") or not p.is_resend(request):
        raise Hold("A current exact resend request is required")
    identity = policy["dot"]
    # Anchor is pinned by the trusted desktop caller, not a Notion editable field.
    exact_message(bridge, identity["anchor_message_id"], identity)
    message = exact_message(bridge, data["approval_message_id"], identity)
    issued = cadence.stamp(message["sent_at"])
    if not cadence.stamp(request["requested_at"]) <= issued <= now:
        raise Hold("Approval must follow the request and not be future-dated")
    try:
        manifest = json.loads(message["content"]["text"])
    except (KeyError, TypeError, json.JSONDecodeError):
        return dict(no_send=True, status="awaiting_owner_confirmation", reason="message_is_not_exact_resend_approval",
                    message_id=message["message_id"], authenticated_owner=True, confirmations=[])
    if not isinstance(manifest, dict):
        raise Hold("Approval must be an explicit structured object")
    expiry = cadence.stamp(manifest.get("expires_at", ""))
    expected = approval_manifest(request, data["sender"], data["recipients"], manifest["expires_at"])
    if manifest != expected or not now < expiry <= issued + timedelta(days=p.MAX_APPROVAL_DAYS):
        raise Hold("Approval scope, exact audience or bounded expiry mismatch")
    grant = dict(scope=p.scope(request), principal=identity["owner_principal"], channel="trusted_user_history",
                 message_ref=message["message_id"], confirmed_at=cadence.iso(issued),
                 expires_at=cadence.iso(expiry), room_id=identity["room_id"],
                 message_sha256=n.digest(message))
    context = dict(schema=p.SCHEMA, owner_principal=identity["owner_principal"], confirmations=[grant])
    if not p.Confirmations(context).match(request, now):
        raise Hold("Retrieved authorization did not bind the current request")
    return dict(no_send=True, status="authorized", context=context, authenticated_owner=True)


def verify_fixture_target(policy, bridge):
    target = policy["notion"]
    if target.get("isolation") != "disposable_fixture" or not target.get("fixture_marker"):
        raise Hold("This staged adapter permits disposable fixture writes only")
    actual = bridge.call("notion_self", {"link_id": target["link_id"]})
    if actual.get("workspace", {}).get("id") != target["workspace_id"]:
        raise Hold("Notion workspace mismatch")
    source = bridge.call("notion_fetch", {"id": "collection://" + target["data_source_id"], "link_id": target["link_id"]})
    if target["fixture_marker"] not in source.get("text", ""):
        raise Hold("Disposable target marker missing from fetched schema")
    view = bridge.call("notion_fetch", {"id": target["view_url"], "link_id": target["link_id"]})
    if target["data_source_id"] not in view.get("text", ""):
        raise Hold("Fixture view points to a different data source")
    return target


def read_fixture(target, bridge):
    response = bridge.call("notion_query_view", {"link_id": target["link_id"],
        "data": {"mode": "view", "view_url": target["view_url"], "page_size": 100}})
    rows = response.get("results", [])
    if response.get("has_more") is not False or any(not p.checkbox(r.get("Fixture")) for r in rows):
        raise Hold("Fixture traversal incomplete or non-fixture row present")
    keys = [cadence.notion_text(r["Key"]) for r in rows]
    if len(keys) != len(set(keys)):
        raise Hold("Duplicate exact notice keys require reconciliation")
    return {k: r for k, r in zip(keys, rows)}


def fixture_properties(row, settings):
    # These are the production native fields involved in this transaction. The
    # fixture has no Round/Settings/Run relations to production data.
    projected = n.properties(row, {s: "https://fixture.example/settings" for s in n.STAGES}, settings)
    keep = {"Name", "Key", "State", "Payload", "Receipt ID", "Delivery type", "Original notice"}
    return dict({k: v for k, v in projected.items() if k in keep}, Fixture="__YES__")


def persist_controls(data, policy, bridge, journal_dir):
    target = verify_fixture_target(policy, bridge)
    raw = read_fixture(target, bridge)
    original_key = data["original_key"]
    if original_key not in raw:
        raise Hold("Fixture original missing")
    original_native = raw[original_key]
    historical_payload = original_native["Payload"]
    notices = n.notices_from_notion(list(raw.values()))
    control = p.controls_from_notion(original_native)
    transition = p.sync(notices, data["rounds"], {original_key: control}, data["sender"], data["recipients"],
                        cadence.stamp(data["now"]), scan_limit=100)
    update = transition["control_updates"][0]
    checkpoint = n.decode_record(update["properties"]["Publication control"])
    child_key = checkpoint.get("active_key")
    if not child_key:
        raise Hold("Fixture has no valid pending control transition")
    child = next(r for r in transition["notices"] if r["key"] == child_key)
    journal_path = Path(journal_dir) / (n.digest([target["data_source_id"], child_key]) + ".journal.json")
    if child_key not in raw:
        if journal_path.exists():
            raise Hold("Prior create outcome unresolved; absent readback does not authorize another create")
        try:
            with journal_path.open("x", encoding="utf-8") as journal:
                json.dump(dict(key=child_key, state="create_started", target=target["data_source_id"]), journal)
        except FileExistsError as exc:
            raise Hold("Another create claim exists; reconcile before continuing") from exc
        bridge.call("notion_create_rows", dict(link_id=target["link_id"],
            parent={"data_source_id": target["data_source_id"]},
            pages=[{"properties": fixture_properties(child, data["settings"])}]))
    after_child = read_fixture(target, bridge)
    if child_key not in after_child or n.notices_from_notion([after_child[child_key]])[0] != child:
        raise Hold("Child write not exactly read back; preserve intent and reconcile")
    relation = after_child[child_key].get("Original notice", [])
    if isinstance(relation, str):
        relation = json.loads(cadence.notion_text(relation))
    if relation != [original_native["url"]]:
        raise Hold("Native original-notice self relation did not read back exactly")
    if after_child[original_key]["Payload"] != historical_payload:
        raise Hold("Historical original changed during transaction")
    write_json(journal_path, dict(key=child_key, state="child_verified", target=target["data_source_id"],
                                 child_url=after_child[child_key]["url"]))
    if data.get("stop_after") == "child_readback":
        return dict(no_send=True, status="interrupted_after_child_readback", child_key=child_key,
                    child_url=after_child[child_key]["url"], historical_payload_sha256=n.digest(historical_payload))
    fresh = after_child[original_key]
    if p.controls_from_notion(fresh) != control:
        raise Hold("Operator control changed; recompute before parent checkpoint write")
    needs_write = any(cadence.notion_text(fresh.get(k, "")) != v for k, v in update["properties"].items())
    if needs_write:
        bridge.call("notion_update_properties", dict(link_id=target["link_id"], page_id=fresh["url"],
            command="update_properties", properties=update["properties"]))
    if data.get("stop_after") == "parent_write":
        return dict(no_send=True, status="interrupted_after_parent_write", child_key=child_key)
    final = read_fixture(target, bridge)
    if (final[original_key]["Payload"] != historical_payload or
            any(cadence.notion_text(final[original_key].get(k, "")) != v for k, v in update["properties"].items()) or
            n.notices_from_notion([final[child_key]])[0] != child or
            p.checkbox(final[original_key]["Publish again"]) != control["requested"]):
        raise Hold("Multi-write transaction failed exact readback")
    write_json(journal_path, dict(key=child_key, state="complete", target=target["data_source_id"],
                                 child_url=final[child_key]["url"]))
    return dict(no_send=True, status="persisted_and_read_back", child_key=child_key,
                child_url=final[child_key]["url"], original_url=final[original_key]["url"],
                row_count=len(final), parent_written=needs_write, historical_payload_sha256=n.digest(historical_payload),
                child_state=child["state"])


def execute(operation, data, policy, bridge, journal_dir):
    try:
        if operation == "persist_controls":
            result = persist_controls(data, policy, bridge, journal_dir)
        else:
            authorization = authorize(data["authorization"] if operation != "authorize" else data, policy, bridge)
            if operation == "authorize" or authorization["status"] != "authorized":
                result = authorization
            else:
                arguments = copy.deepcopy(data["arguments"])
                if "confirmations" in arguments or "native_rows" not in arguments:
                    raise Hold("Use fresh native records; ordinary input cannot provide confirmations")
                native = arguments.pop("native_rows")
                if len(native) > arguments.get("scan_limit", 100):
                    raise Hold("Native adapter read bound exceeded")
                arguments["notices"] = n.notices_from_notion(native)
                arguments["controls"] = {r["key"]: p.controls_from_notion(raw)
                    for raw, r in zip(native, arguments["notices"]) if not p.is_resend(r)}
                arguments["confirmations"] = p.Confirmations(authorization["context"])
                # Ordinary snapshots cannot backdate an expired authorization.
                arguments["now"] = bridge.now()
                if operation == "authorized_prepare":
                    arguments.pop("scan_limit", None)
                result = {"authorized_select": n.select, "authorized_prepare": n.prepare}[operation](**arguments)
        return dict(result, adapter_receipts=bridge.receipts, no_send=True)
    except (Hold, ValueError, KeyError) as exc:
        return dict(no_send=True, status="blocked", reason=str(exc), adapter_receipts=bridge.receipts)
