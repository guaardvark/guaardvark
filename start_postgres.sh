#!/bin/bash
# start_postgres.sh - auto-provision a local PostgreSQL database for development
#
# First run:  Installs PG, creates user/db, enables auto-start (needs sudo once)
# After that: Detects PG is running and connection works — no sudo needed

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"

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

command_exists() { command -v "$1" >/dev/null 2>&1; }

# Check for --skip-postgres flag
SKIP_POSTGRES=false
for arg in "$@"; do
  case "$arg" in
    --skip-postgres|--skip-pg) SKIP_POSTGRES=true ;;
  esac
done

# Allow environment variable override
if [ "$GUAARDVARK_SKIP_POSTGRES" = "1" ] || [ "$GUAARDVARK_SKIP_POSTGRES" = "true" ]; then
  SKIP_POSTGRES=true
fi

# Handle skip request early
if [ "$SKIP_POSTGRES" = true ]; then
  vader_warn "PostgreSQL setup skipped (--skip-postgres or GUAARDVARK_SKIP_POSTGRES=1)"
  vader_info "Make sure GUAARDVARK_DB_HOST points to your external database"
  vader_info "Or set DATABASE_URL directly in your .env file"
  exit 0
fi

# Enhanced error diagnostics
diagnose_postgres_error() {
  local error_msg="$1"
  
  # Check for port conflict
  if echo "$error_msg" | grep -qi "port"; then
    if command_exists netstat; then
      if netstat -tuln 2>/dev/null | grep -q ":${PG_PORT} "; then
        vader_error "Port ${PG_PORT} is already in use!"
        vader_info "Solutions:"
        vader_info "  1. Check if PostgreSQL is already running: pg_isready"
        vader_info "  2. Stop existing PostgreSQL: sudo systemctl stop postgresql"
        vader_info "  3. Change port in postgresql.conf (port = 5433)"
        vader_info "  4. Use --skip-postgres if you use external PostgreSQL"
        return 0
      fi
    fi
  fi
  
  # Check for permission issues
  if echo "$error_msg" | grep -qiE "permission|denied|owner"; then
    vader_error "PostgreSQL permission error detected!"
    vader_info "Solutions:"
    vader_info "  1. Check data directory: ls -la /var/lib/postgresql/"
    vader_info "  2. Fix ownership: sudo chown -R postgres:postgres /var/lib/postgresql/"
    vader_info "  3. Check pg_hba.conf for authentication settings"
    return 0
  fi
  
  # Check for service not running
  if echo "$error_msg" | grep -qiE "failed|error|could not connect"; then
    vader_error "PostgreSQL service failed to start!"
    vader_info "Diagnostic steps:"
    vader_info "  1. Check status: sudo systemctl status postgresql"
    vader_info "  2. View logs: sudo journalctl -u postgresql -n 50"
    vader_info "  3. Try manual start: pg_ctl -D /var/lib/postgresql/data start"
    vader_info "  4. Initialize if new: pg_ctl -D /var/lib/postgresql/data initdb"
    return 0
  fi
  
  return 1
}

# Stock first-run values. When DATABASE_URL is set — in the environment (start.sh
# sources .env) or in .env itself — its role, database, host and port replace
# them: the URL is the source of truth. A fork with its own role on the same
# box (a client vertical next to the engine) must never be "repaired" into the
# stock role: doing so rotated a shared role's password and pointed one install
# at the other's database (2026-08-29). Such a role is verified, never created
# or reset here — provision it yourself or run with --skip-postgres.
PG_USER="guaardvark"
PG_DB="guaardvark"
PG_HOST="localhost"
PG_PORT="5432"
STOCK_ROLE=1
# shellcheck source=scripts/lib/pg_url.sh
. "$SCRIPT_DIR/scripts/lib/pg_url.sh"
_configured_url="${DATABASE_URL:-}"
if [ -z "$_configured_url" ] && [ -f "$ENV_FILE" ]; then
  _configured_url=$(grep -E '^DATABASE_URL=' "$ENV_FILE" | tail -1 | sed 's/^DATABASE_URL=//; s/^"//; s/"$//')
