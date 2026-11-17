"""LLX CLI visual theme — colors, styles, console factory, and logo."""

import os
import select
import shutil
import sys
import time

try:
    import termios
    import tty
except ImportError:  # Windows has no POSIX terminal, so the splash stays still.
    termios = None  # type: ignore[assignment]
    tty = None  # type: ignore[assignment]

from rich.align import Align
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.style import Style
from rich.theme import Theme
from rich.text import Text
from rich.table import Table
from rich.panel import Panel
from rich import box

from llx.banner_art import COMPACT_PLATE, PLATE, POWER, TRACE, WORDMARK

# ── Theme Palettes ────────────────────────────────────────────
# Each palette defines the colors used to build the semantic Rich theme.
# Status colors (success/error/warning/info) are shared across all themes.

_SHARED_STATUS = {
    "success":      "#00b894",    # Mint green
    "error":        "#ff6b6b",    # Soft red
    "warning":      "#fdcb6e",    # Warm amber
    "info":         "#74b9ff",    # Sky blue
}

THEMES = {
    "default": {
        "label":        "Default",
        "description":  "Deep blue gradient",
        "brand":        "#1a0a9e",
        "brand_bright": "#3a2ae8",
        "accent":       "#5545ff",
        "accent_bright":"#7b6eff",
        "muted":        "#4a4570",
        "dim_text":     "#8880b0",
        "gradient":     [(30, 8, 170), (42, 18, 210), (50, 32, 240), (65, 55, 255)],
    },
    "teal": {
        "label":        "Teal",
        "description":  "Clean dark theme with teal accents",
        "brand":        "#008080",
        "brand_bright": "#26a6a6",
        "accent":       "#ce93d8",
        "accent_bright":"#e1bee7",
        "muted":        "#636e72",
        "dim_text":     "#a0a0a0",
        "gradient":     [(0, 102, 102), (0, 128, 128), (38, 166, 166)],
    },
    "musk": {
        "label":        "Musk",
        "description":  "Futuristic neon cyan and red",
        "brand":        "#00e5ff",
        "brand_bright": "#18ffff",
        "accent":       "#ff1744",
        "accent_bright":"#ff5252",
        "muted":        "#555555",
        "dim_text":     "#9e9e9e",
        "gradient":     [(0, 184, 212), (0, 229, 255), (24, 255, 255)],
    },
    "hacker": {
        "label":        "Matrix Hacker",
        "description":  "Terminal green-on-black",
        "brand":        "#00ff41",
        "brand_bright": "#33ff66",
        "accent":       "#39ff14",
        "accent_bright":"#66ff47",
        "muted":        "#2e6b30",
        "dim_text":     "#5aaf5c",
        "gradient":     [(0, 184, 47), (0, 255, 65), (51, 255, 102)],
    },
    "vader": {
        "label":        "Vader",
        "description":  "Dark imposing black and red",
        "brand":        "#d32f2f",
        "brand_bright": "#f44336",
        "accent":       "#b0b0b0",
        "accent_bright":"#e0e0e0",
        "muted":        "#555555",
        "dim_text":     "#9e9e9e",
        "gradient":     [(183, 28, 28), (211, 47, 47), (244, 67, 54)],
    },
    "guaardvark": {
        "label":        "Guaardvark",
        "description":  "Ultra-minimal monochrome",
        "brand":        "#8a9bae",
        "brand_bright": "#a8b5c4",
        "accent":       "#9e9e9e",
        "accent_bright":"#bdbdbd",
        "muted":        "#4a5568",
        "dim_text":     "#718096",
        "gradient":     [(107, 125, 145), (138, 155, 174), (168, 181, 196)],
    },
    "day": {
        "label":        "Day",
        "description":  "Light terminal, ink on paper",
        "brand":        "#1a0a9e",
        "brand_bright": "#2c1bb8",
        "accent":       "#0b6e4f",
        "accent_bright":"#148f68",
        "muted":        "#6b7280",
        "dim_text":     "#4b5563",
        "gradient":     [(26, 10, 158), (44, 27, 184), (85, 69, 255)],
    },
}

