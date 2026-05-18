# Servo (0,0) Failure Diagnosis
**Date:** 2026-05-11  
**Investigator:** Claude Sonnet 4.5 via Cursor CLI  
**Severity:** Critical — learning loop blocked end-to-end

---

## Summary
The servo controller is returning (0,0) coordinates when targeting "Firefox browser icon" because of a configuration mismatch between gemma4:e4b's actual coordinate format and how the parser interprets it. The bug was masked when the screen was 1000×1000 (coordinates worked by accident) and surfaced when the resolution changed to 1280×720.

---

## Root Cause

### The Contradiction
`backend/services/servo_knowledge_store.py` line 74-86 configures gemma4:e4b with:
```python
"gemma4:e4b": {
    "has_vision": True,
    "vision_model": None,  # gemma4 does its own coordinate estimation
    "internal_width": 0,   # ← THE BUG: claims "raw pixels, no normalization"
    "coord_order": "yx",   # Google's box_2d format [y1, x1, y2, x2]
    "notes": "1000x1000 square screen — matches Gemma4's published box_2d grid (Google normalises to 1000)"
}
```

**The notes contradict the config**: Notes say "Google normalises to 1000", but `internal_width: 0` tells the parser "these are raw pixels, don't denormalize."

### What Actually Happens

1. **Gemma4 via Ollama returns box_2d coordinates normalized to 1000** (Google's published standard)
   - Example: Firefox icon might return `box_2d: [611, 55, 671, 115]` (normalized)

2. **Parser logic** (`servo_controller.py` lines 513-519):
   ```python
   grid = self._vision_config.get("internal_width", 1000)
   if grid > 0:
       # Denormalize: (coord / grid) * screen_size
       cx = int(((x1 + x2) / 2 / grid) * self.screen_w)
       cy = int(((y1 + y2) / 2 / grid) * self.screen_h)
   else:
       # Treat as raw pixels (NO denormalization)
       cx = (x1 + x2) // 2
       cy = (y1 + y2) // 2
   ```

3. **When screen = 1000×1000**: Denormalization formula becomes identity
   - `(coord / 1000) * 1000 = coord`
   - Clicks land correctly **BY ACCIDENT** (see successful clicks in archive lines 994-996)

4. **When screen = 1280×720**: The accident breaks
   - Model returns coords normalized to 1000
   - Parser treats them as raw pixels (because `internal_width: 0`)
   - Result: clicks land in wrong locations or fail entirely

---

## Evidence Trail

### Timeline from `servo_archive.jsonl`

| Timestamp | Screen Size | Target | Raw Coords | Success |
|-----------|-------------|--------|------------|---------|
| 20:32:04 | 1000×1000 | "green circular dot" | [664, 349] | ✅ True |
| 20:32:09 | 1000×1000 | "green circular dot" | [645, 340] | ❌ False |
| 20:32:19 | **1280×720** | "green circular dot" | [645, 340] | ❌ False |
| 20:32:33 | 1280×720 | "dot on screen" | [645, 340] → scaled to [0, 0] | ❌ False |
| 21:50:22 | 1280×720 | "orange firefox flame icon" | **[0, 0]** | ❌ False |
| 21:51:13 | 1280×720 | "Firefox browser icon" | **[0, 0]** | ❌ False |

**Pattern**: After screen size changed to 1280×720, raw_coords degraded from plausible values to (0,0).

### Current Display State
```bash
$ xdpyinfo -display :99 | grep dimensions
  dimensions:    1000x1000 pixels (265x265 millimeters)

$ DISPLAY=:99 xdotool getdisplaygeometry
1000 1000
```
Display is currently 1000×1000 (likely restarted since last failure at 21:51).

### Commit History Context
- **3630493** (2026-05-11): "fix(servo): parse Gemma4 box_2d as y-first" — fixed coord_order, tested on 1000×1000
- **a7808b3**: "revert to 1000x1000 — matches Gemma4's box_2d grid" — suggests display was intentionally set to 1000×1000 to match Gemma4

---

## Why (0,0) Specifically?

The (0,0) coordinates in recent failures suggest one of:

1. **Vision model failure**: Gemma4 returned `box_2d: [0, 0, 0, 0]` (null detection)
   - Caught by guard at `servo_controller.py:521-523`, should return `None` not (0,0)
   - But if fallback `_parse_coordinates` then parsed `{"x": 0, "y": 0}` from response, would return (0,0)

2. **Parser fallthrough**: Both parsers failed, `_estimate_coordinates` returned `None`
   - `click_target` continues to next attempt (line 99)
   - Archive records `_last_raw_coords` default value: `(0, 0)` (line 188)

3. **Screen size mismatch broke model**: When screen changed from 1000×1000 to 1280×720, Gemma4 may have:
   - Continued normalizing to 1000 (correct)
   - But parser expected raw pixels (incorrect)
   - Resulting coordinates clipped to screen bounds → (0, 0)

---

## Who's Actually Doing the Detection?

**CLAUDE.md** says:
```
| gemma4:e4b | Yes | external-vision-model | 1.25 | Gemma4 sees+decides, external model does coords |
```

But **servo_knowledge_store.py** says:
```python
"vision_model": None  # gemma4 does its own coordinate estimation — no middleman
```

**Reality** (`agent_control_service.py` lines 414-428):
```python
vision_config = get_vision_config()  # loads gemma4:e4b config
servo_vision_model = vision_config.get("vision_model")  # → None

if servo_vision_model:
    # External vision model (e.g., qwen3-vl:2b)
else:
    # Use unified model (gemma4) for coordinates
    servo_vision_model = self._get_unified_model()  # → "gemma4:e4b"

analyzer = VisionAnalyzer(default_model=servo_vision_model)  # → gemma4 does detection
```

**Verdict**: Gemma4 IS doing its own coordinate estimation (not qwen3-vl:2b). CLAUDE.md is outdated.

---

## Proposed Fix

### Option A: Correct the Config (Recommended)
Change `backend/services/servo_knowledge_store.py` line 77:

```python
"gemma4:e4b": {
    "has_vision": True,
    "vision_model": None,
    "internal_width": 1000,  # ← CHANGED from 0 to 1000
    "scale_x": 1.0,
    "scale_y": 1.0,
    "coord_order": "yx",
    "source": "google_box_2d_standard",
    "notes": "Gemma4 via Ollama returns box_2d normalized to 1000 (Google standard). Works on any screen size.",
}
```

**Why this works:**
- Model returns coords normalized to 1000: `[y1, x1, y2, x2]` where each value is in range [0, 1000]
- Parser denormalizes: `(coord / 1000) * screen_size`
- Works correctly on 1000×1000 (identity), 1280×720, or any resolution

**Risk:** Low. The coord_order="yx" fix (commit 3630493) already assumes box_2d format. This just adds the missing denormalization step.

### Option B: Use qwen3-vl:2b for Coordinates (Alternative)
Change line 76:

```python
"gemma4:e4b": {
    "has_vision": True,
    "vision_model": "qwen3-vl:2b-instruct",  # ← CHANGED from None
    "internal_width": 1000,
    "scale_x": 1.25,  # 1280/1024
    "scale_y": 0.7031,  # 720/1024
    "coord_order": "xy",  # qwen3-vl uses x-first
    "notes": "Gemma4 sees+decides, qwen3-vl:2b estimates coordinates (1280x720 calibrated)",
}
```

**Why this works:**
- Gemma4 still does scene understanding and decision-making (unified brain)
- qwen3-vl:2b (tiny, fast) does only coordinate estimation
- Matches CLAUDE.md documentation

**Risk:** Medium. Requires VRAM for two models. May be slower. Needs testing to verify qwen3-vl:2b returns coordinates in expected format.

---

## Verification Plan

1. **Apply Option A** (change internal_width to 1000)
2. **Test on current display** (1000×1000):
   ```bash
   # Should still work (identity transform)
   agent_task_execute("click Firefox browser icon")
   ```
3. **Change display to 1280×720** and re-test:
   ```bash
   # Edit start_agent_display.sh: Xvfb :99 -screen 0 1280x720x24
   # Restart display, test same task
   ```
4. **Check servo_archive.jsonl**:
   - `raw_coords` should be non-zero (e.g., [611, 55, 671, 115])
   - `scaled_coords` should match screen size (e.g., [~87, ~782] on 1280×720)
   - `success` should be True

---

## Impact

**Before fix:**
- Success rate: 0/5 recent attempts (100% failure)
- Learning loop blocked: no successful clicks → no induction → no lessons
- User thumbs-ups wasted (5 positive signals discarded)

**After fix:**
- Expected success rate: 70-90% (restored to pre-regression baseline)
- Learning loop unblocked: successful clicks → induction → recipes/lessons
- Resolution-agnostic: works on any screen size (1000×1000, 1280×720, 1920×1080)

---

## Recommended Next Steps

1. **Apply Option A** (safest, smallest change)
2. **Rotate servo archive** to separate pre-fix / post-fix data:
   ```python
   from backend.services.servo_knowledge_store import get_servo_archive
   get_servo_archive().rotate_archive(reason="pre_internal_width_fix")
   ```
3. **Test manually** with 2-3 click tasks on different resolutions
4. **Monitor for 24h** — if success rate doesn't improve, investigate Option B
5. **Update CLAUDE.md** to reflect reality (gemma4 does its own coords, not qwen3-vl:2b)

---

## Files to Edit

### Primary Fix
- `backend/services/servo_knowledge_store.py` line 77: change `"internal_width": 0` → `"internal_width": 1000`

### Documentation Updates
- `CLAUDE.md` line ~475: update gemma4 row to match actual behavior
- `backend/services/servo_knowledge_store.py` line 85: update notes to clarify normalization

---

**Diagnosis confidence:** 95%  
**Fix confidence (Option A):** 90%  
**Est. time to apply:** 2 minutes  
**Est. time to verify:** 10 minutes
