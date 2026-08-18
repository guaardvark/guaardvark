#!/bin/bash
# start_redis.sh - auto-provision and start a local Redis server for development
# Handles installation, authentication setup, and .env credential management.

VADER_RED="\033[38;5;196m"
VADER_RED_DARK="\033[38;5;88m"
VADER_RED_LIGHT="\033[38;5;203m"
VADER_GRAY="\033[38;5;244m"
VADER_GRAY_DARK="\033[38;5;238m"
VADER_WHITE="\033[38;5;255m"
VADER_WHITE_DIM="\033[38;5;250m"
VADER_RESET="\033[0m"
VADER_BOLD="\033[1m"

vader_info() { echo -e "  ${VADER_GRAY}·${VADER_RESET} ${VADER_WHITE_DIM}$1${VADER_RESET}"; }
vader_success() { echo -e "  ${VADER_RED}✔${VADER_RESET} ${VADER_WHITE}$1${VADER_RESET}"; }
vader_warn() { echo -e "  ${VADER_RED_LIGHT}⚠${VADER_RESET} ${VADER_RED_LIGHT}$1${VADER_RESET}"; }
vader_error() { echo -e "  ${VADER_RED_DARK}✖${VADER_RESET} ${VADER_RED}$1${VADER_RESET}"; }

PORT=${REDIS_PORT:-6379}
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
REDIS_CONF="/etc/redis/redis.conf"
PIDS_DIR="$SCRIPT_DIR/pids"
REDIS_PID_FILE="$PIDS_DIR/redis.pid"
# Sidecar data dir (not /tmp alone) so we can find our process after restart.
REDIS_ALT_DIR="${REDIS_ALT_DIR:-$SCRIPT_DIR/data/redis-broker}"

# DB index for every URL we write. Sibling installs sharing one box separate
# their brokers by Redis DB number (the Celery queue names are identical, so a
# shared DB lets one install's workers consume the other's tasks). Honor an
# explicit REDIS_DB, else keep the index .env already carries — clobbering it
# back to /0 silently re-merges the brokers.
if [ -z "${REDIS_DB:-}" ] && [ -f "$ENV_FILE" ]; then
  REDIS_DB=$(grep '^REDIS_URL=' "$ENV_FILE" 2>/dev/null | tail -1 | sed -nE 's|^.*:[0-9]+/([0-9]+)[[:space:]]*$|\1|p')
fi
case "${REDIS_DB:-}" in (''|*[!0-9]*) REDIS_DB=0 ;; esac

command_exists() { command -v "$1" >/dev/null 2>&1; }

# ─── Helper: extract Redis password from .env ────────────────────────────────

get_redis_password() {
  if [ -f "$ENV_FILE" ] && grep -q "^REDIS_URL=" "$ENV_FILE"; then
    # Extract password from redis://:PASSWORD@host:port/db
    sed -n 's|^REDIS_URL=redis://:\([^@]*\)@.*|\1|p' "$ENV_FILE"
  fi
}

get_redis_url_port() {
  local url port
  if [ ! -f "$ENV_FILE" ]; then
    return
  fi
  url=$(grep "^REDIS_URL=" "$ENV_FILE" 2>/dev/null | tail -1 | sed 's/^REDIS_URL=//')
  [ -n "$url" ] || return
  port=$(echo "$url" | sed -E 's|^redis://([^@]*@)?[^:]+:([0-9]+).*|\2|')
  [ -n "$port" ] && echo "$port"
}

get_redis_url_raw() {
  if [ -f "$ENV_FILE" ]; then
    grep "^REDIS_URL=" "$ENV_FILE" 2>/dev/null | tail -1 | sed 's/^REDIS_URL=//'
  fi
}

# ─── Helper: test Redis connectivity (with or without auth) ─────────────────

redis_ping() {
  local pass="$1"
  local port="${2:-$PORT}"
  if [ -n "$pass" ]; then
    redis-cli -p "$port" -a "$pass" --no-auth-warning ping 2>/dev/null | grep -q PONG
  else
    redis-cli -p "$port" ping 2>/dev/null | grep -q PONG
  fi
}

redis_port_listening() {
  local port="${1:-$PORT}"
  if command_exists ss; then
    ss -tln 2>/dev/null | grep -q ":${port}\b"
    return $?
  fi
  if command_exists lsof; then
    lsof -ti "tcp:${port}" >/dev/null 2>&1
    return $?
  fi
  return 1
}

