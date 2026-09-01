# Guaardvark Walkthrough Series — Production Bible

12 episodes, produced by the system itself via `scripts/demo_director/`
(deterministic Playwright+xdotool driving, per-beat takes, cloned female
narrator with whisper read-checks, constructed pauses). This file is the
source each `episodes/epNN_*.py` beat file is written from.

Ledger: `docs/local-workspace-only/MASTER_TASKS.md` (walkthrough-videos entry)

---

## Series-wide rules

**Narrator.** Chatterbox clone of the Piper female voice
(`data/uploads/voice_references/piper-female-series-narrator.wav`), Piper
fallback. Narration = list-of-lines; blank line = long pause. Spoken
"Guaardvark" → "Guard-vark" via the pronunciation map; on-screen text keeps
the real spelling. Every line is whisper-verified before use.

**Tone.** Confident, candid engineer showing a friend around. Short
sentences. Concrete numbers, never superlatives without a number. One
honesty beat per episode. Mantra once per episode over a fresh visual proof:
**"One machine. No cloud."**

**On-screen text.** Lower-thirds use exact sidebar names. Cross-link corner
chips `▸ Ep N: <Feature>` (~3s) mirror timestamped description links. Stat
cards in monospace for numbers. Shortcut toasts when a shortcut is used.

**Keyword mesh.** Each episode speaks 1 primary + ≥5 secondary keywords
aloud; every feature appears in ≥3 episodes (matrix in the plan file).

**Recording.** Xvfb `:98` 1920×1080 kiosk Chromium; OBS only for host-desktop
shots (start.sh terminal, Shotcut GUI, Discord client, killswitch terminal).
GPU rule: one heavy service on the card per take; renders pre-produced in
asset sessions, real progress screens recorded then. Never claim: OBS
integration, lipsync, Settings HF-token field, egress blocking. Don't say
"chat keeps answering during a render" — say the UI stays responsive and
renders wait for VRAM.

**Shoot order.** 7 → 5 → 6 → 8 → 9 → 10 → 2 → 3 → 4 → 11 → 12 → 1 (montage last).

---

## Ep 1 — Meet Guaardvark (4:00) — `ep01_tour.py`
**Primary:** the whole system · **GPU cast:** none · **OBS:** start.sh terminal
**Assets:** montage cuts from eps 2–12 (cut last).

Hook:
> "This is Guaardvark."
> "It writes films. Clones voices. Edits video. Trains characters. And
> fixes its own code."
> ""
> "Every bit of it runs on one desktop G P U, in this room, with the
> network cable out."
> "One machine. No cloud."
> "Let me show you around."

Beats:
1. **Montage** — 8 cuts, 2–3s each (agent desktop, energy arc, storyboard
   grid, swarm graph, VRAM bar, theme flip, music video, System Map). Title
   card `GUAARDVARK`.
2. **Install** (OBS) — start.sh timelapse, 11 Vader-red steps. VO: "Eleven
   steps. One command. A fresh machine." Text: `Step 6/11 — Ollama`.
3. **Dashboard** — drag a card; GPU bar. Text: `Dashboard`.
4. **Sidebar tour** — slow scroll through Main / Studio / Management /
   Configuration. VO names the four groups; "twelve episodes ahead."
5. **Theme flip** — Guaardvark → Fallout → Vader → back. Text:
   `Settings ▸ Appearance`.
6. **Plugins glance** — VRAM bar. Chip `▸ Ep 12: Plugins`.
7. **Closer** — nvidia-smi + unplugged cable. "Everything you just saw ran
   right here." Episode end-grid (all 12 tiles).

Cross-links: all episodes (sitemap root).

---

## Ep 2 — A Brain With Three Speeds (5:00) — `ep02_brain.py`
**Primary:** Chat/AgentBrain · **GPU cast:** Ollama live · **Assets:** none

Hook:
> "Ask it to play a song. It answers in under a hundred milliseconds, with
> zero A I calls."
> "Ask it to research something, and click through the results. It thinks
> in steps you can watch."
> ""
> "Same chat box. Three different brains."

Beats:
1. **Reflex** — `media_play` command lands instantly. Stat card
   `Tier 1 — Reflex · 0 LLM calls · <100 ms`.
