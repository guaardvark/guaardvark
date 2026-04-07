#!/bin/bash
# Entrypoint for Guaardvark test containers.
# Starts PostgreSQL and Redis (no systemd needed), then hands off to the user.

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Guaardvark Test Container"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Start PostgreSQL
PG_VERSION=$(ls /etc/postgresql/ 2>/dev/null | head -1)
if [ -n "$PG_VERSION" ]; then
    echo "  Starting PostgreSQL ${PG_VERSION}..."
    sudo pg_ctlcluster "$PG_VERSION" main start 2>/dev/null
    echo "  ✔ PostgreSQL running"
else
    echo "  ⚠ PostgreSQL not found"
fi

# Start Redis
echo "  Starting Redis..."
sudo redis-server --daemonize yes --loglevel warning 2>/dev/null
echo "  ✔ Redis running"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# If a release zip was mounted at /tmp/release.zip, extract it
if [ -f /tmp/release.zip ]; then
    echo "  Found release zip — extracting..."
    mkdir -p ~/guaardvark
    unzip -qo /tmp/release.zip -d ~/guaardvark
    echo "  ✔ Extracted to ~/guaardvark"
    echo ""
    echo "  To install:  cd ~/guaardvark && ./start.sh"
    echo ""
    cd ~/guaardvark
fi

# Hand off to whatever command was passed (default: bash)
exec "$@"
