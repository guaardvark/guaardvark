#!/bin/bash
# Start the Agent Vision Control virtual display
# Usage: ./scripts/start_agent_display.sh [start|stop|status|restart]
#
# Environment variables:
#   GUAARDVARK_AGENT_BROWSER   - Browser to use: firefox|chromium|chrome (auto-detected if unset)
#   GUAARDVARK_AGENT_DISPLAY   - X display number (default: 99)
#   GUAARDVARK_AGENT_VNC_PORT  - VNC port (default: 5999)
#   GUAARDVARK_AGENT_RESOLUTION - Display resolution (default: 1280x720x24)

GUAARDVARK_ROOT="${GUAARDVARK_ROOT:-$(dirname $(dirname $(readlink -f $0)))}"
DISPLAY_NUM="${GUAARDVARK_AGENT_DISPLAY:-99}"
RESOLUTION="${GUAARDVARK_AGENT_RESOLUTION:-1280x720x24}"
VNC_PORT="${GUAARDVARK_AGENT_VNC_PORT:-5999}"
PID_DIR="$GUAARDVARK_ROOT/pids"
LOG_DIR="$GUAARDVARK_ROOT/logs"
DATA_DIR="$GUAARDVARK_ROOT/data/agent"

mkdir -p "$PID_DIR" "$LOG_DIR" "$DATA_DIR"

# ---------------------------------------------------------------------------
# Browser detection & configuration
# ---------------------------------------------------------------------------

detect_browser() {
    # If user set GUAARDVARK_AGENT_BROWSER, respect it
    if [ -n "$GUAARDVARK_AGENT_BROWSER" ]; then
        echo "$GUAARDVARK_AGENT_BROWSER"
        return
    fi
    # Auto-detect: prefer firefox, fall back to chromium/chrome
    if command -v firefox &>/dev/null; then
        echo "firefox"
    elif command -v chromium-browser &>/dev/null; then
        echo "chromium-browser"
    elif command -v chromium &>/dev/null; then
        echo "chromium"
    elif command -v google-chrome &>/dev/null; then
        echo "google-chrome"
    elif command -v google-chrome-stable &>/dev/null; then
        echo "google-chrome-stable"
    else
        echo ""
    fi
}

browser_profile_dir() {
    local browser="$1"
    case "$browser" in
        firefox|firefox-esr)
            echo "$DATA_DIR/firefox_profile"
            ;;
        chromium*|google-chrome*)
            echo "$DATA_DIR/chromium_profile"
            ;;
        *)
            echo "$DATA_DIR/browser_profile"
            ;;
    esac
}

browser_display_name() {
    local browser="$1"
    case "$browser" in
        firefox|firefox-esr) echo "Firefox" ;;
        chromium*) echo "Chromium" ;;
        google-chrome*) echo "Chrome" ;;
        *) echo "$browser" ;;
    esac
}

# Build the env prefix to force X11 (not Wayland) on the virtual display.
# Docker/headless systems don't have Wayland, so this is a no-op there.
browser_env_prefix() {
    local env_prefix="DISPLAY=:$DISPLAY_NUM"
    # If the host session is Wayland, override to force X11 for the virtual display
    if [ -n "$WAYLAND_DISPLAY" ] || [ "$XDG_SESSION_TYPE" = "wayland" ]; then
        env_prefix="$env_prefix MOZ_ENABLE_WAYLAND=0 GDK_BACKEND=x11 WAYLAND_DISPLAY= XDG_SESSION_TYPE=x11"
    fi
    echo "$env_prefix"
}

# Build the browser launch command with profile flags
browser_launch_cmd() {
    local browser="$1"
    local profile_dir="$2"
    local url="${3:-}"  # optional URL to open

    case "$browser" in
        firefox|firefox-esr)
            echo "$browser --no-remote --profile $profile_dir $url"
            ;;
        chromium*|google-chrome*)
            echo "$browser --no-first-run --no-default-browser-check --user-data-dir=$profile_dir $url"
            ;;
        *)
            echo "$browser $url"
            ;;
    esac
}

# Sync session data (cookies, logins) from user's real browser profile
sync_browser_session() {
    local browser="$1"
    local profile_dir="$2"

    case "$browser" in
        firefox|firefox-esr)
            sync_firefox_session "$profile_dir"
            ;;
        chromium*|google-chrome*)
            sync_chromium_session "$profile_dir"
            ;;
    esac
}

