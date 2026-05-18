# Video Editor

Bin-driven montage assembly. Drop B-roll into a project bin, pick a song, click **Plan** — the **Art Director** (vision model) reads each clip, picks a filter, decides which clip goes in which section of the song, and emits an arrangement. Click **Render** to get a Shotcut-readable `.mlt` and a final `.mp4`. Click **Open in Shotcut** if you want to refine anything by hand.

The end-state goal is the Batch Video Generator workflow: pick a Style Recipe, click **Quick Render**, walk away.

---

## Workflow

```
Drop clips into Bin    ←  Library drag, OS file drop (uploads to data/uploads/Videos/)
Drop song into Song    ←  Library drag, OS file drop (uploads to data/uploads/Audio/)
Pick Style Recipe      ←  Default | Grunge | Dark | Cinematic | Music Video
Pick Detection mode    ←  Audio | Motion | Both (strict) | Both (loose)
                              └─ defaults to Both (strict)

  ┌──[ Plan ]────────────────────────────────────────────────┐
  │  auto-editor analyze each clip   →  kept ranges          │
  │  librosa beat_track + sections   →  song structure       │
  │  ffmpeg sample 3 frames per clip                          │
  │  vision model analyze each clip  →  ClipAnalysis (cached) │
  │  apply Director's Notes overrides                         │
  │  arranger combines + recipe bias →  Arrangement.json     │
  └───────────────────────────────────────────────────────────┘
                            ↓
              Arrangement preview shown in UI

(optional) Click a bin tile → Director's Notes panel
  - Edit subject/energy/palette/motion/mood chips
  - Pick filter from category grid (Color/Motion/Stylize/Glitch)
  - Toggle section-fit chips
  - "Re-analyze" forces fresh vision-model pass (cache bust)

                            ↓
  ┌──[ Render ]──────────────────────────────────────────────┐
  │  compose_arrangement → .mlt with V1/V2 alternation for   │
  │                         transitions, per-clip filter      │
  │                         chains, audio chain                │
  │  melt subprocess     → final .mp4                         │
  │  register both as Documents                                │
  └───────────────────────────────────────────────────────────┘
                            ↓
        Optional: [ Open in Shotcut ] for manual refinement
```

**Quick Render** chains Plan → Render in one click for the recipe-and-go pattern.

---

## Style Recipes

JSON files under `data/agent/style_recipes/`. A recipe biases the Art Director and constrains its filter/transition palette. Bundled recipes:

| Recipe | Look | Filter palette | Transitions |
| --- | --- | --- | --- |
| **Default** | No bias, full catalog | all | all |
| **Grunge** | High-contrast B&W, oldfilm, distorted | `high-contrast-bw oldfilm vignette wave-distort desaturate` | `hard-cut dip-to-black` |
| **Dark** | Cool, vignetted, mysterious | `cool-tint vignette desaturate slow-zoom-in` | `hard-cut cross-dissolve dip-to-black` |
| **Cinematic** | Warm-tinted, dissolves, slow zooms | `warm-tint vignette slow-zoom-in pan-left` | `cross-dissolve luma-circle` |
| **Music Video** | High-energy, vertigo zooms, wipes | `warm-tint cool-tint vertigo slow-zoom-in glow` | `hard-cut luma-wipe luma-circle` |

Author a new recipe by dropping a JSON in the directory — the loader picks it up. See `data/agent/style_recipes/README.md` for the schema.

---

## Filter catalog

| Category | Slugs |
| --- | --- |
| Color | `warm-tint` `cool-tint` `high-contrast-bw` `sepia` `desaturate` |
| Motion | `slow-zoom-in` `vertigo` `pan-left` |
| Stylize | `oldfilm` `vignette` `glow` |
| Glitch | `pixelate` `wave-distort` |

Plus `none` for no filter. All are real MLT filter services — generated `.mlt` files open with editable filter objects in Shotcut.

## Transition catalog

`hard-cut` (no overlap), `cross-dissolve` (0.4s), `dip-to-black` (0.6s), `luma-circle` (0.5s), `luma-wipe` (0.5s).

Non-hard-cut transitions force clip placement on alternating V1 / V2 tracks with the right overlap region; a `<transition>` element bridges them on the tractor.

---

## Output paths

| Path | What lives there |
| --- | --- |
| `data/uploads/Videos/` | OS-dropped bin clips |
| `data/uploads/Audio/` | OS-dropped songs |
| `data/outputs/videos/mlt-projects/` | Generated `.mlt` files (open in Shotcut) |
| `data/outputs/videos/editor-renders/` | Final `.mp4` files |
| `data/outputs/videos/clip-scans/` | Vision-analysis cache (`<hash>.json` per clip) |
| `data/outputs/videos/clip-scans/frames/<hash>/` | Sampled JPEG frames the Art Director saw |
| `data/outputs/videos/auto-editor-scans/` | auto-editor kdenlive XML per scan |

