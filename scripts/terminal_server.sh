#!/usr/bin/env bash
# terminal_server.sh — Manage the ttyd web terminal with Vader theme
# Usage: terminal_server.sh {start|stop|status|regenerate-credentials}

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PID_FILE="$PROJECT_ROOT/pids/terminal.pid"
AUTH_FILE="$PROJECT_ROOT/data/terminal/.terminal_auth"
CSS_FILE="$PROJECT_ROOT/data/terminal/vader-terminal.css"
LOG_FILE="$PROJECT_ROOT/logs/terminal.log"
TERMINAL_PORT="${TERMINAL_PORT:-7682}"

mkdir -p "$PROJECT_ROOT/pids" "$PROJECT_ROOT/data/terminal" "$PROJECT_ROOT/logs"

generate_credentials() {
    local user="gvk"
    local pass
    pass=$(openssl rand -base64 18 | tr -d '/+=' | head -c 16)
    echo "${user}:${pass}" > "$AUTH_FILE"
    chmod 600 "$AUTH_FILE"
    echo "$pass"
}

ensure_credentials() {
    if [ ! -f "$AUTH_FILE" ]; then
        generate_credentials > /dev/null
    fi
}

get_pid() {
    if [ -f "$PID_FILE" ]; then
        local pid
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo "$pid"
            return 0
        fi
        rm -f "$PID_FILE"
    fi
    return 1
}

cmd_start() {
    if pid=$(get_pid); then
        echo '{"status":"already_running","pid":'"$pid"',"port":'"$TERMINAL_PORT"'}'
        return 0
    fi

    if ! command -v ttyd &>/dev/null; then
        echo '{"status":"error","message":"ttyd is not installed"}' >&2
        return 1
    fi

    ensure_credentials
    local creds
    creds=$(cat "$AUTH_FILE")

    # Build custom index.html with Vader CSS if available
    local index_flag=""
    if [ -f "$CSS_FILE" ]; then
        local index_file="$PROJECT_ROOT/data/terminal/index.html"
        local css_content
        css_content=$(cat "$CSS_FILE")
        cat > "$index_file" << HTMLEOF
<!DOCTYPE html>
<html>
<head>
<style>${css_content}</style>
</head>
<body>
<div id="terminal-container"></div>
</body>
</html>
HTMLEOF
        index_flag="--index $index_file"
    fi

    nohup ttyd \
        --port "$TERMINAL_PORT" \
        --interface 0.0.0.0 \
        --credential "$creds" \
        --writable \
        --max-clients 3 \
        --client-option fontSize=15 \
        --client-option fontFamily="'Roboto Mono', 'Courier New', monospace" \
        --client-option theme='{"background":"#000000","foreground":"#ffffff","cursor":"#d32f2f","cursorAccent":"#000000","selectionBackground":"rgba(211,47,47,0.3)","black":"#000000","red":"#d32f2f","green":"#4caf50","yellow":"#ff9800","blue":"#42a5f5","magenta":"#ab47bc","cyan":"#26c6da","white":"#e0e0e0","brightBlack":"#424242","brightRed":"#f44336","brightGreen":"#66bb6a","brightYellow":"#ffb74d","brightBlue":"#64b5f6","brightMagenta":"#ce93d8","brightCyan":"#4dd0e1","brightWhite":"#ffffff"}' \
        $index_flag \
        bash -l \
        >> "$LOG_FILE" 2>&1 &

    local pid=$!
    echo "$pid" > "$PID_FILE"
    sleep 1

    if kill -0 "$pid" 2>/dev/null; then
        echo '{"status":"started","pid":'"$pid"',"port":'"$TERMINAL_PORT"'}'
    else
        rm -f "$PID_FILE"
        echo '{"status":"error","message":"ttyd exited immediately, check '"$LOG_FILE"'"}' >&2
        return 1
    fi
}

cmd_stop() {
    if pid=$(get_pid); then
        kill "$pid" 2>/dev/null
        sleep 1
        if kill -0 "$pid" 2>/dev/null; then
            kill -9 "$pid" 2>/dev/null
        fi
        rm -f "$PID_FILE"
        echo '{"status":"stopped"}'
    else
        echo '{"status":"not_running"}'
    fi
}

cmd_status() {
    if pid=$(get_pid); then
        echo '{"running":true,"pid":'"$pid"',"port":'"$TERMINAL_PORT"'}'
    else
        echo '{"running":false,"port":'"$TERMINAL_PORT"'}'
    fi
}

cmd_regenerate() {
    local was_running=false
    if get_pid &>/dev/null; then
        was_running=true
        cmd_stop > /dev/null
    fi

    local new_pass
    new_pass=$(generate_credentials)
    echo '{"status":"regenerated","username":"gvk","password":"'"$new_pass"'"}'

    if [ "$was_running" = true ]; then
        cmd_start > /dev/null
    fi
}

cmd_credentials() {
    ensure_credentials
    local creds
    creds=$(cat "$AUTH_FILE")
    local user="${creds%%:*}"
    echo '{"username":"'"$user"'"}'
}

case "${1:-}" in
    start)              cmd_start ;;
    stop)               cmd_stop ;;
    status)             cmd_status ;;
    regenerate-credentials) cmd_regenerate ;;
    credentials)        cmd_credentials ;;
    *)
        echo "Usage: $0 {start|stop|status|regenerate-credentials|credentials}" >&2
        exit 1
        ;;
esac
