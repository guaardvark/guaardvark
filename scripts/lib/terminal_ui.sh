#!/bin/bash
# scripts/lib/terminal_ui.sh — pinned boot feed for start.sh and stop.sh.
#
# One palette, one painter. On a terminal the mascot plays once, then a fixed
# phase board is redrawn in place (one bar, no step numbers, one activity
# line). Pip, apt, and the boot children feed that line; their transcripts stay
# in the log. NO_COLOR, TERM=dumb, and a pipe print plain scrolling lines.
#
# While the board is up, only the painter writes the terminal. Everyone else
# writes files under .start_cache/:
#   boot.status   phase, label, activity, level (parent only, replaced atomically)
#   boot.live     latest tool line: "info<TAB>text" (children and _vader_run)
#   boot.hold     "1" while a sudo prompt owns the terminal
#   boot.reset    next paint starts below the prompt instead of cursor-up
#   boot.rows     how many lines the last paint used (so close can erase them)
#   boot.closed   painter must exit; close sets this before it erases
#   boot.lock     directory held for the duration of one frame write
#
# A child sourced with GUAARDVARK_BOOT_FRAME=1 writes boot.live and does not
# paint. Bash 3.2: no associative arrays.

_VADER_LIB_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd) || _VADER_LIB_DIR=""
_VADER_ROOT_HINT=$(cd "${_VADER_LIB_DIR}/../.." && pwd) || _VADER_ROOT_HINT=""

_vader_init_palette() {
    _VADER_TTY=0
    _VADER_COLOR=1
    _VADER_TRUE=1
    # A pipe is a transcript, not a panel. Same plain lines as NO_COLOR and dumb.
    if [ -n "${NO_COLOR:-}" ] || [ "${TERM:-}" = "dumb" ] || [ ! -t 1 ]; then
        _VADER_COLOR=0
    fi
    if [ "$_VADER_COLOR" = 1 ]; then
        local nc
        nc=$(tput colors 2>/dev/null || echo 256)
        case "$nc" in
            ''|*[!0-9]*) nc=256 ;;
        esac
        if [ "$nc" -lt 16 ]; then
            _VADER_COLOR=0
        elif [ "$nc" -lt 256 ] && [ "${COLORTERM:-}" != "truecolor" ] && [ "${COLORTERM:-}" != "24bit" ]; then
            _VADER_TRUE=0
        fi
    fi
    VADER_RESET='\033[0m'
    VADER_BOLD='\033[1m'
    if [ "$_VADER_COLOR" != 1 ]; then
        VADER_ACCENT=''
        VADER_OK=''
        VADER_WARN=''
        VADER_BAD=''
        VADER_INK=''
        VADER_DIM=''
        VADER_MUTED=''
        VADER_BRIGHT=''
        VADER_RESET=''
        VADER_BOLD=''
        _VADER_TRUE=0
    elif [ "$_VADER_TRUE" = 1 ]; then
        VADER_ACCENT='\033[38;2;72;64;255m'
        VADER_BRIGHT='\033[38;2;186;178;255m'
        VADER_INK='\033[38;2;236;240;255m'
        VADER_DIM='\033[38;2;148;156;198m'
        VADER_MUTED='\033[38;2;58;64;110m'
        VADER_OK='\033[38;2;80;220;176m'
        VADER_WARN='\033[38;2;255;196;96m'
        VADER_BAD='\033[38;2;255;96;112m'
    else
        VADER_ACCENT='\033[38;5;99m'
        VADER_BRIGHT='\033[38;5;141m'
        VADER_INK='\033[38;5;255m'
        VADER_DIM='\033[38;5;146m'
        VADER_MUTED='\033[38;5;60m'
        VADER_OK='\033[38;5;79m'
        VADER_WARN='\033[38;5;222m'
        VADER_BAD='\033[38;5;203m'
    fi
    # Old names, so a sourced script that still says VADER_RED keeps the accent.
    VADER_RED=$VADER_ACCENT
    VADER_RED_DARK=$VADER_BAD
    VADER_RED_LIGHT=$VADER_WARN
    VADER_GRAY=$VADER_DIM
    VADER_GRAY_DARK=$VADER_MUTED
    VADER_WHITE=$VADER_INK
    VADER_WHITE_DIM=$VADER_DIM

    if [ -t 1 ] && [ "$_VADER_COLOR" = 1 ]; then
        _VADER_TTY=1
    fi
    _VADER_OUT=/dev/stdout
    if [ "$_VADER_TTY" = 1 ] && (: > /dev/tty) 2>/dev/null; then
        _VADER_OUT=/dev/tty
    fi
}

_vader_init_palette

_VADER_SPIN_PID=""
_VADER_FX_PID=""
_VADER_PAINT_PID=""
_VADER_PHASE_FILE=""
_VADER_BOARD=0
_VADER_CHILD=0
_VADER_MODE=""
_VADER_PHASE=1
_VADER_TOTAL=11
_VADER_LABEL=""
_VADER_ACTIVITY=""
_VADER_LEVEL=info
_VADER_LOG=""
_VADER_NAMES=""
_VADER_SKIPS=""
_VADER_DIR=""
_VADER_TAIL_SHOWN=0
_VADER_FRAMES=(⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏)

# Pixel mascot, 19 cells wide. Eyes are the two gaps in the face row.
_VADER_MASCOT=(
    '    ...     ..     '
    '    .... . ....    '
    '     .#..+..#.     '
    '      .#####.      '
    '     +#######.     '
    '     +## .# ..     '
    '     +#######..    '
    '     ++#######..   '
    '     #######+###.  '
    '    .#######..+###.'
    '    .########. ....'
    '    .########. .##.'
    '   .#########.     '
    '   .##########.    '
    '  .#+#########..   '
    '  .++######..##.   '
    ' .+..##########.   '
    '    .##########.   '
    '   .##+#######.    '
    '   .####++++#.     '
    ' .##+.+#.  .#.     '
    '.#++. .##. .##.    '
)

_vader_root() {
    if [ -n "${SCRIPT_DIR:-}" ] && [ -d "${SCRIPT_DIR}" ]; then
        printf '%s' "$SCRIPT_DIR"
        return
    fi
    if [ -n "${_VADER_ROOT_HINT:-}" ]; then
        printf '%s' "$_VADER_ROOT_HINT"
        return
    fi
    printf '%s' "$PWD"
}

# The painter is a background job, so its stdin is /dev/null. tput then
# reports the fallback size (24 lines) and the rail collapses into a strip
# that cursor-up cannot cover. Read the controlling terminal instead.
_vader_read_winsize() {
    local sz rows cols
    rows=0
    cols=0
    sz=$(stty size < /dev/tty 2>/dev/null | tr -d '\r' || true)
    rows=${sz%%[[:space:]]*}
    cols=${sz##*[[:space:]]}
    case "$rows" in ''|*[!0-9]*) rows=0 ;; esac
    case "$cols" in ''|*[!0-9]*) cols=0 ;; esac
    if [ "$rows" -lt 1 ]; then
        rows=${LINES:-0}
        case "$rows" in ''|*[!0-9]*) rows=0 ;; esac
    fi
    if [ "$cols" -lt 1 ]; then
        cols=${COLUMNS:-0}
        case "$cols" in ''|*[!0-9]*) cols=0 ;; esac
    fi
    if [ "$cols" -lt 1 ]; then
        cols=$(tput cols < /dev/tty 2>/dev/null || echo 0)
        case "$cols" in ''|*[!0-9]*) cols=0 ;; esac
    fi
    if [ "$rows" -lt 1 ]; then
        rows=$(tput lines < /dev/tty 2>/dev/null || echo 0)
        case "$rows" in ''|*[!0-9]*) rows=0 ;; esac
    fi
    if [ "$cols" -lt 1 ]; then
        cols=80
    fi
    if [ "$rows" -lt 1 ]; then
        rows=24
    fi
    _VADER_COLS=$cols
    _VADER_ROWS=$rows
}

