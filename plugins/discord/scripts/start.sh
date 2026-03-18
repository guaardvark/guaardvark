#!/bin/bash
# Start Guaardvark Discord Bot
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$PLUGIN_ROOT/../.." && pwd)"
HEALTH_PORT=8200

# Load env
if [ -f "$PROJECT_ROOT/.env" ]; then
    set -a; source "$PROJECT_ROOT/.env"; set +a
fi
export GUAARDVARK_ROOT="$PROJECT_ROOT"

# Check token
if [ -z "$DISCORD_BOT_TOKEN" ]; then
    echo "Error: DISCORD_BOT_TOKEN not set"
    echo "Export it: export DISCORD_BOT_TOKEN=your_token"
    exit 1
fi

# Check if already running
PID_FILE="$PROJECT_ROOT/pids/discord_bot.pid"
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "Discord bot already running (PID: $OLD_PID)"
        exit 0
    fi
    rm -f "$PID_FILE"
fi

# Check port is free
if lsof -Pi :$HEALTH_PORT -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "Error: Port $HEALTH_PORT is already in use"
    exit 1
fi

# Check backend health
FLASK_PORT="${FLASK_PORT:-5002}"
if ! curl -sf "http://localhost:${FLASK_PORT}/api/health" >/dev/null 2>&1; then
    echo "Warning: Backend not reachable at localhost:${FLASK_PORT} (bot will start anyway)"
fi

# Setup venv if needed
if [ -d "$PLUGIN_ROOT/venv" ]; then
    source "$PLUGIN_ROOT/venv/bin/activate"
elif [ -d "$PROJECT_ROOT/backend/venv" ]; then
    source "$PROJECT_ROOT/backend/venv/bin/activate"
fi

# Install requirements
pip install -q -r "$PLUGIN_ROOT/requirements.txt" 2>/dev/null || true

# Log file
LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/discord_bot.log"

echo "Starting Discord Bot..."
echo "Plugin dir: $PLUGIN_ROOT"
echo "Health port: $HEALTH_PORT"
echo "Log: $LOG_FILE"

# Start bot with PYTHONPATH set for relative imports
cd "$PLUGIN_ROOT"
PYTHONPATH="$PLUGIN_ROOT:$PYTHONPATH" python -m bot >> "$LOG_FILE" 2>&1 &

# Save PID
PID_DIR="$PROJECT_ROOT/pids"
mkdir -p "$PID_DIR"
echo $! > "$PID_DIR/discord_bot.pid"
echo "Discord Bot started (PID: $(cat $PID_DIR/discord_bot.pid))"

# Wait for health endpoint
echo "Waiting for health endpoint on port $HEALTH_PORT..."
for i in $(seq 1 30); do
    if curl -sf "http://localhost:$HEALTH_PORT/health" >/dev/null 2>&1; then
        echo "Discord Bot health endpoint ready"
        exit 0
    fi
    sleep 1
done

echo "Warning: Health endpoint not responsive after 30s (bot may still be connecting to Discord)"
exit 0
