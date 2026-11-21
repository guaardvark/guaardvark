# PAN 2 prompt bundle

Data the PAN 2 caption compiler (`backend/services/pan2_prompt_compiler.py`) reads.

- `negative.json` — the negative caption from the reference image-to-video and
  text-to-video workflows in https://huggingface.co/BennyDaBall/PAN2-NVFP4-ComfyUI
  (Apache License 2.0), copied unchanged. PAN 2 reads its negative in the same
  JSON shape as the positive caption.

## The caption format

PAN 2 is prompted with one JSON object: `video_style`, `lighting`,
`perspective_view`, `subjects_lifecycle` (subjects tagged `<|Subject1|>` and so
on), `init_frame_visual_description`, `events` (each with `time_range_sec`,
`event_description`, `camera_motion` and `contained_actions`) and `quality`.
A caption a person writes in that shape is sent as written.
