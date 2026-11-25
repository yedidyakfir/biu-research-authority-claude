# Cadence and independent application rounds

If Configuration `monitoring_cadence.coverage_mode` is `full_due_v1`, use
[operational coverage](operational-coverage.md) for full unfiltered traversal,
resumable source batches and cumulative configured capacity. The bounded pilot
selection below is a rollback path only in that mode; do not apply its numeric
defaults or stage prefixes. Lifecycle/date/eligibility rules below still apply.

When Configuration has `monitoring_cadence.mode = native_index_v1`, use this path instead of the legacy candidate-prefix/retry list. Daily delivery, verification, exclusions, lock, email guards and caps stay under `notion-pilot.md`. The old queue/cursor and archive remain provenance and rollback state; do not advance them from index selection.

## Four editable defaults

Read the four native Cadence Settings rows fresh each run. Keys and default `Interval days`:

| Key | Default | Check |
|---|---:|---|
| no_next_dates | 7 | Official grant URL for dates, unavailability and moves |
| announced_unpublished | 7 | Announced timing and formal publication |
| published_future | 3.5 | Formal call opening and deadlines |
| open | 3.5 | Dates through the final submission deadline |

The cadence class is not evidence of openness or eligibility. A published call with unresolved opening/entry facts uses the conservative twice-weekly class with `opening_or_status_requires_verification`; never describe it as a confirmed future opening. Missing dates stay unknown. A closed round's program URL returns to weekly discovery for new cycles. Retain continuing-applicant deadlines separately from the cutoff for new entrants.

`cadence_contract.regular_due` assigns stable Sun/Wed, Mon/Thu or Sun/Thu pairs for 3.5 days (3/4-day gaps) and one weekday for 7 days. Custom intervals use the first existing scheduled wake after the interval. Do not implement repeated `last_success + 84 hours` rounded forward: that drifts to six-day gaps around weekends. The existing Sun–Thu08:00Asia/Jerusalem schedule is unchanged.

Known publication/opening/deadline milestones override routine cadence at the preceding scheduled wake and first following wake. Date-only facts use explicitly labeled conservative planning proxies, not invented cutoff times. A Friday–Saturday window gets Thursday verification and Sunday reconciliation; disclose that there is no live check inside that window. Evaluate stored calendar facts at every daily wake even when a source is not due. A clock transition alone never proves applications opened; after the last verified deadline do not keep presenting the round as open.

## Bounded selection and observable capacity

1. Verify workspace, catalog/index readiness and lock. Fetch live schemas/views and the four cadence rows. Index entries point to existing queue records and canonical Rounds; they are not newly verified grants. Hold missing URLs and unresolved identities explicitly.
2. Use the configured four stage views plus boundary view. Stage views sort by **Last attempt, Queue rank** and are intentionally not filtered solely on cached Next due: shorter cadence edits must apply immediately. Fetch up to20 rows per stage and20 boundary rows initially; recycle unused allowance round-robin across stages, up to100 index rows total. Respect connector pagination and save continuation cursors when truncated. Bound calendar rows at100, all metadata rows at400 and connector reads at100 (including schema/config, pagination and selected evidence-page reads). Reserve up to60 selected index/round page fetches inside that100; stop and audit deferrals before any limit. Count rows/read attempts used to find work, not just selected calls. Never enumerate historical archives or the full index nightly.
3. Save a private bounded snapshot with `now`, `settings`, `native_notion_rows: true`, `rows`, and `limits` (`call_limit` from the existing cap, `scan_limit:100`, persisted tie cursor, `remaining_work` from any has_more). Run `scripts/cadence_contract.py snapshot.json --output plan.json`. This is the scheduled **no-send selection entry point**. It recomputes due state from fresh settings, counts examined rows, gives due boundaries priority up to the existing cap, then fills remaining slots with oldest due debt and persisted queue rank. Report any boundary work beyond the cap explicitly. Different round keys of one program remain separate. Duplicate/conflicting keys require reconciliation. Plans have zero observed successes.
4. Reuse an official response across discovery/call checks when it is the same request; registered sources are linked in the index. Enforce the unchanged20 source /30 call /100 external request /45 minute /30 second /2 retry caps before every request, redirects and follow-ups included. Leave source verification and digest classification to the existing verifier. No model-based HTML step.
5. Fetch only selected index/round evidence pages. After each actual attempt, use the same CLI with `operation: complete_attempt`, the full `target`, `now`, fresh `settings_rows`, `settings_pages` and `outcome` (`success`, optional sourced `observation` or `error`). It emits the revised target and typed `index_properties`, without writing or sending. Persist actual audit, append sourced revisions, then apply/read back those properties. Success alone advances **Last success**. Failure keeps overdue debt; its next routine retry follows its cadence, accelerated for known boundaries. Deferral is not an attempt or success. Move the fairness cursor only after outcome write/read-back, never on selection alone.
6. Persist Notion-only counts for metadata rows, connector reads, scanned index rows, selected/attempted/deferred checks, oldest overdue age, boundary deferrals, unknown work beyond pagination and settings snapshot. Display capacity: sum `7 / interval_days` over active target classes versus `calls_per_run * 5`; count this as target-check demand, not unique grants or HTTP requests. Never claim all cadences are achieved under overload. Preserve the caps and surface the shortfall for owner review.

## Dates, identity and URL history

Every round has a stable **program key + call/round key**, not merely program plus year. Multiple rounds in one year may coexist. An observation for another round creates/links its own record; `record_observation` rejects cross-round, stale or conflicting same-time replacement. Native Announcement, Publication, Opening and Final deadline fields are projections of sourced facts. Keep all mandatory entry/intermediate/final deadlines, original text/timezone, certainty, verification time and revisions on the round page. Publication existence is separate from its actual publication date: never use the first observation timestamp as a publication or announcement date.

When the existing verifier establishes a new round, create a separate index pointer and Round relation; do not reuse the parent's previous round. Reconcile exact stable keys using the configured dedicated key-lookup views before creating. Under the single-writer lock, set their exact Key filter, query the saved view and check all matches; no SQL/rows fallback. A lookup/creation still consumes the read/write budget. Save ambiguous identities for review. Keep revision pages bounded: append up to20 revisions per history page, then create/link a numbered continuation; retain old pages and never load all history during routine checks.

`observe_url` records redirects/moves as candidates with the old URL intact. Promote only after verifying replacement content identifies the same program/call against authoritative evidence; retain old/new URL, time, reason and evidence. After promotion, update/read back both the index URL and any linked registered Source URL, preserving the history in both records. A redirect, title match or failure alone cannot overwrite the official URL, establish a new round or prove cancellation. Preserve unavailable sources as failures and continue scheduled checks without bypassing access restrictions.

## Migration / rollback

`cadence_migration.py` builds a no-I/O, no-send additive plan from a private snapshot. Persist the before-config and schemas/properties in the BIU migration page first. Original queue records, round reviews and prior run/cursor history remain unchanged. Populate native index rows in bounded batches, reconcile keys/counts/relations, add lifecycle fields to existing rounds, then read back and run the actual selection CLI against native rows. Do not store real fixtures, addresses or workspace IDs in Git. Rollback changes Configuration back to the saved legacy mode/config and reverts the focused code commit; additive rows/fields can remain inactive for inspection. No destructive rollback or second automation.

This pilot is executed by the scheduled desktop agent, not a standalone daemon. A tested helper and migrated index prove the selection path and persistence, not a future unattended wake or full source coverage. Report production configuration, linked code ref, the no-send integration result and remaining scheduler prompt assumptions separately.
