# Guaardvark Code Release

## Backup Information
- **Date:** (filled by Code Release)
- **Type:** Code Release (no data — database and files are created fresh on first run)

## Install (Linux)

**One-liner:**

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
| API | http://localhost:5000 |
| Health Check | http://localhost:5000/api/health |

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

## Alternative: Docker (Linux, core stack only)

If you want to evaluate the UI/API without a native Python install:

```bash
./start-docker.sh          # CPU
./start-docker.sh --gpu    # NVIDIA GPU (requires nvidia-container-toolkit)
```

Docker runs the **core stack** (API, UI, PostgreSQL, Redis, Ollama). It does not include plugins, ComfyUI, or the virtual agent display. For the full experience, use `./start.sh`.

Stop: `docker compose down`

## Custom plugin ports

To run a plugin on a non-default port (e.g. an existing ComfyUI Desktop on 8000), create a `plugin.local.json` next to the plugin's `plugin.json`:

```bash
echo '{"port": 8000}' > plugins/comfyui/plugin.local.json
```

The file is gitignored and merged over the manifest at load, so the override survives updates. Any manifest key can be overridden the same way. The backend's ComfyUI clients follow the effective port automatically (or set `GUAARDVARK_COMFYUI_URL` to point somewhere else entirely).

## Troubleshooting

- Permission issues: `chmod +x *.sh`
- Health diagnostics: `./start.sh --test`
- Wrong Python venv (e.g. after upgrade): `rm -rf backend/venv && ./start.sh`
- Check logs in `logs/`

## Data

To restore existing data, use a separate Guaardvark data backup.
