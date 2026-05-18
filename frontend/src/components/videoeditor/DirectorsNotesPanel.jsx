// Right-rail override panel for the Art Director's per-clip decisions.
// Shows the chosen tags as editable chips + a filter dropdown + a
// "Re-analyze" affordance (placeholder for A3+ cache bust).
//
// v1 keeps the override edits LOCAL to the page state — the next Plan run
// will overwrite them. v2 will persist overrides into the plan job so a
// Re-plan respects them.

import React, { useState, useMemo } from "react";
import {
  Box, Stack, Typography, Chip,
  Button, Divider, Tooltip, CircularProgress,
} from "@mui/material";
import { Refresh as RefreshIcon } from "@mui/icons-material";
import { frameThumbnailUrl } from "../../api/videoEditorService";

const SUBJECTS  = ["wide-landscape", "character-closeup", "object-detail", "crowd", "text-or-ui", "abstract"];
const ENERGIES  = ["calm", "medium", "high", "frenetic"];
const PALETTES  = ["warm", "cool", "neutral", "high-contrast"];
const MOTIONS   = ["static", "slow", "medium", "fast"];
const MOODS     = ["uplifting", "tense", "nostalgic", "aggressive", "mysterious", "playful"];
const SECTIONS  = ["intro", "build", "drop", "outro", "any"];

const FILTER_CATEGORIES = {
  Color:   ["warm-tint", "cool-tint", "high-contrast-bw", "sepia", "desaturate"],
  Motion:  ["slow-zoom-in", "vertigo", "pan-left"],
  Stylize: ["oldfilm", "vignette", "glow"],
  Glitch:  ["pixelate", "wave-distort"],
};

// One cycling chip — clicking advances to the next allowed value.
function CycleChip({ label, value, options, onChange }) {
  const idx = Math.max(0, options.indexOf(value));
  const next = options[(idx + 1) % options.length];
  return (
    <Tooltip title={`Click to cycle to "${next}"`}>
      <Chip
        size="small"
        label={`${label}: ${value}`}
        onClick={() => onChange(next)}
        sx={{ cursor: "pointer", "&:hover": { backgroundColor: "action.hover" } }}
      />
    </Tooltip>
  );
}

const DirectorsNotesPanel = ({
  clipAnalysis,   // ClipAnalysis dict from planJob.result.clip_analyses
  onOverride,    // (patch: Partial<ClipAnalysis>) => void
  onReanalyze,   // () => void   — cache bust + re-vision-this-clip
  rescanning = false,
  clipHash,       // string — needed to build frame thumbnail URLs (optional)
}) => {
  if (!clipAnalysis) {
    return (
      <Box sx={{ p: 2 }}>
        <Typography variant="caption" color="text.secondary">
          Select a clip in the Bin to see the Art Director&apos;s read.
        </Typography>
      </Box>
    );
  }

  const set = (key) => (val) => onOverride({ [key]: val });

  return (
    <Box sx={{ p: 1.5, display: "flex", flexDirection: "column", gap: 1.25 }}>
      <Stack direction="row" justifyContent="space-between" alignItems="center">
        <Typography variant="subtitle2" fontWeight="bold">Director&apos;s Notes</Typography>
        {clipAnalysis.cached && (
          <Chip size="small" label="cached" variant="outlined" sx={{ height: 18, fontSize: "0.65rem" }} />
        )}
      </Stack>

      {clipHash && (
        <Stack direction="row" spacing={0.5} sx={{ mt: 0.5 }}>
          {[0, 1, 2].map((i) => (
            <Box
              key={i}
              component="img"
              src={frameThumbnailUrl(clipHash, i)}
              alt={`frame ${i + 1}`}
              sx={{
                width: "33%",
                aspectRatio: "16/9",
                objectFit: "cover",
                borderRadius: 0.5,
                border: 1,
                borderColor: "divider",
                backgroundColor: "background.default",
              }}
              onError={(e) => { e.currentTarget.style.visibility = "hidden"; }}
            />
          ))}
        </Stack>
      )}

      <Stack spacing={0.5}>
        <CycleChip label="subject"  value={clipAnalysis.subject}          options={SUBJECTS}  onChange={set("subject")} />
        <CycleChip label="energy"   value={clipAnalysis.energy}           options={ENERGIES}  onChange={set("energy")} />
        <CycleChip label="palette"  value={clipAnalysis.dominant_palette} options={PALETTES}  onChange={set("dominant_palette")} />
        <CycleChip label="motion"   value={clipAnalysis.motion}           options={MOTIONS}   onChange={set("motion")} />
        <CycleChip label="mood"     value={clipAnalysis.mood}             options={MOODS}     onChange={set("mood")} />
      </Stack>

      <Divider />

      <Box>
        <Typography variant="caption" color="text.secondary">Filter</Typography>
        <Stack direction="row" spacing={0.5} flexWrap="wrap" sx={{ mt: 0.5 }}>
          <Chip
            size="small"
            label="none"
            color={(clipAnalysis.recommended_filter || "none") === "none" ? "primary" : "default"}
            variant={(clipAnalysis.recommended_filter || "none") === "none" ? "filled" : "outlined"}
            onClick={() => set("recommended_filter")("none")}
            sx={{ mb: 0.5 }}
          />
        </Stack>
        {Object.entries(FILTER_CATEGORIES).map(([cat, slugs]) => (
          <Box key={cat} sx={{ mt: 0.75 }}>
            <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.65rem", textTransform: "uppercase" }}>
              {cat}
            </Typography>
            <Stack direction="row" spacing={0.5} flexWrap="wrap" sx={{ mt: 0.25 }}>
              {slugs.map((slug) => {
                const selected = clipAnalysis.recommended_filter === slug;
                return (
                  <Chip
                    key={slug}
                    size="small"
                    label={slug}
                    color={selected ? "primary" : "default"}
                    variant={selected ? "filled" : "outlined"}
                    onClick={() => set("recommended_filter")(slug)}
                    sx={{ mb: 0.5 }}
                  />
                );
              })}
            </Stack>
          </Box>
        ))}
      </Box>

      <Box>
        <Typography variant="caption" color="text.secondary">Best fit for</Typography>
        <Stack direction="row" spacing={0.5} flexWrap="wrap" sx={{ mt: 0.5 }}>
          {SECTIONS.map((s) => {
            const selected = (clipAnalysis.best_section_fit || []).includes(s);
            return (
              <Chip
                key={s}
                size="small"
                label={s}
                color={selected ? "primary" : "default"}
                variant={selected ? "filled" : "outlined"}
                onClick={() => {
                  const cur = clipAnalysis.best_section_fit || [];
                  const next = cur.includes(s) ? cur.filter((x) => x !== s) : [...cur, s];
                  set("best_section_fit")(next.length ? next : ["any"]);
                }}
                sx={{ mb: 0.5 }}
              />
            );
          })}
        </Stack>
      </Box>

      <Button
        size="small"
        variant="outlined"
        startIcon={rescanning ? <CircularProgress size={16} /> : <RefreshIcon />}
        onClick={onReanalyze}
        disabled={!onReanalyze || rescanning}
      >
        {rescanning ? "Re-analyzing..." : "Re-analyze this clip"}
      </Button>

      <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5 }}>
        Overrides are local until the next Plan. Click Plan again to use the recipe + your edits.
      </Typography>
    </Box>
  );
};

export default DirectorsNotesPanel;
