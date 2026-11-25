# Notion adapter and remaining coordination boundary

`scripts/cleanup_notion.py` uses the actual supported hosted Notion tools. The host
services nonce-bound RPC requests with unmodified authenticated tool results;
credentials and direct HTTP workarounds are unnecessary. It does not trust an
ordinary snapshot or editable Notion field as evidence of write coordination.

## Commands

From the plugin directory, using Python with the repository's existing dependencies:

```text
python scripts/cleanup_notion.py plan --policy private-policy.json --rpc-dir private-rpc --output private-plan.json
python scripts/cleanup_notion.py archive --policy private-policy.json --rpc-dir private-rpc --output private-blocked.json
python -m unittest discover -s tests -p test_cleanup_notion.py -v
python -m unittest discover -s tests -v
```

The second command is a fail-closed diagnostic, **not an enabled archive route**.
Do not invoke it expecting a write. Outputs must be new files outside Git. The
policy supplies pinned `link_id`, `workspace_id`, `config_url`, `ledger_source`,
`ledger_view`, `runs_source`, `runs_view`. Use the verified existing BIU sources
and unfiltered saved views; never SQL or historical grant archive enumeration.
Optional caps: `max_connector_calls=100`, `max_rows_read=5000`,
`max_run_rows=300`, `runtime_seconds=300`, with bounded safety ceilings.

RPC: on `GW_RPC <request-path>`, read its JSON. Dispatch only:

| Method | Supported tool |
| --- | --- |
| notion_self | notion_get_self |
| notion_fetch | Notion fetch |
| notion_query_view | query_data_sources, mode=view |

Write a sibling response JSON with exact `id`, `method`, `args_sha256`, and
unmodified `result`; return its path on stdin. Never fabricate tool responses,
silently change arguments, approve new methods, or retry uncertain mutations.
No send/create/update/delete tool is in this adapter's allowlist. The inherited
RPC transport is not cryptographic protection from a compromised local host.

## Reads and evidence

The adapter verifies the connected workspace and pinned Configuration references,
required native field types and Run relation, unfiltered saved views, bounded
pagination including physically archived query results, and duplicate/active Run
state. It fetches each needed delivered notice's linked Run and extracts the
unique persisted sent manifest, retaining historical recipient values. It never
rewrites those logs. Config/view readback detects changes during traversal, but
does **not** establish an atomic database snapshot.

Count every native row, including finding work and Run rows, every metadata call,
and every pagination continuation. Invalid/repeated cursors, truncated reads,
missing/ambiguous receipts, or changed configuration remain explicit blockers.
A zero-candidate advisory result does not prove safe scheduling.

## Why writes are blocked

The currently available Notion update tool accepts a page ID and properties; it
does not accept an atomic revision precondition or transaction across a notice,
its resend children, configuration, and grant-run state. A human can set Publish
again or a worker can create a child after the last read but before an
unconditional archive write. Readback detects some races after the fact; it cannot
make that write conditional. Native schema locks and an editable lock checkbox
are not mutual exclusion over data edits.

The production adapter therefore hardcodes the unsupported capability as false.
`atomic_family_compare_exchange=true` or `shared_lock_verified=true` in JSON is
ignored. No update tool is called. Do not replace the refusal with an
unconditional update, a last-edited timestamp check, or a simulated transaction.

## Transaction and lock tests

`cleanup_transaction.py` implements the future transaction boundary. It requires
a server-atomic revision covering the complete notice/request family, all relevant
Runs and configuration. Test stores provide that behavior explicitly as mocks.
The staged engine accepts only disposable fixtures; it cannot activate production.

Before provider action it saves the immutable backup and deterministic archive
intent, writes/readbacks the marker, rechecks eligibility, and writes/readbacks
the archive flag. Accepted timeouts reconcile matching persisted state on restart;
an absent uncertain write is held rather than blindly retried. Only sidecar fields
are writable. Tests place edits in the last-read/write gap, add a new resend child,
start a grant run, change settings/history, corrupt readback, repeat attempts,
exhaust budgets and end the runtime window. These prove the algorithm under the
mock's stated atomic semantics, not that Notion provides those semantics.

`SharedRunLock` uses a Windows handle with FileShare.None on an absolute,
non-redirected path. A second process cannot acquire it; Python/.NET compatibility
is tested. The handle is released on process termination, and the path is never
deleted or considered stale by age. The live run's saved PID alone does not
identify its exact lock protocol/path. Both scheduled monitoring and cleanup must
be demonstrably bound to one shared lock before deployment. Do not test this by
taking the real grant lock during implementation or repointing tomorrow's runtime.

## Scheduling gate

Keep cleanup disabled and schedule only advisory planning until an owner-authorized
coordination mechanism exists. Safe mutation requires either a supported atomic
Notion/transactional boundary or an enforced single writer that also excludes
direct operator edits during cleanup, plus the shared grant lock. Setting this up
could require access/workflow changes; none are made or implied here. After that,
install/read back sidecar fields and operator views, prove exact-key/round/resend
lookups still include archived records on a disposable fixture, and run the
deployment checklist before parent schedules the first cleanup.
