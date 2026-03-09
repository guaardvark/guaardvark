#!/bin/bash
# start_postgres.sh - auto-provision a local PostgreSQL database for development

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

PG_USER="guaardvark"
PG_DB="guaardvark"
PG_HOST="localhost"
PG_PORT="5432"

# ─── Step 1: Ensure psql is installed ─────────────────────────────────────────

if ! command_exists psql; then
  vader_info "psql not found. Installing PostgreSQL..."
  sudo apt-get update -qq >/dev/null 2>&1
  if sudo apt-get install -y postgresql postgresql-contrib >/dev/null 2>&1; then
    vader_success "PostgreSQL installed."
  else
    vader_error "Failed to install PostgreSQL. Install manually: sudo apt-get install -y postgresql postgresql-contrib"
    exit 1
  fi
fi

# ─── Step 2: Ensure PostgreSQL service is running ─────────────────────────────

vader_info "Ensuring PostgreSQL service is running..."
if command_exists systemctl; then
  if ! systemctl is-active --quiet postgresql; then
    if sudo systemctl start postgresql >/dev/null 2>&1; then
      sleep 2
      vader_success "PostgreSQL service started via systemctl."
    else
      vader_error "Failed to start PostgreSQL service."
      exit 1
    fi
  else
    vader_success "PostgreSQL service already running."
  fi
else
  # Fallback: try pg_isready
  if ! pg_isready -h "$PG_HOST" -p "$PG_PORT" >/dev/null 2>&1; then
    vader_error "PostgreSQL is not running and systemctl is not available. Start PostgreSQL manually."
    exit 1
  else
    vader_success "PostgreSQL is running."
  fi
fi

# ─── Step 3: Check if DATABASE_URL already exists and works ───────────────────

if [ -f "$ENV_FILE" ]; then
  EXISTING_URL=$(grep -E '^DATABASE_URL=' "$ENV_FILE" | tail -1 | sed 's/^DATABASE_URL=//')
  if [ -n "$EXISTING_URL" ]; then
    vader_info "Found existing DATABASE_URL in .env, testing connection..."
    # Extract password from URL for verification
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
  # Database exists — ensure ownership
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
  # Remove any existing DATABASE_URL lines
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
  exit 1
fi
