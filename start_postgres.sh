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

PG_USER="guaardvark"
PG_DB="guaardvark"
PG_HOST="localhost"
PG_PORT="5432"

# ─── Fast path: If PG is running and connection works, exit immediately ───────
# This is the common case after first-time setup — no sudo needed.

pg_is_running() {
  if command_exists systemctl; then
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
      vader_success "PostgreSQL ready (connection verified)."
      exit 0
    fi
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

if [ "$NEEDS_SUDO" = true ]; then
  echo -e "  ${VADER_WHITE_DIM}Guaardvark needs your password (one time only) to ${REASON}.${VADER_RESET}"
  echo -e "  ${VADER_GRAY}After this, future launches won't require a password.${VADER_RESET}"
  echo ""

  # Validate sudo access upfront so we get a clean prompt
  if ! sudo -v 2>/dev/null; then
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

if command_exists systemctl; then
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
  if ! pg_isready -h "$PG_HOST" -p "$PG_PORT" >/dev/null 2>&1; then
    vader_error "PostgreSQL is not running and systemctl is not available. Start PostgreSQL manually."
    exit 1
  else
    vader_success "PostgreSQL is running."
  fi
fi

# ─── Step 3: Check if existing connection works (may have been fixed by starting PG)

if [ -f "$ENV_FILE" ]; then
  EXISTING_URL=$(grep -E '^DATABASE_URL=' "$ENV_FILE" | tail -1 | sed 's/^DATABASE_URL=//')
  if [ -n "$EXISTING_URL" ]; then
    EXISTING_PASS=$(echo "$EXISTING_URL" | sed -n 's|.*://[^:]*:\([^@]*\)@.*|\1|p')
    if [ -n "$EXISTING_PASS" ] && PGPASSWORD="$EXISTING_PASS" psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" -c "SELECT 1;" >/dev/null 2>&1; then
      vader_success "PostgreSQL connection verified (existing DATABASE_URL works)."
      exit 0
    else
      vader_warn "Existing DATABASE_URL does not connect. Re-provisioning..."
    fi
  fi
fi

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
  exit 0
else
  vader_error "PostgreSQL connection verification failed."
  vader_warn "Check pg_hba.conf allows md5/scram-sha-256 auth for local connections."
  diagnose_postgres_error "connection failed"
  exit 1
fi
