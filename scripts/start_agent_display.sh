#!/bin/bash
# Start the Agent Vision Control virtual display
# Usage: ./scripts/start_agent_display.sh [start|stop|status]

DISPLAY_NUM=99
RESOLUTION="1280x720x24"
VNC_PORT=5999
AGENT_PROFILE="/tmp/agent_firefox_profile2"
PID_DIR="${GUAARDVARK_ROOT:-$(dirname $(dirname $(readlink -f $0)))}/pids"
LOG_DIR="${GUAARDVARK_ROOT:-$(dirname $(dirname $(readlink -f $0)))}/logs"

mkdir -p "$PID_DIR" "$LOG_DIR" "$AGENT_PROFILE"

start() {
    echo "Starting Agent Virtual Display (:$DISPLAY_NUM @ ${RESOLUTION%x*})..."
    
    # Xvfb
    if pgrep -f "Xvfb :$DISPLAY_NUM" > /dev/null 2>&1; then
        echo "  Xvfb already running"
    else
        Xvfb :$DISPLAY_NUM -screen 0 $RESOLUTION -ac &
        echo $! > "$PID_DIR/xvfb.pid"
        sleep 1
        echo "  Xvfb started (PID $(cat $PID_DIR/xvfb.pid))"
    fi
    
    # Desktop environment (openbox + tint2 = real desktop with taskbar and right-click menu)
    if pgrep -f "openbox" > /dev/null 2>&1; then
        echo "  Openbox already running"
    else
        # Configure openbox right-click menu
        mkdir -p "$HOME/.config/openbox"
        cat > "$HOME/.config/openbox/menu.xml" << 'OBMENUX'
<?xml version="1.0" encoding="utf-8"?>
<openbox_menu xmlns="http://openbox.org/3.4/menu">
  <menu id="root-menu" label="Guaardvark Agent">
    <item label="Firefox">
      <action name="Execute">
        <execute>env MOZ_ENABLE_WAYLAND=0 GDK_BACKEND=x11 firefox --no-remote --profile /tmp/agent_firefox_profile2</execute>
      </action>
    </item>
    <item label="Google Chrome">
      <action name="Execute">
        <execute>google-chrome-stable --no-sandbox --user-data-dir=/tmp/agent_chrome_profile</execute>
      </action>
    </item>
    <item label="File Manager">
      <action name="Execute">
        <execute>pcmanfm --new-win</execute>
      </action>
    </item>
    <item label="Terminal">
      <action name="Execute">
        <execute>xterm -fa Monospace -fs 12</execute>
      </action>
    </item>
    <separator/>
    <item label="Guaardvark">
      <action name="Execute">
        <execute>env MOZ_ENABLE_WAYLAND=0 GDK_BACKEND=x11 firefox --no-remote --profile /tmp/agent_firefox_profile2 http://localhost:5175</execute>
      </action>
    </item>
  </menu>
</openbox_menu>
OBMENUX

        DISPLAY=:$DISPLAY_NUM openbox &
        echo $! > "$PID_DIR/openbox.pid"
        sleep 1
        echo "  Openbox desktop started"

        # Taskbar
        DISPLAY=:$DISPLAY_NUM tint2 &>/dev/null &
        echo $! > "$PID_DIR/tint2.pid"
        sleep 1
        echo "  Tint2 taskbar started"
    fi
    
    # Sync cookies and logins from user's real Firefox profile
    # This makes the virtual screen act like a third monitor with the user's session
    USER_FF_PROFILE=$(find "$HOME/.mozilla/firefox" -maxdepth 1 -name "*.default-release" -type d 2>/dev/null | head -1)
    if [ -z "$USER_FF_PROFILE" ]; then
        USER_FF_PROFILE=$(find "$HOME/.mozilla/firefox" -maxdepth 1 -name "*.default" -type d 2>/dev/null | head -1)
    fi
    if [ -n "$USER_FF_PROFILE" ] && [ -d "$USER_FF_PROFILE" ]; then
        echo "  Syncing session from: $USER_FF_PROFILE"
        for f in cookies.sqlite logins.json key4.db cert9.db permissions.sqlite; do
            if [ -f "$USER_FF_PROFILE/$f" ]; then
                cp "$USER_FF_PROFILE/$f" "$AGENT_PROFILE/$f" 2>/dev/null
            fi
        done
        # Copy bookmarks if agent profile doesn't have any yet
        if [ ! -f "$AGENT_PROFILE/places.sqlite" ] && [ -f "$USER_FF_PROFILE/places.sqlite" ]; then
            cp "$USER_FF_PROFILE/places.sqlite" "$AGENT_PROFILE/places.sqlite" 2>/dev/null
            echo "  Copied bookmarks and history"
        fi
    else
        echo "  Warning: No Firefox profile found to sync cookies from"
    fi

    # Ensure Firefox profile has safe settings (light mode, no fullscreen)
    mkdir -p "$AGENT_PROFILE"
    cat > "$AGENT_PROFILE/user.js" << 'USERJS'
// Force light theme (vision models read light UIs better)
user_pref("extensions.activeThemeID", "default-theme@mozilla.org");
user_pref("browser.theme.content-theme", 1);
user_pref("ui.systemUsesDarkTheme", 0);
user_pref("layout.css.prefers-color-scheme.content-override", 1);
// Show bookmarks toolbar
user_pref("browser.toolbars.bookmarks.visibility", "always");
// Disable fullscreen API (prevents black screen on videos)
user_pref("full-screen-api.enabled", false);
// Disable autoplay (videos auto-playing interferes with agent)
user_pref("media.autoplay.default", 5);
// Restore previous session (keep logins)
user_pref("browser.startup.page", 3);
user_pref("browser.sessionstore.resume_from_crash", true);
USERJS

    # Firefox (with user's cookies, forced X11)
    if pgrep -f "firefox.*agent_firefox_profile" > /dev/null 2>&1; then
        echo "  Agent Firefox already running"
    else
        env DISPLAY=:$DISPLAY_NUM \
            MOZ_ENABLE_WAYLAND=0 \
            WAYLAND_DISPLAY= \
            GDK_BACKEND=x11 \
            firefox --no-remote --profile "$AGENT_PROFILE" \
            --width 1280 --height 720 \
            "about:blank" \
            > "$LOG_DIR/agent_firefox.log" 2>&1 &
        echo $! > "$PID_DIR/agent_firefox.pid"
        sleep 3
        echo "  Agent Firefox started (PID $(cat $PID_DIR/agent_firefox.pid))"
    fi
    
    # VNC server (for watching the agent)
    if pgrep -f "x11vnc.*:$DISPLAY_NUM" > /dev/null 2>&1; then
        echo "  x11vnc already running"
    else
        env -u WAYLAND_DISPLAY -u XDG_SESSION_TYPE \
            DISPLAY=:$DISPLAY_NUM \
            x11vnc -nopw -localhost -forever -shared -rfbport $VNC_PORT \
            -bg -o "$LOG_DIR/x11vnc_agent.log" 2>&1
        sleep 1
        echo "  x11vnc started on port $VNC_PORT"
    fi
    
    echo ""
    echo "Agent Virtual Display ready!"
    echo "  Display:  :$DISPLAY_NUM"
    echo "  VNC:      localhost:$VNC_PORT"
    echo "  Firefox:  with your session cookies"
    echo ""
    echo "Connect TigerVNC to localhost:$VNC_PORT to watch the agent."
}