redis_auth_required() {
  local port="${1:-$PORT}"
  local out
  out=$(redis-cli -p "$port" ping 2>&1 || true)
  echo "$out" | grep -qi NOAUTH
}

write_redis_env_urls() {
  local url="$1"
  [ -f "$ENV_FILE" ] || touch "$ENV_FILE"
  chmod 600 "$ENV_FILE" 2>/dev/null || true
  local _tmp="${ENV_FILE}.tmp.$$"
  grep -vE '^(REDIS_URL|CELERY_BROKER_URL|CELERY_RESULT_BACKEND)=' "$ENV_FILE" > "$_tmp" 2>/dev/null || true
  {
    echo "REDIS_URL=${url}"
    echo "CELERY_BROKER_URL=${url}"
    echo "CELERY_RESULT_BACKEND=${url}"
  } >> "$_tmp"
  mv "$_tmp" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
}

write_redis_pid() {
  local port="$1"
  local rpid=""
  mkdir -p "$PIDS_DIR"
  rpid=$(redis-cli -p "$port" INFO server 2>/dev/null | tr -d '\r' | sed -n 's/^process_id://p')
  if [ -n "$rpid" ]; then
    echo "$rpid" > "$REDIS_PID_FILE"
  fi
}

clear_redis_pid_if_dead() {
  if [ -f "$REDIS_PID_FILE" ]; then
    local old
    old=$(cat "$REDIS_PID_FILE" 2>/dev/null || true)
    if [ -z "$old" ] || ! kill -0 "$old" 2>/dev/null; then
      rm -f "$REDIS_PID_FILE"
    fi
  fi
}

# Sync .env to a reachable broker. Prefer default PORT when passwordless (or with
# known password) so we do not leave REDIS_URL stuck on a dead alt port (6380).
# Returns 0 and writes .env on success.
sync_env_to_reachable_broker() {
  local pass="${1:-$(get_redis_password)}"
  local env_port
  env_port=$(get_redis_url_port)

  # 1) Default port, passwordless — preferred durable path (systemd redis).
  if redis_ping "" "$PORT"; then
    write_redis_env_urls "redis://localhost:${PORT}/${REDIS_DB}"
    # Not our sidecar — drop stale alt pid if it pointed at a dead process.
    clear_redis_pid_if_dead
    if [ -n "$env_port" ] && [ "$env_port" != "$PORT" ]; then
      vader_success "Redis broker URL reset to :${PORT} (was stale :${env_port} in .env)."
    else
      vader_success "Redis broker URL set to :${PORT}."
    fi
    return 0
  fi

  # 2) Default port with password from .env.
  if [ -n "$pass" ] && redis_ping "$pass" "$PORT"; then
    write_redis_env_urls "redis://:${pass}@localhost:${PORT}/${REDIS_DB}"
    clear_redis_pid_if_dead
    vader_success "Redis broker URL set to :${PORT} (authenticated)."
    return 0
  fi

  # 3) Env-recorded alt port still alive.
  if [ -n "$env_port" ] && [ "$env_port" != "$PORT" ]; then
    if redis_ping "" "$env_port" || { [ -n "$pass" ] && redis_ping "$pass" "$env_port"; }; then
      if [ -n "$pass" ] && redis_ping "$pass" "$env_port"; then
        write_redis_env_urls "redis://:${pass}@localhost:${env_port}/${REDIS_DB}"
      else
        write_redis_env_urls "redis://localhost:${env_port}/${REDIS_DB}"
      fi
      write_redis_pid "$env_port"
      vader_success "Redis already running on port ${env_port}."
      return 0
    fi
  fi

  return 1
}

