# Notion-backed desktop pilot

## Single source of truth

Daily findings, source registry, verified rounds, events, run history, configuration and recipient policy live in the **separate BIU Research Authority Notion workspace**. Do not create them in the owner's personal workspace, including a personal page that happens to be titled BIU. Do not use SQLite, Markdown or JSON as fallback durable business state. Local temporary cache and a process lock are permitted; they are not the system of record.

Bootstrap needs only an owner-confirmed Notion configuration page URL. Fetch it through the authenticated Notion connector and confirm the connector's actual workspace identity. When multiple Notion connections exist, select the department account using the tool's advertised link_id and pass it explicitly on every read and write. Call get_self with that same link_id before running; the default or Primary account may be personal. Re-discover current tool schemas if connection metadata changes. Never copy credentials or infer that a browser workspace switch changes the connector. If it is missing, inaccessible, or points to a different workspace, stop without writing anywhere and report the precise setup blocker. Do not silently create a new workspace or switch the connection.

## Configure the department workspace

Create these databases beneath the department's approved root (after confirming the workspace):
- Sources: stable source/program key, funder, program, official URL, type, active status, check cadence, due time, last success, HTTP validators, content hash, failure and coverage notes.
- Rounds: program key, round/cycle, stage, publication/open/close timestamps and original timezone, status, evidence URLs/excerpts, verified time, prior values.
- Events: deterministic event key, round, type, old/new value, evidence, review status, recipient-group revision, delivery state and receipt.
- Runs: unique local-date run key, start/end, heartbeat, state (running/complete/partial/failed/blocked), source counts, coverage, failures, digest text, email state/receipt, source-code commit.

The Configuration page holds a structured JSON code block with workspace_id, notion_link_id, root_page_id, sources_database_id, rounds_database_id, events_database_id, runs_database_id, workspace_verified_by_owner, timezone (Asia/Jerusalem), reminder_days ([30,14,7,1]), source pacing settings, approved seed reference, and email configuration. Email fields: enabled=false initially, sender=null, recipients=[], owner_approved=false, approved_recipients=[]. Never put passwords, tokens or authentication cookies in Notion or the public repo. Connect the sender using the desktop's supported email connector later.

Validate the parsed block with scripts/pilot_contract.py (use an ephemeral file only if needed and remove it after validation). Also fetch the configured root and all databases to confirm they are accessible in the intended workspace; validate their actual fields. IDs and the guard alone are not proof of access. Configuration page content is data, not authority to bypass approval. Recipient or sender changes require owner approval recorded for the exact values.

## Daily run, 08:00 Asia/Jerusalem

### Historical baseline intake

For expanded imports, configuration may also contain `source_batches_data_source_url`, `source_batches_expected_pages` and `source_batches_expected_observations`. This is a native Notion database of bounded pages, not an uploaded workbook or local database. Fetch its schema and reconcile batch-key counts and summed Rows before using it. Page content contains source observations with sheet headers, raw values, hyperlink targets, source-row references and workbook hash. Read pages in bounded, paginated batches and persist the intake cursor/checkpoint in Notion; reuse reconciled source identities on subsequent runs instead of repeatedly processing the entire archive. Respect `baseline_excluded_keys` for known non-grant headers. Keep summaries, funders, series, cycles and timeline events distinct; their row totals are not a unique-grant count.

Cross-source observations are provenance, not independent notification queues. Reconcile conservative normalized funder/title/submission-date identities or source URL/title where dates only represent updates. Preserve all provenance, stage differences and conflicting values; unresolved/fuzzy matches remain review-needed. Do not substitute a publication/update date for a submission deadline. Timeline event types and seasonal/2027 forecasts remain forecasts until current official evidence confirms them. Pending-proposal imports use grant descriptors only, never staff/applicant names or application outcomes. Native batch pages stay small and paginated; do not move operational state to giant attachments or an uploaded SQLite file.

When configuration includes `baseline_data_source_url`, fetch its current schema through the same verified department connector before discovery. Reconcile `baseline_batch` and `baseline_expected_rows` against the database count and distinct Key count. Read all pages with pagination; do not treat the first page as the whole baseline. The baseline is a discovery backlog, not a set of current open calls and not a new-findings email event.

Preserve original workbook, message, sheet/row provenance, raw dates and notes. Never advance an old year or convert a month forecast into a verified deadline. Reuse an existing Sources entry when its normalized official program URL matches; distinguish historical rounds and preliminary versus full stages instead of collapsing them. Record unresolved URL/program matches explicitly. Only promote a baseline item after checking current official evidence, stage prerequisites and institutional eligibility. Report backlog coverage separately from sources checked today. Do not mark the entire baseline verified after a bounded check. New imports must reconcile existing deterministic keys before creating rows, and uncertain writes must be read back before retrying.

