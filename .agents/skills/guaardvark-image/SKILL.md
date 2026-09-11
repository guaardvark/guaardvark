---
name: guaardvark-image
description: >-
  Generate or edit images on the user's own GPU through Guaardvark: single images,
  instruction edits, consistent characters from the Cast Library, and batch runs of many
  prompts. Use when the user asks to create, draw, render, visualize, edit, or
  batch-generate images locally.
---

# Images with Guaardvark

Read `guaardvark-setup` first if the backend or the `comfyui` plugin state is unknown.

## One image: MCP `generate_image`

- `prompt` is scene, pose, lighting, setting. Plain prose. Do not paste JSON or tag soup; the
  default model (Z-Image Turbo) reads prompts as language, and SD-era tag lists hurt it.
- `model` default `auto` picks the best downloaded model. Only override when the user names one:
  `zimage-turbo`, `krea2-turbo`, `krea2-raw`, `flux-dev`, `sd-xl`, `sdxl-turbo`,
  `realistic-vision`, `epic-realism`.
- `width` / `height`: 512, 768 or 1024. `style`: realistic, artistic, anime, photographic, digital-art.
- **Consistent character**: pass `subject_ids=[<cast id>]` as its own array. Never put the
  trigger word alone in the prompt and expect the LoRA to load. Find ids with
  `GET /api/cast-library` (see guaardvark-cast).
- On-image text: quote the exact words in double quotes inside the prompt.
- The tool returns the image URL (`/api/outputs/generated_images/<file>.png`, relative to the
  backend), the model that ran, steps, seed and whether a Cast LoRA was applied. Show the URL
  and the prompt you used. Measured: 768x768 on Z-Image Turbo in ~20 s on a free 16 GB card.
- **An empty result means the MCP call timed out** (30 s per call by default,
  `GUAARDVARK_MCP_TIMEOUT` raises it). It happens when the GPU is busy with another job:
  check `inspect_gpu`, wait for that job, then retry; or queue through the REST batch route
  below, which returns at once and is polled.

## Edit an existing image: MCP `edit_image`

- `instruction` is the change ("put a cowboy hat on him", "make the shirt red"). The image the
  user just attached is used automatically; otherwise pass `image` as a path or URL.
- `model` `auto` uses FLUX.1 Kontext when installed, else img2img on the current model.
- For a brand-new picture use `generate_image`, not this.

## Many images: REST batch

```bash
B=${GUAARDVARK_URL:-http://localhost:5000}
curl -s -X POST $B/api/batch-image/generate/prompts -H 'Content-Type: application/json' -d '{
  "prompts": ["prompt one", "prompt two"],
  "model": "auto",
  "subject_ids": []
}'
```
- `prompts` may be strings or `{"prompt": "..."}` objects. There is a per-batch maximum; if the
  server answers 400 "Too many prompts", split the list.
- Optional `adapters` (user LoRAs from guaardvark-models) and `subject_ids` (Cast Library).
- The response is `data.batch_id` (`ImageBatch_<date>_<n>`). Poll
  `GET $B/api/batch-image/status/<batch_id>?include_results=true`: `status` goes
  running → completed, with `completed_images` / `total_images`, `output_dir`, and one
  `results[]` entry per prompt (`success`, `image_path`, `thumbnail_path`, `generation_time`,
  `metadata.model_used`). A contact sheet: `GET $B/api/batch-image/preview/<batch_id>`; one file:
  `GET $B/api/batch-image/image/<batch_id>/<image_name>` (the basename of `image_path`).
  Cancel with `POST $B/api/batch-image/cancel/<batch_id>`. Measured: one 1024x1024 prompt
  completed in ~30 s.
- Helpers: `POST /api/batch-image/enhance-prompt`, `/analyze-prompt`, `/expand-concept` (JSON
  body with the prompt) when the user wants prompt help before spending GPU time.

## Rules

- Say which model actually ran (the response names it). Do not promise a model that is not installed.
- Generation time depends on the GPU; a first image after Ollama held the card can take longer
  because the orchestrator swaps models. That is normal.
- Never upload the user's images anywhere. Everything here is local.
