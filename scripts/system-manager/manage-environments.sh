#!/bin/bash
# manage-environments.sh - Manage multiple running LLM010 environments
# Lists all running instances, their ports, and provides management commands

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

CURRENT_DIR="$(pwd)"
SCAN_ALL=0  # default: only consider current directory

# Function to find all LLM directories
find_llm_directories() {
    if [ "$SCAN_ALL" -eq 0 ]; then
        if [ -f "$CURRENT_DIR/start.sh" ]; then
            printf '%s\n' "$CURRENT_DIR"
        fi
        return
    fi

    # Explicit paths for known environments (highest priority)
    local explicit_paths=(
        "/home/llamax1/dev/LLM010.4"
        "/home/llamax1/dev/LLM020.1"
        "/home/llamax1/LLM_PRO"
    )

    local found_dirs=()

    # Check explicit paths first
    for dir in "${explicit_paths[@]}"; do
        if [ -d "$dir" ] && [ -f "$dir/start.sh" ]; then
            found_dirs+=("$dir")
        fi
    done

    # Generic search as fallback for any folder with start.sh
    local search_paths=(
        "$HOME/LLM*"
        "$HOME/*/LLM*"
        "$HOME/projects/LLM*"
        "$HOME/dev/LLM*"
        "$CURRENT_DIR"
    )

    for pattern in "${search_paths[@]}"; do
        for dir in $pattern; do
            if [ -d "$dir" ] && [ -f "$dir/start.sh" ]; then
                # Avoid duplicates
                local is_duplicate=0
                for existing_dir in "${found_dirs[@]}"; do
                    if [ "$dir" = "$existing_dir" ]; then
                        is_duplicate=1
                        break
                    fi
                done
                if [ $is_duplicate -eq 0 ]; then
                    found_dirs+=("$dir")
                fi
            fi
        done
    done

    # Remove duplicates and sort
    printf '%s\n' "${found_dirs[@]}" | sort -u
}

# Function to check if services are running in a directory
check_services() {
    local dir="$1"
    local backend_running=0
    local frontend_running=0
    local backend_port=""
    local frontend_port=""

    # Primary: Check .env file (matches start.sh behavior)
    if [ -f "$dir/.env" ]; then
        # Source .env file to get ports
        set -a
        source "$dir/.env" 2>/dev/null
        set +a
        backend_port=$FLASK_PORT
        frontend_port=$VITE_PORT
    # Secondary: Check .envrc file (future compatibility)
    elif [ -f "$dir/.envrc" ]; then
        source "$dir/.envrc" 2>/dev/null
        backend_port=$FLASK_PORT
        frontend_port=$VITE_PORT
    fi

    # Fallback: Detect ports from running processes (most reliable for active services)
    if [ -z "$backend_port" ] || [ -z "$frontend_port" ]; then
        # Try to detect Flask port from running processes
        if [ -z "$backend_port" ]; then
            local flask_pid=$(pgrep -f "flask run.*--port" | head -1)
            if [ -n "$flask_pid" ]; then
                # Extract port from process command line or lsof
                backend_port=$(lsof -p "$flask_pid" -a -i TCP -s TCP:LISTEN 2>/dev/null | grep -oP ':\K[0-9]+' | head -1)
            fi
        fi

        # Try to detect Vite port from running processes
        if [ -z "$frontend_port" ]; then
            local vite_pid=$(pgrep -f "vite.*--port" | head -1)
            if [ -n "$vite_pid" ]; then
                # Extract port from process command line or lsof
                frontend_port=$(lsof -p "$vite_pid" -a -i TCP -s TCP:LISTEN 2>/dev/null | grep -oP ':\K[0-9]+' | head -1)
            fi
        fi
    fi

    # Check if processes are running on these ports
    if [ -n "$backend_port" ]; then
        if lsof -i :$backend_port >/dev/null 2>&1 || ss -tlnp 2>/dev/null | grep -q ":$backend_port "; then
            backend_running=1
        fi
    fi

    if [ -n "$frontend_port" ]; then
        if lsof -i :$frontend_port >/dev/null 2>&1 || ss -tlnp 2>/dev/null | grep -q ":$frontend_port "; then
            frontend_running=1
        fi
    fi

    echo "$backend_running $frontend_running $backend_port $frontend_port"
}

