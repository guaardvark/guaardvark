#!/bin/bash
# Installation Health Check Script
# Verifies that all components are properly installed and running

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Status symbols
CHECK_MARK="${GREEN}✓${NC}"
CROSS_MARK="${RED}✗${NC}"
WARNING="${YELLOW}⚠${NC}"

echo -e "${BLUE}╔════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║   Guaardvark Installation Health Check            ║${NC}"
echo -e "${BLUE}╔════════════════════════════════════════════╗${NC}"
echo ""

# Get the script's directory and determine environment root
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
# If script is in a scripts/ subdirectory, go up one level to get environment root
if [ "$(basename "$SCRIPT_DIR")" = "scripts" ]; then
    ENV_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
else
    ENV_ROOT="$SCRIPT_DIR"
fi
cd "$ENV_ROOT" || exit 1

# Detect ports from .env file or running processes
FLASK_PORT=""
VITE_PORT=""

# Primary: Check .env file
if [ -f ".env" ]; then
    set -a
    source ".env" 2>/dev/null
    set +a
    FLASK_PORT=$FLASK_PORT
    VITE_PORT=$VITE_PORT
fi

# Fallback: Detect from running processes
if [ -z "$FLASK_PORT" ]; then
    flask_pid=$(pgrep -f "flask run.*--port" | head -1)
    if [ -n "$flask_pid" ]; then
        FLASK_PORT=$(lsof -p "$flask_pid" -a -i TCP -s TCP:LISTEN 2>/dev/null | grep -oP ':\K[0-9]+' | head -1)
    fi
fi

if [ -z "$VITE_PORT" ]; then
    vite_pid=$(pgrep -f "vite.*--port" | head -1)
    if [ -n "$vite_pid" ]; then
        VITE_PORT=$(lsof -p "$vite_pid" -a -i TCP -s TCP:LISTEN 2>/dev/null | grep -oP ':\K[0-9]+' | head -1)
    fi
fi

# Use defaults if still not found
FLASK_PORT=${FLASK_PORT:-5000}
VITE_PORT=${VITE_PORT:-5173}

# Track overall status
ERRORS=0
WARNINGS=0

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Function to check if a port is in use
port_in_use() {
    lsof -i :"$1" >/dev/null 2>&1
}

