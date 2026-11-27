#!/usr/bin/env bash
# Build a WSL-importable root filesystem: the official Ubuntu WSL image with
# Guaardvark installed by its own public installer, minus model weights and
# minus every per-machine secret. See README.md in this directory.
#
#   packaging/wsl-image/build.sh --out DIR [--ref main] [--repo URL] [--torch cuda|cpu]
#                                [--measure xz,zst] [--icon FILE.ico] [--keep]
#                                [--stop-after install]   (leave the installed container running to inspect it)
#
# Output: guaardvark-wsl-<version>-x64.tar.gz split into parts under 2 GiB, and the
# manifest guaardvark-wsl-<version>-x64.json the desktop app reads
# (desktop/platform/wsl/image.js). --measure also writes other compressions of the
# same tar, for comparison only.
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
TORCH="cuda"
MEASURE=""
# GitHub release assets must be under 2 GiB; parts stay well below it.
PART_BYTES=$((1900 * 1024 * 1024))
ICON=""
KEEP=0
STOP_AFTER=""
MEMORY="${GV_WSL_BUILD_MEMORY:-12g}"
CPUS="${GV_WSL_BUILD_CPUS:-6}"

usage() { sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
    case "$1" in
        --out) OUT="$2"; shift 2 ;;
        --ref) REF="$2"; shift 2 ;;
        --repo) REPO="$2"; shift 2 ;;
        --torch) TORCH="$2"; shift 2 ;;
        --measure) MEASURE="$2"; shift 2 ;;
        --icon) ICON="$2"; shift 2 ;;
        --keep) KEEP=1; shift ;;
        --stop-after) STOP_AFTER="$2"; KEEP=1; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done
[ -n "$OUT" ] || { echo "--out DIR is required" >&2; exit 2; }
case "$TORCH" in cuda|cpu) ;; *) echo "--torch must be cuda or cpu" >&2; exit 2 ;; esac
[ -n "$ICON" ] || { [ -f "$REPO_ROOT/desktop/build/icon.ico" ] && ICON="$REPO_ROOT/desktop/build/icon.ico"; } || true

command -v docker >/dev/null || { echo "docker is required" >&2; exit 1; }
mkdir -p "$OUT/base" "$OUT/logs"
OUT="$(cd "$OUT" && pwd)"
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
# The app supports NVIDIA cards only. install.sh chooses PyTorch from what nvidia-smi
# reports, so a stand-in answers as a current NVIDIA card under WSL for the length of
# the build (lib/nvidia-smi-build); finalize removes it. CUDA PyTorch also runs on CPU.
if [ "$TORCH" = cuda ]; then
    docker exec "$CONTAINER" install -m 0755 /usr/lib/guaardvark-image/nvidia-smi-build /usr/local/bin/nvidia-smi
fi
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
VERSION="$(docker exec "$CONTAINER" cat "$IMG_HOME/guaardvark/VERSION" | tr -dc 'A-Za-z0-9._-')"
NAME="guaardvark-wsl-${VERSION:-unknown}-x64"
docker exec -u "$IMG_USER" "$CONTAINER" "$IMG_HOME/guaardvark/backend/venv/bin/python" -c \
    'import torch; print("torch", torch.__version__, "cuda", torch.version.cuda)' | tee "$OUT/logs/torch.txt"
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

phase_start "compress gzip"
pigz -9 -p "$CPUS" -c "$TAR" > "$TAR.gz" 2>/dev/null || gzip -9 -c "$TAR" > "$TAR.gz"
phase_end

# Parts and manifest in the format desktop/platform/wsl/image.js reads. Part names
# are relative to the manifest; joined in order they are the .tar.gz.
phase_start "split + manifest"
rm -f "$TAR.gz".[0-9][0-9][0-9]
split -b "$PART_BYTES" -d -a 3 --numeric-suffixes=1 "$TAR.gz" "$TAR.gz."
python3 -I - "$OUT" "$NAME" "$VERSION" "$(stat -c %s "$TAR")" <<'PY'
import hashlib, json, os, sys
out, name, version, installed = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
whole = os.path.join(out, f"{name}.tar.gz")
parts = sorted(p for p in os.listdir(out) if p.startswith(f"{name}.tar.gz.") and p[-3:].isdigit())
manifest = {
    "schema": 1, "version": version, "arch": "x64", "format": "tar.gz",
    "size": os.path.getsize(whole), "sha256": sha(whole),
    # Bytes of the uncompressed root filesystem tar: what the import unpacks.
    "installedSize": installed,
    "parts": [{"name": p, "size": os.path.getsize(os.path.join(out, p)),
               "sha256": sha(os.path.join(out, p))} for p in parts],
}
assert sum(p["size"] for p in manifest["parts"]) == manifest["size"]
assert all(p["size"] < 2 * 1024**3 for p in manifest["parts"])
with open(os.path.join(out, f"{name}.json"), "w") as f:
    json.dump(manifest, f, indent=2)
    f.write("\n")
print(json.dumps(manifest, indent=2))
PY
phase_end

for m in ${MEASURE//,/ }; do
    case "$m" in
        xz)  phase_start "measure xz";   xz -6 -T "$CPUS" -c "$TAR" > "$TAR.xz"; phase_end ;;
        zst) phase_start "measure zstd"; zstd -19 -T"$CPUS" -q -c "$TAR" > "$TAR.zst"; phase_end ;;
        *) log "unknown --measure format: $m" ;;
    esac
done

( cd "$OUT" && sha256sum "$NAME".tar* > "$NAME.sha256" )
TOTAL=$(( $(date +%s) - BUILD_T0 ))
printf '%-28s %4dm%02ds\n' "total" $((TOTAL/60)) $((TOTAL%60)) | tee -a "$TIMINGS"
log "sizes:"
ls -l "$OUT/$NAME".tar* | awk '{printf "  %-50s %8.2f GiB  (%d bytes)\n", $9, $5/1073741824, $5}' | sed "s|$OUT/||"
log "done: $OUT"
[ "$KEEP" = 1 ] && log "build container kept: $CONTAINER"
exit 0
