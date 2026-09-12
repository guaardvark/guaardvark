# Guaardvark — Agent Guide

Start here. This is the operating contract for any coding agent (Claude Code, Cursor, Codex,
OpenClaw, Gemini CLI, or any MCP client) that is asked to *use* a running Guaardvark: make
images, video, music videos or short films, narrate, compose, upscale, train a character, add a
model, launch a swarm, answer from the user's documents, or operate the box. `AGENTS.md` is
the short router; this file is the contract behind it. Contributor rules for *changing* the
codebase are at the end and in `CONTRIBUTING.md`.

Every skill under `.agents/skills/` names the exact MCP tool or REST route for its flow. This
guide says how to behave around them: what to check first, what to announce, where to stop for
the user, and what never to do.

---

## 1. First interaction

**A vague or exploratory first message** ("what can you do", "make me something", "help me with
video") gets a preflight (section 3) and then a short menu in plain words: what this machine can
do today, one example prompt per capability that is actually installed, and what would unlock
more (a plugin to start, a model to install). No feature list longer than the screen.

**A specific request** ("a 20-second clip of a kite over a grey sea", "film this script",
"train a LoRA of Mara from these photos") skips the menu. Run the preflight silently, name the
skill you are following, and start.

**A file or link as the request** is an entry point, not a search: a song file means the
music-video skill; a screenplay or logline means the Film Crew; reference photos of a person
mean the Cast skill; a Hugging Face link means the models skill; a PDF or folder means the
knowledge skill; a video means the video editor or the upscale skill, ask which.

**A question about what is happening** ("is it done", "what is using the GPU", "why did it
fail") goes to `get_generation_status`, `inspect_gpu`, or `read_logs` before any guess.

## 2. Rule Zero: every job goes through a skill

Every generation, training, sync or swarm request runs through the matching skill and the
tool or route it names. Do not write ad-hoc Python against the diffusion libraries, do not
call ComfyUI's port directly, do not start a service with `systemctl`, do not invent a route.
The skills carry the request fields the handlers actually read and the limits the models
declare; an improvised call skips both and produces the wrong output or a fight for the GPU.

If no skill covers the ask, say so and offer the closest one. If a route in a skill answers
404, the checkout is older than the skill: say that, and point at the Studio page instead.

## 3. Mandatory preflight

Before the first generation in a session, establish four facts and keep them in mind:

1. **The backend answers.** `GET ${GUAARDVARK_URL:-http://localhost:5000}/api/health`
   (macOS default port 5055). If not, the user starts it (`./start.sh` or `docker compose up`);
   you do not.
2. **Which plugins are running.** `GET /api/plugins/status` returns a map of plugin id to
   state. Generation needs `comfyui`; voice, music and FX need `audio_foundry`; upscaling needs
   `upscaling`; LoRA training needs `lora_trainer`; swarms need `swarm`. A route answers 503
   while its plugin is off. Start one with `POST /api/plugins/<id>/start`.
3. **What models are installed.** `GET /api/batch-video/models` and
   `GET /api/batch-image/models` list every registry entry with `installed`, modes, frame and
   step limits and speed profiles. `GET /api/settings/active_video_model` says which one runs
   by default for text-to-video, image-to-video and scenes. Never name a model you have not
   seen marked installed.
4. **What the hardware allows.** `inspect_gpu` (MCP) shows the card, free memory, who holds
   the exclusive lock and which plugins are resident. The tiers below decide what to offer.

| Tier | VRAM | What this box can do |
|---|---|---|
| A, no NVIDIA GPU | none | chat, retrieval, voice in and out, the coding swarm, the video editor, MCP. No image, video, music or upscaling |
| B, entry GPU | 8–12 GB | plus vector retrieval, screen agents, SD/SDXL-class images, upscaling; music at 12 GB. Video is refused below 16 GB |
| C, design target | 16 GB | the full stack, one heavy job at a time: FLUX-class images, Wan 2.2 / LTX / CogVideoX / MiniMax H3 video, music and neural voice, Film Crew, music videos |
| D, headroom | 20–24 GB+ | concurrency and quality: two resident LLMs, heavier image models by default, full-batch LoRA training |

Present the envelope honestly: "this machine can do X and Y; Z needs a 16 GB card" beats a
menu the box cannot deliver. When a plugin is off or a model is missing, offer the one
action that unlocks it and proceed with the best available path if the user declines. No
nagging.

`GET /api/settings/profile` tells you the product shape: `workstation` (everything) or
`creator` (media first; agent, index, outreach and automation unlisted but live).

## 4. Decision communication contract

**Announce before spend.** Before any call that occupies the GPU for more than a few seconds
(a video clip, a batch, a song, a training run, an upscale of a video), state in one line:
the skill, the model that will run, the size or duration, the step count when it matters,
and the expected wall time. "Queuing a 5-second clip on `wan22-5b`, 24 fps, 20 steps, about
two minutes on this card."

**Ask before switching.** Get a yes before you change a model family, drop below the model's
declared minimum steps (the server raises it anyway), change resolution or duration away from
what the user asked, drop audio, narration or music from an approved plan, or change from one
clip to a batch.

**Never downgrade silently.** If the requested model is not installed, the GPU is too small,
or a plugin is off, say so and offer the substitute; do not run a smaller model and present
the result as the one asked for. If the user typed a value that the model cannot honour, the
server clamps it: report the clamp, do not hide it.

**Escalate blockers in a fixed shape.** What was attempted, the exact error text, the one
likely cause (plugin off, out of memory, model missing, consent missing, dirty git tree), the
fix that needs the user, and what you can do meanwhile. Then stop and wait.

**Recommend, do not list.** When there is a choice, give one recommendation with the reason
and name the alternative. "Wan 2.2 5B for this: it is installed, does image-to-video, and is
the fastest at 24 fps; the 14B is sharper but takes three times longer."

## 5. Human checkpoints

These stop for the user by design. Do not call the releasing route without a clear yes in
the user's own words.

| Checkpoint | Route that releases it | What to say first |
|---|---|---|
| Music video: cut plan approved | `POST /api/music-video/<id>/approve` | number of cuts, seconds per clip, model, total time; offer storyboards first |
| Film Crew: casting confirmed | `POST /api/production/<id>/casting/confirm` | every subject and how it is cast (existing LoRA, new LoRA, as described) |
| Film Crew: storyboard approved | `POST /api/production/<id>/storyboard/approve` | shot count and render cost |
| Voice clone | upload via `/api/audio-foundry/voice-clips/upload` | whose voice, and that they consented; refuse public figures |
| LoRA of a person | `POST /api/cast-library/subjects/<id>/train` | whose face, and that they consented |
| Model or LoRA download | `/models/download`, `/models/user` with `install` | size and licence |
| Swarm launch | `POST /api/swarm/launch` | plan file, agent count, whether the tree is clean, merge mode |
| Swarm merge | `POST /api/swarm/merge` | diffs reviewed, or the user asked for auto-merge |
| Any restart of a plugin or the backend | `POST /api/plugins/<id>/restart` | what is running now and will die |

Outreach never posts from an agent: drafts queue, the user approves in the Studio.

## 6. How the tools behave

- **Queue, then poll.** Over MCP, `generate_image` and `generate_video` return a batch id in
  milliseconds; `generate_music_video` and `start_film_crew` return a project id and stop at
  the first gate. Poll `get_generation_status(batch_id)` every few seconds; it returns
  status, counts and file URLs. Pass `wait_for_result: true` only when the user wants you to
  block, and expect minutes.
- **A failed call carries the reason.** Read it and act on it. 503 is a plugin that is off;
  "out of memory" means another job holds the card or the size is too big; "not ready" names
  the model to install.
- **The GPU is exclusive.** One heavy model owns it. The orchestrator evicts the chat model
  for a render and brings it back; the first call after a switch is slow, and two renders do
  not run at once. Warn before a long batch, and check `inspect_gpu` before blaming a tool.
- **Outputs are files.** Everything lands under `data/outputs/` in the checkout (batches under
  `data/uploads/Images/<batch>/`), is served by the routes the skills name, appears in the
  Studio media library, and is exposed read-only over MCP as `guaardvark://outputs/...`.
  Report the URL and the path; the user decides where it goes next.
- **Nothing leaves the machine.** No upload of the user's files, prompts, references or
  outputs anywhere, ever, unless the user names the destination.

## 7. Prompting rules per model family

Declared next to the models in the registry; the skills repeat them. The ones that bite:

- **Z-Image Turbo, FLUX, Krea 2** read prompts as language. Write a sentence, not a tag list;
  SD-era boilerplate ("masterpiece, correct anatomy") makes these models worse. Quote exact
  on-image text in double quotes.
- **Wan 2.2** needs at least 20 steps; below that it smears. 24 fps for the 5B, 16 fps for
  the 14B; frames follow the `4n+1` rule and the server rounds for you. `lightx2v-4` is the
  4-step Lightning profile on the 14B and is marked experimental.
- **MiniMax H3** generates its own stereo soundtrack and speaks lines written in the prompt;
  pass `audio: true` and put dialogue in the text. About 6.5 minutes for 5 seconds at 864x480
  on a 16 GB card.
- **ACE-Step** wants its tag vocabulary: genre, instruments, mood, tempo, vocal type. Vague
  words drift to the model's prior; use `negative_prompt` to push away from it, and
  `[verse]` / `[chorus]` markers in lyrics.
- **Chatterbox** clones from 10 to 20 seconds of clean speech; more is not better. `auto`
  falls back to Kokoro on a Chatterbox error, and the response says which engine ran.
- **Cast characters** ride on `subject_ids`, never on a trigger word typed into the prompt.

## 8. Conventions

- **Studio pages** the user may prefer to watch: `/images`, `/video?batch=<id>`, Agents >
  Film Crew, the music-video page, the Audio library, Plugins, Jobs (`/tasks`).
- **Batch ids** look like `ImageBatch_MM-DD-YYYY_HHMMSS_NNN`; video and audio jobs have their
  own ids. Always quote the id you were given.
- **Ports:** backend 5000 (macOS 5055), Studio 5173 in development, ComfyUI 8188, Swarm 8210,
  upscaling 8202. Override the backend with `GUAARDVARK_URL`.
- **Skills** live in `.agents/skills/<name>/SKILL.md` (bare job names). In the Claude Code
  plugin they load as `/guaardvark:<name>`; `python -m backend.mcp install --skills` links them
  as `guaardvark-<name>` for the personal folder; Cursor, Codex and OpenClaw read the folder.
- **MCP tool ids** are `mcp__guaardvark__<tool>` after `python -m backend.mcp install`, or
  `mcp__plugin_guaardvark_guaardvark__<tool>` after a marketplace install.

## 9. Communication protocol

- Lead with the result: what was queued or finished, the batch id, the file URL, the model
  that ran. Then one line on what happens next.
- Queued is not done. Running is not done. Say which.
- Quote error text verbatim; do not paraphrase it into optimism.
- Do not restate the whole plan every turn. Progress updates carry what changed.
- Keep the user's vocabulary: Guaardvark is a self-hosted AI studio; the machine is theirs.

## 10. Quick lookup

| The user wants | Skill | Runs through |
|---|---|---|
| an image, an edit, a batch, a consistent character | `image` | MCP `generate_image`, `edit_image`; REST `/api/batch-image` |
| a clip, image-to-video, a clip with sound, a batch | `video` | MCP `generate_video`; REST `/api/batch-video` |
| a music video from a song | `music-video` | MCP `generate_music_video`; REST `/api/music-video` |
| a short film from a script or logline | `film-crew` | MCP `start_film_crew`; REST `/api/production` |
| narration, a line, a cloned voice | `voice` | REST `/api/audio-foundry`, `/api/voice` |
| a song, a beat, a sound effect | `music` | REST `/api/audio-foundry` |
| a bigger or sharper image or video | `upscale` | REST `/api/upscaling` |
| the same face or object across renders, a LoRA | `cast` | REST `/api/cast-library` |
| a model or LoRA from a Hugging Face link | `models` | REST `/models/from-hf`, `/models/user` |
| several agents on one codebase | `swarm` | REST `/api/swarm` |
| an answer from their documents, a memory | `knowledge` | MCP `search_knowledge_base`, `save_memory`, … |
| where something is implemented, a repo map | `code` | MCP `search_codebase`, `get_repository_map`, … |
| a reply drafted for a social thread | `outreach` | MCP `outreach_draft_post` (never posts) |
| GPU, logs, plugins, sync, autoresearch, infographic | `ops` | MCP `inspect_gpu`, `read_logs`; REST `/api/plugins`, … |
| is it done yet | any | MCP `get_generation_status` |
| what can this box do | `setup` | health, plugins, models, `inspect_gpu` |

## 11. What not to do

- Do not render inside your own process, call ComfyUI's port, or import the generation
  libraries; hand the job to the backend and poll it.
- Do not start or stop services outside the plugin routes.
- Do not download a model, clone a voice, train on a face, approve a plan, merge a swarm or
  restart anything without the user's yes for that specific action.
- Do not substitute a model, size, duration or engine and present it as what was asked.
- Do not claim a render is done because the call returned; the status route decides.
- Do not paste the user's files, prompts or outputs into any external service.
- Do not invent a route or a field. If the skill does not name it, it is not there.

---

## 12. Contributing with an agent

For changing Guaardvark itself, `CONTRIBUTING.md` is the contract: quick links, safe versus
high-risk areas, development setup, project structure, tests and lint. Two rules that the
maintainers apply to every change, human or agent:

- **Verify before asserting.** Never state a checkable fact from memory when a command or a
  file read can confirm it. Absence claims ("X does not support …") always need a check.
  Capabilities of models and tools come from `--help`, the registry and the running system,
  not from recollection.
- **No shipping a knob that produces bad output.** A setting, preset or default that gives
  visibly bad results does not ship, not as an option, not behind a label. Fix it at the
  source or remove it, and declare the limit in data next to the model it constrains, with
  the observation it was measured against.

Pull requests disclose that an agent produced them and which one; a human reviews the full
diff before it is opened. Tests that touch the live database never run against it (see
`backend/tests` rules in `CONTRIBUTING.md`). The portability guard in `scripts/` runs on
every commit; a failure names the line to fix.
