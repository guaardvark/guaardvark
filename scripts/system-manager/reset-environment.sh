#!/bin/bash
# reset-environment.sh - Reset and regenerate environment for this folder
# Use this when you need to completely reset a folder's configuration

# Get the script's directory and determine environment root
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
# If script is in a scripts/ subdirectory, go up one level to get environment root
if [ "$(basename "$SCRIPT_DIR")" = "scripts" ]; then
    ENV_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
else
    ENV_ROOT="$SCRIPT_DIR"
fi
FOLDER_NAME="$(basename "$ENV_ROOT")"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}╔════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║   Environment Reset: $FOLDER_NAME${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════╝${NC}"
echo ""

# Parse arguments
FULL_RESET=0
KEEP_VENV=0
WIPE_DATA=0

while [[ $# -gt 0 ]]; do
    case $1 in
        --full)
            FULL_RESET=1
            shift
            ;;
        --keep-venv)
            KEEP_VENV=1
            shift
            ;;
        --wipe-data)
            WIPE_DATA=1
            shift
            ;;
        -h|--help)
            cat << 'EOF'
Usage: ./reset-environment.sh [OPTIONS]

Reset the environment configuration for this folder.

OPTIONS:
    --full          Full reset (removes venv and cache, preserves important data)
    --keep-venv     Keep virtual environment (faster reset)
    --wipe-data     Remove ALL data including database, uploads, outputs (requires confirmation)
    -h, --help      Show this help message

EXAMPLES:
    # Quick reset (keeps venv and all important data, only clears cache)
    ./reset-environment.sh

    # Full reset (removes venv and cache, preserves database/uploads/outputs)
    ./reset-environment.sh --full

    # Reset config but keep venv (fast)
    ./reset-environment.sh --keep-venv

    # Complete wipe including all data (dangerous - requires confirmation)
    ./reset-environment.sh --wipe-data

WHAT GETS RESET (default):
    - Configuration files (.env, .envrc)
    - Port assignments (new ports assigned)
    - PID files and locks
    - Log files
    - Cache files (data/cache/)
    - Running services (stopped)

WHAT IS PRESERVED (default):
    - Database files (data/database/)
    - User uploads (data/uploads/)
    - Generated outputs (data/outputs/)
    - Logos and system files (data/logos/, data/system/)
    - AI models (data/models/)
    - Context data (data/context/)

OPTIONAL (with --full):
    - Virtual environment (backend/venv) - removed unless --keep-venv

OPTIONAL (with --wipe-data):
    - ALL data directory contents - removed with explicit confirmation

EOF
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Confirmation for wipe-data
if [ $WIPE_DATA -eq 1 ]; then
    echo -e "${RED}⚠️  DANGER: WIPE DATA MODE${NC}"
    echo "This will PERMANENTLY REMOVE:"
    echo "  - ALL database files (data/database/)"
    echo "  - ALL user uploads (data/uploads/)"
    echo "  - ALL generated outputs (data/outputs/)"
    echo "  - ALL logos and system files (data/logos/, data/system/)"
    echo "  - ALL AI models (data/models/)"
    echo "  - ALL context data (data/context/)"
    echo "  - ALL cache files (data/cache/)"
    echo ""
    echo -e "${RED}This action CANNOT be undone!${NC}"
    echo ""
    read -p "Type 'yes' to confirm complete data wipe: " confirm
    if [ "$confirm" != "yes" ]; then
        echo "Aborted. Data preserved."
        exit 0
    fi
fi

# Confirmation for full reset
if [ $FULL_RESET -eq 1 ]; then
    echo -e "${YELLOW}⚠️  FULL RESET MODE${NC}"
    echo "This will remove:"
    echo "  - Configuration files"
    echo "  - Virtual environment (unless --keep-venv)"
    echo "  - All logs and caches"
    echo ""
    echo "This will PRESERVE:"
    echo "  - Database files (data/database/)"
    echo "  - User uploads (data/uploads/)"
    echo "  - Generated outputs (data/outputs/)"
    echo "  - All other important data"
    echo ""
    read -p "Are you sure? (yes/no): " confirm
    if [ "$confirm" != "yes" ]; then
        echo "Aborted."
        exit 0
    fi
else
    echo -e "${BLUE}Quick reset mode${NC}"
    echo "This will:"
    echo "  ✓ Stop all running services"
    echo "  ✓ Remove configuration files (.env, .envrc)"
    echo "  ✓ Clear logs and cache files"
    if [ $KEEP_VENV -eq 0 ]; then
        echo "  ✓ Remove virtual environment"
    else
        echo "  ○ Keep virtual environment"
    fi
    echo ""
    echo "This will PRESERVE:"
    echo "  ✓ Database files (data/database/)"
    echo "  ✓ User uploads (data/uploads/)"
    echo "  ✓ Generated outputs (data/outputs/)"
    echo "  ✓ Logos and system files (data/logos/, data/system/)"
    echo "  ✓ AI models (data/models/)"
    echo "  ✓ Context data (data/context/)"
    echo ""
