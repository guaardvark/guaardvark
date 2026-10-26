#!/bin/sh
# Start the Guaardvark MCP server from an installed checkout.
#
# Used by the agent plugin manifests (Claude Code, Grok Build, Antigravity),
# where the plugin's own files may be a bare clone without a venv. The
# checkout is the first of these that carries backend/venv:
#   1. the first argument (the plugin's "Guaardvark checkout" setting)
#   2. $GUAARDVARK_ROOT
#   3. this script's own repository (a plugin loaded from a real checkout)
#   4. the repository holding .agents/plugins/<name>/ when run from there
#   5. ~/.config/guaardvark/root, written by `python -m backend.mcp install`
#   6. the checkout behind the `guaardvark` command on PATH
#   7. ~/guaardvark, where the README's quick start clones it
# Clients that do not expand the argument pass it through literally; it then
# names no checkout and the next candidate is tried.
set -eu

here="$(cd "$(dirname "$0")/.." && pwd)"

saved_root=""
if [ -f "$HOME/.config/guaardvark/root" ]; then
  saved_root="$(cat "$HOME/.config/guaardvark/root" 2>/dev/null || true)"
fi

# A venv console script lives at <checkout>/backend/venv/bin/guaardvark.
cli_root=""
if cli="$(command -v guaardvark 2>/dev/null)"; then
  cli_target="$(readlink -f "$cli" 2>/dev/null || true)"
  [ -n "$cli_target" ] && cli_root="$(dirname "$(dirname "$(dirname "$(dirname "$cli_target")")")")"
fi

root=""
for candidate in \
  "${1:-}" \
  "${GUAARDVARK_ROOT:-}" \
  "$here" \
  "$here/../../.." \
  "$saved_root" \
  "$cli_root" \
  "$HOME/guaardvark"; do
  [ -n "$candidate" ] || continue
  if [ -x "$candidate/backend/venv/bin/python" ] && [ -f "$candidate/backend/mcp/__main__.py" ]; then
    root="$candidate"; break
  fi
done

if [ -z "$root" ]; then
  echo "guaardvark-mcp: no installed Guaardvark checkout found." >&2
  echo "  Run 'python -m backend.mcp install' once from your checkout," >&2
  echo "  or export GUAARDVARK_ROOT=/path/to/guaardvark, then reload plugins." >&2
  echo "  The checkout needs backend/venv (run ./start.sh once)." >&2
  exit 1
fi

cd "$root"
exec backend/venv/bin/python -m backend.mcp
