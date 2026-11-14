// Reading the Code Editor's saved card arrangement.

import { restoreMinimizedSizes } from "../board";

const num = (value, fallback) => (typeof value === "number" ? value : fallback);

/**
 * The layout to show for a saved one. Each default card takes its saved place,
 * size and limits; a saved card with no default is kept. A card saved minimized
 * comes back at the size it opens to (older saves held it at its bar height,
 * pinned there, with the real size in `originalDimensions`).
 *
 * @param {Array} defaults  the default layout, one item per card
 * @param {Array} saved     layout items as last saved
 * @param {Record<string, boolean>} minimized
 * @param {object} originalDimensions  from an older save; may be absent
 * @param {{cardMinGridW: number, cardMinGridH: number}} mins
 */
export function savedEditorLayout(defaults, saved, minimized, originalDimensions, mins) {
  const savedById = new Map(saved.map((item) => [item.i, item]));
  const defaultById = new Map(defaults.map((item) => [item.i, item]));

  const merged = defaults.map((item) => {
    const was = savedById.get(item.i);
    if (!was) return item;
    const h = num(was.h, item.h);
    const minH = num(was.minH, item.minH);
    // A height limit below the card's own height is left over from a minimize.
    const maxH = was.maxH !== undefined && (was.maxH < h || was.maxH < minH) ? undefined : was.maxH;
    return {
      ...item,
      x: num(was.x, item.x),
      y: num(was.y, item.y),
      w: num(was.w, item.w),
      h,
      minW: num(was.minW, item.minW),
      minH,
      maxW: was.maxW !== undefined ? was.maxW : item.maxW,
      maxH,
    };
  });

  saved
    .filter((item) => !defaultById.has(item.i))
    .forEach((item) =>
      merged.push({
        i: item.i,
        x: num(item.x, 0),
        y: num(item.y, 0),
        w: num(item.w, mins.cardMinGridW),
        h: num(item.h, mins.cardMinGridH),
        minW: num(item.minW, mins.cardMinGridW),
        minH: num(item.minH, mins.cardMinGridH),
        maxW: item.maxW,
        maxH: item.maxH,
      }),
    );

  return restoreMinimizedSizes(merged, minimized, originalDimensions, (item) => defaultById.get(item.i)).map(
    (item) => ({ ...item, minH: item.minH ?? mins.cardMinGridH, isDraggable: true, isResizable: true }),
  );
}

/** A layout kept inside the board: no card past the right edge or below `maxRows`. */
export function boundToBoard(layout, cols, maxRows) {
  return layout.map((item) => {
    const w = Math.min(item.w, cols);
    const x = Math.min(Math.max(item.x, 0), Math.max(0, cols - w));
    let h = item.h;
    let y = Math.max(item.y, 0);
    if (maxRows) {
      h = Math.min(h, maxRows);
      y = Math.min(y, Math.max(0, maxRows - h));
    }
    return { ...item, x, y, w, h };
  });
}
