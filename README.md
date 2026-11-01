# BIU Research Authority Claude marketplace

Private Claude Code plugin marketplace for research administration workflows.

## Current contents

- `financial-report`: plugin scaffold only. Its planned first skill is `fund-report-analysis`; no skill is installed or runnable yet.

## Add the marketplace

Authorized users with access to this private repository can run:

```sh
claude plugin marketplace add yedidyakfir/biu-research-authority-claude
claude plugin install financial-report@biu-research-authority
```

The repository contains plugin definitions. Financial source workbooks and classified outputs stay in the ignored local `analysis/` directory.

The [ISF category research](docs/isf-expense-categories.md) records the initial official-source headings and the checks needed before implementing the skill.

Marketplace layout follows the [Claude Code marketplace documentation](https://code.claude.com/docs/en/plugin-marketplaces).