fi
if [ -n "$_configured_url" ] && pg_url_parse "$_configured_url"; then
  PG_USER="$PG_URL_USER"
  PG_DB="$PG_URL_DB"
  [ -n "$PG_URL_HOST" ] && PG_HOST="$PG_URL_HOST"
  [ -n "$PG_URL_PORT" ] && PG_PORT="$PG_URL_PORT"
  if [ "$PG_USER" != "guaardvark" ] || [ "$PG_DB" != "guaardvark" ]; then
    STOCK_ROLE=0
  fi
fi

refuse_reprovision() {
  if pg_isready -h "$PG_HOST" -p "$PG_PORT" >/dev/null 2>&1; then
    vader_error "DATABASE_URL names role '${PG_USER}' and database '${PG_DB}', and the connection failed."
    vader_error "Not re-provisioning: that would reset that role's password and rewrite DATABASE_URL to the stock role."
    vader_info "Fix the password in .env, create the role and database yourself, or start with --skip-postgres."
  else
    vader_error "DATABASE_URL names role '${PG_USER}' and database '${PG_DB}', and PostgreSQL is not answering on ${PG_HOST}:${PG_PORT}."
    vader_info "Start it (for example: sudo systemctl start postgresql) and re-run, or start with --skip-postgres."
  fi
  exit 1
}

# ─── pgvector ─────────────────────────────────────────────────────────────────
# The knowledge index keeps its vectors in PostgreSQL through the `vector`
# extension. A stock `postgresql` install does not carry it (Debian/Ubuntu ship
# it as postgresql-<major>-pgvector) and CREATE EXTENSION needs a superuser,
# which the app role deliberately is not. So both steps live here, next to the
# role and database they serve, rather than surfacing as "extension vector is
# not available" the first time a document is indexed.

pg_superuser_psql() {  # psql as the bootstrap superuser; args pass through
  if [ "$(uname -s)" = "Darwin" ]; then
    psql "$@"
  else
    sudo -u postgres psql "$@"
  fi
}

# Readable by any role, so the no-sudo fast path can check without prompting.
vector_extension_ready_as_app() {  # $1 = app password
  [ "$(PGPASSWORD="$1" psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" -tAc \
      "SELECT 1 FROM pg_extension WHERE extname='vector';" 2>/dev/null)" = "1" ]
}

install_pgvector_package() {
  if [ "$(uname -s)" = "Darwin" ]; then
    if command_exists brew && brew install pgvector >/dev/null 2>&1; then
      vader_success "pgvector installed via Homebrew."
      return 0
    fi
    vader_warn "Could not install pgvector via Homebrew — run: brew install pgvector"
    return 1
  fi
  local major
  major=$(ls /usr/lib/postgresql/ 2>/dev/null | sort -n | tail -1)
  if [ -z "$major" ]; then
    vader_warn "Could not determine the PostgreSQL major version under /usr/lib/postgresql/."
    return 1
  fi
  vader_info "Installing pgvector (postgresql-${major}-pgvector)..."
  if sudo apt-get install -y "postgresql-${major}-pgvector" >/dev/null 2>&1; then
    vader_success "pgvector installed."
    return 0
  fi
  vader_warn "postgresql-${major}-pgvector is not in your apt sources."
  vader_info "If your release does not ship it, add the PostgreSQL apt repository first:"
  vader_info "  https://www.postgresql.org/download/linux/ubuntu/"
  vader_info "then: sudo apt-get install -y postgresql-${major}-pgvector"
  return 1
}

# Enable the extension on $PG_DB, installing the package first if it is missing.
# Never fatal: the app starts either way and the knowledge index reports the
# missing extension itself, but the warning here says exactly what to run.
ensure_vector_extension() {
  if pg_superuser_psql -d "$PG_DB" -c "CREATE EXTENSION IF NOT EXISTS vector;" >/dev/null 2>&1; then
    vader_success "pgvector extension ready on '${PG_DB}'."
    return 0
  fi
  install_pgvector_package || return 1
  if pg_superuser_psql -d "$PG_DB" -c "CREATE EXTENSION IF NOT EXISTS vector;" >/dev/null 2>&1; then
    vader_success "pgvector extension ready on '${PG_DB}'."
    return 0
  fi
  vader_warn "Could not enable the vector extension; the knowledge index will not work until it is. Run:"
  if [ "$(uname -s)" = "Darwin" ]; then
    vader_info "  psql -d ${PG_DB} -c \"CREATE EXTENSION IF NOT EXISTS vector;\""
  else
    vader_info "  sudo -u postgres psql -d ${PG_DB} -c \"CREATE EXTENSION IF NOT EXISTS vector;\""
  fi
  return 1
}

