#!/bin/bash
# Start the Agent Vision Control virtual display
# Usage: ./scripts/start_agent_display.sh [start|stop|status|restart]
#
# Environment variables:
#   GUAARDVARK_AGENT_BROWSER   - Browser to use: firefox|chromium|chrome (auto-detected if unset)
#   GUAARDVARK_AGENT_DISPLAY   - X display number (default: 99)
#   GUAARDVARK_AGENT_VNC_PORT  - VNC port (default: 5999)
#   GUAARDVARK_AGENT_RESOLUTION - Display resolution (default: 1000x1000x24)

GUAARDVARK_ROOT="${GUAARDVARK_ROOT:-$(dirname $(dirname $(readlink -f $0)))}"
DISPLAY_NUM="${GUAARDVARK_AGENT_DISPLAY:-99}"
# 1000x1000 matches Gemma4's internal coordinate grid (Google's box_2d format
# normalizes to 1000). Identity mapping at this resolution — no scaling. A
# bump to 1024x1024 in May 2026 introduced a quiet drift that left all servo
# clicks clustered around screen X-center (Gemma4's "I'm unsure" default).
RESOLUTION="${GUAARDVARK_AGENT_RESOLUTION:-1000x1000x24}"
VNC_PORT="${GUAARDVARK_AGENT_VNC_PORT:-5999}"
PID_DIR="$GUAARDVARK_ROOT/pids"
LOG_DIR="$GUAARDVARK_ROOT/logs"
DATA_DIR="$GUAARDVARK_ROOT/data/agent"

AGENT_FILES_DIR="$GUAARDVARK_ROOT/data/agent/files"
# Agent desktop dir (separate from user's ~/Desktop to avoid collision)
# Option B: ~/.agent_desktop/ for clean separation
AGENT_DESKTOP_DIR="$HOME/.agent_desktop"
# Wrapper that launches Firefox with --profile=<agent profile> and Wayland-X11
# env hygiene. Every Firefox launcher (desktop icon, panel, future tools) MUST
# go through this — invoking /usr/bin/firefox directly grabs the user's default
# profile and locks it, breaking the user's normal Firefox.
AGENT_FIREFOX_LAUNCHER="$GUAARDVARK_ROOT/scripts/agent_firefox_launch.sh"
mkdir -p "$PID_DIR" "$LOG_DIR" "$DATA_DIR" "$AGENT_FILES_DIR" "$AGENT_DESKTOP_DIR"

# ---------------------------------------------------------------------------
# Desktop environment setup — XFCE on Xvfb
#
# What used to be a hodgepodge (openbox + tint2 + xsetroot wallpaper +
# pcmanfm --desktop for icons + a custom tkinter launcher) is now a single
# real XFCE session running on :99. Same display surface the user would
# see if they VNC'd into the box — vision models recognize a normal Ubuntu
# desktop instantly.
#
# Wallpaper, panel, desktop icons, app menu, file manager (Thunar), and
# right-click menu all come from XFCE itself. No more custom widgets.
# ---------------------------------------------------------------------------

seed_agent_desktop() {
    # Populate the agent's isolated desktop dir with starter folders the
    # vision model recognizes (Documents, Downloads, Pictures, Trash —
    # standard XDG names). Idempotent.
    mkdir -p "$AGENT_DESKTOP_DIR"/{Documents,Downloads,Pictures,"Outreach Drafts",Trash}

    # Firefox launcher icon. Without this, fresh clones boot to a desktop
    # with folder icons but no app launcher — and every prompt/recipe that
    # says "click the Firefox icon on the desktop" becomes priming for
    # hallucination. Created idempotently so existing installs stay put.
    if [ ! -f "$AGENT_DESKTOP_DIR/Firefox.desktop" ]; then
        cat > "$AGENT_DESKTOP_DIR/Firefox.desktop" << FIREFOXDESKTOP
[Desktop Entry]
Version=1.0
Name=Firefox
Exec=$AGENT_FIREFOX_LAUNCHER
Terminal=false
Type=Application
Icon=firefox
Categories=Network;WebBrowser;
FIREFOXDESKTOP
    fi
    # XFCE 4.18+ pops an "Untrusted application launcher" dialog unless the
    # file is executable AND its metadata::xfce-exe-checksum matches the
    # current SHA-256 of its contents. The Mark Executable button in that
    # dialog only does the chmod, not the checksum — so it keeps returning.
    # Set both here, every start, so the dialog stops appearing.
    chmod +x "$AGENT_DESKTOP_DIR/Firefox.desktop"
    if command -v gio >/dev/null 2>&1; then
        ff_sha=$(sha256sum "$AGENT_DESKTOP_DIR/Firefox.desktop" | awk '{print $1}')
        gio set "$AGENT_DESKTOP_DIR/Firefox.desktop" metadata::xfce-exe-checksum "$ff_sha" 2>/dev/null || true
    fi
}

