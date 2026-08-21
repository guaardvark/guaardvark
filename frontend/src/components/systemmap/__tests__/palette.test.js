import { describe, it, expect } from "vitest";
import { createMapPalette } from "../palette";

const PAGE_BG = { light: [250, 250, 250], dark: [18, 18, 18] };

function parseRgba(value) {
  const [r, g, b, alpha] = value.match(/[0-9.]+/g).map(Number);
  return { rgb: [r, g, b], alpha };
}

function composite([r, g, b], alpha, bg) {
  return [r, g, b].map((channel, i) => channel * alpha + bg[i] * (1 - alpha));
}

function luminance(rgb) {
  const [r, g, b] = rgb.map((channel) => {
    const c = channel / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(mode, alpha) {
  const bg = PAGE_BG[mode];
  const { rgb, alpha: applied } = parseRgba(createMapPalette(mode).ink(alpha));
  const lFg = luminance(composite(rgb, applied, bg));
  const lBg = luminance(bg);
  const [hi, lo] = lFg > lBg ? [lFg, lBg] : [lBg, lFg];
  return (hi + 0.05) / (lo + 0.05);
}

describe("system map palette", () => {
  // The map was authored against the dark theme; the light theme is only
  // "fixed" if its ink is at least as readable at every level the page uses.
  it.each([0.4, 0.55, 0.7, 0.85, 0.95])("light ink at %s reads at least as well as dark", (alpha) => {
    expect(contrast("light", alpha)).toBeGreaterThanOrEqual(contrast("dark", alpha));
  });

  it("clears AA for body-weight ink", () => {
    expect(contrast("light", 0.85)).toBeGreaterThanOrEqual(4.5);
    expect(contrast("light", 0.95)).toBeGreaterThanOrEqual(4.5);
  });

  it("keeps faint washes faint", () => {
    expect(parseRgba(createMapPalette("light").ink(0.04)).alpha).toBeLessThan(0.2);
  });

  it("keeps a fully transparent gradient stop transparent", () => {
    // The canvas fades glows out with ink(0); a lifted alpha would leave a halo.
    expect(parseRgba(createMapPalette("light").ink(0)).alpha).toBe(0);
    expect(parseRgba(createMapPalette("dark").ink(0)).alpha).toBe(0);
  });

  it("brings section hues down the lightness scale for a light page", () => {
    const [, , lightness] = createMapPalette("light").hue(207, 75, 78, 0.85).match(/[0-9.]+/g).map(Number);
    const [, , darkLightness] = createMapPalette("dark").hue(207, 75, 78, 0.85).match(/[0-9.]+/g).map(Number);
    expect(lightness).toBeLessThan(50);
    expect(darkLightness).toBe(78);
  });
});
