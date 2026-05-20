# Style Recipes

Creative briefs that bias the Video Editor's Art Director (qwen3-vl + arranger) toward a specific aesthetic — Grunge, Dark, Goth, Cinematic, Music-Video, etc.

Same conceptual layer as `data/agent/recipes.json` (servo recipes), different domain. Eventually authored by Captain Recipe McRecipieface; for now, hand-curated.

## File format

One `.json` per recipe. The loader (`plugins/video_editor/service/style_recipe_loader.py`) reads every file in this directory at request time.

```jsonc
{
  "name": "Grunge",
  "description": "Distorted, high-contrast, raw aesthetic for aggressive content.",

  // Bias the Art Director's clip-to-section scoring. Empty arrays = no bias.
  "art_director_bias": {
    "prefer_subjects": ["object-detail", "crowd"],
    "prefer_energy":   ["high", "frenetic"],
    "prefer_motion":   ["fast", "medium"]
  },

  // Constrain the Art Director's filter/transition choices to this subset of the
  // catalog. Empty arrays = use full catalog. Slugs must match those in
  // plugins/video_editor/mlt/filters.py and transitions.py.
  "filter_palette":     ["high-contrast-bw", "oldfilm", "vignette", "wave-distort"],
  "transition_palette": ["hard-cut", "dip-to-black"],

  // Optional: apply this filter at the track level (V1) for a uniform look.
  "global_filter": "high-contrast-bw",

  // "as-is" = the source clips' audio is discarded, soundtrack replaces it.
  // (Other values reserved for future "duck-under" / "ambient-mix" modes.)
  "audio_treatment": "as-is"
}
```

## Authoring tips

- **Names are case-insensitive on lookup** (`Grunge` and `grunge` resolve the same).
- **Filename stem is fallback identity** — `Grunge.json` loads as name=Grunge even if the JSON omits `name`. Prefer setting `name` explicitly.
- **Empty arrays disable bias** for that field. `default.json` is empty arrays everywhere = no creative direction at all.
- **Unknown filter slugs are tolerated** — the arranger logs and skips them, doesn't crash.

## Roadmap

- A1–A4 ship the loader + arranger plumbing + this `default.json`.
- A separate curation session will author Grunge / Dark / Goth / Cinematic / Music-Video / Wedding / etc.
- Eventually, Captain Recipe McRecipieface generates new recipes from observed user preferences (e.g. "the user always swaps in oldfilm + vignette and removes cross-dissolves" → new recipe candidate).
