# Video prompting: what reaches the text encoder

This is the audit behind `GUAARDVARK_VIDEO_REFERENCE_DEFAULTS`. It traces the text, guidance and
negative prompt a video request renders with, compares them with the model makers' own ComfyUI
templates, and lists what is suspected of spoiling a render. Nothing here was rendered: every
claim about the graph comes from running `generate_video` against a fake ComfyUI
(`backend/tests/services/test_video_prompt_defaults.py`). Every claim about image quality is a
suspicion until `scripts/video_prompt_ab.py` has been run on a GPU.

References are to `comfyui-workflow-templates-json` 0.1.57, the version ComfyUI v0.34.0 pins
through `comfyui-workflow-templates` 0.11.48. Template names are the JSON file names in that
package.

## The reported case

The report: Wan 2.2 14B T2V came out washed out, with a clip-art crowd. It was a
`generate_video` MCP call with `style=3d_animation` and 25 steps, at 864×480, 81 frames, 16 fps,
with RIFE ×2. The MCP tool sends no guidance. Before this change the batch route filled in 7.5,
and so did the batch request and the generator request. The prompt enhancer ran.

What the graph received (both samplers; the experts split 25 steps at 12):

| | Sent |
|---|---|
| positive | `An outdoor concert on a green lawn, a black cartoon aardvark rapper on stage, hundreds of fans. 3D-animated, Pixar-style polished CGI, expressive characters, soft global illumination, subsurface scattering, smooth rigging, appealing character design, smooth cinematic motion at native frame rate, strong temporal coherence, natural camera movement and dynamics, high quality, masterpiece` |
| negative | `blurry, low quality, flat shading, low-poly, jagged edges, artifacts, distorted, poorly rendered, low resolution, watermark, text overlay, uncanny valley, temporal inconsistency, animal head, horse head, animal ears, animal face, fur on face, snout, muzzle, whiskers, human-animal hybrid, anthropomorphic, creature hybrid, deformed face, fused features, extra head, two heads, extra limbs, mutated anatomy, malformed body` |
| cfg | 7.5 on both `KSamplerAdvanced` |
| shift | 3.7 (`_wan_dynamic_shift(864, 480)`) on both `ModelSamplingSD3` |
| sampler | `euler` / `simple`, 25 steps, split at 12 |

The reference, `video_wan2_2_14B_t2v` (without the Lightning LoRA):

| | Template |
|---|---|
| cfg | 3.5 |
| shift | 5.0 on both experts |
| sampler | `euler` / `simple`, 20 steps split at 10 |
| negative | `色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走` plus `裸露，NSFW` |
| positive | plain descriptive prose, no quality tags |

The negative translates to: garish colours, overexposed, static, blurry detail, subtitles, style,
artwork, painting, frame, still, **overall greyish**, worst quality, low quality, JPEG artefacts,
ugly, mutilated, extra fingers, badly drawn hands, badly drawn face, deformed, disfigured,
malformed limbs, fused fingers, motionless frame, cluttered background, three legs, **many people
in the background**, walking backwards.

### Suspected causes

