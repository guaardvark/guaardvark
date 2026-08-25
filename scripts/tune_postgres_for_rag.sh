#!/usr/bin/env bash
#
# Raise the two PostgreSQL memory settings that the application cannot set itself.
#
# The knowledge index is a pgvector table with an HNSW index over 2560-dimension
# vectors. Every insert walks the graph (ef_construction x m random probes), and
# every query does the same. With the stock 128 MB shared_buffers none of that
# graph stays resident on a machine with tens of gigabytes free, so each probe
# falls through to the OS page cache.
#
# The application already sets what it is allowed to set, per-role, at runtime:
# work_mem, maintenance_work_mem, effective_cache_size and
# max_parallel_maintenance_workers are all USERSET. The two below are not --
# shared_buffers needs a postmaster restart and max_wal_size needs a superuser --
# which is why they live in a script you run deliberately rather than something
# the app changes underneath you.
#
# Values assume a machine with >= 32 GB of RAM. Adjust SHARED_BUFFERS to roughly
# a quarter of total RAM if yours differs.
#
# Usage:
#   sudo scripts/tune_postgres_for_rag.sh            apply and restart
#   sudo scripts/tune_postgres_for_rag.sh --dry-run  print what would change
set -euo pipefail

SHARED_BUFFERS="${SHARED_BUFFERS:-8GB}"
MAX_WAL_SIZE="${MAX_WAL_SIZE:-8GB}"
DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

if [ "$(id -u)" -ne 0 ] && [ "$DRY_RUN" -eq 0 ]; then
  echo "This needs root to reach the postgres superuser and restart the service." >&2
  echo "Re-run with sudo, or use --dry-run to see the statements." >&2
  exit 1
fi

# Reading current values needs no privileges, so this works in --dry-run too.
show_current() {
  local q="SELECT '  '||name||' = '||setting||coalesce(' '||unit,'') FROM pg_settings
             WHERE name IN ('shared_buffers','max_wal_size');"
  if [ -n "${DATABASE_URL:-}" ]; then
    psql "$DATABASE_URL" -tAc "$q" 2>/dev/null && return 0
  fi
  sudo -n -u postgres psql -tAc "$q" 2>/dev/null && return 0
  echo "  (could not read current settings; set DATABASE_URL or run with sudo)"
}

echo "Current:"
show_current

STATEMENTS="ALTER SYSTEM SET shared_buffers = '${SHARED_BUFFERS}';
ALTER SYSTEM SET max_wal_size = '${MAX_WAL_SIZE}';"

if [ "$DRY_RUN" -eq 1 ]; then
  echo; echo "Would run:"; echo "$STATEMENTS" | sed 's/^/  /'
  echo "  -- then restart postgresql (shared_buffers needs a postmaster restart)"
  exit 0
fi

echo "$STATEMENTS" | sudo -u postgres psql -q
echo "Restarting postgresql ..."
systemctl restart postgresql

echo "Now:"
show_current
