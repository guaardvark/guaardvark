# Guaardvark Code Release

## Backup Information
- **Date:** (filled by Code Release)
- **Type:** Code Release (no data — database and files are created fresh on first run)

## Install (Linux)

**One-liner** (a fresh Ubuntu desktop has no `curl` or `git`: `sudo apt install -y curl git` first):

```bash
curl -fsSL https://guaardvark.com/install.sh | bash
```

(The domain 302-redirects to `raw.githubusercontent.com/guaardvark/guaardvark/main/install.sh`; use that URL directly if you prefer to pin the source.)

Clones to `~/guaardvark` (override with `GUAARDVARK_HOME=/path`) and hands off to `./start.sh`. Re-running updates an existing install; `GUAARDVARK_NO_START=1` clones without launching.

**Not sure what your machine can run?** See [docs/HARDWARE.md](docs/HARDWARE.md) — a tier-by-tier guide to what works CPU-only, on 8–12 GB cards, on the 16 GB design target, and beyond.

**Or from a release zip:**

1. **Extract:**
   ```bash
   unzip guaardvark-release.zip
   cd guaardvark
   chmod +x start.sh start-docker.sh
   ```

2. **Start:**
   ```bash
   ./start.sh
   ```

The startup script handles everything: Python 3.12 (auto-installed if needed), dependencies, database, frontend build, and all services.

**Ubuntu 26.04 and other distros with Python 3.13+:** Your system `python3` may be 3.14 — that is fine. `./start.sh` installs Python 3.12 automatically via apt (deadsnakes PPA) or [uv](https://github.com/astral-sh/uv) when sudo is unavailable.

| Service | URL |
|---------|-----|
| Web UI | http://localhost:5173 |
| API | http://localhost:5000 (macOS: 5055) |
| Health Check | http://localhost:5000/api/health (macOS: 5055) |

First run may ask for your password once (PostgreSQL, Node.js, or Python packages via apt).

**Optional but recommended — Hugging Face token:** the first image/video generation triggers a one-time multi-GB model download. Without a token these downloads are unauthenticated and may be rate-limited. Create a free token at https://huggingface.co/settings/tokens and add one line to `.env` in the project root:

```bash
HF_TOKEN=hf_...
```

**Optional but recommended — protect the desktop from memory pressure:** heavy
generations (large images, video) can push system RAM hard. Guaardvark already
marks its own processes as the OOM killer's preferred victims (so a memory
crisis kills a generation job, not your desktop session), but two OS-level
steps shrink the freeze window further:

```bash
# 1) earlyoom: acts before the kernel stalls; spares the desktop, prefers our workers
sudo apt-get install -y earlyoom
sudo sed -i 's|^EARLYOOM_ARGS=.*|EARLYOOM_ARGS="-r 0 --avoid (^\|/)(gnome-shell\|Xwayland\|gnome-session\|systemd\|dbus)$ --prefer (^\|/)(python3?\|celery)$"|' /etc/default/earlyoom
sudo systemctl restart earlyoom

# 2) Swap posture: >=16GB swap and low swappiness keeps a spike survivable
#    without minutes of desktop-freezing thrash first.
swapon --show   # if under 16G, grow it
echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-guaardvark.conf && sudo sysctl --system
```

## Install (macOS, Apple Silicon)

The same `./start.sh` works on macOS. Known differences, and what to expect:

- **Port 5000 is taken by AirPlay Receiver** on Monterey and later, so on macOS the backend
  defaults to **5055** (`start.sh` writes `FLASK_PORT=5055` to `.env` on first start; the web UI,
  Vite proxy and CLI follow it). Set `FLASK_PORT` yourself to use another port; if you force 5000
  with AirPlay on, `start.sh` detects the clash and says so.