# Virtual theme name resolved at apply-time (not a palette of its own).
_VIRTUAL_THEMES = ("auto",)


# ── Active Theme State ────────────────────────────────────────

_active_theme_name: str = "default"
_active_rich_theme: Theme | None = None


def _build_rich_theme(palette: dict) -> Theme:
    """Build a Rich Theme from a palette dict."""
    brand        = palette["brand"]
    brand_bright = palette["brand_bright"]
    accent       = palette["accent"]
    accent_bright= palette["accent_bright"]
    muted        = palette["muted"]
    dim_text     = palette["dim_text"]
    success      = _SHARED_STATUS["success"]
    error        = _SHARED_STATUS["error"]
    warning      = _SHARED_STATUS["warning"]
    info         = _SHARED_STATUS["info"]

    return Theme({
        "llx.brand":          Style(color=brand, bold=True),
        "llx.brand_bright":   Style(color=brand_bright),
        "llx.accent":         Style(color=accent),
        "llx.accent_bright":  Style(color=accent_bright),

        "llx.success":        Style(color=success, bold=True),
        "llx.error":          Style(color=error, bold=True),
        "llx.warning":        Style(color=warning),
        "llx.info":           Style(color=info),
        "llx.muted":          Style(color=muted),
        "llx.dim":            Style(color=dim_text),

        "llx.prompt":         Style(color=brand, bold=True),

        "llx.table.header":   Style(color=brand_bright, bold=True),
        "llx.table.border":   Style(color=muted),

        "llx.panel.border":   Style(color=brand),
        "llx.panel.title":    Style(color=brand_bright, bold=True),

        "llx.status.online":  Style(color=success, bold=True),
        "llx.status.offline": Style(color=error, bold=True),

        "llx.kv.key":         Style(color=brand_bright, bold=True),

        "llx.tree.folder":    Style(color=brand_bright, bold=True),
        "llx.tree.file":      Style(color="white"),
        "llx.tree.meta":      Style(color=dim_text),
    })


def detect_light_background() -> bool:
    """Best-effort: COLORFGBG bg>=8 usually means a light terminal profile."""
    cfg = os.environ.get("COLORFGBG", "")
    if ";" in cfg:
        try:
            bg = int(cfg.split(";")[-1])
            return bg >= 8
        except ValueError:
            pass
    return False


def resolve_theme_name(name: str | None = None) -> str:
    """Map a theme name (including 'auto') to a real palette key."""
    chosen = (name if name is not None else _active_theme_name) or "default"
    if chosen == "auto":
        return "day" if detect_light_background() else "default"
    return chosen if chosen in THEMES else "default"


def set_active_theme(name: str) -> bool:
    """Set the active theme by name. Returns True if theme exists."""
    global _active_theme_name, _active_rich_theme
    if name not in THEMES and name not in _VIRTUAL_THEMES:
        return False
    _active_theme_name = name
    _active_rich_theme = _build_rich_theme(THEMES[resolve_theme_name(name)])
    return True


def get_active_theme_name() -> str:
    return _active_theme_name


def get_theme_names() -> list[str]:
    return list(THEMES.keys()) + list(_VIRTUAL_THEMES)


def _get_active_rich_theme() -> Theme:
    global _active_rich_theme
    if _active_rich_theme is None:
        _active_rich_theme = _build_rich_theme(THEMES[resolve_theme_name()])
    return _active_rich_theme


def _get_active_palette() -> dict:
    return THEMES[resolve_theme_name()]


def prompt_colors() -> dict[str, str]:
    """Hex colors for the prompt_toolkit HTML prompt (theme-aware)."""
    pal = _get_active_palette()
    return {
        "brand": pal["brand"],
        "brand_bright": pal["brand_bright"],
        "accent": pal["accent"],
        "accent_bright": pal["accent_bright"],
        "muted": pal["muted"],
        "dim": pal["dim_text"],
        "success": _SHARED_STATUS["success"],
        "error": _SHARED_STATUS["error"],
        "warning": _SHARED_STATUS["warning"],
        "info": _SHARED_STATUS["info"],
        "agent": "#e17055",
    }


