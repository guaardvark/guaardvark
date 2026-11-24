# scripts/lib/start_lock.sh — single-instance lock for ./start.sh / ./setup.sh
#
# Sourced by start.sh and stop.sh. Requires SCRIPT_DIR (install root).
# Does not start or stop the app stack.

# shellcheck source=scripts/lib/proc_owner.sh
command -v proc_install_root >/dev/null 2>&1 || . "$(dirname "${BASH_SOURCE[0]}")/proc_owner.sh"

start_lock_pidfile() {
    printf '%s\n' "${START_PID_FILE:-$SCRIPT_DIR/.start_cache/start.sh.pid}"
}

start_lock_cmd() {
    ps -p "$1" -o command= 2>/dev/null || true
}

start_lock_stat() {
    # First character of STAT: R/S/D running-or-sleeping; T/t stopped; Z zombie.
    ps -p "$1" -o stat= 2>/dev/null | tr -d ' ' | cut -c1
}

start_lock_cwd() {
    local pid="$1" cwd=""
    if [ -e "/proc/$pid/cwd" ]; then
        cwd=$(readlink -f "/proc/$pid/cwd" 2>/dev/null)
    elif command -v lsof >/dev/null 2>&1; then
        cwd=$(lsof -a -d cwd -p "$pid" -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)
    fi
    printf '%s' "$cwd"
}

# True if pid is THIS checkout's start.sh / setup.sh, not plugins/*/scripts/start.sh.
start_lock_is_our_script() {
    local pid="$1"
    local cmd cwd
    [ -n "$pid" ] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    cmd=$(start_lock_cmd "$pid")
    [ -n "$cmd" ] || return 1
    case "$cmd" in
        *"/plugins/"*"/scripts/start.sh"*) return 1 ;;
    esac
    case "$cmd" in
        *"$SCRIPT_DIR/start.sh"*|*"$SCRIPT_DIR/setup.sh"*|\
        *"./start.sh"*|*"./setup.sh"*|"start.sh"|*" start.sh"|start.sh*)
            ;;
        *)
            return 1
            ;;
    esac
    # An unreadable cwd is trusted, as the command already named start.sh.
    cwd=$(start_lock_cwd "$pid")
    [ -z "$cwd" ] && return 0
    proc_in_install "$pid" "$SCRIPT_DIR"
}

# True if the process is actually running (not job-control stopped / zombie).
start_lock_is_runnable() {
    local st
    st=$(start_lock_stat "$1")
    case "$st" in
        T|t|Z|"") return 1 ;;
        *) return 0 ;;
    esac
}

# True if pid is us, our parent, or any ancestor (start.sh → stop.sh must
# not reap the installer that invoked it).
start_lock_is_self_or_ancestor() {
    local pid="$1" p
    [ -n "$pid" ] || return 1
    [ "$pid" = "$$" ] && return 0
    [ "$pid" = "$PPID" ] && return 0
    case " ${START_LOCK_PROTECT_PIDS:-} " in
        *" $pid "*) return 0 ;;
    esac
    p=$PPID
    while [ -n "$p" ] && [ "$p" != "0" ] && [ "$p" != "1" ]; do
        [ "$pid" = "$p" ] && return 0
        p=$(ps -p "$p" -o ppid= 2>/dev/null | tr -d ' ')
    done
    return 1
}

# SIGTERM then SIGKILL one pid. Works on STAT=T (stopped) processes.
start_lock_kill_pid() {
    local pid="$1"
    [ -n "$pid" ] || return 0
    start_lock_is_self_or_ancestor "$pid" && return 0
    # Continue first so a Ctrl+Z job can receive TERM.
    kill -CONT "$pid" 2>/dev/null || true
    kill -TERM "$pid" 2>/dev/null || true
    sleep 0.3
    if kill -0 "$pid" 2>/dev/null; then
        kill -KILL "$pid" 2>/dev/null || true
        sleep 0.1
    fi
}

# Reap abandoned / listed start.sh for this checkout. Always clears the pidfile.
# Prints one human line to stdout (caller may prefix). Returns 0.
start_lock_reap() {
    local pf pid cmd
    pf=$(start_lock_pidfile)
    pid=$(cat "$pf" 2>/dev/null || true)
    if [ -n "$pid" ] && start_lock_is_our_script "$pid" && ! start_lock_is_self_or_ancestor "$pid"; then
        start_lock_kill_pid "$pid"
        echo "Stopped leftover start.sh (PID $pid)"
    elif [ -f "$pf" ]; then
        echo "Cleared stale start.sh pidfile"
    fi
    # Catch leftovers whose pidfile was lost (cwd = this checkout).
    # Never reap this process or the start.sh that invoked stop.sh (PPID).
    if command -v pgrep >/dev/null 2>&1; then
        for pid in $(pgrep -f 'start\.sh|setup\.sh' 2>/dev/null); do
            start_lock_is_self_or_ancestor "$pid" && continue
            start_lock_is_our_script "$pid" || continue
            start_lock_kill_pid "$pid"
            echo "Stopped leftover start.sh (PID $pid)"
        done
    fi
    rm -f "$pf" 2>/dev/null || true
}

# Decide whether a pidfile should block a new ./start.sh.
# Echoes:
#   live   — refuse to overlap
#   take   — reap/ignore and continue
start_lock_guard_decision() {
    local pid="$1"
    if [ -z "$pid" ] || [ "$pid" = "$$" ]; then
        echo take
        return
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
        echo take
        return
    fi
    if ! start_lock_is_our_script "$pid"; then
        echo take
        return
    fi
    if ! start_lock_is_runnable "$pid"; then
        echo take
        return
    fi
    echo live
}
