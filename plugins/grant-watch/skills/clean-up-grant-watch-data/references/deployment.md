# Staged deployment and scheduler recipe

No production cleanup or new scheduler is installed. No change to the live digest
checkout is required for this review. The branch includes the earlier staged
publication-control adapter; do not deploy that ancestor incidentally with cleanup.

1. Review the cleanup-only commit/diff. Run the full plugin regression suite and
   validate this skill. Keep the existing desktop branch and linked monitoring
   skill untouched. Install/link only the separate cleanup skill from an isolated
   checkout after review; do not merge main or repoint the digest junction.
2. Keep `Configuration.data_cleanup.enabled=false` while staging. Retention and
   calendar interval are independently editable there. Verify the exact workspace
   and parse/read back the block, retaining all unrelated settings verbatim.
3. Run the implemented `cleanup_notion.py plan` through the authenticated host
   bridge. See [adapter contract](notion-adapter.md) for commands and policy.
   The available Notion connector lacks the atomic conditional update needed to exclude
   an operator request arriving between the last read and write. The adapter
   refuses production archive; rereading or setting a policy flag cannot enable it.
   The journal/readback/restart transaction is tested against an atomic mock only.
   Before enabling writes, obtain supported atomic coordination covering the
   notice family, configuration and active Runs, or an independently enforced
   exclusive-writer workflow covering human editors as well as workers. This is
   an additional access/workflow decision, not permission to change access now.
   Then validate the actual boundary on a disposable Notion fixture.
4. Verify the actual grant worker's lock protocol/path and acquire the SAME lock,
   plus check durable active runs before mutations. A clock window alone is not
   an overlap guarantee. Live shared-lock and remote operator races are remaining
   integration gates; leave all real notices in place until proved. The implemented
   Windows FileShare.None lock excludes other Python/.NET processes on a fixture
   path; it is NOT yet bound to a verified shared path in the live grant worker.
5. Add the three sidecar fields and NEW working/archive views after schema backup;
   restore old view displays if Notion auto-adds fields. Read back Key/Round/all/
   unresolved/control view filters to prove archived rows remain discoverable.
   Test the deployed notice CLI and staged Publish again path against fixture rows
   before scheduling. Never activate physical archive or delete APIs.
6. Parent creates exactly one separate cleanup schedule once gates pass. Suggested
   first run: the first day of the next month at 21:30 Asia/Jerusalem. Configure
   `month_day` (1–28), `interval_months` and `local_time` on Configuration. Keep an
   explicit anchor month. A change in interval/time requires parent to update the
   scheduler from those settings; editing Notion does not silently reschedule it.

For the default monthly interval, a future parent-owned schedule is:

```text
BEGIN:VEVENT
DTSTART;TZID=Asia/Jerusalem:<NEXT_MONTH_FIRST_DAY>T213000
RRULE:FREQ=MONTHLY;INTERVAL=1;BYMONTHDAY=1;BYHOUR=21;BYMINUTE=30;BYSECOND=0
END:VEVENT
timing_mode=exact_schedule
default_timezone=Asia/Jerusalem
```

Resolve the actual DTSTART at scheduling time; no schedule was created here. The
`schedule` CLI calculates next occurrence using `now`, settings, `anchor_month`.
Avoid a 30-day recurring interval. Default mutation window is 21:30–22:00; skip
outside it or on lock/active-run conflict and record a missed cleanup. Do not
catch up during 08:00–20:00 or change grant cadence. Parent can retry within a
safe later window without re-archiving completed keys. Actual write deadlines
   use the adapter/transaction's monotonic runtime limits as well as wall-clock
   window checks. A blocked run never grants permission to widen the window.

Suggested scheduler prompt: “Use the separately linked clean-up-grant-watch-data
skill on the verified desktop. Read BIU Configuration.data_cleanup fresh. Follow
the configured retention and calendar schedule, shared grant lock and 21:30
window. If cleanup is disabled, integration is unverified or evidence incomplete,
report skipped/blocked and leave records in place. Only logically archive eligible
verified delivered Milestone Notices, preserving payloads, keys, receipt history
and Run logs. No email, draft, source requests, physical archive or deletion.”

Rollback: disable the cleanup scheduler and settings; keep all marker/history
fields. Restore working views or clear only logical archive flags after exact-key
readback. Never clear a Delivered/Receipt/State field to restore visibility.
