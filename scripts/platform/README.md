# scripts/platform/ — detect-then-route launcher pattern

**Status: wired.** `start.sh` sources `detect.sh`, calls `detect_platform()`, then sources
`$GUAARDVARK_PLATFORM_BACKEND` (`linux.sh` or `macos.sh`). Both backends implement the shared
interface; Linux also provides `ensure_node_npm` and auto-installs Python 3.12 on Ubuntu 22.04 /
24.04 / 26.04+ (apt when the system already carries 3.12, otherwise uv).

## Why this shape (vs 3 separate start.sh files)
~90% of `start.sh` is platform-agnostic (venv, pip, migrations, Flask/Celery/frontend, health,
agent display). Only ~10% is platform-specific. Three forked `start.sh` files would duplicate the
90% → they drift. So: **one thin orchestrator + pluggable platform backends sharing one interface.**
This extends the pattern `scripts/install_pytorch.sh` already uses (arch-branching helper).

## The pieces
| File | Role |
|------|------|
| `detect.sh` | `detect_platform()` → sets `GUAARDVARK_OS/_ARCH/_ACCEL/_IS_WSL` + picks the backend. Pure detection. |
| `linux.sh` | Linux backend (x86_64 + Pi-arm64 + WSL). apt or uv + systemctl; Node via apt or `~/.local/node`. |
| `macos.sh` | macOS backend (Apple Silicon primary, Intel untested). Homebrew + brew services; no systemd/sudoers/nvidia. |
| `hardware_policy.platform_profile()` | the auto-detected "machine config" — ONE brain shared by bash + Python when present. |

Pi = `linux.sh` with `ARCH=arm64`. WSL = `linux.sh` with `IS_WSL=1`. Only two backend files.

## The interface (every backend implements these identically)
```
platform_install_system_deps    # postgres/redis/ffmpeg/node/build-tools (apt | brew)
# Note: full Video Editor + music video also needs `melt` (MLT) + Shotcut. See plugins/video_editor/README.md "Linux & macOS Setup".
platform_ensure_python           # guarantee Python 3.12; sets PYTHON_CMD (apt | uv/pyenv | brew)
platform_gpu_setup               # nvidia tuning on Linux+CUDA; no-op on mac/cpu
platform_service_start <svc>     # systemctl | brew services | (WSL/no-systemd fallback)
```
Linux additionally defines `ensure_node_npm`. Torch is NOT in the interface —
`scripts/install_pytorch.sh` already branches Mac/ROCm/CUDA/CPU.

## Verify (per target)
`./start.sh --test` on: NVIDIA x86 (regression guard), Pi5/Debian13 (3.12 via uv),
macOS arm64 (3.12 via brew, MPS torch), Ubuntu 26.04 (system Python 3.14 → auto 3.12).
WSL = the Linux path + `IS_WSL` service fallback.