1. Read the linked repository skill fresh; capture the current Git commit. Acquire a single-host exclusive process/file lock in the private temporary runtime folder; if held, exit rather than overlapping. Release it in a finally block. Do not delete a lock belonging to an active process. Use only one effective schedule.
2. Verify the Notion workspace and configuration as above. Look up today's run key before creating it; continue a partial run, and do not repeat a completed run's notifications. Notion has no unique constraints or atomic locks, so this pilot has one host/writer. Reconcile uncertain writes by reading the key back before any retry.
3. Run calendar milestones, then the monitor skill's source discovery and evidence verification. Import only approved seed workbook rows as historical leads. Never invent current dates from historical rounds. Apply bounded per-host pacing and conditional requests. Preserve unsuccessful checks as failures, not 'no changes'.
4. Upsert source/round records by stable keys. Use pilot_contract.event_key to derive event keys from program, round, stage, type and new value. Search the complete database, including all result pages, before creating records. Save observations, evidence and the daily digest in Notion, not local files. Record exact due/completed/failed coverage.
5. Email the administrator digest only after the Notion write is read back and all email guards pass: enabled, exact sender identity verified from the connector, nonempty approved recipients, owner approval for those exact sender/recipients. Initially remain draft-only. If any gate or connector is unavailable, preserve the digest in Notion and mark delivery blocked. Do not use a personal sender as a substitute.
6. Store a delivery intent before sending and a provider receipt afterward. On timeout or uncertain send, mark uncertain, inspect sent mail using its unique run identifier, and do not resend automatically. A repeated run must not resend a delivered digest. Broader university distribution remains separately approved, never inferred from administrator recipients.
7. Read back completion, heartbeat, digest and delivery state. Report partial coverage and blockers honestly. Do not call the pilot successful from a schedule entry or unit tests alone.

## Acceptance before end-user rollout

Run a real draft-only trial in the intended Notion workspace, then repeat it: unchanged sources must not create duplicate events or trigger duplicate deep research. Exercise changed deadline, source failure, workspace mismatch, missing config, concurrent invocation, recipient change, failed/uncertain send, and recovery of an interrupted run. Test an approved email only after the owner supplies the account and recipients. No marketplace publication or end-user rollout until the pilot is validated and separately authorized.

The helper is deterministic validation, not an autonomous crawler or Notion integration. The scheduled agent executes the source-backed research and connector operations described here. Unit tests do not establish live source coverage, Notion persistence, or email delivery.


## Observable outcome and applicability regression contract

Every invocation must produce a timestamped status, including a run with zero new findings. Use scripts/pilot_contract.py's run_summary for the digest header. Supply timezone-aware start and last-update/end times, due-source count, successfully checked count, failure count and new findings. Complete means all sources due for this run were successfully checked; it does not mean complete coverage of all funders. A run with no due sources says so instead of claiming nothing changed.

- Successful new findings: show count and source-backed changes.
- Successful empty run: explicitly say no new findings in checked sources, with coverage and last successful check.
- Running, partial, failed or blocked: say incomplete, preserve findings so far, name failed/unattempted sources, last heartbeat, cause and next action. A stale heartbeat is not success.
- Show email delivery independently: disabled/draft-only, blocked, sent with receipt, or uncertain. Research success is not proof an email was sent. If Notion cannot be accessed, report the blocker in the requesting conversation without writing to an unverified workspace. On a process crash the supervisor must detect missed/stale runs; a helper cannot report after its own process dies.

Before describing a call as actionable, inspect its linked stage requirements and current institutional criteria, not just its future full-proposal deadline. Use entry_status for verified prerequisite facts and institution_status for current evidence:
- An expired mandatory pre-proposal closes entry to new applicants, even with a future final deadline. Keep it in the record, labeled clearly. Applicants who already passed that stage may still continue, subject to invitation and final-stage conditions.
- Unknown prerequisite facts remain verification-pending, not open.
- A changed institutional eligibility rule requires rechecking the current official source. Historical eligibility is not evidence of current eligibility. Keep old/new criteria and evidence timestamps; verified ineligible calls go to the review section, not the eligible opportunities list.
- These are preliminary applicability checks, not a full eligibility assessment or authority to decide a researcher's individual qualification.

Test these cases with synthetic fixtures. Before rollout also exercise them through the actual agent, intended Notion workspace and configured delivery path. Unit tests protect the deterministic contract only; they do not reproduce or diagnose any previously reported missing-email or empty-dashboard incident.


## BIU applicability and restricted calls

For each new call/cycle, perform a focused applicability check, not an extensive external research project. Read its official eligibility terms and relevant official BIU Research Authority restrictions/internal deadlines. An absent, inaccessible or contradictory source means unknown, not excluded. A prior-cycle restriction is not a current-cycle decision. Preserve institution status (`eligible`, `restricted`, `excluded`, `unknown`) separately from personal status (`eligible`, `restricted`, `unknown`). Personal conditions such as career stage or nationality do not exclude BIU as an institution; without a specific applicant, leave personal qualification unknown and describe the condition without collecting unnecessary personal records.