| # | Suspect | Evidence | Status |
|---|---|---|---|
| 1 | **cfg 7.5 against the reference 3.5.** Nothing reads the family `guidance` value (docs/video-pipeline.md F-20). A caller that names no cfg (MCP, film crew, music video, and the REST route when the field is left out) got 7.5 on every model. That includes LTX distilled, whose template uses 1.0. | Graph trace. The UI is not affected: it sends the family value. | Fixed behind the setting. The A/B plan `case` isolates it (`cfg-reference`). |
| 2 | **The identity-bleed guard is on every clip.** `IDENTITY_BLEED_NEGATIVE` was written for character-LoRA clips. It names `animal head, animal face, fur on face, snout, muzzle, whiskers, anthropomorphic`, which is a description of the subject this prompt asks for. | Negative text. | Behind the setting, it is sent only when a cast member or LoRA is in the request (`negative-no-guard`). |
| 3 | **The negative is not the model's.** Ours says nothing against a grey, washed-out image; the template's names it (`整体发灰`), and names garish colour and overexposure. | Template. | Behind the setting, Wan gets the template negative (`negative-template`). |
| 4 | **The style suffix outweighs the subject.** 17 words of subject, then 34 words of style, motion and quality tags. "Soft global illumination" and "subsurface scattering" ask for soft, low-contrast light. "Appealing character design" and "Pixar-style" can pull every figure, the crowd included, toward a generic mascot look. "high quality, masterpiece" are SD-era tags; the template uses none. | Text only. | Not changed. `enhancer-off` and the `styles` plan measure it. |
| 5 | **Shift 3.7, the resolution-scaled curve.** 14B T2V ignored `wan_sampler_profile` and stayed on the curve (F-6, F-40). The 5B and 14B I2V left that curve for a fixed 8.0 after it "produced warping and colour bleed at every size" (the comments at `WAN5B_SAMPLER_PROFILES` and in `_create_wan22_i2v_workflow`). The template uses 5.0. | Graph trace; the repository's own measurement on the 5B. | T2V now takes a named profile's shift, as I2V does. Behind the setting, a request that names none gets the default profile's 8.0, like its siblings. The A/B variant is `shift-8`. 5.0 is not reachable from a request; see follow-ups. |
| 6 | **The template negative names "many people in the background".** This one pushes against the prompt ("hundreds of fans"). | Template. | Reported, not changed: it is the reference. `negative-template` shows its effect. |
| 7 | RIFE ×2 | Doubles the frames after decode; does not change colour. | Not a suspect for washed-out colour. The A/B renders without it by default (`--interpolation 2` to match the report). |

## The text, path by path

### Which LLM

On the report's path, **none**. `enhance_prompt` runs `backend/utils/prompt_enhancer.py`, which
joins strings. An LLM rewrites the prompt only on these paths:

| Path | Where | Model | System prompt |
|---|---|---|---|
| Studio "Director" (`director_mode`) | `batch_video_generator._apply_director` → `director_service.plan(PROMPT_LIST)` → `media_director.enhance_prompts` | the active chat model first, then installed gemma, then qwen (`music_video_director._director_candidates`; the built-in default is `gemma4:e4b`) | `_SYSTEM_ENHANCE_IMAGE`, "a cinematic visual director for **still images**" |
| Storyboard (`storyboard_concept`) | `_apply_storyboard` → `plan(CONCEPT_EXPANSION)` → `media_director.storyboard_from_concept` | same ladder | `_SYSTEM_STORYBOARD_IMAGE` |
| MiniMax H3 polish | `h3_prompt_compiler.polish_intent` | same ladder | `_POLISH_SYSTEM`. Off: `enhance_for_family` passes `polish=False`. |

When a director or storyboard pass changes a prompt, the batch turns the enhancer off. Its style
suffix and its default negative are then not applied (F-17).

### What the enhancer adds (`enhance_video_prompt`)

- `style="none"` returns the prompt unchanged. An unknown style also returns it unchanged.
- **MiniMax** goes to `h3_prompt_compiler.enhance_for_family`. The style becomes an opening phrase
  (`STYLE_OPENINGS`, e.g. "3D CG animation.") inside the H3 section format, with no suffix and no
  negative. H3 has no negative branch and no CFG.
- **Every other family**: prompt + `.` + `STYLE_SUFFIXES[style]` + the family's `MOTION_TERMS`
  (`wan`, `cogvideox` and `ltx` have their own; Hunyuan gets the default) + `high quality,
  masterpiece`.
  - A Motion preset outside the neutral band puts a phrase in front of the motion terms.
  - The duplicate check `motion not in suffix` never matches a whole motion string, so the
    cinematic suffix's "smooth natural motion" is followed by "smooth cinematic motion".
- **Portrait**: height greater than width adds a framing sentence.
- **Fidelity path** (the "Exact text mode", or a prompt `has_text_intent` detects):
  - adds only `high quality, sharp focus, clean details, good contrast` and the motion terms;
  - the style is dropped from the positive, but its negative is still used.
- **Verbatim Prompts** skips all of it, the default negative included.

### The negative each family gets

