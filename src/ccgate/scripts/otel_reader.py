"""otel_reader.py — OTLP metric collector for claude_code.token.usage."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from ccgate.config import load_config
from ccgate.state import ccgate_home

_KNOWN_TYPES = frozenset({"input", "output", "cache_read", "cache_creation"})

# Claude Code emits the `type` attribute in camelCase (cacheRead / cacheCreation).
# Accept both spellings: an unrecognised label silently bucketing to 0 would be a
# wrong number with no error, which §11 "field drift" forbids.
_TYPE_ALIASES = {"cacheRead": "cache_read", "cacheCreation": "cache_creation"}


def _sanitize_session_id(session_id: str) -> str:
    """Return safe session_id for filename use; fall back to 'unknown' if suspicious."""
    if not session_id:
        return "unknown"
    # Reject traversal and separator characters
    if (
        ".." in session_id
        or "/" in session_id
        or "\\" in session_id
        or os.path.isabs(session_id)
    ):
        return "unknown"
    return session_id


def parse_otlp_payload(payload: dict) -> dict:
    """Extract session_id and token counts from an OTLP JSON payload."""
    session_id = "unknown"
    tokens: dict[str, int] = {
        "input": 0, "output": 0, "cache_read": 0, "cache_creation": 0
    }

    for rm in payload.get("resourceMetrics", []):
        # Extract session.id from resource attributes
        for attr in (rm.get("resource") or {}).get("attributes", []):
            if attr.get("key") == "session.id":
                val = (attr.get("value") or {}).get("stringValue")
                if val:
                    session_id = val

        for sm in rm.get("scopeMetrics", []):
            for metric in sm.get("metrics", []):
                if metric.get("name") != "claude_code.token.usage":
                    continue
                for dp in (metric.get("sum") or {}).get("dataPoints", []):
                    type_label = "unknown"
                    for attr in dp.get("attributes", []):
                        if attr.get("key") == "type":
                            raw_label = (attr.get("value") or {}).get("stringValue", "unknown")
                            norm = _TYPE_ALIASES.get(raw_label, raw_label)
                            type_label = norm if norm in _KNOWN_TYPES else "unknown"
                    # Prefer asInt; fall back to asDouble
                    if "asInt" in dp:
                        value = int(dp["asInt"])
                    elif "asDouble" in dp:
                        value = int(dp["asDouble"])
                    else:
                        continue
                    tokens[type_label] = tokens.get(type_label, 0) + value

    return {"session_id": session_id, "tokens": tokens}


def _write_session_file(parsed: dict, source: str, home: Path | None = None) -> Path:
    """Write {session_id}-otel.json atomically; return the path."""
    h = home or ccgate_home()
    sessions_dir = h / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    safe_id = _sanitize_session_id(parsed["session_id"])
    out_path = sessions_dir / f"{safe_id}-otel.json"
    data = {
        "session_id": parsed["session_id"],
        "collected_at": datetime.now(UTC).isoformat(),
        "source": source,
        "tokens": parsed["tokens"],
    }
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(out_path))
    return out_path


def _make_handler(home: Path | None = None):
    class OTLPHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != "/v1/metrics":
                self.send_response(404)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"error":"invalid json"}')
                return
            parsed = parse_otlp_payload(payload)
            _write_session_file(parsed, "otlp_http", home)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

        def do_GET(self):
            self.send_response(404)
            self.end_headers()

        def log_message(self, fmt, *args):
            pass  # suppress default request logging

    return OTLPHandler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ccgate OTLP metric collector")
    sub = parser.add_subparsers(dest="command", required=True)

    serve_p = sub.add_parser("serve", help="Start OTLP HTTP server")
    serve_p.add_argument("--port", type=int, default=None,
                         help="Port to listen on (default: otelPort from config)")

    read_p = sub.add_parser("read", help="Parse a pre-exported OTLP JSON file")
    read_p.add_argument("--file", required=True, help="Path to OTLP JSON file")

    args = parser.parse_args(argv)

    if args.command == "read":
        path = Path(args.file)
        if not path.exists():
            print(f"otel_reader: file not found: {path}", file=sys.stderr)
            return 1
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"otel_reader: {e}", file=sys.stderr)
            return 1
        parsed = parse_otlp_payload(payload)
        out = _write_session_file(parsed, "otlp_file")
        print(f"written: {out}")
        return 0

    # serve mode
    cfg = load_config()
    port = args.port if args.port is not None else cfg["otelPort"]
    handler = _make_handler()
    # Local-only receiver: OTLP metrics come from the Claude Code session on this
    # machine. Bind to loopback, not all interfaces, to avoid exposing the port.
    server = HTTPServer(("127.0.0.1", port), handler)
    print(f"otel_reader: listening on 127.0.0.1:{port} for POST /v1/metrics")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