# ─── Fast path: If PG is running and connection works, exit immediately ───────
# This is the common case after first-time setup — no sudo needed.

# Returns 0 if systemd is the actual init system; 1 otherwise.
# `command_exists systemctl` is unreliable — Ubuntu containers, WSL1, and minimal
# images ship the binary without systemd running as PID 1, where every systemctl
# call returns "Failed to connect to bus".
is_systemd_running() {
  [ -d /run/systemd/system ]
}

pg_is_running() {
  if is_systemd_running && command_exists systemctl; then
    systemctl is-active --quiet postgresql
  elif command_exists pg_isready; then
    pg_isready -h "$PG_HOST" -p "$PG_PORT" >/dev/null 2>&1
  else
    return 1
  fi
}

if pg_is_running && [ -f "$ENV_FILE" ]; then
  EXISTING_URL=$(grep -E '^DATABASE_URL=' "$ENV_FILE" | tail -1 | sed 's/^DATABASE_URL=//')
  if [ -n "$EXISTING_URL" ]; then
    EXISTING_PASS=$(echo "$EXISTING_URL" | sed -n 's|.*://[^:]*:\([^@]*\)@.*|\1|p')
    if [ -n "$EXISTING_PASS" ] && PGPASSWORD="$EXISTING_PASS" psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" -c "SELECT 1;" >/dev/null 2>&1; then
      if vector_extension_ready_as_app "$EXISTING_PASS"; then
        vader_success "PostgreSQL ready (connection verified)."
      else
        # Installs that predate pgvector provisioning land here once.
        vader_info "PostgreSQL connection verified; pgvector extension missing — enabling it (needs sudo once)."
        ensure_vector_extension || true
      fi
      exit 0
    fi
  fi
fi

# ─── macOS (Homebrew) branch ─────────────────────────────────────────────────
# Homebrew Postgres has no `postgres` OS role and no systemd: the install user IS
# the DB superuser and connects over the local socket without a password. So we do
# NOT use apt-get / systemctl / `sudo -u postgres` here. Fully handles macOS + exits.
if [ "$(uname -s)" = "Darwin" ]; then
  echo ""
  echo -e "  ${VADER_WHITE}${VADER_BOLD}PostgreSQL Setup (macOS / Homebrew)${VADER_RESET}"
  echo -e "  ${VADER_GRAY}─────────────────────────────────────────${VADER_RESET}"

  if ! command_exists brew; then
    vader_error "Homebrew not found. Install from https://brew.sh, then re-run."
    exit 1
  fi

  # Use an already-installed postgresql formula (any version); else install @16.
  PG_FORMULA=$(brew list --formula 2>/dev/null | grep -E '^postgresql(@[0-9.]+)?$' | head -1)
  if [ -z "$PG_FORMULA" ]; then
    vader_info "Installing PostgreSQL via Homebrew (postgresql@16)..."
    if brew install postgresql@16 >/dev/null 2>&1; then
      PG_FORMULA="postgresql@16"
      vader_success "PostgreSQL installed."
    else
      vader_error "brew install postgresql@16 failed. Run it manually, then re-run."
      exit 1
    fi
  fi

  # Start via brew services (launchd) and wait for the socket. Idempotent.
  if ! pg_isready -h "$PG_HOST" -p "$PG_PORT" >/dev/null 2>&1; then
    vader_info "Starting PostgreSQL via brew services ($PG_FORMULA)..."
    brew services start "$PG_FORMULA" >/dev/null 2>&1
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      pg_isready -h "$PG_HOST" -p "$PG_PORT" >/dev/null 2>&1 && break
      sleep 1
    done
  fi
  if ! pg_isready -h "$PG_HOST" -p "$PG_PORT" >/dev/null 2>&1; then
    vader_error "PostgreSQL did not come up. Run: brew services start $PG_FORMULA"
    exit 1
  fi
  vader_success "PostgreSQL is running."

  # On Homebrew the current login user is the bootstrap superuser (socket/trust auth).
  [ "$STOCK_ROLE" = 1 ] || refuse_reprovision
  PG_PASS=$(openssl rand -base64 24 | tr -d '/+=' | head -c 32)
  vader_info "Creating/updating PostgreSQL role '${PG_USER}'..."
  if ! psql -d postgres -v ON_ERROR_STOP=1 -c "DO \$\$
