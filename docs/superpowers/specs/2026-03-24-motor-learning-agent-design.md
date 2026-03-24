# Motor Learning Agent — Closed-Loop Hand-Eye Coordination

**Date:** 2026-03-24
**Status:** Approved
**Author:** Claude + User
**Builds on:** [Agent Vision Control Phase 1](2026-03-20-agent-vision-control-design.md)

## Summary

Replace the current open-loop grid-based click targeting with a closed-loop servo system that learns hand-eye coordination through self-supervised practice. The agent moves the cursor like a human moves a mouse — approach, observe, correct, click. Every servo interaction generates labeled training data used to fine-tune a custom vision model (`gvk-eye`) that gets progressively better at spatial reasoning.

This is a fundamentally different approach from all existing computer-use agent systems (Anthropic, OpenAI, Google, Microsoft). Those systems either rely on models pre-trained on massive proprietary datasets to predict coordinates in a single shot, or sidestep the problem with DOM/accessibility APIs. Our system teaches a small model to develop motor control through iterative practice — the same way biological systems learn.

## Innovation

**No other system does closed-loop motor control.** Every production computer-use agent does open-loop ballistic targeting: see screen → predict coordinates → click → hope. Our system introduces:

1. **Proprioception** — the agent knows where its cursor IS relative to where it WANTS to be
2. **Iterative correction** — approach, observe error, adjust, repeat
3. **Self-supervised motor learning** — every correction is a labeled training example
4. **Continuous improvement flywheel** — more use → more data → better model → fewer corrections → faster tasks

## Core Components

### 1. Servo Loop — Closed-Loop Motor Control

The servo loop replaces grid-based coordinate estimation. Instead of predicting exact coordinates in one shot, the agent iteratively converges on the target.

**Loop steps:**

```
1. BALLISTIC MOVE
   - Vision model sees screen, LLM says "target is roughly at (400, 250)"
   - Cursor moves there. No click yet.

2. OBSERVE
   - Screenshot with crosshair showing where cursor landed
   - Vision model answers: "on_target" or direction + distance from target

3. CORRECT
   - If not on_target, nudge cursor in indicated direction
   - Nudge magnitudes: small=10px, medium=40px, large=80px
   - Oscillation dampening: if direction reverses from previous correction,
     halve the nudge magnitude (80→40→20→10) to prevent ping-ponging

4. REPEAT steps 2-3 until on_target (max 4 corrections)

5. CLICK
   - Cursor is confirmed on target. Execute click.

6. VERIFY
   - Did the screen change? If yes, action succeeded.
   - If not, click missed — re-enter servo with escalation.
```

**Adaptive escalation:**
- First attempt: ballistic move + 1 correction max. If screen changes after click, done.
- If screen didn't change (miss): re-enter servo with up to 3 corrections.
- If still failing after 2 full attempts: zoom-crop the area around cursor for higher-precision vision, then servo again.

**Vision prompts for servo (three distinct tasks):**

1. **Coordinate estimation:** "Screen is 1280x720. Where is the [target description]? Respond with x,y coordinates." (`num_predict=128, temperature=0.3`)
2. **Correction prediction:** "The crosshair is visible on screen. How far is it from [target]? Respond: on_target, or direction and distance (small/medium/large)." (`num_predict=64, temperature=0.2`)
3. **On-target classification:** "Is the crosshair directly on [target]? Respond: yes or no." (`num_predict=32, temperature=0.1`)

**Note:** `vision_analyzer.py`'s `analyze()` method currently hardcodes `num_predict=256` and `temperature=0.3`. The servo controller must pass these as parameters. `analyze()` will be modified to accept optional `num_predict` and `temperature` overrides.

### 2. Fixed Resolution Coordinate Space

The virtual display is locked to **1280x720**.

**Rationale:**
- Fewer pixels = easier spatial reasoning for vision models
- VisionAnalyzer processes at max 1024px wide — 1280x720 is near 1:1
- Consistent coordinate space enables implicit spatial learning
- Standard laptop resolution — all websites render correctly
- Faster capture + smaller images = faster servo iterations

**Locked down:**
- Xvfb: `1280x720x24`
- Firefox: maximized, light theme, 100% zoom, bookmarks bar visible

