---
name: classification-rule-learning
description: Learn narrow, foundation-specific classification rules from human-reviewed fund-report decisions stored on this device; prune old history and retire examples covered by accepted rules. Use before fund-report-analysis or when updating its local learning memory.
---

# Classification rule learning

Use only human-reviewed decisions from the device-local memory database. This skill runs at the start of `fund-report-analysis`; it may also be run on its own. The database and token stay outside the plugin repository and are never uploaded or committed.

## Workflow

1. Choose the local memory directory. Honor `FUND_REPORT_MEMORY_DIR` if set. On the BIU Windows workstation, use `D:\work\local-data\financial-report`; elsewhere use the current user's private application-data directory. Run `scripts/bootstrap.py --memory-dir MEMORY_DIR` first. It initializes the database and receiver and deletes raw decisions older than six calendar months; durable rules remain. Add `--tailnet` only when serving the review page through the BIU tailnet. In Codex desktop, use `load_workspace_dependencies` to locate the bundled Python runtime.
2. Read `history` and `rules` from the same database, scoped to the requested foundation and program. History contains only explicit reviewer decisions, with their short rationale and minimal transaction evidence. Never learn from the agent's own suggestions or classifications.
3. Infer a rule only where at least two independent human decisions support the same outcome, the account and a distinctive description or reference phrase establish a narrow pattern, and no reviewed example contradicts it. Read the foundation reference in `../fund-report-analysis/references/foundations/` and avoid rules that would bypass its scope, restrictions, or the approved-budget check. Do not promote a rule merely because two rows share a broad word such as “purchase” or “service.” It is fine to find no safe rule.
4. Write a candidate JSON file outside the repository and pass it to `add-rule`. The script checks category validity and all retained matching decisions, saves the rule, then deletes the covered history in one database transaction. If validation rejects the rule, narrow or discard it; do not force it through. New human decisions that contradict an active rule automatically deactivate that rule while preserving the correction.
5. Return active rules to the calling skill. They are **advice for uncertain rows**, never a replacement for foundation policy or row evidence. A rule never establishes grant eligibility. `suggest-report` returns possible matches without editing the report.

Candidate JSON fields: `foundation_id`, `program`, `condition`, `status`, `category_id`, `explanation`. `condition` accepts `account_exact`, `description_contains_all` and `reference_contains_all`; at least one distinctive description or reference phrase is required. `status` is `classified` or `not_expense`; the latter requires a null category. Use the exact program label from the foundation reference so rules do not cross grant programs.

```text
python scripts/bootstrap.py --memory-dir MEMORY_DIR
python scripts/memory_store.py --db DB prune
python scripts/memory_store.py --db DB history --foundation isf
python scripts/memory_store.py --db DB rules --foundation isf
python scripts/memory_store.py --db DB add-rule CANDIDATE.json
python scripts/memory_store.py --db DB suggest-report REVIEW.json
```

For reviewer pages on another device, follow [local sync setup](references/local-sync.md). If sync was unavailable, use `ingest-reviewed-report` on an exported review JSON; it imports only rows explicitly marked `reviewed_by_user`.
