# Guaardvark GitHub Launch Playbook

## Pre-Launch Checklist (do these first)

- [x] MIT License added
- [x] CONTRIBUTING.md created
- [x] Issue templates (Bug, Feature, Question) created
- [x] PR template created
- [x] CI workflow (GitHub Actions) created
- [x] README badges (license, CI, stars, issues) added
- [x] Contributing section added to README
- [x] Good first issues drafted
- [ ] `gh auth login` — authenticate GitHub CLI
- [ ] Push all the above to `main`
- [ ] Create 6 good-first-issues via `gh issue create` (see docs/good-first-issues.md)
- [ ] Add GitHub repo topics (see below)
- [ ] Enable Discussions on the repo (Settings → Features → Discussions)
- [ ] Pin key issues (the good-first-issues)

## GitHub Repo Topics

Set these via Settings → Topics (or `gh repo edit --add-topic`):

```
ai, self-hosted, local-llm, ollama, rag, retrieval-augmented-generation,
flask, react, video-generation, image-generation, voice, self-improving,
desktop-app, comfyui, llamaindex, developer-tools
```

These make the repo discoverable via GitHub Explore and topic pages.

## Post-Push: Create Issues

After pushing, run the commands from `docs/good-first-issues.md` to create all 6 issues with proper labels.

Create labels first:
```bash
gh label create "good first issue" --color 7057ff --description "Good for newcomers" --force
gh label create "frontend" --color 1d76db --description "React/Vite frontend"
gh label create "backend" --color 0e8a16 --description "Flask backend"
gh label create "cli" --color fbca04 --description "llx CLI tool"
gh label create "devops" --color d93f0b --description "Scripts, CI, deployment"
gh label create "triage" --color ededed --description "Needs triage"
```

---

## Social Media Launch Posts

### Reddit — r/selfhosted

**Title:** I built a self-hosted AI platform that runs entirely offline — chat with RAG, generate video/images, voice interface, self-improving agents, and more

**Body:**

Hey r/selfhosted,

I've been building Guaardvark for the past year — a self-hosted AI platform that runs entirely on your hardware with no cloud dependencies or API keys.

**What it does:**
- Chat with your documents using RAG (hybrid BM25 + vector retrieval)
- Generate video (Wan2.2 14B, CogVideoX) and images (Stable Diffusion) locally
- Voice interface (Whisper STT + Piper TTS)
- Desktop-style file manager in the browser
- Autonomous code agents (ReACT loop)
- Self-improving — runs its own tests, finds bugs, fixes them
- Multi-machine sync via Interconnector
- Plugin system for GPU services (Ollama, ComfyUI)
- CLI tool (`llx`) for terminal access

**Tech stack:** Flask + React + PostgreSQL + Celery + LlamaIndex + Ollama + ComfyUI + Whisper.cpp

**One-command install:**
```
git clone https://github.com/guaardvark/guaardvark.git && cd guaardvark && ./start.sh
```

The startup script handles everything — PostgreSQL, Redis, Python deps, Node deps, DB migrations, frontend build, all services.

Recommended: NVIDIA GPU with 16GB VRAM for video generation, but works on CPU for chat/RAG.

MIT licensed. Looking for contributors — there are good-first-issues tagged if you want to jump in.

GitHub: https://github.com/guaardvark/guaardvark
Website: https://guaardvark.com

Happy to answer questions!

---

### Reddit — r/LocalLLaMA

**Title:** Open-source local AI platform with RAG, video gen (Wan2.2), image gen, voice, self-improving agents — one-command install

**Body:**

Built a platform that wraps Ollama, LlamaIndex, ComfyUI, Whisper, and Piper into a single self-hosted app with a React UI. Everything runs locally.

Highlights for this community:
- **Runtime model switching** — swap LLMs and embedding models via the UI, old model unloads first (no OOM)
- **KV cache optimization** — system prompt locked in Ollama KV cache between turns
- **RAG Autoresearch** — autonomous loop that evaluates retrieval quality, runs experiments, keeps improvements
- **Wan2.2 14B MoE** video generation with RIFE interpolation + Real-ESRGAN upscaling
- **GPU conflict management** — exclusive-access plugins auto-switch to prevent VRAM collisions
- **Self-improving** — runs tests, parses failures, dispatches agent to fix them

