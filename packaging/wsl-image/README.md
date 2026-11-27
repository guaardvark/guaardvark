# Guaardvark WSL image

`build.sh` makes a root filesystem that Windows imports as a ready WSL 2
distribution named `Guaardvark`:

```powershell
wsl --import Guaardvark "$env:LOCALAPPDATA\Guaardvark\wsl" guaardvark-wsl-3.7.0-x64.tar.gz --version 2
wsl -d Guaardvark -- bash -lc "cd ~/guaardvark && ./start.sh"
```

The image is the Ubuntu that `wsl --install` gives today with Guaardvark already
installed by its own `install.sh`, so the first start skips the system packages,
Python, Node, the frontend build, PostgreSQL, Redis, Ollama and PyTorch. It
carries no model weights and no secrets: the first start downloads the chat and
embedding models picked for the PC, as a fresh install does, and makes its own
passwords and keys.

## What is in it

| Part | Where it comes from |
|---|---|
| Base | `ubuntu-26.04.1-wsl-amd64.wsl` from releases.ubuntu.com, the file Microsoft's distribution manifest (`microsoft/WSL`, `distributions/DistributionInfo.json`) lists for the default `Ubuntu`. Its SHA-256 is pinned in `build.sh`; update the URL and hash together when the default moves. |
| Guaardvark | `install.sh` from the requested ref (`--ref`, a branch or tag), run as the default user with no terminal, exactly as `curl … \| bash` runs it, with `GUAARDVARK_BOOTSTRAP_MODELS=0` (no model weights in the image) and the settings in `lib/image.env` set for that run. The checkout is `~/guaardvark` and its `origin` is the public repository. |
| PyTorch | The CUDA build install.sh gives an NVIDIA PC (see below). |
| User | `guaardvark`, uid 1000, the groups Ubuntu's own first-run script gives (`adm cdrom sudo dip plugdev`), no password. |
| `/etc/wsl.conf` | `[boot] systemd=true`, `[user] default=guaardvark`. |
| `/etc/wsl-distribution.conf` | `defaultUid=1000`, `defaultName=Guaardvark`, no OOBE command, WSL's own Start-menu shortcut and Windows Terminal profile off (the desktop app makes its own), icon from `--icon` when given. |

### PyTorch: the NVIDIA build, without a GPU in the build

The Windows app supports NVIDIA cards only, so the image carries the PyTorch an
NVIDIA PC gets from install.sh. install.sh does not choose it itself: start.sh
asks `backend.services.hardware_policy torch_channel`, which reads `nvidia-smi`,
and `scripts/install_pytorch.sh` installs that channel's pinned set. On driver
580 or newer every card from Turing (RTX 20) up, Blackwell (RTX 50, compute 12.0)
included, gets `cu132`: `torch==2.14.1 torchvision==0.29.1 torchaudio==2.11.1
torchcodec==0.17.0` from `https://download.pytorch.org/whl/cu132`. Older drivers
get `cu128` (Blackwell) or `cu126`; Pascal and Volta always `cu126`.

No existing setting forces a CUDA channel without `nvidia-smi` (install_pytorch.sh
honours `GUAARDVARK_TORCH_CHANNEL` only inside its NVIDIA branch, and start.sh
overwrites that variable from the policy). So `build.sh --torch cuda`, the default,
puts `lib/nvidia-smi-build` at `/usr/local/bin/nvidia-smi` for the length of the
build. It answers the installers' queries as an RTX 5060 Ti on Windows driver
581.57 would through WSL (compute 12.0, CUDA 13.0, power settings `[N/A]`), so the
unchanged install.sh takes its NVIDIA path end to end. finalize-root.sh removes it,
and the GPU sudoers rule start.sh wrote for it, before export. `--torch cpu` skips it.

Result in the image: `torch 2.14.1+cu132`, `torch.version.cuda` 13.2. In a
container without a GPU `torch.cuda.is_available()` is `False` and the backend
runs on the CPU; that is also what a Windows PC without an NVIDIA card would see.