sync_firefox_session() {
    local profile_dir="$1"
    local user_profile
    user_profile=$(find "$HOME/snap/firefox/common/.mozilla/firefox" "$HOME/.mozilla/firefox" "$HOME/.config/mozilla/firefox" \
        -maxdepth 1 -name "*.default-release" -type d 2>/dev/null | head -1)
    if [ -z "$user_profile" ]; then
        user_profile=$(find "$HOME/snap/firefox/common/.mozilla/firefox" "$HOME/.mozilla/firefox" "$HOME/.config/mozilla/firefox" \
            -maxdepth 1 -name "*.default" -type d 2>/dev/null | head -1)
    fi
    if [ -n "$user_profile" ] && [ -d "$user_profile" ]; then
        echo "  Syncing session from: $user_profile"
        for f in cookies.sqlite logins.json key4.db cert9.db permissions.sqlite \
                 formhistory.sqlite places.sqlite webappsstore.sqlite; do
            [ -f "$user_profile/$f" ] && cp "$user_profile/$f" "$profile_dir/$f" 2>/dev/null
        done
        if [ -d "$user_profile/storage" ]; then
            rsync -a --quiet "$user_profile/storage/" "$profile_dir/storage/" 2>/dev/null
            echo "  Synced localStorage (auth tokens for all sites)"
        fi
        echo "  Session data synced (cookies, logins, bookmarks, localStorage)"
        # Harden permissions on synced credential files
        chmod 700 "$profile_dir"
        chmod 600 "$profile_dir"/{key4.db,cert9.db,logins.json,cookies.sqlite,formhistory.sqlite,permissions.sqlite} 2>/dev/null
        [ -d "$profile_dir/storage" ] && chmod -R go-rwx "$profile_dir/storage"
    else
        echo "  No Firefox profile found to sync cookies from (fresh profile)"
    fi
}

sync_chromium_session() {
    local profile_dir="$1"
    # Chromium/Chrome stores profiles differently
    local user_profile=""
    for candidate in "$HOME/.config/chromium/Default" "$HOME/.config/google-chrome/Default" \
                     "$HOME/.config/chromium/Profile 1" "$HOME/.config/google-chrome/Profile 1"; do
        if [ -d "$candidate" ]; then
            user_profile="$candidate"
            break
        fi
    done
    if [ -n "$user_profile" ]; then
        echo "  Syncing session from: $user_profile"
        mkdir -p "$profile_dir/Default"
        for f in Cookies "Login Data" "Web Data"; do
            [ -f "$user_profile/$f" ] && cp "$user_profile/$f" "$profile_dir/Default/$f" 2>/dev/null
        done
        if [ -d "$user_profile/Local Storage" ]; then
            rsync -a --quiet "$user_profile/Local Storage/" "$profile_dir/Default/Local Storage/" 2>/dev/null
        fi
        echo "  Session data synced"
        # Harden permissions on synced credential files
        chmod 700 "$profile_dir" "$profile_dir/Default" 2>/dev/null
        chmod 600 "$profile_dir/Default/Cookies" "$profile_dir/Default/Login Data" "$profile_dir/Default/Web Data" 2>/dev/null
        [ -d "$profile_dir/Default/Local Storage" ] && chmod -R go-rwx "$profile_dir/Default/Local Storage"
    else
        echo "  No Chromium/Chrome profile found to sync cookies from (fresh profile)"
    fi
}

# Ensure browser profile has required settings on first run
init_browser_profile() {
    local browser="$1"
    local profile_dir="$2"

    mkdir -p "$profile_dir"

    case "$browser" in
        firefox|firefox-esr)
            # user.js: agent-optimized settings (light theme, no telemetry, no popups)
            if [ ! -f "$profile_dir/user.js" ]; then
                local template="$GUAARDVARK_ROOT/data/agent/firefox_profile/user.js"
                if [ -f "$template" ] && [ "$template" != "$profile_dir/user.js" ]; then
                    echo "  Creating initial user.js from template"
                    cp "$template" "$profile_dir/user.js"
                else
                    echo "  Creating default user.js for agent"
                    cat > "$profile_dir/user.js" << 'FIREFOXJS'
