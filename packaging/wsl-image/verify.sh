#!/usr/bin/env bash
# Boot a built image the way WSL would (systemd as PID 1, the default user) in a
# throwaway container and run Guaardvark from it: first start, a second start,
# and a start after a reboot. CPU only, no models, no published ports.
#
#   packaging/wsl-image/verify.sh --tar guaardvark-wsl-main.tar[.gz|.xz] --out DIR [--keep]
#
# A container is not WSL: no Windows interop, no GPU, docker's own networking.
# What it does show is that the image boots, gets its own keys and secrets, and
# that start.sh brings the studio up from it.

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TAR=""; OUT=""; KEEP=0
MEMORY="${GV_WSL_BUILD_MEMORY:-12g}"
CPUS="${GV_WSL_BUILD_CPUS:-6}"
while [ $# -gt 0 ]; do
    case "$1" in
        --tar) TAR="$2"; shift 2 ;;
        --out) OUT="$2"; shift 2 ;;
        --keep) KEEP=1; shift ;;
        -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done
[ -f "$TAR" ] && [ -n "$OUT" ] || { echo "usage: verify.sh --tar FILE --out DIR" >&2; exit 2; }
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
TAG="guaardvark-wsl-verify:$(date +%Y%m%d%H%M%S)"
C="gv-wsl-verify-$$"
cleanup() { [ "$KEEP" = 1 ] || docker rm -f "$C" >/dev/null 2>&1 || true; }
trap cleanup EXIT

log() { printf '[verify] %s\n' "$*"; }
IMG_USER=guaardvark
IMG_HOME="/home/${IMG_USER}"
as_user() { docker exec -u "$IMG_USER" -w "$IMG_HOME/guaardvark" -e HOME="$IMG_HOME" "$C" bash -lc "$1"; }
wait_systemd() {
    local s=""
    for _ in $(seq 1 90); do
        s="$(docker exec "$C" systemctl is-system-running 2>/dev/null || true)"
        case "$s" in running|degraded) break ;; esac
        sleep 1
    done
    log "systemd: $s"
}
timed_start() {  # $1 = label
    local t0 t1
    t0=$(date +%s.%N)
    local rc=0
    as_user 'GUAARDVARK_BOOTSTRAP_MODELS=0 ./start.sh < /dev/null' > "$OUT/start-$1.log" 2>&1 || rc=$?
    t1=$(date +%s.%N)
    printf '%-22s %6.1f s\n' "start ($1)" "$(awk -v a="$t0" -v b="$t1" 'BEGIN{print b-a}')" | tee -a "$OUT/timings.txt"
    if [ "$rc" -ne 0 ]; then
        log "start.sh exit code $rc ($1); diagnostics in $OUT/diag-$1.txt"
        docker exec "$C" bash -c '
            systemctl --failed --no-pager
            journalctl -b --no-pager -n 60 -u "postgresql*" -u redis-server -u guaardvark-firstboot
            tail -n 40 /var/log/postgresql/*.log
            tail -n 80 ~guaardvark/guaardvark/logs/setup.log' > "$OUT/diag-$1.txt" 2>&1 || true
    fi
}
health() {  # $1 = label
    as_user '
        set -a; . ./.env; set +a
        echo "api  GET /api/health -> $(curl -s --max-time 10 http://127.0.0.1:${FLASK_PORT:-5000}/api/health)"
        echo "web  GET /           -> HTTP $(curl -s -o /dev/null -w "%{http_code} %{content_type} %{size_download} bytes" --max-time 10 http://127.0.0.1:${VITE_PORT:-5173}/)"
        echo "web  <title>         -> $(curl -s --max-time 10 http://127.0.0.1:${VITE_PORT:-5173}/ | grep -o "<title>[^<]*</title>")"
    ' | sed "s/^/[$1] /" | tee -a "$OUT/health.txt"
}

: > "$OUT/timings.txt"; : > "$OUT/health.txt"
t0=$(date +%s)
docker import "$TAR" "$TAG" >/dev/null
printf '%-22s %6d s\n' "docker import" $(( $(date +%s) - t0 )) | tee -a "$OUT/timings.txt"

docker run -d --name "$C" --hostname verify-box \
    --memory "$MEMORY" --memory-swap "$MEMORY" --cpus "$CPUS" \
    --cgroupns=private --cap-add SYS_ADMIN --security-opt apparmor=unconfined \
    --tmpfs /run --tmpfs /run/lock --stop-signal SIGRTMIN+3 -e container=docker \
    "$TAG" bash -c 'mount -o remount,rw /sys/fs/cgroup && exec /usr/lib/systemd/systemd' >/dev/null
wait_systemd

log "first boot checks" | tee -a "$OUT/firstboot.txt"
docker exec "$C" bash -c '
    echo "default user (wsl.conf): $(sed -n "/^\[user\]/,/^\[/p" /etc/wsl.conf | grep default)"
    echo "firstboot unit: $(systemctl show -p Result --value guaardvark-firstboot) / marker left: $(ls /var/lib/guaardvark-image/ 2>/dev/null | wc -l)"
    echo "snakeoil key: $(stat -c "%U:%G %a %y" /etc/ssl/private/ssl-cert-snakeoil.key 2>&1)"
    echo "machine-id: $(wc -c < /etc/machine-id) bytes"
    for u in postgresql redis-server ollama; do echo "$u: $(systemctl is-enabled $u) $(systemctl is-active $u)"; done
    echo "sudo -n as guaardvark: $(runuser -u guaardvark -- sudo -n true && echo ok)"
    echo ".env before first start: $(cut -d= -f1 ~guaardvark/guaardvark/.env | tr "\n" " ")"
' | tee -a "$OUT/firstboot.txt"

timed_start first
health first
as_user 'echo ".env after first start: $(cut -d= -f1 .env | tr "\n" " ")"; echo "SECRET_KEY length: $(grep ^SECRET_KEY= .env | cut -d= -f2- | tr -d "\n" | wc -c)"; echo "DATABASE_URL has password: $(grep -c "^DATABASE_URL=postgresql://guaardvark:[^@]\+@" .env)"; echo "REDIS_URL form: $(grep ^REDIS_URL= .env | sed -E "s#://:[^@]+@#://:<password>@#")"' | tee -a "$OUT/firstboot.txt"

as_user './stop.sh' > "$OUT/stop-1.log" 2>&1 || true
timed_start second
health second

log "reboot (docker restart, as wsl --shutdown and reopen)"
as_user './stop.sh' > "$OUT/stop-2.log" 2>&1 || true
docker restart -t 60 "$C" >/dev/null
wait_systemd
timed_start after-reboot
health after-reboot
as_user 'echo "REDIS_URL after reboot: $(grep ^REDIS_URL= .env | sed -E "s#://:[^@]+@#://:<password>@#")"; tail -n 12 logs/setup.log 2>/dev/null | grep -i redis || true' | tee -a "$OUT/firstboot.txt"
as_user './stop.sh' > "$OUT/stop-3.log" 2>&1 || true
log "done: $OUT"
