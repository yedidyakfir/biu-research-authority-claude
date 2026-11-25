"""Operator requests on the existing notice ledger; no network and no sending.

Editable controls request work, never authorize it. Trusted confirmations enter
only through the caller's separate authorization boundary, never native Payload.
"""
import copy
from datetime import timedelta

import cadence_contract as cadence

SCHEMA = "grant-watch/publication-controls-v1"
MAX_APPROVAL_DAYS = 7


def checkbox(value):
    if value is True or value == "__YES__":
        return True
    if value is None or value is False or value in ("__NO__", ""):
        return False
    raise ValueError("Invalid operator checkbox")


def controls_from_notion(native):
    import notice_contract as n
    raw = cadence.notion_text(native.get("Publication control", ""))
    checkpoint = n.decode_record(raw) if raw else {"sequence": 0, "latched": False}
    if (type(checkpoint.get("sequence")) is not int or checkpoint["sequence"] < 0 or
            type(checkpoint.get("latched")) is not bool):
        raise ValueError("Invalid publication control checkpoint")
    return dict(paused=checkbox(native.get("Pause publication")),
                requested=checkbox(native.get("Publish again")), checkpoint=checkpoint,
                url=native.get("url"))


def audience(sender, recipients):
    import notice_contract as n
    if not sender or not recipients or len(set(recipients)) != len(recipients):
        raise ValueError("Exact verified sender and distinct recipients required")
    return n.digest({"sender": sender, "recipients": sorted(recipients), "cc": [], "bcc": []})


def scope(row):
    return {key: row[key] for key in ("key", "original_key", "request_key", "round_key",
            "stage", "date_version", "kind", "audience")}


def is_resend(row):
    import notice_contract as n
    if row.get("sample"):
        return row.get("delivery_type") == "resend"
    canonical = n.digest([row["round_key"], row["stage"], row["date_version"], row["kind"]])
    if row.get("delivery_type", "original") == "original":
        if row["key"] != canonical:
            raise ValueError("Noncanonical original notice key; reconcile before delivery")
        return False
    if row.get("delivery_type") != "resend" or row.get("original_key") != canonical:
        raise ValueError("Invalid resend origin")
    if (type(row.get("sequence")) is not int or row["sequence"] < 1 or
            row.get("request_key") != n.digest(["publication-request-v1", canonical, row["sequence"]])):
        raise ValueError("Invalid resend request sequence")
    if row["key"] != n.digest(["resend-v1", row["original_key"], row["request_key"]]):
        raise ValueError("Invalid resend idempotency key")
    return True


class Confirmations:
    """Trusted caller input, NOT an identity verification service.

    The desktop agent must read the cited owner/operator messages with the trusted
    history tool and verify authority before constructing this context. A Notion
    approval column, last_edited_by, or file merely claiming verification is not
    proof. The CLI never loads this object from its ordinary/Notion snapshot.
    """
    def __init__(self, context):
        if context.get("schema") != SCHEMA or not context.get("owner_principal"):
            raise ValueError("Trusted publication authority context required")
        self.owner = context["owner_principal"]
        self.operators = context.get("operators", {})
        self.grants = context.get("confirmations", [])
        if len({g["scope"]["key"] for g in self.grants}) != len(self.grants):
            raise ValueError("Duplicate authorization requires reconciliation")

    def match(self, row, now):
        for grant in self.grants:
            if grant.get("scope") != scope(row):
                continue
            principal = grant.get("principal")
            if principal != self.owner and not self.operators.get(principal, {}).get("owner_message_ref"):
                return None
            if grant.get("channel") != "trusted_user_history" or not grant.get("message_ref"):
                return None
            issued, expires = cadence.stamp(grant["confirmed_at"]), cadence.stamp(grant["expires_at"])
            if not cadence.stamp(row["requested_at"]) <= issued <= now < expires <= issued + timedelta(days=MAX_APPROVAL_DAYS):
                return None
            return copy.deepcopy(grant)
        return None