| Family | Enhancer on, setting off (today) | Enhancer off, setting off | Setting on |
|---|---|---|---|
| Wan (14B T2V/I2V, 5B) | style negative + identity guard | builder default (an English anatomy list) | template negative (the Wan text above, without `裸露，NSFW`) with or without the enhancer; guard only for a cast member or LoRA |
| LTX 2.3 / 2.5 | style negative + identity guard | builder default | style negative when enhanced, else builder default; guard only for a cast member or LoRA. The template negative (`pc game, console game, video game, cartoon, childish, ugly`) is not the default: it names "cartoon" and "childish", against five of the eight styles. |
| CogVideoX (T2V, I2V) | **nothing**: the builders were never given one, so even a typed negative was dropped (F-5) | nothing | style negative when enhanced; a typed negative is now sent in every mode |
| Hunyuan | none: `BasicGuider`, no negative branch | none | none |
| MiniMax H3 | none: `BasicGuider`, logged as unused | none | none |

### Guidance

| Model | Template | Value | Before (no cfg named) | Setting on |
|---|---|---|---|---|
| wan22-14b, wan22-14b-i2v | `video_wan2_2_14B_t2v`, `_i2v` | 3.5 (20 steps); 1.0 with the Lightning LoRA | 7.5 | 3.5; the `lightx2v-4` profile still sets 1.0 |
| wan22-5b | `video_wan2_2_5B_ti2v` | 5 (`uni_pc`, shift 8, 20 steps) | 7.5 | 5.0 |
| ltx23-*, ltx25-* | `video_ltx2_3_t2v`, `video_ltx2_5_t2v` | `CFGGuider` 1 | 7.5, with an info log | 1.0 |
| hunyuan-t2v | `hunyuan_video_text_to_video` | `FluxGuidance` 6 | 7.5 | 6.0 |
| hunyuan-i2v | none in this package (assumption: same as T2V) | – | 7.5 | 6.0 (family value) |
| cogvideox-5b, -i2v | none in this package; 6.0 is the `CogVideoSampler` default in the `/object_info` snapshot | 6.0 | 7.5 | 6.0 |
| minimax-h3-* | `video_minimax_h3_t2v` | `BasicGuider`, no CFG | ignored | ignored |

The values live in the registry (`FAMILY_SPECS` / an entry's `cfg_when_unset` and
`negative_when_unset`) and are applied in `backend/services/video_render_limits.py`.

## `GUAARDVARK_VIDEO_REFERENCE_DEFAULTS`

Off by default. A fresh clone renders exactly as before. The test
`test_off_guidance_is_the_old_placeholder` and a 90-request trace (every T2V model × enhancer
on/off × every style, compared node by node against the previous code) show no difference.

| A request that… | Off (before and after this change) | On |
|---|---|---|
| names no guidance (MCP, film crew, music video, REST without the field) | 7.5 on every model | the model's `cfg_when_unset` |
| names a guidance (the Studio page always does) | kept | kept |
| gives no negative, enhancer on | style negative + identity guard | template negative where declared (Wan), else the style negative; the guard only with a cast member or LoRA |
| gives no negative, enhancer off | the builder's own | template negative where declared, else the builder's own |
| types a negative | kept (CogVideoX now gets it too) | kept |
| Wan 14B T2V, no sampler profile named | shift from the resolution curve (3.7 at 864×480) | the default profile's 8.0, as on 14B I2V and the 5B |
| Wan 14B T2V, `wan_sampler_profile` named | that profile's shift (before: ignored) | the same |

Turn it on with `GUAARDVARK_VIDEO_REFERENCE_DEFAULTS=1` in `.env` and restart the backend.
`/api/batch-video/enhance-preview` reports whether it is on (`reference_defaults`), the negative
that will be used, and the guidance a request without one gets (`cfg_when_unset`).

## Prompt styles a model does not offer

A registry entry, or a family row, can declare `prompt_styles_withheld: {style: reason}`. The
style then:

- disappears from the model's `prompt_styles` in `/api/batch-video/models`, and so from the
  Studio menu;
- is refused by the MCP tool and by the REST routes with the reason;
- is refused by `generate_video` for a request that still asks for it with the enhancer on.

No style is withheld yet. Nothing has shown that a style fails on a model, and withholding one
on a guess would remove a working option. Run the `styles` plan below. Where a style comes out
worse than `none` on every seed, declare it with the run's date as the reason. Pinning a style
with the enhancer off is harmless: the style then adds no text.

## The A/B script

