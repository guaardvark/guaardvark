# Architecture & Setup Guide

This file provides an architecture overview plus setup/build/test guidance for the project — useful for both human contributors and AI coding agents (e.g. Claude Code).

## What this is

Guaardvark is a self-hosted, offline-first AI workstation: a Flask backend, a React/Vite frontend, a Python CLI (`llx`), and 10+ GPU service plugins. Everything runs locally by default. The exhaustive feature/model/tool enumeration lives in `CAPABILITIES.md`; the marquee overview is in `README.md`.

## Running & developing

`./start.sh` is the single entry point. On first run it provisions everything: PostgreSQL, Redis, the Python venv (`backend/venv`), Node modules, schema sync, frontend build, and all services (Flask API on `:5000`, Vite UI on `:5173`). Useful flags: `--test` (health diagnostics), `--no-browser`, `--no-auto-build`, `--no-voice`. `./stop.sh` stops services; `./killswitch.sh` is the emergency full-stop (locks the codebase in the DB, kills Celery + agents — see Safety below).

For iterative work, run services individually rather than re-running `start.sh`:

```bash
# Backend (from repo root, venv active)
source backend/venv/bin/activate
export FLASK_APP=backend.app GUAARDVARK_ROOT=$(pwd)
flask run --debug --host=0.0.0.0 --port=5000

# Frontend (HMR)
cd frontend && npm run dev -- --host --port=5173
```

## LAN / remote access

Both servers already bind to `0.0.0.0` (Flask `FLASK_RUN_HOST`, Vite `server.host`/`preview.host`), so they're reachable at the host's LAN IP. Two allowlists gate a browser arriving from a non-localhost origin; both are driven from `.env`:

- `VITE_ALLOWED_HOSTS` — comma-separated hosts/IPs added to Vite's host check (`vite.config.js`). Without your LAN IP here, Vite returns **"Blocked request."** Use `all` to disable the check (trusted nets only).
- `VITE_FRONTEND_URL` — your LAN origin (e.g. `http://192.168.1.108:5173`). Added to the backend REST CORS list (`backend/app.py`) and the SocketIO `cors_allowed_origins` (`backend/socketio_instance.py`); the socket handshake's `Origin` is checked even though Vite proxies the connection.

`start.sh` serves the **production build via `vite preview`**, which does *not* share the `server:` block — so `vite.config.js` repeats host-allowlist + `/api`+`/socket.io` proxy under a `preview:` block (shared `ALLOWED_HOSTS`/`PROXY` consts). The frontend calls relative `/api` and connects the socket to `window.location.origin`, relying on that proxy. `VITE_ALLOWED_HOSTS` is read at build time, so rebuild (re-run `start.sh`, or `npm run build`) after changing it. Caution: this exposes the full API (agent/desktop/system tooling) to anyone on the LAN — only on a trusted network, never port-forwarded to the internet.

## Tests & lint

```bash
python3 run_tests.py                              # full suite: installs deps, runs migrations, then pytest
python3 -m pytest backend/tests/test_rules.py -vv # single file (set GUAARDVARK_MODE=test, DISABLE_CELERY=true)
cd frontend && npm run lint                        # eslint, --max-warnings 0
cd frontend && npm run test                        # vitest (jsdom)
scripts/lint.sh                                    # flake8 (syntax-only: E9,E11,F63,F7,F82) + black --check
```

`run_tests.py` forces `GUAARDVARK_MODE=test` and `DISABLE_CELERY=true`; replicate those env vars when running pytest directly. Backend tests are under `backend/tests/` (mirrors source: `api/`, `services/`, `models/`, `integration/`); shared fixtures in `conftest.py`.

## Architecture

**App bootstrap (`backend/app.py`).** A Flask app singleton built by `create_app()`, guarded so it runs **exactly once per process** (`get_or_create_app()` is the accessor — never call `create_app()` directly). Holds SocketIO, SQLAlchemy, Celery, and an Executor. The `__name__ == "__main__"` guard at the top aliases `backend.app` into `sys.modules` to prevent a dual-import that would create a second Flask app and corrupt shared state.

**Blueprint auto-discovery.** API endpoints in `backend/api/` (~90 modules ending `_api.py`) are **not** registered manually — `backend/utils/blueprint_discovery.py` scans the directory and registers every Flask blueprint it finds. Add a new endpoint by dropping a module that exports a `Blueprint`; no central wiring needed. (A handful of blueprints with import-order constraints are still registered explicitly near the end of `create_app()`.)

