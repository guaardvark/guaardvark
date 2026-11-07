#!/usr/bin/env bash
# start-docker.sh — Linux Docker fallback for core Guaardvark stack (API + UI + Ollama).
# Primary install path remains ./start.sh (full native stack with plugins/GPU).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

GPU_PROFILE=0
DETACH=1
for arg in "$@"; do
    case "$arg" in
        --gpu) GPU_PROFILE=1 ;;
        --foreground|-f) DETACH=0 ;;
        -h|--help)
            echo "Usage: ./start-docker.sh [--gpu] [--foreground]"
            echo ""
            echo "  --gpu          Enable NVIDIA GPU profile for Ollama/backend (requires nvidia-container-toolkit)"
            echo "  --foreground   Run in foreground (default: detached -d)"
            echo ""
            echo "Core stack only: API, UI, PostgreSQL, Redis, Ollama."
            echo "For the full native stack (plugins, agent display, ComfyUI), use ./start.sh"
            exit 0
            ;;
    esac
done

if ! command -v docker >/dev/null 2>&1; then
    echo "Error: docker not found. Install Docker Engine, then re-run." >&2
    echo "  Ubuntu:  sudo apt install docker.io docker-compose-v2 docker-buildx" >&2
    echo "  Others:  INSTALL.md, Docker section" >&2
    exit 1
fi

COMPOSE=(docker compose)
if ! docker compose version >/dev/null 2>&1; then
    if command -v docker-compose >/dev/null 2>&1; then
        COMPOSE=(docker-compose)
    else
        echo "Error: docker compose plugin not found." >&2
        echo "  Ubuntu:  sudo apt install docker-compose-v2" >&2
        echo "  Others:  INSTALL.md, Docker section" >&2
        exit 1
    fi
fi

# Can this user reach the Docker daemon? Asked before anything is built, so a
# missing group does not surface minutes later as a failed image pull.
if ! DOCKER_INFO_ERR="$(docker info 2>&1 >/dev/null)"; then
    if printf '%s' "$DOCKER_INFO_ERR" | grep -qi "permission denied"; then
        echo "Error: $(id -un) may not use Docker yet (permission denied on the Docker socket)." >&2
        echo "  sudo usermod -aG docker \$USER" >&2
        echo "  then log out and back in (or reboot), and re-run ./start-docker.sh." >&2
    else
        echo "Error: the Docker daemon is not answering:" >&2
        printf '  %s\n' "$DOCKER_INFO_ERR" | head -n 3 >&2
        echo "  Start it with: sudo systemctl enable --now docker" >&2
    fi
    exit 1
fi

COMPOSE_FILES=(-f docker-compose.yml)

# The backend container answers to this machine's name, and says it in messages
# that send someone here (docker-compose.yml, backend hostname).
export GUAARDVARK_DOCKER_HOSTNAME="${GUAARDVARK_DOCKER_HOSTNAME:-$(hostname 2>/dev/null || echo guaardvark)}"
if [ "$GPU_PROFILE" -eq 1 ]; then
    COMPOSE_FILES+=(-f docker-compose.gpu.yml)

    # --gpu hands the NVIDIA GPU to containers. On Linux that takes the nvidia
    # runtime from NVIDIA Container Toolkit, which is not in Ubuntu's own
    # archive. Docker Desktop on Windows passes the GPU through WSL 2 and need
    # not list that runtime, so when it is absent ask Docker for a GPU once,
    # with the redis image the stack pulls anyway. Without a GPU the stack
    # builds for minutes and then stops at "could not select device driver".
    if ! docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q '"nvidia"' \
        && ! docker run --rm --gpus all --entrypoint true redis:7-alpine >/dev/null 2>&1; then
        echo "Error: --gpu needs a GPU Docker can hand to containers, and Docker could not." >&2
        echo "  Linux: install NVIDIA Container Toolkit (INSTALL.md, Docker section, GPU), ending with:" >&2
        echo "  sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker" >&2
        echo "  Windows: Docker Desktop with the WSL 2 backend, a current NVIDIA driver, and wsl --update." >&2
        echo "  Or run without --gpu (CPU only)." >&2
        exit 1
    fi

    # PyTorch for this card, the same table as hardware_policy.torch_channel.
    # The PyPI default build has no kernels below sm_75 (Pascal, Volta).
    # WSL keeps nvidia-smi in /usr/lib/wsl/lib; look there when it is not on PATH.
    NVIDIA_SMI="$(command -v nvidia-smi || true)"
    if [ -z "$NVIDIA_SMI" ] && [ -x /usr/lib/wsl/lib/nvidia-smi ]; then
        NVIDIA_SMI=/usr/lib/wsl/lib/nvidia-smi
    fi
    if [ -z "${GUAARDVARK_TORCH_CHANNEL:-}" ] && [ -n "$NVIDIA_SMI" ]; then
        COMPUTE_MAJOR="$("$NVIDIA_SMI" --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -n 1 | cut -d. -f1 | tr -dc '0-9')"
        if [ -n "$COMPUTE_MAJOR" ]; then
            if [ "$COMPUTE_MAJOR" -ge 9 ]; then GUAARDVARK_TORCH_CHANNEL=cu128
            elif [ "$COMPUTE_MAJOR" -ge 8 ]; then GUAARDVARK_TORCH_CHANNEL=cu124
            elif [ "$COMPUTE_MAJOR" -ge 6 ]; then GUAARDVARK_TORCH_CHANNEL=cu118
            else GUAARDVARK_TORCH_CHANNEL=cpu
            fi
            echo "PyTorch build for this GPU (compute ${COMPUTE_MAJOR}.x): ${GUAARDVARK_TORCH_CHANNEL}"
        fi
    fi
    export GUAARDVARK_TORCH_CHANNEL="${GUAARDVARK_TORCH_CHANNEL:-}"
