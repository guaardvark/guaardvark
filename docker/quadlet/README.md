# Guaardvark with Podman Quadlet (rootless)

Runs the stack as systemd user services: PostgreSQL (pgvector), Redis, Ollama,
backend and frontend. Tested on a Fedora-based distro with Podman 5.8 and an
AMD RX 7900 XTX (ROCm). NVIDIA is untested, see "GPU" below.

## 1. Build the images (from the repo root)

```
podman build -f docker/Dockerfile.frontend -t guaardvark-frontend .
podman build -f docker/Dockerfile.backend --build-arg TORCH_CHANNEL=rocm6.3 -t guaardvark-backend .
```

`TORCH_CHANNEL` picks the PyTorch wheels: `rocm6.3` for AMD, `cu132` and so on
for NVIDIA (INSTALL.md lists which card and driver takes which). The backend image is large (about 27 GB with ROCm).

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

- Only the frontend (127.0.0.1:5173) and the backend (127.0.0.1:5000) are published,
  on this machine only.
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
and 5000). Port 5173 is published on this machine only. To reach the UI from
other devices, change `PublishPort=127.0.0.1:5173:5173` to `PublishPort=5173:5173`
in your copy of `guaardvark-frontend.container`; the UI is then reachable from every
network this machine is on unless a firewall blocks the port.
Protected actions still need the API key.

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

## Screen agent (optional)

The agent desktop (Settings -> Agents) needs Xvfb, x11vnc, an XFCE session, dbus,
xdotool, scrot, lsof and a browser. They are not part of the default image. Build
with the `SCREEN_AGENT` argument to include them (several hundred MB more):

```
podman build -f docker/Dockerfile.backend --build-arg TORCH_CHANNEL=rocm6.3 --build-arg SCREEN_AGENT=1 -t guaardvark-backend
```

Tested with `gemma4:e2b` as the vision model: the agent opened Firefox in the
container. A 27B model was too slow for the agent's 45 s step timeout.

## AMD (ROCm) builds

With `--build-arg TORCH_CHANNEL=rocm6.3` (any `rocm*` channel) the image also installs
`rocm-smi`. The backend reads the card's VRAM through it for the load gate, the hardware
profile, the GPU coordinator and the video preflight; without it the container sees no
AMD card. NVIDIA and CPU builds do not change.

## Limitations

The image contains the plugin sources (`plugins/`) and a `/app/backend/venv` that
sees the system-wide packages, which the plugin start scripts expect. Plugins that
need their own environment create it on first start inside the container, on the
`guaardvark-plugins` volume, so it survives a restart of the unit and an image update;
the image's plugin sources are copied onto the volume at every start.

Tested on AMD (RX 7900 XTX, ROCm, rootless Podman) with an image built with
`TORCH_CHANNEL=rocm6.3` and `SCREEN_AGENT=1`:

| Plugin | Result |
|---|---|
| Swarm Orchestrator, Upscaling | start (no job was run in the container) |
| LoRA Trainer | starts (no training was run) |
| Code Search | needs its in-app Install step first, then starts |
| Video Editor | starts; `melt` is not in the image, so rendering to MP4 returns an error |
| Audio Foundry | times out after 150 s on the first start; it builds its own environments |
| GPU Embedding | does not start: it inherits `FLASK_RUN_FROM_CLI` from the backend (`flask run`), so its `app.run()` is skipped and port 8204 never opens. Started by hand without that variable it answers HTTP 503 ("degraded", `gpu_available: false`): its GPU check only runs `nvidia-smi`, and the plugin manager accepts only HTTP 200 |
| Vision Pipeline | does not start: "Required dependency 'ollama' is not running" although Ollama runs outside the container (the plugin manager builds service URLs as `http://localhost:<port>`, which inside the container is not the host) |
| Ollama | the image has no `ollama` binary: with "I run Ollama myself" on, the card cannot be switched off (by design); with it off, Start fails with "ollama command not found" |
| ComfyUI | not part of the image; the plugin's restore script needs `rsync`, which the image does not contain |
| Discord | needs `DISCORD_BOT_TOKEN` in the environment; not tested with a token |

Chat, RAG and document indexing work.
