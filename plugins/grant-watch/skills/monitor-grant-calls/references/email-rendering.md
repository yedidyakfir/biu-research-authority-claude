# Deterministic administrator digest

Use `scripts/pilot_contract.py` → `prepare_email` with a structured report, not model-written HTML. The renderer reads reusable `assets/email/*.html` itself; do not load these templates, a previous email or raw HTML into routine model context. Reuse stored text fields; if synthesis is necessary, produce only compact factual text/caveats, never restyle the report. The Hebrew RTL layout is fixed; English source terms retain their direction. No additional formatting model call is needed.

Report shape: `{"state":"partial","summary":["Concise actual coverage and material unknowns"],"main":[],"unverified_lead":[],"non_eligible":[]}`. Use the existing `eligibility_contract.digest_section` result as the section key. Keep unknown BIU terms in verified rows as explicit caveats. Do not promote forecasts, conflicting dates, failed retrievals or unresolved cycles to verified dates. Confirmed exclusions require the existing current-cycle evidence policy.

Reuse each observation's `name` (or `call_id`), `official_source`, `summary`/`reason`, `caveats` (text or list), and `deadlines`. A deadline has plain-text `stage`, ISO `date` when known, `time`, `timezone`, `certainty`, and optional `source_text`. Preserve original cutoff wording and missing-time caveats. Only explicit `certainty: "verified"` dates in `main` contribute to the nearest-deadline banner; recurring or forecast dates never gain a year automatically. Optional `deadline_text` preserves an already composed display line; retain structured dates for the banner. Optional `source_urls` preserves additional official links. Never insert HTML. Audit fields are ignored, but keep detailed logs out of summary/caveats too.

`state` is required: complete, partial, failed or blocked. `summary` is concise factual coverage, not the detailed audit. Empty/incomplete reports are distinguished. Rendering empty input for validation does not authorize sending a fabricated empty report.

After the existing Notion save/readback, sender verification and same-run Sent reconciliation, prepare locally with the existing helper (paths below are private temporary files, not Git or durable state):

```powershell
python plugins/grant-watch/scripts/pilot_contract.py <config.json> --workspace <verified-workspace-id> --report <report.json> --report-day <YYYY-MM-DD> --connected-sender <verified-sender> --delivery-state ready --output <new-prepared.json>
```

This performs no network or delivery operations. The output contains the exact existing subject, configured approved To list, HTML MIME payload and no CC/BCC; it never sends or drafts. Read the JSON as data, without model rewriting. Persist/read back the actual digest and intent in Notion, then follow the existing authorized connector-send and receipt protocol. Pending/uncertain/sent states still block preparation. Never set ready merely to regenerate a previously delivered message; isolated offline fixtures may use synthetic ready state without writing to the live run. Do not commit private config, reports, reference mail, payloads or recipients. The old escaped plain-string path remains for legacy callers only; new scheduled reports use structured data.
