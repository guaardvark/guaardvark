#!/usr/bin/env bash
# Find the Celery worker processes that belong to one checkout. Sourced by killswitch.sh.
#
#   celery_worker_pids_of "$SCRIPT_DIR"   -> one PID per line; prints nothing when none run
#
# A process counts when it runs Celery's worker CLI on backend.celery_app (as
# start_celery.sh, Docker and backend/services/research_worker.py start it) or
# carries Celery's own "[celeryd: ...]" title (set when setproctitle is
# installed), and its working directory is in the checkout, not in a worktree
# nested inside it. The command line alone cannot tell two installs apart.

# Anchored on the program, so a shell or editor whose arguments merely mention
# the worker command does not match.
CELERY_WORKER_CMD_PATTERN='^\[celeryd: |^([^ ]*/)?(python[0-9.]* +(-m +)?([^ ]*/)?)?celery -A backend\.celery_app\.celery worker( |$)'

# shellcheck source=scripts/lib/proc_owner.sh
command -v proc_in_install >/dev/null 2>&1 || . "$(dirname "${BASH_SOURCE[0]}")/proc_owner.sh"

celery_worker_cwd() {
    local pid="$1" cwd=""
    if [ -e "/proc/$pid/cwd" ]; then
        cwd=$(readlink -f "/proc/$pid/cwd" 2>/dev/null)
    elif command -v lsof >/dev/null 2>&1; then
        cwd=$(lsof -a -d cwd -p "$pid" -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)
    fi
    printf '%s' "$cwd"
}

celery_worker_pids_of() {
    local root pid
    # Physical path: the kernel reports a process's cwd with symlinks resolved.
    root=$(cd "$1" 2>/dev/null && pwd -P) || return 0
    for pid in $(pgrep -f "$CELERY_WORKER_CMD_PATTERN" 2>/dev/null); do
        if proc_in_install "$pid" "$root" cwd; then echo "$pid"; fi
    done
}
