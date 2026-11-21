/**
 * Shared look for the agent's steps inside a chat reply (reasoning, the
 * agent trail, tool calls): a coloured title and plain text, with no fills
 * or borders, so every kind of step reads as one sequence.
 */

// The accent's main tone is tuned as a fill; as small text on a dark
// surface its lighter tone is the readable one.
const accentText = (theme) =>
  theme.palette.mode === "dark" ? theme.palette.primary.light : theme.palette.primary.main;

/**
 * Title colour per step kind, as `sx` colour values. Reasoning uses the
 * warning hue because a theme's accent and secondary may both be greys,
 * while its status hues always carry colour.
 */
export const STEP_COLORS = {
  step: "warning.main",
  thinking: "warning.main",
  note: accentText,
  toolPending: accentText,
  toolSuccess: "success.main",
  toolError: "error.main",
  toolIdle: "text.secondary",
};

/** Clickable title row of a collapsible step. */
export const stepHeaderSx = {
  display: "flex",
  alignItems: "center",
  gap: 0.5,
  minWidth: 0,
  py: 0.25,
  cursor: "pointer",
};

/** Title text of a step; pair with a colour from STEP_COLORS. */
export const stepTitleSx = {
  fontWeight: 600,
  fontSize: "0.75rem",
  lineHeight: 1.5,
};

/** Body text of a step, in the reply's own font. */
export const stepBodySx = {
  m: 0,
  fontFamily: "inherit",
  fontSize: "0.75rem",
  lineHeight: 1.5,
  color: "text.primary",
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
};

/** A step body that scrolls inside a height that also fits a phone. */
export const stepScrollSx = {
  maxHeight: "min(45vh, 360px)",
  overflowY: "auto",
  overflowX: "hidden",
};
