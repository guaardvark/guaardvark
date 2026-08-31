#!/usr/bin/env bash
# Parse a PostgreSQL URL without touching a database.
#
#   pg_url_parse "postgresql://user:pass@host:5432/db?sslmode=require"
#   -> PG_URL_USER=user PG_URL_PASS=pass PG_URL_HOST=host PG_URL_PORT=5432 PG_URL_DB=db
#
# Returns 1 (and clears the variables) when the string is not a postgresql://
# URL or names no user or database. Host and port may be empty (socket
# connections). The password is taken verbatim — a password containing '@'
# or '/' must be percent-encoded, as libpq requires anyway.
pg_url_parse() {
  local url="$1"
  PG_URL_USER=""; PG_URL_PASS=""; PG_URL_HOST=""; PG_URL_PORT=""; PG_URL_DB=""
  case "$url" in
    postgresql://*|postgres://*|postgresql+*://*) ;;
    *) return 1 ;;
  esac
  local rest="${url#*://}"
  local auth="" hostpart="$rest"
  if [ "${rest#*@}" != "$rest" ]; then
    auth="${rest%%@*}"
    hostpart="${rest#*@}"
  fi
  if [ -n "$auth" ]; then
    PG_URL_USER="${auth%%:*}"
    [ "${auth#*:}" != "$auth" ] && PG_URL_PASS="${auth#*:}"
  fi
  local hp="${hostpart%%/*}" db=""
  [ "${hostpart#*/}" != "$hostpart" ] && db="${hostpart#*/}"
  db="${db%%\?*}"
  PG_URL_HOST="${hp%%:*}"
  [ "${hp#*:}" != "$hp" ] && PG_URL_PORT="${hp#*:}"
  PG_URL_DB="$db"
  [ -n "$PG_URL_USER" ] && [ -n "$PG_URL_DB" ]
}