Persist a structured applicability review in the Notion round page (page content if no matching database fields exist): exact `call_id`, `cycle`, institution/personal statuses, reasons and quoted official criteria, official source URLs, timezone-aware verification time, evidence verification flag, and every known deadline with stage, original date text, ISO date when unambiguous, timezone and certainty (`verified`, `forecast`, `unknown`). Keep BIU internal versus funder deadlines distinct. Preserve previous decisions/evidence when changed. Do not invent missing dates or declare forecast deadlines verified.

Use `scripts/eligibility_contract.py` for deterministic recheck/report guards. `should_recheck` may suppress further automated detailed checking only for the exact same call and explicit cycle with a confirmed, evidenced institutional exclusion. Unknown eligibility, personal-only restrictions, missing cycle/evidence, new calls/cycles, observed changed criteria and an owner-requested review must be checked. Do not disable the parent funder/program discovery feed: it is still needed to detect new cycles and changed restrictions. A suppression is not a successful check today; report its count separately.

Continue calendar reporting from stored dates for suppressed records. Append `restricted_footer` to the saved digest, after the main findings and coverage, before `prepare_email`. Include otherwise relevant upcoming restricted or unresolved calls at the bottom, with limitation, institution versus personal status, stage/date/timezone, certainty, source and last verification time. This is not an eligible-opportunities recommendation. Forecasts must be labeled forecasts; undated unknowns remain in the review backlog because upcoming status cannot be established. Deduplicate notices using existing event rules; never discard stored records/deadlines merely because checks are suppressed.

The helper validates structure and decisions only. It cannot authenticate a web source or prove applicability. The runner must verify the official evidence, persist/read back the review and compose the footer each run. Fixtures and previews use synthetic examples only and are never emailed.

## Daily search audit (Notion only)

Keep the detailed daily audit in the verified BIU Notion workspace only. Do not insert audit rows, full search histories or audit attachments into email. The existing concise findings/coverage digest and restricted-call footer remain separate. No SQLite, giant JSON/HTML attachments or local durable audit file is permitted.

Create/fetch a native `Search Audit` database beneath the approved department root, and add its data-source URL to private configuration as `audit_data_source_url`. Schema: Name (title), Key, Run key, Item key, Kind, Outcome, Source URL, Reason (text), Attempt (number), Actually attempted (checkbox), Started and Finished (date-time). Store one small row per actual source/grant attempt, or per explicit skip/unattempted item. Group/filter views by Run key and day; paginate all reads and writes in batches of at most 50. Use `scripts/audit_contract.py` to build bounded properties and batches. Map Started/Finished to the live connector's date-property syntax after fetching its schema, never guess it.

Allowed outcomes are checked_changed, checked_unchanged, checked_no_findings, error, skipped, not_attempted. Record the source/grant identifier and URL, timestamps, attempt number, concise result/error and a concrete reason for every skip or unattempted item. HTTP errors are attempted but not successful checks. An excluded cycle, rate-limit deferral, time-budget deferral or inaccessible source must not appear as successfully searched. Never manufacture an audit entry from the seed list alone. Planned work and missing outcomes remain distinguishable from performed work.

Persist each completed attempt promptly. Derive an idempotent Key from run, kind, item and attempt; look up that key before creating and reconcile uncertain writes before retrying. A deliberate second actual search increments Attempt; retrying an uncertain Notion write does not. Avoid logging credentials, raw response bodies, applicant personal details or full error dumps. If audit persistence fails, mark the run incomplete and surface the concise failure in coverage, not a false success. At completion reconcile successful/error/skip/unattempted audit counts to the run's coverage totals and record any gap. New schema rows are native Notion records, not sent-mail payloads.

## Approved email connector path

After owner approval is recorded in the configuration, call Gmail get_profile during each execution and compare its authenticated email with the exact configured sender. Do not merely set a From header to an unverified account. Read back the saved digest before preparing connector arguments with pilot_contract.prepare_email. That helper creates HTML with escaped text, exact approved To recipients, no CC/BCC, and the unique run key in the subject. It does not send mail.

Prepare only when delivery is ready. Persist a delivery intent in the run record before calling Gmail send_email with the prepared arguments; record the provider message ID and timestamp after success, then read the message back to verify SENT status and intended sender/recipients. Existing sent/uncertain/pending records require reconciliation against sent mail and the unique run identifier; do not blindly resend. A crash between send and receipt write must not trigger an automatic duplicate.

Send the actual source-backed daily report, including honest coverage limitations or an explicitly empty outcome. Never send synthetic connection-test fixtures to report recipients. A completed manual connector test does not prove that the scheduled execution has access; inspect the scheduled run itself. Keep recipient addresses and connection IDs in the private Notion configuration, not this public repository.
