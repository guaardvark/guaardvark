---
name: video
description: >-
  Generate video clips on the user's own GPU through Guaardvark: text-to-video,
  image-to-video, first+last frame animation, clips with their own soundtrack and dialogue
  (MiniMax H3), short looping animations, and batch runs. Use when the user asks to make,
  render, animate, or batch-generate video locally.
---

# Video with Guaardvark

Read `setup` first if the backend or the `comfyui` plugin state is unknown.
Video needs a 16 GB-class NVIDIA card. Clips take minutes, so every route is queued.

## Pick the model

`GET ${GUAARDVARK_URL:-http://localhost:5000}/api/batch-video/models` lists the registry with
`installed`, `modes` (t2v, i2v), `max_frames`, `native_fps`, `aspect_ratios`, `min_steps`,
`speed_profiles`, `audio_out`. Installed on a typical box:

| id | what it is | notes |
|---|---|---|
| `wan22-5b` | Wan 2.2 TI2V-5B, t2v + i2v, 24 fps, up to 121 frames | the everyday default |
| `wan22-14b` / `wan22-14b-i2v` | Wan 2.2 14B MoE, 16 fps, 81 frames | best quality; `speed_profile: lightx2v-4` for 4-step Lightning |
| `ltx23-distilled-fp8` | LTX-2.3 distilled, 8 steps, 161 frames | fastest long clips |
| `minimax-h3-int8` | MiniMax H3, text / first / last / first+last frame, **generates its own stereo soundtrack and spoken lines** | pass `audio: true`; ~6.5 min for 5 s on a 16 GB card |
| `cogvideox-5b` / `-i2v` | CogVideoX, 8 fps, 49 frames | legacy |

The active default is `GET /api/settings/active_video_model` (`resolved.t2v`, `resolved.i2v`, `resolved.scene`).
Never pass a step count below the model's `min_steps`; the server raises it and the result would be smeared anyway.

## One clip: MCP `generate_video`

- `prompt`: scene, subject, motion, style, and any spoken lines (H3 speaks them).
- `model`, `duration_s` (clamped to the model), `aspect_ratio` (one the model declares),
  `style` (cinematic, realistic, anime, 3d_animation, ...), `num_inference_steps`, `speed_profile`.
- `audio: true` forces a soundtrack-capable model (H3) and fails on a silent family.
- `first_image` / `last_image`: document id or path; last frame needs a first+last mode model.
- `reference_images` / `reference_audio`: lock a person, look or voice (reference build only).
- `wait_for_result` default false: the tool returns a batch id and a Studio deep link at once.
  Give the user the link; poll `get_generation_status(batch_id=...)` (MCP) or
  `GET /api/batch-video/status/<batch_id>` if they ask you to wait. `wait_for_result: true`
  blocks for the clip, up to 30 minutes.

## Looping animation: MCP `generate_animation`

Frame-morph GIF/MP4 via img2img: `prompt`, `motion`, `frames` 2-24, `strength` 0.1-0.5, `format` gif|mp4|both.
Use for short loops and stickers, not for cinema clips.

## Batch: REST

```bash
B=${GUAARDVARK_URL:-http://localhost:5000}
# text to video, one clip per prompt
curl -s -X POST $B/api/batch-video/generate/text -H 'Content-Type: application/json' -d '{
  "prompts": ["a red kite over a grey sea", "the same kite at dusk"],
  "model": "wan22-5b", "prompt_style": "cinematic", "enhance_prompt": true, "seed": 42
}'
# image to video
curl -s -X POST $B/api/batch-video/generate/image -H 'Content-Type: application/json' -d '{
  "image_paths": ["/abs/path/frame.png"], "prompt": "slow push in, wind in the grass", "model": "wan22-5b"
}'
```
Optional keys the server honours: `negative_prompt`, `guidance_scale`, `motion_strength`,
`interpolation_multiplier` (RIFE frame interpolation), `combine_frames`, `lora_name` +
`lora_strength`, `adapters`, `guides` (per-prompt audio/image anchors on models that declare them),
`last_frame_paths` (image route), `storyboard_concept` / `storyboard_shots`.
Poll `GET $B/api/batch-video/status/<batch_id>`; files via
`GET $B/api/batch-video/video/<batch_id>/<video_name>`; cancel `POST $B/api/batch-video/batch/<batch_id>/cancel`;
retry `POST $B/api/batch-video/retry/<batch_id>`.

## Rules

- Quote the model and the queued batch id back to the user. Never claim a clip is done until the
  status route says so.
- The GPU is exclusive: while video renders, chat models are evicted. Warn before queuing a long batch.
- The Studio page (Video Gen) shows the same queue; the user may prefer to watch it there.
