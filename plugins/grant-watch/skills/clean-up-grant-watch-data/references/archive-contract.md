# Archive contract v1

## Preservation

Archive means a sidecar flag on the same native row. It never means Notion Trash,
moving/deleting a page, trimming Payload, resetting State, clearing Delivered or
rewriting a receipt. This reduces working-view clutter, not storage. Existing
Key, Round, Digest run, Original notice and Settings relations stay intact.

Proposed additive native fields, **not deployed by this change**:

| Field | Type | Meaning |
| --- | --- | --- |
| Cleanup archived | checkbox | Hide in a new operator working view only |
| Cleanup marker | rich text | Versioned identity, receipt and immutable-row hashes; deterministic attempt key |
| Cleanup archived at | date with time | Archive completion time, distinct from Delivered |

Keep full payload and receipt as the durable deduplication record. The marker
supplements it; the marker alone is NOT a proven replacement for the payload.
Never filter existing Key/Round lookup views on Cleanup archived or terminal
state. Physical Notion archive is unsafe with active-only lookups and prohibited.
Use new operator tabs filtered on the flag; all-state/key/round/reconciliation and
publication-control lookups must still include flagged records. Restoring an
operator view is reversible: clear only the archive flag, retaining the marker
and historical archive timestamp. Restoration is not a resend request.

## Eligibility and exclusions

Require canonical original/resend keys, State=delivered, matching native and
payload Receipt ID and Delivered instant, linked Run relation and exact persisted
Sent manifest/receipt, verified_from_sent=true, SENT label, exact audience/subject/
body hash. Use the historical manifest audience, not today's recipient list.
Unknown or contradictory evidence holds the row; never repair history during
cleanup. Age uses UTC elapsed 24-hour days, strict `age > retention_days`; local
timezone governs the monthly trigger. A date-only Delivered value is insufficient.

Exclude samples, pending/intent/uncertain/approval-pending rows, and every delivered
member of a family with an outstanding resend request, including an unlatched
Publish again checkbox, pending child with checkbox off, or missing active child.
Family means the original key and its explicit resend children. Separate rounds,
stages and notice kinds keep their own keys. A sibling normal reminder does not
turn an already delivered discovery into pending work.

Missing parents, duplicate keys, malformed controls or incomplete traversal block
the entire pass. Failing receipt evidence holds that record. Snapshot input is
limited to 16 MiB; scan defaults to 3000 native rows and 100 archives, editable
within bounded limits. Count rows read during discovery, not only candidates.
Archive candidates sort by Delivered then Key; report capped candidates as deferred.
Do not claim full ledger coverage from a truncated or filtered input.

## Transaction and recovery

1. Acquire the same exclusive lock used by the grant worker (production gate).
   Also verify no active durable grant run; never steal an uncertain lock.
2. Back up the exact native properties and linked manifest reads privately. Plan
   from a complete, fresh snapshot. The offline file adapter is fixture-only.
3. Recheck family/control and immutable preconditions. Write/read back marker.
4. Recheck eligibility and same marker. Write only flag and archive timestamp.
5. Read back original history, marker, flag and timestamp. Record per-key outcome
   outside historical Runs; do not rewrite receipt manifests or delivery intent.

After an interrupted write, read the key first: a matching marker or flag is reused,
never a new attempt. Conflicting marker/history is held. A request arriving after
the marker keeps the row unarchived. No accepted write may be blindly retried.
Notion has no conditional compare-and-set; live mutation still needs a verified
single writer plus handling of concurrent operator edits. File fixtures do not
prove that remote concurrency boundary.

## Snapshot format

JSON fields: `scope="milestone_notices"`, `now` timezone-aware timestamp,
`settings` from Configuration.data_cleanup, `native_rows` (query-shaped properties),
`runs` keyed by Run key. Each Run value contains the stored sent intent, exact
receipt, alert_keys and its Notion `url`. `traversal` contains `has_more=false`,
`includes_logically_archived=true`, `all_notice_states=true`, and actual `rows_read`.
Those flags describe verified connector traversal, not caller permission to write.
The planner never treats a JSON snapshot as authenticated live evidence.

For local transaction tests only, additionally use `isolation="synthetic_disposable"`,
a unique `fixture_id` and `Fixture=true` on every synthetic native row. Never put
real recipients, receipts, payloads or report artifacts in tracked test files.
