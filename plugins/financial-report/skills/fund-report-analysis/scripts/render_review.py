#!/usr/bin/env python3
"""Render a self-contained, JSON-backed HTML review page."""

import argparse
import json
from pathlib import Path
from validate_report import validate


SKILL_ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("json_file", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--memory-endpoint", help="Same-origin path to the device-local decision receiver")
    parser.add_argument("--memory-token-file", type=Path, help="Local bearer-token file for that receiver")
    args = parser.parse_args()
    if bool(args.memory_endpoint) != bool(args.memory_token_file):
        parser.error("--memory-endpoint and --memory-token-file must be used together")
    if args.memory_endpoint and (not args.memory_endpoint.startswith("/") or args.memory_endpoint.startswith("//")):
        parser.error("memory endpoint must be a same-origin path")
    report = json.loads(args.json_file.read_text(encoding="utf-8"))
    errors, counts = validate(report)
    if errors:
        parser.error("review JSON failed validation: " + "; ".join(errors[:5]))
    assets = SKILL_ROOT / "assets"
    html = (assets / "review_template.html").read_text(encoding="utf-8")
    css = (assets / "review.css").read_text(encoding="utf-8")
    export_js = (assets / "xlsx_export.js").read_text(encoding="utf-8")
    app_js = (assets / "review.js").read_text(encoding="utf-8")
    safe_json = json.dumps(report, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace("&", "\\u0026")
    memory_config = {"endpoint": args.memory_endpoint, "token": args.memory_token_file.read_text(encoding="ascii").strip()} if args.memory_endpoint else {}
    safe_memory = json.dumps(memory_config, separators=(",", ":")).replace("<", "\\u003c").replace("&", "\\u0026")
    html = html.replace("/*__STYLE__*/", css).replace("/*__XLSX_EXPORT__*/", export_js).replace("/*__APP__*/", app_js).replace("__REPORT_JSON__", safe_json).replace("__MEMORY_CONFIG__", safe_memory)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html, encoding="utf-8")
    print(json.dumps({"html": str(args.output), "bytes": args.output.stat().st_size, "counts": counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
