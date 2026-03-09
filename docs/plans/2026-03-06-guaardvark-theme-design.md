# Guaardvark Theme Design

**Date:** 2026-03-06
**Status:** Approved

## Overview

Add a new "Guaardvark" theme to the frontend theme system that matches the visual design of guaardvark.com. Ultra-minimal monochrome dark with glass-morphism.

## Color Palette

| Token | Value | Rationale |
|-------|-------|-----------|
| accent | `#8a9bae` | Muted steel-blue for interactive elements |
| accentDark | `#6b7d91` | Darker steel for hover/pressed |
| accentLight | `#a8b5c4` | Lighter steel for highlights |
| secondary | `#9e9e9e` | Neutral gray |
| secondaryDark | `#757575` | — |
| secondaryLight | `#bdbdbd` | — |
| bg | `#000000` | Pure black (matches website) |
| bgPaper | `rgba(255, 255, 255, 0.03)` | Glass panels |
| textPrimary | `rgba(255, 255, 255, 0.7)` | Website heading opacity |
| textSecondary | `rgba(255, 255, 255, 0.45)` | Website body text opacity |
| divider | `rgba(255, 255, 255, 0.06)` | Website card border opacity |

## Typography

- **Body:** Lato, sans-serif (base fontFamily)
- **Headings:** Raleway, sans-serif (h1-h6 via typography overrides)
- **Buttons:** Raleway, sans-serif (uppercase, letter-spacing: 2px)
- **Heading weight:** 300 (light)
- **Heading letter-spacing:** 3px, uppercase

## Component Overrides

### Paper / Cards
- Background: `rgba(255, 255, 255, 0.03)`
- Border: `1px solid rgba(255, 255, 255, 0.06)`
- Backdrop-filter: `blur(12px)`
- Border-radius: 8px

### Buttons
- Ghost style: near-transparent bg, thin steel border
- Font: Raleway, uppercase, letter-spacing: 2px
- Hover: subtle steel-blue glow

### AppBar / Drawer
- Glass panels with blur, matching card treatment

### Alerts
- Glass panel bg with colored borders (steel/green/amber/red)

### Scrollbar
- Ultra-thin, near-invisible

## Files Changed

1. `frontend/src/theme/themes.js` — Add guaardvarkTheme
2. `frontend/index.html` — Add Google Fonts link for Raleway + Lato

## Theme Metadata

- Key: `"guaardvark"`
- Label: `"Guaardvark"`
- Description: `"Ultra-minimal monochrome theme inspired by guaardvark.com"`
- Preview gradient: `linear-gradient(135deg, #8a9bae, #000000)`