stop() {
    echo "Stopping Agent Virtual Display..."
    
    for proc in agent_firefox tint2 openbox matchbox xvfb; do
        pid_file="$PID_DIR/${proc}.pid"
        if [ -f "$pid_file" ]; then
            pid=$(cat "$pid_file")
            kill $pid 2>/dev/null && echo "  Stopped $proc (PID $pid)" || echo "  $proc not running"
            rm -f "$pid_file"
        fi
    done
    
    # Kill x11vnc for our display
    pkill -f "x11vnc.*:$DISPLAY_NUM" 2>/dev/null && echo "  Stopped x11vnc" || echo "  x11vnc not running"
    # Kill any remaining Firefox on agent profile
    pkill -f "firefox.*agent_firefox_profile" 2>/dev/null
    
    echo "Agent Virtual Display stopped."
}

status() {
    echo "Agent Virtual Display Status:"
    pgrep -f "Xvfb :$DISPLAY_NUM" > /dev/null 2>&1 && echo "  Xvfb:     RUNNING" || echo "  Xvfb:     STOPPED"
    pgrep -f "openbox" > /dev/null 2>&1 && echo "  Openbox:  RUNNING" || echo "  Openbox:  STOPPED"
    pgrep -f "tint2" > /dev/null 2>&1 && echo "  Tint2:    RUNNING" || echo "  Tint2:    STOPPED"
    pgrep -f "firefox.*agent_firefox_profile" > /dev/null 2>&1 && echo "  Firefox:  RUNNING" || echo "  Firefox:  STOPPED"
    pgrep -f "x11vnc.*:$DISPLAY_NUM" > /dev/null 2>&1 && echo "  x11vnc:   RUNNING (port $VNC_PORT)" || echo "  x11vnc:   STOPPED"
}

case "${1:-start}" in
    start)  start ;;
    stop)   stop ;;
    status) status ;;
    restart) stop; sleep 2; start ;;
    *) echo "Usage: $0 {start|stop|status|restart}" ;;
esac
