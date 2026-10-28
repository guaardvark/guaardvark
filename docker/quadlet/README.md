# Guaardvark with Podman Quadlet (rootless)

Runs the stack as systemd user services: PostgreSQL (pgvector), Redis, Ollama,
backend and frontend. Tested on a Fedora-based distro with Podman 5.8 and an
AMD RX 7900 XTX (ROCm). NVIDIA is untested, see "GPU" below.

## 1. Build the images (from the repo root)

```
podman build -f docker/Dockerfile.frontend -t guaardvark-frontend .
podman build -f docker/Dockerfile.backend --build-arg TORCH_CHANNEL=rocm6.3 -t guaardvark-backend .
```

`TORCH_CHANNEL` picks the PyTorch wheels: `rocm6.3` for AMD, `cu124` and so on
for NVIDIA. The backend image is large (about 27 GB with ROCm).

## 2. Create the secrets file

```
umask 077
mkdir -p ~/.config/guaardvark
PG=$(openssl rand -hex 16); RD=$(openssl rand -hex 16); KEY=$(openssl rand -hex 24)
cat > ~/.config/guaardvark/guaardvark.env <<EOF
POSTGRES_USER=guaardvark
POSTGRES_PASSWORD=$PG
POSTGRES_DB=guaardvark
REDIS_PASSWORD=$RD
DATABASE_URL=postgresql://guaardvark:$PG@postgres:5432/guaardvark
REDIS_URL=redis://:$RD@redis:6379/0
CELERY_BROKER_URL=redis://:$RD@redis:6379/0
CELERY_RESULT_BACKEND=redis://:$RD@redis:6379/0
GUAARDVARK_API_KEY=$KEY
EOF
```

## 3. Install and start

```
mkdir -p ~/.config/containers/systemd
cp docker/quadlet/*.network docker/quadlet/*.container ~/.config/containers/systemd/
systemctl --user daemon-reload
systemctl --user start guaardvark-frontend
```

Starting the frontend starts the other units through their `Requires=`. The
first start pulls the base images and the backend needs a few minutes.

## 4. First run

Pull a chat model and the embedding model that RAG needs:

```
podman exec guaardvark-ollama ollama pull llama3.2:3b
podman exec guaardvark-ollama ollama pull nomic-embed-text
```

Open http://localhost:5173 and enter the API key once under Settings -> API key:

```
grep GUAARDVARK_API_KEY ~/.config/guaardvark/guaardvark.env
```

## Operation

```
systemctl --user status guaardvark-backend
journalctl --user -u guaardvark-backend -f
systemctl --user stop guaardvark-frontend guaardvark-backend guaardvark-ollama guaardvark-redis guaardvark-postgres
loginctl enable-linger $USER      # start at boot without a login
```

## GPU

The units use `/dev/kfd` and `/dev/dri` (AMD). Your user must be in the `video`
and/or `render` group. On a machine with an integrated GPU, add
`Environment=HIP_VISIBLE_DEVICES=0` to the Ollama and backend units to select
the discrete card.

For NVIDIA, replace the `AddDevice=/dev/kfd` and `AddDevice=/dev/dri` lines with
`AddDevice=nvidia.com/gpu=all` (needs the NVIDIA container toolkit with CDI) and
use `docker.io/ollama/ollama:latest`.

## Differences from docker-compose.yml

- PostgreSQL uses `pgvector/pgvector:pg16` instead of `postgres:16-alpine`.
- Only the frontend (5173) and the backend (127.0.0.1:5000) are published.
  PostgreSQL, Redis and Ollama are reachable only inside the `guaardvark` network.
- Container health checks are set with `HealthCmd=` because `HEALTHCHECK` in the
  Dockerfiles is ignored in the OCI image format.

## System-wide (rootful) install, start at boot without a login

The same units also run as system services. Tested rootful on Podman 5.8.

Images live in the storage of the user that built them. Build as root, or copy
rootless images over:

```
sudo podman build -f docker/Dockerfile.frontend -t localhost/guaardvark-frontend .
sudo podman build -f docker/Dockerfile.backend --build-arg TORCH_CHANNEL=rocm6.3 -t localhost/guaardvark-backend .
```

or

```
podman save localhost/guaardvark-backend:latest | sudo podman load
podman save localhost/guaardvark-frontend:latest | sudo podman load
```

Put the secrets file (created as in step 2) where the system units can read it:

```
sudo install -d -m 700 /etc/guaardvark
sudo cp ~/.config/guaardvark/guaardvark.env /etc/guaardvark/guaardvark.env
sudo chmod 600 /etc/guaardvark/guaardvark.env
```

Install adapted copies of the units. In system units `%h` is `/root`, so the
secrets path is replaced; `WantedBy` becomes `multi-user.target`; `GroupAdd=keep-groups`
is dropped (not needed as root):

```
cd docker/quadlet
sudo install -d /etc/containers/systemd
for f in *.network *.container; do
  sed -e 's#%h/.config/guaardvark/guaardvark.env#/etc/guaardvark/guaardvark.env#' \
      -e 's#^WantedBy=default.target#WantedBy=multi-user.target#' \
      -e '/^GroupAdd=keep-groups/d' "$f" | sudo tee /etc/containers/systemd/"$f" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl start guaardvark-frontend
```

Do not run the rootless and the system-wide units at the same time (ports 5173
and 5000). Port 5173 is published on all interfaces; open it in the firewall
only if you want to reach the UI from other devices. Protected actions need the
API key.

## Using an existing Ollama

If Ollama already runs on the host or in another container that publishes port
11434, skip `guaardvark-ollama.container` (do not install it) and point the
backend at it. In `guaardvark-backend.container`, remove `guaardvark-ollama.service`
from `Requires=` and `After=` and set:

```
Environment=OLLAMA_BASE_URL=http://host.containers.internal:11434
Environment=OLLAMA_HOST=http://host.containers.internal:11434
```

Pull the embedding model that RAG needs into that Ollama
(`ollama pull nomic-embed-text`). Tested rootful with Ollama running in another
container on the same host.
## Limitations

The backend image contains only `backend/`, `scripts/` and `VERSION`. The
`plugins/` directory is not part of it, so no plugin (Audio Foundry, Video
Editor, ComfyUI integration, upscaling, LoRA trainer) is available in the
container. Screen agents additionally need an X desktop (Xvfb, x11vnc, XFCE,
xdotool, scrot, Firefox) that the image does not install. With these packages
added by hand the agent could open Firefox using `gemma4:e2b` and `qwen3.5:9b`
as vision model; a 27B model was too slow for the agent's 45 s step timeout.
Packages added to a running container are lost when it is recreated. Chat, RAG
and document indexing work. Host-level features such as swarms with git
worktrees have not been tested in the container.