On first start on a PC with a card, nothing replaces it: the venv carries
install.sh's completion stamp with the current requirements' fingerprint, so
start.sh skips the dependency bootstrap, and install_pytorch.sh only runs from that
bootstrap (or `scripts/heal_backend_venv.sh`, or a requirements change after an
update; there its fast path keeps a `cu132` build that sees the GPU). No other path
touches the backend's PyTorch; plugins that need their own (video editor, audio)
install it into their own venvs from their Install buttons. The other GPU-related
first-start work is local: the GPU sudoers rule and the Ollama tuning drop-in for
that card (Ollama picks the drop-in up on its next restart, as on a fresh
install). Ollama's CUDA libraries are already in the image. The downloads on that
first start are the models: hardware_policy picks the chat model by VRAM
(`gemma4:12b` for a 12 GB+ card) plus `nomic-embed-text`.

Cards this does not fit: Pascal and Volta (GTX 10, Titan V) need `cu126`; CUDA 13
wheels have no kernels for them. A driver older than 580 cannot run CUDA 13. On
either, torch imports but CUDA stays unavailable and start.sh's preflight warns;
`bash scripts/install_pytorch.sh` inside the distribution
installs the right build. The app could check the Windows driver version before
install.

### Passwordless sudo, and why

`guaardvark` may run `sudo` without a password (`/etc/sudoers.d/90-guaardvark`).
The account has no password to type, and start.sh needs root on the first start
on each machine: it sets a new PostgreSQL password with `sudo -u postgres psql`,
writes Redis's configuration, installs its sudoers rules and the Ollama tuning
drop-in, and repairs packages with `apt-get` when something is missing. The
desktop app runs start.sh with no terminal, so a password prompt would hang it.
This grants nothing new: on Windows, `wsl -d Guaardvark -u root` already opens a
root shell in the distribution without a password.

## Secrets and per-machine state

Nothing secret or machine-specific is baked in. `lib/finalize-root.sh` strips it
after the install and the first start on each machine makes it again with the same
code a fresh install uses:

