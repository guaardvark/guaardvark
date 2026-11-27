#!/usr/bin/env bash
# Runs as root inside the build container after install.sh finished and stop.sh
# ran. Leaves Guaardvark installed but strips everything that must be unique per
# machine or that only the build produced. start.sh recreates what it needs.
#   finalize-root.sh PUBLIC_REPO_URL
set -euo pipefail
PUBLIC_REPO="$1"
USER_NAME=guaardvark
HOME_DIR="/home/$USER_NAME"
GV="$HOME_DIR/guaardvark"
ENV_FILE="$GV/.env"

say() { printf '[finalize] %s\n' "$*"; }

# ── Secrets ──────────────────────────────────────────────────────────────────
# The database role keeps its schema and loses its password; start_postgres.sh
# sees no DATABASE_URL on first start, sets a new random password and writes it.
if systemctl is-active --quiet postgresql; then
    runuser -u postgres -- psql -qtAc "ALTER ROLE $USER_NAME PASSWORD NULL;" \
        && say "postgres role $USER_NAME: password cleared"
    # The backend saves the chat model it fell back to on its first boot, picked for
    # the build machine. Without the row, the user's first boot picks for theirs.
    runuser -u postgres -- psql -d guaardvark -qtAc "DELETE FROM settings WHERE key = 'active_model_name';" >/dev/null \
        && say "settings: active_model_name cleared"
fi

# stop.sh leaves Redis running. When start_redis.sh started it by hand (the
# container's systemd cannot run the unit's sandbox), stop it without a dump.
redis_url="$(grep -E '^REDIS_URL=' "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2-)"
[ -n "$redis_url" ] && redis-cli -u "$redis_url" --no-auth-warning shutdown nosave >/dev/null 2>&1 || true

# .env keeps its non-secret settings. Every line carrying a credential goes; the
# first start writes fresh ones (SECRET_KEY in start.sh, DATABASE_URL in
# start_postgres.sh, the Redis URLs in start_redis.sh).
if [ -f "$ENV_FILE" ]; then
    grep -vE '^(SECRET_KEY|DATABASE_URL|REDIS_URL|CELERY_BROKER_URL|CELERY_RESULT_BACKEND)=' "$ENV_FILE" \
        | grep -viE '^[A-Z0-9_]*(PASSWORD|PASSWD|SECRET|TOKEN|API_KEY|PRIVATE_KEY)[A-Z0-9_]*=' > "$ENV_FILE.new" || true
    chown "$USER_NAME:$USER_NAME" "$ENV_FILE.new"
    chmod 600 "$ENV_FILE.new"
    mv "$ENV_FILE.new" "$ENV_FILE"
    # The image's fixed settings (lib/image.env), the values the build ran with.
    grep -E '^[A-Z_]+=' /usr/lib/guaardvark-image/image.env | while IFS= read -r line; do
        grep -q "^${line%%=*}=" "$ENV_FILE" || echo "$line" >> "$ENV_FILE"
    done
    say ".env keys kept: $(cut -d= -f1 "$ENV_FILE" | grep -v '^#' | grep -v '^$' | tr '\n' ' ')"
fi

# Redis: start_redis.sh wrote its password into /etc/redis/redis.conf. Put the
# package's own file back and leave the unit disabled, so on first start Redis is
# down and start_redis.sh provisions a new password and starts it, as it does on
# a machine that has never had Redis. (Enabled, the package config would come up
# without a password at boot and start_redis.sh would adopt it as is.)
pristine="$(mktemp)"
deb="$(ls /var/cache/apt/archives/redis-server_*.deb 2>/dev/null | head -1)"
if [ -z "$deb" ]; then
    (cd /tmp && apt-get download -q redis-server >/dev/null) && deb="$(ls /tmp/redis-server_*.deb | head -1)"
fi
if [ -n "$deb" ] && dpkg-deb --fsys-tarfile "$deb" | tar -xO ./etc/redis/redis.conf > "$pristine" && [ -s "$pristine" ]; then
    install -m 0640 -o redis -g redis "$pristine" /etc/redis/redis.conf
    say "redis.conf restored from $(basename "$deb")"
else
    sed -i '/^requirepass /d' /etc/redis/redis.conf
    say "redis.conf: requirepass line removed (package file not found)"
fi
rm -f "$pristine"
grep -q '^requirepass ' /etc/redis/redis.conf && { say "redis.conf still has a password"; exit 1; }
systemctl disable redis-server 2>/dev/null || true

# ── Build-time nvidia-smi stand-in ───────────────────────────────────────────
# Gone before export: under WSL the real one comes from /usr/lib/wsl/lib. The GPU
# sudoers rule start.sh wrote names the stand-in's path; start.sh writes it again
# for the real binary on a machine with a card.
if grep -q 'Build-time stand-in for nvidia-smi' /usr/local/bin/nvidia-smi 2>/dev/null; then
    rm -f /usr/local/bin/nvidia-smi
    say "nvidia-smi stand-in removed"