2. **Instinct** — normal question, tokens stream. `Tier 2 — Instinct · one call`.
3. **Deliberation** — multi-step request; AgentThinkingTrail streams live.
   Narration written to cover 20–60s of real thinking (cutaways allowed).
   `Tier 3 — Deliberation`. Honesty beat: the tool selector NOT firing image
   gen on "there is an image on the website".
4. **Slash commands** — popup: `/imagine`, `/agent`, `/voice`, `/model`;
   custom commands come from Rules. Chip `▸ Rules & Prompts`.
5. **`/imagine`** — inline image lands in chat. Chip `▸ Ep 5: Image Gen`.
6. **Floating chat** — drag the chat card onto the Video Gen page. VO: "chat
   follows you everywhere."
7. **Narrate button** — a reply read aloud. Chip `▸ Ep 7: Audio Studio`.
8. **Lessons** — Begin/End Lesson → `lesson_summary` distilled. Text:
   `Lesson Pearls`.
9. **Closer** — drop a file into chat; it indexes. "Where do files live?
   Next episode." Chip `▸ Ep 3: Files`.

Automation notes: Tier-3 latency is real (median 23.4s) — hold rules cover
it; chat model = gemma4; no renders during this shoot.

---

## Ep 3 — Your Files Have a Desktop (5:00) — `ep03_files.py`
**Primary:** Files/RAG · **GPU cast:** Ollama (RAG answers)
**Assets:** staged demo folder tree (no personal files), small corpus so
indexing completes on camera.

Hook:
> "This is a browser tab."
> "Those are folder windows. Draggable. Resizable. Snap to grid."
> ""
> "Your files didn't move to the cloud. The desktop moved into Guaardvark."

Beats (pilot proved 1–2):
1. **Desktop metaphor** — drag/resize/fold windows, auto-arrange.
2. **Bulk import** — drop a whole folder tree; nesting preserved.
3. **Viewers** — PDF, DOCX, CSV, audio open in-app. "Nothing gets
   downloaded. Nothing gets uploaded. It's already home."
4. **Media gallery** — opt-in gallery view, fullscreen preview with sibling
   paging, window-scoped breadcrumbs. Text: `Media view — opt-in`.
5. **Folder Properties** — link to client/project; code-repo toggle
   auto-detects languages. Text: `Entity links`.
6. **Index + retrieval** — index the staged folder (live progress), then
   TestRetrievalModal: the actual chunks + scores. Honesty beat: retrieval
   shown, not asserted. Text: `RAG — show your work`.
7. **Repo intelligence** — chat: "what depends on X?" → dependency-graph
   answer. Stat card `get_dependency_graph`.
8. **Closer** — "Who tunes those retrieval parameters? The system does.
   Overnight. Episode eleven." Chip `▸ Ep 11: Autoresearch`.

Automation notes: folder-window selectors proven in pilot (glyph dclick,
breadcrumb-offset drag, CloseIcon sweep in reset). Stage the demo tree in
Phase 2.

---

## Ep 4 — The Agent Behind the Glass (6:00) — `ep04_agent.py`
**Primary:** Screen Agent · **GPU cast:** Ollama (gemma4 vision) — nothing else
**Display:** the agent's real `:99` (square), framed PiP over branded backdrop.

Hook:
> "The hardest thing we ever built... is a mouse."
> ""
> "This agent has its own desktop. Its own eyes. Its own hands."
> "I'm going to show you it working, missing, and recovering."
> "Because that's what real autonomy looks like."

Beats:
1. **The glass** — AgentScreenViewer card floating over Chat; VNC into the
   XFCE desktop. Stat card `Xvfb :99 · VNC 5999`.
2. **/agent mode** — orange chip; a message becomes a screen task.
3. **SEE-THINK-ACT-VERIFY** — split: VNC + AgentThinkingTrail streaming.
4. **Servo** — crosshair → zoom-crop → corrected click. Text:
   `Servo — approach, observe, correct`.
5. **A miss, on camera** — VERIFY catches it, retries. VO: "It missed. It
   saw that it missed. That's the feature." (THE honesty beat of the series.)
