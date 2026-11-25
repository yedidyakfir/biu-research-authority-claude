# Operational coverage: resumable coordinator and deployment gate

Full due coverage is the objective; a finite pilot slice is not completion. The
existing source and delivery safety limits still apply. This helper is opt-in and
has no network, Notion or email side effects. Adding it does not activate a new
worker, increase production budgets, or establish unattended readiness.

## Entry point and checkpoints

Use the existing `scripts/cadence_contract.py input.json --output output.json`
with `operation: coverage`. For a live `action: start`, supply the fresh private
`config`, `actual_workspace`, `settings_rows`, `run_key`, scheduled `now`, and
`unfiltered_view_verified: true` only after fetching the configured all-index
view and checking its source and absence of filters. Budgets are copied verbatim
from Configuration, including source pacing. The alternative `context` input is
for isolated fixtures and capacity planning, not an override of live limits.

For subsequent actions pass the prior output's `state`, unchanged `context_hash`,
and `event`. Persist checkpoints as bounded native pages linked to the same Run;
local JSON is private scratch only. Read back each write-ahead admission before
executing it. A saved admission replay has `execute: false`: reconcile its result,
do not execute it again. Keep the existing desktop/date lock and one writer.
Use `action: checkpoint` to emit hashed native-page parts of at most9000 characters.
Write/read back all parts, then publish the manifest last. `action: resume` takes
that `checkpoint` and expected `context_hash`; missing, duplicate or changed parts
fail closed. Never call `start` for an existing run/checkpoint to reset its budgets.
Look up the durable run and all prior intents/receipts first. Native persistence
must be verified before cutover; the CLI alone cannot prove a remote write.

1. Follow `next_index_read` using the single configured **unfiltered operational
   index view**, `archive_status: active`, and its exact continuation. Read at
   most the returned `page_size`; retain the unchanged response as a receipt.
   Feed `action: page` with `read_id`, `view_url`, input `cursor`, returned
   `next_cursor`, `has_more`, `rows`, `at` and `native_notion_rows: true`.
   Persist before requesting the next page. A terminal `has_more: false` is
   required; reaching a numeric cap never proves exhaustion. Replays use the
   same saved response and ID. Count duplicates and held rows as scanning work.
   Do not restart stage-view prefixes, query the archive, or infer a total from
   migration counts. Conflicting/malformed rows keep the due total unknown.
2. Freeze the census time/settings under the lock. Recompute stages and due work
   from stored facts, never cached Next due alone. Page traversal is not an
   atomic Notion snapshot: a concurrent edit requires reconciliation/re-enumeration
   within the same budget before claiming exactness. Settings edits after the
   freeze apply at the next run unless the current plan is explicitly superseded.
   Before each source check, read current target evidence and preserve verifier
   rules; an updated/excluded/deleted target is an explicit reconciled outcome.
3. `action: lease` with `lease_id`, `batch_size`, `at` reserves pending due keys.
   Complete the census first so boundaries beyond the first page are visible.
   It prioritizes all known boundaries then oldest debt and the existing fair
   cursor. Independent rounds stay independent. Call/source budgets are
   **cumulative across leases and restarts**, not per batch. Leased but unrecorded
   work remains in flight and cannot silently be selected again.
4. Before non-index reads (settings, calendar, notices, evidence and read-backs),
   reserve their bounded `rows` and `reads` with `action: metadata`, `read_id` and
   `at`. Conservative reservations are not refunded after failures. Before each
   source request, redirect and follow-up use `action: request`, `request_id`,
   target `key`, exact `url`, logical retry `group`, and `at`. Persist admission
   first; apply its timeout in the existing requester. Use the same group only
   for retries of that URL; a redirect is a new counted group. One request per
   host, configured spacing, at most two retries, cumulative request/runtime
   limits and unresolved-request blocking survive restart. Record every result
   with `action: finish_request`, `request_id`, `at`, `status` and optional
   `access_challenge`. HTTP 403/429 or a challenge stops that host for this run;
   never rotate access methods to evade it. This helper does not perform or
   independently verify HTTP enforcement.
5. Reuse an exact fetched response where applicable; do not collapse distinct
   rounds or equate URL reuse/HTTP200 with verified facts. Use the unchanged
   `complete_attempt` verifier/outcome path; persist observations and native audit,
   then read back. Only then use `action: complete` with `key`, same `run_key`,
   `result` (`checked`, `failed`, or `identity_only`), `audit_url`, `read_back: true`.
   Identity-only review is visible and does not assert a completed source check.
   Advance fairness only after actual outcome read-back; keep the legacy cursor.
6. Save `coverage` in the **Notion-only** audit: stage due/checked/failed/identity/
   in-flight/deferred, unknown unscanned, and malformed/conflicting counts.
   `total_due: null` means unknown. `all_due_attempted` can be true with failures;
   `verified_coverage_complete` cannot. Do not describe planning as actual checks.

## Notices and one digest

Continue `notice_contract.py` independently of source selection, including its
pending/unresolved traversal, exact keys, intent, sender/recipient verification,
Sent reconciliation and receipt-bound delivery. Reserve its metadata before
spending the remainder on source work. Coverage checkpoints contain no delivery
receipt and confer no sending permission. An uncertain send stays blocked across
all coverage resumes. A receipt never proves source coverage; completed source
coverage never proves delivery. Keep the existing one authorized digest, renderer,
Sun–Thu08:00Israel wake and private audit. No standalone or test emails.

## Safe production cutover

1. Read the current all-index census and four settings, forecast the next wake,
   and compare full demand with **every** configured budget (including metadata
   and notice reserve). Retain missing-URL/inactive rows separately. A successful
   fixture proves logic only. Do not activate if full due work cannot fit.
2. Before any capacity increase, validate a bounded representative requester /
   verifier / Notion persistence path with no email and measured host latency,
   redirects, retries and connector costs. Explicitly reconcile increased global
   budgets with unchanged per-host backpressure. Do not multiply old per-run
   limits by the number of batches. Reserve enough time/reads for notices, audit,
   rendering, one send and receipt verification. A wake at08:00 starts work; an
   exact08:00 completed digest cannot include subsequent checks without earlier
   collection, which is a separate schedule decision.
3. Under an idle verified desktop lock, save current ref, linked-skill target,
   Configuration and scheduler prompt. Apply the reviewed commit to the existing
   pilot feature branch without merge, publish that branch only if authorized,
   and verify remote/local ref plus skill junction. Parent owns the minimal prompt
   replacement: full unfiltered traversal and resumable same-run checkpoints
   replace pilot stage-slice selection; all source, notice, recipient, schedule
   and receipt guards remain. Change only approved capacity fields in Notion and
   read them back; do not rewrite the whole config or earlier run observations.
4. Exercise the actual no-send CLI on a fresh native snapshot using **those exact
   production settings**. Verify census exhaustion, conservation of counts,
   interruption/resume, one manifest, and uncertain-send blocking. Only then
   activate the linked route and report what still requires a live scheduled run.
   Roll back the focused commit/config/prompt from their before-images; retain
   additive checkpoint/audit history and never roll back confirmed receipts.

Until these steps are substantiated, report the capacity or integration blocker
instead of claiming production readiness or leaving an emergency live change.