BEGIN
  IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '${PG_USER}') THEN
    ALTER ROLE ${PG_USER} WITH LOGIN PASSWORD '${PG_PASS}';
  ELSE
    CREATE ROLE ${PG_USER} WITH LOGIN PASSWORD '${PG_PASS}';
  END IF;
END
\$\$;" >/dev/null 2>&1; then
    vader_error "Failed to create/update role '${PG_USER}'. Try connecting with: psql -d postgres"
    exit 1
  fi
  vader_success "PostgreSQL role '${PG_USER}' ready."

  # CREATE DATABASE can't run inside the DO block above — do it separately.
  DB_EXISTS=$(psql -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='${PG_DB}';" 2>/dev/null)
  if [ "$DB_EXISTS" = "1" ]; then
    psql -d postgres -c "ALTER DATABASE ${PG_DB} OWNER TO ${PG_USER};" >/dev/null 2>&1
    vader_success "Database '${PG_DB}' already exists (ownership verified)."
  else
    if psql -d postgres -c "CREATE DATABASE ${PG_DB} OWNER ${PG_USER};" >/dev/null 2>&1; then
      vader_success "Database '${PG_DB}' created."
    else
      vader_error "Failed to create database '${PG_DB}'."
      exit 1
    fi
  fi

  ensure_vector_extension || true

  # Write DATABASE_URL and verify a TCP connection as the app role.
  DATABASE_URL="postgresql://${PG_USER}:${PG_PASS}@${PG_HOST}:${PG_PORT}/${PG_DB}"
  if [ -f "$ENV_FILE" ]; then
    grep -v '^DATABASE_URL=' "$ENV_FILE" > "${ENV_FILE}.tmp" && mv "${ENV_FILE}.tmp" "$ENV_FILE"
  fi
  echo "DATABASE_URL=${DATABASE_URL}" >> "$ENV_FILE"
  chmod 600 "$ENV_FILE" 2>/dev/null

  if PGPASSWORD="$PG_PASS" psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" -c "SELECT 1;" >/dev/null 2>&1; then
    vader_success "PostgreSQL connection verified. Database is ready."
    exit 0
  else
    vader_error "Could not connect as '${PG_USER}' over TCP. Check Homebrew pg_hba.conf allows local connections."
    exit 1
  fi
fi

# ─── If we reach here, first-time setup or recovery is needed ─────────────────
# This requires sudo. Explain clearly why.

echo ""
echo -e "  ${VADER_WHITE}${VADER_BOLD}PostgreSQL Setup${VADER_RESET}"
echo -e "  ${VADER_GRAY}─────────────────────────────────────────${VADER_RESET}"

NEEDS_SUDO=false
REASON=""

if ! command_exists psql; then
  NEEDS_SUDO=true
  REASON="install PostgreSQL"
elif ! pg_is_running; then
  NEEDS_SUDO=true
  REASON="start the PostgreSQL service"
else
  # PG is running but connection failed — need to provision user/db
  NEEDS_SUDO=true
  REASON="create the database user and database"
fi

# A role this script did not create is only ever verified (fast path above).
# Refuse here, before any sudo prompt, rather than provisioning around it.
[ "$STOCK_ROLE" = 1 ] || refuse_reprovision

if [ "$NEEDS_SUDO" = true ]; then
  echo -e "  ${VADER_WHITE_DIM}Guaardvark needs your password (one time only) to ${REASON}.${VADER_RESET}"
  echo -e "  ${VADER_GRAY}After this, future launches won't require a password.${VADER_RESET}"
  echo ""

  # Validate sudo access upfront so we get a clean prompt
  # 'sudo -v' insists on a password even under NOPASSWD when another matching
  # rule (e.g. the %sudo group) requires one, and it needs a tty - so it fails in
  # any non-interactive run. Try the passwordless path first, exactly as
  # _linux_sudo_available() in scripts/platform/linux.sh does.
  if ! { sudo -n true 2>/dev/null || sudo -v; }; then
    vader_error "sudo authentication failed. Cannot proceed with PostgreSQL setup."
    vader_warn "Run with: sudo -v && ./start.sh"
    exit 1
  fi
fi

# Capture error from pg_isready for diagnostics
PG_ERROR_MSG=""
if ! pg_is_running; then
  PG_ERROR_MSG=$(pg_isready -h "$PG_HOST" -p "$PG_PORT" 2>&1 || true)
  diagnose_postgres_error "$PG_ERROR_MSG"
fi

# ─── Step 1: Ensure psql is installed ─────────────────────────────────────────

if ! command_exists psql; then
  vader_info "Installing PostgreSQL..."
  sudo apt-get update -qq >/dev/null 2>&1
  if sudo apt-get install -y postgresql postgresql-contrib >/dev/null 2>&1; then
    vader_success "PostgreSQL installed."
  else
    vader_error "Failed to install PostgreSQL. Install manually: sudo apt-get install -y postgresql postgresql-contrib"
    exit 1
  fi
fi

# ─── Step 2: Ensure PostgreSQL service is running + enabled on boot ───────────

if is_systemd_running && command_exists systemctl; then
  if ! systemctl is-active --quiet postgresql; then
    if sudo systemctl start postgresql >/dev/null 2>&1; then
      sleep 2
      vader_success "PostgreSQL service started."
    else
      vader_error "Failed to start PostgreSQL service."
      diagnose_postgres_error "service failed to start"
      exit 1
    fi
  else
    vader_success "PostgreSQL service already running."
  fi

  # Enable auto-start on boot so we never need sudo for this again
  if ! systemctl is-enabled --quiet postgresql 2>/dev/null; then
    if sudo systemctl enable postgresql >/dev/null 2>&1; then
      vader_success "PostgreSQL enabled to start on boot (no sudo needed next time)."
    fi
  fi
else
  # No systemd — typical for containers / WSL1 / minimal installs.
  # Use pg_ctlcluster directly if a cluster exists, else fall back to a connectivity check.
  if pg_isready -h "$PG_HOST" -p "$PG_PORT" >/dev/null 2>&1; then
    vader_success "PostgreSQL is running."
  else
    PG_CLUSTER_VERSION=$(ls /etc/postgresql/ 2>/dev/null | head -1)
    if [ -n "$PG_CLUSTER_VERSION" ] && command_exists pg_ctlcluster; then
      vader_info "Starting PostgreSQL cluster ${PG_CLUSTER_VERSION} via pg_ctlcluster (no systemd detected)..."
      if sudo pg_ctlcluster "$PG_CLUSTER_VERSION" main start >/dev/null 2>&1; then
        sleep 2
        if pg_isready -h "$PG_HOST" -p "$PG_PORT" >/dev/null 2>&1; then
          vader_success "PostgreSQL cluster ${PG_CLUSTER_VERSION} started."
        else
          vader_error "pg_ctlcluster reported success but pg_isready still fails. Check /var/log/postgresql/."
          exit 1
        fi
      else
        vader_error "Failed to start PostgreSQL via pg_ctlcluster ${PG_CLUSTER_VERSION} main."
        diagnose_postgres_error "service failed to start"
        exit 1
      fi
    else
      vader_error "PostgreSQL is not running and no systemd or pg_ctlcluster available. Start PostgreSQL manually."
      exit 1
    fi
  fi
fi

# ─── Step 3: Check if existing connection works (may have been fixed by starting PG)

if [ -f "$ENV_FILE" ]; then
  EXISTING_URL=$(grep -E '^DATABASE_URL=' "$ENV_FILE" | tail -1 | sed 's/^DATABASE_URL=//')
  if [ -n "$EXISTING_URL" ]; then
    EXISTING_PASS=$(echo "$EXISTING_URL" | sed -n 's|.*://[^:]*:\([^@]*\)@.*|\1|p')
    if [ -n "$EXISTING_PASS" ] && PGPASSWORD="$EXISTING_PASS" psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" -c "SELECT 1;" >/dev/null 2>&1; then
      vader_success "PostgreSQL connection verified (existing DATABASE_URL works)."
      vector_extension_ready_as_app "$EXISTING_PASS" || ensure_vector_extension || true
      exit 0
    else
      [ "$STOCK_ROLE" = 1 ] || refuse_reprovision
      vader_warn "Existing DATABASE_URL does not connect. Re-provisioning..."
    fi
  fi
fi
[ "$STOCK_ROLE" = 1 ] || refuse_reprovision

# ─── Step 4: Generate a random password ───────────────────────────────────────

PG_PASS=$(openssl rand -base64 24 | tr -d '/+=' | head -c 32)
vader_info "Generated random password for PostgreSQL user."

# ─── Step 5: Create or update PostgreSQL user (idempotent) ────────────────────

vader_info "Creating/updating PostgreSQL user '${PG_USER}'..."
sudo -u postgres psql -c "DO \$\$
BEGIN
  IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '${PG_USER}') THEN
    ALTER USER ${PG_USER} WITH PASSWORD '${PG_PASS}';
  ELSE
    CREATE USER ${PG_USER} WITH PASSWORD '${PG_PASS}';
  END IF;