seed_agent_panel_firefox() {
    # XFCE's default panel ships a "Web Browser" launcher whose Exec is
    # `exo-open --launch WebBrowser`. That dispatcher resolves to the system
    # /usr/share/applications/firefox.desktop, which calls plain `firefox` —
    # no --profile, no env hygiene — so the agent's bottom-bar Firefox icon
    # ends up grabbing the host user's default profile and locking it.
    # ("Firefox is already running, but is not responding" on the user side.)
    #
    # Sweep any panel launcher whose Exec matches that dispatcher and rewrite
    # it to call our wrapper directly. Idempotent — only patches files that
    # still have the broken Exec, leaves already-fixed ones alone.
    local panel_dir="$1/xfce4/panel"
    [ -d "$panel_dir" ] || return 0
    while IFS= read -r -d '' launcher; do
        if grep -q '^Exec=exo-open --launch WebBrowser' "$launcher"; then
            sed -i \
                -e "s|^Exec=exo-open --launch WebBrowser.*|Exec=$AGENT_FIREFOX_LAUNCHER|" \
                -e 's|^Icon=org.xfce.webbrowser$|Icon=firefox|' \
                -e 's|^Name=Web Browser$|Name=Firefox|' \
                -e 's|^Comment=Browse the web$|Comment=Open Firefox in the agent'"'"'s isolated profile|' \
                "$launcher"
        fi
    done < <(find "$panel_dir" -name '*.desktop' -print0 2>/dev/null)
}

write_agent_xdg_user_dirs() {
    # Drop a user-dirs.dirs into the agent's XDG_CONFIG_HOME so every
    # XDG-aware app (xfdesktop, Thunar, GTK file dialogs) points at the
    # agent's folders, not the user's real ones.
    local config_home="$1"
    mkdir -p "$config_home"
    cat > "$config_home/user-dirs.dirs" << XDGDIRS
# Generated by start_agent_display.sh — isolates the agent's standard
# folders from the user's. Edit AGENT_DESKTOP_DIR in the script if you
# want to relocate.
XDG_DESKTOP_DIR="$AGENT_DESKTOP_DIR"
XDG_DOCUMENTS_DIR="$AGENT_DESKTOP_DIR/Documents"
XDG_DOWNLOAD_DIR="$AGENT_DESKTOP_DIR/Downloads"
XDG_PICTURES_DIR="$AGENT_DESKTOP_DIR/Pictures"
XDG_MUSIC_DIR="$AGENT_DESKTOP_DIR"
XDG_VIDEOS_DIR="$AGENT_DESKTOP_DIR"
XDG_TEMPLATES_DIR="$AGENT_DESKTOP_DIR"
XDG_PUBLICSHARE_DIR="$AGENT_DESKTOP_DIR"
XDGDIRS
    chmod 644 "$config_home/user-dirs.dirs"

    # Also write user-dirs.locale to silence the GTK dialog that would
    # otherwise pop up offering to rename them.
    if [ ! -f "$config_home/user-dirs.locale" ]; then
        echo "en_US" > "$config_home/user-dirs.locale"
    fi
}