// Guaardvark Agent — auto-generated Firefox settings
user_pref("extensions.activeThemeID", "firefox-compact-light@mozilla.org");
user_pref("browser.theme.content-theme", 1);
user_pref("ui.systemUsesDarkTheme", 0);
user_pref("layout.css.prefers-color-scheme.content-override", 1);
user_pref("toolkit.telemetry.enabled", false);
user_pref("datareporting.healthreport.uploadEnabled", false);
user_pref("datareporting.policy.dataSubmissionEnabled", false);
user_pref("permissions.default.desktop-notification", 2);
user_pref("dom.webnotifications.enabled", false);
user_pref("identity.fxaccounts.enabled", false);
user_pref("app.normandy.enabled", false);
user_pref("browser.aboutwelcome.enabled", false);
user_pref("app.update.enabled", false);
user_pref("browser.urlbar.autocomplete.enabled", false);
user_pref("browser.urlbar.suggest.bookmark", false);
user_pref("browser.urlbar.suggest.history", false);
user_pref("browser.urlbar.suggest.openpage", false);
user_pref("browser.urlbar.suggest.searches", false);
user_pref("browser.sessionstore.resume_from_crash", false);
user_pref("media.autoplay.default", 5);
user_pref("dom.ipc.processCount", 2);
user_pref("browser.toolbars.bookmarks.visibility", "never");
user_pref("browser.startup.page", 0);
user_pref("browser.startup.homepage", "about:blank");
user_pref("extensions.pocket.enabled", false);
FIREFOXJS
                fi
            else
                echo "  Firefox user.js exists — not overwriting"
            fi
            # Clean stale lock files
            rm -f "$profile_dir/lock" "$profile_dir/.parentlock" 2>/dev/null
            ;;

        chromium*|google-chrome*)
            # Chromium preferences
            mkdir -p "$profile_dir/Default"
            if [ ! -f "$profile_dir/Default/Preferences" ]; then
                echo "  Creating default Chromium preferences for agent"
                cat > "$profile_dir/Default/Preferences" << 'CHROMEJSON'
{
  "browser": {
    "check_default_browser": false,
    "show_home_button": false
  },
  "homepage": "about:blank",
  "session": {
    "restore_on_startup": 5
  },
  "profile": {
    "default_content_setting_values": {
      "notifications": 2
    }
  },
  "autofill": {
    "enabled": false
  }
}
CHROMEJSON
            fi
            # Clean Chromium lock files
            rm -f "$profile_dir/SingletonLock" "$profile_dir/SingletonSocket" \
                  "$profile_dir/SingletonCookie" 2>/dev/null
            ;;
    esac
}

# ---------------------------------------------------------------------------
# Main actions
# ---------------------------------------------------------------------------

BROWSER=$(detect_browser)
BROWSER_NAME=$(browser_display_name "$BROWSER")
PROFILE_DIR=$(browser_profile_dir "$BROWSER")
ENV_PREFIX=$(browser_env_prefix)

