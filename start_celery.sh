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

check_workers() {
  local count=$(pgrep -f "celery.*worker" | wc -l)
  echo $count
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
  vader_success "Worker $worker_name started (PID: $pid)"
}

worker_count=$(check_workers)
if [ $worker_count -gt 0 ]; then
  vader_info "Celery workers already running ($worker_count processes)."
  vader_detail "Use ./stop.sh first to stop them, or kill them manually:"
  pgrep -f "celery.*worker" | while read pid; do
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

export PYTHONPATH="$SCRIPT_DIR:$PYTHONPATH"
export GUAARDVARK_ENHANCED_MODE=true
export GUAARDVARK_ROOT="$SCRIPT_DIR"
export CELERY_WORKER_MODE=true
export PYTORCH_CUDA_ALLOC_CONF="expandable_segments:False,max_split_size_mb:512"

ulimit -n 65535
vader_info "File descriptor limit set to: $(ulimit -n)"

vader_header "Enhanced Celery Worker Startup"
vader_info "Starting workers..."

start_worker "main" "health,default,indexing,generation" 1 1536000

start_worker "training" "training,training_gpu" 1 4096000

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
  pgrep -f "celery.*worker" | while read pid; do
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