# Function to display environment list
list_environments() {
    echo -e "${BLUE}╔════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║          LLM Multi-Environment Manager                         ║${NC}"
    echo -e "${BLUE}╚════════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    local dirs=($(find_llm_directories))

    if [ ${#dirs[@]} -eq 0 ]; then
        echo -e "${YELLOW}No LLM environments found.${NC}"
        echo ""
        echo "Search locations:"
        echo "  - /home/llamax1/dev/LLM010.4"
        echo "  - /home/llamax1/dev/LLM020.1"
        echo "  - /home/llamax1/LLM_PRO"
        echo "  - $HOME/LLM*"
        echo "  - $HOME/*/LLM*"
        echo "  - $HOME/projects/LLM*"
        echo "  - $HOME/dev/LLM*"
        echo ""
        exit 0
    fi

    echo -e "${BOLD}Found ${#dirs[@]} environment(s):${NC}"
    echo ""

    local idx=1
    for dir in "${dirs[@]}"; do
        local folder_name=$(basename "$dir")
        local status_info=($(check_services "$dir"))
        local backend_running=${status_info[0]}
        local frontend_running=${status_info[1]}
        local backend_port=${status_info[2]}
        local frontend_port=${status_info[3]}

        # Determine overall status
        local status_icon="○"
        local status_color=$NC
        local status_text="Stopped"

        if [ $backend_running -eq 1 ] && [ $frontend_running -eq 1 ]; then
            status_icon="●"
            status_color=$GREEN
            status_text="Running"
        elif [ $backend_running -eq 1 ] || [ $frontend_running -eq 1 ]; then
            status_icon="◐"
            status_color=$YELLOW
            status_text="Partial"
        fi

        # Current directory indicator
        local current_indicator=""
        if [ "$dir" = "$CURRENT_DIR" ]; then
            current_indicator=" ${CYAN}(current)${NC}"
        fi

        echo -e "${BOLD}[$idx]${NC} ${status_color}${status_icon}${NC} ${BOLD}${folder_name}${NC}${current_indicator}"
        echo -e "    📁 $dir"

        if [ -n "$backend_port" ] && [ -n "$frontend_port" ]; then
            echo -e "    🔌 Ports: Backend=${backend_port}, Frontend=${frontend_port}"

            if [ $backend_running -eq 1 ]; then
                echo -e "    ${GREEN}✓${NC} Backend:  http://127.0.0.1:${backend_port}"
            else
                echo -e "    ${RED}✗${NC} Backend:  (not running)"
            fi

            if [ $frontend_running -eq 1 ]; then
                echo -e "    ${GREEN}✓${NC} Frontend: http://localhost:${frontend_port}"
            else
                echo -e "    ${RED}✗${NC} Frontend: (not running)"
            fi
        else
            echo -e "    ${YELLOW}⚠${NC}  Not configured (run ./start.sh to initialize)"
        fi

        echo ""
        idx=$((idx + 1))
    done

    echo -e "${BLUE}Commands:${NC}"
    echo "  ./manage-environments.sh                    List all environments"
    echo "  ./manage-environments.sh --stop-all         Stop all environments"
    echo "  ./manage-environments.sh --ports            Show port assignments"
    echo "  ./manage-environments.sh --find-conflicts   Check for port conflicts"
    echo ""
}

# Function to show port assignments
show_ports() {
    echo -e "${BLUE}╔════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║          Port Assignments                  ║${NC}"
    echo -e "${BLUE}╚════════════════════════════════════════════╝${NC}"
    echo ""

    local dirs=($(find_llm_directories))

    printf "%-20s %-10s %-10s %-10s\n" "Environment" "Backend" "Frontend" "Status"
    printf "%-20s %-10s %-10s %-10s\n" "────────────" "───────" "────────" "──────"

    for dir in "${dirs[@]}"; do
        local folder_name=$(basename "$dir")
        local status_info=($(check_services "$dir"))
        local backend_running=${status_info[0]}
        local frontend_running=${status_info[1]}
        local backend_port=${status_info[2]:-"N/A"}
        local frontend_port=${status_info[3]:-"N/A"}

        local status="Stopped"
        if [ $backend_running -eq 1 ] && [ $frontend_running -eq 1 ]; then
            status="Running"
        elif [ $backend_running -eq 1 ] || [ $frontend_running -eq 1 ]; then
            status="Partial"
        fi

        printf "%-20s %-10s %-10s %-10s\n" "$folder_name" "$backend_port" "$frontend_port" "$status"
    done

    echo ""
}

# Function to find port conflicts
find_conflicts() {
    echo -e "${BLUE}╔════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║          Port Conflict Check               ║${NC}"
    echo -e "${BLUE}╚════════════════════════════════════════════╝${NC}"
    echo ""

    local dirs=($(find_llm_directories))
    local -A port_usage
    local conflicts_found=0

    for dir in "${dirs[@]}"; do
        local folder_name=$(basename "$dir")
        local flask_port=""
        local vite_port=""

        # Primary: Check .env file
        if [ -f "$dir/.env" ]; then
            set -a
            source "$dir/.env" 2>/dev/null
            set +a
            flask_port=$FLASK_PORT
            vite_port=$VITE_PORT
        # Secondary: Check .envrc file
        elif [ -f "$dir/.envrc" ]; then
            source "$dir/.envrc" 2>/dev/null
            flask_port=$FLASK_PORT
            vite_port=$VITE_PORT
        fi

        if [ -n "$flask_port" ]; then
            if [ -n "${port_usage[$flask_port]}" ]; then
                echo -e "${RED}✗ Conflict:${NC} Port $flask_port assigned to both:"
                echo "    - ${port_usage[$flask_port]}"
                echo "    - $folder_name"
                conflicts_found=1
            else
                port_usage[$flask_port]="$folder_name (backend)"
            fi
        fi

        if [ -n "$vite_port" ]; then
            if [ -n "${port_usage[$vite_port]}" ]; then
                echo -e "${RED}✗ Conflict:${NC} Port $vite_port assigned to both:"
                echo "    - ${port_usage[$vite_port]}"
                echo "    - $folder_name"
                conflicts_found=1
            else
                port_usage[$vite_port]="$folder_name (frontend)"
            fi
        fi
    done

    if [ $conflicts_found -eq 0 ]; then
        echo -e "${GREEN}✓ No port conflicts found${NC}"
    else
        echo ""
        echo -e "${YELLOW}💡 To fix conflicts:${NC}"
        echo "   cd <conflicting-folder>"
        echo "   ./reset-environment.sh"
        echo "   ./start.sh  # Will assign new ports"
    fi

    echo ""
}

# Function to stop all environments
stop_all() {
    echo -e "${BLUE}╔════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║          Stop All Environments             ║${NC}"
    echo -e "${BLUE}╚════════════════════════════════════════════╝${NC}"
    echo ""

    local dirs=($(find_llm_directories))

    for dir in "${dirs[@]}"; do
        local folder_name=$(basename "$dir")
        local status_info=($(check_services "$dir"))
        local backend_running=${status_info[0]}
        local frontend_running=${status_info[1]}

        if [ $backend_running -eq 1 ] || [ $frontend_running -eq 1 ]; then
            echo -e "Stopping ${BOLD}$folder_name${NC}..."

            if [ -f "$dir/stop.sh" ]; then
                (cd "$dir" && ./stop.sh 2>/dev/null)
            else
                # Manual stop if stop.sh doesn't exist
                local flask_port=""
                local vite_port=""
                
                # Get ports from .env or .envrc
                if [ -f "$dir/.env" ]; then
                    set -a
                    source "$dir/.env" 2>/dev/null
                    set +a
                    flask_port=$FLASK_PORT
                    vite_port=$VITE_PORT
                elif [ -f "$dir/.envrc" ]; then
                    source "$dir/.envrc" 2>/dev/null
                    flask_port=$FLASK_PORT
                    vite_port=$VITE_PORT
                fi
                
                if [ -n "$flask_port" ]; then
                    lsof -ti :$flask_port | xargs kill -9 2>/dev/null || true
                fi
                if [ -n "$vite_port" ]; then
                    lsof -ti :$vite_port | xargs kill -9 2>/dev/null || true
                fi
            fi

            echo -e "  ${GREEN}✓${NC} Stopped"
        fi
    done

    echo ""
    echo -e "${GREEN}✓ All environments stopped${NC}"
    echo ""
}

# Main command processing
case "${1:-}" in
    --all|--scan-all)
        SCAN_ALL=1
        list_environments
        ;;
    --stop-all)
        stop_all
        ;;
    --ports)
        show_ports
        ;;
    --find-conflicts)
        find_conflicts
        ;;
    -h|--help)
        cat << 'EOF'
Multi-Environment Manager

Usage: ./manage-environments.sh [COMMAND]

COMMANDS:
    (none)              List all environments and their status
    --all, --scan-all   Scan all known paths (may include other installs)
    --stop-all          Stop all running environments
    --ports             Show port assignments table
    --find-conflicts    Check for port conflicts
    -h, --help          Show this help

EXAMPLES:
    # List all environments
    ./manage-environments.sh

    # Stop everything
    ./manage-environments.sh --stop-all

    # Check which ports are assigned
    ./manage-environments.sh --ports

    # Find port conflicts
    ./manage-environments.sh --find-conflicts

PER-ENVIRONMENT MANAGEMENT:
    To manage a specific environment, cd into it:

    cd ~/LLM010.5
    ./start.sh              # Start this environment
    ./stop.sh               # Stop this environment
    ./reset-environment.sh  # Reset this environment
    ./start.sh --status     # Check status

EOF
        ;;
    *)
        list_environments
        ;;
esac