# When :6379 is owned by a passworded system Redis we cannot reconfigure (no sudo),
# start a local passwordless broker on the next free port and point .env at it.
# Prefer restarting the port recorded in .env (usually 6380) so URLs stay stable.
start_redis_alt_port() {
  local preferred=""
  local alt_port
  preferred=$(get_redis_url_port)
  if [ -n "$preferred" ] && [ "$preferred" -ge 6380 ] 2>/dev/null && [ "$preferred" -le 6399 ] 2>/dev/null; then
    alt_port="$preferred"
  else
    alt_port="${REDIS_ALT_PORT:-6380}"
  fi

  # If preferred port is free, use it; if occupied but not pingable, try next ports.
  if redis_port_listening "$alt_port" && ! redis_ping "" "$alt_port"; then
    alt_port=$((alt_port + 1))
  fi
  while redis_port_listening "$alt_port" && ! redis_ping "" "$alt_port"; do
    alt_port=$((alt_port + 1))
    if [ "$alt_port" -gt 6399 ]; then
      vader_error "No free Redis port found between 6380-6399."
      return 1
    fi
  done

  if redis_ping "" "$alt_port"; then
    write_redis_env_urls "redis://localhost:${alt_port}/${REDIS_DB}"
    write_redis_pid "$alt_port"
    vader_success "Redis broker already up on port ${alt_port}."
    return 0
  fi

  mkdir -p "$REDIS_ALT_DIR" "$PIDS_DIR"
  vader_warn "Port $PORT is in use with auth we cannot match — starting passwordless broker on :${alt_port}."
  redis-server --daemonize yes --port "$alt_port" --bind 127.0.0.1 --dir "$REDIS_ALT_DIR" \
    --stop-writes-on-bgsave-error no --save "" >/dev/null 2>&1
  sleep 2
  if redis_ping "" "$alt_port"; then
    write_redis_env_urls "redis://localhost:${alt_port}/${REDIS_DB}"
    write_redis_pid "$alt_port"
    vader_success "Redis broker running on port ${alt_port} (passwordless, local-only, pidfile)."
    return 0
  fi
  vader_error "Failed to start passwordless Redis on port ${alt_port}."
  return 1
}

# ─── macOS (Homebrew) branch ─────────────────────────────────────────────────
# No apt / systemd / /etc/redis on macOS. Use Homebrew + brew services, which run
# Redis on 127.0.0.1:6379 passwordless — fine for local broker/cache use. This
# block fully handles macOS and exits; the Linux flow below never runs there.
if [ "$(uname -s)" = "Darwin" ]; then
  if ! redis_ping ""; then
    if ! command_exists redis-server; then
      if command_exists brew; then
        vader_info "Installing Redis via Homebrew..."
        brew install redis >/dev/null 2>&1 || { vader_error "brew install redis failed. Run: brew install redis"; exit 1; }
      else
        vader_error "Homebrew not found. Install from https://brew.sh, then: brew install redis"
        exit 1
      fi
    fi
    vader_info "Starting Redis via brew services..."
    brew services start redis >/dev/null 2>&1
    sleep 2
    if ! redis_ping ""; then
      # brew services can lag or be unavailable — fall back to a direct daemon.
      redis-server --daemonize yes --port "$PORT" --bind 127.0.0.1 --save "" >/dev/null 2>&1
      sleep 2
    fi
  fi

  if redis_ping ""; then
    vader_success "Redis running on port $PORT."
  else
    vader_error "Redis failed to start. Run: brew services start redis"
    exit 1
  fi

  # Point Celery/Redis at the local passwordless broker (idempotent rewrite).
  if [ -f "$ENV_FILE" ]; then
    NEW_URL="redis://localhost:${PORT}/${REDIS_DB}"
    if ! grep -q "^REDIS_URL=${NEW_URL}$" "$ENV_FILE"; then
      _tmp="${ENV_FILE}.tmp.$$"
      grep -vE '^(REDIS_URL|CELERY_BROKER_URL|CELERY_RESULT_BACKEND)=' "$ENV_FILE" > "$_tmp"
      {
        echo "REDIS_URL=${NEW_URL}"
        echo "CELERY_BROKER_URL=${NEW_URL}"
        echo "CELERY_RESULT_BACKEND=${NEW_URL}"
      } >> "$_tmp"
      mv "$_tmp" "$ENV_FILE"
      chmod 600 "$ENV_FILE"
      vader_success "Redis broker URL set to local passwordless (.env updated)."
    fi
  fi
  exit 0
fi

# ─── Step 1: Check if Redis is already running and reachable ─────────────────
# Critical: always leave REDIS_URL / CELERY_* pointing at a broker that PING's.
# A prior alt-port (6380) written to .env with a dead process used to pass
# "Redis already running" via :6379 while Celery still targeted :6380.

REDIS_PASS=$(get_redis_password)
ENV_REDIS_PORT=$(get_redis_url_port)
clear_redis_pid_if_dead

