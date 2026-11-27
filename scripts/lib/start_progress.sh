#!/bin/bash
# scripts/lib/start_progress.sh — machine-readable startup progress for start.sh.
#
# Opt-in: ./start.sh --progress-file <path> (or GUAARDVARK_PROGRESS_FILE=<path>).
# start.sh then appends one JSON object per line to that file as each startup
# stage begins and ends, and exactly one terminal line, "ready" or "failed".
# The desktop app reads the file to draw its loading screen. The format and the
# step ids are documented in desktop/PROTOCOL.md; keep the two in step.
#
# Every function returns at once until progress_init is given a path, so a
# normal ./start.sh writes and prints nothing extra. Bash 3.2: no associative
# arrays, no ${var,,}.

_PROGRESS_FILE=""
_PROGRESS_STEP=""        # step that is open (started, not yet ended)
_PROGRESS_LABEL=""
_PROGRESS_TERMINAL=0     # 1 once "ready" or "failed" has been written
_PROGRESS_LOG=""         # log a "failed" line points the reader at
_PROGRESS_ERRORS=""      # vader_error texts since the open step began

progress_on() {
    [ -n "$_PROGRESS_FILE" ]
}

# JSON string body for $1, left in _PROGRESS_ESC. Control characters other than
# newline, carriage return and tab are dropped.
_progress_esc() {
    local s="$1"
    s=${s//\\/\\\\}
    s=${s//\"/\\\"}
    s=${s//$'\n'/\\n}
    s=${s//$'\r'/\\r}
    s=${s//$'\t'/\\t}
    s=${s//[[:cntrl:]]/}
    _PROGRESS_ESC=$s
}

# Unix time with a fraction, left in _PROGRESS_T. Whole seconds on a bash
# without EPOCHREALTIME (macOS ships 3.2).
_progress_now() {
    if [ -n "${EPOCHREALTIME:-}" ]; then
        _PROGRESS_T=${EPOCHREALTIME/,/.}
    else
        _PROGRESS_T="$(date +%s).0"
    fi
}

# One line, one write: a reader never sees half an object.
_progress_write() {
    printf '%s\n' "$1" >> "$_PROGRESS_FILE" 2>/dev/null || true
}

# progress_event STEP STATE LABEL [DETAIL] [PCT] [EXTRA]
# EXTRA is appended as-is and must already be valid JSON members (,"k":"v").
progress_event() {
    progress_on || return 0
    local step="$1" state="$2" label="${3:-}" detail="${4:-}" pct="${5:-}" extra="${6:-}" line
    _progress_now
    line="{\"t\":${_PROGRESS_T},\"step\":\"${step}\",\"state\":\"${state}\""
    _progress_esc "$label"
    line="${line},\"label\":\"${_PROGRESS_ESC}\""
    if [ -n "$detail" ]; then
        _progress_esc "${detail:0:600}"
        line="${line},\"detail\":\"${_PROGRESS_ESC}\""
    fi
    case "$pct" in
        ''|*[!0-9]*) ;;
        *) line="${line},\"pct\":${pct}" ;;
    esac
    _progress_write "${line}${extra}}"
}

# progress_init PATH — turn progress on. A relative path is taken from the
# directory start.sh was run in. The file is emptied, except on the one
# automatic re-run start.sh makes after undoing a self-edit, which continues
# the same file.
progress_init() {
    local path="$1"
    [ -n "$path" ] || return 0
    case "$path" in
        /*) ;;
        *) path="$PWD/$path" ;;
    esac
    mkdir -p "$(dirname "$path")" 2>/dev/null || true
    if [ -z "${GUAARDVARK_SELF_EDIT_RETRY:-}" ] || [ ! -f "$path" ]; then
        if ! : > "$path" 2>/dev/null; then
            echo "start.sh: cannot write the progress file $path; continuing without it." >&2
            return 0
        fi
        # Error text can carry a service URL; keep the file to this user.
        chmod 600 "$path" 2>/dev/null || true
    fi
    _PROGRESS_FILE="$path"
    export GUAARDVARK_PROGRESS_FILE="$path"
    # Remember what vader_error says, so a "failed" line can repeat it.
    if declare -F vader_error >/dev/null 2>&1 && ! declare -F _progress_wrapped_vader_error >/dev/null 2>&1; then
        eval "_progress_wrapped_$(declare -f vader_error)"
        vader_error() {
            progress_note_error "${1:-}"
            _progress_wrapped_vader_error "$@"
        }
    fi
}

# progress_note_error TEXT — error text that reached the terminal some other way
# than vader_error, kept for the "failed" line.
progress_note_error() {
    progress_on || return 0
    [ -n "${1:-}" ] || return 0
    _PROGRESS_ERRORS="${_PROGRESS_ERRORS:+$_PROGRESS_ERRORS }$1"
}

# progress_log PATH — the log to name if startup fails from here on.
progress_log() {
    _PROGRESS_LOG="$1"
}

# progress_end [STATE] [DETAIL] — end the open step: ok (default), skip or fail.
# A step that ends "fail" without a terminal "failed" line had a problem that
# startup carried on past.
progress_end() {
    progress_on || return 0
    [ -n "$_PROGRESS_STEP" ] || return 0
    progress_event "$_PROGRESS_STEP" "${1:-ok}" "$_PROGRESS_LABEL" "${2:-}"
    _PROGRESS_STEP=""
}

# progress_step ID LABEL [PCT] — begin a step; the one before it ends "ok"
# unless progress_end already closed it.
progress_step() {
    progress_on || return 0
    progress_end ok
    _PROGRESS_STEP="$1"
    _PROGRESS_LABEL="$2"
    _PROGRESS_ERRORS=""
    progress_event "$1" start "$2" "" "${3:-}"
}

# progress_info DETAIL — what the open step is doing right now.
progress_info() {
    progress_on || return 0
    [ -n "$_PROGRESS_STEP" ] || return 0
    progress_event "$_PROGRESS_STEP" info "$_PROGRESS_LABEL" "$1"
}

# progress_ready WEB_URL API_URL — the terminal success line.
progress_ready() {
    progress_on || return 0
    [ "$_PROGRESS_TERMINAL" = 1 ] && return 0
    progress_end ok
    local extra
    _progress_esc "$1"
    extra=",\"url\":\"${_PROGRESS_ESC}\""
    _progress_esc "$2"
    extra="${extra},\"api\":\"${_PROGRESS_ESC}\""
    progress_event ready ok "Guaardvark is ready" "" 100 "$extra"
    _PROGRESS_TERMINAL=1
}

# progress_failed LABEL [DETAIL] [LOG] — the terminal failure line. The open
# step, if any, ends "fail" first and is named in "at".
progress_failed() {
    progress_on || return 0
    [ "$_PROGRESS_TERMINAL" = 1 ] && return 0
    local label="$1" detail="${2:-}" log="${3:-$_PROGRESS_LOG}" at="$_PROGRESS_STEP" extra
    progress_end fail "$detail"
    _progress_esc "$log"
    extra=",\"log\":\"${_PROGRESS_ESC}\""
    if [ -n "$at" ]; then
        extra="${extra},\"at\":\"${at}\""
    fi
    # "detail" is always present on this line, empty when there is nothing to add.
    if [ -z "$detail" ]; then
        extra=",\"detail\":\"\"${extra}"
    fi
    progress_event failed fail "$label" "$detail" "" "$extra"
    _PROGRESS_TERMINAL=1
}

# EXIT trap body. Any way out of start.sh that did not write "ready" writes
# "failed", so a reader is never left waiting on a file that will not grow.
# Must be the first command of the trap: it reads the script's exit status.
progress_on_exit() {
    local rc=$?
    progress_on || return 0
    [ "$_PROGRESS_TERMINAL" = 1 ] && return 0
    # Background helpers are subshells of start.sh; only the script itself reports.
    if [ -n "${BASHPID:-}" ] && [ "$BASHPID" != "$$" ]; then
        return 0
    fi
    local label detail
    if [ -n "$_PROGRESS_STEP" ]; then
        label="Startup stopped at: $_PROGRESS_LABEL"
    else
        label="Guaardvark did not start"
    fi
    detail="$_PROGRESS_ERRORS"
    if [ -z "$detail" ]; then
        if [ "$rc" -eq 0 ]; then
            detail="start.sh ended before Guaardvark was ready: it was stopped, or was run with an option that does not start it."
        elif [ "$rc" -gt 128 ]; then
            detail="start.sh was interrupted (signal $((rc - 128)))."
        else
            detail="start.sh exited with status $rc."
        fi
    fi
    progress_failed "$label" "$detail"
}
