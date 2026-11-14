// Foreground colours for content on a user-chosen background (a dashboard
// card's colour), so text, icons, dividers, inputs and chips stay readable
// whatever the app theme is.
import { createTheme, darken, decomposeColor, getContrastRatio, lighten } from "@mui/material/styles";
import { deepmerge } from "@mui/utils";

const WHITE = [255, 255, 255];
const BLACK = [0, 0, 0];

const rgb = (values) => `rgb(${values.map((v) => Math.round(v)).join(", ")})`;

const TONES = ["primary", "secondary", "success", "warning", "error", "info"];

/**
 * Black or white ink for `background`, whichever contrasts more (WCAG), and
 * each text tier at the lowest opacity that still meets its ratio: primary 7:1,
 * secondary 4.5:1, disabled 3:1 (or full ink when the colour cannot reach it).
 * Returns null for a colour MUI cannot parse.
 */
export function foregroundFor(background) {
  let bg;
  try {
    bg = decomposeColor(background).values.slice(0, 3);
  } catch {
    return null;
  }
  const bgCss = rgb(bg);
  const useWhite = getContrastRatio("#fff", bgCss) >= getContrastRatio("#000", bgCss);
  const ink = useWhite ? WHITE : BLACK;
  const over = (a) => rgb(ink.map((c, i) => c * a + bg[i] * (1 - a)));
  const lowest = (min) =>
    [0.5, 0.6, 0.7, 0.8, 0.87, 0.95, 1].find((a) => getContrastRatio(over(a), bgCss) >= min) ?? 1;
  const tint = (a) => `rgba(${ink.join(", ")}, ${a})`;
  return {
    mode: useWhite ? "dark" : "light",
    background: bgCss,
    text: { primary: tint(lowest(7)), secondary: tint(lowest(4.5)), disabled: tint(lowest(3)) },
    divider: tint(0.18),
    action: {
      active: tint(lowest(4.5)),
      hover: tint(0.08),
      selected: tint(0.16),
      focus: tint(0.16),
      disabled: tint(0.3),
      disabledBackground: tint(0.12),
    },
  };
}

/**
 * What a translucent `color` looks like over `backdrop`, as an opaque colour;
 * `color` itself when it is opaque or cannot be parsed. Contrast has to be
 * judged against this, not against the colour with its alpha dropped.
 */
export function flattenOver(color, backdrop) {
  try {
    const [r, g, b, a = 1] = decomposeColor(color).values;
    if (a >= 1) return color;
    const under = decomposeColor(backdrop).values;
    return rgb([r, g, b].map((c, i) => c * a + under[i] * (1 - a)));
  } catch {
    return color;
  }
}

/**
 * `color` if it reads as text on `background` (4.5:1), otherwise the nearest
 * lighter (toward white ink) or darker shade of it that does.
 */
function readableOn(color, background, towardWhite) {
  const shift = towardWhite ? lighten : darken;
  for (const k of [0, 0.15, 0.3, 0.45, 0.6, 0.75, 0.9]) {
    const shade = k ? shift(color, k) : color;
    if (getContrastRatio(shade, background) >= 4.5) return shade;
  }
  return towardWhite ? "#fff" : "#000";
}

/** The theme's primary and status colours, each shade made readable on the card. */
function tonesOn(palette, fg) {
  const towardWhite = fg.mode === "dark";
  const tones = {};
  TONES.forEach((key) => {
    const entry = palette[key];
    if (!entry?.main) return;
    const tone = {};
    ["main", "light", "dark"].forEach((shade) => {
      if (entry[shade]) tone[shade] = readableOn(entry[shade], fg.background, towardWhite);
    });
    tone.contrastText =
      getContrastRatio("#fff", tone.main) >= getContrastRatio("#000", tone.main) ? "#fff" : "#000";
    tones[key] = tone;
  });
  return tones;
}

/**
 * For <ThemeProvider theme={...}>: the surrounding theme with its mode, text,
 * divider, action and background set for `background`, so everything inside
 * follows without patches. Fonts and shapes stay the app theme's, and so do
 * its primary and status colours wherever they read on the card; where one
 * does not, a lighter or darker shade of it is used. Returns null when
 * nothing should change.
 */
export function themeOnColor(background) {
  const fg = background ? foregroundFor(background) : null;
  if (!fg) return null;
  return (outer) => {
    // Nothing above provides a theme (a card rendered on its own): start from MUI's default.
    const base = outer?.palette ? outer : createTheme();
    return deepmerge(base, {
      palette: {
        ...tonesOn(base.palette, fg),
        mode: fg.mode,
        text: fg.text,
        divider: fg.divider,
        action: fg.action,
        background: { default: background, paper: background },
      },
    });
  };
}
