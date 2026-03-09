"""
Standalone HTTP server that serves the reboot log file during system restart.

Launched by reboot_api.py before start.sh runs. Survives Flask shutdown because:
  1. It runs with cwd=/tmp (stop.sh checks process CWD against project root)
  2. Its command doesn't match stop.sh's kill patterns (python.*backend[./]app)
  3. It runs in its own process group (os.setsid)

Auto-terminates after a configurable timeout (default 5 minutes).
"""

import argparse
import json
import os
import re
import sys
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

ANSI_RE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07')


class RebootLogHandler(BaseHTTPRequestHandler):
    log_file_path = ""
    server_start_time = 0.0
    max_lifetime = 300

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/log":
            self._handle_log()
        elif path == "/shutdown":
            self._json({"ok": True})
            import threading
            threading.Timer(0.3, self.server.shutdown).start()
        else:
            self.send_error(404)

    # ---- handlers ----

    def _handle_log(self):
        params = parse_qs(urlparse(self.path).query)
        offset = int(params.get("offset", ["0"])[0])

        if not os.path.isfile(self.log_file_path):
            return self._json({"success": True, "content_lines": [], "offset": 0, "size": 0})

        try:
            file_size = os.path.getsize(self.log_file_path)

            # File rewritten (start.sh may truncate) — reset offset
            if offset > file_size:
                offset = 0

            with open(self.log_file_path, "r", encoding="utf-8", errors="replace") as f:
                if offset > 0:
                    f.seek(offset)
                content = f.read()

            # Strip ANSI escape codes for clean terminal display
            content = ANSI_RE.sub("", content)
            lines = [ln for ln in content.split("\n") if ln.strip()]

            self._json({
                "success": True,
                "content_lines": lines,
                "offset": file_size,
                "size": file_size,
            })
        except Exception as exc:
            self._json({"success": False, "content_lines": [], "error": str(exc), "offset": 0, "size": 0})

    # ---- helpers ----

    def _json(self, data, code=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")

    def log_message(self, fmt, *args):
        pass  # suppress access logs


def main():
    parser = argparse.ArgumentParser(description="Reboot log server")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--log-file", type=str, required=True)
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    RebootLogHandler.log_file_path = os.path.abspath(args.log_file)
    RebootLogHandler.server_start_time = time.time()
    RebootLogHandler.max_lifetime = args.timeout

    try:
        server = HTTPServer(("0.0.0.0", args.port), RebootLogHandler)
    except OSError as exc:
        print(f"Cannot bind port {args.port}: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)

    server.timeout = 1  # wake every second to check lifetime

    print(f"Log server on :{args.port}  file={args.log_file}  timeout={args.timeout}s", flush=True)

    try:
        while time.time() - RebootLogHandler.server_start_time < args.timeout:
            server.handle_request()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("Log server stopped", flush=True)


if __name__ == "__main__":
    main()
