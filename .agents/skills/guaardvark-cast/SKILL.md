---
name: guaardvark-cast
description: >-
  Build consistent characters, environments and props in Guaardvark's Cast Library and
  train LoRAs for them locally (reference photos → vision bible → sample plan → approved
  samples → training). Use when the user wants the same face or object across images,
  videos, a music video, or a Film Crew production, or asks to "train a LoRA".
---

# Cast Library and LoRA training

Read `guaardvark-setup` first; training needs the `lora_trainer` plugin (CUDA, bf16) and an
installed train-ready base (Z-Image, SDXL or FLUX family). `B=${GUAARDVARK_URL:-http://localhost:5000}`.

## The pipeline

1. **Create the subject**
   ```bash
   curl -s -X POST $B/api/cast-library/subjects -H 'Content-Type: application/json' -d '{
     "kind": "character", "name": "Mara", "description": "late 30s, short grey hair, freckles",
     "trigger_word": "mara_v1", "voice_id": null
   }'
   ```
   `kind` is `character`, `environment` or `prop`. `GET $B/api/cast-library` lists subjects; the
   numeric `id` is what `generate_image` and batch routes take as `subject_ids`.
2. **Upload reference images** (5 to 20 clear shots, varied angles, same subject):
   `curl -s -X POST $B/api/cast-library/subjects/$ID/upload-refs -F files=@1.jpg -F files=@2.jpg`
   Ask first whether the person consented; do not train on someone who has not.
3. **Vision bible from the refs**: `POST $B/api/cast-library/subjects/$ID/bible/from-refs`
   (the vision model writes the identity description that every prompt inherits).
4. **Plan and generate training samples**: `POST .../$ID/plan` then `POST .../$ID/generate`
   (cancel with `.../generate/cancel`). Review `GET .../$ID/samples`; view one with
   `GET .../samples/<sample_id>/image`; drop bad ones with `DELETE .../samples/<sample_id>` or
   `POST .../samples/<sample_id>/regenerate`.
5. **Approve samples**: `POST .../$ID/samples/approve`.
6. **Train**: `POST $B/api/cast-library/subjects/$ID/train` with optional
   `{"training_settings": {...}}`. A 409 `already_training` means wait; a
   `train_base_not_ready` error names the base model to install first. Cancel: `.../train/cancel`.
   Progress: `GET $B/api/cast-library/subjects/$ID` (status, base model, LoRA path when done).
7. **Use it**: `generate_image` with `subject_ids=[ID]`; batch image/video routes with
   `subject_ids`; the Film Crew casts it; the music-video Director locks it per cut.

## Rules

- Training is a GPU job of tens of minutes; say so and check `inspect_gpu` for conflicts first.
- The trigger word is applied by the system when `subject_ids` is passed; the user does not need
  to type it.
- Everything stays local: refs, samples and the LoRA file under `data/`.