END
\$\$;" >/dev/null 2>&1

if [ $? -eq 0 ]; then
  vader_success "PostgreSQL user '${PG_USER}' ready."
else
  vader_error "Failed to create/update PostgreSQL user '${PG_USER}'."
  exit 1
fi

# ─── Step 6: Create database if it doesn't exist (idempotent) ─────────────────

vader_info "Creating database '${PG_DB}' if it does not exist..."
DB_EXISTS=$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='${PG_DB}';" 2>/dev/null)

if [ "$DB_EXISTS" = "1" ]; then
  sudo -u postgres psql -c "ALTER DATABASE ${PG_DB} OWNER TO ${PG_USER};" >/dev/null 2>&1
  vader_success "Database '${PG_DB}' already exists (ownership verified)."
else
  if sudo -u postgres psql -c "CREATE DATABASE ${PG_DB} OWNER ${PG_USER};" >/dev/null 2>&1; then
    vader_success "Database '${PG_DB}' created."
  else
    vader_error "Failed to create database '${PG_DB}'."
    exit 1
  fi
fi

ensure_vector_extension || true

# ─── Step 7: Write DATABASE_URL to .env ───────────────────────────────────────

DATABASE_URL="postgresql://${PG_USER}:${PG_PASS}@${PG_HOST}:${PG_PORT}/${PG_DB}"

