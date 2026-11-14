// Fitting a saved dashboard layout to the cards this version has.

/**
 * The layout to show for a saved one. A saved card keeps its place, its size
 * and its position in the list (later items draw over earlier ones, so the
 * order is how overlapping cards stack). A card the saved layout has never held
 * (one added since it was saved) goes in a new row along the top, in default
 * order, and the saved cards move down by that row, so nothing lands on top of
 * an arrangement the person made. Saved items for cards that no longer exist
 * are kept as they are.
 *
 * @param {Array} defaults  the default layout, one item per card
 * @param {Array} saved     layout items as last saved
 * @param {object} options
 * @param {number} [options.addedRowH]  height of the new top row; default: the added cards' own
 * @param {object} [options.extraItemProps]  props for a saved item with no default
 */
export function mergeSavedLayout(defaults, saved, { addedRowH, extraItemProps = {} } = {}) {
  const defaultById = new Map(defaults.map((item) => [item.i, item]));
  const savedIds = new Set(saved.map((item) => item.i));
  const added = defaults.filter((item) => !savedIds.has(item.i));
  const rowH = (item) => addedRowH ?? item.h ?? 0;
  const shift = added.reduce((max, item) => Math.max(max, rowH(item)), 0);

  const kept = saved.map((was) => {
    const item = defaultById.get(was.i);
    if (!item) return { ...extraItemProps, ...was, y: (was.y || 0) + shift };
    return {
      ...item,
      x: was.x ?? item.x,
      y: (was.y ?? item.y) + shift,
      w: was.w ?? item.w,
      h: was.h ?? item.h,
      static: was.static ?? item.static,
    };
  });

  let nextX = 0;
  const placed = added.map((item) => {
    const at = { ...item, x: nextX, y: 0, h: rowH(item) };
    nextX += item.w || 0;
    return at;
  });
  return [...kept, ...placed];
}