_vader_cols() {
    _vader_read_winsize
    printf '%s' "$_VADER_COLS"
}

_vader_lines() {
    _vader_read_winsize
    printf '%s' "$_VADER_ROWS"
}

_vader_pad() {
    local width="$1" cols pad
    cols=$(_vader_cols)
    pad=$(( (cols - width) / 2 ))
    if [ "$pad" -lt 1 ]; then
        pad=1
    fi
    if [ "$pad" -gt 12 ]; then
        pad=6
    fi
    printf '%*s' "$pad" ''
}

_vader_version() {
    local v="dev" f root
    root=$(_vader_root)
    f="$root/VERSION"
    if [ -f "$f" ]; then
        v=$(tr -d '[:space:]' < "$f")
    fi
    if [ -z "$v" ]; then
        v="dev"
    fi
    printf '%s' "$v"
}

_vader_ltrim() {
    local t="$1"
    printf '%s' "${t#"${t%%[![:space:]]*}"}"
}

_vader_clean() {
    local t="$1"
    t=${t//$'\n'/ }
    t=${t//$'\r'/ }
    t=${t//$'\t'/ }
    t=${t//'|'/}
    printf '%s' "$t"
}

_vader_fit() {
    local s="$1" max="$2"
    if [ "$max" -lt 1 ]; then
        return
    fi
    if [ "${#s}" -le "$max" ]; then
        printf '%s' "$s"
        return
    fi
    if [ "$max" -le 1 ]; then
        printf '…'
        return
    fi
    printf '%s…' "${s:0:$((max - 1))}"
}

_vader_nap() {
    local s="$1"
    if [ "${VADER_DEMO_FAST:-0}" = 1 ]; then
        s="0.001"
    fi
    sleep "$s"
}

_vader_clock() {
    local s="${1:-}" start now
    if [ -z "$s" ]; then
        start=${START_TIME:-$(date +%s)}
        now=$(date +%s)
        s=$((now - start))
        if [ "$s" -lt 0 ]; then
            s=0
        fi
    fi
    printf '%d:%02d' $((s / 60)) $((s % 60))
}

# Gradient over plain text. Resets at the end. One accent color when the
# terminal has no truecolor.
_vader_gradient() {
    local text="$1" r1="$2" g1="$3" b1="$4" r2="$5" g2="$6" b2="$7"
    local len=${#text} i ch denom r g b
    if [ "$_VADER_TRUE" != 1 ]; then
        printf '%b%b%s%b' "$VADER_BOLD" "$VADER_BRIGHT" "$text" "$VADER_RESET"
        return
    fi
    denom=$((len - 1))
    if [ "$denom" -lt 1 ]; then
        denom=1
    fi
    printf '\033[1m'
    for ((i=0; i<len; i++)); do
        ch=${text:i:1}
        r=$(( r1 + (r2 - r1) * i / denom ))
        g=$(( g1 + (g2 - g1) * i / denom ))
        b=$(( b1 + (b2 - b1) * i / denom ))
        printf '\033[38;2;%d;%d;%dm%s' "$r" "$g" "$b" "$ch"
    done
    printf '\033[0m'
}

_vader_rule_width() {
    local cols w
    cols=$(_vader_cols)
    w=$((cols - 4))
    if [ "$w" -gt 62 ]; then
        w=62
    fi
    if [ "$w" -lt 24 ]; then
        w=24
    fi
    printf '%s' "$w"
}

_vader_rule() {
    local width="$1" i r g b
    if [ "$width" -lt 1 ]; then
        return
    fi
    if [ "$_VADER_COLOR" != 1 ]; then
        printf '%*s\n' "$width" '' | tr ' ' '-'
        return
    fi
    if [ "$_VADER_TRUE" != 1 ]; then
        printf '%b' "$VADER_ACCENT"
        for ((i=0; i<width; i++)); do
            printf '━'
        done
        printf '%b\n' "$VADER_RESET"
        return
    fi
    for ((i=0; i<width; i++)); do
        r=$(( 28 + (90 - 28) * i / width ))
        g=$(( 18 + (70 - 18) * i / width ))
        b=$(( 120 + (255 - 120) * i / width ))
        printf '\033[38;2;%d;%d;%dm━' "$r" "$g" "$b"
    done
    printf '\033[0m\n'
}

# One plate row. mark=1 draws the filled cells as the scan highlight.
_vader_full_row() {
    local row="$1" mark="$2" i ch
    printf '\033[48;2;5;6;18m    '
    for ((i=0; i<${#row}; i++)); do
        ch=${row:i:1}
        if [ "$ch" = " " ]; then
            printf '  '
            continue
        fi
        if [ "$mark" = 1 ]; then
            printf '\033[38;2;220;228;255m██'
        elif [ "$ch" = "+" ]; then
            printf '\033[38;2;186;178;255m██'
        elif [ "$ch" = "." ]; then
            printf '\033[38;2;36;22;176m██'
        else
            printf '\033[38;2;55;40;255m██'
        fi
    done
    printf '    \033[0m'
}

_vader_half_row() {
    local top="$1" bot="$2" mark="$3" i tc bc tr tg tb br bg bb
    printf '\033[48;2;5;6;18m  '
    for ((i=0; i<${#top}; i++)); do
        tc=${top:i:1}
        bc=${bot:i:1}
        if [ "$mark" = 1 ] && [ "$tc" != " " ]; then
            tr=220; tg=228; tb=255
        else
            case "$tc" in
                '#') tr=55; tg=40; tb=255 ;;
                '+') tr=186; tg=178; tb=255 ;;
                '.') tr=36; tg=22; tb=176 ;;
                *) tr=5; tg=6; tb=18 ;;
            esac
        fi
        if [ "$mark" = 1 ] && [ "$bc" != " " ]; then
            br=220; bg=228; bb=255
        else
            case "$bc" in
                '#') br=55; bg=40; bb=255 ;;
                '+') br=186; bg=178; bb=255 ;;
                '.') br=36; bg=22; bb=176 ;;
                *) br=5; bg=6; bb=18 ;;
            esac
        fi
        printf '\033[38;2;%d;%d;%dm\033[48;2;%d;%d;%dm▀' "$tr" "$tg" "$tb" "$br" "$bg" "$bb"
    done
    printf '  \033[0m'
}

# Fills _VADER_FRAME. mode is "full" (one row per bitmap line) or "half".
_vader_build_plate() {
    local mode="$1" hi="$2" n r f mark bot
    _VADER_FRAME=()
    n=${#_VADER_MASCOT[@]}
    f=0
    if [ "$mode" = "half" ]; then
        for ((r=0; r<n; r+=2)); do
            mark=0
            if [ "$hi" = "$f" ]; then
                mark=1
            fi
            bot=${_VADER_MASCOT[$((r + 1))]}
            _VADER_FRAME+=("$(_vader_half_row "${_VADER_MASCOT[$r]}" "$bot" "$mark")")
            f=$((f + 1))
        done
        _VADER_PLATE_WIDTH=23
    else
        for ((r=0; r<n; r++)); do
            mark=0
            if [ "$hi" = "$r" ]; then
                mark=1
            fi
            _VADER_FRAME+=("$(_vader_full_row "${_VADER_MASCOT[$r]}" "$mark")")
        done
        _VADER_PLATE_WIDTH=46
    fi
}

_vader_print_frame() {
    local pad="$1" k n
    n=${#_VADER_FRAME[@]}
    for ((k=0; k<n; k++)); do
        printf '%s%s\n' "$pad" "${_VADER_FRAME[$k]}"
    done
}

# Draw the plate. On a terminal, rows drop in and a scanline crosses once.
_vader_play_plate() {
    local mode="$1" pad n k s lines scan=0
    _vader_build_plate "$mode" -1
    n=${#_VADER_FRAME[@]}
    pad=$(_vader_pad "$_VADER_PLATE_WIDTH")
    if [ "$_VADER_TTY" != 1 ]; then
        _vader_print_frame "$pad"
        return
    fi
    lines=$(_vader_lines)
    if [ "$lines" -ge $((n + 3)) ]; then
        scan=1
    fi
    printf '\033[?25l'
    for ((k=0; k<n; k++)); do
        printf '%s%s\n' "$pad" "${_VADER_FRAME[$k]}"
        _vader_nap 0.016
    done
    if [ "$scan" = 1 ]; then
        local step=$(( (n + 7) / 8 ))
        if [ "$step" -lt 1 ]; then
            step=1
        fi
        for ((s=0; s<n; s+=step)); do
            _vader_build_plate "$mode" "$s"
            printf '\033[%dA' "$n"
            _vader_print_frame "$pad"
            _vader_nap 0.02
        done
        _vader_build_plate "$mode" -1
        printf '\033[%dA' "$n"
        _vader_print_frame "$pad"
    fi
    printf '\033[?25h'
}

_vader_centered() {
    local plain="$1" colored="$2" width pad cols
    cols=$(_vader_cols)
    width=${#plain}
    pad=$(( (cols - width) / 2 ))
    if [ "$pad" -lt 1 ]; then
        pad=1
    fi
    if [ "$pad" -gt 8 ]; then
        pad=8
    fi
    printf '%*s%s\n' "$pad" '' "$colored"
}

# Place a label on the same centerline as the mascot plate.
_vader_under_plate() {
    local plain="$1" colored="$2" ppad inner
    if [ "${_VADER_PLATE_ON:-0}" = 1 ]; then
        ppad=$(_vader_pad "$_VADER_PLATE_WIDTH")
        inner=$(( (_VADER_PLATE_WIDTH - ${#plain}) / 2 ))
        if [ "$inner" -lt 0 ]; then
            inner=0
        fi
        printf '%s%*s%s\n' "$ppad" "$inner" '' "$colored"
    else
        _vader_centered "$plain" "$colored"
    fi
}

vader_boot_banner() {
    local mode="text" cols lines ver tag plain colored rulepad
    echo ""
    if [ "$_VADER_COLOR" != 1 ]; then
        ver=$(_vader_version)
        echo "  Guaardvark"
        echo "  local AI studio  ·  v${ver}"
        echo ""
        return
    fi
    cols=$(_vader_cols)
    lines=$(_vader_lines)
    # Full plate is 22 rows. It only plays when the phase board still fits under it.
    if [ "$cols" -ge 52 ] && [ "$lines" -ge 44 ]; then
        mode="full"
    elif [ "$cols" -ge 36 ] && [ "$lines" -ge 28 ]; then
        mode="half"
    fi
    _VADER_PLATE_ON=0
    if [ "$mode" != "text" ]; then
        _vader_play_plate "$mode"
        _VADER_PLATE_ON=1
        echo ""
    fi
    plain="GUAARDVARK"
    colored=$(_vader_gradient "$plain" 48 32 210 196 188 255)
    _vader_under_plate "$plain" "$colored"
    ver=$(_vader_version)
    tag="local AI studio   ·   v${ver}"
    colored=$(printf '%b%s%b' "$VADER_DIM" "$tag" "$VADER_RESET")
    _vader_under_plate "$tag" "$colored"
    echo ""
    if [ "$_VADER_PLATE_ON" = 1 ]; then
        rulepad=$(_vader_pad "$_VADER_PLATE_WIDTH")
        printf '%s' "$rulepad"
        _vader_rule "$_VADER_PLATE_WIDTH"
    else
        rulepad=$(_vader_pad "$(_vader_rule_width)")
        printf '%s' "$rulepad"
        _vader_rule "$(_vader_rule_width)"
    fi
    echo ""
    if [ "$_VADER_TTY" = 1 ]; then
        local root
        root=$(_vader_root)
        mkdir -p "$root/.start_cache" 2>/dev/null || true
        _VADER_PHASE_FILE="$root/.start_cache/boot.phase"
        printf '%s' "starting" > "$_VADER_PHASE_FILE" 2>/dev/null || true
        _vader_fx_start
    fi
}

_vader_fx_kill() {
    if [ -n "${_VADER_FX_PID:-}" ]; then
        kill "$_VADER_FX_PID" 2>/dev/null || true
        wait "$_VADER_FX_PID" 2>/dev/null || true
        _VADER_FX_PID=""
    fi
}

_vader_fx_start() {
    local phasefile began
    if [ "$_VADER_TTY" != 1 ] || [ -z "${_VADER_PHASE_FILE:-}" ]; then
        return 0
    fi
    if [ -n "${_VADER_FX_PID:-}" ]; then
        return 0
    fi
    phasefile="$_VADER_PHASE_FILE"
    began="${START_TIME:-$(date +%s)}"
    local out="$_VADER_OUT"
    (
        frames=(⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏)
        i=0
        while true; do
            phase=$(cat "$phasefile" 2>/dev/null || printf '%s' "starting")
            now=$(date +%s)
            el=$((now - began))
            fr=${frames[$((i % 10))]}
            printf '\033]0;%s  Guaardvark  ·  %s  ·  %ss\007' "$fr" "$phase" "$el" > "$out"
            i=$((i + 1))
            sleep 0.12
        done
    ) >/dev/null 2>/dev/null &
    _VADER_FX_PID=$!
}

# Stop motion and give the cursor back. Leaves the last frame on screen so a
# crash does not wipe the only status the person had.
_vader_fx_stop() {
    _vader_spin_bg_stop
    _vader_paint_stop
    _vader_fx_kill
    if [ "$_VADER_TTY" = 1 ]; then
        printf '\033[?25h\033]0;Guaardvark\007' > "$_VADER_OUT" 2>/dev/null || true
    fi
}

_vader_spin_bg_start() {
    local msg="$1" began
    if [ "${_VADER_BOARD:-0}" = 1 ]; then
        _VADER_ACTIVITY=$(_vader_clean "$msg")
        _VADER_LEVEL=info
        _vader_live_clear
        _vader_status_write
        return 0
    fi
    if [ "$_VADER_TTY" != 1 ]; then
        return 0
    fi
    _vader_spin_bg_stop
    began=$(date +%s)
    local out="$_VADER_OUT"
    printf '\033[?25l' > "$out" 2>/dev/null || true
    (
        frames=(⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏)
        i=0
        while true; do
            fr=${frames[$((i % 10))]}
            el=$(( $(date +%s) - began ))
            printf '\r  \033[38;2;186;178;255m%s\033[0m  \033[38;2;220;226;255m%s\033[38;2;148;156;198m  %ss\033[0m\033[K' \
                "$fr" "$msg" "$el" > "$out"
            i=$((i + 1))
            sleep 0.08
        done
    ) >/dev/null 2>/dev/null &
    _VADER_SPIN_PID=$!
}

_vader_spin_bg_stop() {
    if [ -z "${_VADER_SPIN_PID:-}" ]; then
        return 0
    fi
    kill "$_VADER_SPIN_PID" 2>/dev/null || true
    wait "$_VADER_SPIN_PID" 2>/dev/null || true
    _VADER_SPIN_PID=""
    if [ "$_VADER_TTY" = 1 ]; then
        printf '\r\033[K' > "$_VADER_OUT" 2>/dev/null || true
        if [ "${_VADER_BOARD:-0}" != 1 ]; then
            printf '\033[?25h' > "$_VADER_OUT" 2>/dev/null || true
        fi
    fi
}

_vader_sleep() {
    local secs="$1" msg="$2"
    if [ "$_VADER_TTY" != 1 ]; then
        sleep "$secs"
        return 0
    fi
    if [ "${_VADER_BOARD:-0}" = 1 ]; then
        _VADER_ACTIVITY=$(_vader_clean "$msg")
        _VADER_LEVEL=info
        _vader_live_clear
        _vader_status_write
        sleep "$secs"
        return 0
    fi
    _vader_spin_bg_start "$msg"
    sleep "$secs"
    _vader_spin_bg_stop
}

# --- board -----------------------------------------------------------------

_vader_set_mode_start() {
    if [ "${_VADER_MODE:-}" = "start" ] && [ -n "${_VADER_NAMES:-}" ]; then
        return 0
    fi
    _VADER_MODE=start
    _VADER_TOTAL=11
    _VADER_NAMES="previous servers|environment|redis|database|python|ollama|voice|backend|api|workers|studio"
}

_vader_set_mode_stop() {
    _VADER_MODE=stop
    _VADER_TOTAL=5
    _VADER_NAMES="comfyui|ollama|studio|workers|plugins"
    _VADER_SKIPS=""
}

_vader_label_n() {
    local n="$1" i=1 part rest
    rest="$_VADER_NAMES"
    while [ -n "$rest" ]; do
        part=${rest%%|*}
        if [ "$i" = "$n" ]; then
            printf '%s' "$part"
            return
        fi
        if [ "$rest" = "$part" ]; then
            break
        fi
        rest=${rest#*|}
        i=$((i + 1))
    done
    printf '%s' "$part"
}

_vader_live_clear() {
    if [ -n "${_VADER_DIR:-}" ]; then
        : > "$_VADER_DIR/boot.live" 2>/dev/null || true
    fi
}

_vader_live() {
    local level="$1" text dir
    text=$(_vader_clean "$2")
    dir="${_VADER_DIR:-${GUAARDVARK_BOOT_DIR:-}}"
    if [ -z "$dir" ]; then
        return 0
    fi
    printf '%s\t%s\n' "$level" "$text" > "$dir/boot.live" 2>/dev/null || true
}

_vader_status_write() {
    local root dir tmp
    root=$(_vader_root)
    dir="$root/.start_cache"
    mkdir -p "$dir" 2>/dev/null || true
    _VADER_DIR=$dir
    tmp="$dir/boot.status.$$"
    {
        printf 'mode=%s\n' "${_VADER_MODE:-start}"
        printf 'phase=%s\n' "${_VADER_PHASE:-1}"
        printf 'label=%s\n' "${_VADER_LABEL:-}"
        printf 'level=%s\n' "${_VADER_LEVEL:-info}"
        printf 'total=%s\n' "${_VADER_TOTAL:-11}"
        printf 'log=%s\n' "${_VADER_LOG:-}"
        printf 'activity=%s\n' "${_VADER_ACTIVITY:-}"
        printf 'names=%s\n' "${_VADER_NAMES:-}"
        printf 'skips=%s\n' "${_VADER_SKIPS:-}"
        printf 'started=%s\n' "${START_TIME:-$(date +%s)}"
    } > "$tmp" 2>/dev/null || return 0
    mv -f "$tmp" "$dir/boot.status" 2>/dev/null || true
    export GUAARDVARK_BOOT_DIR="$dir"
    export GUAARDVARK_BOOT_FRAME=1
    export GUAARDVARK_BOOT_STATUS="$dir/boot.status"
}

_vader_set_log() {
    _VADER_LOG="$1"
    if [ "${_VADER_BOARD:-0}" = 1 ]; then
        _vader_status_write
    fi
}

_vader_layout() {
    local cols="${1:-}" lines="${2:-}"
    if [ -z "$cols" ] || [ -z "$lines" ]; then
        _vader_read_winsize
        cols=$_VADER_COLS
        lines=$_VADER_ROWS
    fi
    if [ "$cols" -lt 60 ] || [ "$lines" -lt 28 ]; then
        printf 'strip'
    else
        printf 'rail'
    fi
}

# Held across the tty write so close can erase only between frames.
_vader_paint_lock() {
    local d="$1/boot.lock" i=0
    while [ "$i" -lt 300 ]; do
        if mkdir "$d" 2>/dev/null; then
            return 0
        fi
        i=$((i + 1))
        sleep 0.01
    done
    return 1
}

_vader_paint_unlock() {
    rmdir "$1/boot.lock" 2>/dev/null || true
}

_vader_arm_exit() {
    local existing
    existing=$(trap -p EXIT 2>/dev/null || true)
    if [ -z "$existing" ]; then
        trap '_vader_fx_stop' EXIT
    fi
}

_vader_board_open() {
    if [ "${_VADER_BOARD:-0}" = 1 ]; then
        return 0
    fi
    if [ "$_VADER_TTY" != 1 ]; then
        return 0
    fi
    local root
    root=$(_vader_root)
    _VADER_DIR="$root/.start_cache"
    mkdir -p "$_VADER_DIR" 2>/dev/null || true
    _VADER_BOARD=1
    _vader_fx_kill
    _vader_arm_exit
    export GUAARDVARK_BOOT_DIR="$_VADER_DIR"
    export GUAARDVARK_BOOT_FRAME=1
    printf '0\n' > "$_VADER_DIR/boot.hold" 2>/dev/null || true
    rm -f "$_VADER_DIR/boot.reset" "$_VADER_DIR/boot.closed" 2>/dev/null || true
    rmdir "$_VADER_DIR/boot.lock" 2>/dev/null || true
    _vader_status_write
    printf '\033[?25l' > "$_VADER_OUT" 2>/dev/null || true
    _vader_paint_start
}

_vader_paint_stop() {
    if [ -n "${_VADER_PAINT_PID:-}" ]; then
        kill "$_VADER_PAINT_PID" 2>/dev/null || true
        wait "$_VADER_PAINT_PID" 2>/dev/null || true
        _VADER_PAINT_PID=""
    fi
    if [ -n "${_VADER_DIR:-}" ]; then
        rmdir "$_VADER_DIR/boot.lock" 2>/dev/null || true
    fi
}

_vader_board_erase() {
    local n dir
    dir="${_VADER_DIR:-}"
    if [ -z "$dir" ]; then
        return 0
    fi
    n=$(cat "$dir/boot.rows" 2>/dev/null || echo 0)
    case "$n" in
        ''|*[!0-9]*) n=0 ;;
    esac
    if [ "$n" -gt 0 ] && [ "$_VADER_TTY" = 1 ]; then
        printf '\033[%dA\033[J' "$n" > "$_VADER_OUT" 2>/dev/null || true
    fi
    printf '0\n' > "$dir/boot.rows" 2>/dev/null || true
}

_vader_board_close() {
    if [ "${_VADER_BOARD:-0}" != 1 ]; then
        return 0
    fi
    local dir
    dir="${_VADER_DIR:-}"
    # Stop between frames. Erasing while a paint is in flight reprints the
    # board over the card that replaced it.
    if [ -n "$dir" ]; then
        : > "$dir/boot.closed"
        _vader_paint_lock "$dir" || true
    fi
    _vader_paint_stop
    _vader_board_erase
    if [ -n "$dir" ]; then
        _vader_paint_unlock "$dir"
    fi
    _VADER_BOARD=0
    export GUAARDVARK_BOOT_FRAME=0
    if [ "$_VADER_TTY" = 1 ]; then
        printf '\033[?25h' > "$_VADER_OUT" 2>/dev/null || true
    fi
}

# Stop the painter and leave the checklist where it is, with every row finished.
# The ready card and the stopped card print underneath that frame.
_vader_board_finish() {
    local dir prev
    if [ "${_VADER_BOARD:-0}" != 1 ]; then
        return 0
    fi
    dir="${_VADER_DIR:-}"
    if [ -n "$dir" ]; then
        : > "$dir/boot.closed"
        _vader_paint_lock "$dir" || true
    fi
    _vader_paint_stop
    if [ "${_VADER_TOTAL:-0}" -ge 1 ]; then
        _VADER_PHASE=$((_VADER_TOTAL + 1))
        _vader_status_write
    fi
    prev=0
    if [ -n "$dir" ]; then
        prev=$(cat "$dir/boot.rows" 2>/dev/null || echo 0)
        case "$prev" in ''|*[!0-9]*) prev=0 ;; esac
    fi
    if [ "$prev" -gt 0 ] && [ "$_VADER_TTY" = 1 ]; then
        _vader_paint_once "$prev" 0 "$dir" "$_VADER_OUT" >/dev/null || true
    fi
    if [ -n "$dir" ]; then
        _vader_paint_unlock "$dir"
    fi
    _VADER_BOARD=0
    export GUAARDVARK_BOOT_FRAME=0
    if [ "$_VADER_TTY" = 1 ]; then
        printf '\033[?25h' > "$_VADER_OUT" 2>/dev/null || true
    fi
}

# Read boot.status into _VS_* . Not sourced: activity text is not trusted as shell.
_vader_read_status() {
    local file="$1" line key val
    _VS_MODE=start
    _VS_PHASE=1
    _VS_LABEL=""
    _VS_LEVEL=info
    _VS_TOTAL=11
    _VS_LOG=""
    _VS_ACTIVITY=""
    _VS_NAMES=""
    _VS_SKIPS=""
    _VS_STARTED=${START_TIME:-$(date +%s)}
    [ -f "$file" ] || return 0
    while IFS= read -r line || [ -n "$line" ]; do
        key=${line%%=*}
        val=${line#*=}
        case "$key" in
            mode) _VS_MODE=$val ;;
            phase) _VS_PHASE=$val ;;
            label) _VS_LABEL=$val ;;
            level) _VS_LEVEL=$val ;;
            total) _VS_TOTAL=$val ;;
            log) _VS_LOG=$val ;;
            activity) _VS_ACTIVITY=$val ;;
            names) _VS_NAMES=$val ;;
            skips) _VS_SKIPS=$val ;;
            started) _VS_STARTED=$val ;;
        esac
    done < "$file"
}

_vader_split_names() {
    local rest="$1" part
    _VS_NAME_LIST=()
    rest=${rest#|}
    while [ -n "$rest" ]; do
        part=${rest%%|*}
        _VS_NAME_LIST+=("$part")
        if [ "$rest" = "$part" ]; then
            break
        fi
        rest=${rest#*|}
    done
}

_vader_is_skip() {
    local n="$1"
    case ",${_VS_SKIPS:-}," in
        *,"$n",*) return 0 ;;
    esac
    return 1
}

# One paint. Previous row count is $1. The board goes to $4 (/dev/tty).
# Stdout is only the new row count: the painter reads it through a command
# substitution, and any other byte there desyncs cursor-up.
_vader_paint_once() {
    local prev="$1" tick="$2" dir="$3" out="$4"
    local cols layout clock ver right left gap i name mark mcol ncol
    local activity level phase total logline width filled shim j ch
    local line extra fr holdv
    local -a names
    dir="${3}"
    if [ -f "$dir/boot.hold" ]; then
        holdv=$(cat "$dir/boot.hold" 2>/dev/null || echo 0)
        if [ "$holdv" = 1 ]; then
            printf '%s' "$prev"
            return 0
        fi
    fi
    if [ -f "$dir/boot.reset" ]; then
        prev=0
        rm -f "$dir/boot.reset" 2>/dev/null || true
    fi
    _vader_read_status "$dir/boot.status"
    _vader_read_winsize
    cols=$_VADER_COLS
    if [ -f "$dir/boot.cols" ]; then
        local lastc
        lastc=$(cat "$dir/boot.cols" 2>/dev/null || echo 0)
        if [ "$lastc" != "$cols" ]; then
            prev=0
        fi
    fi
    printf '%s\n' "$cols" > "$dir/boot.cols" 2>/dev/null || true
    layout=$(_vader_layout "$cols" "$_VADER_ROWS")
    case "${_VS_PHASE:-1}" in ''|*[!0-9]*) _VS_PHASE=1 ;; esac
    case "${_VS_TOTAL:-11}" in ''|*[!0-9]*) _VS_TOTAL=11 ;; esac
    case "${_VS_STARTED:-0}" in ''|*[!0-9]*) _VS_STARTED=$(date +%s) ;; esac
    phase=$_VS_PHASE
    total=$_VS_TOTAL
    if [ "$total" -lt 1 ]; then
        total=1
    fi
    clock=$(_vader_clock "$(( $(date +%s) - _VS_STARTED ))")
    ver=$(_vader_version)
    activity=$(_vader_fit "${_VS_ACTIVITY:-}" $((cols - 6)))
    level=${_VS_LEVEL:-info}
    if [ -s "$dir/boot.live" ]; then
        local live
        live=$(head -n 1 "$dir/boot.live" 2>/dev/null || true)
        if [ -n "$live" ]; then
            level=${live%%$'\t'*}
            activity=$(_vader_fit "${live#*$'\t'}" $((cols - 6)))
        fi
    fi
    _vader_split_names "${_VS_NAMES:-}"
    names=("${_VS_NAME_LIST[@]}")
    if [ "${#names[@]}" -eq 0 ]; then
        names=("$_VS_LABEL")
    fi
    fr=${_VADER_FRAMES[$((tick % 10))]}
    logline=${_VS_LOG:-}
    if [ -z "$logline" ]; then
        logline="logs/setup.log"
    fi
    # Show the path relative to the install when it lives under it.
    local root
    root=$(_vader_root)
    case "$logline" in
        "$root"/*) logline=${logline#"$root"/} ;;
    esac

    local -a board=()
    left="GUAARDVARK"
    if [ "${_VS_MODE:-start}" = "stop" ]; then
        right="stopping · ${clock}"
    else
        right="v${ver} · ${clock}"
    fi
    gap=$((cols - 4 - ${#left} - ${#right}))
    if [ "$gap" -lt 2 ]; then
        right=$clock
        gap=$((cols - 4 - ${#left} - ${#right}))
    fi
    if [ "$gap" -lt 1 ]; then
        gap=1
    fi

    if [ "$layout" = "rail" ]; then
        line=$(printf '  %s%*s%s' \
            "$(_vader_gradient "$left" 48 32 210 196 188 255)" \
            "$gap" "" \
            "$(printf '%b%s%b' "$VADER_DIM" "$right" "$VADER_RESET")")
        board+=("$line")
        board+=("")
        i=1
        for name in "${names[@]}"; do
            if _vader_is_skip "$i"; then
                mark="–"
                mcol=$VADER_MUTED
                ncol=$VADER_MUTED
            elif [ "$i" -lt "$phase" ]; then
                mark="✔"
                mcol=$VADER_OK
                ncol=$VADER_DIM
            elif [ "$i" -eq "$phase" ]; then
                mark=$fr
                mcol=$VADER_BRIGHT
                ncol=$VADER_INK
            else
                mark="·"
                mcol=$VADER_MUTED
                ncol=$VADER_MUTED
            fi
            line=$(printf '  %b%s%b  %b%s%b' "$mcol" "$mark" "$VADER_RESET" "$ncol" "$name" "$VADER_RESET")
            if [ "$i" -eq "$phase" ]; then
                line=$(printf '  %b%b%s%b  %b%b%s%b' "$VADER_BOLD" "$mcol" "$mark" "$VADER_RESET" "$VADER_BOLD" "$ncol" "$name" "$VADER_RESET")
            fi
            board+=("$line")
            i=$((i + 1))
        done
        board+=("")
    else
        name=${_VS_LABEL:-${names[$((phase - 1))]:-}}
        line=$(printf '  %b%b%s%b  %b%b%s%b' "$VADER_BOLD" "$VADER_BRIGHT" "$fr" "$VADER_RESET" "$VADER_BOLD" "$VADER_INK" "$name" "$VADER_RESET")
        board+=("$line")
    fi

    case "$level" in
        warn)
            line=$(printf '  %b⚠%b  %b%s%b' "$VADER_WARN" "$VADER_RESET" "$VADER_DIM" "$activity" "$VADER_RESET")
            ;;
        bad)
            line=$(printf '  %b✖%b  %b%s%b' "$VADER_BAD" "$VADER_RESET" "$VADER_BAD" "$activity" "$VADER_RESET")
            ;;
        ok)
            line=$(printf '  %b✔%b  %b%s%b' "$VADER_OK" "$VADER_RESET" "$VADER_INK" "$activity" "$VADER_RESET")
            ;;
        *)
            line=$(printf '  %b%s%b' "$VADER_DIM" "$activity" "$VADER_RESET")
            ;;
    esac
    board+=("$line")

    local labelplain
    labelplain=${_VS_LABEL:-}
    width=$((cols - 4 - ${#labelplain} - 2))
    if [ "$width" -gt 48 ]; then
        width=48
    fi
    if [ "$width" -lt 12 ]; then
        width=12
    fi
    filled=$(( (phase - 1) * width / total ))
    if [ "$filled" -lt 0 ]; then
        filled=0
    fi
    if [ "$filled" -gt "$width" ]; then
        filled=$width
    fi
    shim=$(( tick % width ))
    # One escape per run, not per cell. The shimmer cell is the bright one.
    local acc ink mut rst
    acc=$(printf '%b' "$VADER_ACCENT")
    ink=$(printf '%b' "$VADER_INK")
    mut=$(printf '%b' "$VADER_MUTED")
    rst=$(printf '%b' "$VADER_RESET")
    line="  "
    for ((j=0; j<width; j++)); do
        if [ "$j" -eq "$shim" ]; then
            line="${line}${ink}━"
        elif [ "$j" -lt "$filled" ]; then
            line="${line}${acc}━"
        else
            line="${line}${mut}╌"
        fi
    done
    line="${line}${rst}  ${mut}${labelplain}${rst}"
    board+=("$line")
    if [ "$layout" = "rail" ]; then
        board+=("")
    fi
    line=$(printf '  %b%s%b' "$VADER_MUTED" "$(_vader_fit "$logline" $((cols - 4)))" "$VADER_RESET")
    board+=("$line")

    local count=${#board[@]}
    {
        printf '\033]0;%s  Guaardvark  ·  %s  ·  %s\007' "$fr" "${_VS_LABEL:-starting}" "$clock"
        if [ "$prev" -gt 0 ]; then
            printf '\033[%dA' "$prev"
        fi
        for line in "${board[@]}"; do
            # Colours are already real ESC bytes. %s keeps backslashes in tool output.
            printf '%s\033[K\n' "$line"
        done
        if [ "$prev" -gt "$count" ]; then
            extra=$((prev - count))
            for ((j=0; j<extra; j++)); do
                printf '\033[K\n'
            done
            printf '\033[%dA' "$extra"
        fi
        printf '\033[?25l'
    } > "$out" 2>/dev/null || true
    printf '%s\n' "$count" > "$dir/boot.rows" 2>/dev/null || true
    printf '%s' "$count"
}

_vader_paint_start() {
    if [ -n "${_VADER_PAINT_PID:-}" ]; then
        return 0
    fi
    local out="$_VADER_OUT" dir="$_VADER_DIR"
    # stderr stays off the terminal: a broken-pipe note from a dying frame
    # would scroll the board by one line and every later cursor-up would drift.
    (
        prev=0
        tick=0
        while true; do
            if [ -f "$dir/boot.closed" ]; then
                exit 0
            fi
            if ! _vader_paint_lock "$dir"; then
                sleep 0.05
                continue
            fi
            if [ -f "$dir/boot.closed" ]; then
                _vader_paint_unlock "$dir"
                exit 0
            fi
            prev=$(_vader_paint_once "$prev" "$tick" "$dir" "$out")
            case "$prev" in
                ''|*[!0-9]*) prev=0 ;;
            esac
            _vader_paint_unlock "$dir"
            tick=$((tick + 1))
            sleep 0.08
        done
    ) 2>"$dir/boot.painter.err" &
    _VADER_PAINT_PID=$!
    # The painter is a fork of this script, so its cmdline is start.sh too.
    # stop.sh reaps every start.sh except the pids listed here. Without this
    # it kills the painter at step 1 and the rest of the boot is invisible.
    START_LOCK_PROTECT_PIDS="${START_LOCK_PROTECT_PIDS:-} ${_VADER_PAINT_PID}"
    export START_LOCK_PROTECT_PIDS
    printf '%s\n' "$_VADER_PAINT_PID" > "$dir/boot.painter.pid" 2>/dev/null || true
}

# --- public printers -------------------------------------------------------

vader_header() {
    _vader_spin_bg_stop
    local t
    t=$(_vader_ltrim "${1:-}")
    if [ "${_VADER_CHILD:-0}" = 1 ]; then
        [ -n "$t" ] && _vader_live info "$t"
        return 0
    fi
    if [ "${_VADER_BOARD:-0}" = 1 ]; then
        if [ -n "$t" ]; then
            _VADER_ACTIVITY=$(_vader_clean "$t")
            _VADER_LEVEL=info
            _vader_live_clear
            _vader_status_write
        fi
        return 0
    fi
    echo ""
    if [ "$_VADER_COLOR" != 1 ]; then
        echo "-----------------------------------------------------------------"
        [ -n "$t" ] && echo "  $t"
        echo "-----------------------------------------------------------------"
        return 0
    fi
    _vader_rule "$(_vader_rule_width)"
    if [ -n "$t" ]; then
        echo -e "  ${VADER_INK}${VADER_BOLD}${t}${VADER_RESET}"
    fi
    _vader_rule "$(_vader_rule_width)"
}

vader_separator() {
    _vader_spin_bg_stop
    if [ "${_VADER_CHILD:-0}" = 1 ] || [ "${_VADER_BOARD:-0}" = 1 ]; then
        return 0
    fi
    if [ "$_VADER_COLOR" != 1 ]; then
        echo "  -----------------------------------------------------------------"
        return 0
    fi
    printf '  %b─────────────────────────────────────────────────────────────────%b\n' "$VADER_MUTED" "$VADER_RESET"
}

vader_title() {
    _vader_spin_bg_stop
    local t
    t=$(_vader_ltrim "${1:-}")
    if [ "${_VADER_CHILD:-0}" = 1 ]; then
        [ -n "$t" ] && _vader_live info "$t"
        return 0
    fi
    if [ "${_VADER_BOARD:-0}" = 1 ]; then
        _VADER_ACTIVITY=$(_vader_clean "$t")
        _VADER_LEVEL=info
        _vader_live_clear
        _vader_status_write
        return 0
    fi
    if [ "$_VADER_COLOR" != 1 ]; then
        echo "  $t"
        return 0
    fi
    echo -e "  ${VADER_BRIGHT}${VADER_BOLD}${t}${VADER_RESET}"
}

vader_section() {
    vader_title "$1"
}

vader_info() {
    _vader_spin_bg_stop
    if [ "${_VADER_CHILD:-0}" = 1 ]; then
        _vader_live info "$1"
        return 0
    fi
    if [ "${_VADER_BOARD:-0}" = 1 ]; then
        _VADER_ACTIVITY=$(_vader_clean "$1")
        _VADER_LEVEL=info
        _vader_live_clear
        _vader_status_write
        return 0
    fi
    if [ "$_VADER_COLOR" != 1 ]; then
        echo "  · $1"
        return 0
    fi
    echo -e "  ${VADER_DIM}·${VADER_RESET} ${VADER_DIM}$1${VADER_RESET}"
}

vader_detail() {
    vader_info "$1"
}

vader_success() {
    _vader_spin_bg_stop
    if [ "${_VADER_CHILD:-0}" = 1 ]; then
        _vader_live ok "$1"
        return 0
    fi
    if [ "${_VADER_BOARD:-0}" = 1 ]; then
        _VADER_ACTIVITY=$(_vader_clean "$1")
        _VADER_LEVEL=ok
        _vader_live_clear
        _vader_status_write
        return 0
    fi
    if [ "$_VADER_COLOR" != 1 ]; then
        echo "  ✔ $1"
        return 0
    fi
    echo -e "  ${VADER_OK}✔${VADER_RESET} ${VADER_INK}$1${VADER_RESET}"
}

vader_warn() {
    _vader_spin_bg_stop
    if [ "${_VADER_CHILD:-0}" = 1 ]; then
        _vader_live warn "$1"
        return 0
    fi
    if [ "${_VADER_BOARD:-0}" = 1 ]; then
        _VADER_ACTIVITY=$(_vader_clean "$1")
        _VADER_LEVEL=warn
        _vader_live_clear
        _vader_status_write
        return 0
    fi
    if [ "$_VADER_COLOR" != 1 ]; then
        echo "  ⚠ $1"
        return 0
    fi
    echo -e "  ${VADER_WARN}⚠${VADER_RESET} ${VADER_DIM}$1${VADER_RESET}"
}

# Quiet completion: the phase stays on the rail as a dim dash (left running,
# not installed) instead of a mint check.
vader_quiet() {
    _vader_spin_bg_stop
    local msg
    msg=$(_vader_clean "$1")
    if [ "${_VADER_CHILD:-0}" = 1 ]; then
        _vader_live info "$msg"
        return 0
    fi
    if [ "${_VADER_BOARD:-0}" = 1 ]; then
        _VADER_SKIPS="${_VADER_SKIPS:+${_VADER_SKIPS},}${_VADER_PHASE}"
        _VADER_ACTIVITY=$msg
        _VADER_LEVEL=info
        _vader_live_clear
        _vader_status_write
        return 0
    fi
    if [ "$_VADER_COLOR" != 1 ]; then
        echo "  – $msg"
        return 0
    fi
    echo -e "  ${VADER_MUTED}–${VADER_RESET} ${VADER_DIM}$msg${VADER_RESET}"
}

vader_error() {
    _vader_spin_bg_stop
    if [ "${_VADER_CHILD:-0}" = 1 ]; then
        _vader_live bad "$1"
        return 0
    fi
    if [ "${_VADER_BOARD:-0}" = 1 ]; then
        local log
        log=${_VADER_LOG:-}
        _vader_board_close
        if [ "$_VADER_COLOR" != 1 ]; then
            echo "  ✖ $1"
        else
            echo -e "  ${VADER_BAD}✖${VADER_RESET} ${VADER_BAD}$1${VADER_RESET}"
        fi
        if [ -n "$log" ] && [ -f "$log" ] && [ "${_VADER_TAIL_SHOWN:-0}" != 1 ]; then
            _VADER_TAIL_SHOWN=1
            if [ "$_VADER_COLOR" = 1 ]; then
                echo -e "  ${VADER_DIM}${log}${VADER_RESET}"
            else
                echo "  $log"
            fi
            tail -n 12 "$log" | sed 's/^/  /'
        fi
        return 0
    fi
    if [ "$_VADER_COLOR" != 1 ]; then
        echo "  ✖ $1"
        return 0
    fi
    echo -e "  ${VADER_BAD}✖${VADER_RESET} ${VADER_BAD}$1${VADER_RESET}"
}

vader_step() {
    _vader_spin_bg_stop
    local n="$1" msg="$2" label phase
    if [ "${_VADER_CHILD:-0}" = 1 ]; then
        _vader_live info "$msg"
        return 0
    fi
    if [ "${_VADER_MODE:-}" != "stop" ]; then
        _vader_set_mode_start
    fi
    label=$(_vader_label_n "$n")
    phase=$(_vader_clean "$msg")
    phase=${phase%.}
    phase=${phase%.}
    phase=${phase%.}
    _VADER_PHASE=$n
    _VADER_LABEL=$label
    _VADER_ACTIVITY=$phase
    _VADER_LEVEL=info
    if [ -n "${_VADER_PHASE_FILE:-}" ]; then
        printf '%s' "$label" > "$_VADER_PHASE_FILE" 2>/dev/null || true
    fi
    if [ "$_VADER_TTY" = 1 ]; then
        _vader_board_open
        _vader_live_clear
        _vader_status_write
        return 0
    fi
    printf '  %s — %s\n' "$label" "$phase"
}

# Ask for a sudo timestamp while the painter is paused, so the prompt is not
# painted over. No-op when a timestamp is already valid.
_vader_sudo_v() {
    command -v sudo >/dev/null 2>&1 || return 0
    sudo -n true 2>/dev/null && return 0
    if [ "${_VADER_BOARD:-0}" = 1 ] && [ -n "${_VADER_DIR:-}" ]; then
        printf '1\n' > "$_VADER_DIR/boot.hold"
        sleep 0.2
        : > "$_VADER_DIR/boot.reset"
    fi
    sudo -v
    local rc=$?
    if [ -n "${_VADER_DIR:-}" ]; then
        printf '0\n' > "$_VADER_DIR/boot.hold"
    fi
    return $rc
}

# Run a command. On the board, stdout and stderr become the activity line and
# a full copy is appended to the log. Off the board, a spinner plus the log.
_vader_consume() {
    local log="$1" live="$2" hold="$3" line clean
    while IFS= read -r line || [ -n "$line" ]; do
        [ -z "$line" ] && continue
        printf '%s\n' "$line" >> "$log"
        clean=$(printf '%s' "$line" | sed $'s/\033\\[[0-9;?]*[A-Za-z]//g' | tr -d '\000-\010\013\014\016-\037')
        [ -z "$clean" ] && continue
        if printf '%s' "$clean" | grep -q '\[sudo\]\|[Pp]assword for\|Password:'; then
            printf '1\n' > "$hold"
            printf '%s\n' "$clean" > /dev/tty 2>/dev/null || true
        else
            printf '0\n' > "$hold"
            clean=$(_vader_fit "$clean" 180)
            printf 'info\t%s\n' "$clean" > "$live"
        fi
    done
}

_vader_run() {
    local log="$1"
    shift
    if [ "${1:-}" = "--" ]; then
        shift
    fi
    if [ "$#" -eq 0 ]; then
        return 0
    fi
    mkdir -p "$(dirname "$log")" 2>/dev/null || true
    _vader_set_log "$log"
    if [ "${_VADER_BOARD:-0}" != 1 ] || [ -z "${_VADER_DIR:-}" ]; then
        _vader_spin_bg_start "${_VADER_ACTIVITY:-working}"
        "$@" >> "$log" 2>&1
        local rc=$?
        _vader_spin_bg_stop
        return $rc
    fi
    local live hold rc
    live="$_VADER_DIR/boot.live"
    hold="$_VADER_DIR/boot.hold"
    if command -v stdbuf >/dev/null 2>&1 && [ "$(type -t "$1" 2>/dev/null || true)" != "function" ]; then
        stdbuf -oL -eL "$@" 2>&1 | tr '\r' '\n' | _vader_consume "$log" "$live" "$hold"
        rc=${PIPESTATUS[0]}
    else
        "$@" 2>&1 | tr '\r' '\n' | _vader_consume "$log" "$live" "$hold"
        rc=${PIPESTATUS[0]}
    fi
    return "$rc"
}

_vader_kv() {
    local key="$1" value="$2" vcol="$3"
    if [ "$_VADER_COLOR" != 1 ]; then
        printf '  %-8s  %s\n' "$key" "$value"
        return 0
    fi
    printf '  %b%-8s%b  %b%s%b\n' "$VADER_DIM" "$key" "$VADER_RESET" "$vcol" "$value" "$VADER_RESET"
}

# Close the board and leave the ready card in the normal buffer.
# vader_ready_card SECONDS STUDIO_URL API_URL LAN_URL LOGFILE
vader_ready_card() {
    local dur="$1" studio="$2" api="$3" lan="$4" logfile="$5" clock
    clock=$(_vader_clock "$dur")
    _vader_board_finish
    _vader_fx_stop
    echo ""
    if [ "$_VADER_COLOR" != 1 ]; then
        echo "  ready    studio is up    ·    ${clock}"
        echo ""
        [ -n "$studio" ] && printf '  %-8s  %s\n' "studio" "$studio"
        [ -n "$api" ] && printf '  %-8s  %s\n' "api" "$api"
        if [ -n "$lan" ]; then
            echo ""
            echo "  on this network"
            printf '  %-8s  %s\n' "studio" "$lan"
        fi
        echo ""
        echo "  ./stop.sh  ·  ${logfile}"
        echo ""
        return 0
    fi
    printf '  %s' "$(_vader_gradient "ready" 186 178 255 80 220 176)"
    printf '   %bstudio is up%b' "$VADER_INK" "$VADER_RESET"
    printf '   %b·%b   %b%s%b\n' "$VADER_DIM" "$VADER_RESET" "$VADER_BRIGHT" "$clock" "$VADER_RESET"
    echo ""
    _vader_rule "$(_vader_rule_width)"
    echo ""
    [ -n "$studio" ] && _vader_kv "studio" "$studio" "$VADER_BRIGHT"
    [ -n "$api" ] && _vader_kv "api" "$api" "$VADER_DIM"
    if [ -n "$lan" ]; then
        echo ""
        printf '  %bon this network%b\n' "$VADER_DIM" "$VADER_RESET"
        _vader_kv "studio" "$lan" "$VADER_BRIGHT"
    fi
    echo ""
    printf '  %b./stop.sh%b  %b·%b  %b%s%b\n' \
        "$VADER_INK" "$VADER_RESET" "$VADER_DIM" "$VADER_RESET" "$VADER_DIM" "$logfile" "$VADER_RESET"
    echo ""
}

vader_boot_done() {
    vader_ready_card "$1" "" "" "" ""
}

vader_stop_begin() {
    if [ "${_VADER_CHILD:-0}" = 1 ]; then
        _vader_live info "stopping"
        return 0
    fi
    _vader_set_mode_stop
    _VADER_PHASE=1
    _VADER_LABEL="comfyui"
    _VADER_ACTIVITY="stopping"
    _VADER_LEVEL=info
    if [ -z "${START_TIME:-}" ]; then
        START_TIME=$(date +%s)
        export START_TIME
    fi
    if [ "$_VADER_TTY" != 1 ]; then
        echo ""
        echo "  Guaardvark"
        echo "  stopping"
        return 0
    fi
    _vader_board_open
    _vader_status_write
}

vader_stop_done() {
    if [ "${_VADER_CHILD:-0}" = 1 ]; then
        _vader_live ok "stopped"
        return 0
    fi
    local start now dur clock
    start=${START_TIME:-$(date +%s)}
    now=$(date +%s)
    dur=$((now - start))
    clock=$(_vader_clock "$dur")
    _vader_board_finish
    _vader_fx_stop
    echo ""
    if [ "$_VADER_COLOR" != 1 ]; then
        echo "  stopped    services are down    ·    ${clock}"
        echo ""
        return 0
    fi
    printf '  %s' "$(_vader_gradient "stopped" 186 178 255 80 220 176)"
    printf '   %bservices are down%b' "$VADER_INK" "$VADER_RESET"
    printf '   %b·%b   %b%s%b\n' "$VADER_DIM" "$VADER_RESET" "$VADER_BRIGHT" "$clock" "$VADER_RESET"
    echo ""
    _vader_rule "$(_vader_rule_width)"
    echo ""
}

# --- demo / source ---------------------------------------------------------

_vader_demo() {
    local root
    root=$(mktemp -d "${TMPDIR:-/tmp}/vader-demo.XXXXXX")
    SCRIPT_DIR=$root
    export SCRIPT_DIR
    mkdir -p "$root/.start_cache" "$root/logs"
    printf '%s\n' "2.9.1" > "$root/VERSION"
    START_TIME=$(date +%s)
    export START_TIME
    trap '_vader_fx_stop; rm -rf "$SCRIPT_DIR"' EXIT
    vader_boot_banner
    local i msgs
    msgs=(
        "Stopping previous servers"
        "Checking environment"
        "Redis"
        "Database"
        "Python environment"
        "Ollama"
        "Voice"
        "Backend"
        "API server"
        "Workers"
        "Studio"
    )
    for i in 1 2 3 4 5 6 7 8 9 10 11; do
        vader_step "$i" "${msgs[$((i - 1))]}"
        if [ "$i" = 3 ]; then
            _vader_run "$root/logs/setup.log" -- bash -c 'printf "ping\rping ok\n"; sleep 0.05'
            vader_success "broker answered"
        fi
        if [ "$i" = 5 ]; then
            vader_warn "pip scratch has 4GB free"
        fi
        if [ "${VADER_DEMO_FAST:-0}" = 1 ]; then
            sleep 0.16
        else
            sleep 0.28
        fi
    done
    vader_ready_card 38 "http://localhost:5173" "http://localhost:5000" "http://192.168.1.20:5173" "logs/backend_startup.log"
    START_TIME=$(date +%s)
    vader_stop_begin
    vader_step 1 "ComfyUI"
    vader_quiet "not running"
    sleep 0.12
    vader_step 2 "Ollama"
    vader_quiet "left running"
    sleep 0.12
    vader_step 3 "Studio"
    vader_success "backend stopped"
    sleep 0.12
    vader_step 4 "Workers"
    vader_success "workers stopped"
    sleep 0.12
    vader_step 5 "Plugins"
    vader_success "plugins stopped"
    sleep 0.2
    vader_stop_done
    trap - EXIT
    _vader_fx_stop
    rm -rf "$root"
}

if [ "${BASH_SOURCE[0]}" != "$0" ]; then
    # Child of a board: write boot.live only. Do not paint and do not erase
    # the parent's rows (stdout here is often the log pipe, not the terminal).
    if [ "${GUAARDVARK_BOOT_FRAME:-0}" = 1 ] && [ -n "${GUAARDVARK_BOOT_DIR:-}" ]; then
        _VADER_CHILD=1
        _VADER_DIR=$GUAARDVARK_BOOT_DIR
    fi
    return 0
fi

case "${1:-}" in
    --demo) _vader_demo ;;
    *)
        echo "usage: scripts/lib/terminal_ui.sh --demo" >&2
        exit 2
        ;;
esac
