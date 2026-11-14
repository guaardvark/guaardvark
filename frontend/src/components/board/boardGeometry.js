// Layout arithmetic shared by every card board (Dashboard, Notes). A board is a
// react-grid-layout grid of many narrow columns on which cards are placed
// freely; `grid` below is LayoutContext's gridSettings.

/** A card's title bar, in px: what a minimized card shows. */
export const CARD_BAR_PX = 40;

/** Width of the bars the stacked presets place down the right edge, in px. */
export const BAR_PRESET_WIDTH_PX = 300;

/** A compact-preset card is this fraction of a default card. */
export const COMPACT_SCALE = 0.71;

/**
 * Grid rows a title bar needs. The drag placeholder is the grid cell, so a
 * minimized card has to occupy only its bar. Matches react-grid-layout's pixel
 * height: rows * rowHeight + (rows - 1) * margin.
 */
export function barRows(rowHeight, margin, barPx = CARD_BAR_PX) {
  let rows = 1;
  while (rows < 12) {
    const px = Math.round(rowHeight * rows + Math.max(0, rows - 1) * margin);
    if (px >= barPx) return rows;
    rows += 1;
  }
  return rows;
}

/**
 * Compact preset: smaller cards in reading order, as many per row as fit.
 * @param {string[]} ids  cards in the order to place them
 * @param {object} grid   gridSettings
 * @param {number} width  board width in px
 * @param {object} [itemProps]  extra props for every item (e.g. isResizable)
 */
export function compactPreset(ids, grid, width, itemProps = {}) {
  const w = Math.round(grid.cardGridW * COMPACT_SCALE);
  const h = Math.round(grid.cardGridH * COMPACT_SCALE);
  const perRow = Math.max(1, Math.floor(width / (w * (width / grid.COLS_COUNT))));
  return ids.map((id, idx) => ({
    i: id,
    x: (idx % perRow) * w,
    y: Math.floor(idx / perRow) * h,
    w,
    h,
    minW: grid.cardMinGridW,
    isDraggable: true,
    isResizable: true,
    ...itemProps,
  }));
}

/**
 * Bars preset: one bar per card stacked down the right edge of the board.
 * @param {number} barPx  the height each bar's slot is given, in px
 */
export function barsPreset(ids, grid, width, barPx, itemProps = {}) {
  const colPx = width / grid.COLS_COUNT;
  const w = Math.round(BAR_PRESET_WIDTH_PX / colPx);
  const h = Math.round(barPx / grid.ROW_HEIGHT_PX);
  const x = Math.max(0, grid.COLS_COUNT - w);
  return ids.map((id, idx) => ({
    i: id,
    x,
    y: idx * h,
    w,
    h,
    minW: grid.cardMinGridW,
    isDraggable: true,
    isResizable: true,
    ...itemProps,
  }));
}

/**
 * The layout handed to the grid for a stored one: a minimized card is given
 * the height of its bar (its stored height is what it opens to again), and no
 * item's minimum exceeds its size, which the grid warns about.
 */
export function gridLayout(layout, minimized, rows) {
  return layout.map((item) => {
    if (minimized?.[item.i]) {
      return { ...item, h: rows, minH: rows, maxH: rows, minW: Math.min(item.minW ?? 1, item.w) };
    }
    return { ...item, minW: Math.min(item.minW ?? 1, item.w), minH: Math.min(item.minH ?? 1, item.h) };
  });
}

/**
 * The stored layout after the grid reports a drag or resize. Each reported item
 * keeps its stored props and takes the new place and width; it takes the new
 * height too unless it was minimized, because the grid only ever saw its bar.
 * @param {Array} reported  the grid's layout
 * @param {Array} stored    the layout before the gesture
 * @param {Record<string, boolean>} minimized  flags as the gesture began
 */
export function mergeReported(reported, stored, minimized) {
  const before = new Map((stored || []).map((item) => [item.i, item]));
  return reported
    .filter((item) => item && item.i)
    .map((item) => {
      const was = before.get(item.i);
      if (!was) return item;
      const next = { ...was, x: item.x, y: item.y, w: item.w };
      if (!minimized?.[item.i]) next.h = item.h;
      return next;
    });
}

/**
 * How a board of card-sized cells behaves at the current width. The grid has a
 * fixed column count and a responsive pixel width, so on a narrow board every
 * card would shrink in proportion. Once a card `targetCols` wide would render
 * under `minCardPx`, the board is narrow and is shown re-packed instead (see
 * repackRows) at `cardsPerRow` cards a row, each at least `minCardPx` wide.
 */
export function narrowReflow(grid, width, targetCols, minCardPx = 300) {
  const { COLS_COUNT, CARD_MARGIN_PX, CONTAINER_PADDING_PX } = grid;
  // react-grid-layout's own geometry. colWidth can be negative with this many
  // columns; it nets out positive once the margins inside a card are added.
  const colPx = COLS_COUNT > 0 ? (width - CARD_MARGIN_PX * (COLS_COUNT - 1) - CONTAINER_PADDING_PX * 2) / COLS_COUNT : 0;
  const cardPx = (span) => colPx * span + Math.max(0, span - 1) * CARD_MARGIN_PX;
  let cardsPerRow = 1;
  for (let n = 2; n <= 8; n += 1) {
    if (cardPx(Math.floor(COLS_COUNT / n)) >= minCardPx) cardsPerRow = n;
    else break;
  }
  return { isNarrow: width > 0 && cardPx(targetCols) < minCardPx, cardsPerRow };
}

/**
 * A layout re-packed into equal columns in reading order, each row as tall as
 * its tallest card. Items are locked: this view is derived from the stored
 * layout and is not something to drag.
 */
export function repackRows(layout, cols, cardsPerRow, fallbackH) {
  const ordered = [...layout].sort((a, b) => a.y - b.y || a.x - b.x);
  const span = Math.floor(cols / cardsPerRow);
  const out = [];
  let y = 0;
  for (let i = 0; i < ordered.length; i += cardsPerRow) {
    const row = ordered.slice(i, i + cardsPerRow);
    const rowH = row.reduce((max, item) => Math.max(max, item.h || fallbackH), 0);
    row.forEach((item, c) =>
      out.push({ ...item, x: c * span, y, w: span, h: item.h, minW: 1, isDraggable: false, isResizable: false }),
    );
    y += rowH;
  }
  return out;
}

/** The mode after `mode` in `modes`, wrapping round. */
export function nextMode(modes, mode) {
  return modes[(modes.indexOf(mode) + 1) % modes.length];
}

/**
 * Layouts saved before minimizing left a card's size alone hold a minimized
 * card at its shrunk height (often pinned there with minH/maxH), with the size
 * it opens to kept beside the layout in `originalDimensions`. Put those sizes
 * back on the items and lift the pins.
 * @param {(item: object) => object} [fallback]  size for a minimized card with no kept size
 */
export function restoreMinimizedSizes(layout, minimized, originalDimensions, fallback) {
  const kept = originalDimensions && typeof originalDimensions === "object" ? originalDimensions : {};
  return layout.map((item) => {
    if (!minimized?.[item.i]) return item;
    const size = kept[item.i] || fallback?.(item);
    if (!size) return item;
    const { minH: _minH, maxH: _maxH, ...rest } = item;
    const pinned = item.maxH !== undefined && item.maxH === item.minH;
    return { ...(pinned ? rest : item), w: size.w ?? item.w, h: size.h ?? item.h };
  });
}
