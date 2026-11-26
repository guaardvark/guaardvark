"""
Standalone HTTP server that serves the reboot log file during system restart.

Launched by reboot_api.py before start.sh runs. Survives Flask shutdown because:
  1. It runs with cwd=/tmp (stop.sh checks process CWD against project root)
  2. Its command doesn't match stop.sh's kill patterns (python.*backend[./]app)
  3. It runs in its own process group (os.setsid)

Stops after a configurable timeout (default 5 minutes), or earlier on
POST /shutdown.

It listens on 127.0.0.1 only. The reboot page is handed
http://127.0.0.1:<port>, the address that is actually bound (localhost may
resolve to ::1 and refuse the connection). A browser on another device cannot
reach it and keeps polling the backend's health instead.
A reply names the requesting page in Access-Control-Allow-Origin only when
the page is one of this install's frontend origins, which reboot_api.py passes
as --allow-origin, so no other page can read the log. POST /shutdown is
refused to a page on any other origin; the backend's own call sends no Origin.
A page whose DNS name was re-pointed at 127.0.0.1 counts as same-origin and
needs no CORS, so a request addressed to a name that is not this machine's
is refused first, by the backend's Host rule (backend/utils/host_check.py).
"""

import argparse
import json
import os
import re
import sys
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

if __package__:
    from . import host_check
else:
    # Run as a script by reboot_api.py: backend/utils is sys.path[0], and the
    # backend package (stopping as this starts) is not imported.
    import sidecar_guard

    host_check = sidecar_guard.host_check

ANSI_RE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07')


class RebootLogHandler(BaseHTTPRequestHandler):
    log_file_path = ""
    server_start_time = 0.0
    max_lifetime = 300
    allowed_origins: frozenset = frozenset()

    def _host_refused(self) -> bool:
        """Answer 421 and return True when the request is addressed to a
        name that is not this machine's."""
        host_header = host_check.single_host(self.headers.get_all("Host") or [])
        if host_check.host_allowed(host_header):
            return False
        body = host_check.refusal_body(host_header)
        self.send_response(421)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return True

    def do_OPTIONS(self):
        if self._host_refused():
            return
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self._host_refused():
            return
        path = urlparse(self.path).path
        if path == "/log":
            self._handle_log()
        elif path == "/shutdown":
            # A GET can be sent by any page (an <img> tag), so it stops nothing.
            self.send_error(405, "Use POST")
        else:
            self.send_error(404)

    def do_POST(self):
        if self._host_refused():
            return
        if urlparse(self.path).path != "/shutdown":
            self.send_error(404)
            return
        if self.headers.get("Origin") is not None and not self._origin_allowed():
            self._json({"ok": False, "error": "origin not allowed"}, 403)
            return
        self.server.stop_requested = True
        self._json({"ok": True})

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

    def _allowed_origin(self):
        """The allow-list entry matching the request's Origin, or None.

        The entry is what gets echoed back, never the request's own value.
        """
        origin = (self.headers.get("Origin") or "").strip().rstrip("/").lower()
        return next((o for o in self.allowed_origins if o == origin), None)

    def _origin_allowed(self):
        return self._allowed_origin() is not None

    def _json(self, data, code=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        self.send_header("Vary", "Origin")
        allowed = self._allowed_origin()
        if allowed is not None:
            self.send_header("Access-Control-Allow-Origin", allowed)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def log_message(self, fmt, *args):
        pass  # suppress access logs


def main():
    parser = argparse.ArgumentParser(description="Reboot log server")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--log-file", type=str, required=True)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--allow-origin", action="append", default=[],
                        help="a page origin allowed to read the log (repeatable)")
    args = parser.parse_args()

    RebootLogHandler.log_file_path = os.path.abspath(args.log_file)
    RebootLogHandler.server_start_time = time.time()
    RebootLogHandler.max_lifetime = args.timeout
    RebootLogHandler.allowed_origins = frozenset(
        o.strip().rstrip("/").lower() for o in args.allow_origin if o.strip()
    )

    try:
        server = HTTPServer(("127.0.0.1", args.port), RebootLogHandler)
    except OSError as exc:
        print(f"Cannot bind port {args.port}: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)

    server.timeout = 1  # wake every second to check lifetime and /shutdown
    server.stop_requested = False

    print(f"Log server on 127.0.0.1:{args.port}  file={args.log_file}  timeout={args.timeout}s", flush=True)

    try:
        while (not server.stop_requested
               and time.time() - RebootLogHandler.server_start_time < args.timeout):
            server.handle_request()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("Log server stopped", flush=True)


if __name__ == "__main__":
    main()
