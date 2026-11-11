# BIU Research Authority plugin marketplace

Public, Codex-first plugin marketplace for research administration workflows. The same skill package also supports Claude Code. Financial workbooks, classified outputs, and reviewer memory stay on each user's device; they are not stored in this repository.

## Current contents

- `financial-report`: includes `fund-report-analysis` for ledger review and `classification-rule-learning` for device-local learning from human decisions.
- `grant-watch`: includes `monitor-grant-calls` for source-backed grant-call monitoring and `set-up-grant-watch` to prepare its private state, trial run, and daily scheduled task. A scheduler, durable state, and delivery channel must be configured for unattended operation.

## Distribute to staff through Codex

For staff who use Codex, a ChatGPT workspace admin can import this repository once from **Admin > Plugins > Add > Import marketplace**. The native catalog is `.agents/plugins/marketplace.json`; plugin packages live under `plugins/`. Set a plugin to **Installed** for authorized workspace roles, or **Available** if staff should add it themselves. Staff do not need GitHub access. Keep financial workbooks, grant-monitoring state, credentials, and local learning databases outside the plugin repository. See the [OpenAI workspace import guide](https://learn.chatgpt.com/docs/enterprise/plugin-management).

For an individual Codex desktop installation from GitHub:

```sh
codex plugin marketplace add yedidyakfir/biu-research-authority-claude --ref main
codex plugin list --marketplace biu-research-authority --available
codex plugin add financial-report@biu-research-authority
codex plugin add grant-watch@biu-research-authority
```

Restart the ChatGPT desktop app after adding the marketplace. Its display name is **BIU Research Authority**. **Financial Report** contains `fund-report-analysis` and `classification-rule-learning`; **Grant Watch** contains `monitor-grant-calls` and `set-up-grant-watch`. A GitHub marketplace is separate from the universal public plugin directory.

For local Codex testing by a maintainer:

```sh
codex plugin marketplace add ./
codex plugin add financial-report@biu-research-authority
codex plugin add grant-watch@biu-research-authority
```

## Claude Code compatibility

The Claude marketplace manifest remains at `.claude-plugin/marketplace.json`. Claude Code users can run:

```sh
claude plugin marketplace add yedidyakfir/biu-research-authority-claude
claude plugin install financial-report@biu-research-authority
claude plugin install grant-watch@biu-research-authority
```

Financial source workbooks and classified outputs stay in the ignored local `analysis/` directory. Learned decision history lives in a separate local database on the user's workstation. In Codex desktop, the skill uses the bundled Python and spreadsheet libraries; its bootstrap helper starts the local memory receiver. Other clients need an available Python runtime.

The [ISF category research](docs/isf-expense-categories.md) records the initial official-source headings and the checks needed before implementing the skill.

The Codex package follows the [OpenAI plugin packaging guide](https://developers.openai.com/plugins/build/plugins); the Claude layout follows the [Claude Code marketplace documentation](https://code.claude.com/docs/en/plugin-marketplaces).

## Releasing plugin updates

Increase the changed plugin's `plugins/<name>/plugin.json` version when releasing new bundled skills. For Claude Code marketplace users, also update that plugin entry's `version` in `.claude-plugin/marketplace.json` and the catalog's top-level version for a catalog release. Claude Code uses the plugin version to decide whether a cached installation needs new files; the marketplace version alone does not trigger that update. Users receive the release through marketplace auto-update when enabled or by updating the plugin manually. Codex users refresh their marketplace source and restart the desktop app to load new local plugin files.