if command_exists redis-cli; then
  # Stale .env port (e.g. 6380 down) but default PORT alive → rewrite .env now.
  if sync_env_to_reachable_broker "$REDIS_PASS"; then
    # Optional: fix dir/save on default PORT when we own the password path.
    if redis_ping "$REDIS_PASS" "$PORT" || redis_ping "" "$PORT"; then
      AUTH_ARGS=()
      if [ -n "$REDIS_PASS" ] && redis_ping "$REDIS_PASS" "$PORT"; then
        AUTH_ARGS=(-a "$REDIS_PASS" --no-auth-warning)
      fi
      if [ "${#AUTH_ARGS[@]}" -gt 0 ] || redis_ping "" "$PORT"; then
        CURRENT_DIR=$(redis-cli "${AUTH_ARGS[@]}" -p "$PORT" CONFIG GET dir 2>/dev/null | tail -1)
        if [ "$CURRENT_DIR" = "/" ] || [ -z "$CURRENT_DIR" ]; then
          redis-cli "${AUTH_ARGS[@]}" -p "$PORT" CONFIG SET save "" >/dev/null 2>&1
          redis-cli "${AUTH_ARGS[@]}" -p "$PORT" CONFIG SET stop-writes-on-bgsave-error no >/dev/null 2>&1
        else
          redis-cli "${AUTH_ARGS[@]}" -p "$PORT" CONFIG SET stop-writes-on-bgsave-error no >/dev/null 2>&1
        fi
      fi
    fi
    exit 0
  fi

  # Env alt port dead — try restart sidecar on that port before broader install path.
  if [ -n "$ENV_REDIS_PORT" ] && [ "$ENV_REDIS_PORT" != "$PORT" ]; then
    vader_warn "Broker in .env (:$ENV_REDIS_PORT) is down — restarting local passwordless broker."
    start_redis_alt_port && exit 0
  fi

  # Port is listening but our ping failed — common when system Redis has a password
  # we no longer have in .env (e.g. after .env was trimmed) and we lack sudo to
  # rewrite /etc/redis/redis.conf.
  if redis_port_listening "$PORT"; then
    if redis_auth_required "$PORT" && [ -z "$REDIS_PASS" ]; then
      vader_warn "Redis on port $PORT requires authentication but .env has no password."
      start_redis_alt_port && exit 0
      exit 1
    fi
    if redis_ping ""; then
      write_redis_env_urls "redis://localhost:${PORT}/${REDIS_DB}"
      vader_success "Redis already running on port $PORT (passwordless)."
      exit 0
    fi
    if [ -n "$REDIS_PASS" ]; then
      vader_warn "Redis is listening on port $PORT but .env credentials were rejected."
      start_redis_alt_port && exit 0
      exit 1
    fi
  fi
fi

# ─── Step 2: Ensure redis-server is installed ────────────────────────────────

if ! command_exists redis-server; then
  vader_info "redis-server not found. Installing Redis..."
  if command_exists apt-get; then
    sudo apt-get update -qq >/dev/null 2>&1
    if sudo apt-get install -y redis-server >/dev/null 2>&1; then
      vader_success "Redis installed."
    else
      vader_error "Failed to install Redis. Install manually: sudo apt-get install -y redis-server"
      exit 1
    fi
  else
    vader_error "redis-server not found and apt-get not available. Install Redis manually."
    exit 1
  fi
fi

# ─── Step 3: Provision Redis authentication if not already configured ────────

if [ -z "$REDIS_PASS" ]; then
  vader_info "Provisioning Redis authentication..."
  REDIS_PASS=$(openssl rand -hex 20)

  # Write Redis config
  if [ -w "$(dirname "$REDIS_CONF")" ] 2>/dev/null || sudo -n test -w "$(dirname "$REDIS_CONF")" 2>/dev/null; then
    sudo mkdir -p "$(dirname "$REDIS_CONF")"
    cat <<REDISEOF | sudo tee "$REDIS_CONF" >/dev/null