**Tool registry (`backend/tools/` + `backend/services/agent_tools.py`).** ~70 tool classes (subclasses of `BaseTool`) registered into a global registry at startup via `backend/tools/tool_registry_init.py`. Tools carry a **category** (content, generation, desktop, agent_control, system, browser, etc.) and flags (`is_dangerous`, `requires_approval`). These categories/flags are what the MCP policy gates on — see below.

**AgentBrain (`backend/services/agent_brain.py`).** Three-tier router every chat message enters at Tier 1 and escalates only if needed: **Reflex** (<100ms, pattern match, 0 LLM calls) → **Instinct** (single pre-warmed shot) → **Deliberation** (full ReACT loop). Telemetry appends to `logs/tier_telemetry.jsonl`. State lives in `backend/services/brain_state.py`.

**MCP (`backend/mcp/`).** Bidirectional. The stdio **server** exposes tools/resources to external clients under a strict **default-deny** policy (`backend/mcp/config.py`): the `desktop`, `agent_control`, `system`, `test_execution`, `browser`, and `mcp` categories are hidden, plus anything flagged dangerous/approval-required. Only `data/outputs/` is served, read-only, as `guaardvark://outputs/...`. Config source of truth is `data/config/mcp.json`; env vars override. **When adding a tool that touches the machine, set its category/flags correctly — that is the security boundary.**

**Async work (Celery).** `backend/celery_app.py` + `backend/tasks/`. Long-running work (video render, training, self-improvement, social outreach, backups, RAG autoresearch) runs as Celery tasks; `celery beat` schedules periodic jobs. Workers use the `spawn` multiprocessing start method. Tasks fetch an app context via `get_or_create_app()`. `start_celery.sh` / `start_redis.sh` / `start_postgres.sh` bring up the dependencies.

**Plugins (`plugins/`).** Each is a self-contained GPU service (comfyui, ollama, swarm, audio_foundry, lora_trainer, upscaling, video_editor, vision_pipeline, gpu_embedding, discord, training) with a `plugin.json` manifest (id, port, `vram_estimate_mb`, endpoints, config). The **System Resource Orchestrator** arbitrates VRAM across them. Plugins run as separate processes on their own ports and are health-checked.

**Frontend (`frontend/src/`).** React 18 + Vite + Material-UI v5. `pages/` (~38 routes), `components/` (chat, agent, videoeditor, documents, swarm, …), `stores/` (Zustand for global state, React Context for layout/status). REST via Axios, realtime via `socket.io-client`, code editing via Monaco, graph views via reactflow/d3-force, VNC viewer via `@novnc/novnc`. The Vite config proxies to Flask on `:5000` and includes node polyfills for browser builds.

**CLI (`cli/llx/`).** The `llx` command (published to PyPI as `guaardvark`) — a REPL + command catalog (`commands/`, `command_catalog.py`) that talks to the backend, with its own lite server fallback. Version is single-sourced from the repo-root `VERSION` file.

## Database & schema

SQLAlchemy models in `backend/models.py` (~56 models; `db = SQLAlchemy()` shared instance). **Schema is kept in sync via `scripts/schema_sync.py`, not migration replay** — it diffs `models.py` against the live DB and applies changes (`--check` to verify only). `start.sh` and `run_tests.py` run schema sync / `scripts/check_migrations.py` automatically. After changing `models.py`, run `python3 scripts/schema_sync.py` (or `--check` in CI). Default DB URL: `postgresql://guaardvark:guaardvark@localhost:5432/guaardvark` (override via `DATABASE_URL`).

## Knowledge index (RAG)

**Storage is PostgreSQL + pgvector**, one table per *(profile, scope, embedding dimension)* —
e.g. `data_guaardvark_global_2560`. The dimension is part of the table name on purpose: switching
embedding models lands in a new table instead of contaminating an existing one. `backend/services/
indexing_service.py` builds it through a single factory (`_make_vector_store`) that
`backend/utils/unified_index_manager.py` also uses, so the two construction sites cannot disagree
about the backend. Set `GUAARDVARK_VECTOR_STORE=simple` to fall back to the on-disk JSON store.

Each table carries a `vector`/`halfvec` embedding column with an **HNSW** index, and a generated
`text_search_tsv` column with a **GIN** index — a persisted sparse index, rather than one rebuilt
per process.

### Three traps when changing the embedding model or the store