start() {
    # Restrict default permissions — credential files should not be world/group-readable
    umask 077

    echo "Starting Agent Virtual Display (:$DISPLAY_NUM @ ${RESOLUTION%x*})..."

    if [ -z "$BROWSER" ]; then
        echo "  WARNING: No supported browser found (firefox, chromium, or chrome)."
        echo "  Install one with: sudo apt install firefox  OR  sudo apt install chromium-browser"
        echo "  Continuing without browser support..."
    else
        echo "  Browser: $BROWSER_NAME ($BROWSER)"
    fi

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
        # Build browser launch command for the menu
        local browser_cmd=$(browser_launch_cmd "$BROWSER" "$PROFILE_DIR")
        local browser_gvk_cmd=$(browser_launch_cmd "$BROWSER" "$PROFILE_DIR" "http://localhost:5175")
        local menu_env="env $ENV_PREFIX"

        mkdir -p "$HOME/.config/openbox"
        cat > "$HOME/.config/openbox/menu.xml" << OBMENUX
<?xml version="1.0" encoding="utf-8"?>
<openbox_menu xmlns="http://openbox.org/3.4/menu">
  <menu id="root-menu" label="Guaardvark Agent">
    <item label="$BROWSER_NAME">
      <action name="Execute">
        <execute>$menu_env $browser_cmd</execute>
      </action>
    </item>
    <separator label="Tools"/>
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
        <execute>$menu_env $browser_gvk_cmd</execute>
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

    # Browser profile setup
    if [ -n "$BROWSER" ]; then
        sync_browser_session "$BROWSER" "$PROFILE_DIR"
        init_browser_profile "$BROWSER" "$PROFILE_DIR"

        # Auto-launch browser on the virtual display if not already running
        local browser_pattern
        case "$BROWSER" in
            firefox|firefox-esr) browser_pattern="firefox.*$(basename $PROFILE_DIR)" ;;
            *) browser_pattern="$BROWSER.*$(basename $PROFILE_DIR)" ;;
        esac

        if pgrep -f "$browser_pattern" > /dev/null 2>&1; then
            echo "  $BROWSER_NAME already running on :$DISPLAY_NUM"
        else
            local launch_cmd=$(browser_launch_cmd "$BROWSER" "$PROFILE_DIR")
            env $ENV_PREFIX $launch_cmd > /dev/null 2>&1 &
            echo $! > "$PID_DIR/agent_browser.pid"
            sleep 3
            echo "  $BROWSER_NAME launched on :$DISPLAY_NUM"
        fi
    fi

    # VNC server (for watching the agent)
    if pgrep -f "x11vnc.*:$DISPLAY_NUM" > /dev/null 2>&1; then
        echo "  x11vnc already running"
    else
        # Auto-generate VNC password on first run
        VNC_PASSWD_FILE="$GUAARDVARK_ROOT/data/.vnc_passwd"
        if [ ! -f "$VNC_PASSWD_FILE" ]; then
            x11vnc -storepasswd "$(openssl rand -base64 12)" "$VNC_PASSWD_FILE" 2>/dev/null
            chmod 600 "$VNC_PASSWD_FILE"
            echo "  VNC password generated: $VNC_PASSWD_FILE"
        fi

        env -u WAYLAND_DISPLAY -u XDG_SESSION_TYPE \
            DISPLAY=:$DISPLAY_NUM \
            x11vnc -rfbauth "$VNC_PASSWD_FILE" -localhost -forever -shared -rfbport $VNC_PORT \
            -bg -o "$LOG_DIR/x11vnc_agent.log" 2>&1
        sleep 1
        echo "  x11vnc started on port $VNC_PORT (password-protected)"
    fi

    echo ""
    echo "Agent Virtual Display ready!"
    echo "  Display:   :$DISPLAY_NUM"
    echo "  VNC:       localhost:$VNC_PORT"
    echo "  Browser:   $BROWSER_NAME (with your session cookies)"
    echo ""
    echo "Connect TigerVNC to localhost:$VNC_PORT to watch the agent."
}

stop() {
    echo "Stopping Agent Virtual Display..."

    for proc in agent_browser agent_firefox tint2 openbox matchbox xvfb; do
        pid_file="$PID_DIR/${proc}.pid"
        if [ -f "$pid_file" ]; then
            pid=$(cat "$pid_file")
            kill $pid 2>/dev/null && echo "  Stopped $proc (PID $pid)" || echo "  $proc not running"
            rm -f "$pid_file"
        fi
    done

    # Kill x11vnc for our display
    pkill -f "x11vnc.*:$DISPLAY_NUM" 2>/dev/null && echo "  Stopped x11vnc" || echo "  x11vnc not running"
    # Kill any remaining browser on agent profile
    pkill -f "firefox.*firefox_profile" 2>/dev/null
    pkill -f "chrom.*chromium_profile" 2>/dev/null

    echo "Agent Virtual Display stopped."
}

status() {
    echo "Agent Virtual Display Status:"
    echo "  Browser:  $BROWSER_NAME ($BROWSER)"
    pgrep -f "Xvfb :$DISPLAY_NUM" > /dev/null 2>&1 && echo "  Xvfb:     RUNNING" || echo "  Xvfb:     STOPPED"
    pgrep -f "openbox" > /dev/null 2>&1 && echo "  Openbox:  RUNNING" || echo "  Openbox:  STOPPED"
    pgrep -f "tint2" > /dev/null 2>&1 && echo "  Tint2:    RUNNING" || echo "  Tint2:    STOPPED"

    local browser_running=false
    case "$BROWSER" in
        firefox|firefox-esr) pgrep -f "firefox.*firefox_profile" > /dev/null 2>&1 && browser_running=true ;;
        *) pgrep -f "$BROWSER.*$(basename $PROFILE_DIR)" > /dev/null 2>&1 && browser_running=true ;;
    esac
    $browser_running && echo "  Browser:  RUNNING" || echo "  Browser:  STOPPED"

    pgrep -f "x11vnc.*:$DISPLAY_NUM" > /dev/null 2>&1 && echo "  x11vnc:   RUNNING (port $VNC_PORT)" || echo "  x11vnc:   STOPPED"
}

case "${1:-start}" in
    start)  start ;;
    stop)   stop ;;
    status) status ;;
    restart) stop; sleep 2; start ;;
    *) echo "Usage: $0 {start|stop|status|restart}" ;;
esac
