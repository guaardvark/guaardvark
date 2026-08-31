# Changelog

## Unreleased

- `start_postgres.sh` takes the role, database, host and port from `DATABASE_URL` and never
  re-provisions a role it did not create; before this a fork with its own role on the same
  machine had its password reset and its URL rewritten to the stock database. `start.sh` and
  the agent display kill only a port's listener, not a process holding a client socket to it.
  The DSN is logged with its password masked. ComfyUI's liveness probe tolerates ~2 minutes of
  silence while a 20 GB+ model loads on a 16 GB card (measured downstream), instead of 20 s.
- **Profiles.** One switch sets the product shape: `GUAARDVARK_PROFILE=<name>` in `.env` or
  `./start.sh --profile <name>`. `workstation` is today's product and sets nothing;
  `creator` is the media workflow (image, video, audio, Film Crew, LoRA, upscaling) with the
  agent, knowledge-index, outreach and automation subsystems left installed but unlisted and
  off by default; an extension can ship its own. An explicit `.env` value, flag, plugin toggle
  or DB setting always wins over a profile, and hidden means unlisted, never removed. See
  `backend/profiles/README.md`. The sidebar lists what the profile lists, `/` lands where it
  says, Settings → Product Profile switches profiles (applies on restart), and a fresh
  install asks once — Creator or Workstation — before anything else.
- **Extensions.** A client vertical lives in `extensions/<id>/` — blueprints, models, Celery
  tasks, column migrations, seeds, a profile, an optional sidecar plugin — and core loads it
  through fixed hook points without any core file naming it. A broken extension is reported
  by id while the others still load, and a declared URL prefix with no mounted route is an
  error rather than a silent 404. Extensions register handlers for their own task types
  instead of editing the unified task executor. `extensions/_template/` is the starting point;
  see `extensions/README.md`. On the frontend, `extensions/<id>/frontend/index.jsx` contributes
  routes, nav groups, themes, page context, chat surfaces, store state, a header bar and a
  logo; core merges them at build time and imports for the extension resolve through core's
  dependencies and the `@` alias.
- Settings → Maintenance gains **Delete History**, next to Clear Cache: removes every
  batch-image, batch-video and audio generation — the media directories and files, their
  `documents`/`folders` rows and `job_history` entries — and logs each purge to
  `retention_audit`. Batches still generating are skipped. Film Crew productions, video
  editor projects, the cast library and LoRAs, and chat history are not touched. The audio
  sidecar gains `DELETE /jobs` so its in-memory job list and its `.jobs` files stay in step.

## 2.8.0 — MiniMax H3, a rebuilt knowledge index, and a cleaner clean install

367 commits since 2.7.0. The largest single change is the knowledge index, which was
rebuilt from the storage layout up and needs one re-index (see the note below). Around
it: three new video model families, an overnight self-improvement director, Discord
through the same chat engine as the UI, a privacy audit of every path that could reach
the network, a platform layer with macOS in CI, and the clean-install bugs a tester
found on a fresh Windows 11 / WSL2 box.

**This release requires a full re-index of your knowledge base.** Existing vectors were
built with different chunking and are not migrated. Nothing is lost — your documents are
the source of truth and are re-read from disk — but plan for the corpus to be
unavailable while it rebuilds. See *Upgrading the knowledge index* below.

### Clean-install fixes

All three were reported against a fresh install on 2026-08-29 and all three were real:

- **Film Crew failed with `model 'gemma4:e4b' not found`.** The installer's hardware
  policy pulls `gemma4:e2b` on most machines; the swarm agents hard-coded `e4b`. A chat
  model name is now a preference resolved against what Ollama actually has — same
  family first, then the saved active model, then the policy's tier model
  (`backend/services/ollama_chat_model.py`).
- **pgvector was never installed.** `start_postgres.sh` provisioned PostgreSQL but not
  the `vector` extension the index stores into, and enabling it needs a superuser the
  app role is not. Provisioning now installs `postgresql-<major>-pgvector` (Homebrew
  `pgvector` on macOS) and runs `CREATE EXTENSION`; existing installs get it on the next
  start, with one sudo prompt.
