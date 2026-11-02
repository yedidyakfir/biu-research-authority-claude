# Device-local review sync

The review page keeps its working draft in that browser. Explicit confirmation of a category or “not an expense” is also queued for a token-protected receiver on the machine running the skill. The receiver writes a SQLite file on that machine; it does not send transactions to a cloud service. If the receiver is unavailable, the page says the decision is saved in the browser only and retries its queue on reload or the next decision. The JSON download is a portable fallback.

On the BIU Windows workstation, set `FUND_REPORT_MEMORY_DIR=D:\work\local-data\financial-report` or pass that directory explicitly. Keep `memory.sqlite3` and `token` there, outside the repository. For a different host, choose its private local data directory.

At the start of the report workflow:

1. Initialize and prune `memory.sqlite3` with `memory_store.py`.
2. Start `memory_server.py --db MEMORY_DIR/memory.sqlite3 --token-file MEMORY_DIR/token --port 8898` bound to `127.0.0.1`. Keep it running while reviewers use the page. On Windows, use a hidden background process. Check `http://127.0.0.1:8898/health` before starting a second process.
3. If this workstation publishes pages through Tailscale Serve, configure the tailnet-only path once with `tailscale serve --bg --set-path=/fund-report-memory 8898`. Keep the existing `/` and other serve routes intact. Do not enable Funnel or public exposure.
4. Render the HTML with `--memory-endpoint /fund-report-memory/decisions --memory-token-file MEMORY_DIR/token`. Publish it through the host's usual secure file-sharing workflow. The HTML contains a per-device bearer token, so share the link only with authorized reviewers and keep the published copy temporary.

When the receiver cannot be used, render without the memory options and tell the reviewer that decisions live only in the browser until they export JSON. On the skill machine, run `memory_store.py --db DB ingest-reviewed-report EXPORTED.json` to bring back explicit user decisions. Never import unreviewed classifications as human examples.
