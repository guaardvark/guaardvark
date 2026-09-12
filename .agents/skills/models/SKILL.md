---
name: models
description: >-
  Add any Hugging Face image or video model, checkpoint or LoRA to Guaardvark from a URL,
  list what is installed, and download registry models on request. Use when the user
  pastes a huggingface.co link or a .safetensors URL, asks "can it run <model>", or wants
  a new LoRA or checkpoint available in the Studio.
---

# Models and LoRAs with Guaardvark

`B=${GUAARDVARK_URL:-http://localhost:5000}`. Nothing downloads without an explicit Install;
the product never phones home on its own. Always confirm the size and the licence with the user first.

## What is there

- Video: `GET $B/api/batch-video/models` (registry + user catalog, with `is_downloaded` / `is_ready`, `missing_files`, `capabilities`).
- Image: `GET $B/api/batch-image/models`.
- Download a registry model: `POST $B/api/batch-video/models/download {"model_id": "wan22-14b"}` /
  `POST $B/api/batch-image/models/download {"model_path": "<id>"}`; progress at
  `GET .../models/download-status`.

## Add from a Hugging Face URL (the "paste a link" path)

1. Inspect: `POST $B/api/batch-video/models/from-hf {"url": "https://huggingface.co/<org>/<repo>"}`
   (or the `batch-image` twin). The server reads the repo and returns what it found: files,
   revision, the likely `role` (checkpoint, LoRA, VAE, text encoder), `family`, whether a
   `model_index` exists, and a proposed catalog entry.
2. Show that to the user: which file(s), role, family, size, licence.
3. Register (and optionally install) with `POST $B/api/batch-video/models/user` (or `batch-image`)
   sending the entry back, e.g.
   ```json
   {"url": "...", "hf_repo": "org/repo", "revision": "main", "files": ["model.safetensors"],
    "role": "lora", "family": "wan22", "name": "My LoRA", "description": "...", "install": true}
   ```
   `role` and `family` decide where the Studio offers it (a Wan LoRA appears under Wan models;
   an SDXL checkpoint under image models). Video entries also take `like: "<registry id>"` to
   inherit that model's capabilities.
4. Remove: `DELETE .../models/user/<model_id>` with `{"delete_files": true|false}`.

## Using a user LoRA

- Batch image: `adapters: [{"id": "<user model id>", "scale": 0.8}]` on `/generate/prompts`.
- Batch video: `lora_name` + `lora_strength`, or `adapters` on `/generate/text`.
- A trained Cast LoRA is different: it rides on `subject_ids` (the cast skill).

## Rules

- A direct `.safetensors` URL works when it lives on huggingface.co; the inspector needs the repo
  to read the file list. For other hosts, ask the user to download the file and drop it in the
  Studio's model folder instead.
- Declared limits (min steps, max frames) come from the registry entry; a user model inherits
  them from `like`/`family`. Do not invent numbers for a model the registry does not know.
