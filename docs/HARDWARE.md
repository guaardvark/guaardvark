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
| **B — Entry GPU** | 8–12 GB | Everything above, plus vector RAG, screen agents, SD/SDXL-class image generation, upscaling, and (at 12 GB) music generation. Video generation is refused — the preflight requires a 16 GB-class card. |
| **C — Design target** | 16 GB | The tier Guaardvark is built and tested on. The full stack: FLUX-class images, Wan 2.2 / LTX / CogVideoX video, music + neural voice, Film Crew, music videos — one heavy job at a time. |
| **D — Headroom** | 20–24 GB+ | Concurrency and quality upgrades: two resident LLMs, heavier image models preferred automatically, text encoders stay on-GPU, larger agent step budgets, full-batch LoRA training. |

A single NVIDIA (CUDA) GPU is the primary target. AMD and Apple Silicon support
is partial — see the sections at the end.

The canonical question — **"can I run this on a 12 GB card?"** — has a clean
answer: yes for chat, RAG, screen agents, SD/SDXL images, upscaling, music, and
voice; no for video generation, whose preflight checks total VRAM against a
16 GB minimum for the Wan / LTX / CogVideoX families and refuses below it
(with ~0.5 GB of grace, so "16 GB" cards that report 15.9 GB pass).

On first run, `./start.sh` detects your hardware (written to
`~/.guaardvark/hardware.json`) and derives the PyTorch build, Ollama server
tuning, and default models from it — see
`backend/services/hardware_policy.py`. You don't pick a tier; the system adapts.

## Tier A — CPU-only

What works, and how the system adapts:

- **Chat + RAG.** Ollama runs the default models on CPU. Machines with ≤ 8 GB
  RAM (or any ARM machine) get a small text-only tier (`llama3.2:1b` +
  `nomic-embed-text`); everything else gets `gemma4:e2b` (5.1B, ~7.2 GB
  download, vision-capable). With no GPU, models are kept resident in RAM
  instead of being unloaded, so you pay the load cost once.
- **Retrieval degrades honestly.** Advanced (vector) RAG defaults off on
  CPU-only hosts; retrieval falls back to BM25 keyword search under memory
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
- **Video generation is gated off.** The preflight requires a 16 GB-class card
  for every current video family (Wan 2.2 including the 5B, LTX-2.3/2.5,
  CogVideoX-5B). The Low VRAM preset reduces frames/resolution/steps but does
  not bypass this gate — it exists to keep 16 GB cards inside budget.

## Tier C — the 16 GB design target

16 GB is the tier the project is engineered against ("the 16 GB ceiling is the
design target" — `hardware_policy.py`), and where everything has been tuned to
coexist:

- Wan 2.2 TI2V-5B, the default video model, was built for 16 GB cards; the
  LTX-2.3 FP8 and LTX-2.5 int8 variants specifically target 16 GB
  Ada-generation cards.
- Image pipelines automatically enable sequential CPU offload on cards
  ≤ 18 GB; Wan's text encoders are placed on the CPU on cards ≤ 20 GB. Both
  are automatic — no settings to find.
- Film Crew and music-video renders claim ~14 GB exclusively and evict the
  chat model and ComfyUI caches before starting (see the contention section).
- Expect **one heavy job at a time**: chat plus a render works because the
  render evicts chat and it reloads after; two renders do not overlap.

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

- **≤ 8 GB RAM (or ARM):** the bootstrap installs the small text-only model
  tier — the ~7.2 GB Gemma4 download doesn't fit.
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
- **Conservative default:** ARM machines currently get the small
  `llama3.2:1b` tier regardless of unified memory — pull a larger model
  manually if your machine can hold it.
- **Experimental:** the offline (diffusers) video path has an MPS branch with
  tentative, advisory limits — untested on real Apple hardware.
- **Not available:** the ComfyUI video pipeline (requires NVIDIA), the offline
  image generator's GPU path (CUDA-only today), and the agent virtual desktop
  (X11-only: Xvfb + XFCE + x11vnc).

## AMD (ROCm)

Best-effort and unverified on real hardware: detection installs the ROCm
PyTorch build and reads VRAM via `rocm-smi`, but several GPU-presence checks
are NVIDIA-only, so generation paths should be considered NVIDIA-first for
now. If you run Guaardvark on an AMD card, an issue report with logs would
genuinely help.

## Where these numbers live

For contributors updating this guide: hardware detection and policy are in
`backend/services/hardware_policy.py` and `hardware_detector.py`; per-model
VRAM budgets in `backend/services/video_model_registry.py`; the video
preflight minimums in `backend/services/comfyui_video_generator.py`
(`MODEL_MIN_VRAM_GB`); arbitration in
`backend/services/gpu_memory_orchestrator.py` and `gpu_resource_policy.py`;
plugin budgets in each `plugins/*/plugin.json` (`vram_estimate_mb`).

See also: [README Requirements](../README.md#requirements) ·
[CAPABILITIES.md](../CAPABILITIES.md) · [ARCHITECTURE.md](ARCHITECTURE.md)
