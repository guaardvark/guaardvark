#!/usr/bin/env bash
# Build a WSL-importable root filesystem: the official Ubuntu WSL image with
# Guaardvark installed by its own public installer, minus model weights and
# minus every per-machine secret. See README.md in this directory.
#
#   packaging/wsl-image/build.sh --out DIR [--ref main] [--repo URL]
#                                [--compress gz|xz|zst|all|none] [--icon FILE.ico] [--keep]
#                                [--stop-after install]   (leave the installed container running to inspect it)
#
# Runs entirely in docker: no published ports, no GPU, capped memory and CPU.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"

# The distribution `wsl --install` gives today (microsoft/WSL distributions/DistributionInfo.json,
# "Default": "Ubuntu"). Update both lines together when Microsoft moves the default.
BASE_URL="${GV_WSL_BASE_URL:-https://releases.ubuntu.com/26.04.1/ubuntu-26.04.1-wsl-amd64.wsl}"
BASE_SHA256="${GV_WSL_BASE_SHA256:-48d56724b5c8e60f24893e83e73bbb58c60b3ca22fba3da977075420acd54104}"

PUBLIC_REPO="https://github.com/guaardvark/guaardvark.git"
REPO="$PUBLIC_REPO"
REF="main"
OUT=""
COMPRESS="gz"
ICON=""
KEEP=0
STOP_AFTER=""
MEMORY="${GV_WSL_BUILD_MEMORY:-12g}"
CPUS="${GV_WSL_BUILD_CPUS:-6}"

usage() { sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
    case "$1" in
        --out) OUT="$2"; shift 2 ;;
        --ref) REF="$2"; shift 2 ;;
        --repo) REPO="$2"; shift 2 ;;
        --compress) COMPRESS="$2"; shift 2 ;;
        --icon) ICON="$2"; shift 2 ;;
        --keep) KEEP=1; shift ;;
        --stop-after) STOP_AFTER="$2"; KEEP=1; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done
[ -n "$OUT" ] || { echo "--out DIR is required" >&2; exit 2; }
case "$COMPRESS" in gz|xz|zst|all|none) ;; *) echo "--compress must be gz, xz, zst, all or none" >&2; exit 2 ;; esac
[ -n "$ICON" ] || { [ -f "$REPO_ROOT/desktop/build/icon.ico" ] && ICON="$REPO_ROOT/desktop/build/icon.ico"; } || true

command -v docker >/dev/null || { echo "docker is required" >&2; exit 1; }
mkdir -p "$OUT/base" "$OUT/logs"
OUT="$(cd "$OUT" && pwd)"
SAFE_REF="$(printf '%s' "$REF" | tr -c 'A-Za-z0-9._-' '-')"
NAME="guaardvark-wsl-${SAFE_REF}"
CONTAINER="gv-wsl-build-$$"
IMG_USER=guaardvark
IMG_HOME="/home/${IMG_USER}"
BASE_FILE="$OUT/base/$(basename "$BASE_URL")"
BASE_TAG="guaardvark-wsl-base:$(basename "$BASE_URL" .wsl | sed 's/^ubuntu-//; s/-wsl-amd64$//')"
TIMINGS="$OUT/logs/timings.txt"
: > "$TIMINGS"

log() { printf '[wsl-image] %s\n' "$*"; }
phase_start() { PHASE_NAME="$1"; PHASE_T0=$(date +%s); log "$1..."; }
phase_end() { local s=$(( $(date +%s) - PHASE_T0 )); printf '%-28s %4dm%02ds\n' "$PHASE_NAME" $((s/60)) $((s%60)) | tee -a "$TIMINGS"; }

cleanup() {
    if [ "$KEEP" = 0 ] && docker inspect "$CONTAINER" >/dev/null 2>&1; then
        docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

BUILD_T0=$(date +%s)

# 1. Base image: the exact file `wsl --install` downloads, checked against the manifest's hash.
phase_start "base download"
if [ ! -f "$BASE_FILE" ] || ! echo "$BASE_SHA256  $BASE_FILE" | sha256sum -c --status; then
    curl -fL --retry 3 -o "$BASE_FILE.part" "$BASE_URL"
    mv "$BASE_FILE.part" "$BASE_FILE"
fi
echo "$BASE_SHA256  $BASE_FILE" | sha256sum -c --quiet
docker import "$BASE_FILE" "$BASE_TAG" >/dev/null
phase_end

# 2. Boot it with systemd as PID 1, as WSL does with systemd=true. SYS_ADMIN and an
# unconfined AppArmor profile only let systemd mount its own cgroup tree; there are
# no published ports and no GPU devices.
phase_start "container boot"
docker run -d --name "$CONTAINER" --hostname guaardvark \
    --memory "$MEMORY" --memory-swap "$MEMORY" --cpus "$CPUS" \
    --cgroupns=private --cap-add SYS_ADMIN --security-opt apparmor=unconfined \
    --tmpfs /run --tmpfs /run/lock --stop-signal SIGRTMIN+3 -e container=docker \
    "$BASE_TAG" bash -c 'mount -o remount,rw /sys/fs/cgroup && exec /usr/lib/systemd/systemd' >/dev/null
for _ in $(seq 1 60); do
    state="$(docker exec "$CONTAINER" systemctl is-system-running 2>/dev/null || true)"
    case "$state" in running|degraded) break ;; esac
    sleep 1