- **LoRA training stopped at `No module named 'peft'`.** Z-Image training runs in the
  backend venv, which never listed it. It does now.

### Video generation

- **MiniMax H3** — download plan in the video model registry and generation through
  ComfyUI. It fits a 16 GB card at the template's settings; see *Known limitations*
  for the speed caveat.
- **LTX-2.5 distilled** as a local ComfyUI family, with I2V/T2V aligned to the official
  pipeline for identity preservation, a preflight file check, and the audio VAE
  registered where the loader actually looks.
- **HunyuanVideo 13B** T2V/I2V (GGUF Q5_K_M) in the downloader and generator.
- **Wan 2.2** — quality presets can no longer hand Wan a step count it cannot render
  (`minSteps` on the model entry, measured against the smearing that 10 steps produced);
  the 14B's trained sampler shift is fixed at 8.0 by default instead of scaled by pixel
  area; 1:1 is back; a sampler profile toggle for the 5B (adaptive euler or official
  uni_pc); 24 fps presets and a Motion preset that reaches the model; guidance comes
  from the model's own workflow.
- **Live latent preview** while ComfyUI renders.
- Long renders survive: VRAM-wait admission, staged progress, a real start budget for
  the ComfyUI launcher with a loud fallback, and a ComfyUI interrupt scoped to the
  prompts we queued rather than everything in its queue.
- A per-family pixel-area clamp prevents hangs; an unsupported aspect ratio is clamped
  server-side as well as in the UI.
- The chosen model animates its own keyframe; a failed director pass no longer switches
  the prompt enhancer off.
- Video from inline chat (`generate_video` tool), a fullscreen player with prev/next,
  and a VLM temporal-quality reviewer (MiniCPM-V 4.5).

### Images and the media workspace

- One tabbed media workspace, a route per tab.
- **Image upscaling**, single and batch.
- Batch images: clear completed batches from the queue, Adjust & Retry no longer
  multiplies the prompt by the quantity, prompt auto-detect no longer silently
  overrides steps (and has a toggle), Z-Image keeps its CFG-free defaults.
- The hidden SD-1.5 fallback is gone; img2img goes through the same guards as txt2img;
  large canvases no longer take the desktop down with them; the seed generator builds
  on CPU when CUDA cannot initialise.
- Documents: opt-in media gallery, fast streaming PDF viewing and an in-app DOCX viewer.

### Knowledge index

Vectors moved to pgvector, hybrid search is back, and ingest is roughly an order of
magnitude faster. Measured on the same machine and the same model, ingesting the same
corpus:

Measured on the same machine and the same model, ingesting the same corpus:

| | before | after |
|---|---|---|
| Fixed cost per document | ~3.5 s | **0.02–0.07 s** |
| Characters embedded per chunk | ~2,230 | **~800** |
| A 151 KB document | 19.3 s | **6.1 s** |
| A 2 KB note | 9.3 s | **0.1 s** |

Four things account for most of it:

- **Every chunk was being embedded twice.** The text kept for citations was stored in
  metadata, and metadata is concatenated ahead of the chunk before embedding — so each
  chunk was sent to the model as both its text and its own metadata. Roughly half of all
  embedding work was duplication.
- **Chunks are sized by what is embedded**, not by what they carry. Chunk size is
  computed as `size - len(metadata)`, and that metadata is now excluded before splitting
  rather than after, so a document with a long path and tags no longer loses most of its
  chunk to text it was never going to embed.
- **The index no longer rewrites itself on every document.** It kept a JSON copy of every
  node and rewrote the whole file each time a document was added, so adding one document
  got slower as the corpus grew. That file is gone.
- **Garbage collection is amortised** rather than run twice per document. In a process
  holding the ML stack a full collection costs ~250 ms, which on a small file exceeded
  parsing, chunking and embedding combined.

Ingest cost is now flat in corpus size: one constant pair of coefficients predicts it
across a corpus growing from 0 to 703 documents and 36,000 chunks.

#### Keyword search moved into PostgreSQL

The keyword half of hybrid search now queries the full-text index PostgreSQL was already
maintaining, instead of an in-memory index rebuilt from a JSON file. Retrieval behaviour
is unchanged in shape — same fusion, same adaptive weighting, same reranking — but it no
longer depends on a file that had to be rewritten constantly, and it can filter by project
in SQL rather than after the fact.