vader_info "Writing DATABASE_URL to .env..."
if [ -f "$ENV_FILE" ]; then
  grep -v '^DATABASE_URL=' "$ENV_FILE" > "${ENV_FILE}.tmp" && mv "${ENV_FILE}.tmp" "$ENV_FILE"
fi

echo "DATABASE_URL=${DATABASE_URL}" >> "$ENV_FILE"
vader_success "DATABASE_URL written to .env."

# ─── Step 8: Verify connection ────────────────────────────────────────────────

vader_info "Verifying PostgreSQL connection..."
if PGPASSWORD="$PG_PASS" psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" -c "SELECT 1;" >/dev/null 2>&1; then
  vader_success "PostgreSQL connection verified. Database is ready."

  # ─── Step 9: Re-sync the Claude Code 'postgres' MCP server (best-effort) ─────
  # The MCP server config caches the DB password in its connection string. When
  # we rotate the password above (Step 4), that cached copy goes stale and shows
  # up as "postgres: failed — Connection closed" in /doctor. Re-point it at the
  # freshly-written DATABASE_URL. Guarded so it's a no-op on machines without the
  # claude CLI or without a 'postgres' MCP server already registered.
  if command -v claude >/dev/null 2>&1 && (cd "$SCRIPT_DIR" && claude mcp get postgres >/dev/null 2>&1); then
    vader_info "Re-syncing Claude Code 'postgres' MCP server with new credentials..."
    if (cd "$SCRIPT_DIR" \
          && claude mcp remove postgres >/dev/null 2>&1 \
          && claude mcp add postgres -- npx -y @ahmetkca/mcp-server-postgres "$DATABASE_URL" >/dev/null 2>&1); then
      vader_success "Claude Code 'postgres' MCP server updated."
    else
      vader_warn "Could not auto-update the 'postgres' MCP server; run /doctor in Claude Code if it reports a failure."
    fi
  fi

  exit 0
else
  vader_error "PostgreSQL connection verification failed."
  vader_warn "Check pg_hba.conf allows md5/scram-sha-256 auth for local connections."
  diagnose_postgres_error "connection failed"
  exit 1
fi