- **GPU work runs on Metal (MPS) where the feature supports it.** Verified on Apple Silicon:
  offline image generation for the Z-Image and Krea 2 families (#183) and LoRA training (#182;
  slow, with timeouts raised to match). Not verified by this project: video generation through
  ComfyUI on Metal (no render on a Mac is on file). ACE-Step song generation and FX Lab (Stable
  Audio Open) each have an experimental Metal path: ACE-Step tries MPS on its own; FX Lab tries
  it when `AUDIO_FOUNDRY_SAO_MPS=1` is set for the audio service. Results from real Macs are
  welcome in #41; the full list is in `docs/HARDWARE.md`, "Apple Silicon".
- **The screen agent is Linux-only.** It is an X11 virtual display (Xvfb); on macOS the agent
  tools report that plainly and the rest of the app is unaffected.
- **Already running ComfyUI Desktop?** Point Guaardvark at it with the port override under
  "Custom plugin ports" below; every page follows the effective port.
- **Fonts** for the video text overlay are found automatically; set
  `GUAARDVARK_OVERLAY_FONT=/path/to/font.ttf` to choose one.

A fresh Apple Silicon runner installs and imports the backend in CI on every push, so a
regression in the above shows up before a person hits it. Report anything else under the
`mac` label — the install thread is issue #41.

## Alternative: Docker (Linux, core stack only)

If you want to evaluate the UI/API without a native Python install:

```bash
./start-docker.sh          # CPU
./start-docker.sh --gpu    # NVIDIA GPU (requires nvidia-container-toolkit)
```

Docker runs the **core stack** (API, UI, PostgreSQL, Redis, Ollama). It does not include plugins, ComfyUI, or the virtual agent display. For the full experience, use `./start.sh`.

**Before the first start (Ubuntu).** A fresh Ubuntu has no Docker, and Ubuntu's `docker.io` does not bring Compose or buildx with it:

```bash
sudo apt install docker.io docker-compose-v2 docker-buildx
sudo usermod -aG docker $USER    # then log out and back in (newgrp is not installed on 26.04)
```

