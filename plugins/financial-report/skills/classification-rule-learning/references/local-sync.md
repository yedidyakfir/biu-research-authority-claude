# Device-local review sync

The review page keeps its working draft in that browser. Explicit confirmation of a category or “not an expense” is also queued for a token-protected receiver on the machine running the skill. The receiver writes a SQLite file on that machine; it does not send transactions to a cloud service. If the receiver is unavailable, the page says the decision is saved in the browser only and retries its queue on reload or the next decision. The JSON download is a portable fallback.

On the BIU Windows workstation, set `FUND_REPORT_MEMORY_DIR=D:\work\local-data\financial-report` or pass that directory explicitly. Keep `memory.sqlite3` and `token` there, outside the repository. For a different host, choose its private local data directory.

At the start of the report workflow, run `scripts/bootstrap.py --memory-dir MEMORY_DIR` with the available Python runtime. It initializes the database, removes decisions older than six months, creates a token, and starts the receiver on `127.0.0.1:8898` if needed. It prints the endpoint and token-file path to pass to `render_review.py`; it never prints the token.

For a Codex desktop user reviewing on the **same computer**, use the loopback endpoint `http://127.0.0.1:8898/decisions`. Serve the output folder privately on a loopback address (for example, `python -m http.server 8896 --bind 127.0.0.1 --directory OUTPUT`) and open `http://127.0.0.1:8896/review.html`. This keeps the report on the computer and allows the browser to reach the token-protected memory receiver. The agent runs these commands; the reviewer needs neither Tailscale nor GitHub access. Choose another unused local port if 8896 is occupied.

For the BIU workstation where reviewers open the page on a **phone or another computer**, run bootstrap with `--tailnet`. It configures only the tailnet-only `/fund-report-memory` path alongside existing Tailscale Serve routes and returns `/fund-report-memory/decisions`. Publish the HTML through the host's normal secure workflow. Never enable Funnel or public exposure.

The HTML contains a per-device bearer token, so share the page only with authorized reviewers and keep the published copy temporary. Keep the receiver running while reviewers use the page.

When the receiver cannot be used, render without the memory options and tell the reviewer that decisions live only in the browser until they export JSON. On the skill machine, run `memory_store.py --db DB ingest-reviewed-report EXPORTED.json` to bring back explicit user decisions. Never import unreviewed classifications as human examples.