Both `.mlt` and `.mp4` outputs are registered as Documents and show up in the Documents tree.

---

## Endpoints (port 8207)

Read:
- `GET  /health`
- `GET  /status`
- `GET  /config`
- `GET  /recipes`
- `GET  /catalog/filters`
- `GET  /catalog/transitions`
- `GET  /jobs` · `GET /jobs/{id}`
- `GET  /vision/frames/{hash}/{i}` — JPEG of one sampled frame

Write:
- `POST /plan` — submit a Plan job (bin + song + scan mode + recipe + seed + overrides). Returns `{job_id}`.
- `POST /shotcut/compose-arrangement` — render an Arrangement to `.mlt` + `.mp4` (multi-clip + filters + transitions).
- `POST /shotcut/compose` — legacy single-clip path (used by older M3 callers).
- `POST /vision/rescan-clip` — force cache bust + fresh vision-model pass for one clip.
- `POST /vision/clip-hash` — resolve cache key for URL building.
- `POST /auto-editor/trim` — direct auto-editor wrapper (mp4 or kdenlive XML).
- `POST /beat-sync/render` — the original M1 demo flow.
- `POST /open-in-shotcut` — spawn Shotcut on a `.mlt` (path must live under mlt-projects/).

Flask proxies all of these at `/api/video-editor/*`.

---

## Architecture notes

### CrewInterface

The Art Director sits behind a Protocol:

```python
class CrewInterface(Protocol):
    def analyze_clip(self, frames, clip_id, source_path, recipe) -> ClipAnalysis: ...
    def arrange(self, clip_analyses, song, kept_ranges_by_clip, recipe, seed) -> Arrangement: ...
```

`LocalArtDirector` is v1: in-process vision-model call via Ollama + rule-based arranger. When the `film_crew` plugin eventually lands, `FilmCrewClient` (HTTP client to that plugin) drops in with a one-line change in `service/app.py`. **Don't bypass the interface** in call sites — keep model specifics behind it.

### Pipeline orchestration

`service/jobs_pipeline.py` runs four ordered stages, all instrumented for progress reporting:

1. `analyze_mod.analyze_clip` per bin clip → kept-ranges (parallelizable later; currently sequential).
2. `analyze_song` → tempo + 4-section labeled energy split.
3. `frame_sampler.sample_frames` + `crew.analyze_clip` per clip → ClipAnalysis (cached by content hash).
4. `_apply_overrides` if any → `crew.arrange` → Arrangement.

Caches live keyed by file content hash (`(path, mtime, size)` sha256, 16 chars) so the same clip used in two projects shares the same cached vision read.

### Track placement (multi-clip render)

`mlt/timeline_compose.compose_arrangement` walks the arrangement and decides which video track each clip lands on:

- A run of `hard-cut` clips stays on a single track (V1, `playlist0`).
- A non-hard-cut `transition_to_next` swaps the next clip onto the other track.
- Audio always sits on its own track (`playlist_audio`).
- The chosen transition's overlap (e.g. 0.4s for cross-dissolve) extends both clips into the overlap region; a `<transition>` element on the tractor bridges them.

Same source path used multiple times in the arrangement → one shared chain (the LAST filter recommendation wins for that chain). Per-instance filters need filter-track tracks — deferred.

### Drift safety

All time math is anchored to t=0 absolute frames (`frame_math.seconds_to_absolute_frame`). Never delta-accumulate. Verified by `test_frame_math.test_no_drift_over_200_cuts_*`.

---

## Plugin lifecycle

```bash
# bring it up
bash plugins/video_editor/scripts/start.sh

# bring it down
bash plugins/video_editor/scripts/stop.sh

# logs
tail -f logs/video_editor.log
```

First boot creates `plugins/video_editor/venv/` and installs ~250 MB of deps
(librosa, numba, auto-editor, fastapi, lxml). Subsequent boots are <2s.

`config.yaml` overrides:
- `melt.path` — explicit `melt` binary. Auto-resolved from `/snap/shotcut/current/melt` (the wrapper script, NOT `/bin/melt`).
- `auto_editor.path` — explicit `auto-editor` binary. Defaults to the one installed in the plugin venv.
- `output.mlt_projects_dir`, `output.renders_dir` — output paths (relative to project root).
- `registration.backend_url` — where to POST output Documents (default `http://localhost:5002`).
- `beat_sync.subdivision` / `min_clip_seconds` / `tightness` — librosa pacing defaults.

---

## Testing

```bash
backend/venv/bin/python -m pytest plugins/video_editor/tests/ -v
```

101 tests covering: drift-safe frame math, multi-modal auto-editor, song-section labeling, clip-hash stability, recipe loading, arranger reproducibility, filter catalog round-trip, transition emission, multi-clip composition, JSON-tolerant Art Director parsing, override application.