ensure_xfwm4_no_compositing() {
    # xfwm4 ships with use_compositing=true by default. On Xvfb (which only
    # has Mesa's software GLX, no real GPU), that compositor leaves visible
    # window trails / streaks behind moved or resized windows — DAMAGE events
    # fire but the unaccelerated compositor can't keep up, so unrepainted
    # regions stay on screen. The May 2026 servo screenshots showed stacked
    # ghost title bars across the desktop because of this.
    #
    # Flip compositing off. xfwm4 falls back to plain X drawing, which Xvfb
    # handles natively — no trails, no GL path involved.
    local channel_dir="$1/xfce4/xfconf/xfce-perchannel-xml"
    local channel_file="$channel_dir/xfwm4.xml"
    mkdir -p "$channel_dir"
    if [ ! -f "$channel_file" ]; then
        cat > "$channel_file" << 'XFWM4XML'
<?xml version="1.0" encoding="UTF-8"?>

<channel name="xfwm4" version="1.0">
  <property name="general" type="empty">
    <property name="use_compositing" type="bool" value="false"/>
  </property>
</channel>
XFWM4XML
        return 0
    fi
    if grep -q 'name="use_compositing"' "$channel_file"; then
        sed -i 's|<property name="use_compositing" type="bool" value="true"/>|<property name="use_compositing" type="bool" value="false"/>|' "$channel_file"
    else
        sed -i 's|</channel>|  <property name="general" type="empty">\n    <property name="use_compositing" type="bool" value="false"/>\n  </property>\n</channel>|' "$channel_file"
    fi
}

seed_agent_autostart_overrides() {
    # /etc/xdg/autostart/*.desktop entries fire when xfce4-session starts.
    # Drop Hidden=true overrides into the agent's XDG_CONFIG_HOME/autostart/
    # with the same basename — XFCE merges the two and short-circuits the
    # system entry. Scoped to the agent's config dir, so the host GNOME
    # session is untouched.
    #
    # Targets are the autostart entries responsible for the dbus connection
    # leak documented in stop()/section 2b:
    #   geoclue-demo-agent              → geoclue-2.0/demos/agent
    #   tracker-miner-fs-3              → tracker-miner-fs (desktop-search indexer)
    #   org.gnome.Evolution-alarm-notify → pulls evolution-source-registry,
    #                                      evolution-calendar-factory,
    #                                      evolution-addressbook-factory
    #
    # Suppressing the autostart means the helpers never spawn — the kill
    # loop in section 2b stays as belt-and-suspenders for legacy sessions.
    local autostart_dir="$1/autostart"
    mkdir -p "$autostart_dir"
    for name in geoclue-demo-agent tracker-miner-fs-3 org.gnome.Evolution-alarm-notify; do
        [ -f "/etc/xdg/autostart/$name.desktop" ] || continue
        cat > "$autostart_dir/$name.desktop" << OVERRIDE
[Desktop Entry]
Type=Application
Name=$name (disabled for agent display)
Hidden=true
NoDisplay=true
X-GNOME-Autostart-enabled=false
OVERRIDE
    done
}

seed_agent_dbus_service_overrides() {
    # The agent's session bus (spun up by dbus-run-session) reads service
    # files from XDG_DATA_HOME/dbus-1/services first, then XDG_DATA_DIRS,
    # then /usr/share. Dropping a service file with the same Name= here
    # overrides the system one for the agent's bus only.
    #
    # xdg-desktop-portal is D-Bus-activated (no autostart entry), triggered
    # by Firefox file dialogs and GTK apps that opt into portals. Pointing
    # its Exec at /bin/false makes activation fail benignly — Firefox falls
    # back to its native GtkFileChooser, Thunar likewise has non-portal
    # paths. No portal = no xdg-desktop-portal-gnome/gtk backend either.
    local services_dir="$1/dbus-1/services"
    mkdir -p "$services_dir"
    cat > "$services_dir/org.freedesktop.portal.Desktop.service" << 'PORTALOVERRIDE'
[D-BUS Service]
Name=org.freedesktop.portal.Desktop
Exec=/bin/false
PORTALOVERRIDE
}

