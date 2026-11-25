// The board every card page is drawn on: a free-placement grid where cards are
// dragged by their bar, resized from any edge, overlap, stack by last touch and
// minimize to their bar. The page owns which cards exist, what they show and
// where the layout is saved; the board owns how they behave.

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, useTheme } from "@mui/material";
import ReactGridLayout from "react-grid-layout/legacy";
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";
import { CARD_BAR_PX, barRows, gridLayout } from "./boardGeometry";

const RESIZE_HANDLES = ["s", "w", "e", "n", "sw", "nw", "se", "ne"];

// On the board while a card is being dragged or resized.
const GESTURE_CLASS = "card-board-gesture";

/** Never starts a drag, whatever the card puts in its bar. */
export const DRAG_CANCEL = "button, input, textarea, select, option, .non-draggable";

/**
 * Spacing for a workspace whose cards tile edge to edge (Code Editor, Video
 * Editor) instead of floating apart: near-zero gaps, and a bar slot that holds
 * the whole card frame's title bar.
 */
export const TILED = { marginScale: 1 / 20, paddingScale: 1 / 10, barPx: 48 };

/**
 * @param {React.RefObject} containerRef  from useBoardWidth (not used with fitWidth)
 * @param {number} width  board width in px; with fitWidth, the width until the board has measured itself
 * @param {boolean} fitWidth  measure the board's own container and follow it
 * @param {object} spacing  {marginScale, paddingScale, barPx} over the grid's defaults, e.g. TILED
 * @param {number} maxRows  cards cannot be dragged or resized below this row
 * @param {object} grid   LayoutContext gridSettings
 * @param {Array} layout  stored layout of the cards to show, in stacking order (later draws over earlier)
 * @param {Record<string, boolean>} minimized  cards showing only their bar
 * @param {object} stacking  from useCardStacking
 * @param {boolean} locked   no dragging or resizing (a derived view)
 * @param {string} dragHandle  selector of the bar a card is dragged by
 * @param {string} dragCancel  selector of what never starts a drag
 * @param {function} onDragStart  (cardId)
 * @param {function} onDragStop   (reportedLayout)
 * @param {function} onResizeStop (reportedLayout)
 * @param {function} cardProps  (cardId) => extra props for the card's cell
 * @param {function} renderCard (layoutItem, isMinimized) => node
 * @param {*} gridKey  changes to remount the grid, so a preset's positions apply
 */
