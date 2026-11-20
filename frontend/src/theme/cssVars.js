// The active palette as CSS custom properties on :root, for the rules in
// index.css that cannot read the MUI theme (scrollbars, body, grid placeholder).

/**
 * @param {object} palette  a MUI theme palette
 * @returns {Record<string, string>} property name -> colour
 */
export function themeCssVars(palette) {
  return {
    "--bg-default": palette.background.default,
    "--bg-paper": palette.background.paper,
    "--text-primary": palette.text.primary,
    "--text-secondary": palette.text.secondary,
    "--divider": palette.divider,
    "--primary-main": palette.primary.main,
    "--scrollbar-track": palette.background.paper,
    "--scrollbar-thumb": palette.divider,
    // A shade darker than the resting thumb (action.hover was lighter).
    "--scrollbar-thumb-hover": palette.action.disabled,
    "--scrollbar-thumb-active": palette.primary.main,
  };
}
