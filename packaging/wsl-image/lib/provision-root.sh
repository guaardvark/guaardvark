#!/usr/bin/env bash
# Runs as root inside the build container, before Guaardvark is installed.
# Creates the default user and the two WSL configuration files.
set -euo pipefail

USER_NAME=guaardvark
USER_UID=1000

# Same groups Ubuntu's own first-run script (/usr/lib/wsl/wsl-setup) gives the user it creates.
if ! id "$USER_NAME" >/dev/null 2>&1; then
    adduser --quiet --disabled-password --gecos '' --uid "$USER_UID" "$USER_NAME"
fi
usermod -aG adm,cdrom,sudo,dip,plugdev "$USER_NAME"

# The account has no password (nothing to type, no hash in /etc/shadow), so sudo
# must not ask for one: start.sh needs root on first start to set the database
# password, install its sudoers and Ollama drop-ins, and for any later package fix,
# and the desktop app runs it without a terminal. `wsl -u root` already gives the
# Windows user root in this distribution, so this grants nothing new.
cat > /etc/sudoers.d/90-guaardvark <<EOF
$USER_NAME ALL=(ALL) NOPASSWD:ALL
EOF
chmod 0440 /etc/sudoers.d/90-guaardvark
visudo -cf /etc/sudoers.d/90-guaardvark >/dev/null

cat > /etc/wsl.conf <<EOF
[boot]
systemd=true

[user]
default=$USER_NAME
EOF

# No OOBE command: the user already exists, and Ubuntu's wsl-setup would prompt
# (Ubuntu Insights consent) and copy a font into Windows. The desktop app makes the
# Start-menu entry, so WSL's own shortcut and terminal profile stay off.
{
    echo "[oobe]"
    echo "defaultUid = $USER_UID"
    echo "defaultName = Guaardvark"
    echo ""
    echo "[shortcut]"
    echo "enabled = false"
    if [ -f /usr/lib/guaardvark-image/guaardvark.ico ]; then
        install -D -m 0644 /usr/lib/guaardvark-image/guaardvark.ico /usr/share/wsl/guaardvark.ico
        echo "icon = /usr/share/wsl/guaardvark.ico"
    fi
    echo ""
    echo "[windowsterminal]"
    echo "enabled = false"
} > /etc/wsl-distribution.conf
chmod 0644 /etc/wsl.conf /etc/wsl-distribution.conf

# Package indexes as of the build, as a fresh `wsl --install` has them.
apt-get update -qq
echo "provisioned: $(id "$USER_NAME")"
cat /etc/wsl.conf /etc/wsl-distribution.conf
