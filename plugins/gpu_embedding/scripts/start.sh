#!/bin/bash
# Start script for GPU Embedding Service plugin

set -e

# Get script directory and plugin root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$PLUGIN_ROOT/../.." && pwd)"

# Load plugin config
PLUGIN_CONFIG="$PLUGIN_ROOT/plugin.json"
if [ ! -f "$PLUGIN_CONFIG" ]; then
    echo "Error: plugin.json not found at $PLUGIN_CONFIG"
    exit 1
fi

# Extract port from plugin.json (default: 5002)
PORT=$(python3 -c "import json; f=open('$PLUGIN_CONFIG'); d=json.load(f); print(d.get('port', 5002))" 2>/dev/null || echo "5002")

# Check if service is already running
if lsof -Pi :$PORT -sTCP:LISTEN -t >/dev/null 2>&1 ; then
    echo "GPU Embedding Service is already running on port $PORT"
    exit 0
fi

# Set environment variables
export CELERY_WORKER_PROCESS=false  # This is NOT a Celery worker
export CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-0}  # Use GPU 0 by default

# Python path
export PYTHONPATH="$PROJECT_ROOT:$PYTHONPATH"

# Log file
LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/gpu_embedding_service.log"

echo "Starting GPU Embedding Service..."
echo "Plugin: $PLUGIN_ROOT"
echo "Port: $PORT"
echo "Log: $LOG_FILE"
echo "CUDA Device: $CUDA_VISIBLE_DEVICES"

# Start the service
cd "$PLUGIN_ROOT"
python3 -m plugins.gpu_embedding.service.app > "$LOG_FILE" 2>&1 &

# Save PID
echo $! > "$PROJECT_ROOT/pids/gpu_embedding_service.pid"

echo "GPU Embedding Service started (PID: $(cat $PROJECT_ROOT/pids/gpu_embedding_service.pid))"
echo "Check logs: tail -f $LOG_FILE"
echo "Check health: curl http://localhost:$PORT/health"

