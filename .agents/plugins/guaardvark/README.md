# Guaardvark Plugin for Antigravity

Drive a self-hosted Guaardvark AI studio from Antigravity: local image, video, music-video and
Film Crew generation, voice and music, upscaling, LoRA training, Hugging Face model onboarding,
agent swarms, local knowledge, code intelligence and supervised outreach, on your own GPU.

## What is Included

- **15 Agent Skills**:
  - `guaardvark-setup`: Health, plugins, models, GPU inspection.
  - `guaardvark-image`: Text-to-image, instruction edits, background cut-out, inpaint/outpaint, consistent characters.
  - `guaardvark-video`: Text-to-video, image-to-video, camera motions, video batches.
  - `guaardvark-music-video`: Beat-synced music videos with Director storyboard and keyframes.
  - `guaardvark-film-crew`: Screenplay to finished short video with screenwriter, cast, cinematographer, keyframes, editor.
  - `guaardvark-voice`: Voiceover, TTS (Chatterbox, Kokoro, Piper), consent-gated voice cloning.
  - `guaardvark-music`: Song generation with vocals/instrumentals (ACE-Step) and sound effects (Stable Audio).
  - `guaardvark-upscale`: Real-ESRGAN, HAT-L, SwinIR 2x/4x/8x upscaling for image and video.
  - `guaardvark-cast`: Consistent characters, props, Cast Library, local LoRA training.
  - `guaardvark-models`: Onboard Hugging Face checkpoints, LoRAs, and registry models.
  - `guaardvark-swarm`: Parallel coding swarms in isolated git worktrees with deterministic merging.
  - `guaardvark-knowledge`: Local RAG over indexed documents, semantic search, memory across chats.
  - `guaardvark-code`: Semantic code search, repository map, import dependency graph.
  - `guaardvark-outreach`: Supervised social draft queue with human review.
  - `guaardvark-ops`: VRAM inspection, task logs, Celery workers, service lifecycle.
- **MCP Server (`guaardvark`)**: the tools of your local Guaardvark, under its default-deny policy (desktop, shell and browser tools stay hidden unless you allowlist them).
- **Rules (`rules/AGENTS.md`)**: Operating contract, mandatory preflight, GPU tier limits, and decision protocols.

## Installation

### In this workspace

Opening this repository in Antigravity loads the plugin in `.agents/plugins/guaardvark/`.

### Global installation (for any project)

From the Guaardvark checkout:

```bash
agy plugin install .agents/plugins/guaardvark
```

Or run `python -m backend.mcp install --skills` from the checkout, which also saves the checkout's
location for the plugin's server.

## Managing the Plugin

```bash
agy plugin list                 # View installed plugins and status
agy plugin enable guaardvark    # Enable plugin
agy plugin disable guaardvark   # Disable plugin
agy plugin uninstall guaardvark # Uninstall plugin
```

## Suggested Prompts

- *"Generate an image of a cybernetic aardvark in a neon cyberpunk alley"*
- *"Inspect GPU status and resident models in Guaardvark"*
- *"Start a Film Crew production for my short screenplay"*
