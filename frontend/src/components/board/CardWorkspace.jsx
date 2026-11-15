// A page made of cards: each part of the page is a card on the shared board,
// in the shared card frame. The page hands over its parts and where each goes
// by default; the workspace keeps the arrangement (places, sizes, minimized
// cards, colours) in the backend's page-state store under `boardKey`.

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import CardBoard, { TILED } from "./CardBoard";
import { barRows, narrowReflow, repackRows } from "./boardGeometry";
import { loadBoardState, saveBoardState } from "./boardState";
import { useBoardGestures, useCardStacking, useMeasuredWidth } from "./useBoard";
import DashboardCardWrapper from "../dashboard/DashboardCardWrapper";
import EntityContextMenu from "../common/EntityContextMenu";
import useContextMenu from "../../hooks/useContextMenu";
import { useLayout, useDashboardWidth } from "../../contexts/LayoutContext";
import { useUiTranslation } from "../../i18n";

// A workspace card holds forms and galleries. Once the narrowest card of the
// arrangement would be under NARROW_CARD_PX the cards are stacked instead, in
// columns at least STACKED_CARD_PX wide.
const NARROW_CARD_PX = 360;
const STACKED_CARD_PX = 420;

/**
 * The arrangement to show for a saved one: saved cards keep their place and
 * size, cards the page no longer has are dropped, and cards the save predates
 * take their default place. A save from another `version` is not used.
 */
export function workspaceLayout(defaults, saved, version) {
  if (!saved || saved.version !== version || !Array.isArray(saved.layout) || !saved.layout.length) return defaults;
  const defaultById = new Map(defaults.map((item) => [item.i, item]));
  const kept = saved.layout
    .filter((item) => defaultById.has(item.i))
    .map((item) => ({ ...defaultById.get(item.i), x: item.x, y: item.y, w: item.w, h: item.h }));
  const keptIds = new Set(kept.map((item) => item.i));
  return [...kept, ...defaults.filter((item) => !keptIds.has(item.i))];
}

/**
 * @param {string} boardKey  names the saved arrangement (/api/state/board/<boardKey>): a-z, 0-9 and hyphens
 * @param {number} version   bump when the default layout changes shape, so everyone gets it once
 * @param {Array<{id: string, title: string, body: node, layout: {x, y, w, h, minW?, minH?}}>} cards
 * @param {Array<{id: string, label: string, layout: Object<string, {x, y, w, h}>}>} [presets]
 *   ready-made arrangements offered in the board's menus; `layout` maps a card id to its place
 */
