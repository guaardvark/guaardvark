---
name: guaardvark
description: Guaardvark CLI for chatting with the local AI assistant, running agents, generating images/videos, and managing RAG, files, tasks, and backups from the terminal.
---

# Guaardvark CLI

Guaaardvark is a self-hosted, offline-first AI workstation. The `guaardvark` CLI (package `guaardvark`, command alias `llx` in the source tree) is a terminal front-end for the same Flask backend that powers the web UI and MCP server.

## Installation & binary

- Installed via PyPI: `pip install guaardvark`
- Binary: `guaardvark` (on PATH, e.g. `~/.local/bin/guaardvark`). The source-tree alias is `llx` (`cd cli && pip install -e .`).
- The CLI talks to a running Guaardvark backend (default `http://localhost:5000`, or the `FLASK_PORT` you configured — e.g. `5055`). Start services with `./start.sh` (or `guaardvark start`).

## Global options (apply to most commands)

```bash
guaardvark --json ...        # machine-readable JSON output (scripting)
guaardvark --server <url>    # override server URL, e.g. -s http://localhost:5055
guaardvark --timeout <sec>   # request timeout
guaardvark --non-interactive # do not drop into the REPL when no subcommand given
```

## Core commands

### Status & system
```bash
guaardvark status          # system dashboard / health
guaardvark health          # health check
guaardvark doctor          # run environment health check (or repair)
guaardvark start | stop    # start / stop Guaardvark services
guaardvark models          # LLM model management (list active models)
```

### Chat & AI assistant

```bash
guaardvark chat "explain this codebase"        # chat with the LLM (with RAG context)
guaardvark chat --resume                        # continue last conversation
guaardvark chat --session <id>                  # resume a specific session
guaardvark chat --list                          # list recent sessions
guaardvark chat --export --session <id>         # export conversation to markdown
guaardvark chat --no-rag                        # disable RAG context
guaardvark chat --project <id>                  # scope RAG to a project
echo "hi" | guaardvark chat                     # piped input
guaardvark ask "one-shot question"              # one-off message (no REPL)
```

### Agents

```bash
guaardvark agents list                          # list configured agents
guaardvark agents info                          # agent details + available tools
guaardvark agents run --agent <name> "prompt"   # run an agent (auto-selects best if omitted)
guaardvark agents update                        # update agent config
guaardvark recipes list | show | validate       # inspect/validate agent recipes
```

### Media generation

```bash
guaardvark images list                          # list image batches
guaardvark images generate "a red fox"          # generate images from a prompt
guaardvark images status <batch>                # batch generation status
guaardvark images models                        # list image models
guaardvark images delete <batch>                # delete a batch
guaardvark videos generate "prompt"             # generate video from text
guaardvark videos from-image <img> "prompt"     # video from a source image
guaardvark videos status | models | list        # video batch info
guaardvark generate image "prompt" | csv "..."   # convenience generate helpers
```

### RAG / search / files

```bash
guaardvark search "query"                       # semantic search over indexed docs
guaardvark index <path>                          # index files/dirs for RAG
guaardvark rag                                   # RAG index inspection/evaluation
guaardvark files ...                             # file and folder management
```

### Operations

```bash
guaardvark backup                                # backup / restore
guaardvark tasks                                 # create, run, monitor tasks
guaardvark jobs                                  # background job management
guaardvark logs                                  # log viewing / analysis
guaardvark outreach                            # social outreach (status, queue, approve)
guaardvark settings                              # application settings
guaardvark family                                 # Interconnector family network
```

## Interactive REPL slash commands

Running bare `guaardvark` starts a REPL:

```
/imagine <prompt>       generate an image
/video <prompt>         generate a video
/voice <text>           text-to-speech
/agent                  toggle autonomous agent mode
/web                    open the web UI
/ingest <path>          index files/directories for RAG
/search <query>         semantic search
/models list            list available Ollama models
/remember <text>        save to persistent memory
/backup create          create a system backup
/jobs list|watch       monitor background tasks
/config                view/change settings
/help                  full command reference
```