**Not locked down:**
- Website content (dynamic — that's what the model learns)
- Mouse position (that's the point)

**Coordinate context in prompts:**
Every vision prompt includes: `"Screen is 1280x720. Top-left is (0,0). Bottom-right is (1280,720)."`

### 3. Training Data Pipeline

Every servo interaction is automatically recorded. No human labeling.

**Recording format per servo attempt:**

```json
{
    "timestamp": "2026-03-24T14:30:00",
    "screenshot_path": "data/training/servo/img_00142.jpg",
    "crosshair_pos": [400, 300],
    "target_description": "Reply button under first comment",
    "target_actual": [412, 287],
    "corrections": [
        {"direction": "right", "distance": "small", "pixels": 10},
        {"direction": "up", "distance": "small", "pixels": 13}
    ],
    "success": true,
    "app_context": "firefox_youtube"
}
```

**Deriving `target_actual`:** The cursor position at the moment `on_target` is confirmed (or after the final correction) becomes `target_actual`. If the verification step (step 6) determines the click missed (screen didn't change), `success` is set to `false` and the record is still saved — failed attempts are valuable training data but are labeled as unreliable for coordinate estimation. Only records with `success: true` are used for coordinate estimation training; all records (success or failure) are used for correction prediction and on-target classification training.

```
```

**Three training tasks derived from this data:**

1. **Coordinate estimation** — (screenshot, target description) → (x, y). Loss = pixel distance.
2. **Correction prediction** — (screenshot + crosshair, target description) → direction + magnitude. The "hand-eye" skill.
3. **On-target classification** — (screenshot + crosshair, target description) → yes/no. The "am I there yet?" skill.

**Data accumulation:**
- Passive: every real task generates data
- Active: automated practice sessions click known targets on real pages
- Failed attempts are as valuable as successes

### 4. Fine-Tuning Pipeline

**Base model:** `qwen3-vl:2b-instruct` (1.9GB, fast, already our best UI reader)

**Method:** QLoRA (Quantized Low-Rank Adaptation)
- ~100MB adapter on frozen base model
- 1000 examples: ~20-30 minutes on one 16GB GPU
- Tools: `unsloth` or `peft` + `transformers`

**Training data format (HuggingFace conversations + image column, matching existing `finetune_vision.py`):**

```jsonl
{"image": "data/training/screenshots/img_00142.jpg", "conversations": [{"role": "user", "content": "Screen is 1280x720. The crosshair is at (400, 300). How far is it from the Reply button?"}, {"role": "assistant", "content": "{\"on_target\": false, \"direction\": \"right_and_up\", \"dx\": 12, \"dy\": -13}"}]}
```

The `image` column is cast via HuggingFace `datasets.Image()`. The `conversations` field uses the model's native chat template via `tokenizer.apply_chat_template()`. This is consistent with the existing training infrastructure at `backend/services/training/scripts/finetune_vision.py`.
```

**Model versioning:**
- `gvk-eye-v0` — base qwen3-vl:2b, no fine-tuning (current)
- `gvk-eye-v1` — first fine-tune after ~500 servo interactions
- `gvk-eye-vN` — subsequent versions as data grows
- Registered in Ollama: `ollama create gvk-eye-v1 -f Modelfile`
- **Note:** QLoRA produces safetensors adapters. `register_model.py` must convert to GGUF format (via `llama.cpp` convert script) before `ollama create`, or use Ollama's `ADAPTER` Modelfile directive if supported.
- Agent config points to active version, easy swap/rollback

**Eval before deploy:**
- 20% holdout test set
- Metrics: mean pixel error, on-target accuracy
- New model must beat current on both metrics to be promoted

**Improvement flywheel:**

```
Use agent → servo generates data → accumulate batch →
fine-tune → eval → if better, deploy → agent gets smarter →
fewer corrections needed → faster tasks → more data → repeat
```

## Architecture

### New and Modified Files

```
backend/
  services/
    agent_control_service.py    # MODIFY — use servo_controller instead of grid targeting
    local_screen_backend.py     # MODIFY — update screen_size() fallback to 1280x720
    servo_controller.py         # NEW — closed-loop motor control engine
    training_data_collector.py  # NEW — records servo interactions to disk
    training/scripts/
      generate_practice_data.py # NEW — automated practice session runner
      prepare_training_set.py   # NEW — raw servo logs → QLoRA format
      fine_tune_servo.py        # NEW — QLoRA fine-tuning for gvk-eye model
      evaluate_model.py         # NEW — eval against held-out test set
      register_model.py         # NEW — GGUF conversion + ollama create wrapper
  utils/
    vision_analyzer.py          # MODIFY — add servo-specific prompts, parameterize num_predict/temperature
    cursor_overlay.py           # KEEP — crosshair compositing (central to servo)
    grid_overlay.py             # DEPRECATE — replaced by servo approach
  tests/
    test_screen_interface.py    # MODIFY — update screen_size assertion to 1280x720
data/
  training/
    servo_logs/                 # Raw servo interaction recordings
    screenshots/                # Captured screenshots with metadata
    datasets/                   # Prepared training/eval splits
    models/                     # Fine-tuned model adapters
scripts/
  start_agent_display.sh        # MODIFY — resolution, Firefox dimensions to 1280x720
```

**Note on file paths:** Training scripts go under `backend/services/training/scripts/` to co-locate with the existing `finetune_vision.py`. Training data goes under `data/training/` (consistent with existing `TRAINING_DIR` in config). These are two different directories for two different purposes: scripts vs data.

### Component Responsibilities

| Component | Does | Depends On |
|-----------|------|------------|
| `servo_controller.py` | Ballistic move → observe → correct → click | `local_screen_backend.py`, `vision_analyzer.py`, `cursor_overlay.py` |
| `agent_control_service.py` | Task planning — LLM decides WHAT to do | `servo_controller.py` for HOW to click |
| `training_data_collector.py` | Silently records every servo interaction | Nothing — pure observer, writes to disk |
| `generate_practice_data.py` | Drives agent to click known targets on real pages | `servo_controller.py` |
| `fine_tune.py` | QLoRA training run | Training data on disk, GPU |

### Separation of Concerns

- **agent_control_service** — the BRAIN (decides what to do)
- **servo_controller** — the HAND-EYE (executes clicks precisely)
- **training_data_collector** — the MEMORY (observes and records)
- **training pipeline** — the PRACTICE GYM (improves the model offline)

### What Gets Replaced

The grid overlay system is fully replaced:
- `grid_overlay.py` — no longer called
- Sub-cell refinement in `agent_control_service.py` — removed
- Grid-based decision prompts — replaced with coordinate-based prompts

The crosshair/bullseye overlay (`cursor_overlay.py`) becomes MORE important — it's now the agent's primary spatial reference for servo corrections.

## Build Order

1. **Servo controller** — the core loop, works with existing vision model immediately
2. **Resize virtual display** — 1280x720, update start_agent_display.sh
3. **Integrate into agent_control_service** — replace grid targeting
4. **Training data collector** — silently record servo interactions
5. **Verify end-to-end** — test YouTube comment reply task again
6. **Practice data generator** — automated target-clicking sessions
7. **Training pipeline** — prepare data, fine-tune, eval, deploy
8. **Continuous learning** — retrain as data grows

## Success Criteria

- Agent can click the YouTube "Reply" button reliably (currently misses)
- Servo loop converges in ≤3 corrections for typical UI targets
- Training data accumulates automatically without human intervention
- Fine-tuned `gvk-eye-v1` achieves lower mean pixel error than base model
- System works on any app/website — not just YouTube

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Vision model can't judge distances accurately | Servo loop tolerates imprecise corrections — even "wrong direction, small" eventually converges via oscillation dampening |
| Fine-tuning overfits to specific sites | Practice on diverse pages (YouTube, Google, Wikipedia, GitHub, etc.) |
| 16GB GPU can't fine-tune while agent runs | Training is offline — stop agent, train, restart |
| QLoRA adapter doesn't improve accuracy | Keep base model as fallback; servo loop works without training too |
| qwen3-vl:2b too small for coordinate precision | Escalate to qwen3-vl:8b for servo corrections (already have escalation pattern) |
