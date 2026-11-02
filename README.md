# BIU Research Authority plugin marketplace

Private, Codex-first plugin marketplace for research administration workflows. The same skill package also supports Claude Code.

## Current contents

- `financial-report`: includes `fund-report-analysis` for ledger review and `classification-rule-learning` for device-local learning from human decisions.

## Distribute to staff through Codex

For staff who use Codex, a ChatGPT workspace admin can import this private repository once from **Admin > Plugins > Add > Import marketplace**. The native catalog is `.agents/plugins/marketplace.json`; the plugin package is `plugins/financial-report/plugin.json`. Set `financial-report` to **Installed** for the authorized workspace roles, or **Available** if staff should add it themselves. Staff do not need GitHub access. Keep transaction workbooks and the local learning database outside the plugin repository. See the [OpenAI workspace import guide](https://learn.chatgpt.com/docs/enterprise/plugin-management).

For local Codex testing by a maintainer:

```sh
codex plugin marketplace add ./
codex plugin add financial-report@biu-research-authority
```

## Claude Code compatibility

The Claude marketplace manifest remains at `.claude-plugin/marketplace.json`. Authorized maintainers with private-repository access can run:

```sh
claude plugin marketplace add yedidyakfir/biu-research-authority-claude
claude plugin install financial-report@biu-research-authority
```

Financial source workbooks and classified outputs stay in the ignored local `analysis/` directory. Learned decision history lives in a separate local database on the user's workstation. In Codex desktop, the skill uses the bundled Python and spreadsheet libraries; its bootstrap helper starts the local memory receiver. Other clients need an available Python runtime.

The [ISF category research](docs/isf-expense-categories.md) records the initial official-source headings and the checks needed before implementing the skill.

The Codex package follows the [OpenAI plugin packaging guide](https://developers.openai.com/plugins/build/plugins); the Claude layout follows the [Claude Code marketplace documentation](https://code.claude.com/docs/en/plugin-marketplaces).
