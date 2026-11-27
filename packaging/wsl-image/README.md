# Guaardvark WSL image

`build.sh` makes a root filesystem that Windows imports as a ready WSL 2
distribution named `Guaardvark`:

```powershell
wsl --import Guaardvark "$env:LOCALAPPDATA\Guaardvark\wsl" guaardvark-wsl-main.tar.gz --version 2
wsl -d Guaardvark -- bash -lc "cd ~/guaardvark && ./start.sh"
```

The image is the Ubuntu that `wsl --install` gives today with Guaardvark already
installed by its own `install.sh`, so the first start skips the system packages,
Python, Node, the frontend build, PostgreSQL, Redis and Ollama. It carries no
model weights and no secrets.

## What is in it

| Part | Where it comes from |
|---|---|
| Base | `ubuntu-26.04.1-wsl-amd64.wsl` from releases.ubuntu.com, the file Microsoft's distribution manifest (`microsoft/WSL`, `distributions/DistributionInfo.json`) lists for the default `Ubuntu`. Its SHA-256 is pinned in `build.sh`; update the URL and hash together when the default moves. |
| Guaardvark | `install.sh` from the requested ref (`--ref`, a branch or tag), run as the default user with no terminal, exactly as `curl … \| bash` runs it, with `GUAARDVARK_BOOTSTRAP_MODELS=0` set for that run only. The checkout is `~/guaardvark` and its `origin` is the public repository. |
| User | `guaardvark`, uid 1000, the groups Ubuntu's own first-run script gives (`adm cdrom sudo dip plugdev`), no password. |
| `/etc/wsl.conf` | `[boot] systemd=true`, `[user] default=guaardvark`. |
| `/etc/wsl-distribution.conf` | `defaultUid=1000`, `defaultName=Guaardvark`, no OOBE command, WSL's own Start-menu shortcut and Windows Terminal profile off (the desktop app makes its own), icon from `--icon` when given. |

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
| Ollama tuning drop-in rendered for the build hardware | deleted | start.sh (rendered for this machine; Ollama picks it up on its next restart, as on a fresh install) |
| Saved chat model (`settings.active_model_name`, `data/active_model.json`) | deleted | backend, on first boot, for this machine |

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
packaging/wsl-image/build.sh --out DIR [--ref main] [--repo URL] \
    [--compress gz|xz|zst|all|none] [--icon desktop/build/icon.ico]
