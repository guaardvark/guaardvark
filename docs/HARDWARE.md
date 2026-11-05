# Hardware Guide

Which Guaardvark features actually run on your machine, tier by tier.

Every number below comes from the code: the VRAM budgets the GPU orchestrator
debits per job, the preflight gates that refuse work, and the download sizes in
the model registry. Treat them as design targets and calibrated estimates — not
formal benchmarks (a measured benchmark suite is a planned follow-up). Where
something is untested we say so and under-promise.

## The short answer

| Tier | GPU VRAM | What to expect |
|------|----------|----------------|
| **A — CPU-only / no NVIDIA GPU** | none | Chat + RAG (keyword-first retrieval), voice in and out, the parallel coding-agent Swarm, the video editor, MCP. No image, video, or music generation; no upscaling. |
| **B — Entry GPU** | 8–12 GB | Everything above, plus vector RAG, screen agents, SD/SDXL-class image generation, upscaling, and (at 12 GB) music generation. Video: at 12 GB the Wan 2.2 5B passes the preflight (its floor is 11 GB); every other video family needs 16 GB, and 8 GB cards get no video. |
| **C — Design target** | 16 GB | The whole studio: every image model, every video model except the two larger MiniMax H3 builds (24 GB and 48 GB), music, sound effects and voices, LoRA training, Film Crew, music videos. |
| **D — Headroom** | 20–24 GB+ | Concurrency and quality upgrades: two resident LLMs, heavier image models preferred automatically, text encoders stay on-GPU, larger agent step budgets, full-batch LoRA training. |

A single NVIDIA (CUDA) GPU is the primary target. AMD and Apple Silicon support
is partial — see the sections at the end.

The canonical question — **"can I run this on a 12 GB card?"** — has a clean
answer: yes for chat, RAG, screen agents, SD/SDXL images, upscaling, music, and
voice; for video, only the Wan 2.2 5B. The video preflight checks total VRAM
against each model's floor, with ~0.5 GB of grace: 11 GB for the Wan 2.2 5B,
16 GB for the Wan 14B, LTX, Hunyuan, CogVideoX and MiniMax models (so "16 GB"
cards that report 15.9 GB pass). A full 5B render on a 12 GB card has not been
confirmed yet.

On first run, `./start.sh` detects your hardware (written to
`~/.guaardvark/hardware.json`) and derives the PyTorch build, Ollama server
tuning, and default models from it — see
`backend/services/hardware_policy.py`. You don't pick a tier; the system adapts.

## Tier A — CPU-only

What works, and how the system adapts:

- **Chat + RAG.** Ollama runs the default models on CPU. Without a GPU that
  has room for `gemma4:12b`, a machine gets `qwen3.5:4b` (3.3 GB download,
  reads images too), or `qwen3.5:2b` with 5 GB of RAM or less (a 4 GB
  Raspberry Pi), plus `nomic-embed-text`. With no GPU, models are kept resident
  in RAM instead of being unloaded, so you pay the load cost once.
- **Retrieval degrades honestly.** Advanced (vector) RAG defaults off on
  CPU-only hosts; retrieval falls back to keyword-only search under memory
  pressure rather than thrashing.
- **Voice.** Speech-to-text (whisper.cpp) is a CPU build. Kokoro TTS is
  near-realtime on CPU; Chatterbox TTS works on CPU but is slow.
- **Coding agents.** The Swarm Orchestrator (parallel coding agents in git
  worktrees) declares zero VRAM and no GPU requirement.
- **Not available:** image, video, and music generation, and upscaling — all
  require an accelerator. The video pipeline refuses outright with
  "GPU required for video generation" when no NVIDIA GPU is detected.
- **Screen agents: not validated on CPU.** There is no hard GPU gate on the
  agent path, but every See-Think-Act iteration is a vision-model inference;
  on CPU expect it to be far too slow to be useful. Treat agents as GPU
  territory.

## Tier B — entry GPU (8–12 GB)

- **Chat, RAG, and vision** are comfortable; the chat pipeline budgets ~8 GB
  for the resident LLM.
- **Screen agents work**, with a reduced step budget: below 16 GB VRAM an agent
  task gets 10 steps instead of 20.
- **Image generation** is realistic with SD (~4 GB working estimate) and
  SDXL-class (~8 GB) models. The heavier paths (FLUX, Z-Image) budget
  11–12 GB — tight-to-refused on a 12 GB card, refused on 8 GB.
- **Upscaling** runs fine (~1.5 GB, tiled to avoid OOM).
- **Music generation** (ACE-Step, ~10 GB, runs exclusively) fits on 12 GB —
  the orchestrator evicts everything else first — but not on 8 GB. FX
  generation (~6 GB) and TTS (≤ 2 GB) are fine on both.