def guard(row, controls, confirmations, audience_key, now):
    if row.get("receipt") and row["state"] in {"pending", "awaiting_confirmation"}:
        raise ValueError("A confirmed receipt cannot be reset to publish again")
    resend = is_resend(row)
    original = row["original_key"] if resend else row["key"]
    control = (controls or {}).get(original)
    if control and control.get("paused"):
        return "publication_paused"
    if not resend:
        return None
    if not control:
        return "fresh_original_control_required"
    checkpoint = control["checkpoint"]
    if not control.get("requested") or not checkpoint.get("latched") or checkpoint.get("active_key") != row["key"]:
        return "resend_request_inactive"
    if row["audience"] != audience_key:
        return "resend_audience_changed"
    if not isinstance(confirmations, Confirmations) or not confirmations.match(row, now):
        return "awaiting_owner_confirmation"
    return None


def timing(row, confirmations, now):
    grant = confirmations.match(row, now)
    if not grant:
        raise ValueError("Current trusted confirmation required")
    return cadence.stamp(grant["confirmed_at"]), cadence.stamp(grant["expires_at"])


def sync(notices, rounds, controls, sender, recipients, now, *, scan_limit=100):
    """Bounded control pass. Persist new child first, then control checkpoint.

    Caller supplies exact-key siblings (including terminal children) for each
    original it reads. A deterministic next sequence reuses a child after a crash
    before checkpoint persistence. No delivered Payload or receipt is rewritten.
    """
    import notice_contract as n
    if type(scan_limit) is not int or scan_limit < 1 or len(notices) > scan_limit or len(controls) > scan_limit:
        raise ValueError("Publication read bound exceeded")
    rows = copy.deepcopy(notices)
    by_key = {r["key"]: r for r in rows}
    if len(by_key) != len(rows) or set(controls) - set(by_key):
        raise ValueError("Missing or duplicate original notice")
    updates, requests = [], []
    audience_key = audience(sender, recipients)
    for key, control in controls.items():
        original = by_key[key]
        if original.get("sample") or is_resend(original):
            continue
        cp = copy.deepcopy(control["checkpoint"])
        if cp.get("active_key"):
            active = by_key.get(cp["active_key"])
            if (not active or not is_resend(active) or active["original_key"] != key or
                    active["sequence"] != cp["sequence"]):
                raise ValueError("Read and reconcile the exact original's active request")
        status = "paused" if control.get("paused") else original["state"]
        if control.get("requested") and not cp["latched"]:
            current, _, valid = n.current_notice(original, rounds)
            if (original["state"] != "delivered" or not original.get("receipt", {}).get("message_id") or
                    not valid or n.excluded(current)):
                status = "request_blocked_not_current_delivered_notice"
            else:
                cadence.safe_url(control["url"])
                sequence = cp["sequence"] + 1
                request_key = n.digest(["publication-request-v1", key, sequence])
                child_key = n.digest(["resend-v1", key, request_key])
                if child_key not in by_key:
                    child = {k: copy.deepcopy(original[k]) for k in
                             ("round_key", "stage", "date_version", "kind", "fact", "name", "first_seen")}
                    child.update(key=child_key, delivery_type="resend", original_key=key,
                        original_url=control.get("url"), round_url=original.get("round_url"),
                        request_key=request_key, requested_at=cadence.iso(now), sequence=sequence,
                        audience=audience_key, state="awaiting_confirmation", sample=False)
                    rows.append(child)
                    by_key[child_key] = child
                elif not is_resend(by_key[child_key]):
                    raise ValueError("Conflicting request key requires reconciliation")
                cp.update(sequence=sequence, latched=True, active_key=child_key)
        elif not control.get("requested") and cp["latched"]:
            cp["latched"] = False
            child = by_key.get(cp.get("active_key"))
            if child is None:
                raise ValueError("Read active request by exact key before clearing its control")
            if child["state"] in {"pending", "awaiting_confirmation"} and not child.get("receipt"):
                child.update(state="suppressed", reason="operator_cancelled_request")
        if cp.get("active_key"):
            child = by_key.get(cp["active_key"])
            if child is None:
                raise ValueError("Read active request by exact key, including terminal history")
            status = child["state"] if not control.get("paused") else "paused"
            if child["state"] in {"pending", "awaiting_confirmation"}:
                requests.append(dict(scope=scope(child), requested_at=child["requested_at"],
                                     status="awaiting_owner_confirmation"))
        updates.append(dict(key=key, url=control.get("url"), expected=copy.deepcopy(control),
            properties={"Publication control": n.encode_record(cp), "Request status": status}))
    return dict(schema=SCHEMA, no_send=True, notices=rows, control_updates=updates,
                confirmation_requests=requests, scanned=len(notices))
