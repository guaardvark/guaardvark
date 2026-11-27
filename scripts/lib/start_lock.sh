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

# ── Run lock held by the kernel (Linux) ──────────────────────────────────────
# A helper process holds flock(2) on .start_cache/start.lock for as long as the
# start.sh that took it runs; the kernel drops the lock the moment the helper
# exits, so a crash or SIGKILL leaves nothing stale behind. The helper holds the
# descriptor, not start.sh: every server start.sh launches would inherit one
# start.sh held and keep the lock after start.sh had finished.
# Without the flock command (macOS) the pidfile check in start.sh is the guard.

start_lock_file() {
    printf '%s\n' "$SCRIPT_DIR/.start_cache/start.lock"
}

# start_lock_acquire — take the lock for this shell ($$). Sets START_LOCK_HELD:
#   yes   taken (or already held by this process before it re-ran itself with exec)
#   busy  another start.sh holds it
#   none  this system has no flock command
# Waits up to 3 s, time for the helper of a start.sh that was just killed to exit.
start_lock_acquire() {
    local lf
    lf=$(start_lock_file)
    if ! command -v flock >/dev/null 2>&1; then
        START_LOCK_HELD=none
        return 0
    fi
    # start.sh re-runs itself with exec after undoing a self-edit: same PID, and
    # the helper it started still holds the lock for it.
    if [ "${START_LOCK_TOKEN%%-*}" = "$$" ] && start_lock_helper_is_ours; then
        START_LOCK_HELD=yes
        return 0
    fi
    START_LOCK_TOKEN="$$-$RANDOM$RANDOM"
    flock -w 3 -o -E 75 "$lf" bash "$SCRIPT_DIR/scripts/lib/start_lock_watch.sh" "$$" "$START_LOCK_TOKEN" "$lf" \
        </dev/null >/dev/null 2>&1 &
    START_LOCK_HELPER=$!
    export START_LOCK_TOKEN START_LOCK_HELPER
    local tries=0 rc
    while [ "$tries" -lt 100 ]; do
        if start_lock_helper_is_ours; then
            START_LOCK_HELD=yes
            return 0
        fi
        if ! proc_alive "$START_LOCK_HELPER"; then
            wait "$START_LOCK_HELPER" 2>/dev/null
            rc=$?
            # 75 is the -E code for "held by someone else"; anything else means
            # flock could not do its job here (an unwritable .start_cache, say).
            if [ "$rc" = 75 ]; then START_LOCK_HELD=busy; else START_LOCK_HELD=none; fi
            return 0
        fi
        tries=$((tries + 1))
        sleep 0.1
    done
    kill -TERM "$START_LOCK_HELPER" 2>/dev/null
    START_LOCK_HELD=none
}

# True when the lock file names this process's helper.
start_lock_helper_is_ours() {
    [ -n "${START_LOCK_TOKEN:-}" ] || return 1
    case "$(cat "$(start_lock_file)" 2>/dev/null)" in
        *" $START_LOCK_TOKEN") return 0 ;;
    esac
    return 1
}

# PID of the start.sh whose helper holds the lock, from the lock file.
start_lock_owner() {
    local line
    line=$(cat "$(start_lock_file)" 2>/dev/null)
    line=${line#* }
    printf '%s' "${line%%-*}"
}

# Give the lock up early (start.sh does once Guaardvark is ready) or on exit.
start_lock_release() {
    [ "${START_LOCK_HELD:-}" = yes ] || return 0
    local watcher=""
    if start_lock_helper_is_ours; then
        watcher=$(cut -d' ' -f1 "$(start_lock_file)" 2>/dev/null)
    fi
    kill -TERM ${START_LOCK_HELPER:-} $watcher 2>/dev/null || true
    START_LOCK_HELD=released
}

# Remove the pidfile if it still names this process; a later start.sh may own it.
start_lock_drop_pidfile() {
    local pf
    pf=$(start_lock_pidfile)
    [ "$(cat "$pf" 2>/dev/null)" = "$$" ] && rm -f "$pf" 2>/dev/null
    return 0
}

# End the helper of a start.sh that is gone or stopped (Ctrl+Z), so the lock is
# free at once. SIGKILL, because a stopped process acts on nothing else.
start_lock_kill_orphan_helper() {
    local watcher owner flk
    owner=$(start_lock_owner)
    [ -n "$owner" ] && start_lock_is_runnable "$owner" && return 0
    watcher=$(cut -d' ' -f1 "$(start_lock_file)" 2>/dev/null)
    [ -n "$watcher" ] && kill -0 "$watcher" 2>/dev/null || return 0
    case "$(start_lock_cmd "$watcher")" in
        *start_lock_watch.sh*) ;;
        *) return 0 ;;
    esac
    flk=$(ps -p "$watcher" -o ppid= 2>/dev/null | tr -d ' ')
    case "$(start_lock_cmd "$flk")" in
        flock*) kill -KILL "$flk" 2>/dev/null ;;
    esac
    pkill -KILL -P "$watcher" 2>/dev/null
    kill -KILL "$watcher" 2>/dev/null
    return 0
}

# Reap abandoned / listed start.sh for this checkout and clear the pidfile, except
# that the start.sh which ran this stop.sh (an ancestor) keeps its pidfile and lock.
# Prints one human line to stdout (caller may prefix). Returns 0.
start_lock_reap() {
    local pf pid cmd keep=0
    pf=$(start_lock_pidfile)
    pid=$(cat "$pf" 2>/dev/null || true)
    if [ -n "$pid" ] && start_lock_is_self_or_ancestor "$pid"; then
        keep=1
    elif [ -n "$pid" ] && start_lock_is_our_script "$pid"; then
        start_lock_kill_pid "$pid"
        echo "Stopped leftover start.sh (PID $pid)"
    elif [ -f "$pf" ]; then
        echo "Cleared stale start.sh pidfile"
    fi
    # Catch leftovers whose pidfile was lost (cwd = this checkout).
    # Never reap this process or the start.sh that invoked stop.sh (PPID).
    # Run by the start.sh that holds the lock, leave a start.sh that is waiting
    # for it (it has a flock child): it refuses on its own and says why.
    local owner caller_holds=0
    owner=$(start_lock_owner)
    [ -n "$owner" ] && start_lock_is_self_or_ancestor "$owner" && caller_holds=1
    if command -v pgrep >/dev/null 2>&1; then
        for pid in $(pgrep -f 'start\.sh|setup\.sh' 2>/dev/null); do
            start_lock_is_self_or_ancestor "$pid" && continue
            [ "$caller_holds" = 1 ] && [ -n "$(pgrep -P "$pid" -x flock 2>/dev/null)" ] && continue
            start_lock_is_our_script "$pid" || continue
            start_lock_kill_pid "$pid"
            echo "Stopped leftover start.sh (PID $pid)"
        done
    fi
    start_lock_kill_orphan_helper
    [ "$keep" = 1 ] || rm -f "$pf" 2>/dev/null || true
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
