#!/bin/bash

VADER_RED="\033[38;5;196m"
VADER_RED_DARK="\033[38;5;88m"
VADER_RED_LIGHT="\033[38;5;203m"
VADER_GRAY="\033[38;5;244m"
VADER_GRAY_DARK="\033[38;5;238m"
VADER_WHITE="\033[38;5;255m"
VADER_WHITE_DIM="\033[38;5;250m"
VADER_RESET="\033[0m"
VADER_BOLD="\033[1m"

vader_header() { echo -e "\n${VADER_RED}${VADER_BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${VADER_RESET}\n${VADER_WHITE}${VADER_BOLD}  $1${VADER_RESET}\n${VADER_RED}${VADER_BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${VADER_RESET}"; }
vader_info() { echo -e "  ${VADER_GRAY}·${VADER_RESET} ${VADER_WHITE_DIM}$1${VADER_RESET}"; }
vader_success() { echo -e "  ${VADER_RED}✔${VADER_RESET} ${VADER_WHITE}$1${VADER_RESET}"; }
vader_warn() { echo -e "  ${VADER_RED_LIGHT}⚠${VADER_RESET} ${VADER_RED_LIGHT}$1${VADER_RESET}"; }
vader_error() { echo -e "  ${VADER_RED_DARK}✖${VADER_RESET} ${VADER_RED}$1${VADER_RESET}"; }
vader_detail() { echo -e "    ${VADER_GRAY}·${VADER_RESET} ${VADER_WHITE_DIM}$1${VADER_RESET}"; }
vader_section() { echo -e "\n${VADER_RED}${VADER_BOLD}► $1${VADER_RESET}"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
VENV_DIR="$BACKEND_DIR/venv"
LOGS_DIR="$SCRIPT_DIR/logs"

# Celery processes that belong to THIS checkout: matched on the real worker/beat
# CLI (never a parent shell that embeds the pattern in its argv) and then kept
# only when the process's working directory is under this repo. Two installs on
# one machine — the engine and a client vertical — otherwise see each other's
# workers as their own: this script reported "already running" and started
# nothing, and start.sh recorded the other install's PID.
own_celery_pids() {  # $1 = worker | beat
  local pid cwd
  for pid in $(pgrep -f "celery -A backend.celery_app.celery $1" 2>/dev/null); do
    cwd=""
    if [ -e "/proc/$pid/cwd" ]; then
      cwd=$(readlink -f "/proc/$pid/cwd" 2>/dev/null)
    elif command -v lsof >/dev/null 2>&1; then
      cwd=$(lsof -a -d cwd -p "$pid" -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)
    fi
    case "$cwd" in "$SCRIPT_DIR"|"$SCRIPT_DIR"/*) echo "$pid" ;; esac
  done
}

check_workers() {
  own_celery_pids worker | wc -l
}

check_beat() {
  own_celery_pids beat | wc -l
}

start_worker() {
  local worker_name=$1
  local queues=$2
  local concurrency=$3
  local max_memory=${4:-1024000}

  vader_info "Starting Celery worker: $worker_name (queues: $queues, concurrency: $concurrency)"

  nohup celery -A backend.celery_app.celery worker \
    --hostname="$worker_name@%h" \
    --queues="$queues" \
    --concurrency="$concurrency" \
    --max-memory-per-child="$max_memory" \
    --loglevel=info \
    --logfile="$LOGS_DIR/celery_${worker_name}.log" \
    >> "$LOGS_DIR/celery_${worker_name}.log" 2>&1 &

  local pid=$!
  echo $pid > "$SCRIPT_DIR/pids/celery_${worker_name}.pid"
  # nohup backgrounding only proves we launched it, not that it survived. Give it a
  # moment and verify the process is actually alive (catches immediate crashes:
  # bad broker URL, import error, port clash) before announcing success.
  sleep 2
  if kill -0 "$pid" 2>/dev/null; then
    vader_success "Worker $worker_name started (PID: $pid)"
  else
    vader_error "Worker $worker_name FAILED to start (died immediately) — see $LOGS_DIR/celery_${worker_name}.log"
    return 1
  fi
}

clean_beat_schedule() {
  # Linux shelve/dbm appends ".db" to the path Celery is given, so
  # --schedule=.../celerybeat-schedule.db creates celerybeat-schedule.db.db.
  # Only removing the bare path left a corrupt shelf that killed beat on every restart.
  rm -f "$SCRIPT_DIR/data/celerybeat-schedule.db" \
        "$SCRIPT_DIR/data/celerybeat-schedule.db.db" \
        "$SCRIPT_DIR/data/celerybeat-schedule.db.dir" \
        "$SCRIPT_DIR/data/celerybeat-schedule.db.bak" \
        "$SCRIPT_DIR/data/celerybeat-schedule" \
        "$SCRIPT_DIR/data/celerybeat-schedule.dir" \
        "$SCRIPT_DIR/data/celerybeat-schedule.bak" 2>/dev/null
  rm -f "$SCRIPT_DIR"/data/celerybeat-schedule.db* 2>/dev/null
}

wait_for_broker() {
  # Fail closed: workers that start against a dead REDIS_URL spam forever.
  local url port pass tries=0 max_tries=30
  url="${CELERY_BROKER_URL:-${REDIS_URL:-redis://localhost:6379/0}}"
  port=$(echo "$url" | sed -E 's|^redis://([^@]*@)?[^:]+:([0-9]+).*|\2|')
  pass=$(echo "$url" | sed -n 's|^redis://:\([^@]*\)@.*|\1|p')
  [ -n "$port" ] || port=6379
  vader_info "Waiting for Redis broker on :${port}..."
  while [ "$tries" -lt "$max_tries" ]; do
    if [ -n "$pass" ]; then
      if redis-cli -p "$port" -a "$pass" --no-auth-warning ping 2>/dev/null | grep -q PONG; then
        vader_success "Redis broker reachable on :${port}"
        return 0
      fi
    else
      if redis-cli -p "$port" ping 2>/dev/null | grep -q PONG; then
        vader_success "Redis broker reachable on :${port}"
        return 0
      fi
    fi
    tries=$((tries + 1))
    sleep 1
  done
  vader_error "Redis broker not reachable at ${url} after ${max_tries}s"
  vader_detail "Run ./start_redis.sh then re-check REDIS_URL in .env"
  return 1
}

start_beat() {
  # Celery beat scheduler — dispatches the periodic tasks declared in
  # backend.celery_app.beat_schedule (process_approved_drafts every 60s,
  # tick_reddit_outreach every 45 min, recon ticks, etc). Without this,
  # @shared_task definitions still register but nothing fires unless an
  # API endpoint or operator dispatches them manually.
  #
  # --schedule lives in data/ so it survives a clean checkout but is
  # gitignored. --pidfile makes stop.sh's pid sweep deterministic.
  vader_info "Starting Celery beat scheduler"

  clean_beat_schedule
  mkdir -p "$SCRIPT_DIR/pids" "$SCRIPT_DIR/data"

  # Use a path WITHOUT a trailing .db so shelve creates a single
  # celerybeat-schedule.db (not celerybeat-schedule.db.db).
  local schedule_path="$SCRIPT_DIR/data/celerybeat-schedule"

  nohup celery -A backend.celery_app.celery beat \
    --scheduler=backend.celery_beat_gates:GatedScheduler \
    --loglevel=info \
    --schedule="$schedule_path" \
    --pidfile="$SCRIPT_DIR/pids/celery_beat.pid" \
    --logfile="$LOGS_DIR/celery_beat.log" \
    >> "$LOGS_DIR/celery_beat.log" 2>&1 &

  # $! is the celery-beat process itself (nohup runs it directly, no subshell).
  # Beat also drops its own --pidfile shortly after; that write can race, so we
  # verify liveness via the PID we hold rather than trusting the file's existence.
  local beat_pid=$!
  sleep 2
  if kill -0 "$beat_pid" 2>/dev/null; then
    vader_success "Celery beat started (PID: $beat_pid)"
    return 0
  fi

  # One automatic recovery: wipe schedule artifacts and retry (dbm corruption).
  vader_warn "Celery beat died immediately — wiping schedule files and retrying once..."
  if grep -qE '_dbm\.error|cannot add item to database|shelve' "$LOGS_DIR/celery_beat.log" 2>/dev/null; then
    vader_detail "Detected schedule/dbm error in celery_beat.log"
  fi
  clean_beat_schedule
  nohup celery -A backend.celery_app.celery beat \
    --scheduler=backend.celery_beat_gates:GatedScheduler \
    --loglevel=info \
    --schedule="$schedule_path" \
    --pidfile="$SCRIPT_DIR/pids/celery_beat.pid" \
    --logfile="$LOGS_DIR/celery_beat.log" \
    >> "$LOGS_DIR/celery_beat.log" 2>&1 &
  beat_pid=$!
  sleep 2
  if kill -0 "$beat_pid" 2>/dev/null; then
    vader_success "Celery beat started after schedule reset (PID: $beat_pid)"
    return 0
  fi
  vader_error "Celery beat FAILED to start (corrupt schedule? import error?) — see $LOGS_DIR/celery_beat.log"
  return 1
}

worker_count=$(check_workers)
if [ $worker_count -gt 0 ]; then
  vader_info "Celery workers already running ($worker_count processes)."
  vader_detail "Use ./stop.sh first to stop them, or kill them manually:"
  own_celery_pids worker | while read pid; do
    vader_detail "PID $pid: $(ps -p $pid -o command= | cut -c1-80)"
  done
  exit 0
fi

if [ -f "$VENV_DIR/bin/activate" ]; then
  source "$VENV_DIR/bin/activate"
else
  vader_error "Virtualenv not found at $VENV_DIR. Run ./setup_dev_env.sh first."
  exit 1
fi

cd "$SCRIPT_DIR" || exit 1

mkdir -p "$LOGS_DIR"
mkdir -p "$SCRIPT_DIR/pids"

# Load .env so workers get DATABASE_URL / REDIS_URL / CELERY_* etc. Launched bare
# (not via start.sh) this script otherwise leaves workers on the default DB
# password and every task dies with 'password authentication failed'. The
# explicit exports below still win over anything in .env.
if [ -f "$SCRIPT_DIR/.env" ]; then
  set -a
  . "$SCRIPT_DIR/.env"
  set +a
fi

export PYTHONPATH="$SCRIPT_DIR:$PYTHONPATH"
export GUAARDVARK_ENHANCED_MODE=true
export GUAARDVARK_ROOT="$SCRIPT_DIR"
export CELERY_WORKER_MODE=true
# Must match start.sh: both processes share the card and the allocator policy.
export PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True,max_split_size_mb:512,garbage_collection_threshold:0.8"

ulimit -n 65535
vader_info "File descriptor limit set to: $(ulimit -n)"

vader_header "Enhanced Celery Worker Startup"

# Block until REDIS_URL / CELERY_BROKER_URL answers PING — otherwise workers
# boot into an infinite "Cannot connect to redis://…Connection refused" loop.
if ! wait_for_broker; then
  exit 1
fi

vader_info "Starting workers..."

# SINGLE-GPU INVARIANT: all GPU-bearing queues live on ONE worker so the in-process
# JobOperationGate (a 1-slot mutex that can't arbitrate across worker PIDs) is authoritative.
# Previously `generation`/`default` (video renders) ran on main while `training_gpu` (LoRA)
# ran on the training worker — two separate PIDs could each launch a GPU task and OOM the
# 16GB card. training_gpu now rides on main; the training worker keeps CPU-only `training`.
# main's max-memory-per-child bumped to 4GB since it now carries the heavy GPU/LoRA work.
start_worker "main" "health,default,indexing,generation,training_gpu" 1 4096000

start_worker "training" "training" 1 4096000

start_beat

vader_info "Waiting for workers to initialize..."
sleep 3

vader_section "Worker Status"
worker_count=$(check_workers)
if [ $worker_count -eq 0 ]; then
  vader_error "No Celery workers started successfully!"
  vader_detail "Check logs in $LOGS_DIR/celery_*.log"
  exit 1
else
  vader_success "$worker_count Celery workers running (concurrency=1 for race condition test)"
  
  vader_info "Worker processes:"
  own_celery_pids worker | while read pid; do
    vader_detail "PID $pid: $(ps -p $pid -o command= | cut -c1-100)"
  done
  
  vader_info "Log files:"
  for logfile in "$LOGS_DIR"/celery_*.log; do
    if [ -f "$logfile" ]; then
      vader_detail "$logfile"
    fi
  done
  
  vader_info "To stop workers: ./stop.sh"
  vader_info "To monitor: tail -f $LOGS_DIR/celery_*.log"
fi

deactivate
