#!/bin/bash
# scripts/lib/start_lock_watch.sh OWNER_PID TOKEN LOCKFILE
#
# Runs under `flock -o LOCKFILE` for start_lock_acquire (scripts/lib/start_lock.sh):
# flock holds the start.sh run lock until this script exits. It writes its PID and
# TOKEN to LOCKFILE, which tells start_lock_acquire the lock is taken, then waits
# while OWNER_PID (the start.sh that asked for the lock) runs.

. "$(dirname "$0")/proc_owner.sh"   # proc_alive

printf '%s %s\n' "$$" "$2" > "$3"
while proc_alive "$1"; do
    sleep 1
done
