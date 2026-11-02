#!/usr/bin/env python3
"""Token-protected localhost receiver for explicit browser review decisions."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
from pathlib import Path
import secrets

from memory_store import connect, ingest_decision


def load_token(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with path.open("x", encoding="ascii") as output:
            output.write(secrets.token_urlsafe(48))
    return path.read_text(encoding="ascii").strip()


def handler_for(db_path, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # Do not put financial text or bearer tokens in service logs.

        def reply(self, status, payload):
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.rstrip("/").endswith("/health"):
                self.reply(200, {"ready": True})
            else:
                self.reply(404, {"error": "not found"})

        def do_POST(self):
            if not self.path.rstrip("/").endswith("/decisions"):
                self.reply(404, {"error": "not found"})
                return
            supplied = self.headers.get("Authorization", "")
            if not hmac.compare_digest(supplied, f"Bearer {token}"):
                self.reply(401, {"error": "unauthorized"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 2 or length > 32768:
                    raise ValueError("invalid request size")
                item = json.loads(self.rfile.read(length))
                with connect(db_path) as db:
                    key = ingest_decision(db, item)
                self.reply(200, {"saved": True, "decision_key": key})
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                self.reply(400, {"error": str(exc)})

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8898)
    args = parser.parse_args()
    token = load_token(args.token_file)
    with connect(args.db):
        pass
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(args.db, token))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    main()