Ranking is tuned for how people actually search. Rare words now decide a query: asking for
a specific name, identifier or error code puts the passage containing it first, instead of
letting common words in the rest of the question outvote it.

#### Also fixed in the index

- **Client, project and job metadata was never indexed.** Both metadata indexers passed
  their metadata in a form the indexing layer rejects, so every attempt failed and logged a
  message that read like a transient problem. They now work.
- **Uploads went through a lesser pipeline than everything else.** Files uploaded through
  the UI were read as plain text — a PDF or DOCX arrived as mojibake, markdown was never
  sectioned, and re-indexing appended a second copy instead of replacing the first.
- **Re-indexing generated text left the old copy behind.** A repository summary, client
  profile or extracted relationship stayed in the index after being regenerated, competing
  with the current version at query time.
- Re-indexing a document is no longer slower on a large corpus than a small one.
- Documents with no headings no longer explode into tens of thousands of fragments.
- Audio and video files are no longer fed to a text reader. They are not yet indexed;
  they are simply left alone until transcription lands.

- Deleting a document now actually removes it from the knowledge base; documents left
  PENDING by an interrupted run are requeued; auto-resume defers to an Ollama outage
  instead of condemning documents to it.
- Corpus sensemaking and a document navigation surface; index profiles (one registry,
  several derived projections); a staged document archive with filter, dedup and
  chronology; a `docling` dependency declared so PDF and Word files can be indexed at all.
- The knowledge tools work inside the MCP subprocess.

#### Upgrading the knowledge index

1. Back up if you want a fallback: `pg_dump` your database, and keep `data/docstore.json`
   until you are satisfied.
2. Upgrade and restart.
3. Re-index. The knowledge base rebuilds from your documents; the background catch-up job
   will work through them on its own, or drive it directly for a bulk rebuild.
4. `data/docstore.json` and `data/index_store.json` are no longer used and can be deleted
   once the rebuild finishes.

If you run PostgreSQL with default memory settings, `scripts/tune_postgres_for_rag.sh`
raises the two that matter for a vector index of any size. It prints what it would change
with `--dry-run` and needs root only to apply.

### Chat

- The floating chat keeps its thread across a refresh and names the page it is
  looking at; a distribution can declare which pages are chat surfaces.
- Pluggable per-turn context providers, and a knowledge-source registry on the RAG
  retrieval step; facts supplied by a provider are not treated as a web-search question.
- Markdown tables render as tables; a pasted description no longer triggers image
  generation; explicit "remember this" intents are captured.
- Rule bundles apply by name (`flask load-rules`), and Guaardvark ships its own voice as
  one; lesson bundles load into agent memories.
- The Ollama context window is bounded on every call that was leaving it unset.
- `list_documents` joins the core tools.

### Self-improvement and autoresearch

- Autoresearch rebuilt as a research system: live parameters, honest evals, bounded
  overnight runs with two-fidelity evaluation, a real kill switch.
- One overnight director schedules retrieval tuning, code tuning and Auto Improve.
- Proposals and research runs bind to the saved active model in the worker; a directed
  run only counts as a success when it staged a fix; bulk approve/reject/apply in the
  fixes dialog with progress and a result banner.

### Film Crew and training

- A `training_director` engine for procedure-guide videos: Kokoro or a cloned narrator,
  per-shot control over whether people appear, image-to-video that actually renders.
- Before Create, the LoRA trainer says whether the base model will be downloaded.

### Discord

- `/ask`, channel chat and voice all go through the unified chat engine, so the bot
  answers the way the UI does; the bot listens and talks in voice channels; `/video`
  delivers the finished clip to the channel.

### Privacy and security

- **Generation never leaves the machine**: backend model loads read local registry
  files only and never reach Hugging Face on their own; downloads happen only behind
  an explicit Install.
- ComfyUI binds to loopback and stops pinning host memory.
- Exact host matching and an explicit URL regex in the scout; an arithmetic-only
  calculator tool; batch-image names resolved through `safe_join`; cast-library deletes
  and other destructive media and GPU routes require localhost or the API key.
