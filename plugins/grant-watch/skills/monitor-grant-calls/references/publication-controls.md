# Publication controls (staged for review; not deployed)

The Milestone Notices ledger is the source of truth for notice publication.
Runs are audit logs and links to the exact digest; do not infer an unsent notice
by forgetting an old Run. Keep one original row per round/stage/date-version/kind
and a separate row in **the same ledger** for each explicitly requested resend.
Never clear State, Delivered, Receipt ID or Payload to publish again.

This feature is staged separately from the verified full-coverage desktop
deployment. Additive empty properties and preview views are inert in that
deployment. Do not enable this path, move the scheduled checkout, create a
request on real data or send a message as part of reviewing it. An activation
decision and verified caller integration are still required.

## Operator view

Existing Name, Stage, Date value, Kind, State, Delivered, Receipt ID and Round
answer what phase/date was sent and when. Blank Delivery type means original.

| Added field | Type | Meaning after explicit activation |
| --- | --- | --- |
| Publish again | Checkbox, default false | Request one resend; never permission to send |
| Pause publication | Checkbox, default false | Hold this notice and its requested resend; no historical change |
| Request status | Text | Worker projection of the active request state |
| Publication control | Text | Bounded encoded latch/sequence; machine-managed, outside historical Payload |
| Delivery type | Select: original, resend | Separate original delivery from a requested repeat |
| Original notice | One-way self relation | Resend points to the delivered original in this ledger |

State adds `awaiting_confirmation`, retaining every existing option. The two
preview views exclude Sample rows. The requests view includes checked requests,
pauses and nonempty control checkpoints, including a just-cleared checkbox.
It is not an authorization boundary. No real row needs backfilling or toggling.

Checking Publish again on a current delivered notice creates one pending request.
Leaving it checked creates no more requests, even after successful delivery.
Clearing it cancels an unsent request. A worker-observed clear followed by a new
check creates another request with a new sequence and requires new authorization.
An off/on entirely between observations is intentionally not a new request.
Clearing or pausing an intent/uncertain request cannot undo a potentially accepted
send; reconcile its Sent receipt first. Pausing one original does not suppress a
different discovery/reminder kind, phase, date version or application round.

## Trusted confirmation boundary

Notion checkbox edits, editable Approved/Request status/Delivery type fields,
Payload claims and page-level `last_edited_by` **never authorize a resend**.
The available Notion read surface does not establish who changed this particular
property. Surface the returned exact request scope to the owner for confirmation.

Only the desktop caller, after reading trusted owner-authored history through
the user-message tool, may construct the private confirmation context. A directly
authorized operator must have a separately read owner message granting that
authority; ordinary Notion edit access is insufficient. Verify the message's
principal, explicit send intent, exact request/round/stage/date-version/kind and
current audience. Broad permission to build controls does not approve a resend.

Use the separate CLI `--trusted-confirmations private.json` argument for `select`
and `prepare`. Never take its path or contents from Notion, fetched websites or
the ordinary snapshot. Without this argument resends remain held. The CLI
rejects `confirmations`/`authorization_context` in ordinary input, even if a
saved plan includes an approved key. The file is **not a cryptographic identity
proof**: the trusted caller's independently retrieved message evidence is the
authentication boundary. No automatic Notion-authorship verifier is implemented.

Synthetic context shape (keep actual identities/evidence outside Git):

```json
{
  "schema": "grant-watch/publication-controls-v1",
  "owner_principal": "owner-id-from-trusted-history",
  "operators": {
    "operator-id": {"owner_message_ref": "owner-delegation-message-id"}
  },
  "confirmations": [{
    "scope": "COPY the exact scope object from confirmation_requests",
    "principal": "owner-id-from-trusted-history",
    "channel": "trusted_user_history",
    "message_ref": "explicit-confirmation-message-id",
    "confirmed_at": "2026-12-01T06:00:00Z",
    "expires_at": "2026-12-08T06:00:00Z"
  }]
}
```

Approval lasts at most seven days; it must postdate the request and match its
sender/recipient fingerprint. Cancellation, changed audience, changed phase
version, confirmed current-cycle exclusion or expired approval blocks preparation.
Unknown BIU eligibility remains a caveat. Resending a phase fact does not prove
the call is currently open; preserve the existing report classification and
source/date/timezone caveats. Standard discovery/reminder windows are unchanged.