fi

# ── API key ────────────────────────────────────────────────────────────────
# The UI reaches the backend through the frontend container, so every browser,
# this host's included, counts as another device, and protected actions need
# the install's API key. docker compose reads GUAARDVARK_API_KEY from .env next
# to docker-compose.yml (a value exported in this shell wins over it).
ENV_FILE="$SCRIPT_DIR/.env"
env_file_value() {
    [ -f "$ENV_FILE" ] || return 0
    { grep -E "^$1=" "$ENV_FILE" || true; } | tail -n 1 | cut -d= -f2- | tr -d "\"' \r"
}
env_file_key() { env_file_value GUAARDVARK_API_KEY; }
# Writes NAME=VALUE into .env, replacing an empty NAME= line rather than adding
# a second one. Rewriting in place keeps the file's owner and mode.
write_env_value() {
    if [ ! -f "$ENV_FILE" ]; then
        (umask 077 && : > "$ENV_FILE")
    elif grep -q "^$1=" "$ENV_FILE"; then
        _kept="$(grep -v "^$1=" "$ENV_FILE" || true)"
        if [ -n "$_kept" ]; then printf '%s\n' "$_kept" > "$ENV_FILE"; else : > "$ENV_FILE"; fi
    elif [ -n "$(tail -c 1 "$ENV_FILE")" ]; then
        # The last line has no newline; do not glue the value onto it.
        printf '\n' >> "$ENV_FILE"
    fi
    printf '%s=%s\n' "$1" "$2" >> "$ENV_FILE"
    # Same rule as start.sh: .env holds secrets, so only its owner reads it.
    chmod 600 "$ENV_FILE"
}
NEW_API_KEY=""
if [ -z "${GUAARDVARK_API_KEY:-}" ] && [ -z "$(env_file_key)" ]; then
    # 32 random bytes as URL-safe base64, the same form the backend makes.
    NEW_API_KEY="$(head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=\n')"
    write_env_value GUAARDVARK_API_KEY "$NEW_API_KEY"
    # Shown now as well as at the end: if this first start stops partway, the
    # key is already saved and would otherwise never be printed.
    echo "Created this install's API key (saved as GUAARDVARK_API_KEY in .env): $NEW_API_KEY"
fi

