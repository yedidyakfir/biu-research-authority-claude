---
name: monitor-grant-calls
description: Monitor recurring grant programs for new calls, application openings, deadline changes, and closings; prepare evidence-backed daily digests and publication drafts. Use for grant-call monitoring, not eligibility or proposal writing.
---

# Monitor grant calls

Maintain a source-backed record of each grant **program** and its dated **call rounds**. The skill describes the monitoring decisions; a separate scheduled runner and durable state store are required for unattended daily operation. Use `set-up-grant-watch` to prepare and verify that automation. Read [the monitoring design](references/monitoring-design.md) before setting up a runner or changing its cadence.

## Seed and verify

- Import the supplied grant workbook into private, durable state outside this public plugin repository. Treat its rows as historical examples and source leads, not current open calls. Normalize funder and program identities; deduplicate shared funder URLs. Preserve the original dates and submission stage (letter of intent, pre-proposal, full proposal, or proof of concept) as historical round data.
- For each program, register its official funder page, available official API/feed, BIU page if available, and access method. Confirm whether the program still exists and whether the URL now redirects. Do not infer a new year's dates by advancing an old deadline.
- Prefer a funder's official call record for publication, opening, and deadline facts. A BIU announcement, RSS item, sitemap change, or search result can trigger investigation but is not by itself proof of the current funder deadline. Keep the source URL, quoted field or short evidence excerpt, observed time, timezone, and confidence with every accepted fact.

## Daily monitoring

1. Run the calendar from stored verified facts each day, even when no sites change. Identify calls that open today, deadlines approaching by the configured reminder intervals, and calls that close today. Distinguish submission stages and timezones.
2. Discover new records through official APIs/feeds and registered announcement sources. Check registered program pages with conditional HTTP requests when supported. Compare a normalized, relevant-content fingerprint after a successful response. An unchanged response consumes no model tokens; do not re-research it. Use host-specific rate limits and honor access restrictions.
3. Escalate a new record, relevant change, missing/ambiguous date, redirect to a new call, or source failure to focused research. Read the changed page and linked official call documents; extract dates and status with evidence. Treat a 200 response containing a CAPTCHA or unrelated page as a failure, not a change. When a primary source is unavailable, mark verification pending and route it for human review.
4. Compare verified facts to the stored round. Create idempotent events for a new call, opening, changed deadline, approaching deadline, closing, cancellation, or source failure. Preserve prior values and evidence. Do not create a second notice for the same event and recipient group.
5. Produce a daily administrator digest from stored state: new or changed calls, openings, upcoming closing milestones, failed checks, and last successful coverage. Prepare a university-wide notice only for a verified, relevant event, with a link and clear submission stage. Route the draft through the Research Authority's publication approval process before broad distribution. Sending or publishing requires an authorized channel and configured recipient policy.

## Coverage and escalation

- Monitor the scheduler itself. A missed run, stale source, repeated fetch block, or unsupported page must appear in the digest and a repair queue; silence is not proof that no grant changed.
- Perform periodic independent source audits and reconcile program coverage. New programs cannot be discovered by revisiting only the original workbook URLs. Record which funders and source types the monitor actually covers, and state the maximum likely detection delay for each.
- Never claim complete coverage from a finite seed list. If there is no authorized way to read a protected BIU page or a funder blocks automation, use an approved feed/API or a named manual check and disclose the gap.