# Guaardvark Redis config — auto-generated by start_redis.sh
bind 127.0.0.1 ::1
port $PORT
requirepass $REDIS_PASS
daemonize no
dir /var/lib/redis
stop-writes-on-bgsave-error no
REDISEOF
    sudo mkdir -p /var/lib/redis 2>/dev/null
    sudo chown redis:redis /var/lib/redis 2>/dev/null
    sudo chmod 640 "$REDIS_CONF"
    sudo chown redis:redis "$REDIS_CONF" 2>/dev/null
    vader_success "Redis config written to $REDIS_CONF"
  else
    vader_warn "Cannot write $REDIS_CONF (no sudo). Redis will run without auth."
    if redis_port_listening "$PORT"; then
      vader_warn "Port $PORT already in use — will use an alternate local broker port."
      start_redis_alt_port && exit 0
      exit 1
    fi
    REDIS_PASS=""
  fi

  # Update .env with Redis credentials
  if [ -n "$REDIS_PASS" ] && [ -f "$ENV_FILE" ]; then
    REDIS_URL="redis://:${REDIS_PASS}@localhost:${PORT}/${REDIS_DB}"

    # Append or update each key
    for key in REDIS_URL CELERY_BROKER_URL CELERY_RESULT_BACKEND; do
      if grep -q "^${key}=" "$ENV_FILE"; then
        sed -i "s|^${key}=.*|${key}=${REDIS_URL}|" "$ENV_FILE"
      else
        echo "${key}=${REDIS_URL}" >> "$ENV_FILE"
      fi
    done

    chmod 600 "$ENV_FILE"
    vader_success "Redis credentials written to .env"
  fi
elif [ ! -f "$REDIS_CONF" ] || ! grep -q "^requirepass" "$REDIS_CONF" 2>/dev/null; then
  # Password exists in .env but Redis config is missing — recreate it
  vader_info "Restoring Redis config from .env credentials..."
  if sudo -n test -w "$(dirname "$REDIS_CONF")" 2>/dev/null || [ -w "$(dirname "$REDIS_CONF")" ]; then
    sudo mkdir -p "$(dirname "$REDIS_CONF")"
    cat <<REDISEOF | sudo tee "$REDIS_CONF" >/dev/null
# Guaardvark Redis config — auto-generated by start_redis.sh
bind 127.0.0.1 ::1
port $PORT
requirepass $REDIS_PASS
daemonize no
dir /var/lib/redis
stop-writes-on-bgsave-error no
REDISEOF
    sudo mkdir -p /var/lib/redis 2>/dev/null
    sudo chown redis:redis /var/lib/redis 2>/dev/null
    sudo chmod 640 "$REDIS_CONF"
    sudo chown redis:redis "$REDIS_CONF" 2>/dev/null
    vader_success "Redis config restored to $REDIS_CONF"
  fi
fi

# ─── Step 4: Start Redis service ─────────────────────────────────────────────

if command_exists systemctl; then
  vader_info "Attempting to start redis service via systemctl..."
  if systemctl --user start redis-server >/dev/null 2>&1 || sudo -n systemctl start redis-server >/dev/null 2>&1; then
    sleep 2
    if command_exists redis-cli && sync_env_to_reachable_broker "$REDIS_PASS"; then
      vader_success "Redis service started via systemctl."
      exit 0
    fi
  fi
fi

# ─── Step 5: Fallback - start redis-server directly ─────────────────────────

vader_info "Starting redis-server directly on port $PORT..."
if redis_port_listening "$PORT"; then
  vader_warn "Port $PORT is already in use."
  if redis_auth_required "$PORT" || [ -n "$REDIS_PASS" ]; then
    start_redis_alt_port && exit 0
  fi
  if redis_ping ""; then
    write_redis_env_urls "redis://localhost:${PORT}/${REDIS_DB}"
    vader_success "Redis already running on port $PORT."
    exit 0
  fi
  vader_error "Port $PORT is occupied and not reachable with current credentials."
  exit 1
fi
if [ -n "$REDIS_PASS" ]; then
  redis-server --daemonize yes --port "$PORT" --bind "127.0.0.1 ::1" --requirepass "$REDIS_PASS" --dir /tmp --stop-writes-on-bgsave-error no >/dev/null 2>&1
else
  redis-server --daemonize yes --port "$PORT" --dir /tmp --stop-writes-on-bgsave-error no >/dev/null 2>&1
fi
sleep 2
if command_exists redis-cli && redis_ping "$REDIS_PASS"; then
  if [ -n "$REDIS_PASS" ]; then
    write_redis_env_urls "redis://:${REDIS_PASS}@localhost:${PORT}/${REDIS_DB}"
  else
    write_redis_env_urls "redis://localhost:${PORT}/${REDIS_DB}"
  fi
  vader_success "redis-server started on port $PORT."
  exit 0
else
  # Last resort: sidecar when direct start on PORT failed.
  start_redis_alt_port && exit 0
  vader_error "Failed to start redis-server."
  exit 1
fi