ensure_xfdesktop_single_click() {
    # xfdesktop defaults to double-click-to-activate desktop icons. The
    # vision agent's servo sends a single click — XFCE treats it as a
    # "select" (subtle highlight, below the pixel-diff threshold), and
    # the see-think-act loop flags every icon launch as failed. Flipping
    # /desktop-icons/single-click=true lets one click fire the launcher.
    #
    # We write directly into the per-channel XML so the setting is in
    # place before xfconfd starts; doing it via xfconf-query would need
    # the agent's DBUS_SESSION_BUS_ADDRESS, which only exists after the
    # XFCE session is already running.
    local channel_dir="$1/xfce4/xfconf/xfce-perchannel-xml"
    local channel_file="$channel_dir/xfce4-desktop.xml"
    mkdir -p "$channel_dir"
    if [ ! -f "$channel_file" ]; then
        cat > "$channel_file" << 'XFCEDESKTOPXML'
<?xml version="1.0" encoding="UTF-8"?>

<channel name="xfce4-desktop" version="1.0">
  <property name="desktop-icons" type="empty">
    <property name="single-click" type="bool" value="true"/>
  </property>
</channel>
XFCEDESKTOPXML
        return 0
    fi
    if grep -q 'name="single-click"' "$channel_file"; then
        return 0
    fi
    if grep -q 'name="desktop-icons"' "$channel_file"; then
        sed -i 's|<property name="desktop-icons" type="empty">|<property name="desktop-icons" type="empty">\n    <property name="single-click" type="bool" value="true"/>|' "$channel_file"
    else
        sed -i 's|</channel>|  <property name="desktop-icons" type="empty">\n    <property name="single-click" type="bool" value="true"/>\n  </property>\n</channel>|' "$channel_file"
    fi
}