1. **HNSW rejects vector columns above 2000 dimensions.** The default embedding model is 2560-dim,
   so the column must be `halfvec` (`use_halfvec=True`, ceiling 4000) **and** the index must use
   `halfvec_cosine_ops` — `vector_cosine_ops` is rejected outright against a halfvec column. Get
   either wrong and Postgres logs a warning at table-creation time and carries on **with no ANN
   index at all**: every query becomes a sequential scan, which looks like "retrieval got slow"
   rather than an error.
2. **Keyword ranking needs length normalisation, and has no IDF.** The sparse leg ranks with
   `ts_rank_cd(..., 34)`. The `34` is `32` (bounded scaling) **plus `2` (divide by document
   length)**, and the length half is not optional: without it a short chunk holding the answer
   ranks below longer chunks that merely mention the query's words more often. Separately,
   `ts_rank_cd` has no IDF at all — a term appearing in 800 chunks scores like one appearing in
   14 — so `PostgresSparseRetriever` selects the *rarest* query terms before broadening a query,
   because Postgres cannot weight terms inside a `tsquery`. Skip either and retrieval still
   works; it just quietly answers with the wrong passage.

3. **The docstore must stay empty on pgvector.** `store_nodes_override` mirrors every node into a
   `SimpleDocumentStore`, and `storage_context.persist()` rewrites that store **in full, as JSON,
   on every ingested document** — so the cost of adding one document grows with the size of the
   corpus already added. It is set only for the file-backed store, which genuinely needs it.
   Re-enabling it against pgvector reintroduces quadratic ingest and, at a few million nodes, a
   `json.dumps` that cannot fit in memory.

All three failures are quiet. None of them raise.

### Retrieval

`search_with_llamaindex()` is the retrieval path; `query_index()` is a thin legacy wrapper and
should not be used for new work. The pipeline is: vector + keyword retrieval fused by
`relative_score` with a per-query adaptive alpha (keyword-ish queries lean sparse, prose leans
dense) → metadata filters enforced **after** fusion → dedup → cross-encoder rerank
(`backend/utils/reranker.py`, admitted against free VRAM with a CPU fallback) → MMR for diversity
→ trim to the caller's count.

The keyword leg is `PostgresSparseRetriever`, querying the `text_search_tsv`/GIN index directly.
It tries `websearch_to_tsquery` first — AND-by-default, so it stays precise and its candidate set
stays small — and broadens to an OR over the query's *rarest* terms only when the precise form
matches nothing. Broadening by default measurably hurts: the vector leg already supplies recall,
and a keyword leg returning many loosely-matched chunks crowds out the one the vector leg ranked
correctly.

`VectorStoreQueryMode.HYBRID` is deliberately **not** used. It ignores `alpha` and concatenates
cosine scores with raw `ts_rank` values without normalising either, which would silently destroy
the adaptive alpha. Fusion min-max normalises each leg independently, so only the ordering within
a leg matters, not its scale.

Filters are still enforced post-fusion, but the reason has changed: the sparse leg *can* filter in
SQL (`BM25Retriever` could not), so the post-fusion pass is now a backstop rather than the only
line of defence against a leg re-admitting nodes outside the requested project scope.

Pass `with_trace=True` to get a **provenance trace** alongside the results: which legs actually
ran, the effective alpha, whether resource pressure forced a keyword-only fallback, the embedding
model and dimensions, and whether reranking applied. Under low free VRAM retrieval degrades to
keyword-only by design; the trace is how a caller tells a degraded answer from a bad one. The
`search_knowledge_base` tool renders it as a banner **before** the passages, because callers
truncate tool output and a clipped degradation notice is worse than none.

### Ingest

Per-format dispatch in `get_documents_from_file()`: **Docling** for `.pdf`/`.docx`/`.pptx`
(reading order, section headers, page numbers and bounding boxes), heading-aware sectioning for
markdown, existing readers for the rest. Docling returns `None` rather than raising when it cannot
help, so the legacy readers remain the fallback chain.

OCR is deliberately **not** installed: the OCR extra pulls `opencv-python>=5`, which requires
`numpy>=2` and is incompatible with the pinned ML stack. Born-digital PDFs are read from the text
layer and unaffected; a scanned PDF yields no text and says so rather than indexing an empty
document.