# ── Backward-compatible module-level color variables ──────────
# These are used by get_banner() and gradient_text().
# They reflect the DEFAULT theme. For dynamic access, use _get_active_palette().

BRAND        = "#1a0a9e"
BRAND_BRIGHT = "#3a2ae8"
ACCENT       = "#5545ff"
ACCENT_BRIGHT= "#7b6eff"
SUCCESS      = "#00b894"
ERROR        = "#ff6b6b"
WARNING      = "#fdcb6e"
INFO         = "#74b9ff"
MUTED        = "#636e72"
DIM_TEXT     = "#b2bec3"

# Keep LLX_THEME for any code that imports it directly (backward compat)
LLX_THEME = _build_rich_theme(THEMES["default"])


# ── Unicode Indicators ─────────────────────────────────────────

ICON_SUCCESS  = "✓"
ICON_ERROR    = "✗"
ICON_WARNING  = "▲"
ICON_INFO     = "●"
ICON_ONLINE   = "●"
ICON_OFFLINE  = "○"
ICON_BULLET   = "›"
ICON_SPINNER  = "dots"

# Local coding icons (new)
ICON_READ     = "◉"
ICON_EDIT     = "✎"
ICON_RUN      = "▶"
ICON_TODO     = "☑"
ICON_GIT      = "⎇"
ICON_DIFF     = "≠"


# ── Console Factory ────────────────────────────────────────────

def make_console(stderr: bool = False) -> Console:
    """Create a Console with the active LLX theme applied.

    Honors NO_COLOR (wins) and FORCE_COLOR / CLICOLOR_FORCE.
    """
    no_color = bool(os.environ.get("NO_COLOR"))
    force = bool(os.environ.get("FORCE_COLOR") or os.environ.get("CLICOLOR_FORCE"))
    kwargs = {"theme": _get_active_rich_theme(), "stderr": stderr}
    if no_color:
        kwargs["no_color"] = True
    elif force:
        kwargs["force_terminal"] = True
    return Console(**kwargs)


# ── Styled Table Factory ───────────────────────────────────────

def make_table(title: str | None = None, **kwargs) -> Table:
    """Create a Table with LLX styling defaults."""
    return Table(
        title=title,
        title_style="llx.brand_bright",
        show_header=True,
        header_style="llx.table.header",
        border_style="llx.table.border",
        box=box.ROUNDED,
        show_lines=False,
        pad_edge=True,
        **kwargs,
    )


# ── Styled Panel Factory ──────────────────────────────────────

def make_panel(content, title: str | None = None, **kwargs) -> Panel:
    """Create a Panel with LLX styling defaults."""
    return Panel(
        content,
        title=title,
        title_align="left",
        border_style="llx.panel.border",
        box=box.ROUNDED,
        padding=(1, 2),
        **kwargs,
    )


# ── Gradient Text ──────────────────────────────────────────────

def _lerp_color(c1: tuple, c2: tuple, t: float) -> tuple:
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


def gradient_text(text: str) -> Text:
    """Render text with a gradient using the active theme's colors."""
    stops = _get_active_palette()["gradient"]
    result = Text()
    printable = [i for i, ch in enumerate(text) if ch not in (" ", "\n")]
    total = max(len(printable) - 1, 1)
    pos = 0

    for i, char in enumerate(text):
        if char in (" ", "\n"):
            result.append(char)
            continue
        t = pos / total
        n = len(stops) - 1
        seg = min(int(t * n), n - 1)
        local_t = (t * n) - seg
        r, g, b = _lerp_color(stops[seg], stops[seg + 1], local_t)
        result.append(char, style=Style(color=f"#{r:02x}{g:02x}{b:02x}", bold=True))
        pos += 1

    return result


# ── Pixel Art Aardvark ─────────────────────────────────────────
# Bitmap: '#' = filled pixel (rendered as ██), ' ' = empty

_AARDVARK_BITMAP = [
    "    ##      ##",
    "    # #    # #",
    "     ## ## ##",
    "      ######",
    "      #######",
    "      ## ## #",
    "     #########",
    "     ##########",
    "     ###########",
    "    ########  ####",
    "    #########  # #",
    "    #########  ###",
    "   ###########",
    "   ###########",
    "  #############",
    "  # ####### ###",
    " #  ###########",
    "    ###########",
    "   ###########",
    "   ##########",
    " #### ##   ##",
    "####  ###  ###",
]