- **Video generation is gated to one model.** The Wan 2.2 TI2V-5B declares an
  11 GB floor, so a 12 GB card passes its preflight and the automatic pick
  chooses it (the pick wants the model's ~11 GB plus 1 GB of margin; an 11 GB
  card can select it by hand). A full render on 12 GB has not been confirmed
  yet. Every other family (Wan 2.2 14B, LTX-2.3/2.5, Hunyuan, CogVideoX-5B,
  MiniMax) requires a 16 GB-class card, and 8 GB cards get no video. The Low
  VRAM preset reduces frames/resolution/steps but does not bypass this gate —
  it exists to keep 16 GB cards inside budget.

## Tier C — the 16 GB design target

One 16 GB card runs the whole studio ("the 16 GB ceiling is the design target" —
`hardware_policy.py`):

- Every image model: Z-Image Turbo, FLUX.1, FLUX 3 Klein, Krea 2, Qwen-Image 2.1,
  MOSS-Image and SDXL, and the Qwen-Image-Edit, FLUX Kontext and MOSS-Image
  editors. Each needs 8–14 GB (`video_model_registry.py`, `media_model_registry.py`).
- Every video model except the two larger MiniMax H3 builds (Int8 unpruned for
  24 GB, BF16 for 48 GB): Wan 2.2 14B and 5B, LTX-2.3 FP8, LTX-2.5 Int8,
  HunyuanVideo 13B, CogVideoX-5B, and MiniMax H3 Int8 and H3 Reference Int8 with
  their own soundtrack. LTX-2.3 FP8 and LTX-2.5 Int8 were made for 16 GB
  Ada-generation cards.
- Image pipelines switch on sequential CPU offload by themselves on cards
  ≤ 18 GB, and Wan's text encoders sit on the CPU on cards ≤ 20 GB. No settings
  to find.
- The GPU orchestrator gives each render the card it needs: Film Crew and
  music-video renders claim ~14 GB and move the chat model and ComfyUI caches
  out first, chat reloads afterwards, and the next render starts as soon as the
  card is free (see the contention section).

Everything Guaardvark does runs on that one card: Video Generation, Image Generation, Image Editing, Infographics, Upscaling (4K/8K), Music Generation, Sound Effects, Text to Speech, Voice Cloning, Voice Chat, RAG (Chat With Your Files), Local Chat, Plan Mode, Film Crew Agents, Music Video Director, Video Editor, LoRA Training, Cast Library (Consistent Characters), Agent Screen, Desktop Control, Coding Agents (Swarm), Code Editor, System Map, Self-Improvement, Autoresearch, MCP Server and Client, Agent Skills, Claude Code, Grok and Antigravity Plugins, CLI, GPU Orchestrator, Plugin Manager, Multi-Machine Sync, File Manager, Projects, Clients and Websites, Notes, Jobs and Scheduling, Backups, Social Posting, WordPress, Discord Bot, Inbound Code Guard, Works Offline, 12 Interface Languages.

## Tier D — headroom (20–24 GB+)

- **≥ 20 GB:** Ollama serves 2 parallel requests and keeps 2 models loaded
  (below this, 1 and 1); the heavier Krea2 model becomes the preferred
  automatic image model; Wan's text encoders stay on the GPU.
- **> 24 GB:** agent tasks get a 30-step budget (vs 20 at 16 GB); LoRA
  training runs at its top configuration (batch 4, 4096 sequence length, no
  CPU offload — the 24 GB-class ladder in
  `backend/services/hardware_service.py`).
- CogVideoX-5B is happiest here (20 GB recommended).

## One GPU, many models — contention is managed, not free

Running Ollama, ComfyUI, and audio backends against one card only works
because a set of arbitration layers coordinates them
(`backend/services/gpu_resource_policy.py` enumerates them):

- **GPU Memory Orchestrator** — every heavy job debits a VRAM budget before
  touching the card (images ~8–12 GB, video ~14 GB, music ~10 GB exclusive)
  and is refused or queued if it doesn't fit, with a ~10% safety margin.
- **Job gate + cross-process lock** — heavy renders are exclusive: they evict
  the resident chat model (it reloads afterwards) and free ComfyUI's caches.
- **RAM admission gate** — system RAM and swap are checked before big loads.

Practical consequences: a resident chat model and a video render cannot share
a 16 GB card, so the system doesn't try — evict-and-reload is the designed
behavior, not a bug. Your desktop compositor also permanently holds
~600–800 MB of VRAM, which the budgets account for.

