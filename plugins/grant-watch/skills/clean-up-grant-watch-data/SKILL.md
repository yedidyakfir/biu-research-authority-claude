---
name: clean-up-grant-watch-data
description: Plan and validate reversible Grant Watch data retention when asked to clean up or archive old delivered milestone notices. Read configurable retention and monthly cadence from the existing Notion Configuration. This staged skill provides a no-send dry run and disposable-fixture archive; it cannot perform production cleanup or authorize sending.
---

# Clean up Grant Watch data

Use the existing Milestone Notices ledger, not a new archive database. The first
rule covers only real delivered notices whose **verified Delivered timestamp is
strictly more than retention_days elapsed days ago** (default 7). Monthly interval
defaults to 1 calendar month in Asia/Jerusalem. Dates of discovery, milestone,
planned send, creation and editing are not delivery dates.

## Read and plan

1. Read the owner's configured Notion connection and Configuration fresh; verify
   the actual workspace. Use `data_cleanup` settings. This version must remain
   `enabled=false`, `mode=staged_logical_archive_v1`. Do not change daily monitoring,
   renderer, recipients, source limits, publication controls or schedules.
2. Follow [the archive contract](references/archive-contract.md). Read only the
   native Milestone Notices ledger through bounded saved views and the linked
   receipt manifests needed for candidate notices. Never scan the raw grant
   archive. Preserve data provenance; never infer receipt verification from age.
3. Require exhausted traversal including every notice state and logically archived
   row. If the scan/byte budget, connector, original-request graph or receipt
   readback is incomplete, report unknown/unscanned work and archive nothing.
4. Run from the plugin root, with private ephemeral JSON outside Git:

   ```text
   python scripts/cleanup_contract.py plan private-snapshot.json --output private-plan.json
   ```

   This is an advisory dry run, not proof of live write safety. Read the returned
   status, held reasons, deferred keys and counts. Do not reclassify failures as
   successful empty cleanup. No mail, draft, live source request or deletion tool
   belongs in this workflow.

## Test and stage

The same CLI accepts `fixture-archive` only for an explicitly marked local
synthetic disposable fixture. It writes the compact marker, reads it back, then
sets the logical archive flag and verifies history stayed byte-for-byte intact.
`--stop-after marker` and `--stop-after archive` exercise restart recovery.

```text
python -m unittest discover -s tests -p test_cleanup_contract.py -v
python scripts/cleanup_contract.py fixture-archive private-fixture.json --output private-result.json
```

Never mark a real row as Fixture to get past this restriction. No production write
adapter ships with this staged version. Keep records in place until the integration
gates in [deployment](references/deployment.md) are met. Parent owns scheduling.
Return branch/commit, dry-run counts, tests, untouched live runtime ref, and exact
remaining integration blockers. Future cleanup rules need their own preservation,
scope and deduplication tests; this skill does not authorize broader retention.
