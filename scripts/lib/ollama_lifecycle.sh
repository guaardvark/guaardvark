#!/usr/bin/env bash
# Ollama stop policy shared by stop.sh.
#
#   . scripts/lib/ollama_lifecycle.sh
#   mode=$(ollama_stop_mode "$STOP_ALL" "$KEEP_OLLAMA")   # keep | owned | all
#   stop_ollama "$mode" "$PIDS_DIR/ollama.pid"           # sets $ollama_killed
#
# Modes:
#   keep   touch nothing (--keep-ollama, or GUAARDVARK_OLLAMA_KEEP_RUNNING=1 in the environment)
#   owned  stop only the instance start.sh launched, named by the PID file (the default)
#   all    also stop user-owned `ollama serve` processes, the systemd service and any
#          unresponsive holder of port 11434 (--all)
#
# GUAARDVARK_STOP_DRY_RUN=1 reports every action without performing it. Output goes through
# `ollama_log` (vader_info when the caller defines it, plain echo otherwise).

ollama_log() {
    if command -v vader_info >/dev/null 2>&1; then vader_info "$1"; else echo "  · $1"; fi
}

ollama_stop_mode() {
    local flag_all="${1:-0}" flag_keep="${2:-0}"
    if [ "$flag_keep" = 1 ] || [ "${GUAARDVARK_OLLAMA_KEEP_RUNNING:-0}" = 1 ]; then
        echo keep
    elif [ "$flag_all" = 1 ]; then
        echo all
    else
        echo owned
    fi
}

_ollama_kill() {
    # TERM, then KILL if it is still there. Returns 0 when the process is gone.
    local pid="$1"
    if [ "${GUAARDVARK_STOP_DRY_RUN:-0}" = 1 ]; then
        ollama_log "[dry-run] would stop PID $pid"
        return 0
    fi
    kill -TERM "$pid" 2>/dev/null
    sleep 1
    if kill -0 "$pid" 2>/dev/null; then
        kill -KILL "$pid" 2>/dev/null
        sleep 1
    fi
    ! kill -0 "$pid" 2>/dev/null
}

stop_ollama() {
    local mode="$1" pid_file="$2"
    ollama_killed=0

    if [ "$mode" = keep ]; then
        ollama_log "Leaving Ollama running (keep-running policy)."
        return 0
    fi

    # 1. The instance start.sh launched, by PID file. Always ours to stop.
    if [ -f "$pid_file" ]; then
        local pid
        pid=$(cat "$pid_file" 2>/dev/null)
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            ollama_log "Stopping Ollama started by start.sh (PID: $pid)..."
            _ollama_kill "$pid" && ollama_killed=$((ollama_killed + 1))
        fi
        [ "${GUAARDVARK_STOP_DRY_RUN:-0}" = 1 ] || rm -f "$pid_file"
    fi

    if [ "$mode" = owned ]; then
        if [ "$ollama_killed" -eq 0 ]; then
            ollama_log "Ollama was not started by start.sh; leaving it as it is (./stop.sh --all stops every instance)."
        fi
        return 0
    fi

    # 2. --all: user-owned `ollama serve` processes (never another user's, never the systemd account's).
    # Anchored so a shell whose command line merely mentions "ollama serve" never matches.
    local current_user pid owner
    current_user=$(whoami)
    for pid in $(pgrep -f '(^|/)ollama serve$' 2>/dev/null); do
        [ "$pid" = "$$" ] || [ "$pid" = "$PPID" ] && continue
        owner=$(ps -o user= -p "$pid" 2>/dev/null | tr -d ' ')
        if [ "$owner" = "$current_user" ]; then
            ollama_log "Stopping user-owned ollama serve (PID: $pid)..."
            _ollama_kill "$pid" && ollama_killed=$((ollama_killed + 1))
        fi
    done

    # 3. --all: the systemd service, when sudo needs no password.
    if command -v systemctl >/dev/null 2>&1; then
        if [ "${GUAARDVARK_STOP_DRY_RUN:-0}" = 1 ]; then
            ollama_log "[dry-run] would run: sudo -n systemctl stop ollama"
        elif sudo -n systemctl stop ollama 2>/dev/null; then
            ollama_log "Stopped Ollama systemd service"
            ollama_killed=$((ollama_killed + 1))
        fi
    fi

    # 4. --all: whatever still holds 11434 without answering.
    if command -v lsof >/dev/null 2>&1; then
        for pid in $(lsof -i TCP:11434 -sTCP:LISTEN -t 2>/dev/null); do
            if ! curl -sf --max-time 2 http://127.0.0.1:11434/ >/dev/null 2>&1; then
                ollama_log "Stopping unresponsive process on port 11434 (PID: $pid)..."
                _ollama_kill "$pid" && ollama_killed=$((ollama_killed + 1))
            fi
        done
    fi
    return 0
}