## Film Crew: converting a Fountain/visual-novel script

Use the **Film Crew** page in the web UI (not the CLI) for the sequential Screenwriter → Casting → Cinematographer → Storyboard → Editor pipeline.

### Goal
Take a Fountain-style script (or visual novel text with `SFX`, `MUSIC`, `CHARACTER`, `IMAGE`, and choice branches) and rewrite it into a linear Guaardvark Film Crew script the Screenwriter can break into scenes, shots, and subjects.

### Step-by-step conversion rules

1. **Remove Fountain metadata.** Keep Title/Author/Credit as plain text at the top if useful, but strip Fountain syntax like `INT.`, `EXT.`, scene headings, and slugline parentheses.
2. **Linearize the story.** Film Crew renders a single video timeline, so convert choice branches (`* Fight it!`, `* Run!`) into one chosen path or into separate productions. Do not leave branching choices in the script.
3. **Mark recurring visual identities with `[[Name]]`.**
   - `[[Elara]]` → the Screenwriter extracts Elara as a `character` and sets `cast_required = True`. The production pauses at the Casting stage until you assign or train a LoRA for her so she looks consistent in every shot.
   - `[[Corrupted Wolf]]` → same, for any creature/prop you want visually locked.
4. **Mark inline/generated assets with `{{Name:kind}}`.**
   - `{{Lumin Seed:prop}}`, `{{Satchel:prop}}`, `{{Knife:prop}}` → generated per shot, no LoRA gate.
   - `{{Old Cabin:environment}}`, `{{Forest Path:environment}}` → generated as set dressing per shot.
5. **Add explicit shot lines.** Replace free prose with `SHOT # - ANGLE: description` lines so the Cinematographer gets clear camera setups, e.g.:
   - `SHOT 1 - WIDE: ...`
   - `SHOT 2 - MEDIUM: ...`
   - `SHOT 3 - CLOSE-UP: ...`
6. **Preserve audio cues.** Keep `SFX:` and `MUSIC:` notes; the Editor can use them for sound design and scoring.
7. **Add a `FADE IN:` / `FADE OUT.` bookend.** Optional but helps the pipeline infer the production boundaries.

### Markup quick reference

| Markup | Meaning |
|--------|---------|
| `[[Name]]` | Pin as a recurring cast member (trainable LoRA). Default kind is `character`. |
| `[[Name:prop]]` | Pin as a recurring prop/trainable subject (e.g., a hero prop you want consistent). |
| `[[Name:environment]]` | Pin as a recurring location/set. |
| `{{Name:character}}` | Force kind override to `character` without pinning (no LoRA gate). |
| `{{Name:prop}}` | Force kind override to `prop`, generated inline. |
| `{{Name:environment}}` | Force kind override to `environment`, generated inline. |

### Example: "The Last Spark"

See the converted sample script at:

```text
docs/film-crew-scripts/the_last_spark.tmplt.txt
```

It demonstrates:
- `[[Elara]]` and `[[Corrupted Wolf]]` as trainable cast members.
- `{{...:prop}}` and `{{...:environment}}` for inline assets.
- Reordered scenes (forest confrontation first for tension, cabin as optional prelude).
- Explicit `SHOT # - ANGLE:` breakdowns.

### How to run it

1. Open the Guaardvark web UI → **Film Crew**.
2. Click **New Production**.
3. Paste the converted script into **Script Text**.
4. Set the production name and click **Roll Cameras**.
5. The Screenwriter extracts scenes/shots/subjects. Resolve any `[[...]]` cast members in the Casting stage, then approve the storyboard.

## Notes

- The CLI reads model/provider settings from the backend (e.g. the active LLM provider — local Ollama, Mistral, or OpenAI-compatible — configured in the web UI or via `POST /api/llm/provider`).
- For scripting/automation, prefer `--json` and `--non-interactive`.
- Media generation and model availability depend on the backend's configured providers (local Ollama/ComfyUI, or cloud LLM providers).