def _bitmap_to_blocks(bitmap: list[str]) -> list[str]:
    """Convert '#'/' ' bitmap rows to block character art."""
    lines = []
    for row in bitmap:
        line = ""
        for ch in row:
            line += "\u2588\u2588" if ch == "#" else "  "
        lines.append(line.rstrip())
    return lines


_AARDVARK_LINES = _bitmap_to_blocks(_AARDVARK_BITMAP)

# ── Small Aardvark (5 lines, same height as wordmark) ────────

_SMALL_AARDVARK_BITMAP = [
    " ##    ##",
    "  ######",
    " ##########",
    " ##########  ##",
    "####  ####  ##",
]

_SMALL_AARDVARK_LINES = _bitmap_to_blocks(_SMALL_AARDVARK_BITMAP)

# ── GUAARDVARK Wordmark ───────────────────────────────────────

_WORDMARK_LINES = [
    " \u2588\u2588\u2588  \u2588   \u2588  \u2588\u2588\u2588   \u2588\u2588\u2588  \u2588\u2588\u2588\u2588  \u2588\u2588\u2588\u2588  \u2588   \u2588  \u2588\u2588\u2588  \u2588\u2588\u2588\u2588  \u2588   \u2588",
    "\u2588     \u2588   \u2588 \u2588   \u2588 \u2588   \u2588 \u2588   \u2588 \u2588   \u2588 \u2588   \u2588 \u2588   \u2588 \u2588   \u2588 \u2588  \u2588",
    "\u2588  \u2588\u2588 \u2588   \u2588 \u2588\u2588\u2588\u2588\u2588 \u2588\u2588\u2588\u2588\u2588 \u2588\u2588\u2588\u2588  \u2588   \u2588 \u2588   \u2588 \u2588\u2588\u2588\u2588\u2588 \u2588\u2588\u2588\u2588  \u2588\u2588\u2588",
    "\u2588   \u2588 \u2588   \u2588 \u2588   \u2588 \u2588   \u2588 \u2588  \u2588  \u2588   \u2588  \u2588 \u2588  \u2588   \u2588 \u2588  \u2588  \u2588  \u2588",
    " \u2588\u2588\u2588   \u2588\u2588\u2588  \u2588   \u2588 \u2588   \u2588 \u2588   \u2588 \u2588\u2588\u2588\u2588    \u2588   \u2588   \u2588 \u2588   \u2588 \u2588   \u2588",
]


# ── Retro Scanline Stripes & Shadow ───────────────────────────

_STRIPE_EVEN     = (14, 14, 32)   # Lighter scanline
_STRIPE_ODD      = (6, 6, 16)     # Darker scanline
_SHADOW_MAX_DIST = 10
_SHADOW_BASE     = 8
_SHADOW_STEP     = 5
_SHADOW_ALT_BUMP = 4
_SHADOW_BLUE_MUL = 2.0
_ART_CELLS       = 37             # Bitmap cells wide (×2 = 74 chars)
_ART_CHARS       = 74             # Character width for wordmark / stripes


def _stripe_rgb(row_idx: int) -> tuple[int, int, int]:
    """Base stripe color for a global row index."""
    return _STRIPE_EVEN if row_idx % 2 == 0 else _STRIPE_ODD


def _shadow_shade(dist: int) -> tuple[int, int, int] | None:
    """RGB shadow glow for given horizontal distance. None if out of range."""
    if dist < 1 or dist > _SHADOW_MAX_DIST:
        return None
    v = _SHADOW_BASE + (dist - 1) * _SHADOW_STEP
    return (v, v, min(int(v * _SHADOW_BLUE_MUL), 255))


def _horizontal_distances(filled: list[bool]) -> list[int]:
    """Horizontal distance to nearest True cell. 0 for filled cells."""
    n = len(filled)
    d = [n] * n
    last = -n
    for i in range(n):
        if filled[i]:
            last = i
            d[i] = 0
        else:
            d[i] = i - last
    last = 2 * n
    for i in range(n - 1, -1, -1):
        if filled[i]:
            last = i
        else:
            d[i] = min(d[i], last - i)
    return d