start_xfce_session() {
    # Start an XFCE session on the virtual display via dbus-run-session,
    # which gives XFCE the per-session DBus it expects without a real
    # login manager (gdm3/lightdm). Works under Xvfb when the inherited
    # host-session env is scrubbed first.
    #
    # Why `env -i` here: a naive launch inherits DBUS_SESSION_BUS_ADDRESS,
    # XDG_SESSION_ID/TYPE, XDG_RUNTIME_DIR, etc. from the user's host GNOME
    # session — xfce4-session sees those and refuses to start with
    # "Another session manager is already running", and xfconfd dies fighting
    # for the org.xfce.Xfconf name on the wrong DBus. Clean slate fixes it.
    #
    # Why a dedicated XDG_RUNTIME_DIR: gvfs/at-spi/xdg-app-helper all want
    # to mount/socket under XDG_RUNTIME_DIR. Sharing /run/user/$UID with
    # the host session triggers `Permission denied` and partial brokenness.
    #
    # Why a dedicated XDG_CONFIG_HOME: xfconf, panel layout, and desktop
    # icon state live under XDG_CONFIG_HOME. Pointing it at an agent-only
    # dir means the agent's XFCE can't trample any future host XFCE config,
    # and vice versa. The agent's settings are also backed up cleanly with
    # the rest of data/agent/.
    local agent_config_home="$DATA_DIR/xfce_config"
    local agent_data_home="$DATA_DIR/data_home"
    # Run the panel-launcher patch every invocation, even on the early-return
    # path below: previous start_agent_display.sh runs (before this fix landed)
    # left bad Exec lines on disk, and a no-op restart should heal them.
    seed_agent_panel_firefox "$agent_config_home"

    # Same idempotent heal-on-every-invocation pattern for the dbus-leak
    # suppression files. Cheap, and lets older installs pick up the new
    # overrides without a rebuild.
    seed_agent_autostart_overrides "$agent_config_home"
    seed_agent_dbus_service_overrides "$agent_data_home"

    # Re-stamp the desktop launchers on EVERY invocation too, not just on cold
    # start. Mid-session edits to Firefox.desktop (e.g. icon swaps) change the
    # file's SHA-256 but leave the stored xfce-exe-checksum xattr pointing at
    # the old hash, which traps every click in XFCE 4.18+'s "Untrusted
    # application launcher" dialog. seed_agent_desktop is idempotent: it only
    # writes Firefox.desktop if missing, but always re-applies chmod +x and
    # gio set metadata::xfce-exe-checksum. Running it here heals stale trust
    # even when the XFCE session is already up and we early-return below.
    seed_agent_desktop

    if pgrep -f "xfce4-session" > /dev/null 2>&1; then
        # Only count it if it's on OUR display — host session is also xfce-shaped.
        for pid in $(pgrep -f "xfce4-session" 2>/dev/null); do
            if grep -qaz "DISPLAY=:$DISPLAY_NUM" /proc/$pid/environ 2>/dev/null; then
                echo "  XFCE session already running on :$DISPLAY_NUM (PID $pid)"
                return 0
            fi
        done
    fi

    local agent_runtime_dir="/tmp/xdg-runtime-agent-$DISPLAY_NUM"
    mkdir -p "$agent_runtime_dir"
    chmod 700 "$agent_runtime_dir"

    write_agent_xdg_user_dirs "$agent_config_home"
    ensure_xfdesktop_single_click "$agent_config_home"
    ensure_xfwm4_no_compositing "$agent_config_home"

    # env -i wipes the inherited environment; we re-inject only what XFCE
    # legitimately needs. Notably absent: DBUS_SESSION_BUS_ADDRESS,
    # XDG_SESSION_*, GNOME_*, WAYLAND_DISPLAY, DESKTOP_SESSION.
    # XDG_DESKTOP_DIR pins xfdesktop to the agent's dir directly (some
    # versions of glib's g_get_user_special_dir read the env var before
    # falling back to user-dirs.dirs; we set both for belt-and-suspenders).
    # XDG_DATA_HOME points at an agent-private data dir so the dbus service
    # override (seed_agent_dbus_service_overrides) actually wins over
    # /usr/share/dbus-1/services on the agent's session bus.
    # GIO_USE_VOLUME_MONITOR=unix tells GIO to use only its built-in unix
    # monitor — gvfs-{udisks2,afc,goa,gphoto2,mtp}-volume-monitor are never
    # queried, never D-Bus-activated, never leak.
    env -i \
        HOME="$HOME" \
        USER="$USER" \
        LOGNAME="${LOGNAME:-$USER}" \
        SHELL="${SHELL:-/bin/bash}" \
        PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
        LANG="${LANG:-en_US.UTF-8}" \
        LC_ALL="${LC_ALL:-en_US.UTF-8}" \
        DISPLAY=":$DISPLAY_NUM" \
        XDG_RUNTIME_DIR="$agent_runtime_dir" \
        XDG_CONFIG_HOME="$agent_config_home" \
        XDG_DATA_HOME="$agent_data_home" \
        XDG_DESKTOP_DIR="$AGENT_DESKTOP_DIR" \
        XDG_CURRENT_DESKTOP="XFCE" \
        XDG_SESSION_DESKTOP="xfce" \
        GIO_USE_VOLUME_MONITOR=unix \
        dbus-run-session -- startxfce4 \
        >> "$LOG_DIR/xfce_agent.log" 2>&1 &
    echo $! > "$PID_DIR/xfce.pid"
    # Give the session a moment to bring up xfwm4, xfdesktop, xfce4-panel.
    sleep 3
    # On a true fresh install the panel files don't exist until xfce4-panel
    # writes them above; re-run the patch now that they should be on disk.
    seed_agent_panel_firefox "$agent_config_home"
    echo "  XFCE session started (PID $(cat $PID_DIR/xfce.pid), log: $LOG_DIR/xfce_agent.log)"
    echo "  Agent desktop:    $AGENT_DESKTOP_DIR"
    echo "  Agent XFCE config: $agent_config_home"
}

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
            echo "$browser --no-remote --remote-debugging-port 9222 --profile $profile_dir $url"
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
        Xvfb :$DISPLAY_NUM -screen 0 $RESOLUTION -ac -s 0 -dpms >/dev/null 2>&1 &
        echo $! > "$PID_DIR/xvfb.pid"
        sleep 1
        echo "  Xvfb started (PID $(cat $PID_DIR/xvfb.pid))"
    fi

    # Desktop environment — single XFCE session brings up wallpaper, panel,
    # desktop icons, app menu, and Thunar file manager. Replaces the previous
    # openbox + tint2 + wallpaper + pcmanfm + custom tkinter launcher stack.
    start_xfce_session

    # Browser profile prep ONLY. We sync cookies/session into the agent's
    # profile dir so when the agent decides to launch Firefox itself (via
    # the XFCE app menu, a .desktop shortcut, or its own click logic), it
    # gets the user's logged-in profile. We do NOT auto-launch the browser
    # — the agent boots to a clean desktop and opens what it needs.
    if [ -n "$BROWSER" ]; then
        sync_browser_session "$BROWSER" "$PROFILE_DIR"
        init_browser_profile "$BROWSER" "$PROFILE_DIR"
    fi

    # VNC server (for watching the agent) — passwordless, localhost-only.
    # Local single-user machine; -localhost binds 127.0.0.1 only, so the
    # network boundary is the security boundary. No password to type.
    if pgrep -f "x11vnc.*-rfbport $VNC_PORT" > /dev/null 2>&1; then
        echo "  x11vnc already running"
    else
        env -u WAYLAND_DISPLAY -u XDG_SESSION_TYPE \
            DISPLAY=:$DISPLAY_NUM \
            x11vnc -nopw -localhost -forever -shared -rfbport $VNC_PORT \
            -bg -o "$LOG_DIR/x11vnc_agent.log" >/dev/null 2>&1
        sleep 1
        echo "  x11vnc started on port $VNC_PORT (passwordless, localhost-only)"
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

    # Helper: kill processes by pattern only when they're on OUR display.
    # Each process gets its own /proc/$pid/environ scan so we don't touch
    # the user's host-session XFCE (which is also xfce4-session, just on a
    # different DISPLAY).
    kill_on_agent_display() {
        local pattern="$1" label="$2"
        for pid in $(pgrep -f "$pattern" 2>/dev/null); do
            if grep -qaz "DISPLAY=:$DISPLAY_NUM" /proc/$pid/environ 2>/dev/null; then
                kill $pid 2>/dev/null && echo "  Killed $label (PID $pid)"
            fi
        done
    }

    # 1. PID files first (clean shutdown path).
    for proc in xfce agent_browser agent_firefox \
                pcmanfm nautilus tint2 openbox matchbox xvfb; do
        pid_file="$PID_DIR/${proc}.pid"
        if [ -f "$pid_file" ]; then
            pid=$(cat "$pid_file")
            kill $pid 2>/dev/null && echo "  Stopped $proc (PID $pid)" || true
            rm -f "$pid_file"
        fi
    done

    # 2. XFCE session — scoped strictly to our DISPLAY so the host session survives.
    for pat in xfce4-session xfdesktop xfce4-panel xfwm4 xfsettingsd xfce4-power-manager \
               xfconfd Thunar xfce4-notifyd light-locker; do
        kill_on_agent_display "$pat" "$pat"
    done

    # 2b. Systemd-spawned helpers that XFCE pulls in via xdg-autostart / portal /
    # gvfs activation. They run under user@1000.service, not the XFCE session,
    # so they survive xfce4-session teardown — that's the source of the dbus
    # connection leak ("LimitsExceeded for UID 1000") after many restarts.
    # The kill_on_agent_display helper checks /proc/$pid/environ for DISPLAY=:99
    # so the user's real GNOME session is never touched.
    for pat in evolution-source-registry evolution-calendar-factory \
               evolution-addressbook-factory gvfs-udisks2-volume-monitor \
               gvfs-afc-volume-monitor gvfs-goa-volume-monitor gvfs-gphoto2-volume-monitor \
               gvfs-mtp-volume-monitor goa-daemon "geoclue-2.0/demos/agent" \
               xdg-desktop-portal tracker-miner-fs xfce-polkit; do
        kill_on_agent_display "$pat" "$pat"
    done

    # 3. Window-manager / panel orphans from older runs (no PID files).
    kill_on_agent_display "openbox" "orphan openbox"
    kill_on_agent_display "tint2" "orphan tint2"
    kill_on_agent_display "pcmanfm.*--desktop" "orphan pcmanfm"

    # 4. Browser + display server + VNC.
    pkill -f "firefox.*firefox_profile" 2>/dev/null && echo "  Stopped agent Firefox" || true
    pkill -f "chrom.*chromium_profile" 2>/dev/null
    pkill -f "Xvfb :$DISPLAY_NUM" 2>/dev/null && echo "  Killed Xvfb :$DISPLAY_NUM" || true
    pkill -f "x11vnc.*-rfbport $VNC_PORT" 2>/dev/null && echo "  Stopped x11vnc" || echo "  x11vnc not running"

    # 5. Last resort: free the VNC port if anything still holds it.
    local port_holder=$(lsof -ti :$VNC_PORT 2>/dev/null)
    if [ -n "$port_holder" ]; then
        kill $port_holder 2>/dev/null && echo "  Killed process holding port $VNC_PORT (PID $port_holder)"
    fi

    echo "Agent Virtual Display stopped."
}

