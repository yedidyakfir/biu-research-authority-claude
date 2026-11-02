# Financial Report plugin

This plugin packages `fund-report-analysis` and `classification-rule-learning` for Codex and Claude Code. The report skill reads an Excel ledger, preserves an untouched source copy, records proposed classifications in JSON, and creates a self-contained HTML review page. Reviewers can correct uncertain rows and export a new Excel workbook.

The rule skill runs at the start of report analysis. It learns narrow suggestions from explicit reviewer decisions stored in a device-local SQLite database. The review page can send those decisions to a token-protected receiver on the workstation; its [local sync setup](skills/classification-rule-learning/references/local-sync.md) describes the optional receiver. Raw decisions older than six months and examples absorbed into accepted rules are removed. Learned rules remain advisory and never establish grant eligibility.

Codex workspace admins can import the private repository and assign the plugin by workspace role; staff then use it from their Plugins directory without repository access. Claude Code maintainers can install from the private marketplace with `/plugin install financial-report --marketplace yedidyakfir/biu-research-authority-claude`.

Foundation policy lives in `skills/fund-report-analysis/references/foundations/`. The first reference covers the Israel Science Foundation personal-grant budget headings. Those headings are a provisional taxonomy until the grant track, award year, approved budget, and written exceptions are known.
