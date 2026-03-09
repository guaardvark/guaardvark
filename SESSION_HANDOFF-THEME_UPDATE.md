# Session Handoff: Guaardvark Theme Update

**Date:** 2026-03-06
**Status:** Complete, ready for testing and promotion to default

## What Was Done

Created a new "Guaardvark" theme for the frontend that matches the visual design of guaardvark.com as closely as possible. The theme is currently available in Settings > Theme as "Guaardvark" (listed first in the theme selector).

## Files Changed

### 1. `frontend/src/theme/themes.js`
- Added `guaardvarkTheme` definition with full component overrides
- Listed as first entry in the `themes` export map

### 2. `frontend/index.html`
- Added Google Fonts preconnect and import for Raleway (weights 200-600) and Lato (weights 300, 400, 700)

### 3. `docs/plans/2026-03-06-guaardvark-theme-design.md`
- Design document with approved specifications

## Theme Specifications

### Colors
| Token | Value | Notes |
|-------|-------|-------|
| accent (primary) | `#8a9bae` | Muted steel-blue |
| accentDark | `#6b7d91` | Hover/pressed states |
| accentLight | `#a8b5c4` | Highlights |
| secondary | `#9e9e9e` | Neutral gray |
| background | `#000000` | Pure black (matches website) |
| bgPaper | `#080a0e` | Very dark near-black with subtle blue tint |
| textPrimary | `rgba(255, 255, 255, 0.7)` | Matches website heading opacity |
| textSecondary | `rgba(255, 255, 255, 0.45)` | Matches website body text opacity |
| divider | `rgba(138, 155, 174, 0.15)` | Steel-blue tinted borders |

### Typography
- **Body font:** Lato, sans-serif
- **Headings (h1-h6):** Raleway, sans-serif — weight 300, uppercase, letter-spacing 3px
- **Subtitles:** Raleway, sans-serif — weight 400, letter-spacing 1px
- **Buttons:** Raleway, sans-serif — uppercase, letter-spacing 2px

### Component Overrides
- **Cards/Paper:** Glass-morphism — `rgba(255,255,255,0.03)` bg, `rgba(138,155,174,0.15)` border, `backdrop-filter: blur(12px)`
- **Buttons:** Ghost style — near-transparent bg, steel-blue border, hover glow
- **AppBar:** `rgba(0,0,0,0.8)` with blur, steel-blue bottom border
- **Drawer:** `rgba(0,0,0,0.9)` with blur, steel-blue right border
- **Alerts:** Glass panels with colored borders (steel/green/amber/red)
- **Scrollbar:** Ultra-thin (6px), near-invisible

## Bugs Fixed During Implementation

### White folder window on DocumentsPage
- **Cause:** `bgPaper` was set to `rgba(255, 255, 255, 0.03)`. FolderWindowWrapper.jsx line 155 uses `alpha(theme.palette.background.paper, 0.95)` which replaced the 0.03 alpha with 0.95, producing `rgba(255, 255, 255, 0.95)` (white)
- **Fix:** Changed `bgPaper` to solid `#080a0e` (visually equivalent on black, safe with `alpha()`)

### Card borders too faint
- **Cause:** Initial border was `rgba(255, 255, 255, 0.06)` — invisible on pure black
- **Fix:** Changed to `rgba(138, 155, 174, 0.15)` — steel-blue tinted, matching folder window borders from FolderWindowWrapper.jsx

### Settings page separators invisible
- **Cause:** Theme divider token was `rgba(255, 255, 255, 0.06)`
- **Fix:** Updated divider to `rgba(138, 155, 174, 0.15)` — consistent with all other borders

## Tomorrow: Making It the Default Theme

To make "guaardvark" the default theme, change the initial state in the Zustand store:

**File:** `frontend/src/stores/useAppStore.js`
- Find: `themeName: "default"`
- Change to: `themeName: "guaardvark"`

Note: Existing users with a saved theme preference in localStorage will keep their current theme. Only new users (or after clearing localStorage) will get the new default. To force the switch for existing installs, you could also rename the theme key from `"guaardvark"` to `"default"` and rename the current default to something like `"classic"`.

## Preview Gradient
`linear-gradient(135deg, #8a9bae, #000000)` — steel-to-black (shown in theme selector)