status() {
    echo "Agent Virtual Display Status:"
    echo "  Browser:  $BROWSER_NAME ($BROWSER)"
    pgrep -f "Xvfb :$DISPLAY_NUM" > /dev/null 2>&1 && echo "  Xvfb:     RUNNING" || echo "  Xvfb:     STOPPED"

    # XFCE on OUR display only — pgrep would otherwise hit the user's
    # host session too.
    local xfce_pid=""
    for pid in $(pgrep -f "xfce4-session" 2>/dev/null); do
        if grep -qaz "DISPLAY=:$DISPLAY_NUM" /proc/$pid/environ 2>/dev/null; then
            xfce_pid=$pid
            break
        fi
    done
    [ -n "$xfce_pid" ] && echo "  XFCE:     RUNNING (PID $xfce_pid)" || echo "  XFCE:     STOPPED"

    local browser_running=false
    case "$BROWSER" in
        firefox|firefox-esr) pgrep -f "firefox.*firefox_profile" > /dev/null 2>&1 && browser_running=true ;;
        *) pgrep -f "$BROWSER.*$(basename $PROFILE_DIR)" > /dev/null 2>&1 && browser_running=true ;;
    esac
    $browser_running && echo "  Browser:  RUNNING" || echo "  Browser:  STOPPED (agent will launch on demand)"

    pgrep -f "x11vnc.*-rfbport $VNC_PORT" > /dev/null 2>&1 && echo "  x11vnc:   RUNNING (port $VNC_PORT)" || echo "  x11vnc:   STOPPED"
}

case "${1:-start}" in
    start)  start ;;
    stop)   stop ;;
    status) status ;;
    restart) stop; sleep 2; start ;;
    *) echo "Usage: $0 {start|stop|status|restart}" ;;
esac

# ---------------------------------------------------------------------------
# Manual verification after running start.sh:
# ---------------------------------------------------------------------------
#   1. Connect noVNC at localhost:5999 (password: guaardvark)
#   2. Should see: blue/blue-gradient wallpaper, Documents/Downloads/Outreach Drafts
#      folder icons on desktop, clickable Firefox icon, bottom taskbar
#   3. Take a screenshot for documentation: scrot data/training/desktop_baseline.png
#      (or use the agent_screen_capture tool via the API)
# ---------------------------------------------------------------------------