const CardBoard = ({
  containerRef,
  width,
  grid,
  layout,
  minimized,
  stacking,
  locked = false,
  dragHandle,
  dragCancel = DRAG_CANCEL,
  onDragStart,
  onDragStop,
  onResizeStop,
  cardProps,
  renderCard,
  gridKey,
  fitWidth = false,
  spacing,
  maxRows,
}) => {
  const theme = useTheme();
  const { COLS_COUNT, ROW_HEIGHT_PX, CARD_MARGIN_PX, CONTAINER_PADDING_PX } = grid;

  const margin = CARD_MARGIN_PX * (spacing?.marginScale ?? 1);
  const padding = CONTAINER_PADDING_PX * (spacing?.paddingScale ?? 1);

  // fitWidth: follow the container, whenever it is mounted and however it is resized.
  const [node, setNode] = useState(null);
  const [measured, setMeasured] = useState(0);
  const attach = useCallback((el) => setNode(el), []);
  useEffect(() => {
    if (!fitWidth || !node) return undefined;
    const measure = () => {
      if (node.clientWidth > 0) setMeasured(node.clientWidth);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [fitWidth, node]);
  const boardWidth = fitWidth && measured > 0 ? measured : width;

  // While a card is dragged or resized the pointer crosses other cards; without
  // this the browser reads the press-and-move as selecting their text.
  const boxRef = useRef(null);
  const setBox = useCallback(
    (el) => {
      boxRef.current = el;
      if (fitWidth) attach(el);
      else if (typeof containerRef === "function") containerRef(el);
      else if (containerRef) containerRef.current = el;
    },
    [fitWidth, attach, containerRef],
  );
  const endGesture = useCallback(() => {
    boxRef.current?.classList.remove(GESTURE_CLASS);
    window.removeEventListener("mouseup", endGesture);
  }, []);
  const beginGesture = useCallback(() => {
    window.getSelection()?.removeAllRanges();
    boxRef.current?.classList.add(GESTURE_CLASS);
    // The grid's own stop callback is not reached if the press ends off the page's grid.
    window.addEventListener("mouseup", endGesture);
  }, [endGesture]);
  useEffect(() => endGesture, [endGesture]);

  // An edge-to-edge board can still show a strip of page round each card
  // (the theme's choice); it pads inside the cell, so the grid is unchanged
  // apart from a minimized card's slot, which also holds the strip.
  const cardInset = spacing === TILED ? theme.board?.cardInset ?? 0 : 0;
  const rows = barRows(ROW_HEIGHT_PX, margin, (spacing?.barPx ?? CARD_BAR_PX) + 2 * cardInset);
  const shown = useMemo(() => gridLayout(layout, minimized, rows), [layout, minimized, rows]);

  // The grid resets its items' inline styles; put the stacking order back.
  useEffect(() => {
    stacking.reapply();
  });

  return (
    <Box
      ref={setBox}
      sx={{
        width: "100%",
        [`&.${GESTURE_CLASS}, &.${GESTURE_CLASS} *`]: { userSelect: "none !important" },
        "& .react-grid-item": {
          transition: "transform 0.2s ease-out !important",
          ...(cardInset > 0 && { padding: `${cardInset}px`, boxSizing: "border-box" }),
          "&.react-draggable-dragging": {
            transition: "none !important",
            opacity: 0.9,
            // Outline the card, not its grid cell, so a minimized bar does not
            // drag a full-card frame.
            "& > div": {
              outline: `2px solid ${theme.palette.primary.main}`,
              borderRadius: "4px",
            },
          },
        },
      }}
    >
      <ReactGridLayout
        key={gridKey}
        className="layout"
        layout={shown}
        style={{ transition: "all 0.2s ease-out" }}
        cols={COLS_COUNT}
        rowHeight={ROW_HEIGHT_PX}
        width={boardWidth}
        containerPadding={[padding, padding]}
        margin={[margin, margin]}
        maxRows={maxRows || undefined}
        isDraggable={!locked}
        isResizable={!locked}
        compactType={null}
        preventCollision={false}
        useCSSTransforms={false}
        allowOverlap={true}
        draggableHandle={dragHandle}
        draggableCancel={dragCancel}
        onDragStart={(_layout, item) => {
          beginGesture();
          onDragStart?.(item.i);
        }}
        onDragStop={(reported) => {
          endGesture();
          onDragStop?.(reported);
        }}
        onResizeStart={beginGesture}
        onResizeStop={(reported) => {
          endGesture();
          onResizeStop?.(reported);
        }}
        resizeHandles={RESIZE_HANDLES}
      >
        {shown.map((item) => {
          const cardId = item.i;
          const isMinimized = Boolean(minimized?.[cardId]);
          return (
            <div
              key={cardId}
              data-card-id={cardId}
              style={{
                transition: "transform 0.2s ease-out, box-shadow 0.2s ease-out, rotate 0.15s ease-out, scale 0.15s ease-out",
                height: isMinimized ? "auto" : "100%",
                maxHeight: "100%",
                overflow: "hidden",
              }}
              {...(cardProps ? cardProps(cardId) : null)}
              onMouseDown={() => stacking.raise(cardId)}
              onMouseUp={stacking.reapply}
            >
              {renderCard(item, isMinimized)}
            </div>
          );
        })}
      </ReactGridLayout>
    </Box>
  );
};

CardBoard.propTypes = {
  containerRef: PropTypes.oneOfType([PropTypes.object, PropTypes.func]),
  width: PropTypes.number.isRequired,
  grid: PropTypes.object.isRequired,
  layout: PropTypes.array.isRequired,
  minimized: PropTypes.object,
  stacking: PropTypes.object.isRequired,
  locked: PropTypes.bool,
  dragHandle: PropTypes.string.isRequired,
  dragCancel: PropTypes.string,
  onDragStart: PropTypes.func,
  onDragStop: PropTypes.func,
  onResizeStop: PropTypes.func,
  cardProps: PropTypes.func,
  renderCard: PropTypes.func.isRequired,
  gridKey: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  fitWidth: PropTypes.bool,
  spacing: PropTypes.object,
  maxRows: PropTypes.number,
};

export default CardBoard;
