#!/usr/bin/env python3
"""Initialize local memory, prune old decisions, and start its receiver if needed."""

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import urlopen

from memory_server import load_token
from memory_store import DEFAULT_DB, connect, prune


def healthy(port):
    try:
        with urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
            return response.status == 200 and json.load(response).get("ready") is True
    except (OSError, ValueError):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory-dir", type=Path,
                        default=Path(os.environ.get("FUND_REPORT_MEMORY_DIR", DEFAULT_DB.parent)))
    parser.add_argument("--port", type=int, default=8898)
    parser.add_argument("--tailnet", action="store_true", help="Configure the existing Tailscale Serve path")
    args = parser.parse_args()
    folder = args.memory_dir.expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    db_path = folder / "memory.sqlite3"
    token_path = folder / "token"
    with closing(connect(db_path)) as db:
        removed = prune(db)
    load_token(token_path)
    started = False
    if not healthy(args.port):
        log_path = folder / "server-error.log"
        with log_path.open("ab") as log:
            subprocess.Popen([sys.executable, str(Path(__file__).with_name("memory_server.py")),
                              "--db", str(db_path), "--token-file", str(token_path),
                              "--port", str(args.port)],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                             start_new_session=os.name != "nt")
        for _ in range(40):
            if healthy(args.port):
                started = True
                break
            time.sleep(0.25)
        if not healthy(args.port):
            parser.error(f"receiver did not start; check {log_path}")
    if args.tailnet:
        subprocess.run(["tailscale", "serve", "--bg", "--set-path=/fund-report-memory", str(args.port)],
                       check=True, capture_output=True, text=True)
    endpoint = "/fund-report-memory/decisions" if args.tailnet else f"http://127.0.0.1:{args.port}/decisions"
    print(json.dumps({"db": str(db_path), "token_file": str(token_path), "endpoint": endpoint,
                      "receiver_started": started, "old_decisions_deleted": removed["deleted"]}, indent=2))


if __name__ == "__main__":
    main()
