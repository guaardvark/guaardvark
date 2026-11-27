#!/usr/bin/env bash
# Adds Guaardvark to the applications menu for this user, pointing at the
# desktop app in this checkout. `--uninstall` removes it again.
#
#   desktop/linux/install-shortcut.sh
#   desktop/linux/install-shortcut.sh --uninstall
set -euo pipefail

HERE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
APP_DIR="$(dirname "$HERE")"
LAUNCHER="$HERE/guaardvark-desktop.sh"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
ENTRY="$DATA_HOME/applications/guaardvark.desktop"
ICON_BASE="$DATA_HOME/icons/hicolor"
ICON_NAME="guaardvark"

refresh() {
  if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$DATA_HOME/applications" >/dev/null 2>&1 || true
  fi
  if command -v gtk-update-icon-cache >/dev/null 2>&1 && [ -f "$ICON_BASE/index.theme" ]; then
    gtk-update-icon-cache -q "$ICON_BASE" >/dev/null 2>&1 || true
  fi
}

remove_shortcut() {
  rm -f "$ENTRY" "$ICON_BASE/512x512/apps/$ICON_NAME.png" "$ICON_BASE/256x256/apps/$ICON_NAME.png"
  refresh
  echo "Removed the Guaardvark shortcut ($ENTRY)."
}

add_shortcut() {
  if [ ! -x "$APP_DIR/node_modules/electron/dist/electron" ]; then
    echo "The desktop app's packages are not installed yet. Run this first:" >&2
    echo "  cd \"$APP_DIR\" && npm install && npx install-electron" >&2
    exit 1
  fi

  mkdir -p "$DATA_HOME/applications"
  local sizes=0
  if [ -f "$APP_DIR/build/icon.png" ]; then
    install -Dm644 "$APP_DIR/build/icon.png" "$ICON_BASE/512x512/apps/$ICON_NAME.png"
    sizes=$((sizes + 1))
  fi
  if [ -f "$APP_DIR/assets/icon-256.png" ]; then
    install -Dm644 "$APP_DIR/assets/icon-256.png" "$ICON_BASE/256x256/apps/$ICON_NAME.png"
    sizes=$((sizes + 1))
  fi
  [ "$sizes" -gt 0 ] || echo "No icon found in $APP_DIR; the shortcut gets the default one." >&2

  # Exec is a quoted string in the desktop entry format: these four characters
  # need a backslash inside it, and a literal percent sign is doubled.
  local exec_path="$LAUNCHER"
  exec_path="${exec_path//\\/\\\\}"
  exec_path="${exec_path//\"/\\\"}"
  exec_path="${exec_path//\`/\\\`}"
  exec_path="${exec_path//\$/\\\$}"
  exec_path="${exec_path//%/%%}"

  cat > "$ENTRY" <<ENTRY_EOF
[Desktop Entry]
Type=Application
Name=Guaardvark
Comment=Start Guaardvark and open it in its own window
Exec="$exec_path"
Icon=$ICON_NAME
Terminal=false
Categories=Graphics;
StartupNotify=true
StartupWMClass=guaardvark
ENTRY_EOF
  chmod 644 "$ENTRY"
  refresh
  echo "Added Guaardvark to the applications menu ($ENTRY)."
  echo "It starts the app in $APP_DIR."
}

case "${1:-}" in
  "") add_shortcut ;;
  --uninstall) remove_shortcut ;;
  -h|--help) sed -n '2,6p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' ;;
  *) echo "Unknown option: $1 (use --uninstall or no option)" >&2; exit 2 ;;
esac
