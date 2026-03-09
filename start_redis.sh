#!/bin/bash
# start_redis.sh - auto-provision and start a local Redis server for development

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

command_exists() { command -v "$1" >/dev/null 2>&1; }

# ─── Step 1: Check if Redis is already running ────────────────────────────────

if command_exists redis-cli && redis-cli -p "$PORT" ping >/dev/null 2>&1; then
  vader_success "Redis already running on port $PORT."
  exit 0
fi

# ─── Step 2: Ensure redis-server is installed ─────────────────────────────────

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

# ─── Step 3: Start Redis service ──────────────────────────────────────────────

if command_exists systemctl; then
  vader_info "Attempting to start redis service via systemctl..."
  if systemctl --user start redis-server >/dev/null 2>&1 || sudo systemctl start redis-server >/dev/null 2>&1; then
    sleep 2
    if command_exists redis-cli && redis-cli -p "$PORT" ping >/dev/null 2>&1; then
      vader_success "Redis service started via systemctl."
      exit 0
    fi
  fi
fi

# ─── Step 4: Fallback - start redis-server directly ───────────────────────────

vader_info "Starting redis-server directly on port $PORT..."
redis-server --daemonize yes --port "$PORT" >/dev/null 2>&1
sleep 2
if command_exists redis-cli && redis-cli -p "$PORT" ping >/dev/null 2>&1; then
  vader_success "redis-server started on port $PORT."
  exit 0
else
  vader_error "Failed to start redis-server."
  exit 1
fi
