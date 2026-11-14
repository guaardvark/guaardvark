// Fitting a saved dashboard layout to the cards this version has.

/**
 * The default layout with extra cards (ones an extension adds) placed after it:
 * in rows under the lowest card, left to right, wrapping at `cols`.
 *
 * @param {Array} layout  the core default layout
 * @param {Array<{id: string, w: number, h: number}>} cards  sizes in default-card units
 * @param {{cardGridW: number, cardGridH: number, COLS_COUNT: number}} grid
 */
export function appendCards(layout, cards, grid) {
  if (!cards.length) return layout;
  let y = layout.reduce((max, item) => Math.max(max, item.y + item.h), 0);
  let x = 0;
  let rowH = 0;
  const added = cards.map((card) => {
    const w = Math.min(Math.round(card.w * grid.cardGridW), grid.COLS_COUNT);
    const h = Math.round(card.h * grid.cardGridH);
    if (x > 0 && x + w > grid.COLS_COUNT) {
      y += rowH;
      x = 0;
      rowH = 0;
    }
    const item = { i: card.id, x, y, w, h };
    x += w;
    rowH = Math.max(rowH, h);
    return item;
  });
  return [...layout, ...added];
}

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
