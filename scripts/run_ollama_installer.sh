#!/usr/bin/env bash
# Run Ollama's Linux installer (https://ollama.com/install.sh, saved to a file).
#
#   scripts/run_ollama_installer.sh <path-to-ollama-install.sh>
#
# Ollama's installer checks for an AMD GPU before an NVIDIA one whenever
# nvidia-smi is missing, so a machine with an NVIDIA card (driver not yet
# installed) and an AMD integrated GPU downloads the ~2.7 GB ROCm add-on and
# no NVIDIA support. The installer has no option to skip ROCm, so in exactly
# that case it runs with lspci, lshw and sudo wrapped to hide AMD (vendor 1002)
# and NVIDIA (10de) devices: it then installs its standard build, which already
# carries the CUDA libraries, and neither ROCm nor NVIDIA's driver repository.
# Every other machine (AMD only, no GPU, nvidia-smi present, WSL) runs the
# installer unchanged.
set -u

installer="${1:?usage: run_ollama_installer.sh <path-to-ollama-install.sh>}"
shift

nvidia_device_present() {
  local dev class
  [ -d /proc/driver/nvidia ] && return 0
  # sysfs needs no tools or root; lspci is often not installed.
  for dev in /sys/bus/pci/devices/*; do
    [ "$(cat "$dev/vendor" 2>/dev/null)" = "0x10de" ] || continue
    class=$(cat "$dev/class" 2>/dev/null)
    case "$class" in 0x03*) return 0 ;; esac
  done
  command -v lspci >/dev/null 2>&1 && [ -n "$(lspci -d 10de: 2>/dev/null)" ]
}

if [ "$(uname -s)" != "Linux" ] || command -v nvidia-smi >/dev/null 2>&1 || ! nvidia_device_present; then
  exec sh "$installer" "$@"
fi

shim=$(mktemp -d 2>/dev/null || echo "${TMPDIR:-/tmp}/ollama-installer-shim.$$")
mkdir -p "$shim"
trap 'rm -rf "$shim"' EXIT

real_lspci=$(command -v lspci 2>/dev/null || true)
real_lshw=$(command -v lshw 2>/dev/null || true)
real_sudo=$(command -v sudo 2>/dev/null || true)

if [ -n "$real_lspci" ]; then
  cat > "$shim/lspci" <<EOF
#!/bin/sh
# Hides NVIDIA (10de) and AMD (1002) devices from Ollama's installer.
for a in "\$@"; do
  case "\$a" in 10de:*|1002:*|-d10de:*|-d1002:*) exit 0 ;; esac
done
"$real_lspci" "\$@" | grep -v -e 'NVIDIA' -e 'AMD/ATI'
exit 0
EOF
fi
if [ -n "$real_lshw" ]; then
  cat > "$shim/lshw" <<EOF
#!/bin/sh
# Drops NVIDIA (10DE) and AMD (1002) vendor lines from lshw's output.
"$real_lshw" "\$@" | grep -v -E 'vendor: .*\[(10DE|1002)\]'
EOF
fi
if [ -n "$real_sudo" ] && [ -n "$real_lshw" ]; then
  # The installer runs `sudo lshw`, and sudo resets PATH; route that one
  # command through the wrapper, everything else straight to sudo.
  cat > "$shim/sudo" <<EOF
#!/bin/sh
if [ "\${1:-}" = "lshw" ]; then
  shift
  "$real_sudo" "$real_lshw" "\$@" | grep -v -E 'vendor: .*\[(10DE|1002)\]'
  exit 0
fi
exec "$real_sudo" "\$@"
EOF
fi
chmod +x "$shim"/* 2>/dev/null

echo "An NVIDIA card is present, so Ollama's ROCm (AMD GPU) download is skipped; its standard build includes CUDA support."
PATH="$shim:$PATH" sh "$installer" "$@"
rc=$?
echo "Ollama's \"CPU-only\" note above comes from that skip: it uses the NVIDIA card once the NVIDIA driver is installed."
exit $rc
