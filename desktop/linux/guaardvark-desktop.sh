#!/usr/bin/env bash
# Starts the Guaardvark desktop app from this checkout. The menu shortcut
# written by install-shortcut.sh runs this.
set -euo pipefail

APP_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
ELECTRON="$APP_DIR/node_modules/electron/dist/electron"

if [ ! -x "$ELECTRON" ]; then
  echo "The desktop app's packages are not installed yet. Run:" >&2
  echo "  cd \"$APP_DIR\" && npm install && npx install-electron" >&2
  exit 1
fi

# Chromium's sandbox needs either unprivileged user namespaces or a setuid-root
# chrome-sandbox helper. Ubuntu 24.04 and later restrict the first, and an
# Electron unpacked by npm does not have the second, so there the app can only
# start with the sandbox off. Where either one is available it stays on.
sandbox_available() {
  local helper="$APP_DIR/node_modules/electron/dist/chrome-sandbox"
  if [ -u "$helper" ] && [ "$(stat -c %u "$helper")" = "0" ]; then return 0; fi
  local restricted=/proc/sys/kernel/apparmor_restrict_unprivileged_userns
  local clone=/proc/sys/kernel/unprivileged_userns_clone
  if [ -r "$restricted" ] && [ "$(cat "$restricted")" = "1" ]; then return 1; fi
  if [ -r "$clone" ] && [ "$(cat "$clone")" = "0" ]; then return 1; fi
  return 0
}

flags=()
sandbox_available || flags+=(--no-sandbox)

exec "$ELECTRON" "$APP_DIR" "${flags[@]}" "$@"
