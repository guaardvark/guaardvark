#!/bin/bash

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
DESKTOP_FILE="$SCRIPT_DIR/guaardvark.desktop"
INSTALL_DIR="$HOME/.local/share/applications"
INSTALLED_FILE="$INSTALL_DIR/guaardvark.desktop"

mkdir -p "$INSTALL_DIR"

cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Guaardvark
Comment=Guaardvark AI Assistant - Launch in app mode
Exec=$SCRIPT_DIR/start.sh --app-mode
Path=$SCRIPT_DIR
Icon=$SCRIPT_DIR/1_logo.png
Terminal=false
Categories=Utility;Application;Development;
StartupNotify=true
MimeType=
Keywords=AI;Assistant;LLM;Chat;
EOF

cp "$DESKTOP_FILE" "$INSTALLED_FILE"

chmod +x "$SCRIPT_DIR/start.sh"

if command -v update-desktop-database &> /dev/null; then
    update-desktop-database "$INSTALL_DIR"
    echo "Desktop database updated"
fi

echo "Desktop launcher installed successfully!"
echo "You can now find 'Guaardvark' in your application menu."
echo ""
echo "To uninstall, run: rm $INSTALLED_FILE"
