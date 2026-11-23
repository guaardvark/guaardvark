#!/bin/bash
# scripts/lib/apt_install.sh — apt-get install that survives empty package lists.
# Sourced by start.sh (which scripts/platform/linux.sh relies on) and start_postgres.sh.
#
# Fresh cloud, container and minimal server images ship with no apt package
# lists, so a bare `apt-get install` answers "Unable to locate package" for
# packages the release does carry (zstd, postgresql-16-pgvector, python3.12-dev).

# gv_apt_install PKG...
#   sudo apt-get install -y PKG...; when that fails, refresh the package lists
#   once per run (shared with child scripts through _GV_APT_REFRESHED) and try
#   again. Returns apt-get's status. Output goes wherever the caller sends it.
gv_apt_install() {
    command -v apt-get >/dev/null 2>&1 || return 1
    sudo apt-get install -y "$@" && return 0
    [ "${_GV_APT_REFRESHED:-0}" = 1 ] && return 1
    _GV_APT_REFRESHED=1
    export _GV_APT_REFRESHED
    sudo apt-get update -qq || return 1
    sudo apt-get install -y "$@"
}
