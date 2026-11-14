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
 */
const CardWorkspace = ({ boardKey, version = 1, cards }) => {
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
  const loaded = useRef(false);
  const stacking = useCardStacking();
  const menu = useContextMenu();

  const layoutRef = useRef(layout);
  layoutRef.current = layout;
  const minimizedRef = useRef(minimized);
  minimizedRef.current = minimized;
  const colorsRef = useRef(colors);
  colorsRef.current = colors;
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
    if (!reflow.isNarrow) return layout;
    const rows = barRows(ROW_HEIGHT_PX, CARD_MARGIN_PX * TILED.marginScale, TILED.barPx);
    return repackRows(
      layout.map((item) => (minimized[item.i] ? { ...item, h: rows } : item)),
      COLS_COUNT,
      reflow.cardsPerRow,
      40,
    );
  }, [reflow.isNarrow, reflow.cardsPerRow, layout, minimized, ROW_HEIGHT_PX, CARD_MARGIN_PX, COLS_COUNT]);

  const onUserLayoutChange = (reported, fromDrag) => {
    if (reflow.isNarrow) return;
    const settled = gestures.settle(reported, fromDrag);
    if (!settled) return;
    setLayout(settled);
    save({ layout: settled });
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
    stacking.reset();
    save({ layout: defaults, minimized: {}, colors: {} });
  };

  const byId = new Map(cards.map((card) => [card.id, card]));

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
              // Tiled cards leave no empty board to right-click, so the bar's menu resets the layout too.
              contextMenuArea="header"
              contextMenuActions={[{ label: t("layout.board.resetLayout"), onClick: resetLayout }]}
            >
              {card.body}
            </DashboardCardWrapper>
          );
        }}
      />
      <EntityContextMenu
        anchorPosition={menu.anchorPosition}
        onClose={menu.close}
        actions={menu.isOpen ? [{ label: t("layout.board.resetLayout"), onClick: resetLayout }] : []}
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
};

export default CardWorkspace;
