#!/bin/sh
# Start the Guaardvark MCP server from an installed checkout.
#
# Used by the Claude Code plugin manifest (.claude-plugin/plugin.json), where
# the plugin's own files may be a bare clone without a venv. The checkout is
# resolved, in order, from: the first argument (the plugin's "Guaardvark
# checkout" setting), $GUAARDVARK_ROOT, and this script's own repository when
# it carries a backend venv (a --plugin-dir load of a real checkout).
set -eu
here="$(cd "$(dirname "$0")/.." && pwd)"
root=""
for candidate in "${1:-}" "${GUAARDVARK_ROOT:-}" "$here"; do
  [ -n "$candidate" ] || continue
  if [ -x "$candidate/backend/venv/bin/python" ] && [ -f "$candidate/backend/mcp/__main__.py" ]; then
    root="$candidate"; break
  fi
done
if [ -z "$root" ]; then
  echo "guaardvark-mcp: no installed Guaardvark checkout found." >&2
  echo "  Set the plugin's 'Guaardvark checkout' to the folder that holds start.sh," >&2
  echo "  or export GUAARDVARK_ROOT=/path/to/guaardvark, then reload plugins." >&2
  echo "  The checkout needs backend/venv (run ./start.sh once)." >&2
  exit 1
fi
cd "$root"
exec backend/venv/bin/python -m backend.mcp