fi

# Step 1: Stop all running services
echo -e "${BLUE}[1/6]${NC} Stopping services..."
if [ -f "$ENV_ROOT/stop.sh" ]; then
    "$ENV_ROOT/stop.sh" 2>/dev/null || true
fi

# Kill any processes using the old ports
# Check .env first (primary), then .envrc (secondary)
if [ -f "$ENV_ROOT/.env" ]; then
    set -a
    source "$ENV_ROOT/.env" 2>/dev/null || true
    set +a
    if [ -n "$FLASK_PORT" ]; then
        lsof -ti :$FLASK_PORT | xargs kill -9 2>/dev/null || true
    fi
    if [ -n "$VITE_PORT" ]; then
        lsof -ti :$VITE_PORT | xargs kill -9 2>/dev/null || true
    fi
elif [ -f "$ENV_ROOT/.envrc" ]; then
    source "$ENV_ROOT/.envrc" 2>/dev/null || true
    if [ -n "$FLASK_PORT" ]; then
        lsof -ti :$FLASK_PORT | xargs kill -9 2>/dev/null || true
    fi
    if [ -n "$VITE_PORT" ]; then
        lsof -ti :$VITE_PORT | xargs kill -9 2>/dev/null || true
    fi
fi

# Step 2: Remove configuration files
echo -e "${BLUE}[2/6]${NC} Removing configuration files..."
rm -f "$ENV_ROOT/.envrc"
rm -f "$ENV_ROOT/.env"
rm -f "$ENV_ROOT/frontend/.env.local"

# Step 3: Clean up PIDs and locks
echo -e "${BLUE}[3/6]${NC} Cleaning PIDs and locks..."
rm -rf "$ENV_ROOT/pids"
rm -f "$ENV_ROOT/"*.pid

# Step 4: Clean logs and caches
echo -e "${BLUE}[4/6]${NC} Cleaning logs and caches..."
rm -rf "$ENV_ROOT/logs"
if [ -d "$ENV_ROOT/data/cache" ]; then
    rm -rf "$ENV_ROOT/data/cache"
    echo -e "  ${GREEN}✓${NC} Removed cache directory"
fi
rm -f "$ENV_ROOT/backend/"*.log
rm -f "$ENV_ROOT/"*.log

# Step 5: Virtual environment (optional)
if [ $FULL_RESET -eq 1 ] || [ $KEEP_VENV -eq 0 ]; then
    echo -e "${BLUE}[5/6]${NC} Removing virtual environment..."
    rm -rf "$ENV_ROOT/backend/venv"
    echo -e "  ${GREEN}✓${NC} Removed virtual environment"
else
    echo -e "${BLUE}[5/6]${NC} Keeping virtual environment..."
    echo -e "  ${GREEN}✓${NC} Virtual environment preserved"
fi

# Step 6: Intelligent data directory handling
echo -e "${BLUE}[6/6]${NC} Processing data directory..."

if [ $WIPE_DATA -eq 1 ]; then
    # Complete wipe - remove everything
    if [ -d "$ENV_ROOT/data" ]; then
        echo -e "  ${RED}⚠️  WIPING ALL DATA${NC}"
        rm -rf "$ENV_ROOT/data"
        echo -e "  ${RED}✗${NC} All data removed (database, uploads, outputs, etc.)"
    else
        echo -e "  ${YELLOW}○${NC} No data directory found"
    fi
else
    # Intelligent cleanup - preserve important data, only remove cache/temp
    if [ -d "$ENV_ROOT/data" ]; then
        echo -e "  ${GREEN}✓${NC} Preserving important data:"
        
        # Preserve critical directories
        for preserve_dir in "database" "logos" "system" "uploads" "models" "context" "outputs"; do
            if [ -d "$ENV_ROOT/data/$preserve_dir" ]; then
                echo -e "    ${GREEN}✓${NC} Preserved data/$preserve_dir/"
            fi
        done
        
        # Only remove cache (already done in step 4) and temporary files
        # Remove any .tmp, .temp, or temporary files in data root
        find "$ENV_ROOT/data" -maxdepth 1 -type f \( -name "*.tmp" -o -name "*.temp" -o -name "*~" \) -delete 2>/dev/null || true
        
        echo -e "  ${GREEN}✓${NC} Data directory preserved (cache and temp files removed)"
    else
        echo -e "  ${YELLOW}○${NC} No data directory found"
    fi
fi

echo ""
echo -e "${GREEN}✅ Environment reset complete!${NC}"
echo ""
echo -e "${BLUE}Next steps:${NC}"
echo "  1. Run: ./start.sh"
echo "     (This will regenerate .env and assign new ports)"
echo ""
echo "  2. Check status: ./start.sh --status"
echo ""
if [ $WIPE_DATA -eq 0 ]; then
    echo -e "${GREEN}Note:${NC} All important data has been preserved."
    echo "      Database, uploads, outputs, and other critical files are intact."
fi
echo ""
echo -e "${BLUE}Your folder: $FOLDER_NAME${NC}"
echo ""