- Pillow floor raised to 12.3.0 and torch stopped silently undoing it;
  `socket.io-parser` overridden to 4.2.7; the Dependabot queue cleared again (av 18,
  safetensors 0.8, pydantic 2.13, psutil 7, typer 0.27, and the rest).
- CodeQL runs on every push; pull requests need a signed CLA; a pre-commit, commit-msg
  and pre-push guard keeps machine-specific content out of the repository.

### GPU and resources

- Resident models are reclaimable, so admission can free VRAM instead of refusing.
- Cross-process training leases; heartbeat leases under `flock`; eviction only when it
  helps; the GPU power limit is left alone by default.
- The job reaper tolerates ComfyUI probe blips and never steals a live worker's gate.
- The default image model is no longer refused on 32 GB machines, and a RAM-gate
  refusal now says "system RAM (not VRAM)".

### Install, platform and operations

- A curl-able bootstrap installer: `curl -fsSL https://guaardvark.com/install.sh | bash`.
- A platform layer (`scripts/platform/`) that says in one place what this machine can
  do, with macOS in CI; macOS honours the ComfyUI port override, finds a font, and names
  the reconciler that failed.
- Fresh Ubuntu 24.04 installs: Python dev headers before pip, the setuptools pin that
  made PyTorch uninstallable is gone, PyTorch wheels are staged before the working ones
  are removed, the Node major version is verified, the Postgres sudo gate no longer
  needs a tty, and bootstrap survives tmpfs `ENOSPC`, half-installed venvs and
  proxy-only boxes.
- `scripts/heal_backend_venv.sh` re-runs every reconciler the failure sentinel names,
  not only the venv ones (#41).
- Plugins ship disabled by default, with Ollama the exception; `plugin.local.json`
  per-install overrides survive updates; plugin config actually saves; restart waits out
  the cooldown instead of failing half-way.
- Backups dump only the database and install the app is using, and stop following
  plugin symlinks out of the project.
- Redis broker URL and Celery beat heal on stop/start; an abandoned `start.sh` is reaped
  without killing a new boot; the Intel e1000e NIC hang is detected and mitigated before
  heavy downloads.

### Interconnector and connections

- Stable, IP-independent node identity and a server-side client heartbeat daemon.
- Outbound connections with a credential store, publishing, and a publish approval queue.

### MCP and CLI

- `python -m backend.mcp install` writes the server entry into the configs of the agent
  clients it detects; `python -m backend.mcp doctor` self-tests the server and flags stale
  client configs. The server is on the 2.x MCP SDK.
- `/abort` for a wedged chat and stream timeouts that end one; offline recipe
  inspection commands (`recipes list / show / validate`).
- The CLI test suite runs in CI; the `release` workflow publishes to PyPI on tag and
  refuses a tag that disagrees with `VERSION` or a version PyPI already has.

### Desktop agent

- DOM-based verification fast path (fixes 60 s task-timeout blowouts); a window
  fast path for launch gates; the DOM element inventory feeds the decision prompt.
- Servo calibration restored with a live Gemma4 fit; truth injection and
  validation-gated calibration in the learning stack.
- Basic browser-navigation recipes and a YouTube-comment recipe.

### Documentation

- `docs/HARDWARE.md` — what runs CPU-only, on 8–12 GB, on the 16 GB target, and beyond.
- `AGENT_MENTAL_MODEL.md` — chat versus agent versus Swarm.
- The README repositioned around the whole platform, with the walkthrough series;
  `CONTRIBUTING.md` carries the comment and portability standards.
- The knowledge index documented, with the claims that were aspirational corrected.

### Known limitations

- Python 3.12 only; the ML wheels for 3.13/3.14 are still missing upstream.
- MiniMax H3 on a 16 GB card is correct but slow: it runs at the template's 20 steps
  with no distilled speed LoRA and no SageAttention path, and a tester measured about
  12 minutes for a 3 s clip on an RTX 5060 Ti. Both accelerations are tracked for the
  next release.
- The Creator profile (#114) — the media-creation workflow with the agent, index,
  outreach and automation subsystems one setting away — is designed and not yet built.
