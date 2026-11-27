#!/bin/bash
# scripts/lib/sudo_no_prompt.sh — sudo that never asks for a password while start.sh
# runs for the desktop app. Sourced by start.sh, start_redis.sh and start_postgres.sh.
#
# The app runs start.sh with a progress file and a stdin that is not a terminal, and
# nobody can type a password. sudo fails at once when there is no controlling
# terminal, but asks on one when there is and then waits for good. In that mode this
# file defines a sudo() function for the shell that sources it, so every sudo there
# runs as `sudo -n` through scripts/lib/sudo_no_prompt/sudo, which notes each refusal
# in GV_SUDO_REFUSED_FILE for the "failed" line. Only the setup scripts get it: the
# environment of the services start.sh launches is left as it was.
#
#   GV_SUDO_NO_PROMPT        1 when the mode is on (not exported)
#   gv_sudo_external CMD...  run an outside program that calls sudo itself (an
#                            installer) with the same rule, by PATH for that command only

_GV_SUDO_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GV_SUDO_REFUSED_FILE="$(cd "$_GV_SUDO_LIB_DIR/../.." && pwd)/.start_cache/sudo-refused"
GV_SUDO_NO_PROMPT=0
_GV_REAL_SUDO="$(type -P sudo 2>/dev/null)"

if [ -n "${GUAARDVARK_PROGRESS_FILE:-}" ] && [ ! -t 0 ] && [ -n "$_GV_REAL_SUDO" ]; then
    GV_SUDO_NO_PROMPT=1
    sudo() {
        GUAARDVARK_REAL_SUDO="$_GV_REAL_SUDO" GUAARDVARK_SUDO_REFUSED="$GV_SUDO_REFUSED_FILE" \
            "$_GV_SUDO_LIB_DIR/sudo_no_prompt/sudo" "$@"
    }
fi

gv_sudo_external() {
    if [ "$GV_SUDO_NO_PROMPT" = 1 ]; then
        PATH="$_GV_SUDO_LIB_DIR/sudo_no_prompt:$PATH" GUAARDVARK_REAL_SUDO="$_GV_REAL_SUDO" \
            GUAARDVARK_SUDO_REFUSED="$GV_SUDO_REFUSED_FILE" "$@"
    else
        "$@"
    fi
}