Chunks carry their source, section breadcrumb and page so a retrieved passage can cite itself, and
`backend/utils/contextual_prepender.py` writes that context into the embedded text as well
(Anthropic's Contextual Retrieval). Carried metadata is excluded from the embed/LLM metadata budget
— LlamaIndex counts metadata against chunk size, and a long path plus a breadcrumb will otherwise
overrun the smallest hierarchical tier and silently drop the document to a fallback splitter.

Only **leaf** nodes are indexed. `HierarchicalNodeParser` emits every tier and a parent repeats its
children's text verbatim, so indexing all tiers embeds the same prose two or three times and lets
one query match both a parent and its own child. `GUAARDVARK_INDEX_LEAF_NODES_ONLY=false` restores
the old behaviour.

**One entry point.** `add_file_to_index()` is the pipeline; `add_file_to_index_by_id()` is the same
thing for callers that cannot hold an ORM object (the Celery worker avoids Flask by design).
`add_text_to_index()` is for synthetic text with no file behind it — a repository summary, a client
profile — and takes `replace_where=[...]` naming the metadata keys that identify the caller's
content, so re-running a producer replaces its previous output instead of leaving a stale copy to
compete with the current one at query time.

Three things keep ingest cheap, and all three are easy to undo without noticing:

- **Metadata is excluded from the embed budget on the *document*, before splitting.** LlamaIndex
  sizes each chunk as `chunk_size - len(metadata)` and reads that from the document being split,
  not from the nodes coming out — so excluding keys afterwards fixes what is embedded but not what
  is *counted*, and a document carrying a long path plus tags loses most of its chunk to metadata
  it was never going to embed.
- **`original_text` is excluded too.** `contextual_prepender` stashes each chunk's pre-prefix text
  in metadata so citations can quote the document rather than the breadcrumb. Metadata is
  concatenated ahead of the chunk before embedding, so leaving that key in the budget embeds every
  chunk twice — once as its text, once as its own metadata.
- **`gc.collect()` is amortised, not per document.** A full collection in this process walks
  LlamaIndex, torch and a resident model: ~250 ms, which on a small file exceeds parsing, chunking
  and embedding combined. `GUAARDVARK_INDEX_GC_EVERY` (default 25) controls it. A micro-benchmark
  in a fresh interpreter reports ~4 ms, so the cost is invisible unless measured in place.

### Profiles and corpus summaries

An **index profile** is a named projection of the corpus — chunking strategy, embedding model and
retrieval defaults — managed in `backend/services/index_profiles.py` and toggled in Settings. The
`Document` table is the single source of truth; profiles are derived projections, so deletion
happens once at the registry rather than being mirrored across stores. Profile parameters sit below
an autoresearch experiment and below an explicit caller request in the precedence chain.

**RAPTOR** (`backend/services/raptor_service.py`) recursively clusters chunks and writes LLM
summaries, inserted into the same index as ordinary nodes. Top-k retrieval answers "where is X
stated" but not "what are the recurring themes"; because the summaries live in the same index, a
broad question retrieves them and a specific one retrieves leaves, with no routing logic. It is an
explicit operation — one LLM call per cluster per level — never a side effect of ingest.

## Configuration

All paths resolve through `backend/config.py` — **never hardcode paths.** `GUAARDVARK_ROOT` anchors everything; storage/upload/output/cache/log/backup dirs are derived (`data/`, `logs/`, `backups/`) and overridable via `GUAARDVARK_*` env vars. Secrets and `DATABASE_URL` come from the repo-root `.env`. `GUAARDVARK_MODE` selects runtime mode (`default` / `test`).

## Safety-critical systems

These have real teeth — understand them before touching agent/self-improvement/outreach code:

- **Codebase lock.** `killswitch.sh` and the `codebase_locked` / `self_improvement_enabled` rows in `system_settings` (plus the `data/.codebase_lock` lockfile) gate whether the self-improvement engine may modify code. Self-improvement runs test → agent fix → verify, optionally behind an "Uncle Claude" (Anthropic API) guardian review and a Pending Fixes approval queue.
- **Outreach** (`backend/tasks/social_outreach_tasks.py`, `backend/tools/outreach_tools.py`) is **supervised by default** — drafts queue and nothing posts without explicit approval. Per-platform cadence limits, a JSONL audit trail, persona enforcement, and a global kill switch all apply. Operator identity is config-driven (do not hardcode a person — see commit history).
- **MCP default-deny** (above) is the boundary for what external clients can invoke.

## Conventions

- Commit messages: conventional format `type(scope): description` (`feat`, `fix`, `refactor`, `style`, `docs`, `test`, `chore`).
- Python: type hints where surrounding code uses them; imports grouped stdlib / third-party / local; paths via `backend.config`.
- React: functional components + hooks; MUI v5 for UI; Zustand for global state, Context for layout.
- Keep changes focused and match surrounding style; one concern per PR.
