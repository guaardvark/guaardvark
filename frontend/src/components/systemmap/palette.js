// Theme-aware palette for the system map.
//
// The map paints ink over the page background rather than over a surface of
// its own, so every colour here is an "r, g, b" triplet plus an alpha chosen
// at the call site. Only the triplets and the node lightness flip between
// modes; alphas keep their meaning in both (low = faint wash, high = solid).

import { useMemo } from "react";
import { useTheme } from "@mui/material";

const rgba = (triplet, alpha) => `rgba(${triplet}, ${alpha})`;

// Compositing toward black gains contrast faster than toward white, so these
// alphas need lifting on a light page. 0 stays 0: canvas gradients fade out
// through ink(0) and a lifted alpha would leave a halo.
const boostAlpha = (alpha) => (alpha === 0 ? 0 : Math.min(1, alpha * 1.2 + 0.06));

const TRIPLETS = {
  dark: {
    ink: "168, 216, 255",
    danger: "255, 110, 110",
    warn: "255, 184, 77",
    ghost: "120, 220, 180",
    finding: "255, 170, 80",
    findingSoft: "255, 220, 130",
    halo: "255, 255, 255",
  },
  light: {
    ink: "13, 38, 66",
    danger: "198, 40, 40",
    warn: "176, 96, 0",
    ghost: "13, 118, 90",
    finding: "199, 106, 0",
    findingSoft: "173, 128, 0",
    halo: "13, 38, 66",
  },
};

const SURFACES = {
  dark: {
    panelBg: "rgba(14, 22, 40, 0.10)",
    fieldBg: "rgba(20, 30, 50, 0.6)",
    tooltipBg: "rgba(20, 40, 60, 0.95)",
    tooltipInk: "rgba(200, 230, 255, 0.95)",
    errorBg: "rgba(255, 100, 100, 0.15)",
    errorInk: "rgba(255, 200, 200, 0.95)",
    errorBorder: "rgba(255, 100, 100, 0.3)",
    haloAlpha: 0.18,
    ringAlpha: 0.75,
    // Multiplier on the distance-derived edge alpha in the canvas.
    edgeAlpha: 0.4,
  },
  light: {
    panelBg: "rgba(255, 255, 255, 0.72)",
    fieldBg: "rgba(255, 255, 255, 0.9)",
    tooltipBg: "rgba(255, 255, 255, 0.98)",
    tooltipInk: "rgba(13, 38, 66, 0.95)",
    errorBg: "rgba(198, 40, 40, 0.10)",
    errorInk: "rgba(140, 20, 20, 0.95)",
    errorBorder: "rgba(198, 40, 40, 0.35)",
    haloAlpha: 0.14,
    ringAlpha: 0.85,
    edgeAlpha: 0.24,
  },
};

// Section hues are authored against the dark map, where nodes sit at ~78%
// lightness. On a light page the same hue has to come down the scale and up in
// saturation to stay legible, so both the canvas and the legend route their
// hues through this one transform.
function toneFor(isLight) {
  return (saturation, lightness, alpha) =>
    isLight
      ? {
          saturation: Math.min(90, saturation + 12),
          lightness: Math.max(26, lightness - 36),
          alpha: Math.min(1, alpha * 1.15 + 0.08),
        }
      : { saturation, lightness, alpha };
}

export function createMapPalette(mode) {
  const isLight = mode === "light";
  const triplets = isLight ? TRIPLETS.light : TRIPLETS.dark;
  const surfaces = isLight ? SURFACES.light : SURFACES.dark;
  const tone = toneFor(isLight);

  const a = isLight ? boostAlpha : (alpha) => alpha;

  return {
    isLight,
    ...surfaces,
    inkRGB: triplets.ink,
    dangerRGB: triplets.danger,
    warnRGB: triplets.warn,
    ghostRGB: triplets.ghost,
    haloRGB: triplets.halo,
    ink: (alpha) => rgba(triplets.ink, a(alpha)),
    danger: (alpha) => rgba(triplets.danger, a(alpha)),
    warn: (alpha) => rgba(triplets.warn, a(alpha)),
    ghost: (alpha) => rgba(triplets.ghost, a(alpha)),
    finding: (alpha) => rgba(triplets.finding, a(alpha)),
    findingSoft: (alpha) => rgba(triplets.findingSoft, a(alpha)),
    halo: (alpha) => rgba(triplets.halo, a(alpha)),
    severity: {
      high: rgba(triplets.danger, 1),
      medium: rgba(triplets.warn, 1),
      low: rgba(triplets.ink, a(0.7)),
      info: rgba(triplets.ink, a(0.4)),
    },
    boostAlpha: a,
    /** Section hue, expressed in dark-map terms and remapped for the mode. */
    hue: (h, saturation, lightness, alpha = 1) => {
      const t = tone(saturation, lightness, alpha);
      return `hsla(${h}, ${t.saturation}%, ${t.lightness}%, ${t.alpha})`;
    },
    tone,
  };
}

export function useMapPalette() {
  const theme = useTheme();
  return useMemo(() => createMapPalette(theme.palette.mode), [theme.palette.mode]);
}
