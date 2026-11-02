# BIU Research Authority Claude marketplace

Private Claude Code plugin marketplace for research administration workflows.

## Current contents

- `financial-report`: includes the `fund-report-analysis` skill for ledger classification and review.

## Add the marketplace

Authorized users with access to this private repository can run:

```sh
claude plugin marketplace add yedidyakfir/biu-research-authority-claude
claude plugin install financial-report@biu-research-authority
```

The repository contains the plugin and its reusable workflow. Financial source workbooks and classified outputs stay in the ignored local `analysis/` directory.

The [ISF category research](docs/isf-expense-categories.md) records the initial official-source headings and the checks needed before implementing the skill.

Marketplace layout follows the [Claude Code marketplace documentation](https://code.claude.com/docs/en/plugin-marketplaces).
