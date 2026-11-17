#!/usr/bin/env python3
"""Rebuild the braille launch mark from the poster logo.

The source is docs/media/guaardvark-banner-dark.png. The wordmark and URL
sit in the bottom quarter of that file; only the character above them is
used. Needs Pillow and numpy (a maintainer tool, not a CLI dependency).

Writes cli/llx/banner_art.py for the REPL and scripts/lib/banner_art.sh for
the boot splash, from the same trace, so the two marks cannot drift.
The shell file is precolored: the splash has no Python.

    python3 scripts/build_cli_banner.py
    python3 scripts/build_cli_banner.py --preview /tmp/gv-banner
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs" / "media" / "guaardvark-banner-dark.png"
OUT = ROOT / "cli" / "llx" / "banner_art.py"
OUT_BASH = ROOT / "scripts" / "lib" / "banner_art.sh"

# Same split as cli/llx/theme.py (_TRACE_S, _PULSE_S, _TRACE_STEPS).
# 0.90s over the 365-cell trace, then one 0.14s pulse. Measured 2026-11-10.
TRACE_S = 0.90
PULSE_S = 0.14
TRACE_STEPS = 28

# Measured against the poster on 2026-11-10. 0.42 keeps the stroke and the
# ear traces without filling the belly. 44x24 braille cells is the smallest
# plate where the power mark and the nostrils still separate. The compact
# plate is the same crop at 28x14, threshold 0.45.
PLATE_COLS = 44
PLATE_ROWS = 24
PLATE_THRESH = 0.42
COMPACT_COLS = 28
COMPACT_ROWS = 14
COMPACT_THRESH = 0.45
# Skeleton steps light plate dots within this many dot-widths. Wider bridges
# the gap between the smile and the outline.
STROKE_RADIUS = 2.2
WORDMARK = "G U A A R D V A R K"

# Truecolor matches the REPL ink. 256-color is the nearest gray:
# 255=#eeeeee for #f4f4f5, 247=#9e9e9e for #a1a1aa,
# 234=#1c1c1c, 243=#767676 for #737373. Head is white or black.
_PALETTES = {
    "FT_D": {"ink": "244;244;245", "head": "255;255;255", "dim": "161;161;170", "mode": "2"},
    "FT_L": {"ink": "28;28;28", "head": "0;0;0", "dim": "115;115;115", "mode": "2"},
    "F8_D": {"ink": "255", "head": "231", "dim": "247", "mode": "5"},
    "F8_L": {"ink": "234", "head": "16", "dim": "243", "mode": "5"},
}

_CSI = re.compile(r"\x1b\[[0-9;]*m")

_BITS = {
    (0, 0): 0x01,
    (0, 1): 0x02,
    (0, 2): 0x04,
    (0, 3): 0x40,
    (1, 0): 0x08,
    (1, 1): 0x10,
    (1, 2): 0x20,
    (1, 3): 0x80,
}


def _character() -> np.ndarray:
    """Grayscale crop of the aardvark. Drops the poster wordmark and URL."""
    image = Image.open(SRC).convert("L")
    arr = np.array(image)
    cut = int(arr.shape[0] * 0.75)
    top = arr[:cut]
    ys, xs = np.where(top > 40)
    if len(ys) == 0:
        raise SystemExit(f"no ink in the top of {SRC}")
    return top[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]


def _mask(char: np.ndarray, cols: int, rows: int, thresh: float) -> np.ndarray:
    src = Image.fromarray(char).resize((cols * 2, rows * 4), Image.Resampling.LANCZOS)
    return np.array(src).astype(np.float32) / 255.0 > thresh


def _zhang_suen(binary: np.ndarray) -> np.ndarray:
    img = np.pad(binary.astype(np.uint8), 1, constant_values=0)
    changed = True
    while changed:
        changed = False
        for step in (0, 1):
            delete = []
            height, width = img.shape
            for i in range(1, height - 1):
                if not img[i].any():
                    continue
                for j in range(1, width - 1):
                    if img[i, j] != 1:
                        continue
                    n = (
                        img[i - 1, j],
                        img[i - 1, j + 1],
                        img[i, j + 1],
                        img[i + 1, j + 1],
                        img[i + 1, j],
                        img[i + 1, j - 1],
                        img[i, j - 1],
                        img[i - 1, j - 1],
                    )
                    neighbors = int(sum(n))
                    if neighbors < 2 or neighbors > 6:
                        continue
                    seq = n + (n[0],)
                    turns = sum(1 for k in range(8) if seq[k] == 0 and seq[k + 1] == 1)
                    if turns != 1:
                        continue
                    if step == 0:
                        if n[0] * n[2] * n[4] or n[2] * n[4] * n[6]:
                            continue
                    elif n[0] * n[2] * n[6] or n[0] * n[4] * n[6]:
                        continue
                    delete.append((i, j))
            if delete:
                changed = True
                for i, j in delete:
                    img[i, j] = 0
    return img[1:-1, 1:-1]


def _adjacent(point: tuple[int, int], nodes: set[tuple[int, int]]) -> list[tuple[int, int]]:
    x, y = point
    found = []
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            nxt = (x + dx, y + dy)
            if nxt in nodes:
                found.append(nxt)
    return found


def _walk(nodes: set[tuple[int, int]]) -> list[tuple[int, int]]:
    """One pen stroke. Starts at the bottom endpoint (the tail) and prefers
    going straight, then picks up the nearest unfinished branch."""
    if not nodes:
        return []
    degree = {p: len(_adjacent(p, nodes)) for p in nodes}
    ends = [p for p, d in degree.items() if d <= 1]
    start = max(ends or list(nodes), key=lambda p: (p[1], -p[0]))
    order: list[tuple[int, int]] = []
    visited: set[tuple[int, int]] = set()
    current = start
    prev: tuple[int, int] | None = None
    guard = 0
    limit = len(nodes) * 3
    while guard < limit and len(visited) < len(nodes):
        guard += 1
        if current in visited:
            rest = nodes - visited
            if not rest:
                break
            branches = [p for p in rest if any(n in visited for n in _adjacent(p, nodes))]
            anchor = order[-1]
            current = min(
                branches or list(rest),
                key=lambda p: (p[0] - anchor[0]) ** 2 + (p[1] - anchor[1]) ** 2,
            )
            prev = None
            continue
        order.append(current)
        visited.add(current)
        nbrs = [p for p in _adjacent(current, nodes) if p not in visited]
        if not nbrs:
            continue
        if prev is None or len(nbrs) == 1:
            nxt = nbrs[0]
        else:
            vx, vy = current[0] - prev[0], current[1] - prev[1]
            nxt = max(nbrs, key=lambda p: vx * (p[0] - current[0]) + vy * (p[1] - current[1]))
        prev, current = current, nxt
    return order


def _plate_rows(mask: np.ndarray, cols: int, rows: int) -> list[str]:
    lines = []
    for cy in range(rows):
        chars = []
        for cx in range(cols):
            bits = 0
            for dy in range(4):
                for dx in range(2):
                    if mask[cy * 4 + dy, cx * 2 + dx]:
                        bits |= _BITS[(dx, dy)]
            chars.append(chr(0x2800 + bits) if bits else " ")
        lines.append("".join(chars))
    return lines


def _trace(mask: np.ndarray, cols: int, rows: int) -> list[tuple[int, int]]:
    skel = _zhang_suen(mask.astype(np.uint8))
    ys, xs = np.where(skel > 0)
    order = _walk({(int(x), int(y)) for x, y in zip(xs, ys)})
    dots = [(int(x), int(y)) for y, x in zip(*np.where(mask))]
    claimed: set[tuple[int, int]] = set()
    seen: set[tuple[int, int]] = set()
    trace: list[tuple[int, int]] = []
    radius2 = STROKE_RADIUS ** 2

    def claim(px: int, py: int) -> None:
        cell = (py // 4, px // 2)
        if cell in seen:
            return
        if not (0 <= cell[0] < rows and 0 <= cell[1] < cols):
            return
        seen.add(cell)
        trace.append(cell)

    for sx, sy in order:
        for px, py in dots:
            if (px, py) in claimed:
                continue
            if (px - sx) ** 2 + (py - sy) ** 2 <= radius2:
                claimed.add((px, py))
                claim(px, py)
    for px, py in dots:
        if (px, py) not in claimed:
            claim(px, py)
    return trace


def _power_cells(mask: np.ndarray, cols: int, rows: int) -> list[tuple[int, int]]:
    """The belly power mark.

    On this crop it sits about 70-79% down and 61-70% across. One column
    further right is the front leg, which runs into the foot, so the box
    stops at 70% across.
    """
    lit = {
        (r, c)
        for r in range(rows)
        for c in range(cols)
        if mask[r * 4 : (r + 1) * 4, c * 2 : (c + 1) * 2].any()
    }
    r_lo, r_hi = int(round(rows * 0.70)), int(round(rows * 0.79))
    c_lo, c_hi = int(round(cols * 0.61)), int(round(cols * 0.70))
    return [
        (r, c)
        for r, c in sorted(lit)
        if r_lo <= r <= r_hi and c_lo <= c <= c_hi
    ]


def _emit(plate: list[str], trace: list[tuple[int, int]], power: list[tuple[int, int]], compact: list[str]) -> str:
    def rows(name: str, lines: list[str]) -> str:
        body = ",\n".join(f"    {line!r}" for line in lines)
        return f"{name}: tuple[str, ...] = (\n{body},\n)\n"

    def pairs(name: str, items: list[tuple[int, int]]) -> str:
        chunks = []
        for i in range(0, len(items), 6):
            chunk = ", ".join(f"({r}, {c})" for r, c in items[i : i + 6])
            chunks.append(f"    {chunk},")
        return f"{name}: tuple[tuple[int, int], ...] = (\n" + "\n".join(chunks) + "\n)\n"

    return f'''"""REPL launch mark. Regenerated by scripts/build_cli_banner.py.