One-command install: `git clone ... && ./start.sh`

MIT licensed. GitHub: https://github.com/guaardvark/guaardvark

---

### Reddit — r/opensource

**Title:** Guaardvark — MIT-licensed self-hosted AI platform (RAG, video/image gen, voice, self-improving agents)

**Body:**

Just open-sourced Guaardvark under MIT — a self-hosted AI platform I've been building for a year. Looking for contributors and feedback.

It's a full-stack app (Flask + React) that combines:
- RAG-powered chat (LlamaIndex + Ollama)
- Local video generation (Wan2.2, CogVideoX via ComfyUI)
- Image generation (Stable Diffusion)
- Voice (Whisper + Piper TTS)
- Desktop-style file management
- Self-improving code agents
- Multi-machine sync
- CLI tool

Everything runs locally, no API keys, no cloud. One command to install.

There are `good first issue` tags if you want to contribute. CONTRIBUTING.md and issue templates are set up.

GitHub: https://github.com/guaardvark/guaardvark

---

### Hacker News — Show HN

**Title:** Show HN: Guaardvark – Self-hosted AI platform with RAG, video gen, voice, and self-improving agents

**Body:**

I've been building Guaardvark for the past year. It's a self-hosted AI platform that runs entirely on your hardware.

Core idea: one unified interface for chat (with RAG), image/video generation, voice, code agents, and file management — all backed by local models via Ollama.

Some things I'm proud of:
- One-command install handles everything (PostgreSQL, Redis, deps, migrations, builds)
- Self-improving: the system runs its own test suite, identifies failures, and dispatches an AI agent to fix them
- RAG Autoresearch: autonomous loop that experiments with retrieval parameters and keeps improvements
- Video generation with Wan2.2 14B on a single 16GB GPU
- Plugin architecture for GPU services with VRAM budget management

Tech: Flask, React, PostgreSQL, Celery, LlamaIndex, Ollama, ComfyUI, Whisper.cpp, Piper TTS

MIT licensed. Looking for contributors.

https://github.com/guaardvark/guaardvark

---

### Twitter/X

**Post 1 (launch):**

Guaardvark is now open source (MIT).

A self-hosted AI platform — no cloud, no API keys, your data stays local.

- RAG chat with your documents
- Video gen (Wan2.2 14B)
- Image gen (Stable Diffusion)
- Voice (Whisper + Piper)
- Self-improving code agents
- One-command install

github.com/guaardvark/guaardvark

**Post 2 (thread):**

What makes it different:

1. Self-improving — runs its own tests, finds bugs, fixes them autonomously
2. RAG Autoresearch — experiments with retrieval settings, keeps what works
3. GPU service management — plugin system handles VRAM conflicts automatically
4. Multi-machine sync — connect Guaardvark instances into a family
5. Desktop-style file UI in the browser

Built with @Flask, @reactjs, @ollaboratory, @LlamaIndex, @comaboratory

---

## Awesome Lists to Submit To

After the repo has some stars and activity:

1. **awesome-selfhosted** — https://github.com/awesome-selfhosted/awesome-selfhosted (requires OSI license ✅)
2. **awesome-llm** — https://github.com/Hannibal046/Awesome-LLM
3. **awesome-generative-ai** — https://github.com/steven2358/awesome-generative-ai
4. **awesome-rag** — search for RAG-focused lists
5. **awesome-local-ai** — if it exists

## Timeline

1. **Day 0:** Push infrastructure, create issues, set topics, enable discussions
2. **Day 1:** Post to r/selfhosted (this is your #1 audience)
3. **Day 2:** Post to r/LocalLLaMA
4. **Day 3:** Show HN
5. **Day 3-4:** Twitter/X thread
6. **Day 5:** r/opensource
7. **Week 2:** Submit to awesome lists
8. **Ongoing:** Engage with every issue, PR, and comment quickly — responsiveness is the #1 signal of a healthy project
