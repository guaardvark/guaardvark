# Animation Generator Design

**Date:** 2026-03-07
**Status:** Approved

## Overview

Add animated image/video generation to Guaardvark's agent tool system. Uses the existing Stable Diffusion pipeline's img2img capability to generate frame sequences, then assembles them into GIF and/or MP4 via imageio. Optionally uses the vision model (qwen3-vl) to steer frame evolution.

## Architecture

```
generate_animation tool (ReACT loop)
  → AnimationGenerator service
    → OfflineImageGenerator.generate_image_from_image() [new img2img method]
    → Optional: Ollama vision model steering every N frames
    → imageio.mimwrite() → GIF + MP4
  → /api/outputs/generated_animations/<file>
  → chat:image (GIF) + chat:video (MP4) → inline display
```

## Components

### 1. img2img on OfflineImageGenerator

New method `generate_image_from_image(request, init_image, strength)` that loads `StableDiffusionImg2ImgPipeline` from the same model weights. No extra download needed — diffusers loads img2img from the same checkpoint. Strength 0.0-1.0 controls deviation from input.

### 2. AnimationGenerator Service (`backend/services/animation_generator.py`)

```python
@dataclass
class AnimationRequest:
    prompt: str
    motion_prompt: str = ""
    num_frames: int = 8        # 4-24
    strength: float = 0.20     # img2img denoising
    width: int = 512
    height: int = 512
    fps: int = 8
    output_format: str = "both"  # gif, mp4, both
    style: str = "realistic"
    model: str = "sd-1.5"
    use_vision_steering: bool = False
    loop: bool = True          # ping-pong for smooth loop
    seed: int = None
```

Generation loop:
1. Frame 1: txt2img with base prompt
2. Frames 2-N: img2img from previous frame with evolving prompt
3. If vision_steering: every 4 frames, feed latest to qwen3-vl for refined prompt
4. If loop: append reversed frames (minus endpoints) for ping-pong
5. Assemble GIF + MP4 via imageio

### 3. Agent Tool: `generate_animation`

Parameters for LLM:
- prompt (required): Scene description
- motion (required): What changes between frames
- frames (optional, default 8)
- strength (optional, default 0.20)
- format (optional, default "both")
- vision_steering (optional, default false)

Returns metadata with gif_url and/or mp4_url.

### 4. Frontend Display

- GIF: Existing CardMedia (img tags animate GIFs natively)
- MP4: New `<video>` element with controls, autoplay, loop, muted
- Both StreamingMessage and MessageItem handle video type
- History persistence via generatedImages array (extend with type field)

### 5. Serving Route

`/api/outputs/generated_animations/<filename>` serves from `OUTPUT_DIR/generated_animations/`.

## Error Handling

- Mid-sequence failure: assemble partial animation from completed frames
- OOM: retry once at 384x384
- Vision steering timeout: skip, continue with base prompt

## File Changes

| File | Change |
|---|---|
| `backend/services/offline_image_generator.py` | Add `generate_image_from_image()` |
| `backend/services/animation_generator.py` | New service |
| `backend/tools/image_tools.py` | Add `AnimationGeneratorTool` |
| `backend/tools/tool_registry_init.py` | Register tool |
| `backend/api/output_api.py` | Add animation serving route |
| `backend/services/unified_chat_engine.py` | Add animation keywords |
| `frontend/src/components/chat/StreamingMessage.jsx` | Handle chat:video |
| `frontend/src/components/chat/MessageItem.jsx` | Render video type |

## Constraints

- GPU: RTX 4070 Ti SUPER 16GB shared with Ollama models
- 512x512 @ 8 frames ≈ 96s generation time
- img2img reuses loaded pipeline weights — no extra VRAM
- Vision steering adds ~3-5s per call (every 4 frames)