Source: docs/media/guaardvark-banner-dark.png, character only (the poster
wordmark and URL are in the bottom quarter and are not part of the plate).
Full plate is {PLATE_COLS} by {PLATE_ROWS} braille cells, dot on at luminance
{PLATE_THRESH}. Compact plate is {COMPACT_COLS} by {COMPACT_ROWS} at {COMPACT_THRESH}.
TRACE is the skeleton stroke order; POWER is the belly power mark.
"""

from __future__ import annotations

WORDMARK = {WORDMARK!r}

{rows("PLATE", plate)}
{pairs("TRACE", trace)}
{pairs("POWER", power)}
{rows("COMPACT_PLATE", compact)}
'''


def _style(spec: dict[str, str], key: str) -> str:
    bright = "1;" if key == "head" else ""
    return f"\033[{bright}38;{spec['mode']};{spec[key]}m"


def _render_line(
    line: str,
    row: int,
    lit: set[tuple[int, int]] | None,
    head: set[tuple[int, int]],
    spec: dict[str, str],
    body: str,
) -> str:
    """One plate row. Spaces stay uncolored so the terminal background shows through."""
    parts: list[str] = []
    cols = len(line)
    i = 0
    while i < cols:
        on = line[i] != " " and (lit is None or (row, i) in lit)
        if not on:
            j = i + 1
            while j < cols and (line[j] == " " or (lit is not None and (row, j) not in lit)):
                j += 1
            parts.append(" " * (j - i))
            i = j
            continue
        style = "head" if (row, i) in head else body
        glyphs = [line[i]]
        j = i + 1
        while j < cols:
            on_j = line[j] != " " and (lit is None or (row, j) in lit)
            if not on_j or ("head" if (row, j) in head else body) != style:
                break
            glyphs.append(line[j])
            j += 1
        parts.append(_style(spec, style) + "".join(glyphs) + "\033[0m")
        i = j
    return "".join(parts)


def _stroke_frames(
    plate: list[str],
    trace: list[tuple[int, int]],
    power: list[tuple[int, int]],
) -> list[tuple[set[tuple[int, int]] | None, set[tuple[int, int]], str]]:
    """Same frames the REPL plays: 27 partials, the finished plate, the pulse."""
    total = len(trace)
    frames: list[tuple[set[tuple[int, int]] | None, set[tuple[int, int]], str]] = []
    for step in range(1, TRACE_STEPS):
        k = total * step // TRACE_STEPS
        prev = total * (step - 1) // TRACE_STEPS
        frames.append((set(trace[:k]), set(trace[prev:k]), "dim"))
    frames.append((None, set(), "ink"))
    frames.append((None, set(power), "dim"))
    return frames


def _paint(
    plate: list[str],
    frames: list[tuple[set[tuple[int, int]] | None, set[tuple[int, int]], str]],
    spec: dict[str, str],
) -> list[str]:
    painted = []
    for lit, head, body in frames:
        lines = [
            _render_line(line, r, lit, head, spec, body)
            for r, line in enumerate(plate)
        ]
        painted.append("\n".join(lines))
    return painted


def _visible(line: str) -> str:
    return _CSI.sub("", line)


def _check_frames(
    plate: list[str],
    frames: list[tuple[set[tuple[int, int]] | None, set[tuple[int, int]], str]],
    painted: list[str],
    ink: int,
) -> None:
    if len(painted) != len(frames):
        raise SystemExit(f"expected {len(frames)} frames, got {len(painted)}")
    rows = len(plate)
    cols = len(plate[0])
    for index, (blob, (lit, head, _body)) in enumerate(zip(painted, frames)):
        lines = blob.split("\n")
        if len(lines) != rows:
            raise SystemExit(f"frame {index} has {len(lines)} rows, expected {rows}")
        for line in lines:
            if len(_visible(line)) != cols:
                raise SystemExit(f"frame {index} row width is {len(_visible(line))}, expected {cols}")
        dots = sum(1 for ch in _visible(blob) if 0x2800 <= ord(ch) <= 0x28FF)
        expected = ink if lit is None else len(lit)
        if dots != expected:
            raise SystemExit(f"frame {index} has {dots} dots, expected {expected}")
        if head and "\033[1;" not in blob:
            raise SystemExit(f"frame {index} is missing the bright head")
        if not head and "\033[1;" in blob:
            raise SystemExit(f"frame {index} has a bright head with nothing to light")


def _bash_quote(text: str) -> str:
    body = (
        text.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\x1b", "\\033")
        .replace("\n", "\\n")
    )
    return "$'" + body + "'"


def _bash_array(name: str, items: list[str]) -> str:
    body = "\n".join(f"    {_bash_quote(item)}" for item in items)
    return f"{name}=(\n{body}\n)\n"


def _emit_bash(
    plate: list[str],
    compact: list[str],
    painted: dict[str, list[str]],
) -> str:
    step_s = f"{TRACE_S / TRACE_STEPS:.6f}"
    done = TRACE_STEPS - 1
    pulse = TRACE_STEPS
    inks = []
    for name, spec in _PALETTES.items():
        inks.append(f"_GV_INK_{name}={_bash_quote(_style(spec, 'ink'))}")
        inks.append(f"_GV_DIM_{name}={_bash_quote(_style(spec, 'dim'))}")
    arrays = "\n".join(_bash_array(f"_GV_{name}", frames) for name, frames in painted.items())
    compact_array = _bash_array("_GV_COMPACT", compact)
    return f"""# Boot splash mark. Generated by scripts/build_cli_banner.py. Do not edit.