# ── Database and queue passwords ───────────────────────────────────────────
# docker-compose.yml falls back to the stock "guaardvark" for both when .env
# does not set them. Hex keeps them safe inside the backend's connection URLs.
random_hex() { head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n'; }

# Redis keeps nothing on disk here, so any install can take a password at once.
NEW_REDIS_PASSWORD=0
if [ -z "${GUAARDVARK_REDIS_PASSWORD:-}" ] && [ -z "$(env_file_value GUAARDVARK_REDIS_PASSWORD)" ]; then
    write_env_value GUAARDVARK_REDIS_PASSWORD "$(random_hex)"
    NEW_REDIS_PASSWORD=1
fi

# PostgreSQL reads POSTGRES_PASSWORD only when it creates the database, so a
# password is generated only while this project has no database volume yet.
# An existing database keeps the password it was created with.
compose_project_name() {
    local name
    name="$({ "${COMPOSE[@]}" "${COMPOSE_FILES[@]}" config 2>/dev/null || true; } | sed -n 's/^name: *//p' | head -n 1 || true)"
    if [ -z "$name" ]; then
        name="${COMPOSE_PROJECT_NAME:-$(env_file_value COMPOSE_PROJECT_NAME)}"
    fi
    if [ -z "$name" ]; then
        name="$(basename "$SCRIPT_DIR" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-')"
    fi
    printf '%s' "$name"
}
NEW_POSTGRES_PASSWORD=0
STOCK_POSTGRES_PASSWORD=0
if [ -z "${GUAARDVARK_POSTGRES_PASSWORD:-}" ] && [ -z "$(env_file_value GUAARDVARK_POSTGRES_PASSWORD)" ]; then
    PROJECT_NAME="$(compose_project_name)"
    if PG_VOLUMES="$(docker volume ls -q \
            --filter "label=com.docker.compose.project=$PROJECT_NAME" \
            --filter "label=com.docker.compose.volume=pgdata" 2>/dev/null)" \
        && [ -z "$PG_VOLUMES" ]; then
        write_env_value GUAARDVARK_POSTGRES_PASSWORD "$(random_hex)"
        NEW_POSTGRES_PASSWORD=1
    else
        # A database already exists (or Docker could not say): keep the stock
        # password rather than lock the backend out of it.
        STOCK_POSTGRES_PASSWORD=1
    fi
fi

# ── Database indexes ───────────────────────────────────────────────────────
# A database first created by the postgres:16-alpine image (musl) sorts text
# differently from pgvector/pgvector:pg16 (glibc), so its text indexes would
# return wrong results. Rebuild them once per database volume; a marker in the
# data directory skips this afterwards, and on a new volume it takes a moment.
echo "Starting PostgreSQL..."
"${COMPOSE[@]}" "${COMPOSE_FILES[@]}" up -d --wait postgres
if ! "${COMPOSE[@]}" "${COMPOSE_FILES[@]}" exec -T -u postgres postgres sh -c '
        marker="$PGDATA/.guaardvark-indexes-rebuilt"
        [ -f "$marker" ] && exit 0
        echo "Rebuilding database indexes once for this PostgreSQL image..."
        psql -v ON_ERROR_STOP=1 -q -U guaardvark -d guaardvark -c "REINDEX DATABASE guaardvark" \
            && touch "$marker"'; then
    echo "Warning: could not rebuild the database indexes; they are tried again on the next start." >&2
fi

UP_ARGS=(up --build)
if [ "$DETACH" -eq 1 ]; then
    UP_ARGS+=(-d)
fi
if [ "$GPU_PROFILE" -eq 1 ]; then
    echo "Starting Guaardvark (Docker, GPU)..."
else
    echo "Starting Guaardvark (Docker, CPU)..."
fi

"${COMPOSE[@]}" "${COMPOSE_FILES[@]}" "${UP_ARGS[@]}"

WEB_PORT="${GUAARDVARK_FRONTEND_PUBLISH_PORT:-$(env_file_value GUAARDVARK_FRONTEND_PUBLISH_PORT)}"
API_PORT="${GUAARDVARK_BACKEND_PUBLISH_PORT:-$(env_file_value GUAARDVARK_BACKEND_PUBLISH_PORT)}"
echo ""
echo "  Web UI:       http://localhost:${WEB_PORT:-5173}"
echo "  API:          http://localhost:${API_PORT:-5000}"
echo "  Health:       http://localhost:${API_PORT:-5000}/api/health"
echo "  Stop:         docker compose down"
echo ""
echo "  Only this machine can open these. To use the Web UI from another device,"
echo "  INSTALL.md (Docker, Ports) shows the one line to add to .env."
echo ""
if [ -n "$NEW_API_KEY" ]; then
    echo "  ────────────────────────────────────────────────────────────────"
    echo "  API key for this install (saved as GUAARDVARK_API_KEY in .env):"
    echo ""
    echo "      $NEW_API_KEY"
    echo ""
    echo "  Open the Web UI, go to Settings → API key, paste it and press Save."
    echo "  That signs the browser in; do it once in every browser you use."
    echo "  Running tools, automation, backups and other protected actions need it."
    echo "  ────────────────────────────────────────────────────────────────"
else
    echo "  API key:      GUAARDVARK_API_KEY in .env (grep GUAARDVARK_API_KEY .env);"
    echo "                enter it once in Settings → API key in each browser."
fi
if [ "$NEW_POSTGRES_PASSWORD" -eq 1 ]; then
    echo ""
    echo "  PostgreSQL has this install's own password: GUAARDVARK_POSTGRES_PASSWORD in .env."
fi
if [ "$NEW_REDIS_PASSWORD" -eq 1 ]; then
    echo ""
    echo "  Redis has this install's own password: GUAARDVARK_REDIS_PASSWORD in .env."
fi
if [ "$NEW_POSTGRES_PASSWORD" -eq 1 ] || [ "$NEW_REDIS_PASSWORD" -eq 1 ]; then
    echo "  The backend reads them from there; you need them only for psql or redis-cli."
fi
if [ "$STOCK_POSTGRES_PASSWORD" -eq 1 ]; then
    echo ""
    echo "  PostgreSQL still uses the stock password from when its database was"
    echo "  created. INSTALL.md (Docker → Passwords) shows how to change it."
fi
echo ""
echo "  Note: Docker mode runs the core stack only. Use ./start.sh for the full install."
