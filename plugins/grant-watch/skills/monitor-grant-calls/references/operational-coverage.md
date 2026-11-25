# Operational coverage: resumable coordinator and deployment gate

Full due coverage is the objective; a finite pilot slice is not completion. The
existing source and delivery safety limits still apply. This helper is opt-in and
has no network, Notion or email side effects. Adding it does not activate a new
worker, increase production budgets, or establish unattended readiness. When
Configuration `monitoring_cadence.coverage_mode = full_due_v1`, this is the active
scheduled route; its fresh configured capacity replaces legacy pilot numeric
caps and stage-prefix selection. Do not alter the08:00Sun–Thu wake.

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

## Desktop batch execution

The desktop agent is the orchestrator; the executable source transport is
`scripts/source_batch.py snapshot.json --output-dir PRIVATE_BATCH_DIRECTORY --live`.
It has no email or Notion APIs and does not verify calls from HTTP status alone.
Never use `--live` during validation or outside the actual scheduled date-key run.
The same entry point with `mode: fixture` exercises the complete scheduler without
network; fixture outputs and example receipts must never enter the live ledger.

1. Lease up to20 due keys, including boundaries first. Resolve their exact current
   source URLs through the existing verifier; fetch target evidence only for those
   keys. Missing URL/identity is an explicit unattempted outcome, not a success.
2. Use coverage `reserve_batch` with `batch_id`, `keys`, `request_allowance`, `at`.
   For a20-key batch reserve up to120 attempts from the remaining global budget,
   not a new per-batch budget. Write/read back the resulting checkpoint in native
   parts (create/read in batches of at most8), publishing its manifest last.
   Reuse two named checkpoint slots within the same Run: write/read back the
   inactive slot, then atomically point the manifest to it. Keep the previous
   committed slot until the new manifest reads back exactly. A partial write
   cannot replace the active checkpoint; do not create a full new family of
   checkpoint pages for every request or overwrite the only recoverable copy.
   The allowance covers redirects/retries/follow-ups. Never replay an already
   reserved batch; reconcile its local journal and durable checkpoint first.
3. Transport input includes `mode: live`, same `run_key`, `run_started_at`, current
   `started_at`, `run_used_requests` **before this reservation**, `limits` copied
   from the coverage context plus `max_parallel:4`, `targets:[{key,url}]`, and
   `reservation` containing batch ID, exact keys, allowance, run key, native
   `notion_url` and `read_back:true`. Carry forward `blocked_hosts` and same-run
   `cache` metadata from prior batches. The existing network approval/access rules
   still apply; a socket denial is an environment failure, not a reason to rotate
   access methods or hammer every remaining source.
4. The transport groups identical URLs while retaining all round keys. It enforces
   at most4 independent hosts concurrently, exactly1 in flight per host, at least
   the configured2 seconds between starts, a child-process hard timeout at30
   seconds, counted explicit redirects and at most2 retries. A403/429 blocks
   further requests to that host for the run. Reuse a200 response only from this
   run with its local body and hash intact; that is evidence reuse, not proof of
   eligibility/openness. Preserve its original `fetched_at` in source provenance;
   reuse is not a new HTTP observation. Cache loss requires counted retrieval or explicit failure.
5. Persist actual request logs and outcomes in bounded native audit pages. After
   read-back, `finish_batch` takes batch ID, `actual_requests`, request-log SHA256,
   audit URL and `read_back:true`. Only a confirmed finished batch releases unused
   reserved capacity, once. Then use the unchanged verifier/`complete_attempt`
   path for each distinct round. Apply up to50 read-back outcomes with coverage
   `outcomes:{outcomes:[...]}`; each item uses the existing `complete` shape.
   A host/rate/runtime refusal with no actual request is `unattempted` plus reason,
   not a failed attempted check. An HTTP failure is `failed`; a200 alone is neither.
6. Continue the same date-key run until every due key has an observed outcome or
   an explicit failure/unattempted reason. Persist progress and renew the existing
   run heartbeat between batches. The maximum processing envelope is12 hours
   from the08:00cutoff, ending by20:00and leaving12 hours before the next weekday
   wake. The wake schedule is unchanged; do not start a second run to reset budgets.
   Surface overload promptly. Before the single authorized digest, reevaluate
   stored calendar facts at current time so a deadline crossed during processing
   is not described as still open. Never regenerate the renderer or email audit rows.

The12-hour envelope is a capacity ceiling, not a promised finish. Measure actual
source/verifier/persistence throughput per batch. The observed30-target run took
about34 minutes; scaling that pace is roughly9 hours for459 targets. A mock
workload can prove guards, traversal and restart behavior but cannot prove future
funder availability or model verification latency. If progress projects beyond
the remaining window, report the concrete unchecked keys and bottleneck promptly.

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
