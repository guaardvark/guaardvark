#!/usr/bin/env bash
# Which Guaardvark install a process belongs to. Sourced by start.sh, stop.sh and the
# other scripts/lib helpers so that starting or stopping one checkout never signals
# another's processes, including a git worktree checked out inside this one.
#
#   proc_cwd PID                     working directory, or empty when it can't be read
#   install_root_of PATH             innermost install (start.sh + backend/app.py) holding PATH
#   proc_install_root PID [cwd]      the install PID runs from: its working directory, else an
#                                    absolute path on its command line, else GUAARDVARK_ROOT in
#                                    its environment. "cwd" consults the working directory only.
#   proc_in_install PID ROOT [cwd]   true when PID runs from ROOT and not from an install nested
#                                    inside it; same sources and "cwd" as proc_install_root
#   port_listener_pids PORT          PIDs listening on TCP PORT, one per line
#   proc_alive PID                   true while PID runs (a zombie has stopped running)
#
# Every lookup prints nothing when it cannot tell, and callers treat that as "not ours".

# kill -0 also succeeds for a zombie: a process that has exited but whose parent has
# not collected its exit status yet (a container whose PID 1 never does, for one).
proc_alive() {
    local pid="$1" st=""
    kill -0 "$pid" 2>/dev/null || return 1
    if [ -r "/proc/$pid/stat" ]; then
        st=$(cat "/proc/$pid/stat" 2>/dev/null)
        st=${st##*) }
    else
        st=$(ps -o stat= -p "$pid" 2>/dev/null)
        st=${st#"${st%%[! ]*}"}
    fi
    case "$st" in
        Z*) return 1 ;;
    esac
    return 0
}

proc_cwd() {
    local pid="$1" cwd=""
    if [ -e "/proc/$pid/cwd" ]; then
        cwd=$(readlink -f "/proc/$pid/cwd" 2>/dev/null)
    elif command -v lsof >/dev/null 2>&1; then
        cwd=$(lsof -a -d cwd -p "$pid" -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)
    fi
    # A removed worktree's processes report "<path> (deleted)"; that path no longer
    # names an install, and its parent may be a different one.
    case "$cwd" in *" (deleted)") cwd="" ;; esac
    printf '%s' "$cwd"
}

install_root_of() {
    local dir="$1"
    case "$dir" in /*) ;; *) return 0 ;; esac
    while :; do
        if [ -f "$dir/start.sh" ] && [ -f "$dir/backend/app.py" ]; then
            (cd "$dir" 2>/dev/null && pwd -P)
            return 0
        fi
        case "$dir" in /|"") return 0 ;; esac
        dir=${dir%/*}
        [ -z "$dir" ] && dir=/
    done
}

_proc_args() {
    if [ -r "/proc/$1/cmdline" ]; then
        tr '\0' '\n' < "/proc/$1/cmdline" 2>/dev/null
    else
        ps -p "$1" -o args= 2>/dev/null | tr ' ' '\n'
    fi
}

proc_install_root() {
    local pid="$1" mode="${2:-}" cwd root="" arg env_root=""
    cwd=$(proc_cwd "$pid")
    [ -n "$cwd" ] && root=$(install_root_of "$cwd")
    if [ -z "$root" ] && [ "$mode" != cwd ]; then
        while IFS= read -r arg; do
            case "$arg" in
                /*) root=$(install_root_of "$arg"); [ -n "$root" ] && break ;;
            esac
        done < <(_proc_args "$pid")
        if [ -z "$root" ] && [ -r "/proc/$pid/environ" ]; then
            env_root=$(tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null | sed -n 's/^GUAARDVARK_ROOT=//p' | head -1)
            [ -n "$env_root" ] && root=$(install_root_of "$env_root")
        fi
    fi
    printf '%s' "$root"
}

# 0 when PATH is ROOT, or below it with no other install in between; 1 when PATH is
# below ROOT inside a nested install or inside another install; 2 when it tells nothing.
# ROOT is physical; a PATH outside it textually may still reach it through a symlink.
_path_in_root() {
    local path="$1" root="$2" dir owner
    case "$path" in
        "$root"|"$root"/*) ;;
        *)
            owner=$(install_root_of "$path")
            [ -z "$owner" ] && return 2
            [ "$owner" = "$root" ] && return 0
            return 1 ;;
    esac
    dir="$path"
    while [ "$dir" != "$root" ]; do
        [ -f "$dir/start.sh" ] && [ -f "$dir/backend/app.py" ] && return 1
        dir=${dir%/*}
    done
    return 0
}

proc_in_install() {
    local pid="$1" mode="${3:-}" root cwd arg env_root rc
    root=$(cd "$2" 2>/dev/null && pwd -P) || return 1
    cwd=$(proc_cwd "$pid")
    if [ -n "$cwd" ]; then
        _path_in_root "$cwd" "$root"; rc=$?
        [ "$rc" -ne 2 ] && return "$rc"
    fi
    [ "$mode" = cwd ] && return 1
    while IFS= read -r arg; do
        case "$arg" in
            /*) _path_in_root "$arg" "$root"; rc=$?; [ "$rc" -ne 2 ] && return "$rc" ;;
        esac
    done < <(_proc_args "$pid")
    if [ -r "/proc/$pid/environ" ]; then
        env_root=$(tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null | sed -n 's/^GUAARDVARK_ROOT=//p' | head -1)
        if [ -n "$env_root" ]; then
            env_root=$(cd "$env_root" 2>/dev/null && pwd -P) || return 1
            [ "$env_root" = "$root" ] && return 0
        fi
    fi
    return 1
}

port_listener_pids() {
    if command -v lsof >/dev/null 2>&1; then
        lsof -i TCP:"$1" -sTCP:LISTEN -t 2>/dev/null | sort -u
    elif command -v ss >/dev/null 2>&1; then
        ss -tlpn 2>/dev/null | grep ":$1\b" | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u
    fi
}
