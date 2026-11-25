---
name: monitor-grant-calls
description: Monitor recurring grant programs for new calls, application openings, deadline changes, and closings; prepare evidence-backed daily digests and publication drafts. Use for grant-call monitoring and preliminary applicability flags, not full eligibility assessment or proposal writing.
---

# Monitor grant calls

Maintain a source-backed record of each grant **program** and its dated **call rounds**. The skill describes the monitoring decisions; a separate scheduled runner and durable state store are required for unattended daily operation. Use `set-up-grant-watch` to prepare and verify that automation. Read [the monitoring design](references/monitoring-design.md) before setting up a runner or changing its cadence.

## Desktop development pilot override

For the BIU desktop pilot, first read [the Notion pilot contract](references/notion-pilot.md). Notion in the distinct department workspace is the durable store for configuration, findings, history and recipient policy. This supersedes the older SQLite design. Stop on missing or mismatched workspace access; do not use personal Notion or local business-state files as fallback. Email is draft-only until the sender and exact recipients are supplied and approved. Read the linked repository skill fresh each run; no marketplace release is needed for pilot edits.

## Seed and verify

- Import the supplied grant workbook into private, durable state outside this public plugin repository. Treat its rows as historical examples and source leads, not current open calls. Normalize funder and program identities; deduplicate shared funder URLs. Preserve the original dates and submission stage (letter of intent, pre-proposal, full proposal, or proof of concept) as historical round data.
- For each program, register its official funder page, available official API/feed, BIU page if available, and access method. Confirm whether the program still exists and whether the URL now redirects. Do not infer a new year's dates by advancing an old deadline.
- Prefer a funder's official call record for publication, opening, and deadline facts. A BIU announcement, RSS item, sitemap change, or search result can trigger investigation but is not by itself proof of the current funder deadline. Keep the source URL, quoted field or short evidence excerpt, observed time, timezone, and confidence with every accepted fact.

## Daily monitoring

For operational full-coverage readiness or an explicitly activated resumable worker,
use [operational coverage](references/operational-coverage.md). A bounded pilot
slice cannot establish that all due records were checked. The coordinator copies
configured limits without increasing them; validate capacity before production cutover.
When `monitoring_cadence.coverage_mode = full_due_v1`, that operational route
supersedes the pilot prefix and its legacy numeric caps. Read the current configured
capacity envelope and complete the full due-key reconciliation; never substitute
a reduced slice if the envelope is insufficient. Per-host safety and delivery guards remain.

If Configuration enables `milestone_notices.mode = durable_v1`, follow [durable phase notices](references/milestone-notices.md) every run, independently of source selection. It replaces generic deadline/closing notification assumptions: each sourced announcement, anticipated publication and opening has its own discovery and approaching notice, with separate settings and confirmed-receipt tracking. Existing deadline facts and verification remain in the report.

The separate [publication controls](references/publication-controls.md) feature is
staged for review. Do not activate it from the existence of Notion columns or a
checked request. Only an explicitly activated `publication_controls.mode =
operator_requests_v1` uses that path; every resend still requires independently
verified owner/operator confirmation for its exact request. Preserve delivered
rows and use additive resend rows in the existing notice ledger.

If Configuration has `monitoring_cadence.mode = native_index_v1`, follow [cadence and independent rounds](references/cadence-and-rounds.md) for the four editable defaults, bounded native due-index selection, stage boundaries, sourced date/URL history and capacity reporting. This replaces legacy candidate-prefix selection, while retaining the verifier, daily delivery schedule, caps and email controls.

If private configuration has `operational_catalog`, follow its compact active/due queues, version and persistent fair cursor under the Notion pilot contract. Do not re-enumerate raw baseline/archive pages nightly. Historical deduplication is a separate background job. Apply explicit per-run source/call/request/time budgets, record deferrals in Notion, and distinguish planned counts from actual successful checks.

1. Run the calendar from stored verified facts each day, even when no sites change. Identify calls that open today, deadlines approaching by the configured reminder intervals, and calls that close today. Distinguish submission stages and timezones.
2. Discover new records through official APIs/feeds and registered announcement sources. Check registered program pages with conditional HTTP requests when supported. Compare a normalized, relevant-content fingerprint after a successful response. An unchanged response consumes no model tokens; do not re-research it. Use host-specific rate limits and honor access restrictions.
3. Escalate a new record, relevant change, missing/ambiguous date, redirect to a new call, or source failure to focused research. Read the changed page and linked official call documents; extract dates and status with evidence. Treat a 200 response containing a CAPTCHA or unrelated page as a failure, not a change. When a primary source is unavailable, mark verification pending and route it for human review.
4. Compare verified facts to the stored round. Create idempotent events for a new call, opening, changed deadline, approaching deadline, closing, cancellation, or source failure. Preserve prior values and evidence. Do not create a second notice for the same event and recipient group.
5. Check mandatory prior submission stages and current BIU applicability against official call documents and applicable official BIU restrictions before calling a grant actionable. Record institutional eligibility separately from personal applicant conditions and unknowns. A future full-proposal deadline does not reopen an expired mandatory pre-proposal. Follow the [eligibility and restricted-call policy](references/notion-pilot.md#biu-applicability-and-restricted-calls); preserve reasons, evidence and dates rather than silently dropping calls.
   Unknown BIU eligibility, internal deadlines/requirements, or an inaccessible BIU portal do not block delivery. Keep otherwise source-verified current calls in the main list with explicit unknowns, without calling them BIU-cleared. Only confirmed current-cycle institutional exclusion moves a call to the non-eligible section or permits omission; a lead-only restriction with a possible partner route is not blanket exclusion. Personal conditions are informational. Missing/conflicting funder deadlines, unknown cycles, failed source retrievals or unverified mandatory funder entry stages belong in a separate, explicitly unverified leads section, not an open-call list.
6. Produce a daily administrator digest from stored state: new or changed calls, openings, upcoming closing milestones, failed checks, and last successful coverage. Prepare a university-wide notice only for a verified, relevant event, with a link and clear submission stage. Route the draft through the Research Authority's publication approval process before broad distribution. Sending or publishing requires an authorized channel and configured recipient policy.
   Send a truthful labeled digest to the approved administrators even with partial coverage or incomplete BIU eligibility. Keep the detailed failure/search audit Notion-only. Preserve sender/recipient verification, saved digest and send intent, same-run Sent reconciliation and receipt controls; never retry an uncertain send.
   Use the [deterministic email renderer](references/email-rendering.md): pass existing structured observations to `pilot_contract.prepare_email`. Reuse its checked-in Hebrew RTL templates without reading/recreating HTML or making an extra model-formatting call. Preserve source links, dates/timezones and row caveats; detailed audit stays in Notion.

## Coverage and escalation

- Monitor the scheduler itself. A missed run, stale source, repeated fetch block, or unsupported page must appear in the digest and a repair queue; silence is not proof that no grant changed.
- Perform periodic independent source audits and reconcile program coverage. New programs cannot be discovered by revisiting only the original workbook URLs. Record which funders and source types the monitor actually covers, and state the maximum likely detection delay for each.
- Never claim complete coverage from a finite seed list. If there is no authorized way to read a protected BIU page or a funder blocks automation, use an approved feed/API or a named manual check and disclose the gap.