def _bg_color(dist: int, row_idx: int) -> tuple[int, int, int]:
    """Background color: max of shadow glow and stripe base."""
    stripe = _stripe_rgb(row_idx)
    shade = _shadow_shade(dist)
    if shade:
        return (max(shade[0], stripe[0]), max(shade[1], stripe[1]), max(shade[2], stripe[2]))
    return stripe


def _hex(r: int, g: int, b: int) -> str:
    return f"#{r:02x}{g:02x}{b:02x}"


def _stripe_line(row_idx: int) -> Text:
    """Full-width pure stripe scanline."""
    r, g, b = _stripe_rgb(row_idx)
    result = Text()
    result.append("\u2588" * _ART_CHARS, style=Style(color=_hex(r, g, b)))
    return result


def get_aardvark(row_offset: int = 0) -> Text:
    """Return the aardvark on full-width retro stripe background."""
    stops = _get_active_palette()["gradient"]
    DBL = "\u2588\u2588"
    rows = [row.ljust(_ART_CELLS) for row in _AARDVARK_BITMAP]
    total_filled = max(sum(ch == '#' for r in rows for ch in r) - 1, 1)

    result = Text()
    filled_idx = 0

    for local_idx, row in enumerate(rows):
        grow = row_offset + local_idx
        filled_map = [ch == '#' for ch in row]
        dists = _horizontal_distances(filled_map)

        for col, ch in enumerate(row):
            if ch == '#':
                t = filled_idx / total_filled
                n = len(stops) - 1
                seg = min(int(t * n), n - 1)
                lt = (t * n) - seg
                r, g, b = _lerp_color(stops[seg], stops[seg + 1], lt)
                result.append(DBL, style=Style(color=_hex(r, g, b), bold=True))
                filled_idx += 1
            else:
                r, g, b = _bg_color(dists[col], grow)
                result.append(DBL, style=Style(color=_hex(r, g, b)))

        if local_idx < len(rows) - 1:
            result.append("\n")

    return result


def get_wordmark(row_offset: int = 0) -> Text:
    """Return the GUAARDVARK wordmark on full-width retro stripe background."""
    stops = _get_active_palette()["gradient"]
    BLK = "\u2588"
    rows = [line.ljust(_ART_CHARS) for line in _WORDMARK_LINES]
    total_filled = max(sum(ch == BLK for r in rows for ch in r) - 1, 1)

    result = Text()
    filled_idx = 0

    for local_idx, line in enumerate(rows):
        grow = row_offset + local_idx
        filled_map = [ch == BLK for ch in line]
        dists = _horizontal_distances(filled_map)

        for col, ch in enumerate(line):
            if ch == BLK:
                t = filled_idx / total_filled
                n = len(stops) - 1
                seg = min(int(t * n), n - 1)
                lt = (t * n) - seg
                r, g, b = _lerp_color(stops[seg], stops[seg + 1], lt)
                result.append(BLK, style=Style(color=_hex(r, g, b), bold=True))
                filled_idx += 1
            else:
                r, g, b = _bg_color(dists[col], grow)
                result.append(BLK, style=Style(color=_hex(r, g, b)))

        if local_idx < len(rows) - 1:
            result.append("\n")

    return result


def get_logo() -> Text:
    """Return the combined logo (wordmark with stripes)."""
    return get_wordmark()


def terminal_rows() -> int:
    try:
        return shutil.get_terminal_size(fallback=(80, 24)).lines
    except Exception:
        return 24


# The stroke needs about a second to read as a drawing. Longer and the
# prompt feels late. 28 steps over the 365-cell trace, then one power pulse.
_TRACE_S = 0.90
_PULSE_S = 0.14
_TRACE_STEPS = 28
_FULL_MIN_COLS = len(PLATE[0]) + 12
_COMPACT_MIN_COLS = len(COMPACT_PLATE[0]) + 10


def _mark_on_light() -> bool:
    return resolve_theme_name() == "day" or detect_light_background()