```

Everything runs in docker: the base file is checked against its hash, imported as
an image and booted with systemd as PID 1 (WSL with `systemd=true` does the same),
capped at `--memory 12g --memory-swap 12g --cpus 6`, with no published ports and
no GPU. systemd needs `CAP_SYS_ADMIN` and an unconfined AppArmor profile to mount
its own cgroup tree in a container; nothing else is widened. The root filesystem
is then taken with `docker export` and passed through `lib/rewrite_rootfs.py`,
which removes `/.dockerenv`, puts back the base image's own `/etc/hosts`,
`/etc/hostname` and `/etc/resolv.conf` (docker owns those in a running container),
and writes `/etc/machine-id` empty.

Output in `DIR`: `guaardvark-wsl-<ref>.tar` (+ `.gz` / `.xz` / `.zst`), a
`.sha256` file, and `logs/` with the install log, the health check, the timings and
`size-report.txt` (largest directories).

`verify.sh --tar FILE --out DIR` imports the result into docker, boots it the same
way and runs start.sh three times as the default user: first start, a second
start, and a start after a restart of the container (as `wsl --shutdown` and
reopening). It records timings, `/api/health` and the web UI's answer, and checks
that the first boot made its own keys and secrets.

## Measurements

Build of `main` at e18515f5 (3.7.0) on 2026-11-27, docker capped at 12 GB / 6 CPUs,
no GPU. Numbers from `logs/timings.txt`, `logs/size-report.txt` and `verify.sh`.

| Build step | Time |
|---|---|
| install.sh (system packages, Python 3.12 venv, Node, frontend build, PostgreSQL, Redis, Ollama, migrations, first start) | 7m42s to 9m18s over four runs |
| export + rewrite | 30-50 s |
| gzip (`pigz -9`, 6 threads) | 1m18s to 1m32s |
| xz (`-6`, 6 threads) | 6m03s |
| zstd (`-19`, 6 threads) | 6m02s |
| whole build with gzip only | 12m11s |

| Artifact | Size |
|---|---|
| `.tar` (uncompressed) | 8.40 GiB (9,022,003,200 bytes); 8.14 GiB of file data in 236,560 files |
| `.tar.gz` | 3.71 GiB (3,979,823,491 bytes) |
| `.tar.xz` | 2.85 GiB (3,057,428,872 bytes) |
| `.tar.zst` | 2.92 GiB (3,138,392,970 bytes) |

Largest parts (file data): the backend venv 2.78 GiB (PyTorch CPU 0.77 GiB,
llvmlite, playwright, transformers, scipy...), Ollama's libraries 2.45 GiB
(`cuda_v12` 1.4 GB and `cuda_v13` 1.1 GB, which Ollama needs on NVIDIA cards),
the rest of `/usr/lib` 1.23 GiB, the frontend 0.50 GiB (`node_modules` and
`dist`), apt package lists 0.15 GiB, PostgreSQL's data 0.07 GiB.

Booting the `.tar.gz` with `verify.sh` (docker import 56 s):

| Start | Time to "studio is up" (start.sh wall time) |
|---|---|
| First start on the "new machine" (secrets made, frontend rebuilt) | 92.7 s |
| Second start | 48.3 s |
| Start after a restart of the container | 48.3 s |

Every start ended with `/api/health` answering
`{"status":"ok","version":"3.7.0",...}` and the web UI answering
`HTTP 200 text/html` with `<title>guaardvark</title>`. The first start rebuilds
the frontend because the build bakes `VITE_ALLOWED_HOSTS` (the machine's LAN
address and hostname) into it and start.sh rebuilds when that changes; that is
most of the gap between the first and the later starts. With models pulled
(`GUAARDVARK_BOOTSTRAP_MODELS` left on) the first start adds the chat and
embedding model downloads.

## Publishing

GitHub release assets must each be under 2 GiB (1000 assets per release, no limit
on the total). No compression gets this image under the limit
(gzip 3.71 GiB, xz 2.85 GiB, zstd 2.92 GiB), and the large parts (PyTorch, Ollama's
CUDA libraries) are what a GPU install needs, so trimming will not get it there
either. Proposal, nothing published yet:

1. **GitHub release, split.** Publish the `.tar.gz` as numbered parts of about
   1.9 GiB (`split -b 1900M -d -a 2 guaardvark-wsl-X.tar.gz guaardvark-wsl-X.tar.gz.part`),
   plus a `SHA256SUMS` covering every part and the whole file, attached to the
   same release as the app. The app downloads the parts, checks each hash,
   joins them into one file, checks the whole hash, and runs `wsl --import`.
   It costs nothing, lives beside the code and the release notes, and needs no new
   account. Two parts for gzip, two for xz.
2. **xz instead of gzip** saves 0.86 GiB per download (23 %), costs 4.7 more
   minutes per build and a slower import (to be timed on Windows); `wsl --import`
   has read `.tar.xz` since 2022. Worth it if download size matters more than
   build time.
3. **Docker Hub**, where the project's images already are, could carry the rootfs
   as a single-layer image (no per-file limit), and the app would fetch the layer
   blob over the registry API. More moving parts than (1), and anonymous pulls are
   rate-limited per IP.

A separate download host (object storage, a CDN) would also work but costs money
and is a new outbound service, so it is not proposed here.

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
  and the backend's PyTorch (see below).
- Windows interop pieces of start.sh (`--wsl-keep-alive`, the network note) and
  `localhost` forwarding of ports 5000 and 5173 to the Windows browser.
- Redis under the real systemd unit: in docker the unit's sandboxing fails
  ("Failed to set up user namespacing"), so start_redis.sh falls back to starting
  `redis-server` itself. That works, but it is not the WSL path.
- First-start and warm-start times on a real disk and CPU.

## Open points

- **PyTorch in the backend venv is the CPU build.** start.sh picks the torch
  channel from the hardware it runs on, and the build has no GPU. On an NVIDIA PC
  the venv is already "healthy", so start.sh does not swap it; the preflight only
  warns ("torch imports but CUDA is unavailable"). Ollama and the ComfyUI plugin
  (its own venv) are not affected. Options: run `scripts/install_pytorch.sh` on the
  first start when `nvidia-smi` works and torch has no CUDA (several GB of CUDA
  wheels then), or publish a second, CUDA-torch image for NVIDIA PCs.
- **First start pulls models unless told not to.** With Ollama up and no chat or
  embedding model installed, start.sh pulls the hardware tier's chat model and
  `nomic-embed-text`. If models are to come only from the app's Install buttons,
  the app should run start.sh with `GUAARDVARK_BOOTSTRAP_MODELS=0`; start.sh then
  says "Chat cannot answer yet: no chat model is installed" and the UI runs
  without chat until one is installed.
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
- `scripts/dep_reconciler` keys its state file by hostname
  (`data/dep_reconciler/state-guaardvark.json` in the image). Under WSL the
  hostname is the PC's name, so the first reconcile there (only plugin enables and
  venv repairs run it) starts from an empty state.