`scripts/video_prompt_ab.py` renders the variants one after another through
`/api/batch-video/generate/text`, all with the same prompt and seed. Guidance and the negative
are always sent explicitly, so the backend setting does not change what a variant renders. Its
output goes to `data/outputs/video_prompt_ab/<time>/`: `results.json`, the clips, and an
`index.html` that plays them side by side with the text each one was sent. The frame checker
from PR #233 is applied when the checkout has it.

```bash
# see what would be queued, without queuing anything
python scripts/video_prompt_ab.py --dry-run

# the report, one factor at a time (7 clips of wan22-14b per seed, 81 frames at 864x480)
python scripts/video_prompt_ab.py --model wan22-14b --plan case --seeds 1234,99

# with RIFE x2 as in the report
python scripts/video_prompt_ab.py --model wan22-14b --plan case --interpolation 2

# every style at the reference values: the evidence for prompt_styles_withheld
python scripts/video_prompt_ab.py --model wan22-14b --plan styles --seeds 1234,99
python scripts/video_prompt_ab.py --model ltx23-distilled-fp8 --plan styles --frames 97
```

`--base` points it at the backend: `$GUAARDVARK_API`, else `$GUAARDVARK_URL/api`, else
`http://127.0.0.1:5000/api` (macOS uses port 5055). Ctrl-C cancels the clip in flight and keeps the results so far.

The variants in `case`:

| Name | style | enhancer | cfg | negative |
|---|---|---|---|---|
| reported | 3d_animation | on | 7.5 | style + guard |
| cfg-reference | 3d_animation | on | reference | style + guard |
| negative-no-guard | 3d_animation | on | 7.5 | style |
| negative-template | 3d_animation | on | 7.5 | template (Wan, LTX) |
| shift-8 (Wan 14B T2V only) | 3d_animation | on | 7.5 | style + guard; `wan_sampler_profile=official` |
| enhancer-off | 3d_animation | off | 7.5 | style + guard |
| reference-setting | 3d_animation | on | reference | what the setting sends (on Wan 14B T2V with `official`) |

## Findings not acted on

- **Batch-level metadata never reaches the clip.** `_run_batch_inner` builds each
  `VideoGenerationRequest.metadata` from `item.metadata` alone. So these fields the REST route
  puts in the batch metadata never reach `generate_video`:
  - `steps_explicit`;
  - `upscale`;
  - `teacache_threshold` and `feta_weight`;
  - an MCP `aspect_ratio`.

  So:
  - a step count typed in the Studio is handled like a preset one: a speed profile replaces it,
    and MiniMax raises it to the floor;
  - the 2× upscale toggle does nothing on a batch;
  - CogVideoX never gets TeaCache or Enhance-A-Video from the Studio.

  `test_batch_metadata_reaches_each_clip` holds this as a strict xfail.
  `high_consistency` is read from the batch metadata directly and works. Fixing this changes
  what a fresh clone renders, so it is left for its own change.
- **The Director rewrites video prompts with a still-image contract.** `_SYSTEM_ENHANCE_IMAGE`
  asks for composition, lighting and palette, and says nothing about motion or camera. Its
  "STYLE is appended by caller" does not hold on the video path: the batch turns the enhancer off
  after a successful rewrite.
- **Motion terms contradict some styles.** Wan's "smooth cinematic motion at native frame rate"
  sits next to `stop_motion`'s "slight handcraft imperfection between frames" and
  `western_cartoon`'s "snappy keyframed motion".
- **`FAMILY_SPECS["guidance"]` still has no reader.** `cfg_when_unset` now carries the same
  values; the old key was left in place.
- **`verbatim_prompts_enabled()` imports the Flask app** when no app context is active (the batch
  worker thread). The import starts the LLM initialisation thread. It was found because the
  tests had to replace that seam.
- **Wan 14B I2V uses shift 8.0**, where `video_wan2_2_14B_i2v` uses 5.0.

## Follow-ups

1. Run the `case` plan on Wan 14B with two seeds. Turn the setting on if `reference-setting`
   beats `reported` on both.
2. Run the `styles` plan on each installed model. Declare `prompt_styles_withheld` where a style
   loses to `none` on every seed.
3. Compare the template's shift 5.0 on Wan 14B against 8.0. It needs a sampler profile row with
   `euler` and 5.0, so a request can name it.
4. Pass batch metadata through to each clip (the first finding), in its own change.
