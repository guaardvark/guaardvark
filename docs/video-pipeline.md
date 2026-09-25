# Video pipeline: from prompt to finished file

This is a trace of what happens to a video request, from the moment it enters Guaardvark to the
MP4 on disk, for every family in `backend/services/video_model_registry.py`. It ends with a list of
findings: places where the code does something a caller would not expect.

This document describes behaviour. It does not change any. Line numbers are against commit
`e18cdb2` and will drift; the function names are the stable handle.

Abbreviations used in the tables:

| Short | File |
|---|---|
| **G** | `backend/services/comfyui_video_generator.py` |
| **W** | `backend/services/comfyui_video_workflows.py` |
| **R** | `backend/services/video_model_registry.py` |
| **B** | `backend/services/batch_video_generator.py` |
| **API** | `backend/api/batch_video_generation_api.py` |
| **T** | `backend/tools/image_tools.py` |

Contents:

1. [Entry points](#1-entry-points)
2. [The shared spine](#2-the-shared-spine)
3. [Wan 2.2](#3-wan-22-t2v-14b-i2v-14b-ti2v-5b)
4. [LTX 2.3 and 2.5](#4-ltx-23-and-ltx-25)
5. [HunyuanVideo](#5-hunyuanvideo)
6. [MiniMax H3](#6-minimax-h3)
7. [CogVideoX](#7-cogvideox)
8. [SVD](#8-svd)
9. [Music video and Film Crew](#9-music-video-and-film-crew-render-stages)
10. [Defaults per entry point](#10-defaults-per-entry-point)
11. [Findings](#11-findings)
12. [How this was checked](#12-how-this-was-checked)

---

## 1. Entry points

| Entry | Code | Queue | Model resolution | Preflight | GPU admission |
|---|---|---|---|---|---|
| MCP / chat `generate_video` | `T:VideoGeneratorTool.execute`. Over MCP it forwards to `POST /api/tools/execute` (`backend/utils/backend_http.py:run_tool_in_backend`), which runs the same tool in the backend | `BatchVideoGenerator` | `T:resolve_request` → `resolve_active_video_model(role)` | `prepare_video_model` (starts ComfyUI if that is all it lacks) | batch `gpu_session` (see §2) |
| Studio `POST /api/batch-video/generate/text` | `API:generate_text_to_video_batch` | `BatchVideoGenerator` | `API:_resolve_request_model` → `resolve_active_video_model("t2v", body.model)` | `prepare_video_model` | batch `gpu_session` |
| Studio `POST /api/batch-video/generate/image` | `API:generate_image_to_video_batch` | `BatchVideoGenerator` | `resolve_active_video_model("i2v", body.model)` | `prepare_video_model` | batch `gpu_session` |
| Studio `POST /api/batch-video/<id>/retry` | `API:retry_batch` | `BatchVideoGenerator` | the stored `retry_data.params.model` | **none** | batch `gpu_session` |
| Music video, `clip_generator` stage | `backend/tasks/music_video_tasks.py:_generate_one_clip` | none: one clip per Celery call, self-redispatching | `resolve_active_video_model("i2v", …, surface="music-video")` at create time | `preflight_video_model` at create time only | its own `gpu_session` per clip |
| Film Crew, `editor` stage | `backend/tasks/production_swarm_tasks.py:run_editor` → adapters at the bottom of G | none | `resolve_active_video_model("i2v", …, surface="film-crew")` at render time | `preflight_video_model` (no auto-start) | one `gpu_session` for the whole render |

All paths end in `ComfyUIVideoGenerator.generate_video` (G:1803). The batch path reaches it
through `VideoGenerationRouter.generate_video` (`backend/services/video_generation_router.py`).
The router can fall back to `OfflineVideoGenerator` (diffusers, CogVideoX only) when
`GUAARDVARK_VIDEO_BACKEND` is `auto` (the default) and ComfyUI cannot be started. Music video and
Film Crew call `comfyui_video_generator.get_video_generator()` directly, so they have no offline
fallback.

### 1.1 MCP tool: `T:resolve_request` (pure)

1. **Model.**
   - The explicit `model` wins.
   - Otherwise `resolve_active_video_model("i2v" if first_image else "t2v")`. Its error is
     discarded and `DEFAULT_T2V_MODEL` (`wan22-5b`) is used instead (T:1122-1123).
2. **Capability refusals**, each a one-sentence error:
   - `audio` on a model without `audio_out`.
   - References on a model without `ref2v`.
   - `last_image` without `l2v` or `flf2v`.
   - `first_image` on a T2V-only model with no I2V sibling. When a sibling exists, the tool swaps
     to it (`i2v_model_for`).
3. **Frames.**
   - `duration_s × native_fps` if given, else `duration_frames` (default 49).
   - Clamped to `[max(round(min_clip_s × fps), 9), max_frames]` (T:1164).
4. **Steps.** An explicit value is clamped to 1..100 and raised to `min_steps`, and
   `metadata.steps_explicit=True` is set. Otherwise the tool uses `default_steps`.
5. **Aspect ratio.**
   - Must be one the model declares.
   - `_dims_for_ratio` then sizes the frame inside `min(max_pixel_area, 864×480)` (T:954).
   - Without `aspect_ratio`, width and height are left unset and filled later from
     `clip_defaults_for`.
6. **Style.** `style` becomes `prompt_style`. `style="none"` also sets `enhance_prompt=False`
   (T:1204).

`execute` then:
- resolves document ids to paths (`_media_path`);
- calls `prepare_video_model`;
- queues with `start_batch_from_prompts`, or with `start_batch_from_images` when `first_image` is
  given;
- returns immediately, or polls for up to 30 minutes when `wait_for_result=true`.

### 1.2 Studio REST: `API:generate_*_video_batch`

- `_clip_params` (API:140) fills `duration_frames`, `fps`, `width`, `height` and
  `num_inference_steps` from `clip_defaults_for(model)` when the body omits them.
- Every other knob is parsed from the body with a fixed default:

  | Knob | Default |
  |---|---|
  | `guidance_scale` | 7.5 |
  | `interpolation_multiplier` | 2 |
  | `prompt_style` | `cinematic` |
  | `enhance_prompt` | on |
  | `motion_strength` | 1.0 |
  | `lora_strength` | 1.0 |

- `upscale`, `teacache_threshold`, `feta_weight`, `high_consistency` and `steps_explicit` travel in
  `metadata`.
- **Storyboard mode** (`storyboard_concept` plus `storyboard_shots`) queues N copies of the concept.
  The worker rewrites them into N shots.

### 1.3 The batch queue: `B:_start_batch` → `_queue_worker` → `_run_batch` → `_run_batch_inner`

| Stage | Where | What it decides |
|---|---|---|
| Build request | B:`_start_batch` | Fills anything still missing from `clip_defaults_for(model)`. Stores `retry_data`. Persists `batch_metadata.json`. Enqueues. |
| GPU admission | B:`_run_batch` (B:748) | `gpu_session(JobKind.VIDEO_RENDER, on_busy="wait", require_fit=True, evict_ollama=True, free_comfyui=True, vram_estimate_mb=vram_mb_for_model(model))`. A busy refusal retries with backoff until `GUAARDVARK_VIDEO_VRAM_WAIT_S` (default **5400 s**). A capacity refusal is terminal. |
| Cast resolution | B:`_run_batch_inner` | `subject_ids` → cast LoRAs and a lock prefix (`cast_lock.subjects_to_lock`), plus an optional approved still (`metadata.keyframe_sample_id`). |
| Verbatim check | B:974 | Settings → Verbatim Prompts turns off both the director and the enhancer. |
| Storyboard / director | B:`_apply_storyboard`, `_apply_director` | Rewrites prompts. **When the rewrite changes anything, `enhance_prompt` is switched off** (see finding F-17). |
| Cinematic keyframe | B:`_generate_keyframe_still` | Applies when `cinematic_keyframe`, cast is selected, or an approved still exists, and the item has no image of its own. Renders a still, then animates it with `i2v_model_for(model)` (see F-21). |
| I2V auto-caption | B:`_caption_image_for_i2v` | An image item with no prompt gets a VLM caption, or a motion-only prompt. |
| Per-item request | B:1095 | Builds `VideoGenerationRequest` from the batch fields and hands it to the router. |
| Quality stats | B:`_attach_quality_metrics` | Basic stats. For cinematic runs, an identity score against the keyframe. For high-consistency or cinematic runs, a VLM review. Fail-open; never fails an item. |
| Status | B:1266 | `completed` when no item failed, else `error`. An item counts as completed when `result.success` is true, even without a `video_path`. |
| Registration | B:1305 | Every `*.mp4` under the batch folder becomes a Document with `model=batch_request.model`. |

---

## 2. The shared spine

Every request passes through `G:generate_video` in this order, whatever the family.

| # | Where | Inputs | What it decides |
|---|---|---|---|
| 1 | G:1806 | `service_available` | Stops if ComfyUI cannot be reached. |
| 2 | G:1816 | `/object_info` | Stops without `VHS_VideoCombine`. |
| 3 | G:1833-1868 | prompt, `enhance_prompt`, `prompt_style`, `fidelity_mode`, `motion_strength`, width/height (before clamping), duration, first/last-frame flags, `language`, `h3_intent` | Runs `enhance_video_prompt(model_family=_model_family(model))` (`backend/utils/prompt_enhancer.py`). Family motion terms exist for `wan`, `cogvideox` and `ltx`; Hunyuan gets the default terms. MiniMax is handed to `h3_prompt_compiler.enhance_for_family`. **The default negative prompt is only filled in here** (G:1862), so no enhancer means no default negative. Skipped under Verbatim Prompts; a failure only warns. |
| 4 | G:1870-1905 | `output_dir`, `metadata.item_id` | Creates `<batch>/<item>/{videos,frames,thumbnails}`. |
| 5 | G:1915-1917 | `metadata.image_path`, seed | The start image comes **only** from `metadata["image_path"]`; MiniMax also reads `first_frame_path`. A missing seed becomes `time_ms % 2^31` and is not reported back. |
| 6 | G:1920 `_ensure_comfyui_reserve_for` | registry `comfyui_reserve_vram_gb` | Restarts ComfyUI with the model's `--reserve-vram`: Wan 14B 1.0, MiniMax H3 5.0. Waits up to 600 s for a busy queue, then **continues with the wrong reserve** and a warning. |
| 7 | G:1928 `_vram_preflight` | `min_vram_gb` (registry, else `MODEL_MIN_VRAM_GB` in G) | Refuses a card below the floor minus 0.5 GB. Fails open if the probe breaks. |
| 8 | G:1934 `_ensure_vram_for_model` | `vram_mb` | Books VRAM with `gpu_memory_orchestrator` (`hard_fit`). Waits with backoff up to `GUAARDVARK_VIDEO_VRAM_WAIT_S`, default **600 s** here. |
| 9 | G:1944 `_clamp_aspect_ratio` | registry `aspect_ratios` | Keeps any ratio within 6% of a declared one. Otherwise reshapes to the nearest ratio in the same orientation, at equal area. Warning only. |
| 10 | G:1947 `_clamp_pixel_area` | entry `max_pixel_area`, else FAMILY_SPECS; `duration_tiers` | Scales down to the cap. A MiniMax clip over 175 frames is held to 864×480. Warning only. |
| 11 | G:1950 `_align_dimensions` | **family** alignment table | Rounds to the nearest multiple: Wan 16, LTX 32, Hunyuan 16, MiniMax 32, Cog 16. Does not read the entry's `dimension_alignment` (F-11). |
| 12 | G:1957-2367 | model id | Picks the family branch and builds the graph (§3-§8). Anything unrouted gets "Unsupported video model". |
| 13 | G:2370 | `metadata.upscale` | Adds `UpscaleModelLoader(RealESRGAN_x2.pth)` and `ImageUpscaleWithModel` in front of VHS. Placed after RIFE. |
| 14 | G:2384 | `face_restore` | Adds CodeFormer through `FaceRestoreCFWithModel`, if the nodes exist and `codeformer` is installed; otherwise a warning and the clip renders without it. |
| 15 | G:2417 | `freeu` | Wires only onto a CogVideoX loader node, and on CogVideoX it deliberately skips. **No family gets FreeU** (F-1). |
| 16 | G:2450 | `lora_name`, `lora_strength` | Same pattern: **no family applies it** (F-2). |
| 17 | G:2494 | `generate_frames_only` | Adds a `SaveImage` PNG sequence next to the MP4. The MP4 is still made. |
| 18 | G:2503-2536 | workflow | Starts the progress bridge (`/ws`), then `POST /prompt`. A queue error is reported with its text. |
| 19 | G:2541-2570 | frames, `request.num_inference_steps`, upscale, size, `frames_per_batch`, interpolation | Soft timeout (base 1200 s for Wan, 600 s otherwise, scaled) and hard ceiling of at least 3 h. `_wait_for_completion` polls `/history`, extends while ComfyUI or the GPU is busy, and gives up on an orphaned prompt. |
| 20 | G:2577-2604 | outputs | `_download_result` fetches `gifs` and `images`. The primary file is the first video, else the first file. `_looks_like_blank_video` rejects a missing file, one under 10 KB, or one at least 95% black; it fails open. |
| 21 | G:2605-2640 | | `video_path` relative to the batch folder, thumbnail, and Documents registration unless `metadata.batch_controlled`. |
| 22 | G:2651-2677 | exception | OOM text marks `service_available=False`. `finally` releases the VRAM booking. |

`_model_family` (G:449) maps any id it does not recognise to `cogvideox`. That drives alignment,
the VRAM floor and the enhancer hints even for an id that step 12 later rejects.

### Optional nodes, shared by the builders (W)

| Helper | Adds | Used by |
|---|---|---|
| `_add_rife_interpolation` (W:2421) | `RIFE VFI(rife49.pth)` before VHS, and sets `frame_rate = fps × multiplier` | every builder, when `interpolation_multiplier > 1` |
| `_add_upscale_node` (W:2475) | RealESRGAN ×2, with no install check | the spine, step 13 |
| `_add_face_detailer_node` (W:2643) | CodeFormer, fidelity 0.5 | the spine, step 14 |
| `_add_freeu_node` (W:2523) | `FreeU_V2` | unreachable (F-1) |
| `_add_lora_loader` (W:2619) | `LoraLoader` | unreachable (F-2) |
| `_stack_user_loras` / `_chain_model_only_loras` (W:2549 / 2598) | `LoraLoaderModelOnly` chain for `adapters` | Wan, LTX, Hunyuan, MiniMax. Not Cog. |
| `_apply_declared_attention` (W:2581) | `ModelAttentionBackend` pin from registry `attention` | Wan 14B builders only |
| `_add_av_audio_decode` (W:2401) | `LTXVAudioVAEDecode` → VHS `audio` | LTX, when registry `audio_out` is true (it is false on both shipped entries) |
| `_add_frame_export` (W:2674) | `SaveImage` | the spine, step 17 |
| `_build_vae_decode_node` (W:2373) | `VAEDecodeTiled` when either side is at least 720 px, else `VAEDecode` | Wan, LTX |

---

## 3. Wan 2.2: t2v 14B, i2v 14B, TI2V 5B

**Registry.**

| | `wan22-14b` | `wan22-14b-i2v` | `wan22-5b` |
|---|---|---|---|
| Alignment | 32 | 32 | 32 |
| Max pixel area | 1,000,000 | 1,000,000 | 1,000,000 |
| Aspect ratios | 16:9, 9:16, 1:1 | same | same |
| Steps (min / default) | 20 / 25 | 20 / 25 | 20 / 20 |
| Native fps / max frames | 16 / 81 | 16 / 81 | 24 / 121 |
| `--reserve-vram` | 1.0 | 1.0 | none |
| Speed profiles | `lightx2v-4` (4 steps, cfg 1, shift 5, one LoRA per expert) | same | none |
| Attention pin | pytorch | pytorch | none |

- `wan_comfyui_map` (R:2273) decides I2V by whether the id **contains** `i2v`.
- The 5B model is "single" because it has no HighNoise/LowNoise files.
- The aliases `wan22` and `wan2.2` route to `wan22-14b`.

### Stages after the spine

| Stage | 14B T2V | 14B I2V | 5B |
|---|---|---|---|
| Node check | needs `UnetLoaderGGUF` | needs `UnetLoaderGGUF` | none |
| Speed profile | G:`_resolve_wan_profile`: profile steps (unless `steps_explicit`), cfg, shift, LoRA pair | same | only validated; 5B declares no profiles |
| Adapters / encoder | `_resolve_adapters`, `resolve_text_encoder` | same | same |
| Image | `metadata.image_path` is an **error** | required; uploaded via `/upload/image` | uploaded **if the file exists**, else silently T2V |
| Builder | W:`_create_wan22_t2v_workflow` (W:458) | W:`_create_wan22_i2v_workflow` (W:702) | W:`_create_wan22_5b_workflow` (W:887) |
| Sampler | two `KSamplerAdvanced`, experts split at `steps // 2`, `euler`/`simple`; the low-noise pass has `noise_seed` 0 | same split; `euler` hard-coded whatever the profile | one `KSampler` with the profile's sampler (`uni_pc` for "official") |
| Shift | profile shift, else `_wan_dynamic_shift(w,h)`, clamped to 3..12 | profile shift, else the sampler-profile shift (8.0 for "official"), else dynamic | sampler-profile shift |
| Latent | `EmptyHunyuanLatentVideo` | `WanImageToVideo(start_image)` | `Wan22ImageToVideoLatent` (+`start_image`) |
| Text encoder | `CLIPLoader(type=wan)`; runs on CPU at ≤20 GB total or when the probe fails (`_wan_clip_device`) | same | same |
| LoRAs | profile LoRA per expert; adapters on **both** experts at the same strength | same | adapters from id 20 |
| Attention | `_apply_declared_attention` | same | none |

### Parameter map

In the tables below, "enh" means the prompt enhancer (§2 step 3).

| `VideoGenerationRequest` field | Where it ends up |
|---|---|
| `prompt` | enh, then `CLIPTextEncode.text` (node 5) |
| `negative_prompt` | node 6. If empty: the enhancer default, else the builder's own default |
| `duration_frames` | latent `length`, **not snapped to 4n+1 and not capped at `max_frames`** |
| `fps` | `VHS_VideoCombine.frame_rate` (× RIFE) |
| `width`, `height` | spine clamps, then the latent node; also the shift and the decode tiling |
| `num_inference_steps` | sampler `steps` and the expert split. Replaced by profile steps unless `steps_explicit`. **5B ignores the profile.** No `min_steps` floor |
| `guidance_scale` | sampler `cfg`. Replaced by the profile cfg. 7.5 by default, against the family `guidance` 3.5 |
| `seed` | high-noise sampler `noise_seed` (or 5B `KSampler.seed`) |
| `interpolation_multiplier` | RIFE |
| `wan_sampler_profile` | 5B: sampler and shift. 14B I2V: **shift only**. 14B T2V: **ignored** |
| `speed_profile` | 14B: steps, cfg, shift, LoRAs. 5B: validated, then unused |
| `adapters`, `text_encoder` | `LoraLoaderModelOnly` chain; `CLIPLoader.clip_name` |
| `face_restore`, `metadata.upscale`, `generate_frames_only` | spine steps 13-17 |
| `freeu`, `lora_name`, `lora_strength` | **ignored** (F-1, F-2) |
| `motion_strength`, `prompt_style`, `fidelity_mode`, `language`, `h3_intent` | enh only; `language` and `h3_intent` have no effect for Wan |
| `first_frame_path`, `last_frame_path`, `guides`, `ref_*`, `style_embedding` | **ignored**; `first_frame_path` only flips the enhancer's first-frame hint |
| `combine_frames` | ignored; `frames_per_batch` only scales the timeout |

---

## 4. LTX 2.3 and LTX 2.5

**Registry.** `ltx23-distilled-fp8` and `ltx25-distilled-int8`, both declaring:

| Key | Value |
|---|---|
| Alignment | 32 |
| Native fps / max frames | 16 / 161 |
| Steps (default / min) | 8 / 8 |
| `audio_out` | false |
| `vram_mb` | 14000 |
| `aspect_ratios` | none |
| `comfyui_reserve_vram_gb` | none |

- `ltx_comfyui_map` (R:2364) adds `upscale_model` to 2.5 entries.
- Routing (G:2224-2238):
  - Any id starting with `ltx` takes this branch.
  - An unknown `ltx25*` id becomes `ltx25-distilled-int8`; any other unknown `ltx*` id becomes
    `ltx23-distilled-fp8`.
  - 2.5 is chosen by the id prefix or the presence of `upscale_model`.

| Stage | LTX 2.3 | LTX 2.5 |
|---|---|---|
| Missing files | not checked | `_ltx25_missing_files` (skipped when ComfyUI's model tree is not local) |
| Steps / cfg | `steps or 8`; cfg from the request (7.5 unless the caller sends it; above 1.5 only an info log) | same cfg; **steps discarded** (`_ = num_inference_steps`, fixed 8 + 3 manual sigmas) |
| Image | `i2v = image_path exists`; a missing file silently gives T2V | same |
| Builder | W:`_create_ltx23_t2v_workflow` / `_i2v_` (W:1010 / 1168) | W:`_create_ltx25_t2v_workflow` / `_i2v_` (W:1358 / 1552) |
| Graph | `DualCLIPLoader(ltxv)` + projection, `EmptyLTXVLatentVideo` or `LTXVImgToVideo(strength 1.0)`, audio latent concat, `ModelSamplingLTXV(2.05, 0.95)`, `KSampler(euler_ancestral_cfg_pp, simple)` | `CLIPLoader(ltxv)`, half-size stage 1 (`_ltx25_stage1_size`), `SamplerCustomAdvanced` with `ManualSigmas`, `LTXVLatentUpsampler` ×2, stage 2 at `seed+1` with `gradient_estimation`; I2V re-anchors with `LTXVImgToVideoInplace` |
| Frames | `_ltx_frame_count`: floor to 8n+1, minimum 9; **not capped at 161** | same |
| LoRAs | adapters before `ModelSamplingLTXV` | adapters before `CFGGuider` (both stages) |
| Output size | as requested after the spine | `floor(w/2/32)·32 × 2`, so 800×480 comes out 768×448 with no log |
| fps | `request.fps or 16` into audio latent, conditioning and VHS | same |

### Parameter map

| Field | Where it ends up |
|---|---|
| `prompt`, `negative_prompt` | `CLIPTextEncode`. The negative is the enhancer default or the builder default |
| `duration_frames` | `EmptyLTXVLatentVideo.length` / `LTXVImgToVideo.length` and audio `frames_number` |
| `num_inference_steps` | 2.3 `KSampler.steps`; **2.5 ignored**. No `min_steps` floor |
| `guidance_scale` | 2.3 `KSampler.cfg`; 2.5 `CFGGuider.cfg` |
| `seed` | sampler / `RandomNoise`; 2.5 also `seed+1` |
| `motion_strength` | enh only; never reaches `LTXVImgToVideo.strength` (fixed 1.0) |
| `adapters`, `text_encoder` | LoRA chain; 2.3 replaces `clip_name1` only, 2.5 replaces the whole CLIP file |
| `freeu`, `lora_name` | ignored (the `lora_name` warning says "no LoRA hook", which is wrong for LTX) |
| `speed_profile`, `style_embedding`, `first_frame_path`, `last_frame_path`, `guides`, `ref_*`, `wan_sampler_profile`, `combine_frames` | ignored, with no message |

---

## 5. HunyuanVideo

**Registry.** `hunyuan-t2v` and `hunyuan-i2v`, both declaring:

| Key | Value |
|---|---|
| Alignment | 16 |
| Max pixel area | 1,000,000 |
| Native fps / max frames | 24 / 129 |
| Steps (min / default) | 20 / 20 |
| `vram_mb` | 11000 |

`hunyuan_comfyui_map` (R:488) decides I2V by `"i2v" in id`.

| Stage | Where | What it decides |
|---|---|---|
| Route | G:2090 | An unknown `hunyuan*` id becomes `hunyuan-t2v`. Needs `UnetLoaderGGUF`. |
| Frames | G:`_hunyuan_frame_count` | Rounds to the **nearest** 4n+1, so a clip can grow by 2 frames. Not capped at 129. |
| Adapters / encoder | G:2104-2111 | Same helpers as Wan. |
| I2V | G:2112 | Requires `metadata.image_path`; uploads it. |
| T2V | G:2135 | Rejects `image_path`. |
| Graph | W:`_hunyuan_loader_nodes`, `_create_hunyuan_t2v_workflow` (W:1848), `_create_hunyuan_i2v_workflow` (W:1902), `_hunyuan_tail_nodes` (W:1796) | `UnetLoaderGGUF`, `DualCLIPLoader(clip_l, llava, hunyuan_video)` → `FluxGuidance(guidance_scale)`, `EmptyHunyuanLatentVideo` or `HunyuanImageToVideo(v2 replace)`, `ModelSamplingSD3(shift 7.0)`, `BasicGuider`, `euler`/`simple`, `VAEDecodeTiled(256/64/64/8)`. |

### Parameter map

| Field | Where it ends up |
|---|---|
| `prompt` | `CLIPTextEncode` or `TextEncodeHunyuanVideo_ImageToVideo` |
| `negative_prompt` | **ignored**: not passed, and the graph has no negative branch |
| `guidance_scale` | `FluxGuidance.guidance`, 7.5 by default against the family `guidance` 6.0 |
| `num_inference_steps` | `BasicScheduler.steps`, no floor |
| `seed` | `RandomNoise` |
| `adapters`, `text_encoder` | LoRA chain; replaces the llava encoder |
| `freeu`, `lora_name`, `speed_profile`, `style_embedding`, `first_frame_path`, `last_frame_path`, `guides`, `ref_*` | ignored, and none of them produces a message (`lora_name` logs a warning, like every family) |

---

## 6. MiniMax H3

**Registry.** `_H3_COMMON` (R:212-244):

| Key | Value |
|---|---|
| Alignment | 32 |
| Max pixel area | 768×1344 |
| Aspect ratios | declared |
| `cfg` | false |
| Native fps | 24 |
| Frame rule | 17k+5 |
| Max frames | 362 |
| Steps (min / default) | 20 / 20 |
| `duration_tiers` | ≤175 frames at 768×1344, longer at 864×480 |
| `--reserve-vram` | 5.0 |

- The fl2va builds (`minimax-h3-int8`, `-int8-full`, `-bf16`) declare t2v, i2v, l2v and flf2v.
- `minimax-h3-ref2va-int8` declares ref2v, with `ref_limits`.
- `minimax-h3-int8` declares `tier_defaults`, including `speed_profile: turbo-8` on 16 GB.

| Stage | Where | What it decides |
|---|---|---|
| Route | G:2349 | An unknown `minimax*` id becomes `minimax-h3-int8`. Sets `has_audio`. |
| Dispatch | G:`_build_minimax_request` (G:941) | A ref2v-only build goes to `_build_minimax_ref_request` (G:836). `ref_*` is refused on fl2va builds. |
| Common | G:`_resolve_minimax_common` (G:765) | Speed profile → LoRA (must be installed). Steps: an explicit value wins, even below the floor; else the profile's steps; else `max(requested or default, min_steps)`. `negative_prompt` and `guidance_scale` are logged as unused. Style embedding token appended to the prompt. |
| Frames | G:`_minimax_frame_count` | Snaps **up** to 17k+5. Not capped at 362. |
| First / last frame | G:982-998 | `first_frame_path or metadata.image_path`; a last frame needs l2v or flf2v. |
| Guides | G:1000-1025 | Audio guides cut with ffmpeg (`_prepare_guide_audio`) and uploaded. |
| fl2va graph | W:`_create_minimax_workflow` (W:2165) | `UNETLoader`, `CLIPLoader(minimax)`, video + audio VAE, `MiniMaxH3ImageToVideo`, `BasicGuider`, `res_multistep`/`simple`, `VAEDecode` + `VAEDecodeAudio` → VHS with audio. Profile LoRA at node 15, adapters from 40, guides from 17. |
| ref2va graph | W:`_create_minimax_ref_workflow` (W:2000) | Reference images `LoadImage` from 20, clips `VHS_LoadVideo(force_rate 24, cap 362)` from 30, soundtracks from 33, audio from 36 → `MiniMaxH3ReferenceToVideo`. |

### Parameter map

| Field | Where it ends up |
|---|---|
| `prompt` | enh → `h3_prompt_compiler` → node 6 `prompt`, plus the style token |
| `negative_prompt`, `guidance_scale` | ignored (logged) |
| `num_inference_steps`, `speed_profile`, `metadata.steps_explicit` | `BasicScheduler.steps`, profile LoRA |
| `duration_frames` | snapped up; also picks the area tier |
| `fps` | VHS `frame_rate` and the guide-audio length; not forced to the native 24 |
| `first_frame_path` / `metadata.image_path`, `last_frame_path`, `guides` | `LoadImage` nodes 5/16, `MiniMaxH3AddGuide` pairs (fl2va); refused on ref2va |
| `ref_images`, `ref_videos`, `ref_audios`, `metadata.ref_image_size` | ref2va nodes; refused on fl2va |
| `language`, `h3_intent` | the H3 compiler; batch and MCP callers cannot set them (§10) |
| `adapters`, `text_encoder` | LoRA chain, `CLIPLoader.clip_name` |
| `freeu`, `lora_name`, `wan_sampler_profile`, `combine_frames` | ignored |

---

## 7. CogVideoX

**Registry.**

| | `cogvideox-5b` | `cogvideox-5b-i2v` |
|---|---|---|
| Form | diffusers snapshot | single file |
| `requires` | none | `t5-encoder`, `cogvideox-vae` |
| Native fps / max frames | 8 / 49 | 8 / 49 |
| Steps (min / default) | 50 / 50 | 50 / 50 |
| FAMILY_SPECS `max_pixel_area` | none | none |

| Stage | Where | What it decides |
|---|---|---|
| Spine | §2 | No aspect clamp, no area clamp, align 16. |
| T2V route | G:2156, exact id `cogvideox-5b` | Rejects `image_path`. The model is the hub id `THUDM/CogVideoX-5b` loaded by `DownloadAndLoadCogVideoModel`. |
| T2V graph | W:`_create_cogvideox_text2video_workflow` (W:161) | `CLIPLoader(t5 …fp8, sd3)`, `CogVideoTextEncode` ×2, `EmptyLatentImage`, `CogVideoSampler(CogVideoXDDIM)`, `CogVideoDecode` (tiled). |
| I2V route | G:2183, exact id `cogvideox-5b-i2v` | Requires `image_path`. `_cogvideox_i2v_missing_files` links `checkpoints/` into `diffusion_models/` as a side effect. |
| I2V graph | W:`_create_cogvideox_i2v_workflow` (W:290) | `CogVideoXModelLoader`, `CogVideoXVAELoader`, `ImageResizeKJ(divisible_by 16)`, `CogVideoImageEncode`, `CogVideoSampler(image_cond_latents)`. |
| Optional nodes | W:`_add_cogvideox_optional_nodes` (W:111) | `metadata.teacache_threshold` → `CogVideoXTeaCache`; `metadata.feta_weight` → `CogVideoEnhanceAVideo`. No range check. |
| Offline fallback | `OfflineVideoGenerator.generate_video` | Runs when the router falls back. `cogvideox-5b-i2v` without an image silently becomes `cogvideox-5b`. |

### Parameter map

| Field | Where it ends up |
|---|---|
| `prompt` | positive `CogVideoTextEncode` |
| `negative_prompt` | **dropped**: not passed to either builder, so the negative encoder gets `""` |
| `duration_frames` | `CogVideoSampler.num_frames`, **not snapped to 8n+1 and not capped at 49** |
| `num_inference_steps` | sampler `steps`, no 50-step floor |
| `guidance_scale` | sampler `cfg`, 7.5 by default against the family `guidance` 6.0 |
| `freeu`, `lora_name` | skipped, with a warning |
| `adapters`, `text_encoder`, `speed_profile`, `style_embedding`, `first_frame_path`, `last_frame_path`, `guides`, `ref_*` | ignored, with no message |

---

## 8. SVD

SVD is retired.

- There is no registry entry and no branch in `generate_video`.
- In `OfflineVideoGenerator`, `SVD_MODELS` is empty.
- `W:_create_svd_workflow` has no callers.
- `_DIMENSION_ALIGNMENT_BY_FAMILY["svd"]` is never reached, because `_model_family` never returns
  `svd`.
- The Studio and MCP paths refuse the id as an unknown model.

`G:SvdI2VGenerator` is SVD in name only. It renders `cogvideox-5b-i2v` with:

| Setting | Value |
|---|---|
| fps | 7 |
| Frames | 14–25 |
| Prompt | empty; prompt and LoRAs are dropped |
| Size | 512×512 |
| Steps | 25 |
| cfg | 7.5 |
| Interpolation | 2 |

It is reached only from Film Crew with `GUAARDVARK_FILM_I2V` set to `cogvideox`, `cog` or `svd`.
Music video maps the same `i2v_engine` values to `cogvideox-5b-i2v` directly.

---

## 9. Music video and Film Crew render stages

### Music video: `backend/tasks/music_video_tasks.py`

- **Stages:** `draft → analyzing → awaiting_approval → generating → assembling → complete`.
  Approval (`POST /api/music-video/<id>/approve`) is the gate before GPU spend.
- **Rendering:**
  - `run_clip_generator` renders one clip per Celery call and re-dispatches itself 12 s later.
  - `_generate_one_clip` builds `VideoGenerationRequest` itself and calls `generate_video`
    directly (no batch queue).
  - Each clip runs inside `gpu_session(VIDEO_RENDER, require_fit=True, free_comfyui=True)`.
    `GpuBusyError` defers the clip; the stage fails after 3 h.
- **Model:** resolved at create time with `surface="music-video"`. When the stored model is empty,
  `_settings()` re-resolves it and falls back to `wan22-5b` without an error.
- **Request:**

  | Field | Value |
  |---|---|
  | Size | 832×480, fixed |
  | Frames | the cut length × native fps, clamped to the model's range |
  | Steps | only if `i2v_steps` is set, else the dataclass 25 |
  | cfg | 7.5 |
  | Seed | none (time-based) |
  | `enhance_prompt` | False, so no default negative prompt |
  | Interpolation | 2; 1 on native-audio models |
  | Image | the keyframe still (FLUX, 1344×768, 45 steps, seed `1000+idx`) |

  Native-audio models (H3) get an `h3_prompt_compiler` prompt and a song-slice audio guide at
  frame 0.
- **Output:** each clip is conformed to 1920×1080 @ 24 fps with its audio stripped. The
  video_editor plugin composes the timeline over the song.

### Film Crew: `backend/tasks/production_swarm_tasks.py:run_editor`

- **Stages:**
  - `… → storyboard_gen → awaiting_approval → rendering → complete`.
  - Gate 2 (`POST /api/production/<id>/storyboard/approve`) marks every shot approved.
  - With `GUAARDVARK_FILM_AUTOCURATE` on (the default), a passing curator run advances to
    rendering with no human click.
- **Renderer**, chosen from the resolved model's capabilities:
  - `MiniMaxH3SceneGenerator` for audio + i2v or ref2v models;
  - else `Wan22I2VGenerator(model, fps=native_fps)`;
  - or `SvdI2VGenerator` under `GUAARDVARK_FILM_I2V`.
- **GPU admission:** one `gpu_session` covers the whole render. `GpuBusyError` fails the stage with
  no defer.
- **Request per shot:**

  | Adapter | Frames | Size | Steps / cfg | Other |
  |---|---|---|---|---|
  | `Wan22I2VGenerator` | 17–49, snapped with `snap_frames` | **512×512** (not passed) | 25 / 7.5 | `enhance_prompt=False`, `lora_name` inert |
  | `MiniMaxH3SceneGenerator` | from the shot windows | tier size (fallback 864×480) | tier `speed_profile`, `num_inference_steps=0` | `fps=24`, H3-compiled prompt, first/last frame or cast references |

- **Output:**
  - Clips are concatenated with voiceover and score by `FfmpegRunner.concat_with_audio`.
  - The final file is written under `tempfile.mkdtemp()`, and its Document row points there
    (`production_documents.register_production_output` does not copy it).

---

## 10. Defaults per entry point

The same bare request, "a clip of X on model M", arrives at `generate_video` with different
values depending on where it came from.

Studio and MCP values were produced by running `_clip_params` and `resolve_request` against the
registry with a 16376 MB card (see §12). The other columns are read from the code.

| Knob | Studio REST | MCP `generate_video` | Music video | Film Crew (Wan adapter) | Direct `VideoGenerationRequest()` |
|---|---|---|---|---|---|
| Size | `clip_defaults_for`: Wan 1312×736, LTX 1344×768, Hunyuan 1328×736, Cog 672×384, H3 int8 864×480 | the same; **864×480 (Cog 848×480) whenever `aspect_ratio` is given, even "16:9"** | 832×480 | 512×512 | 512×512 |
| Frames | `clip_defaults_for`: 49; H3 int8 124 | 49 clamped to `[min_clip_s × fps, max_frames]`: **H3 72** | cut length | 17–49 | 25 |
| fps | `native_fps` | `native_fps` | `native_fps` | `native_fps` | 24 |
| Steps | tier / `default_steps` / floor | `default_steps`; an explicit value is raised to the floor | 25 unless set | 25 | 25 |
| cfg | 7.5 | 7.5 (not settable) | 7.5 | 7.5 | 7.5 |
| Seed | settable, else random | random | random | random | random |
| Prompt enhancer | on (`cinematic`) | on; **`style=none` turns it off** | off | off | on |
| Default negative | yes, while the enhancer runs | yes, unless `style=none` | none | none | yes |
| Interpolation | body, default 2 | 2 | 2 (1 on H3) | 2 | 2 |
| `language`, `h3_intent` | cannot be set | cannot be set | set for H3 | set by the H3 adapter | settable |
| Preflight | `prepare_video_model` | `prepare_video_model` | `preflight_video_model` at create only | `preflight_video_model` | none |

---

## 11. Findings

Each finding cites the code it rests on. None is acted on in this change. "Assumption" marks
anything that could not be confirmed without a GPU or ComfyUI.

### Accepted but ignored

- **F-1. `freeu` never reaches any graph.**
  - G:2417-2447 only looks for a CogVideoX loader node, and on CogVideoX it skips with a warning.
  - On every other family nothing happens and nothing is logged.
  - `_add_freeu_node` is therefore unreachable. The CogVideoX warning even says "FreeU works on
    supported Wan paths".
  - The Studio "cinema" preset turns FreeU on for Wan (`frontend/src/pages/VideoGeneratorPage.jsx:260`)
    and shows a FreeU chip (`VideoGenEffectiveSettings.jsx:100`).
- **F-2. `lora_name` / `lora_strength` never reach any graph.**
  - G:2450-2488 skips CogVideoX with a warning and every other family with "no LoRA hook".
  - That message is wrong for Wan, LTX, Hunyuan and MiniMax, which do stack LoRAs through
    `adapters`.
  - `_add_lora_loader` is unreachable.
  - Studio sends `lora_name` (`VideoGeneratorPage.jsx:849`).
- **F-3. `first_frame_path` is documented as an alias of `metadata["image_path"]` (G:161), but only
  MiniMax reads it (G:983).**
  - Wan 5B and LTX render T2V instead.
  - Wan 14B I2V, Hunyuan I2V and Cog I2V refuse with "requires an input image".
  - The enhancer is still told there is a first frame.
- **F-4. Fields a model cannot honour are silently dropped.** The dataclass comment says such a
  field is refused "with a plain message instead of ignoring it" (G:156-158). Only the Wan
  speed-profile check and the MiniMax branch refuse. Every other family drops these fields with no
  message:
  - `speed_profile`, `style_embedding`, `last_frame_path`, `guides`, `ref_*`;
  - on CogVideoX, also `adapters` and `text_encoder`.
- **F-5. Negative prompts are dropped on three families.**
  - CogVideoX drops `negative_prompt`, including the default the enhancer just filled in: neither
    builder call passes it.
  - Hunyuan has no negative input.
  - MiniMax logs it as unused.
- **F-6. `wan_sampler_profile` is ignored on Wan 14B T2V.** The builder has no parameter for it. On
  14B I2V only its shift is used; the sampler stays `euler` (W:804, 823). The profile table says it
  is "used by BOTH the 5B and the 14B MoE workflow" (G:657).
- **F-7. On LTX 2.5, `num_inference_steps` is discarded** (W:1399). It still scales the timeout.
- **F-8. `motion_strength` only adds words to the prompt.** It never reaches a node (e.g.
  `LTXVImgToVideo.strength` stays 1.0). With the enhancer off it does nothing.
- **F-9. Wan 5B passes `request.num_inference_steps` and `guidance_scale` to its builder, not the
  resolved profile values** (G:2024-2025). This is harmless only while 5B declares no speed profile.
- **F-10. Three fields are misleading.**
  - `combine_frames` is never read in G.
  - `frames_per_batch` only scales the timeout.
  - `generate_frames_only` still writes the MP4: it adds frames, it does not replace the video.

### Silent clamps and snaps

In every item below the value changes with at most a log line; nothing is written to
`result.metadata`.

- **F-11. Alignment comes from the family table, not the entry.** `_align_dimensions` uses the
  family table (G:391, 595), not the entry's `dimension_alignment`.
  - All three Wan entries declare 32; the generator snaps Wan to 16.
  - The MCP `_dims_for_ratio` and `clip_defaults_for` use 32.
  - The snap rounds to the nearest multiple after the area clamp, so the result can land slightly
    over the pixel budget.
- **F-12. `max_frames` is never enforced inside `generate_video`**: Wan 81/121, LTX 161, Hunyuan
  129, H3 362, Cog 49. Only the MCP tool and `clip_defaults_for` read it. Studio passes the
  caller's frame count straight through.
- **F-13. Frame grids disagree between families.**

  | Family | Snap | Rule |
  |---|---|---|
  | Wan | none | registry says 4n+1; ComfyUI floors it (assumption) |
  | Cog | none | FAMILY_SPECS says 8n+1 |
  | LTX | floor to 8n+1, minimum 9 | |
  | Hunyuan | round to the nearest 4n+1 | can lengthen a clip |
  | MiniMax | round up to 17k+5 | |

  The registry's `snap_frames` rounds down by default ("a request is never lengthened without
  asking"). Only `Wan22I2VGenerator` uses it.
- **F-14. `min_steps` is enforced only by the MCP tool, by `clip_defaults_for` (for omitted
  steps) and by MiniMax's non-explicit path.**
  - A step count sent in the Studio body is passed through unchanged on every other family.
  - Direct callers, Film Crew (25 steps on Cog, floor 50) and music video also bypass it.
- **F-15. The chosen seed is not returned.** A missing seed becomes a time-derived one (G:1917)
  that is never written to the result, so a clip cannot be reproduced.
- **F-16. An adapter strength of 0 becomes 0.7.** `_chain_model_only_loras` uses `strength or 0.7`
  (W:2613). `_resolve_adapters` keeps 0.0.

### Defaults that differ between entry points (see §10)

- **F-17. The default negative prompt is only filled in inside the enhancer block** (G:1862), so
  every path that turns the enhancer off also loses it:
  - a successful director or storyboard pass (B:`_apply_director`, `_apply_storyboard`);
  - MCP `style="none"` (T:1204);
  - Verbatim Prompts;
  - music video and Film Crew.

  Studio `prompt_style="none"` keeps the enhancer on, so it does get a negative.
- **F-18. MCP with an explicit `aspect_ratio` renders at most 864×480**, even for the model's native
  16:9. Without it, the same MCP call gets 1312×736 (Wan) or 1344×768 (LTX). The 864×480 budget
  is a literal in `T:_dims_for_ratio` (T:954).
- **F-19. MiniMax H3 clip length depends on the entry point.** A bare MCP call renders 72 frames
  (the 3 s `min_clip_s` floor); Studio renders 124 from `tier_defaults`. The `-int8-full` and
  `-bf16` builds have no tiers: Studio gives them 49, which is snapped up to 56.
- **F-20. `guidance_scale` defaults to 7.5 on every entry point.** `clip_defaults_for` fills fps,
  frames, steps and size, but not cfg. So:
  - LTX distilled (registry description: "CFG=1") gets 7.5 with only an info log;
  - Hunyuan `FluxGuidance` and Cog get 7.5 against the family 6.0;
  - Wan gets 7.5 against 3.5.

  FAMILY_SPECS `guidance`, `lora_slot` and `audio_out` have no reader in `backend/`.
- **F-21. The cinematic keyframe path swaps the batch model for `i2v_model_for(model)`**
  (B:1032/1039/1068), for example `wan22-14b` → `wan22-14b-i2v`.
  - The swapped model was never preflighted, so a missing I2V sibling fails at render time.
  - Registration still records `model=batch_request.model` and that model's licence attribution
    (B:1292).
- **F-22. MCP discards `resolve_active_video_model`'s error and falls back to `wan22-5b`**
  (T:1122-1123). The user then sees a preflight error about `wan22-5b` instead of the real reason.
- **F-23. `language` and `h3_intent` cannot be set from Studio or MCP.** Neither the batch request
  nor `_process_item` carries them. MiniMax dialogue from those paths is always compiled as
  English.
- **F-24. The same environment variable has two defaults.** `GUAARDVARK_VIDEO_VRAM_WAIT_S` is
  5400 s in batch admission (B:766) and 600 s in per-clip booking (G:65).

### Error paths that report success, or lose the cause

- **F-25. ComfyUI's execution error text is thrown away.**
  - `_wait_for_completion` logs `exec_err` and returns None (G:1648-1651).
  - The user sees "ComfyUI generation timed out or failed" (G:2573).
  - A ComfyUI-side OOM therefore never reaches the OOM branch.
- **F-26. A PNG can be reported as the video.**
  - Per-file download errors are swallowed.
  - With `generate_frames_only`, if the MP4 download fails and the PNGs arrive,
    `primary = video_files[0] if video_files else downloaded_files[0]` (G:2590) is a PNG.
  - `_looks_like_blank_video` passes non-video files over 10 KB (G:100), so `success=True`.
- **F-27. Offline fallback success without an MP4.** When frames render but muxing fails,
  `result.success = bool(frame_paths)` (`offline_video_generator.py:1169`) with no `video_path`.
  The batch counts that item as completed (B:`_process_item`).
- **F-28. A start image that does not exist is dropped instead of refused** on Wan 5B and LTX
  (`if image_path and Path(image_path).exists()`). Wan 14B I2V and Hunyuan I2V refuse the same
  condition.
- **F-29. `_ensure_comfyui_reserve_for` proceeds with the wrong `--reserve-vram`** after 600 s of a
  busy queue. The Wan 14B registry comment records the cost of that mismatch as 43 minutes per 5 s
  clip. The aliases `wan22` and `wan2.2` skip the check, because it looks up the raw id.
- **F-30. A degraded feature still reports success.** Face restore (missing node or weights),
  FreeU, `lora_name`, enhancer failure and frame export all degrade to a clip without the feature
  and a log line. Nothing records the degradation in the batch result.
- **F-31. The batch OOM handler's "mark unavailable" step never takes effect.** On an OOM in a
  batch item, B:1169 runs `self.video_generator.service_available = False`. `self.video_generator`
  is the `VideoGenerationRouter`, where `service_available` is a read-only property, so the
  assignment raises `AttributeError` (checked by running it). The surrounding `try/except` hides
  the error. The ComfyUI generator's own OOM branch does set its flag, but step 1 of the spine
  re-probes it on the next call.
- **F-32. Retry skips preflight and loses part of the request.** `retry_batch` never calls
  `prepare_video_model`, and `retry_data` omits:
  - `storyboard_concept` and `director_guidance` (a storyboard batch retries as N copies of the raw
    concept);
  - `last_frame_paths` and `guides`.
- **F-33. Batch registration picks up stray files.** It registers every `*.mp4` under the batch
  folder (B:1305), not the files in `status.results`, so any leftover MP4 there becomes a Document.
- **F-34. Music video leaves gaps instead of errors.**
  - The assembler keeps only `done` clips and errors only when none remain, so a failed clip
    leaves a gap in the timeline.
  - The storyboard generator swallows per-cut failures and clears the stage error.
- **F-35. Film Crew H3 assigns clips by the wrong index.**
  - `run_editor` assigns `res.clip_paths[i]` to shot `i` (`production_swarm_tasks.py:681-683`).
  - On the H3 scene path those paths are one per window (`services/swarm/agents/editor.py:172`),
    not one per shot, so later shots get no clip or the wrong one.
- **F-36. Film Crew reports success after dropping audio.** Text-to-speech and score failures are
  swallowed (`editor.py`), and the render completes as a silent concat.
- **F-37. The Film Crew final video lives in a temp directory.** The render goes to
  `tempfile.mkdtemp()` and the Document row stores that path (`production_documents.py:58-66`), so
  the file lives only as long as the system temp directory.
- **F-38. Twelve MiniMax guides overwrite a LoRA node.** Guides take node ids 17 and up, two per
  guide, and user LoRAs start at 40 (W:2323-2340). With 12 or more guides the ids collide; there is
  no guide-count limit (G:1000-1025).
- **F-39. MiniMax ref2v file count misses clip soundtracks.** The total counts `v["audio"]`
  (G:862), but the schema field is `audio_path` (G:897), so separate soundtracks are not counted
  against `ref_limits.files`. `ref_limits.video_seconds` is never checked.

### Values hard-coded in builders that belong in the registry

Each of these is a per-model choice written as a literal in a builder or generator. They are
candidates for registry fields next to the model, with a note of what each was measured against.

| Value | Where | Registry today |
|---|---|---|
| Wan sampler profiles (`euler` / `uni_pc` + shift 8.0, default "official") | G:663-671 | none |
| Wan 14B sampler `euler`/`simple`, expert split at `steps // 2`, low-noise seed 0 | W:501, 606-632, 743, 800-826 | none |
| Wan dynamic shift (base 1280×704, 8.0, clamp 3..12) | G:683-691 | none |
| Text encoder on CPU at ≤20 GB (Wan, LTX, Hunyuan) | G:622-655 | none |
| LTX `ModelSamplingLTXV(2.05, 0.95)`, `euler_ancestral_cfg_pp`, `img_compression` 18 | W:1110-1127, 1228 | none |
| LTX 2.5 sigma lists, samplers, half-size stage 1, `seed+1` | W:1328-1336, 1461, 1503 | none |
| LTX steps fallback 8, cfg fallback 1.0, fps fallback 16 | G:2252-2254, 2285 | `default_steps`, `native_fps` exist but are not read |
| Hunyuan shift 7.0, VAE tile, image interleave 4, `v2 (replace)` | W:1763-1766, 1952 | none |
| MiniMax `res_multistep`, reference `force_rate 24`, `max_ref_frames 362` | W:2045, 2074, 2111 | `native_fps`, `max_frames` duplicate them |
| CogVideoX hub id, T5 file path, 16-divisible resize | G:352, W:182, 323, 384 | `t5-encoder` `dst` |
| `MODEL_MIN_VRAM_GB` table | G:358-370 | `min_vram_gb`, declared only by H3 |
| Fallback filenames in every loader | W:916-918, 1042-1046, 1770-1775, 1982-1987; G:1460 | registry `files[*].dst` |
| Default negative prompts, one literal per builder | W:493, 735, 921, 1051, 1202, 1353 | none |
| RIFE `rife49.pth`, RealESRGAN ×2, CodeFormer fidelity 0.5 | W:2450, 2499, 2663 | `realesrgan-x2`, `codeformer` entries exist, not read |
| MCP ratio budget 864×480 | T:954 | `max_pixel_area`, `tier_defaults` |

### Registry versus builder

- **F-40. Wan 14B T2V still uses the resolution-scaled shift.** The registry comments say the warp
  "was the sampler shift… the shift is fixed at its source" (R:349-353). The 14B T2V builder still
  uses `_wan_dynamic_shift` (W:507), which gives 3.0 at 736×416. I2V and 5B use the fixed 8.0.
- **F-41. The LoRA comments contradict the code.** G:2445-2449 and the `Wan22I2VGenerator`
  docstring say Wan GGUF has no LoRA hook. The Wan builders do stack `LoraLoaderModelOnly`, and the
  registry ships Wan LoRAs.
- **F-42. I2V routing depends on the id text.** `wan_comfyui_map` and `hunyuan_comfyui_map` decide
  I2V from the id string, and CogVideoX routes by exact id. A user-catalog model with a different
  id takes the wrong builder or none at all.
- **F-43. `cogvideox-5b` loads T5 but declares no `requires`.** The preflight passes without T5.
- **F-44. `tier_defaults.speed_profile` (H3 `turbo-8` on 16 GB) is not returned by
  `clip_defaults_for`.** Studio and MCP render H3 at the standard 20 steps; Film Crew's
  `MiniMaxH3SceneGenerator` applies the tier profile.
- **F-45. The unrouted-model error lists supported ids from a literal** (G:2363), which omits
  Hunyuan and the extra H3 builds.

### Unreachable code

Reported only; nothing was removed:
- `W:_create_svd_workflow`
- `W:_add_freeu_node` and `W:_add_lora_loader` (F-1, F-2)
- the non-Cog FreeU branch at G:2436-2442
- `_DIMENSION_ALIGNMENT_BY_FAMILY["svd"]`
- the SVD helpers in `OfflineVideoGenerator`
- the `min_short_edge` check in `_resolve_minimax_common` (no declared profile sets it)

---

## 12. How this was checked

- Every stage and finding was traced by reading the code at `e18cdb2`.
- The per-entry-point numbers in §10 (Studio and MCP columns) and F-18/F-19 were produced by
  running the real `_clip_params`, `clip_defaults_for` and `VideoGeneratorTool.resolve_request`
  against the shipped registry. Only the VRAM probe was patched, to report 16376 MB.
- No GPU, ComfyUI or database was used. Nothing here claims a render works or fails on real
  hardware. Behaviour inside ComfyUI nodes is marked as an assumption where it matters.
