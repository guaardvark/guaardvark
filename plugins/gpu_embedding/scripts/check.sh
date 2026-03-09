#!/bin/bash
# Health check script for GPU Embedding Service plugin

set -e

# Get script directory and plugin root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Load plugin config
PLUGIN_CONFIG="$PLUGIN_ROOT/plugin.json"
if [ ! -f "$PLUGIN_CONFIG" ]; then
    echo "Error: plugin.json not found"
    exit 1
fi

# Extract port from plugin.json (default: 5002)
PORT=$(python3 -c "import json; f=open('$PLUGIN_CONFIG'); d=json.load(f); print(d.get('port', 5002))" 2>/dev/null || echo "5002")

# Check if service is running
if ! lsof -Pi :$PORT -sTCP:LISTEN -t >/dev/null 2>&1 ; then
    echo "Service is not running on port $PORT"
    exit 1
fi

# Check health endpoint
HEALTH_URL="http://localhost:$PORT/health"
echo "Checking health at $HEALTH_URL..."

RESPONSE=$(curl -s -w "\n%{http_code}" "$HEALTH_URL" || echo -e "\n000")
HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" = "200" ]; then
    echo "Service is healthy"
    echo "$BODY" | python3 -m json.tool 2>/dev/null || echo "$BODY"
    exit 0
elif [ "$HTTP_CODE" = "503" ]; then
    echo "Service is degraded (model not loaded or GPU unavailable)"
    echo "$BODY" | python3 -m json.tool 2>/dev/null || echo "$BODY"
    exit 2
else
    echo "Service health check failed (HTTP $HTTP_CODE)"
    echo "$BODY"
    exit 1
fi