| Item | In the image | Made on first start by |
|---|---|---|
| `SECRET_KEY` | removed from `.env` | start.sh (`secrets.token_hex(32)`) |
| PostgreSQL password | `DATABASE_URL` removed from `.env`, role password set to NULL; schema and seed rows kept | start_postgres.sh (no `DATABASE_URL` → new random password, `ALTER USER`, written to `.env`) |
| Redis password | `/etc/redis/redis.conf` back to the package's file, `redis-server` unit disabled, URLs removed from `.env` | start_redis.sh (Redis down and no password → new random password, config written, Redis started) |
| PostgreSQL TLS key (ssl-cert "snakeoil") | deleted | `guaardvark-firstboot.service`, once, before PostgreSQL starts (openssl directly: `make-ssl-cert` goes through debconf, which Ubuntu's landscape-config holds at first boot, and failed there) |
| `/etc/machine-id` | empty | systemd on first boot |
| Ollama key (`/usr/share/ollama/.ollama/id_ed25519`) | deleted | Ollama on first serve |
| Cluster node id, `hardware.json` (`~/.guaardvark`) | deleted | start.sh |
| The build's `nvidia-smi` stand-in and the GPU sudoers rule naming it | deleted | start.sh writes the rule for the real `nvidia-smi` |
| Ollama tuning drop-in rendered for the build hardware | deleted | start.sh (rendered for this machine; Ollama picks it up on its next restart, as on a fresh install) |
| Saved chat model (`settings.active_model_name`, `data/active_model.json`) | deleted | backend, on first boot, for this machine |

`.env` in the image holds `PYTHON_CMD=python3.12` and the image's fixed settings
from `lib/image.env`, nothing else.

### One fixed setting: `VITE_ALLOWED_HOSTS=localhost`

start.sh allows the machine's LAN address in the Vite server and folds every
`VITE_*` variable into the frontend build's fingerprint. Under WSL's default NAT
networking that address is a VM address only the PC itself can reach, and it
usually changes when WSL restarts. So on every PC the first start rebuilt the
frontend, and so would every start after `wsl --shutdown`. That build is not just
slow: `vite build` peaked at 5.9-6.4 GB of memory here, on top of the backend,
the workers and a chat model warming up. In a 12 GB container that ran out of
memory on every first start once the models were downloaded (see Measurements),
and WSL gives Linux half the PC's RAM by default, 8 GB on a 16 GB PC.

The image therefore runs its build with `VITE_ALLOWED_HOSTS=localhost` and writes
the same into `.env`. start.sh keeps a value `.env` sets, the fingerprint matches
the build's, and no start rebuilds the frontend unless the code changes. The
Windows browser uses `localhost`; vite.config.js still allows the machine's own
hostname. The one thing lost is reaching the studio by LAN address from another
device under mirrored networking; adding that address to `VITE_ALLOWED_HOSTS` in
`.env` restores it.

Also removed: pip, npm and apt download caches (`data/piptmp` alone was 3.6 GB),
logs, pid files, shell histories, `/tmp`, the journal, the add-apt-repository
cache, and Ollama's ROCm add-on (see below). The apt package lists stay, as in
Ubuntu's own image, so `sudo apt install` works straight away. NLTK's tokenizer
data (`data/cache/nltk_data`, a few MB) stays; it is data, not a model.

The redis choice needs a word: if the unit stayed enabled with the package's
configuration, Redis would come up without a password at boot and start_redis.sh
would adopt that instead of provisioning one. Disabled, every start brings it up
through start_redis.sh, which start.sh runs anyway.

The old PostgreSQL role's password hash may survive in the cluster's files as a
dead row until vacuum; it belongs to a password no copy of the image uses.

## Building

```bash
packaging/wsl-image/build.sh --out DIR [--ref main] [--repo URL] [--torch cuda|cpu] \
    [--measure xz,zst] [--icon desktop/build/icon.ico]
```

Everything runs in docker: the base file is checked against its hash, imported as
an image and booted with systemd as PID 1 (WSL with `systemd=true` does the same),
capped at `--memory 12g --memory-swap 12g --cpus 6`, with no published ports and
no GPU. systemd needs `CAP_SYS_ADMIN` and an unconfined AppArmor profile to mount
its own cgroup tree in a container; nothing else is widened. The root filesystem
is then taken with `docker export` and passed through `lib/rewrite_rootfs.py`,
which removes `/.dockerenv`, puts back the base image's own `/etc/hosts`,
`/etc/hostname` and `/etc/resolv.conf` (docker owns those in a running container),
drops anything left in `/tmp` and `/var/tmp`, and writes `/etc/machine-id` empty.

Output in `DIR`, with the version read from the installed checkout:

- `guaardvark-wsl-<version>-x64.tar.gz`, and the same file split into
  `guaardvark-wsl-<version>-x64.tar.gz.001`, `.002`, … of 1900 MiB each (GitHub
  release assets must be under 2 GiB);
- `guaardvark-wsl-<version>-x64.json`, the manifest `desktop/platform/wsl/image.js`
  reads: `schema`, `version`, `arch`, `format` (`tar.gz`), `size` and `sha256` of
  the whole `.tar.gz`, `installedSize` (bytes of the uncompressed tar, what the
  import unpacks), and `parts` with each part's `name`, `size` and `sha256`. Part
  names are relative to the manifest; joined in order they are the `.tar.gz`;
- `guaardvark-wsl-<version>-x64.tar` (uncompressed), `.sha256`, and with
  `--measure xz,zst` the other compressions, for comparison only;
- `logs/` with the install log, the PyTorch build, the health check, the timings and
  `size-report.txt` (largest directories).

`verify.sh --manifest guaardvark-wsl-<version>-x64.json --out DIR` checks every
part and the joined file against the manifest, imports the result into docker,
boots it the same way and runs start.sh three times as the default user: first
start (with the model downloads, as on a user's PC), a second start, and a start
after a restart of the container (as `wsl --shutdown` and reopening). It records
timings, `/api/health`, the web UI's answer, the PyTorch build and whether any
start reinstalled it, a reply from the downloaded chat model, and that the first
boot made its own keys and secrets. `--tar FILE` imports a single file instead;
`--no-models` skips the downloads.

## Measurements

Build of `main` at e18515f5 (3.7.0) on 2026-11-27 with `--torch cuda`, docker
capped at 12 GB / 6 CPUs, no GPU. Numbers from `logs/timings.txt`,
`logs/size-report.txt` and `verify.sh`.

| Build step | Time |
|---|---|
| install.sh (system packages, Python 3.12 venv with CUDA PyTorch, Node, frontend build, PostgreSQL, Redis, Ollama, migrations, first start) | 9m45s and 15m20s in two runs (mostly download speed: the CUDA wheels are about 3 GB) |
| export + rewrite | 1m29s to 2m20s |
| gzip (`pigz -9`, 6 threads) | 1m59s to 2m31s |
| split + manifest (hashes) | 14-34 s |
| xz (`-6`, 6 threads), `--measure` only | 9m47s |
| whole build, gzip + parts + manifest | 14m28s |

| Artifact | Size |
|---|---|
| `.tar` (uncompressed, the manifest's `installedSize`) | 12.77 GiB (13,714,872,320 bytes); 12.51 GiB of file data in 237,648 files |
| `.tar.gz` (the download) | 6.31 GiB (6,771,700,153 bytes), as 3 parts of 1.86 GiB and one of 0.74 GiB |
| `.tar.xz`, measured only | 5.04 GiB (5,406,771,808 bytes) |

The CPU-PyTorch image measured earlier was 8.40 GiB as `.tar`, 3.71 GiB gzip,
2.85 GiB xz; CUDA PyTorch adds 4.4 GiB uncompressed and 2.6 GiB to the download.

Largest parts (file data): the backend venv 7.13 GiB, of which NVIDIA's CUDA
libraries (`nvidia/*`) 3.03, torch 1.12 and triton 0.87; Ollama's libraries 2.45
GiB (`cuda_v12` and `cuda_v13`); the rest of `/usr/lib` 1.23 GiB; the frontend
0.50 GiB; apt package lists 0.15 GiB; PostgreSQL's data 0.07 GiB.

`verify.sh --manifest` on that build: the four parts and the joined file matched
the manifest (36 s to join and hash), `docker import` took 100 s, then:

| Start | Time to "studio is up" (start.sh wall time) |
|---|---|
| First start: secrets made, `qwen3.5:4b` and `nomic-embed-text` downloaded (3.6 GB), model warmed up | 122.7 s |
| Second start | 48.8 s |
| Start after a restart of the container | 53.9 s |

Each start ended with `/api/health` answering `{"status":"ok","version":"3.7.0",...}`
and the web UI answering `HTTP 200 text/html` with `<title>guaardvark</title>`.
None rebuilt the frontend ("Frontend unchanged since the last good build") and
none ran the PyTorch installer or a dependency bootstrap. PyTorch read
`2.14.1+cu132`, CUDA build 13.2, `cuda.is_available()` False, before and after
the starts. The downloaded model answered through Ollama (`reply: 'ready'`).
The container peaked at 6.04 GiB.

Before `VITE_ALLOWED_HOSTS` was fixed, the same first start ran out of the
container's 12 GB on every attempt, with the CPU-PyTorch image too: `vite build`
reached 5.9-6.4 GB while the chat model held 3.8 GB on the CPU and the backend and
workers about 2.5 GB. CUDA PyTorch itself adds about 0.36 GB to the backend
process (1.09 against 0.73 GB).

## Publishing

GitHub release assets must each be under 2 GiB (1000 assets per release, no limit
on the total). The build writes the format `desktop/platform/wsl/image.js` reads:
`guaardvark-wsl-<version>-x64.json` plus `guaardvark-wsl-<version>-x64.tar.gz.001`
to `.004`, each 1900 MiB or less, all attached to release `v<version>`. The app
fetches the manifest, downloads and checks each part (resuming partial parts),
checks the joined stream against the whole hash and streams it into
`wsl --import Guaardvark <dir> -`. Nothing has been published.

xz would save 1.27 GiB per download (20 %) for about 8 more minutes of build. It
needs the app to say `format: tar.xz`, and whether `wsl --import -` accepts xz,
or gzip, from stdin rather than from a named file is a Windows-PC check.

## `.wsl` files and `wsl --install --from-file`

Since WSL 2.4.4 (November 2024) a distribution can be shipped as a `.wsl` file: the
same gzip tar, renamed, with `/etc/wsl-distribution.conf` inside. It installs with
`wsl --install --from-file <file> --name <name> --location <dir>` or a double-click,
and WSL then runs the file's OOBE command and makes the Start-menu shortcut and
Windows Terminal profile it describes. Ubuntu's own image is one.

This image already carries `/etc/wsl-distribution.conf`, so renaming the
`.tar.gz` to `.wsl` makes it a valid `.wsl` file. For the desktop app, plain
`wsl --import` is still the better call:

- the app chooses the name and the folder explicitly (`--import` also reads the
  tarball from stdin with `-`, which could take joined download parts without a
  temporary file; whether stdin accepts a compressed stream is untested);
- it works on any WSL 2 the app may meet, where `--from-file` needs 2.4.4 or later;
- what `.wsl` adds (OOBE, shortcut, terminal profile) is switched off here on
  purpose, because the app owns setup and its own shortcut.

A `.wsl` file is worth publishing as well if people should be able to install the
distribution without the app by double-clicking it; then `[shortcut]` should be
turned on and the icon given. Microsoft's guide recommends gzip ("other
compression formats run the risk of breaking compatibility with older WSL
versions"); `wsl --import` has read `.tar.xz` for WSL 2 since WSL 0.61.4 (2022);
zstd appears in no WSL release note as an import format. `wsl --import --vhd`
takes a ready ext4 `.vhdx` and skips extraction, at the price of an uncompressed
download.

## What only a Windows PC can show

- The import itself (`wsl --import … --version 2`) and the first boot under WSL's
  own init: systemd coming up, `guaardvark-firstboot.service` running before
  PostgreSQL, the default user taking effect.
- Whether `wsl --import` honours `/etc/wsl-distribution.conf` (name, shortcut,
  terminal profile) or only `--install --from-file` does.
- The NVIDIA path: `nvidia-smi` through `/usr/lib/wsl/lib`, Ollama on the GPU,
  and `torch.cuda.is_available()` True for the image's `cu132` PyTorch on an
  RTX card (an RTX 5060 Ti is the case the build stands in for).
- Whether `wsl --import <name> <dir> -` reads a gzip (or xz) stream from stdin,
  which is how the app imports the joined parts.
- Windows interop pieces of start.sh (`--wsl-keep-alive`, the network note) and
  `localhost` forwarding of ports 5000 and 5173 to the Windows browser.
- Redis under the real systemd unit: in docker the unit's sandboxing fails
  ("Failed to set up user namespacing"), so start_redis.sh falls back to starting
  `redis-server` itself. That works, but it is not the WSL path.
- First-start and warm-start times on a real disk and CPU.

## Open points

- **First start downloads the models**, as install.sh does: hardware_policy picks
  the chat model by VRAM (`gemma4:12b` from about 12 GB, `qwen3.5:4b` below) plus
  `nomic-embed-text`. hardware_policy also counts RAM: 8 GB or less picks the
  small model whatever the card, and WSL gets half the PC's RAM by default, so a
  16 GB PC gets `qwen3.5:4b` even with a 16 GB card unless `.wslconfig` raises
  `memory=`.
- **Cards and drivers CUDA 13 does not cover** (Pascal, Volta, drivers before
  580) get a PyTorch that imports without CUDA. The app could check the Windows
  NVIDIA driver version before installing.
- **Two things seen in stock start scripts while building this** (not changed
  here; both affect any fresh WSL install, not only the image):
  - start_postgres.sh asks `systemctl is-active postgresql`, which is Ubuntu's
    wrapper unit and stays active when the real cluster unit
    (`postgresql@18-main`) has failed, so it can report "PostgreSQL service
    already running" over a cluster that is down. An earlier build of this image,
    whose first-boot unit failed and took PostgreSQL down with it, hit exactly
    that: "already running", then `Failed to create/update PostgreSQL user`.
  - On a stock WSL install, apt starts Redis without a password; start_redis.sh
    then writes a password into `/etc/redis/redis.conf` but `systemctl start` is a
    no-op on the running server, so `.env` gets the passwordless URL. After the
    next `wsl --shutdown` Redis boots with the password and `.env` no longer
    matches, and start_redis.sh moves to a passwordless broker on port 6380.
    This is read from the code; docker cannot run the unit to confirm it, so it
    needs a check on a Windows PC.
  - A stock WSL install also rebuilds the frontend whenever WSL's NAT address
    changes (see "One fixed setting" above). A core fix would leave
    `VITE_ALLOWED_HOSTS` out of the frontend build fingerprint in start.sh; it
    only governs the dev server's host check.
- `scripts/dep_reconciler` keys its state file by hostname
  (`data/dep_reconciler/state-guaardvark.json` in the image). Under WSL the
  hostname is the PC's name, so the first reconcile there (only plugin enables and
  venv repairs run it) starts from an empty state.