6. **Recipes** — deterministic recipes bypass vision; instant.
7. **Learn by demonstration** — human performs a task once; before/after
   pairs captured. Text: `DemoRecorder`.
8. **Apprentice** — GUIDED → SUPERVISED → AUTONOMOUS; promotion after 3
   clean runs.
9. **Eye bake-off** — ranking table. VO speaks the real number: "Eighty-two
   pixels, mean. We publish our own miss distance."
10. **Closer** — agent opens the Video Gen page. "What it does with those
    hands is up to you. Next: it makes movies." Chip `▸ Ep 6`.

Automation notes: ONLY vision-driven episode — shoot in short takes, retake
budget high, VisionAnalyzer-90s/task-60s timeout inversion must be fixed
first. Expect this to be the longest shoot day.

---

## Ep 5 — A Million Pictures, One Prompt (5:00) — `ep05_images.py`
**Primary:** Image Gen/Upscaling · **GPU cast:** ComfyUI (Ollama for the
Director beat, recorded first) · **Assets:** big pre-rendered batch.

Hook:
> "One concept in."
> "The Media Director — an L L M art director — writes a dozen distinct,
> connected prompts."
> ""
> "Not the same image with different seeds."
> "Different images, that belong together."

Beats:
1. **Media Director** — concept → expanded prompts (Ollama; record before
   ComfyUI takes the card).
2. **Live batch** — 4 images, turbo model, grid fills on camera.
3. **The wall** — browse the pre-rendered big batch in Media Library.
4. **Models** — registry + download modal with live MB/s.
5. **Infographic** — "type → five seconds → P N G."
6. **Fix Anatomy / face restore** — before/after. Honesty beat: VRAM
   calibration ladder — "it measures, it doesn't guess."
7. **Kontext edit** — natural-language image edit on an upload.
8. **Upscaling** — 8K result, pixel-peep pan; frame-by-frame video upscale
   mention.
9. **Auto-filing** — outputs land in Files under /Images. Chip `▸ Ep 3`.
10. **Closer** — a keyframe animates. "Make them move. Episode six. Give
    them a *face that persists* — episode nine." Chips `▸ Ep 6 · ▸ Ep 9`.

---

## Ep 6 — Hollywood on One GPU (6:00) — `ep06_video.py`
**Primary:** Video Gen · **GPU cast:** ComfyUI · **Assets:** Cinema-tier
renders pre-produced; Fast-tier short render live.

Hook:
> "Eleven video models. Five families."
> "Wan. CogVideo X. L T X. Hunyuan. MiniMax H3."
> "Text to video. Image to video. And one of them talks."
> ""
> "On the same card that ran your chat in episode two."

Beats:
1. **Model menu** — 11 backends across 5 families; VideoModelsModal downloads,
   with MiniMax H3's license chip and application-form link on its row. Stat
   card: `video_model_registry.py — one source of truth` (the capability
   contract every page reads lives there too).
1b. **One of them talks** — a 5 s MiniMax H3 clip from the "Two-line dialogue
   scene" preset, audio up on the reveal. Numbers on screen from the measured
   run on a 16 GB RTX 40-series card: 186 s on the 8-step profile, 390 s at
   20 steps, 14.5 GB peak. Honesty beat: ask the chat for a clip "with
   sound" on Wan and read the refusal aloud — the tool names the model that
   can, instead of rendering a silent clip.
2. **Preset tour** — Quality (Fast→Maximum), Duration, Motion, Aspect. VO on
   the pixel-area trick: "reshape the frame; the VRAM bill stays the same."
3. **Prompt styles** — Cinematic / Anime / Claymation / Ghibli — instant.
4. **Launch** — Fast-tier render live; stage chips march: Queued → Storyboard
   → Director → Keyframe → Generating → Post → Done.
5. **gpu_wait** — warning chip + queue hint. VO: "renders wait for the card
   instead of failing. Up to ninety minutes, if that's what it takes."
6. **Draft vs Cinema** — side-by-side (pre-rendered). `2× RIFE + 2× ESRGAN`.
7. **Advanced Editor** — one click into ComfyUI, Guaardvark-themed.
8. **Closer** — "Single clips are one thing. A *beat-synced music video* is
   episode eight." Chip `▸ Ep 8`.

