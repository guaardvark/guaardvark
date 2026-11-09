#!/bin/sh
# Runs before the container's command (the API and workers, or anything passed
# to docker run).
set -e

# Plugin sources from this image onto the plugins volume (see sync_plugins.py).
python /app/docker/sync_plugins.py || echo "sync_plugins failed; plugins left as they were" >&2

# Settings the app writes to its .env (plugin switches, Keep ready, network
# access, ...) live on the state volume so a recreated container keeps them.
# Variables set by compose still win: the app loads .env without overriding.
if [ -d /app/state ] && [ ! -e /app/.env ] && [ ! -L /app/.env ]; then
    ln -s /app/state/.env /app/.env
fi

# Ollama runs in its own container here, so the backend must never try to start
# or stop it. Once the app has written a .env, that file is what it reads for
# this switch, so the line goes in there as well.
if [ "${GUAARDVARK_OLLAMA_EXTERNAL:-}" = "1" ] && [ -d /app/state ] \
        && ! grep -q '^GUAARDVARK_OLLAMA_EXTERNAL=' /app/state/.env 2>/dev/null; then
    echo 'GUAARDVARK_OLLAMA_EXTERNAL=1' >> /app/state/.env
fi

exec "$@"