done
log "systemd: ${state:-unknown}"
docker exec "$CONTAINER" mkdir -p /usr/lib/guaardvark-image
docker cp "$HERE/lib/." "$CONTAINER:/usr/lib/guaardvark-image/"
[ -n "$ICON" ] && docker cp "$ICON" "$CONTAINER:/usr/lib/guaardvark-image/guaardvark.ico"
phase_end

# 3. The default user, sudo, /etc/wsl.conf and /etc/wsl-distribution.conf.
phase_start "provision user"
docker exec "$CONTAINER" bash /usr/lib/guaardvark-image/provision-root.sh 2>&1 | tee "$OUT/logs/provision.log"
phase_end

# 4. Guaardvark, installed by its own install.sh exactly as a user runs it, without model pulls.
phase_start "install (install.sh)"
docker exec -u "$IMG_USER" -w "$IMG_HOME" -e HOME="$IMG_HOME" "$CONTAINER" \
    bash -l /usr/lib/guaardvark-image/install-user.sh "$REPO" "$REF" > "$OUT/logs/install.log" 2>&1 \
    || { log "install.sh failed; see $OUT/logs/install.log"; tail -n 40 "$OUT/logs/install.log"; exit 1; }
phase_end
tail -n 25 "$OUT/logs/install.log"
if [ "$STOP_AFTER" = install ]; then
    log "stopped after install; container left running: $CONTAINER"
    exit 0
fi

# 5. Prove the install answers before freezing it, then stop it the supported way.
phase_start "check + stop"
docker exec -u "$IMG_USER" "$CONTAINER" bash -c '
    set -a; . ~/guaardvark/.env 2>/dev/null; set +a
    echo "health: $(curl -s --max-time 10 http://127.0.0.1:${FLASK_PORT:-5000}/api/health)"
    echo "web:    HTTP $(curl -s -o /dev/null -w %{http_code} --max-time 10 http://127.0.0.1:${VITE_PORT:-5173}/)"
' | tee "$OUT/logs/build-health.txt"
docker exec -u "$IMG_USER" -w "$IMG_HOME/guaardvark" -e HOME="$IMG_HOME" "$CONTAINER" \
    bash -lc './stop.sh' > "$OUT/logs/stop.log" 2>&1 || log "stop.sh reported an error; see $OUT/logs/stop.log"
phase_end

# 6. Remove secrets, per-machine identity and caches.
phase_start "finalize"
docker exec "$CONTAINER" bash /usr/lib/guaardvark-image/finalize-root.sh "$PUBLIC_REPO" 2>&1 | tee "$OUT/logs/finalize.log"
docker stop -t 60 "$CONTAINER" >/dev/null
phase_end

# 7. Export the root filesystem. Docker-owned files are swapped back for the base
# image's own, and the machine id is left empty for the first boot to fill.
phase_start "export tar"
TAR="$OUT/$NAME.tar"
docker export "$CONTAINER" | python3 -I "$HERE/lib/rewrite_rootfs.py" --base "$BASE_FILE" \
    --out "$TAR" --report "$OUT/logs/size-report.txt"
phase_end

compress() {  # $1 = gz|xz|zst
    case "$1" in
        gz)  phase_start "compress gzip";  pigz -9 -p "$CPUS" -k -c "$TAR" > "$TAR.gz" 2>/dev/null || gzip -9 -k -c "$TAR" > "$TAR.gz" ;;
        xz)  phase_start "compress xz";    xz -6 -T "$CPUS" -k -c "$TAR" > "$TAR.xz" ;;
        zst) phase_start "compress zstd";  zstd -19 -T"$CPUS" -q -k -c "$TAR" > "$TAR.zst" ;;
    esac
    phase_end
}
case "$COMPRESS" in
    all) compress gz; compress xz; compress zst ;;
    none) ;;
    *) compress "$COMPRESS" ;;
esac

( cd "$OUT" && sha256sum "$NAME".tar* > "$NAME.sha256" )
TOTAL=$(( $(date +%s) - BUILD_T0 ))
printf '%-28s %4dm%02ds\n' "total" $((TOTAL/60)) $((TOTAL%60)) | tee -a "$TIMINGS"
log "sizes:"
ls -l "$OUT/$NAME".tar* | awk '{printf "  %-50s %8.2f GiB  (%d bytes)\n", $9, $5/1073741824, $5}' | sed "s|$OUT/||"
log "done: $OUT"
[ "$KEEP" = 1 ] && log "build container kept: $CONTAINER"
exit 0
