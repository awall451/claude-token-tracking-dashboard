#!/usr/bin/env python3
"""
All-in-one HTTP server for claude-token-tracker.

Serves frontend/ static files, stats.json, and refreshes stats on a
background schedule.

Env vars:
  PORT              HTTP port (default: 9420)
  CLAUDE_DIR        Path to ~/.claude data (default: ~/.claude)
  REFRESH_INTERVAL  Seconds between auto-refreshes (default: 300)
  STATS_PATH        Where to write stats.json (default: /data/stats.json)
"""

import json
import os
import pathlib
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

# Ensure parser/ is importable
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "parser"))
import ledger as ledger_mod
import parse as parser_mod

PORT = int(os.environ.get("PORT", 9420))
REFRESH_INTERVAL = int(os.environ.get("REFRESH_INTERVAL", 300))
STATS_PATH = pathlib.Path(os.environ.get("STATS_PATH", "/data/stats.json"))
FRONTEND_DIR = pathlib.Path(__file__).parent.parent / "frontend"
# Extra transcript roots (e.g. an archive copy of ~/.claude/projects), comma-separated
ARCHIVE_DIRS = [pathlib.Path(p) for p in os.environ.get("ARCHIVE_DIRS", "").split(",") if p]

_stats_lock = threading.Lock()
_last_refreshed: float = 0.0
# Per-file parse results keyed by mtime/size; unchanged transcripts are not re-read.
_parse_cache: dict = {}
_ledger = None


def refresh_stats() -> bool:
    global _last_refreshed
    try:
        global _ledger
        if _ledger is None:
            _ledger = ledger_mod.connect(ledger_mod.LEDGER_PATH)
        roots = [parser_mod.PROJECTS_DIR, *ARCHIVE_DIRS]
        ledger_mod.ingest(_ledger, parser_mod.parse_all(projects_dir=roots, cache=_parse_cache))
        sessions = ledger_mod.load_sessions(_ledger)   # durable history, not just what's on disk
        output = parser_mod.build_output(sessions)
        STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(output))
        tmp.replace(STATS_PATH)
        _last_refreshed = time.time()
        print(f"[refresh] {len(sessions)} sessions → {STATS_PATH}", flush=True)
        return True
    except Exception as e:
        print(f"[refresh] ERROR: {e}", flush=True)
        return False


def background_refresh():
    while True:
        time.sleep(REFRESH_INTERVAL)
        with _stats_lock:
            refresh_stats()


MIME = {
    ".html": "text/html; charset=utf-8",
    ".js":   "application/javascript",
    ".css":  "text/css",
    ".json": "application/json",
    ".png":  "image/png",
    ".ico":  "image/x-icon",
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[http] {self.address_string()} {fmt % args}", flush=True)

    def send_body(self, code: int, content_type: str, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/") or "/"

        # /stats.json → serve generated stats
        if path == "/stats.json":
            if not STATS_PATH.exists():
                with _stats_lock:
                    refresh_stats()
            if STATS_PATH.exists():
                self.send_body(200, "application/json", STATS_PATH.read_bytes())
            else:
                self.send_body(503, "text/plain", b"stats not available yet")
            return

        # /api/status
        if path == "/api/status":
            body = json.dumps({
                "last_refreshed": _last_refreshed,
                "refresh_interval": REFRESH_INTERVAL,
                "stats_exists": STATS_PATH.exists(),
            }).encode()
            self.send_body(200, "application/json", body)
            return

        # Frontend static files
        if path == "/" or path == "/frontend":
            file_path = FRONTEND_DIR / "index.html"
        else:
            # strip /frontend prefix if present
            rel = path.removeprefix("/frontend").lstrip("/") or "index.html"
            file_path = FRONTEND_DIR / rel

        # Reject traversal (e.g. /../../data/.claude/history.jsonl)
        file_path = file_path.resolve()
        if not file_path.is_relative_to(FRONTEND_DIR.resolve()):
            self.send_body(404, "text/plain", b"not found")
            return

        if file_path.exists() and file_path.is_file():
            suffix = file_path.suffix.lower()
            ct = MIME.get(suffix, "application/octet-stream")
            self.send_body(200, ct, file_path.read_bytes())
        else:
            self.send_body(404, "text/plain", b"not found")

    def do_POST(self):
        path = urlparse(self.path).path

        if path == "/api/refresh":
            with _stats_lock:
                ok = refresh_stats()
            code = 200 if ok else 500
            self.send_body(code, "application/json",
                           json.dumps({"ok": ok}).encode())
        else:
            self.send_body(404, "text/plain", b"not found")


def main():
    # Initial parse on startup
    print(f"[startup] CLAUDE_DIR={os.environ.get('CLAUDE_DIR', '~/.claude')}", flush=True)
    print(f"[startup] refresh every {REFRESH_INTERVAL}s → {STATS_PATH}", flush=True)
    with _stats_lock:
        refresh_stats()

    t = threading.Thread(target=background_refresh, daemon=True)
    t.start()

    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[startup] listening on :{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