const CardWorkspace = ({ boardKey, version = 1, cards, presets }) => {
  const { t } = useUiTranslation();
  const { gridSettings } = useLayout();
  const { COLS_COUNT, ROW_HEIGHT_PX, CARD_MARGIN_PX } = gridSettings;
  const [containerRef, width] = useMeasuredWidth(useDashboardWidth());

  // Card ids and default places only change when the page changes its cards.
  const defaultsKey = JSON.stringify(cards.map((card) => [card.id, card.layout]));
  const defaults = useMemo(
    () => cards.map((card) => ({ minW: 20, minH: 10, ...card.layout, i: card.id })),
    [defaultsKey],
  );

  const [layout, setLayout] = useState(defaults);
  const [minimized, setMinimized] = useState({});
  const [colors, setColors] = useState({});
  const [hidden, setHidden] = useState({});
  const loaded = useRef(false);
  const stacking = useCardStacking();
  const menu = useContextMenu();

  const layoutRef = useRef(layout);
  layoutRef.current = layout;
  const minimizedRef = useRef(minimized);
  minimizedRef.current = minimized;
  const colorsRef = useRef(colors);
  colorsRef.current = colors;
  const hiddenRef = useRef(hidden);
  hiddenRef.current = hidden;
  const gestures = useBoardGestures(
    useCallback(() => layoutRef.current, []),
    useCallback(() => minimizedRef.current, []),
  );

  useEffect(() => {
    let cancelled = false;
    loadBoardState(`board/${boardKey}`)
      .then((saved) => {
        if (cancelled) return;
        setLayout(workspaceLayout(defaults, saved, version));
        if (saved?.version === version) {
          if (saved.cardColors) setColors(saved.cardColors);
          if (saved.minimizedCards) setMinimized(saved.minimizedCards);
          if (saved.hiddenCards) setHidden(saved.hiddenCards);
        }
      })
      // An arrangement that cannot be read is not worth stopping the page for.
      .catch(() => {})
      .finally(() => {
        if (!cancelled) loaded.current = true;
      });
    return () => {
      cancelled = true;
    };
  }, [boardKey, version, defaults]);

  const save = useCallback(
    (next = {}) => {
      // Nothing is written until the saved arrangement has been read.
      if (!loaded.current) return;
      saveBoardState(`board/${boardKey}`, {
        version,
        layout: next.layout ?? layoutRef.current,
        cardColors: next.colors ?? colorsRef.current,
        minimizedCards: next.minimized ?? minimizedRef.current,
        hiddenCards: next.hidden ?? hiddenRef.current,
        lastSaved: new Date().toISOString(),
      }).catch((err) => console.warn(`${boardKey}: arrangement not saved:`, err));
    },
    [boardKey, version],
  );

  // On a narrow page the cards are shown stacked at a readable width instead of
  // shrinking; that view is derived and nothing done in it is kept.
  const narrowest = Math.min(...defaults.map((item) => item.w));
  const reflow = {
    isNarrow: narrowReflow(gridSettings, width, narrowest, NARROW_CARD_PX).isNarrow,
    cardsPerRow: narrowReflow(gridSettings, width, narrowest, STACKED_CARD_PX).cardsPerRow,
  };
  const shown = useMemo(() => {
    const visible = layout.filter((item) => !hidden[item.i]);
    if (!reflow.isNarrow) return visible;
    const rows = barRows(ROW_HEIGHT_PX, CARD_MARGIN_PX * TILED.marginScale, TILED.barPx);
    return repackRows(
      visible.map((item) => (minimized[item.i] ? { ...item, h: rows } : item)),
      COLS_COUNT,
      reflow.cardsPerRow,
      40,
    );
  }, [reflow.isNarrow, reflow.cardsPerRow, layout, hidden, minimized, ROW_HEIGHT_PX, CARD_MARGIN_PX, COLS_COUNT]);

  const onUserLayoutChange = (reported, fromDrag) => {
    if (reflow.isNarrow) return;
    const settled = gestures.settle(reported, fromDrag);
    if (!settled) return;
    // The board only reports the cards it shows; hidden ones keep their place.
    const reportedIds = new Set(settled.map((item) => item.i));
    const next = [...settled, ...layoutRef.current.filter((item) => !reportedIds.has(item.i))];
    setLayout(next);
    save({ layout: next });
  };

  const toggleMinimize = (cardId) => {
    gestures.beginToggle(cardId);
    const next = { ...minimizedRef.current, [cardId]: !minimizedRef.current[cardId] };
    setMinimized(next);
    save({ minimized: next });
  };

  const changeColor = (cardId, color) => {
    const next = { ...colorsRef.current, [cardId]: color };
    if (!color) delete next[cardId];
    setColors(next);
    save({ colors: next });
  };

  const resetLayout = () => {
    setLayout(defaults);
    setMinimized({});
    setColors({});
    setHidden({});
    stacking.reset();
    save({ layout: defaults, minimized: {}, colors: {}, hidden: {} });
  };

  // A ready-made arrangement: every card shown and open, each in the preset's place.
  const applyPreset = (preset) => {
    const next = defaults.map((item) => ({ ...item, ...(preset.layout[item.i] || {}) }));
    setLayout(next);
    setMinimized({});
    setHidden({});
    stacking.reset();
    save({ layout: next, minimized: {}, hidden: {} });
  };

  const hideCard = (cardId) => {
    const next = { ...hiddenRef.current, [cardId]: true };
    setHidden(next);
    save({ hidden: next });
  };

  const showCards = (cardIds) => {
    const next = { ...hiddenRef.current };
    cardIds.forEach((id) => delete next[id]);
    setHidden(next);
    save({ hidden: next });
  };

  const byId = new Map(cards.map((card) => [card.id, card]));
  const hiddenIds = cards.map((card) => card.id).filter((id) => hidden[id]);

  // The same items on a card's bar and on the empty board: tiled cards can leave no board to right-click.
  const boardActions = () => [
    ...(presets || []).map((preset) => ({
      label: t("layout.board.useLayout", { name: preset.label }),
      onClick: () => applyPreset(preset),
    })),
    { label: t("layout.board.resetLayout"), onClick: resetLayout },
    ...hiddenIds.map((id, idx) => ({
      label: t("dashboard.menu.showCard", { card: byId.get(id).title }),
      onClick: () => showCards([id]),
      dividerBefore: idx === 0,
    })),
    hiddenIds.length > 1 && { label: t("dashboard.menu.showAllHidden"), onClick: () => showCards(hiddenIds) },
  ].filter(Boolean);

  return (
    <div
      onContextMenu={(e) => {
        // The empty board only; a card has menus of its own.
        if (!e.target.closest?.("[data-card-id]")) menu.open(e);
      }}
    >
      <CardBoard
        containerRef={containerRef}
        width={width}
        grid={gridSettings}
        spacing={TILED}
        layout={shown}
        minimized={minimized}
        stacking={stacking}
        locked={reflow.isNarrow}
        dragHandle=".card-header-buttons"
        onDragStart={gestures.onDragStart}
        onDragStop={(reported) => onUserLayoutChange(reported, true)}
        onResizeStop={(reported) => onUserLayoutChange(reported, false)}
        renderCard={(item, isMinimized) => {
          const card = byId.get(item.i);
          return (
            <DashboardCardWrapper
              id={`${boardKey}-${item.i}`}
              title={card.title}
              cardColor={colors[item.i]}
              onCardColorChange={(color) => changeColor(item.i, color)}
              isMinimized={isMinimized}
              onToggleMinimize={() => toggleMinimize(item.i)}
              keepMounted
              onHideCard={() => hideCard(item.i)}
              contextMenuArea="header"
              contextMenuActions={boardActions()}
            >
              {card.body}
            </DashboardCardWrapper>
          );
        }}
      />
      <EntityContextMenu
        anchorPosition={menu.anchorPosition}
        onClose={menu.close}
        actions={menu.isOpen ? boardActions() : []}
      />
    </div>
  );
};

CardWorkspace.propTypes = {
  boardKey: PropTypes.string.isRequired,
  version: PropTypes.number,
  cards: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string.isRequired,
      title: PropTypes.string.isRequired,
      body: PropTypes.node,
      layout: PropTypes.object.isRequired,
    }),
  ).isRequired,
  presets: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string.isRequired,
      label: PropTypes.string.isRequired,
      layout: PropTypes.object.isRequired,
    }),
  ),
};

export default CardWorkspace;
