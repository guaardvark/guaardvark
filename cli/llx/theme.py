"""LLX CLI visual theme — colors, styles, console factory, and logo."""

from rich.console import Console, Group
from rich.style import Style
from rich.theme import Theme
from rich.text import Text
from rich.table import Table
from rich.panel import Panel
from rich import box

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
        "brand":        "#1a3a8a",
        "brand_bright": "#4a7ee0",
        "accent":       "#00cec9",
        "accent_bright":"#81ecec",
        "muted":        "#636e72",
        "dim_text":     "#b2bec3",
        "gradient":     [(10, 30, 120), (25, 70, 180), (55, 130, 220)],
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
}


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


def set_active_theme(name: str) -> bool:
    """Set the active theme by name. Returns True if theme exists."""
    global _active_theme_name, _active_rich_theme
    if name not in THEMES:
        return False
    _active_theme_name = name
    _active_rich_theme = _build_rich_theme(THEMES[name])
    return True


def get_active_theme_name() -> str:
    return _active_theme_name


def get_theme_names() -> list[str]:
    return list(THEMES.keys())


def _get_active_rich_theme() -> Theme:
    global _active_rich_theme
    if _active_rich_theme is None:
        _active_rich_theme = _build_rich_theme(THEMES[_active_theme_name])
    return _active_rich_theme


def _get_active_palette() -> dict:
    return THEMES[_active_theme_name]


# ── Backward-compatible module-level color variables ──────────
# These are used by get_banner() and gradient_text().
# They reflect the DEFAULT theme. For dynamic access, use _get_active_palette().

BRAND        = "#1a3a8a"
BRAND_BRIGHT = "#4a7ee0"
ACCENT       = "#00cec9"
ACCENT_BRIGHT= "#81ecec"
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


# ── Console Factory ────────────────────────────────────────────

def make_console(stderr: bool = False) -> Console:
    """Create a Console with the active LLX theme applied."""
    return Console(theme=_get_active_rich_theme(), stderr=stderr)


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
    "      ##          ##",
    "      # #        # #",
    "       ## ##  ## ##",
    "        ## #### ##",
    "         ########",
    "        ##########",
    "        ### ## ###",
    "        ##########",
    "       ############",
    "       #############",
    "      ##############",
    "      ###############",
    "     ################",
    "     #############  ####",
    "     ##############  # #",
    "     ##############  ###",
    "    ##################",
    "    ##################",
    "   ####################",
    "   ## ############## ##",
    "  ##  ##################",
    "      ##################",
    "     ##################",
    "     #################",
    "     ################",
    "  #####  ####    ####",
    " ######  #####   #####",
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


def get_aardvark() -> Text:
    """Return the aardvark mascot as gradient-colored Rich Text."""
    return gradient_text("\n".join(_AARDVARK_LINES))


def get_small_aardvark() -> Text:
    """Return a compact 5-line aardvark mascot as gradient-colored Rich Text."""
    return gradient_text("\n".join(_SMALL_AARDVARK_LINES))


def get_wordmark() -> Text:
    """Return the GUAARDVARK wordmark as gradient-colored Rich Text."""
    return gradient_text("\n".join(_WORDMARK_LINES))


def get_logo() -> Text:
    """Return the combined logo as gradient-colored Rich Text."""
    return gradient_text("\n".join(_WORDMARK_LINES))


def _gradient_line(text: str, stops: list) -> Text:
    """Apply gradient coloring to a single line of block characters."""
    result = Text()
    printable = [i for i, ch in enumerate(text) if ch.strip()]
    total = max(len(printable) - 1, 1)
    pos = 0
    for ch in text:
        if not ch.strip():
            result.append(ch)
        else:
            t = pos / total
            n = len(stops) - 1
            seg = min(int(t * n), n - 1)
            local_t = (t * n) - seg
            r, g, b = _lerp_color(stops[seg], stops[seg + 1], local_t)
            result.append(ch, style=Style(color=f"#{r:02x}{g:02x}{b:02x}", bold=True))
            pos += 1
    return result


def get_banner(version: str, status_line: str, model_line: str) -> Panel:
    """Build the REPL banner: about info + small aardvark above the wordmark."""
    from datetime import datetime
    palette = _get_active_palette()
    now = datetime.now()
    stops = palette["gradient"]

    # Layout widths
    wordmark_width = max(len(line) for line in _WORDMARK_LINES)
    aardvark_lines = _SMALL_AARDVARK_LINES

    # About text lines (left side, above wordmark letters)
    about_lines = [
        "www.guaardvark.com",
        now.strftime("%Y-%m-%d  %H:%M:%S"),
        f"v{version}",
        "",
        "",
    ]

    # Compose top section: about text (left) + aardvark (right-aligned to wordmark width)
    dim_style = Style(color=palette["dim_text"])
    top = Text()
    for i in range(5):
        about = about_lines[i]
        ark = aardvark_lines[i] if i < len(aardvark_lines) else ""
        padding = max(wordmark_width - len(about) - len(ark), 2)
        top.append(about, style=dim_style)
        top.append(" " * padding)
        top.append_text(_gradient_line(ark, stops))
        if i < 4:
            top.append("\n")

    # Wordmark + status below
    wordmark = get_wordmark()
    spacer = Text("")
    status = Text.from_markup(f"  {status_line}")
    model = Text.from_markup(f"  {model_line}")

    group = Group(top, wordmark, spacer, status, model)

    return Panel(
        group,
        border_style=Style(color=palette["brand"]),
        box=box.ROUNDED,
        padding=(1, 2),
    )


# ── Initialize from config on import ──────────────────────────

def init_theme_from_config():
    """Load the saved theme from config. Called once at startup."""
    try:
        from llx.config import load_config
        config = load_config()
        name = config.get("theme", "default")
        if name in THEMES:
            set_active_theme(name)
    except Exception:
        pass  # Fall back to default

init_theme_from_config()