def _mark_colors() -> dict[str, str]:
    """Ink for the logo. The mark stays black and white; themes color the prompt."""
    if _mark_on_light():
        return {"ink": "#1c1c1c", "head": "#000000", "dim": "#737373", "border": "#d4d4d8"}
    return {"ink": "#f4f4f5", "head": "#ffffff", "dim": "#a1a1aa", "border": "#3f3f46"}


def _centered(text: str, width: int) -> str:
    if len(text) >= width:
        return text
    return " " * ((width - len(text)) // 2) + text


def _plate_text(
    rows: tuple[str, ...],
    lit: set[tuple[int, int]] | None,
    head: set[tuple[int, int]],
    colors: dict[str, str],
    body: str,
) -> Text:
    """Braille plate. lit=None draws every dot. head cells use the bright ink."""
    result = Text()
    for r, line in enumerate(rows):
        for c, ch in enumerate(line):
            on = ch != " " and (lit is None or (r, c) in lit)
            if not on:
                result.append(" ")
                continue
            tone = colors["head"] if (r, c) in head else colors[body]
            result.append(ch, style=Style(color=tone, bold=(r, c) in head))
        if r < len(rows) - 1:
            result.append("\n")
    return result


def _banner_group(
    plate: Text,
    width: int,
    version: str,
    status_line: str,
    model_line: str,
    colors: dict[str, str],
    show_wordmark: bool,
) -> Group:
    word = _centered(WORDMARK, width) if show_wordmark else " " * min(width, len(WORDMARK))
    wordmark = Text(word, style=Style(color=colors["ink"], bold=True))
    version_line = Text(
        _centered(f"v{version}", width),
        style=Style(color=_get_active_palette()["dim_text"]),
    )
    return Group(
        plate,
        Text(""),
        wordmark,
        version_line,
        Text(""),
        Text.from_markup(f"  {status_line}"),
        Text.from_markup(f"  {model_line}"),
    )


def _mark_panel(group: Group, colors: dict[str, str]) -> Align:
    panel = Panel(
        group,
        border_style=Style(color=colors["border"]),
        box=box.ROUNDED,
        padding=(1, 2),
        expand=False,
    )
    return Align.center(panel)


def _full_banner(version: str, status_line: str, model_line: str) -> RenderableType:
    colors = _mark_colors()
    width = len(PLATE[0])
    plate = _plate_text(PLATE, None, set(), colors, "ink")
    return _mark_panel(
        _banner_group(plate, width, version, status_line, model_line, colors, True),
        colors,
    )


def _text_banner(version: str, status_line: str, model_line: str) -> RenderableType:
    """Wordmark only. Used when even the compact plate would wrap."""
    colors = _mark_colors()
    group = Group(
        Text(WORDMARK, style=Style(color=colors["ink"], bold=True)),
        Text(f"v{version}", style=Style(color=_get_active_palette()["dim_text"])),
        Text(""),
        Text.from_markup(f"  {status_line}"),
        Text.from_markup(f"  {model_line}"),
    )
    return _mark_panel(group, colors)


def should_use_compact_banner(config_banner: str | None = None) -> bool:
    """True when the 24-row plate would overflow the pane."""
    mode = (config_banner or "auto").lower()
    if mode == "compact":
        return True
    if mode == "full":
        return False
    size = shutil.get_terminal_size(fallback=(80, 24))
    return size.lines < 40 or size.columns < _FULL_MIN_COLS


def get_compact_banner(version: str, status_line: str, model_line: str) -> RenderableType:
    """Smaller dot aardvark for short terminals and `banner: compact`."""
    colors = _mark_colors()
    width = len(COMPACT_PLATE[0])
    plate = _plate_text(COMPACT_PLATE, None, set(), colors, "ink")
    return _mark_panel(
        _banner_group(plate, width, version, status_line, model_line, colors, True),
        colors,
    )


def get_banner(
    version: str,
    status_line: str,
    model_line: str,
    compact: bool | None = None,
) -> RenderableType:
    """Finished launch mark. Compact on a short or narrow pane, text if narrower still."""
    if compact is None:
        banner_pref = "auto"
        try:
            from llx.config import load_config

            banner_pref = load_config().get("banner", "auto")
        except Exception:
            pass
        compact = should_use_compact_banner(banner_pref)
    if compact:
        cols = shutil.get_terminal_size(fallback=(80, 24)).columns
        if cols < _COMPACT_MIN_COLS:
            return _text_banner(version, status_line, model_line)
        return get_compact_banner(version, status_line, model_line)
    return _full_banner(version, status_line, model_line)


def _power_on_frames(
    version: str,
    status_line: str,
    model_line: str,
) -> list[tuple[RenderableType, float]]:
    """Stroke frames, then the finished plate, then one bright pulse on the power mark."""
    colors = _mark_colors()
    width = len(PLATE[0])
    total = len(TRACE)
    step_s = _TRACE_S / _TRACE_STEPS
    frames: list[tuple[RenderableType, float]] = []
    for step in range(1, _TRACE_STEPS):
        k = total * step // _TRACE_STEPS
        prev = total * (step - 1) // _TRACE_STEPS
        plate = _plate_text(PLATE, set(TRACE[:k]), set(TRACE[prev:k]), colors, "dim")
        group = _banner_group(plate, width, version, status_line, model_line, colors, False)
        frames.append((_mark_panel(group, colors), step_s))
    done = _plate_text(PLATE, None, set(), colors, "ink")
    frames.append((
        _mark_panel(
            _banner_group(done, width, version, status_line, model_line, colors, True),
            colors,
        ),
        step_s,
    ))
    pulse = _plate_text(PLATE, None, set(POWER), colors, "dim")
    frames.append((
        _mark_panel(
            _banner_group(pulse, width, version, status_line, model_line, colors, True),
            colors,
        ),
        _PULSE_S,
    ))
    return frames


def _can_animate(console: Console) -> bool:
    if not console.is_terminal or console.color_system is None:
        return False
    if termios is None or not sys.stdin.isatty():
        return False
    return True


def _key_hit(timeout: float) -> bool:
    """Wait up to timeout seconds. A key skips the rest of the splash and is consumed.

    The read is non-blocking. A pty can report readable and then block on read,
    which would freeze the splash on the first frame.
    """
    if termios is None or not sys.stdin.isatty():
        time.sleep(timeout)
        return False
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    was_blocking = os.get_blocking(fd)
    try:
        tty.setcbreak(fd)
        os.set_blocking(fd, False)
        ready, _, _ = select.select([fd], [], [], timeout)
        if not ready:
            return False
        try:
            data = os.read(fd, 16)
        except BlockingIOError:
            return False
        return bool(data)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        os.set_blocking(fd, was_blocking)


def play_banner(console: Console, version: str, status_line: str, model_line: str) -> None:
    """Print the launch mark. On a color terminal the stroke draws itself, then the power mark pulses."""
    banner_pref = "auto"
    try:
        from llx.config import load_config

        banner_pref = load_config().get("banner", "auto")
    except Exception:
        pass
    compact = should_use_compact_banner(banner_pref)
    final = get_banner(version, status_line, model_line, compact=compact)
    if compact or not _can_animate(console):
        console.print(final)
        return
    frames = _power_on_frames(version, status_line, model_line)
    try:
        with Live(
            frames[0][0],
            console=console,
            auto_refresh=False,
            transient=False,
            redirect_stdout=False,
            redirect_stderr=False,
            vertical_overflow="visible",
        ) as live:
            try:
                skipped = _key_hit(frames[0][1])
                if not skipped:
                    for panel, delay in frames[1:]:
                        live.update(panel, refresh=True)
                        if _key_hit(delay):
                            skipped = True
                            break
                live.update(final, refresh=True)
            except KeyboardInterrupt:
                live.update(final, refresh=True)
                raise
    except Exception:
        console.print(final)
        raise


# ── Initialize from config on import ──────────────────────────

def init_theme_from_config():
    """Load the saved theme from config. Called once at startup."""
    try:
        from llx.config import load_config
        config = load_config()
        name = config.get("theme", "default")
        if name in THEMES or name in _VIRTUAL_THEMES:
            set_active_theme(name)
    except Exception:
        pass  # Fall back to default

init_theme_from_config()