## System RAM, swap, and disk

- **≤ 8 GB RAM, or ARM without an NVIDIA or Apple GPU:** the bootstrap
  installs `qwen3.5:4b` (`qwen3.5:2b` at 5 GB or less). An ARM machine with an
  NVIDIA GPU (GB10 / DGX Spark, Jetson) and more than 8 GB is judged by its GPU
  memory like any other: `gemma4:12b` when it has room (12 GB of GPU memory, or
  16 GB on a Mac), `qwen3.5:4b` when it does not.
- **Image generation uses serious system RAM** when CPU offload kicks in:
  calibrated figures range from ~10 GB (SDXL) to ~21–24 GB (Z-Image / Krea2)
  of resident memory per job. 32 GB of system RAM is a sensible floor for the
  generation stack; 64 GB is comfortable.
- **Big GPU, small RAM (cloud shapes):** the system-RAM gate applies no matter
  how large the card is. Z-Image and Krea2 always run with CPU offload, so
  their weights live in system RAM between stages even on a 24 GB GPU. A
  16 GB-RAM VM such as Google Cloud's `g2-standard-4` (one 24 GB L4) will
  refuse them with `system RAM too low (not VRAM)`; the 32 GB `g2-standard-8`
  admits them. On 16 GB of RAM, pick SDXL or SD 1.5 explicitly — Auto resolves
  to Z-Image. Swap does not count toward the gate.
- **Swap:** ≥ 16 GB swap with low swappiness is recommended so a spike
  degrades instead of freezing the desktop — setup commands are in
  [INSTALL.md](../INSTALL.md).
- **Disk:** generation models are multi-GB downloads (Wan 2.2 5B ~9.5 GB,
  FLUX ~12.6 GB, CogVideoX ~11 GB, LTX-2.5 ~20 GB plus companion files).
  Plan on tens of GB free for a full video stack, plus ~8 GB of headroom for
  the CUDA Python stack itself.

## Apple Silicon (macOS)

Partial, actively improving — tracked in
[#43](https://github.com/guaardvark/guaardvark/issues/43).

- **Works:** chat, RAG, and voice through Ollama (which uses Metal on its
  own); the install and startup path; the video editor (with `melt`/Shotcut
  installed).
- **Verified on Apple Silicon:** offline image generation for the Z-Image
  and Krea 2 families runs natively on Metal (MPS; #183, live renders), and
  LoRA training runs on MPS (#182; slow, with timeouts raised to match).
- **Default chat model by memory:** a Mac with 16 GB or more gets
  `gemma4:12b`; one with less gets `qwen3.5:4b`.
- **Not verified by this project:** video generation through ComfyUI on
  Metal (no render on a Mac is on file); the other offline image families
  on MPS; the offline (diffusers) video path, which has an MPS branch with
  tentative, advisory limits untested on real Apple hardware.
- **Not available:** the agent virtual desktop (X11-only: Xvfb + XFCE +
  x11vnc).

## AMD (ROCm)

Best-effort and unverified on real hardware: detection installs the ROCm
PyTorch build and reads VRAM via `rocm-smi`, but several GPU-presence checks
are NVIDIA-only, so generation paths should be considered NVIDIA-first for
now. If you run Guaardvark on an AMD card, an issue report with logs would
genuinely help.

With an integrated GPU next to a discrete AMD card, both are listed as GPUs. The
VRAM checks (load gate and hardware profile) use the card with the most VRAM. To
make PyTorch use only the discrete card, set `HIP_VISIBLE_DEVICES=0` in the
environment of the backend (for example `Environment=HIP_VISIBLE_DEVICES=0` in a
container unit). The numbering can differ between `rocm-smi` and PyTorch;
`torch.cuda.get_device_properties(0)` shows which card is device 0.

Checked on an RX 7900 XTX with a Radeon 780M iGPU: `torch.cuda.device_count()` is
2, and 1 with `HIP_VISIBLE_DEVICES=0`.

## Where these numbers live

For contributors updating this guide: hardware detection and policy are in
`backend/services/hardware_policy.py` and `hardware_detector.py`; per-model
VRAM budgets and the video preflight floors (`vram_mb` per model and
`min_vram_gb` per family) in `backend/services/video_model_registry.py`; arbitration in
`backend/services/gpu_memory_orchestrator.py` and `gpu_resource_policy.py`;
plugin budgets in each `plugins/*/plugin.json` (`vram_estimate_mb`).

See also: [README Requirements](../README.md#requirements) ·
[CAPABILITIES.md](../CAPABILITIES.md) · [ARCHITECTURE.md](ARCHITECTURE.md)