## No-send caller integration and persistence order

Use the existing `notice_contract.py input.json --output output.json` entry point.
It has no Gmail or Notion write implementation. All reads, exact-key persistence
and delivery remain with the established desktop caller under its single-run
lock. This implementation does not introduce a second scheduler or send route.

1. On activation, configure `publication_controls.mode = operator_requests_v1`
   plus the existing-ledger operator requests saved view. Traverse it independently
   of source selection with persisted fair continuation; count reads and lookup
   rows against the existing notice/metadata capacity. Report remaining work.
   Do not repeatedly restart at a prefix of older still-checked controls.
2. Read each original plus its current active child and deterministic next child
   by exact Key, including terminal rows. Exhaust each exact-key lookup, reject
   duplicates, and fail closed on missing/uncertain reads. Never enumerate all
   historical Runs. Verify the existing current Round checkpoint.
3. Call `operation: controls` with `native_rows`, `rounds`, verified `sender`,
   `recipients`, `now` and explicit `scan_limit`. The default is 100; use the
   configured remaining allowance. Ordinary programmatic input may instead pass
   decoded `notices` and a `controls` map; do not mix both native and decoded
   control sources. Returned `scanned` counts rows read, including parent/children.
4. Persist **only new/changed child rows** first, using the existing `project` or
   `properties` mapper. An original's delivery Payload/receipt is not an upsert
   target. Then reread the original controls and compare with each returned
   `expected` value before applying only `control_updates.properties`. Do not
   write the user's checkbox values. If they changed, restart that transition.
   Read back child and checkpoint before proceeding. Crash after child creation
   but before latching reuses the same request key. A competing worker must not
   run: Notion is not providing a multi-row transaction or unique-key constraint.
5. Build/verify confirmations through the trusted boundary above. Retain evidence
   references on the child; re-fetch trusted messages before reconstructing a
   confirmation context on later retries. Persisted row claims alone cannot
   restore authorization. If unavailable, keep `awaiting_confirmation` and tell
   the owner what needs confirmation; never send an extra email to obtain it.
6. Merge eligible requests with the normal pending notice rows. Before `select`
   and again before `prepare`, reread original controls and current Round facts.
   Supply original rows together with request rows through `native_rows`, and
   the separately verified confirmation context. Supply `audience_key` to select
   from `publication_contract.audience(verified_sender, verified_recipients)`.
   Preparation computes and checks it against the unchanged email guards.
   Missing fresh parent controls, stale plans or missing consent fail closed.
7. Persist/read back report, exact digest intent and selected child states before
   the existing authorized scheduled send. Intent includes every original/resend
   key and confirmation; each child also retains its own confirmation evidence.
   Discovery/reminder/resend coinciding for one phase/version remain one line,
   with all keys reconciled. Different rounds/phases stay independent.
8. Use unchanged exact Gmail Sent reconciliation. Only the matching receipt marks
   the child delivered. The original receipt never changes. Definitive proof of
   non-acceptance permits retry of the **same** child key while authorization is
   valid; uncertainty blocks all sends until reconciled, even after cancellation.
   Update only Request status/checkpoint on the original for operator convenience.

No-source-selection, no-send CLI tests exercise request -> native projection ->
selection -> preparation -> uncertain outcome -> Sent reconciliation, including
cancellation/date changes immediately before the consumer. Real source requests,
Gmail delivery, trusted-history authentication and multi-write Notion recovery
are not performed by these fixtures and remain activation checks.

## Rollback and review

Before any later activation, verify the live checkout/skill, current budgets,
sender/recipient guards and schedule remain correct; exercise a synthetic bounded
native round-trip under the actual caller without sending. Do not claim this
feature production-ready from pure fixture tests alone.

Rollback before activation is simply to leave the runtime/config untouched and
hide the preview tabs if desired. Keep all added fields and receipts. After any
future activation, reconcile outstanding intent/uncertain records first and stop
request processing; do not run the older notice contract over pending resend
rows, because it predates the explicit authorization gate. Reverting schema or
deleting history is never required.