fi
rm -f /etc/sudoers.d/gpu-guaardvark

# ── Per-machine identity ─────────────────────────────────────────────────────
rm -rf "$HOME_DIR/.guaardvark"                       # hardware.json, node_id, first-start note
rm -f /usr/share/ollama/.ollama/id_ed25519 /usr/share/ollama/.ollama/id_ed25519.pub
rm -f "$HOME_DIR/.ollama/id_ed25519" "$HOME_DIR/.ollama/id_ed25519.pub"
# Ollama tuning rendered for the build machine's hardware; start.sh renders this
# machine's on first start.
rm -f /etc/systemd/system/ollama.service.d/guaardvark-tuning.conf "$GV/data/ollama-dropin.rendered.conf"
rmdir /etc/systemd/system/ollama.service.d 2>/dev/null || true
# PostgreSQL's TLS key from the ssl-cert package; firstboot.sh makes this machine's.
find /etc/ssl/certs -maxdepth 1 -lname ssl-cert-snakeoil.pem -delete
rm -f /etc/ssl/private/ssl-cert-snakeoil.key /etc/ssl/certs/ssl-cert-snakeoil.pem
: > /etc/machine-id
rm -f /var/lib/dbus/machine-id /var/lib/systemd/random-seed /var/lib/systemd/credential.secret

# First-boot unit, ordered before every PostgreSQL cluster unit present.
pg_units="postgresql.service"
for conf in /etc/postgresql/*/*/postgresql.conf; do
    [ -f "$conf" ] || continue
    ver="$(basename "$(dirname "$(dirname "$conf")")")"
    cl="$(basename "$(dirname "$conf")")"
    pg_units="$pg_units postgresql@${ver}-${cl}.service"
done
cat > /etc/systemd/system/guaardvark-firstboot.service <<EOF
[Unit]
Description=Guaardvark image: per-machine keys on first boot
ConditionPathExists=/var/lib/guaardvark-image/firstboot-pending
Before=$pg_units

[Service]
Type=oneshot
ExecStart=/usr/lib/guaardvark-image/firstboot.sh

[Install]
WantedBy=multi-user.target
EOF
chmod 0755 /usr/lib/guaardvark-image/firstboot.sh
systemctl enable guaardvark-firstboot.service
mkdir -p /var/lib/guaardvark-image
touch /var/lib/guaardvark-image/firstboot-pending
say "first-boot unit before: $pg_units"

# ── Build leftovers ──────────────────────────────────────────────────────────
# Runtime state of this build's processes; start.sh writes its own.
find "$GV/logs" -mindepth 1 -delete 2>/dev/null || true
find "$GV/pids" -mindepth 1 ! -name '.migration_fingerprint' -delete 2>/dev/null || true
rm -rf "$GV/data/piptmp" "$GV/data/.pt-wheels-"* "$GV/.start_cache/start.sh.pid" "$GV/.start_cache/profile.err"
# Runtime state written by the build's own run (model choice, GPU check, which plugins ran).
rm -f "$GV/data/active_model.json" "$GV/data/gpu_stack_status.json" "$GV/data/plugin_state.json"
# Ollama's installer fetches its ROCm add-on when it sees an AMD display device; the
# build host's shows through docker. Under WSL2 the installer never fetches it (WSL
# passes only NVIDIA through), so it does not belong in the image.
rm -rf /usr/local/lib/ollama/rocm_*
# The checkout points at the public repository, whatever it was built from.
runuser -u "$USER_NAME" -- git -C "$GV" remote set-url origin "$PUBLIC_REPO"

# Download caches: pip, uv, npm, apt archives.
rm -rf "$HOME_DIR/.cache" "$HOME_DIR/.npm" "$HOME_DIR/.wget-hsts" "$HOME_DIR/.lesshst" \
       /root/.cache /root/.npm /root/.wget-hsts /root/.launchpadlib
rm -f "$HOME_DIR/.bash_history" /root/.bash_history "$HOME_DIR/.python_history" /root/.python_history
apt-get clean
rm -rf /var/cache/apt/*.bin /var/cache/debconf/*-old /var/lib/apt/lists/partial/*

# Logs and temp files.
find /var/log -type f \( -name '*.gz' -o -name '*.[0-9]' -o -name '*.old' \) -delete
# -c: no O_CREAT, which fs.protected_regular refuses even to root in sticky
# directories such as /var/log/postgresql.
find /var/log -type f -exec truncate -c -s 0 {} + || say "some logs could not be emptied"
rm -rf /var/log/journal/* /tmp/* /tmp/.[!.]* /var/tmp/* /var/crash/* 2>/dev/null || true

# Stop the services so their files are consistent on disk for the export.
systemctl stop ollama redis-server postgresql 2>/dev/null || true
say "done"
