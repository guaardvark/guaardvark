#!/usr/bin/env bash
# WSL2 helpers. Sourced by start.sh when GUAARDVARK_IS_WSL=1.
#
# WSL shuts a distribution down about 15 seconds after its last terminal closes
# (instanceIdleTimeout) and the VM a minute later (vmIdleTimeout). The services
# start.sh leaves running do not count as a session, so closing the window takes
# the studio down. Both timeouts are settings in the Windows user's .wslconfig;
# -1 disables them. Nothing here runs unless the user asks for it.

# Path of the Windows user's .wslconfig as seen from WSL, or nothing when
# Windows interop is unavailable.
wsl_config_path() {
    local cmd=/mnt/c/Windows/System32/cmd.exe profile
    [ -x "$cmd" ] || cmd=$(command -v cmd.exe 2>/dev/null) || return 1
    profile=$(cd /mnt/c 2>/dev/null && "$cmd" /c "echo %USERPROFILE%" 2>/dev/null | tr -d '\r')
    [ -n "$profile" ] || return 1
    profile=$(wslpath -u "$profile" 2>/dev/null) || return 1
    [ -d "$profile" ] || return 1
    printf '%s/.wslconfig\n' "$profile"
}

# 0 when both idle timeouts are disabled in the given .wslconfig.
wsl_keep_alive_configured() {
    local cfg="$1"
    [ -f "$cfg" ] || return 1
    python3 - "$cfg" <<'PY'
import re, sys
section, found = "", {}
for raw in open(sys.argv[1], encoding="utf-8-sig", errors="replace"):
    line = raw.strip()
    m = re.match(r"\[(.+)\]$", line)
    if m:
        section = m.group(1).strip().lower()
        continue
    m = re.match(r"([A-Za-z0-9_.]+)\s*=\s*(.*)$", line)
    if m:
        found[(section, m.group(1).lower())] = m.group(2).strip()
def negative(key):
    try:
        return int(found[key]) < 0
    except (KeyError, ValueError):
        return False
sys.exit(0 if negative(("general", "instanceidletimeout")) and negative(("wsl2", "vmidletimeout")) else 1)
PY
}

# Printed under the ready card. Silent once keep-alive is configured.
wsl_keep_alive_note() {
    local cfg
    cfg=$(wsl_config_path) || cfg=""
    if [ -n "$cfg" ] && wsl_keep_alive_configured "$cfg"; then
        return 0
    fi
    echo ""
    echo "  WSL: keep this window open. Windows stops this Linux system, and Guaardvark"
    echo "  with it, about 15 seconds after its last terminal closes."
    echo "  To keep it running in the background:  ./start.sh --wsl-keep-alive"
    echo ""
}

# Set both idle timeouts to -1 in the Windows user's .wslconfig, keeping every
# other line. The previous file is saved beside it first.
wsl_keep_alive_enable() {
    local cfg
    cfg=$(wsl_config_path) || {
        echo "Could not find the Windows user folder from WSL (is Windows interop disabled?)." >&2
        echo "Add these lines to %UserProfile%\\.wslconfig yourself:" >&2
        printf '  [general]\n  instanceIdleTimeout=-1\n\n  [wsl2]\n  vmIdleTimeout=-1\n' >&2
        return 1
    }
    if wsl_keep_alive_configured "$cfg"; then
        echo "WSL keep-alive is already set in $cfg"
        return 0
    fi
    [ -f "$cfg" ] && cp -p "$cfg" "$cfg.before-guaardvark"
    python3 - "$cfg" <<'PY' || return 1
import os, re, sys
path = sys.argv[1]
want = {"general": ("instanceIdleTimeout", "-1"), "wsl2": ("vmIdleTimeout", "-1")}
lines = open(path, encoding="utf-8-sig", errors="replace").read().splitlines() if os.path.exists(path) else []
out, section, done = [], None, set()
def close(sec):
    if sec in want and sec not in done:
        key, value = want[sec]
        while out and not out[-1].strip():
            out.pop()
        out.append(f"{key}={value}")
        out.append("")
        done.add(sec)
for line in lines:
    m = re.match(r"\s*\[(.+)\]\s*$", line)
    if m:
        close(section)
        section = m.group(1).strip().lower()
        out.append(line)
        continue
    if section in want:
        key, value = want[section]
        if re.match(rf"\s*{key}\s*=", line, re.I):
            out.append(f"{key}={value}")
            done.add(section)
            continue
    out.append(line)
close(section)
for sec, (key, value) in want.items():
    if sec not in done:
        if out and out[-1].strip():
            out.append("")
        out += [f"[{sec}]", f"{key}={value}", ""]
with open(path, "w", encoding="ascii", errors="replace", newline="\r\n") as fh:
    fh.write("\n".join(out).rstrip("\n") + "\n")
PY
    echo "WSL keep-alive written to $cfg"
    [ -f "$cfg.before-guaardvark" ] && echo "The previous file is saved as $cfg.before-guaardvark"
    echo "It takes effect after WSL restarts. In Windows PowerShell run:  wsl --shutdown"
    echo "then open Ubuntu again and run ./start.sh. Guaardvark then keeps running after"
    echo "the window closes; stop it with ./stop.sh, or stop WSL with: wsl --shutdown"
}
