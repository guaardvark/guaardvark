// The board every card page is drawn on: a free-placement grid where cards are
// dragged by their bar, resized from any edge, overlap, stack by last touch and
// minimize to their bar. The page owns which cards exist, what they show and
// where the layout is saved; the board owns how they behave.

import React, { useEffect, useMemo } from "react";
import PropTypes from "prop-types";
import { Box, useTheme } from "@mui/material";
import ReactGridLayout from "react-grid-layout/legacy";
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";
import { barRows, gridLayout } from "./boardGeometry";

const RESIZE_HANDLES = ["s", "w", "e", "n", "sw", "nw", "se", "ne"];

/** Never starts a drag, whatever the card puts in its bar. */
export const DRAG_CANCEL = "button, input, textarea, select, option, .non-draggable";

/**
 * @param {React.RefObject} containerRef  from useBoardWidth
 * @param {number} width  board width in px
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
}) => {
  const theme = useTheme();
  const { COLS_COUNT, ROW_HEIGHT_PX, CARD_MARGIN_PX, CONTAINER_PADDING_PX } = grid;

  const rows = barRows(ROW_HEIGHT_PX, CARD_MARGIN_PX);
  const shown = useMemo(() => gridLayout(layout, minimized, rows), [layout, minimized, rows]);

  // The grid resets its items' inline styles; put the stacking order back.
  useEffect(() => {
    stacking.reapply();
  });

  return (
    <Box
      ref={containerRef}
      sx={{
        width: "100%",
        "& .react-grid-item": {
          transition: "transform 0.2s ease-out !important",
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
        width={width}
        containerPadding={[CONTAINER_PADDING_PX, CONTAINER_PADDING_PX]}
        margin={[CARD_MARGIN_PX, CARD_MARGIN_PX]}
        isDraggable={!locked}
        isResizable={!locked}
        compactType={null}
        preventCollision={false}
        useCSSTransforms={false}
        allowOverlap={true}
        draggableHandle={dragHandle}
        draggableCancel={dragCancel}
        onDragStart={onDragStart ? (_layout, item) => onDragStart(item.i) : undefined}
        onDragStop={onDragStop}
        onResizeStop={onResizeStop}
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
                transition: "transform 0.2s ease-out, box-shadow 0.2s ease-out",
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
  containerRef: PropTypes.object,
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
};

export default CardBoard;
