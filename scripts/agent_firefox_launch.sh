#!/bin/bash
# Launch Firefox in the agent's isolated profile on the virtual display.
# Wired into tint2's Firefox launcher icon via data/agent/desktop/applications/firefox.desktop —
# clicking the bottom-bar Firefox icon runs THIS script, not /usr/share/applications/firefox.desktop
# (which would attach to the user's main browser or open with the default profile, both bad).
#
# Behavior:
#   - If a Firefox process is already running on our profile, raise its window and exit.
#   - Otherwise, clear any stale lock files, launch fresh on :99 with the agent profile.
#
# Race protection: flock on a fixed lockfile so two near-simultaneous launches
# (e.g. auto-launch at display-start vs an early click) can't both grab the
# profile and fight over the lock.

set -u

GUAARDVARK_ROOT="${GUAARDVARK_ROOT:-$(dirname $(dirname $(readlink -f "$0")))}"
DISPLAY_NUM="${GUAARDVARK_AGENT_DISPLAY:-99}"
PROFILE_DIR="$GUAARDVARK_ROOT/data/agent/firefox_profile"
LOCKFILE="/tmp/agent_firefox_launch.lock"

export DISPLAY=":$DISPLAY_NUM"

# Force X11 on Wayland hosts — Mozilla otherwise tries to grab a Wayland
# socket that doesn't exist on the virtual display.
if [ -n "${WAYLAND_DISPLAY:-}" ] || [ "${XDG_SESSION_TYPE:-}" = "wayland" ]; then
    export MOZ_ENABLE_WAYLAND=0
    export GDK_BACKEND=x11
    export WAYLAND_DISPLAY=
    export XDG_SESSION_TYPE=x11
fi

# Serialize check-and-launch so two parallel calls don't both decide to start.
exec 9>"$LOCKFILE"
if ! flock -n 9; then
    # Another invocation is mid-launch; let it finish.
    exit 0
fi

profile_basename="$(basename "$PROFILE_DIR")"

# Already running on our profile? Raise + exit.
if pgrep -f "firefox.*${profile_basename}" > /dev/null 2>&1; then
    if command -v wmctrl &>/dev/null; then
        wmctrl -a "Mozilla Firefox" 2>/dev/null || true
    elif command -v xdotool &>/dev/null; then
        xdotool search --name "Mozilla Firefox" windowactivate 2>/dev/null || true
    fi
    exit 0
fi

# Stale-lock cleanup. Firefox writes .parentlock and lock to the profile dir;
# if a previous instance was killed hard (Xvfb crash, kill -9, OOM) those
# can persist with a dead PID inside and block legitimate restarts. Both lock
# files are absent when Firefox started cleanly, so removing them when
# Firefox is verifiably not running is safe.
for lf in "$PROFILE_DIR/lock" "$PROFILE_DIR/.parentlock"; do
    [ -e "$lf" ] && rm -f "$lf"
done

# Launch detached so the wrapper script returns quickly (tint2 doesn't want
# its launcher process held open). nohup + & + redirect, no exec — exec would
# replace the shell with firefox and the trailing & wouldn't apply correctly.
nohup firefox \
    --no-remote \
    --remote-debugging-port 9222 \
    --profile "$PROFILE_DIR" \
    >/dev/null 2>&1 &