# Function to detect if a listening port belongs to another root
port_owned_elsewhere() {
    local port="$1"
    local owner_pid=""
    local owner_cwd=""
    if command_exists lsof; then
        owner_pid=$(lsof -i TCP:"$port" -sTCP:LISTEN -t 2>/dev/null | head -1)
        if [ -n "$owner_pid" ]; then
            owner_cwd=$(readlink -f "/proc/$owner_pid/cwd" 2>/dev/null)
        fi
    fi
    if [ -n "$owner_pid" ] && [ -n "$owner_cwd" ]; then
        case "$owner_cwd" in
            "$ENV_ROOT"/*) return 1 ;;
            *) return 0 ;;
        esac
    fi
    return 1
}

# Function to check HTTP endpoint
check_endpoint() {
    local url="$1"
    local timeout=5

    if command_exists curl; then
        response=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout $timeout "$url" 2>/dev/null)
        if [ "$response" = "200" ]; then
            return 0
        fi
    fi
    return 1
}

echo "1. Checking Required Software..."
echo "────────────────────────────────────────────"

# Check Python
if command_exists python3; then
    PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
    echo -e "  ${CHECK_MARK} Python installed: $PYTHON_VERSION"

    # Check if Python version is 3.10+
    PYTHON_MAJOR=$(echo $PYTHON_VERSION | cut -d. -f1)
    PYTHON_MINOR=$(echo $PYTHON_VERSION | cut -d. -f2)
    if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 10 ]); then
        echo -e "  ${WARNING} Python 3.10+ recommended (current: $PYTHON_VERSION)"
        WARNINGS=$((WARNINGS + 1))
    fi
else
    echo -e "  ${CROSS_MARK} Python not found"
    echo -e "     Install: https://www.python.org/downloads/"
    ERRORS=$((ERRORS + 1))
fi

# Check pip
if command_exists pip3 || command_exists pip; then
    echo -e "  ${CHECK_MARK} pip installed"
else
    echo -e "  ${CROSS_MARK} pip not found"
    ERRORS=$((ERRORS + 1))
fi

# Check Node.js
if command_exists node; then
    NODE_VERSION=$(node --version 2>&1)
    echo -e "  ${CHECK_MARK} Node.js installed: $NODE_VERSION"
else
    echo -e "  ${CROSS_MARK} Node.js not found"
    echo -e "     Install: https://nodejs.org/"
    ERRORS=$((ERRORS + 1))
fi

# Check npm
if command_exists npm; then
    NPM_VERSION=$(npm --version 2>&1)
    echo -e "  ${CHECK_MARK} npm installed: $NPM_VERSION"
else
    echo -e "  ${CROSS_MARK} npm not found"
    ERRORS=$((ERRORS + 1))
fi

# Check Redis
echo ""
if command_exists redis-server; then
    echo -e "  ${CHECK_MARK} redis-server installed"
else
    echo -e "  ${WARNING} redis-server not found (optional but recommended)"
    echo -e "     Install: sudo apt-get install redis-server (Ubuntu/Debian)"
    echo -e "              brew install redis (macOS)"
    WARNINGS=$((WARNINGS + 1))
fi

echo ""
echo "2. Checking Python Dependencies..."
echo "────────────────────────────────────────────"

# Check for virtual environment (environment isolation)
if [ -d "backend/venv" ]; then
    echo -e "  ${CHECK_MARK} Virtual environment exists (backend/venv)"
    VENV_ACTIVE=0
    if [ -n "$VIRTUAL_ENV" ]; then
        VENV_ACTIVE=1
        echo -e "  ${CHECK_MARK} Virtual environment is active"
    else
        echo -e "  ${WARNING} Virtual environment not activated (will check system Python)"
    fi
else
    echo -e "  ${WARNING} Virtual environment not found (backend/venv)"
    echo -e "     Run: python3 -m venv backend/venv"
    WARNINGS=$((WARNINGS + 1))
    VENV_ACTIVE=0
fi

# Check for requirements.txt in backend directory (environment-specific)
if [ -f "backend/requirements.txt" ]; then
    echo -e "  ${CHECK_MARK} backend/requirements.txt found (environment-specific)"

    # Check key packages (use venv Python if available)
    PYTHON_CMD="python3"
    if [ $VENV_ACTIVE -eq 1 ] && [ -f "backend/venv/bin/python" ]; then
        PYTHON_CMD="backend/venv/bin/python"
    elif [ -f "backend/venv/bin/python" ]; then
        PYTHON_CMD="backend/venv/bin/python"
    fi

    for package in "flask" "flask-cors" "celery" "redis" "llama_index"; do
        if $PYTHON_CMD -c "import $package" 2>/dev/null; then
            echo -e "  ${CHECK_MARK} $package installed"
        else
            echo -e "  ${CROSS_MARK} $package not installed"
            echo -e "     Run: source backend/venv/bin/activate && pip install -r backend/requirements.txt"
            ERRORS=$((ERRORS + 1))
        fi
    done
elif [ -f "requirements.txt" ]; then
    echo -e "  ${WARNING} requirements.txt found (root level, prefer backend/requirements.txt)"
    WARNINGS=$((WARNINGS + 1))
else
    echo -e "  ${WARNING} requirements.txt not found"
    WARNINGS=$((WARNINGS + 1))
fi

echo ""
echo "3. Checking Frontend Dependencies..."
echo "────────────────────────────────────────────"

if [ -d "frontend/node_modules" ]; then
    echo -e "  ${CHECK_MARK} node_modules directory exists"
else
    echo -e "  ${CROSS_MARK} node_modules not found"
    echo -e "     Run: cd frontend && npm install"
    ERRORS=$((ERRORS + 1))
fi

if [ -f "frontend/package.json" ]; then
    echo -e "  ${CHECK_MARK} package.json found"
else
    echo -e "  ${CROSS_MARK} package.json not found"
    ERRORS=$((ERRORS + 1))
fi

echo ""
echo "4. Checking Services Status..."
echo "────────────────────────────────────────────"

# Check Backend (using detected port)
if check_endpoint "http://127.0.0.1:${FLASK_PORT}/api/health"; then
    echo -e "  ${CHECK_MARK} Backend server running (http://127.0.0.1:${FLASK_PORT})"
else
    echo -e "  ${CROSS_MARK} Backend server NOT running"
    echo -e "     Expected port: ${FLASK_PORT}"
    echo -e "     Start: ./start.sh"
    ERRORS=$((ERRORS + 1))
fi

# Warn if backend port is owned by another root
if port_owned_elsewhere "$FLASK_PORT"; then
    echo -e "  ${WARNING} Backend port ${FLASK_PORT} is bound by a process outside ${ENV_ROOT}"
    WARNINGS=$((WARNINGS + 1))
fi

# Check Frontend (using detected port)
if port_in_use "$VITE_PORT"; then
    echo -e "  ${CHECK_MARK} Frontend dev server running (http://localhost:${VITE_PORT})"
else
    echo -e "  ${WARNING} Frontend dev server NOT running"
    echo -e "     Expected port: ${VITE_PORT}"
    echo -e "     Start: ./start.sh"
    WARNINGS=$((WARNINGS + 1))
fi

# Warn if frontend port is owned by another root
if port_owned_elsewhere "$VITE_PORT"; then
    echo -e "  ${WARNING} Frontend port ${VITE_PORT} is bound by a process outside ${ENV_ROOT}"
    WARNINGS=$((WARNINGS + 1))
fi

# Check Redis
if port_in_use 6379; then
    echo -e "  ${CHECK_MARK} Redis server running (localhost:6379)"
elif command_exists redis-cli; then
    if redis-cli ping >/dev/null 2>&1; then
        echo -e "  ${CHECK_MARK} Redis server running"
    else
        echo -e "  ${WARNING} Redis server NOT running (optional)"
        echo -e "     Start: redis-server"
        WARNINGS=$((WARNINGS + 1))
    fi
else
    echo -e "  ${WARNING} Redis status unknown (optional)"
    WARNINGS=$((WARNINGS + 1))
fi

echo ""
echo "5. Checking Project Structure..."
echo "────────────────────────────────────────────"

for dir in "backend" "frontend" "data" "data/logs" "data/outputs" "data/storage"; do
    if [ -d "$dir" ]; then
        echo -e "  ${CHECK_MARK} $dir/ directory exists"
    else
        echo -e "  ${WARNING} $dir/ directory missing"
        WARNINGS=$((WARNINGS + 1))
    fi
done

# Check important files
for file in "backend/app.py" "frontend/src/main.jsx" "start.sh"; do
    if [ -f "$file" ]; then
        echo -e "  ${CHECK_MARK} $file exists"
    else
        echo -e "  ${CROSS_MARK} $file missing"
        ERRORS=$((ERRORS + 1))
    fi
done

echo ""
echo "════════════════════════════════════════════"
echo -e "${BLUE}Summary:${NC}"
echo "────────────────────────────────────────────"

if [ $ERRORS -eq 0 ] && [ $WARNINGS -eq 0 ]; then
    echo -e "${GREEN}✓ All checks passed!${NC}"
    echo ""
    echo "Your installation is ready to use."
    echo ""
    echo "Next steps:"
    echo "  1. If services aren't running, start them: ./start.sh"
    echo "  2. Open your browser: http://localhost:${VITE_PORT}"
    echo "  3. Verify backend health: http://127.0.0.1:${FLASK_PORT}/api/health"
    exit 0
elif [ $ERRORS -eq 0 ]; then
    echo -e "${YELLOW}⚠ ${WARNINGS} warning(s) found${NC}"
    echo ""
    echo "Your installation should work, but consider addressing the warnings above."
    exit 0
else
    echo -e "${RED}✗ ${ERRORS} error(s) found${NC}"
    if [ $WARNINGS -gt 0 ]; then
        echo -e "${YELLOW}⚠ ${WARNINGS} warning(s) found${NC}"
    fi
    echo ""
    echo "Please fix the errors above before running the application."
    echo ""
    echo "Quick fixes:"
    echo "  - Install Python dependencies: pip install -r requirements.txt"
    echo "  - Install frontend dependencies: cd frontend && npm install"
    echo "  - Start the backend: python backend/app.py"
    echo "  - Or use the startup script: ./start.sh"
    echo ""
    echo "For detailed instructions, see: INSTALLATION.md"
    exit 1
fi
