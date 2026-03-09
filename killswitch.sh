#!/usr/bin/env bash
# Guaardvark Kill Switch — Emergency full stop
# Works independently of Flask. Talks directly to PostgreSQL and OS signals.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export GUAARDVARK_ROOT="$SCRIPT_DIR"

echo "=== GUAARDVARK KILL SWITCH ACTIVATED ==="
echo "Timestamp: $(date -Iseconds)"

# 1. Set codebase_locked=true in database
echo "[1/5] Locking codebase in database..."
if [ -f "$GUAARDVARK_ROOT/.env" ]; then
    source "$GUAARDVARK_ROOT/.env"
fi
DB_URL="${DATABASE_URL:-postgresql://guaardvark:guaardvark@localhost:5432/guaardvark}"

psql "$DB_URL" -c "
    INSERT INTO system_settings (key, value) VALUES ('codebase_locked', 'true')
    ON CONFLICT (key) DO UPDATE SET value = 'true';
    INSERT INTO system_settings (key, value) VALUES ('self_improvement_enabled', 'false')
    ON CONFLICT (key) DO UPDATE SET value = 'false';
" 2>/dev/null && echo "  Database flags set." || echo "  WARNING: Could not update database."

# 2. Create filesystem lockfile
echo "[2/5] Creating filesystem lockfile..."
mkdir -p "$GUAARDVARK_ROOT/data"
echo "KILL_SWITCH_ACTIVATED=$(date -Iseconds)" > "$GUAARDVARK_ROOT/data/.codebase_lock"
echo "  Lockfile created at data/.codebase_lock"

# 3. Kill Celery workers
echo "[3/5] Stopping Celery workers..."
pkill -f "celery.*worker.*guaardvark" 2>/dev/null && echo "  Celery workers stopped." || echo "  No Celery workers found."

# 4. Kill agent executor threads (via PID files)
echo "[4/5] Stopping running agents..."
if [ -d "$GUAARDVARK_ROOT/pids" ]; then
    for pidfile in "$GUAARDVARK_ROOT/pids"/*.pid; do
        [ -f "$pidfile" ] || continue
        pid=$(cat "$pidfile")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null
            echo "  Killed process $pid ($(basename "$pidfile"))"
        fi
    done
fi

# 5. Optionally stop the entire application
if [ "$1" = "--full" ]; then
    echo "[5/5] Full shutdown requested..."
    if [ -f "$GUAARDVARK_ROOT/stop.sh" ]; then
        bash "$GUAARDVARK_ROOT/stop.sh"
    fi
else
    echo "[5/5] Application left running (self-improvement disabled, codebase locked)."
    echo "  Use './killswitch.sh --full' to also stop the application."
fi

echo ""
echo "=== KILL SWITCH COMPLETE ==="
echo "To unlock: Remove data/.codebase_lock and set codebase_locked=false in Settings."