# Same plate as cli/llx/banner_art.py. {TRACE_STEPS} steps over {TRACE_S:.2f}s
# (frame {done} is settled), then one {PULSE_S:.2f}s power pulse.

_GV_WORDMARK={WORDMARK!r}
_GV_PLATE_COLS={PLATE_COLS}
_GV_PLATE_ROWS={PLATE_ROWS}
_GV_COMPACT_COLS={COMPACT_COLS}
_GV_COMPACT_ROWS={COMPACT_ROWS}
_GV_STEP_S={step_s}
_GV_PULSE_S={PULSE_S}
_GV_DONE_INDEX={done}
_GV_PULSE_INDEX={pulse}

{chr(10).join(inks)}

{compact_array}
{arrays}"""


def _preview(path: Path, plate: list[str], trace: list[tuple[int, int]], power: list[tuple[int, int]]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    rows = len(plate)
    cols = len(plate[0])
    power_set = set(power)

    def draw(lit: set[tuple[int, int]], head: set[tuple[int, int]], name: str) -> None:
        cw, ch = 12, 24
        pad = 18
        canvas = Image.new("RGB", (cols * cw + pad * 2, rows * ch + pad * 2), (8, 8, 10))
        pen = ImageDraw.Draw(canvas)
        for r, line in enumerate(plate):
            for c, ch_ in enumerate(line):
                if ch_ == " " or (r, c) not in lit:
                    continue
                if (r, c) in power_set and name.startswith("power"):
                    color = (255, 80, 80)
                elif (r, c) in head:
                    color = (255, 255, 255)
                else:
                    color = (188, 188, 188)
                bits = ord(ch_) - 0x2800
                x0, y0 = pad + c * cw, pad + r * ch
                spots = ((0, 0, 0x01), (0, 1, 0x02), (0, 2, 0x04), (0, 3, 0x40),
                         (1, 0, 0x08), (1, 1, 0x10), (1, 2, 0x20), (1, 3, 0x80))
                xs = (0.32, 0.70)
                ys = (0.16, 0.38, 0.60, 0.82)
                for dx, dy, bit in spots:
                    if bits & bit:
                        cx = x0 + int(xs[dx] * cw)
                        cy = y0 + int(ys[dy] * ch)
                        pen.ellipse([cx - 2, cy - 2, cx + 2, cy + 2], fill=color)
        canvas.save(path / name)

    n = len(trace)
    for i, frac in enumerate((0.12, 0.28, 0.45, 0.62, 0.78, 1.0)):
        k = max(1, int(n * frac))
        draw(set(trace[:k]), set(trace[max(0, k - 10) : k]), f"frame-{i}.png")
    draw(set(trace), set(), "final.png")
    draw(set(trace), power_set, "power.png")


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild the CLI braille launch mark.")
    parser.add_argument("--preview", type=Path, help="also write frame PNGs to this directory")
    args = parser.parse_args()

    char = _character()
    mask = _mask(char, PLATE_COLS, PLATE_ROWS, PLATE_THRESH)
    plate = _plate_rows(mask, PLATE_COLS, PLATE_ROWS)
    trace = _trace(mask, PLATE_COLS, PLATE_ROWS)
    power = _power_cells(mask, PLATE_COLS, PLATE_ROWS)
    compact_mask = _mask(char, COMPACT_COLS, COMPACT_ROWS, COMPACT_THRESH)
    compact = _plate_rows(compact_mask, COMPACT_COLS, COMPACT_ROWS)

    ink = {(r, c) for r, line in enumerate(plate) for c, ch in enumerate(line) if ch != " "}
    missing = ink - set(trace)
    extra = set(trace) - ink
    if missing or extra:
        raise SystemExit(f"trace does not match the plate (missing {len(missing)}, extra {len(extra)})")
    if not 8 <= len(power) <= 14:
        raise SystemExit(f"power mark resolved to {len(power)} cells, expected 6-14: {power}")
    if any(plate[r][c] == " " for r, c in power):
        raise SystemExit("power cell is empty")
    if not any(ch != " " for line in compact for ch in line):
        raise SystemExit("compact plate is blank")
    if any(len(line) != COMPACT_COLS for line in compact):
        raise SystemExit("compact plate is not a rectangle")

    frames = _stroke_frames(plate, trace, power)
    painted = {name: _paint(plate, frames, spec) for name, spec in _PALETTES.items()}
    for name, blobs in painted.items():
        try:
            _check_frames(plate, frames, blobs, len(trace))
        except SystemExit as exc:
            raise SystemExit(f"{name}: {exc.code}") from exc

    OUT.write_text(_emit(plate, trace, power, compact), encoding="utf-8")
    OUT_BASH.write_text(_emit_bash(plate, compact, painted), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}  cells={len(trace)} power={len(power)}")
    print(f"wrote {OUT_BASH.relative_to(ROOT)}  frames={len(frames)} bytes={OUT_BASH.stat().st_size}")
    if args.preview:
        _preview(args.preview, plate, trace, power)
        print(f"preview {args.preview}")


if __name__ == "__main__":
    main()