Honesty beat: preflight refuses silent model fallback; gated-model errors
tell you exactly what to do; the 1344x768 canvas on a 16 GB card runs out
of memory and the registry says so instead of offering it.

---

## Ep 7 — The Voice Foundry (5:00) — `ep07_audio.py`
**Primary:** Audio Studio/Voice Cloning · **GPU cast:** Audio Foundry ONLY
**Assets:** this shoot GENERATES the series music bed + stingers.
**Shot FIRST** — its outputs feed every other episode.

Hook (the true story — it happened in this repo):
> "The narrator of this series isn't a person."
> ""
> "She started as a tiny local text-to-speech model."
> "Then this feature cloned her — into the voice you're hearing right now."
> ""
> "Let me show you how. Including the part where the system demanded
> consent before it would speak a single word."

Beats:
1. **The reference** — play the 13-second Piper clip; VO explains the
   phonetic script ("azure... measured... those words hit every sound in
   English").
2. **Consent** — upload flow creates the sidecar; then the 403 ON CAMERA for
   an unconsented path. Text: `403 — consent required`. (Series' strongest
   trust beat.)
3. **The clone** — same paragraph: Piper vs Kokoro vs Chatterbox clone,
   waveforms side by side. "Same identity. New range."
4. **Self-check** — whisper reads a take back and rejects a babbled line.
   VO: "it listens to itself before you ever hear it."
5. **Music** — chips: dark · cello · epic → Polish pass preview → 60s
   ACE-Step instrumental. "This track becomes the bed for the whole series."
6. **FX Lab** — "rain on a tin roof, distant thunder" → 20s ambience.
7. **Auto-filing** — tracks appear in Files with waveforms. Chip `▸ Ep 3`.
8. **Closer** — "One song. One video. Made *from* each other. Episode
   eight." Chip `▸ Ep 8`.

Automation: Audio Foundry holds the card alone (10GB reservation; it kills
ComfyUI — that's why the cast list exists).

---

## Ep 8 — Drop a Song, Get a Music Video (5:00) — `ep08_musicvideo.py`
**Primary:** Music Video · **GPU cast:** ComfyUI (analyze/approve beats
recorded first with Ollama) · **Assets:** full run pre-produced from Ep 7's
song (21 clips ≈ 2–3h asset session).

Hook:
> "Drop in an M P 3."
> "The system reads its tempo. Its beats. Its energy."
> ""
> "Then an A I director writes a different shot for every cut."
> "Watch the arc."

Beats:
1. **Three inputs** — song (Ep 7's track), style prompt, short narrative.
2. **Energy arc** — the strip renders: cool blue calm → red drop. Cuts per
   minute follow it.
3. **The plan** — per-cut prompts, all different. Stat card quoting the
   director's own docstring problem statement.
4. **Cost gate** — approval BEFORE any GPU spend. "Planning is free. You
   approve before the card lifts a finger."
5. **Generation** — live launch, stage progress, then time-lapse from the
   asset session.
6. **The video** — cuts landing on beats; energy arc overlaid in a corner.
6b. **One pass per cut** — switch the I2V model to MiniMax H3: the planner's
   cut ceiling follows the model (15 s), so the same song plans fewer, longer
   windows; each renders in one pass with its song slice anchored at frame
   zero. Record the real cut count against the 21 clips on screen. Honesty
   beat: the song stays the master track; the model's own soundtrack is
   dropped by design.
7. **Closer** — "One song and one director. Now imagine five crew members
   and a *script*. Episode nine." Chip `▸ Ep 9`.

---

## Ep 9 — A Film Crew That Never Sleeps (6:00) — `ep09_filmcrew.py`
**Primary:** Film Crew/Cast & LoRA · **GPU cast:** ComfyUI · **Assets:** LoRA
training + production render pre-produced; the two human gates are live.

Hook:
> "Screenwriter. Casting director. Cinematographer. Storyboard artist.
> Editor."
> ""
> "Five A I crew members. One three-line logline."
> "And the only person on set... is you. Exactly twice."

Beats:
1. **Logline in** — create production; StageProgress begins.
2. **Screenwriter** — structured scene/shot breakdown appears.
3. **Casting gate** (human #1) — pick from Cast Library.
4. **Cast & LoRA detour** — reference photos → vision-built identity bible →
   LoRA controls (rank/alpha/steps) → sample approve/reject. Stat card:
   `~46 MB per character`.
5. **Cinematographer** — shot plans: camera, framing, lens.
6. **Storyboard + Curator** — grid fills; Gemma-4 *looks at* each frame,
   auto-approves on-model shots, escalates the doubtful. "The A I reviews
   its own work — and knows when to ask."
7. **Regenerate one shot** — prompt override, on camera.
8. **Approval gate** (human #2) → rendering: clips + voiceover + per-scene
   music mix (Ep 7's stack). Honesty beat: kill the backend mid-stage,
   restart, watch it resume idempotently.
8b. **A scene that speaks** — the same production created with the video
   model set to MiniMax H3 in the New Production dialog: one scene renders
   as a single window, the cast member says their two lines in the clip
   itself, windows join on the storyboard stills, no narration laid over.
   Honesty beat: say whether identity from the cast's reference images held
   against the LoRA still — whichever way it went.
9. **Closer** — final.mp4 plays; hover "Open in Shotcut". "That file is a
   real editing project. Episode ten." Chip `▸ Ep 10`.

Never mention lipsync.

---

## Ep 10 — The Editor That Shows Its Work (5:00) — `ep10_editor.py`
**Primary:** Video Editor/Shotcut · **GPU cast:** none (melt = CPU; vision
look-pick pass runs before recording) · **OBS:** Shotcut GUI segment.
**Assets:** Ep 6's clips + Ep 7's song.

Hook:
> "Most A I editors are a black box."
> ""
> "This one lets you open the exact frames its art director looked at."
> "And overrule it. Per clip."

Beats:
1. **Drop in** — 6 clips + the song; three-lane timeline.
2. **Plan** — style recipe "Cinematic": auto-editor trims + beat/energy +
   vision look-picks per clip.
3. **Director's Notes** — THE FRAMES the model saw (`/vision/frames/`);
   override one filter choice. (The episode's thesis beat.)
4. **Render** — .mlt + .mp4; drag a text overlay on the preview (real
   ffmpeg drawtext).
5. **Open in Shotcut** (OBS) — the filters are native, editable MLT objects.
   "Nothing is baked-in fakery."
6. **Shortcuts montage** — space, t, del, ⌘Z toasts.
7. **Closer** — "This episode was assembled by this editor, from episode
   six's clips, to episode seven's music. The system now produces its own
   tutorials. Which raises a question." (beat) "Can it improve its own
   *code*? Episode eleven." Chip `▸ Ep 11`.

---

## Ep 11 — The System That Fixes Itself (6:00) — `ep11_selfimprove.py`
**Primary:** Swarm/Self-Improvement/System Map/Autoresearch · **GPU cast:**
Ollama (local swarm backend) · **Assets:** overnight autoresearch run
pre-executed; staged known-fixable test failure.

Hook:
> "Every night, this system runs its own test suite."
> "When something fails, it dispatches an agent to fix it. Then re-runs the
> tests."
> ""
> "And — this part matters — it asks an outside guardian for permission
> before touching a single file."

Beats:
1. **System Map** — 712-node constellation; `/` search, `R` reset toasts.
2. **Finding → Fix** — click a stale node → "Send to the self-improvement
   agent" → a real PendingFix staged. "It never fabricates a diff."
3. **Self-improvement run** — pytest → parse → dispatch → green; color-coded
   live bar. (Staged failure so it completes in minutes.)
4. **Uncle Claude** — guardian review, risk level, `blocked_by_guardian`
   state. "An independent model, with veto power."
5. **Pending Fixes** — the human queue. "You are the last gate."
6. **Swarm** — template launch: 5 agents in isolated git worktrees, live
   graph, resource monitor gating spawns on VRAM.
7. **Flight Mode** — network down, agents keep coding via Ollama.
8. **Autoresearch** — overnight run → morning report ledger → promotion,
   then a revert. "Keeps the wins. Reverts the regressions."
9. **The retro** (honesty beat) — stat card: `3.4 days · 134,000,000 rows ·
   fixed`. VO tells the runaway story straight, ending on the five kill
   switches that exist because of it.
10. **Closer** — "Autonomy needs a leash. Episode twelve: the command
    center — and every way to pull the plug." Chip `▸ Ep 12`.

---

## Ep 12 — Command Center (6:00) — `ep12_command.py`
**Primary:** Plugins/GPU/Safety/Reach · **GPU cast:** staged per segment ·
**OBS:** killswitch terminal + Discord client.
**Assets:** one live short render for the VRAM bar; clean second-node
pairing (merge `fix/interconnector-stable-identity` first, or scope down).

Hook:
> "Eleven episodes of A I doing whatever it wants would be terrifying."
> ""
> "If you couldn't see everything. Gate everything. And kill everything."
> ""
> "Welcome to the command center."

Beats:
1. **VRAM budget** — Plugins page: stacked per-plugin segments moving during
   a live render.
2. **Conflict detection** — starting ComfyUI offers to stop Ollama. VO tells
   the 16-gigabyte truth straight: "one card. The system referees." While
   ComfyUI cold-starts, fill the wait with the prompt compiler: paste a plain
   sentence into the Video Generator on MiniMax H3 and show the effective
   prompt it becomes (numbered shots, cut times, speaker ids). Record the
   cold-start seconds and the first render's reserve-VRAM refusal, then the
   3 GB reserve that fixes it.
3. **Jobs vs Activity** — what you queued vs what it's doing on its own.
4. **Kill switches** — five of them, ending with `./killswitch.sh` in a
   terminal (OBS). "Talks straight to the database and the O S. It works
   even when the app doesn't."
5. **Codebase Lock** — the AI cannot edit its own guardrails.
6. **CLI** — `llx` REPL: `llx videos generate`, `llx ask`, agentic
   edit/test/diff. "The whole platform from a shell."
7. **MCP** — Claude Desktop connects; default-deny categories. Stat card:
   `23 tools · 58 resources · default deny`.
8. **Discord** (OBS) — `/imagine` + a short one-on-one `/voice` exchange.
9. **Interconnector** — second node registers, hardware self-profile,
   learning broadcast. "Fixes propagate to the whole family."
10. **Backup** — schema-migration-aware restore.
11. **Series closer** — montage reprise → end-grid of all 12. "One machine.
    No cloud. Twelve episodes. Everything linked below."

---

## Asset pre-production checklist (Phase 2, before shoots)

- [ ] Staged demo folder tree (Ep 3) — generic docs/PDFs/media, no personal data
- [ ] Ep 5 big image batch (pre-render) + kontext source image
- [ ] Ep 6 Cinema-tier renders (pre-render) + prompt list
- [ ] Ep 7 shoot doubles as audio asset session (music bed, stingers, FX)
- [ ] Ep 8 full music-video run from Ep 7's song (2–3h GPU session)
- [ ] Ep 9 LoRA training + production render (longest asset session)
- [ ] Ep 11 overnight autoresearch run + staged test failure. Seeding a fresh
  box (2026-08-29): the corpus gate counts TEXT documents (images/audio and
  raw .pdf/.docx bytes don't count, minimum 10) — bulk-import the Ep 3
  AcmeCorp tree plus the repo's own docs with `force_copy: true` (the
  default MOVES the source files), then `POST /api/autoresearch/eval-pairs/regenerate`,
  then `POST /api/autoresearch/runs {"budget_hours": 0.25}`. The beat file
  refuses to shoot over "No research runs yet." The fix queue needs a real
  PendingFix: dispatch an `unwired-tool` finding from the System Map (the
  agent must actually call the edit tool — a directed run can report
  "success" on a bare final answer with nothing staged).
- [ ] Ep 12 interconnector: merge identity branch or scope segment down
- [ ] Thumbnails: Image Gen/Infographic, episode keyword in title text
- [ ] Descriptions: primary-keyword opener + chapters (= on-screen labels)
  + "Featured in this episode" links to the other 11