**GPU (`--gpu`).** Containers reach an NVIDIA GPU through NVIDIA Container Toolkit, which is not in Ubuntu's archive. From [NVIDIA's install guide](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html):

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt update && sudo apt install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker
```

`./start-docker.sh` checks each of these before it builds anything and says which one is missing. With `--gpu` it also builds PyTorch for your card: `cu118` for GTX 10/16 and RTX 20 cards, `cu124` for RTX 30/40, `cu128` for newer ones. Set `GUAARDVARK_TORCH_CHANNEL` to pick one yourself.

**Restarts.** The containers start again on their own after a reboot or a crash. `docker compose down` stops them until the next `./start-docker.sh`.

**Build stops at "network is unreachable" with an IPv6 address.** Seen on networks without IPv6 routing, where the image build tried Docker Hub's IPv6 address. Running `./start-docker.sh` again got through.

**API key.** Under Docker the UI reaches the backend through the frontend container, so every browser, this host's included, counts as another device, and protected actions (running tools, automation, backups, file edits) need this install's API key. The first `./start-docker.sh` creates one, saves it as `GUAARDVARK_API_KEY` in `.env` next to `docker-compose.yml`, and prints it. Open the Web UI, go to **Settings → Access**, paste it and press Save. That signs the browser in once (it keeps a sign-in cookie, not the key); do the same once in each browser you use. Later starts leave the key alone; `grep GUAARDVARK_API_KEY .env` shows it again. To change it, edit that line (or delete it and let the next start make a new one) and run `./start-docker.sh` again; every browser then signs in again with the new key. Running `docker compose up` yourself skips this step, and protected actions stay refused until `GUAARDVARK_API_KEY` is set in `.env`.

**Ports.** The Web UI (5173) and the API (5000) are published on every interface, so other devices can use them with the API key. PostgreSQL (5432), Redis (6379) and Ollama (11434) are published on `127.0.0.1` only: the backend reaches them inside Docker's network, and the host ports are there for `psql`, `redis-cli` or `ollama` on this machine. Ollama has no login, so publishing it to the network gives everyone on it your models; PostgreSQL and Redis are behind the passwords below. To publish one anyway for a setup that needs it, set `GUAARDVARK_POSTGRES_PUBLISH_HOST=0.0.0.0`, `GUAARDVARK_REDIS_PUBLISH_HOST=0.0.0.0` or `GUAARDVARK_OLLAMA_PUBLISH_HOST=0.0.0.0` (or one address of this machine) in the `.env` next to `docker-compose.yml` and run `./start-docker.sh` again.

**Passwords.** The first `./start-docker.sh` also writes random `GUAARDVARK_POSTGRES_PASSWORD` and `GUAARDVARK_REDIS_PASSWORD` into that `.env`; the backend reads them from there, and you need them only for `psql` or `redis-cli` on this machine. Running `docker compose up` yourself without them falls back to the stock password `guaardvark` for both. PostgreSQL reads its password only when it creates the database, so an install whose database volume is older than this keeps the stock one, and the start script says so. To change it: run `ALTER USER guaardvark PASSWORD '…';` in `psql` (use letters and digits so it fits in a URL), put the same value in `.env` as `GUAARDVARK_POSTGRES_PASSWORD=…`, and run `./start-docker.sh` again. Redis keeps nothing on disk, so editing `GUAARDVARK_REDIS_PASSWORD` and starting again is enough.

Stop: `docker compose down`

## Custom plugin ports

To run a plugin on a non-default port (e.g. an existing ComfyUI Desktop on 8000), create a `plugin.local.json` next to the plugin's `plugin.json`:

```bash
echo '{"port": 8000}' > plugins/comfyui/plugin.local.json
```

The file is gitignored and merged over the manifest at load, so the override survives updates. Any manifest key can be overridden the same way. The backend's ComfyUI clients follow the effective port automatically (or set `GUAARDVARK_COMFYUI_URL` to point somewhere else entirely).

## Plugin servers and the network

Every plugin server is called by the backend on the same machine, so each listens on `127.0.0.1` only. To let another machine reach one (another install driving this box's ComfyUI or audio, say), set its variable in `.env` and restart the plugin:

| Plugin | Port | Variable |
|---|---|---|
| ComfyUI | 8188 | `GUAARDVARK_COMFYUI_LISTEN=0.0.0.0` |
| Audio Foundry | 8206 | `GUAARDVARK_AUDIO_FOUNDRY_HOST=0.0.0.0` |
| Upscaling | 8202 | `GUAARDVARK_UPSCALING_HOST=0.0.0.0` |
| Video Editor | 8207 | `GUAARDVARK_VIDEO_EDITOR_HOST=0.0.0.0` |
| Vision Pipeline | 8201 | `GUAARDVARK_VISION_PIPELINE_HOST=0.0.0.0` (the camera and its frames come with it) |
| GPU Embedding | 8204 | `PLUGIN_GPU_EMBEDDING_HOST=0.0.0.0` |
| Discord bot health | 8200 | `DISCORD_HEALTH_HOST=0.0.0.0` |

Most of these servers have no login of their own; opening one to the network opens it to everyone on that network. Upscaling and the Vision Pipeline answer every route but `/health` only with the token in `data/.upscaling_internal_secret` or `data/.vision_pipeline_internal_secret` (sent as `Authorization: Bearer <token>`), which the backend sends for you; another machine calling them needs that token. The Swarm (8210) runs coding agents in your repositories and stays on `127.0.0.1`.

The optional web terminal (`scripts/terminal_server.sh start`, ttyd on port 7682, needs `ttyd` installed) is a shell on this machine. It listens on `127.0.0.1` and asks for the user `gvk` and a password made on its first start, kept in `data/terminal/.terminal_auth` (`scripts/terminal_server.sh regenerate-credentials` makes a new one). `GUAARDVARK_TERMINAL_INTERFACE=0.0.0.0` opens it to the network; anyone who can read that password, or watch the process list on this machine, can then use your shell from there. Each answers only requests addressed to an IP address, `localhost` or one of this machine's names, as the backend does (see `host_not_allowed` under Troubleshooting); a caller that uses another name for this machine needs that name in `GUAARDVARK_CORS_ORIGINS`.

## Troubleshooting

- Permission issues: `chmod +x *.sh`
- **A page says to enter the API key**: protected actions (running tools, automation, backups, file edits) work without a key only on the Guaardvark machine itself. On a network you trust, turn on **Settings → Access → Network access** on the Guaardvark machine and every device on its local network can use them without a key. Otherwise, create a key in **Settings → Access** on the Guaardvark machine, then paste it into **Settings → Access** on the other device and press Save, once per browser. Once a key exists, every browser needs to be signed in with it, the Guaardvark machine's included (the browser that created the key already is); that machine keeps the key in `.env` as `GUAARDVARK_API_KEY`.
- **Chat replies, progress or voice never arrive in a browser that reaches Guaardvark under another name** (a reverse proxy such as `https://guaardvark.example`, a DNS name from your router, or Docker opened from another device at `http://<host-ip>:5173`): the backend accepts browser pages only from this install's own addresses — the frontend port on `localhost`, `127.0.0.1`, this machine's own IP addresses, its hostname and `<hostname>.local`, plus `VITE_FRONTEND_URL`. Add the other origin to `.env` as `GUAARDVARK_CORS_ORIGINS=https://guaardvark.example` (comma-separated for several; each is what the browser's address bar shows before the first `/` after the host) and restart the backend. Under Docker, put the line in the `.env` next to `docker-compose.yml`. `logs/backend.log` names the refused one in a line ending `is not an accepted origin.` The same setting applies when saving or starting anything is refused with `cross_site_request` (logged as `[CROSS-SITE] Refused ...`), which can happen with a browser that does not report same-origin requests behind a proxy that does not pass the address it was reached at.
- **A request is refused with `host_not_allowed` (HTTP 421, "this Guaardvark does not answer to the name …")**: the backend answers only requests addressed to an IP address, `localhost`, this machine's hostname (its first part and `<first part>.local` too), or a name you have listed, so a web page whose DNS name has been pointed at this machine cannot use it. If you reach the backend or the UI by another name (a DNS name from your router such as `gpubox.lan`, a Tailscale name, an Interconnector master URL written with a name, a reverse proxy that passes on the `Host` header, or Docker opened at `http://<name>:5173`), add that address to `.env` as `GUAARDVARK_CORS_ORIGINS=http://gpubox.lan:5000` (the refusal names the exact address to add; comma-separated for several) and restart. Under Docker, put the line in the `.env` next to `docker-compose.yml`. A name in `VITE_ALLOWED_HOSTS` counts too, and `VITE_ALLOWED_HOSTS=all` turns this check off along with Vite's. An Interconnector master URL written as the master's IP address always works. `logs/backend.log` names each refused request in a line starting `[HOST] Refused`. The plugin servers and the ComfyUI Guaardvark starts apply the same check with the same settings (restart the plugin after changing them), as do the MCP server's HTTP transport (`python -m backend.mcp http`) and the reboot log; their refusals are logged in the plugin's own log under `logs/`, and ComfyUI's in `logs/comfyui.log`.
- **`start.sh is running as root`**: run `./start.sh` as your normal user, without `sudo`; it asks for your password itself when it installs system packages. If root is the only account on the machine (some GPU cloud hosts and containers), run `GUAARDVARK_ALLOW_ROOT=1 ./start.sh`.
- Health diagnostics: `./start.sh --test`
- Wrong Python venv (e.g. after upgrade): `rm -rf backend/venv && ./start.sh`
- Check logs in `logs/`
- **`extension "vector" is not available`** when indexing: PostgreSQL is installed but pgvector is not. `./start.sh` installs it and enables the extension (needs sudo once); to do it by hand, `sudo apt-get install -y postgresql-<major>-pgvector` then `sudo -u postgres psql -d guaardvark -c "CREATE EXTENSION IF NOT EXISTS vector;"`. If apt cannot find the package on your release, add the [PostgreSQL apt repository](https://www.postgresql.org/download/linux/ubuntu/) first.
- **You run Ollama yourself and do not want the scripts touching it**: `./start.sh --external-ollama` (persists `GUAARDVARK_OLLAMA_EXTERNAL=1` in `.env`). `start.sh` then only checks that `127.0.0.1:11434` answers, and `stop.sh` leaves it alone. Without that flag, `stop.sh` stops only the Ollama `start.sh` itself launched; `./stop.sh --keep-ollama` (or `GUAARDVARK_OLLAMA_KEEP_RUNNING=1`) keeps even that one, and `./stop.sh --all` restores the full sweep (your own `ollama serve`, the systemd service, a dead port holder). Both switches are also in Settings → Product Profile → Ollama.
- **Ollama says a model is missing that `ollama list` shows** (WSL2 especially): a hand-started `ollama serve` runs as your user and reads `~/.ollama/models`, while the systemd service runs as `ollama` and reads `/usr/share/ollama/.ollama/models`. Stop the hand-started one and use the service: `sudo systemctl restart ollama`.
- **`start.sh` says it is "not re-provisioning" PostgreSQL**: your `DATABASE_URL` names a role or database other than the stock `guaardvark`, and the connection failed. The script never resets a role it did not create. Fix the password in `.env`, create the role and database yourself, or run `./start.sh --skip-postgres` for an externally managed database.
- **Film Crew fails with `model 'gemma4:e4b' not found`**: fixed in 2.8.0 — agents now use whichever Gemma4 tag the installer pulled. On older versions, `ollama pull gemma4:e4b`.
- **Faster attention for video models.** ComfyUI runs PyTorch attention by
  default.   Set `GUAARDVARK_COMFYUI_ATTENTION=ck` in `.env` to use the Comfy
  Kitchen int8 kernel that ships in the backend venv, or `sage` if you have
  installed the `sageattention` package into `backend/venv` yourself (Guaardvark
  never installs it for you; SageAttention 2 needs a CUDA 12.4+ toolchain the
  default cu121 wheels do not carry). `auto` picks whichever is available. The
  setting applies to every ComfyUI-routed model, so compare a render before and
  after; the ComfyUI plugin's start log prints the backend it chose. Measured on
  MiniMax H3 (16 GB RTX 40-series, 864x480, 20 steps): `ck` took 339 s against
  390 s with frames indistinguishable at the same seed.
- **Video clips from chat or MCP look washed out or over-cooked**: a request that names no
  guidance renders with the model's own template value (LTX 1, Wan 14B 3.5, Wan 5B 5). Set
  `GUAARDVARK_VIDEO_REFERENCE_DEFAULTS=1` in `.env` (off by default) and restart the backend to
  also send Wan's template negative prompt when none is given. `scripts/video_prompt_ab.py`
  compares the variants on your card.
- **A 20 GB-class video model runs out of memory at the first step** (MiniMax H3 on a 16 GB
  card): ComfyUI left too little room for its activations. Each model declares the
  `--reserve-vram` it needs (H3 5.0, Wan 2.2 14B 1.0) and Guaardvark relaunches ComfyUI when the
  running value differs. Remove `GUAARDVARK_COMFYUI_RESERVE_VRAM` from `.env` if it is set: an
  explicit value overrides every model's own, and H3 runs out of memory at 1.0.
- **A video model renders cleanly on one machine and garbled on another**: the Interconnector
  syncs Guaardvark's code, but not ComfyUI and its custom nodes, the model files, `.env`, the
  PyTorch build or the Studio's own settings (Low VRAM, quality tier). On each machine run
  `backend/venv/bin/python scripts/video_box_fingerprint.py --out fingerprint-<machine>.json`
  (`--no-hash` skips hashing the multi-gigabyte model files), copy one file across and run
  `backend/venv/bin/python scripts/video_box_fingerprint.py --diff fingerprint-a.json fingerprint-b.json`.
  To see how one clip was rendered, run it with `--only graph,mp4 --mp4 path/to/clip.mp4`: a clip
  saved by ComfyUI's video node carries the graph that made it, and the `checks` list names any
  setting that differs from the graph the checkout builds today. `scripts/video_smoke.py --models wan22-5b --mode t2v
  --width 1280 --height 704 --frames 121` renders the same clip on both machines.

## Data

To restore existing data, use a separate Guaardvark data backup.
