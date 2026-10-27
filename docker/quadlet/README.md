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
