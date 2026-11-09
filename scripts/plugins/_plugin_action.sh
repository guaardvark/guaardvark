#!/bin/bash
# Shared body of start_plugin.sh and stop_plugin.sh: ACTION=start|stop.
# With the backend running, the request goes through its plugin manager
# (POST /api/plugins/<name>/<action>) so the GPU orchestrator knows what is
# resident. With the backend down, the plugin's own scripts/<action>.sh runs.
# --direct always runs the plugin's script.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NAME="${1:-}"
DIRECT="${2:-}"

if [ -z "$NAME" ] || [ "$NAME" = "-h" ] || [ "$NAME" = "--help" ]; then
    echo "Usage: ${PROG:-$(basename "$0")} <plugin> [--direct]"
    echo "Plugins: $(cd "$ROOT/plugins" && ls -d */ 2>/dev/null | tr -d / | while read -r d; do [ -f "$d/plugin.json" ] && printf '%s ' "$d"; done)"
    exit 2
fi
case "$NAME" in
    */*|.*) echo "Not a plugin name: $NAME"; exit 2 ;;
esac

PLUGIN_DIR="$ROOT/plugins/$NAME"
[ -f "$PLUGIN_DIR/plugin.json" ] || { echo "No plugin named '$NAME' in $ROOT/plugins"; exit 1; }

if [ -f "$ROOT/.env" ] && [ -z "${FLASK_PORT:-}" ]; then
    FLASK_PORT="$(grep -E '^FLASK_PORT=' "$ROOT/.env" | tail -1 | cut -d= -f2 | tr -d '"' || true)"
fi
BACKEND="http://127.0.0.1:${FLASK_PORT:-5000}"

if [ "$DIRECT" != "--direct" ] && curl -sf -m 3 "$BACKEND/api/health" >/dev/null 2>&1; then
    echo "Asking the backend at $BACKEND to $ACTION $NAME..."
    BODY="$(curl -s -m 120 -X POST "$BACKEND/api/plugins/$NAME/$ACTION")"
    echo "$BODY" | python3 -c '
import json, sys
d = json.load(sys.stdin)
data = d.get("data") or {}
ok = d.get("success") and data.get("success", True) is not False
print(d.get("message") or d.get("error") or data.get("error") or "no message")
sys.exit(0 if ok else 1)'
    exit $?
fi

SCRIPT="$PLUGIN_DIR/scripts/$ACTION.sh"
[ -x "$SCRIPT" ] || [ -f "$SCRIPT" ] || { echo "$NAME has no scripts/$ACTION.sh; use the Plugins page"; exit 1; }
echo "Backend not reachable at $BACKEND (or --direct); running $NAME's scripts/$ACTION.sh"
mkdir -p "$ROOT/logs" "$ROOT/pids"
exec bash "$SCRIPT"
