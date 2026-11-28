// Behaviour every card board shares: measuring itself, stacking the card that
// was touched last on top, and turning what the grid reports after a drag or
// resize into the layout to store.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { mergeReported } from "./boardGeometry";

/**
 * The board's width in px. Starts from the page's computed width and follows
 * the container when it is mounted with the page.
 * @returns {[React.RefObject, number]} a ref for the board container, and the width
 */
export function useBoardWidth(pageWidth) {
  const containerRef = useRef(null);
  const [width, setWidth] = useState(pageWidth);

  useEffect(() => {
    if (pageWidth > 0) setWidth(pageWidth);
  }, [pageWidth]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return undefined;
    const measure = () => {
      if (el.clientWidth > 0) setWidth(el.clientWidth);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return [containerRef, width];
}

const gridItemOf = (cardId) => {
  const el = document.querySelector(`[data-card-id="${cardId}"]`);
  return el?.closest(".react-grid-item") || el;
};

/**
 * Last-touch stacking. The card touched last is drawn above the rest, and the
 * others keep the order they were touched in; a card never touched stays under
 * every touched one. The order lives in memory only. It is written to the grid
 * items as z-index 1..n, and the grid rewrites its items' inline styles, so the
 * board re-applies it after each render. The board isolates its own stacking,
 * so these values never compete with anything outside it.
 */
export function useCardStacking() {
  const order = useRef([]);

  const reapply = useCallback(() => {
    order.current.forEach((cardId, idx) => {
      const item = gridItemOf(cardId);
      if (item) item.style.zIndex = idx + 1;
    });
  }, []);

  const raise = useCallback(
    (cardId) => {
      const last = order.current[order.current.length - 1];
      if (last !== cardId) order.current = [...order.current.filter((id) => id !== cardId), cardId];
      reapply();
    },
    [reapply],
  );

  const reset = useCallback(() => {
    order.current.forEach((cardId) => {
      const item = gridItemOf(cardId);
      if (item) item.style.zIndex = "";
    });
    order.current = [];
  }, []);

  return useMemo(() => ({ raise, reapply, reset }), [raise, reapply, reset]);
}

/**
 * Drag, resize and minimize bookkeeping. A minimized card is handed to the
 * grid at the height of its bar while its stored height stays what it opens
 * to, so a gesture's report has to be read against the flags as they were when
 * the gesture began: a double-click on a bar is also a press that starts a
 * drag, and the toggle lands before the drag reports.
 *
 * @param {() => Array} getLayout  the stored layout, current at call time
 * @param {() => Record<string, boolean>} getMinimized  minimized flags, current at call time
 */
export function useBoardGestures(getLayout, getMinimized) {
  const suspended = useRef(false);
  const dragging = useRef(false);
  const flagsAtStart = useRef(null);

  const onDragStart = useCallback(() => {
    dragging.current = true;
    const live = { ...getMinimized() };
    // A toggle on this same press has already recorded the flag from before
    // it flipped; that wins over the live map.
    flagsAtStart.current = flagsAtStart.current ? { ...live, ...flagsAtStart.current } : live;
  }, [getMinimized]);

  /** The layout to store after the grid reports, or null when the report is to be ignored. */
  const settle = useCallback(
    (reported, fromDrag) => {
      dragging.current = false;
      if (suspended.current) {
        flagsAtStart.current = null;
        return null;
      }
      const flags = fromDrag ? flagsAtStart.current || getMinimized() : getMinimized();
      flagsAtStart.current = null;
      return mergeReported(reported, getLayout(), flags);
    },
    [getLayout, getMinimized],
  );

  /** Ignore grid reports until the next frame: the board is placing cards itself. */
  const suspendFrame = useCallback((after) => {
    suspended.current = true;
    requestAnimationFrame(() => {
      suspended.current = false;
      after?.();
    });
  }, []);

  /** Call before flipping a card's minimized flag. */
  const beginToggle = useCallback(
    (cardId) => {
      flagsAtStart.current = { ...(flagsAtStart.current || {}), [cardId]: !!getMinimized()[cardId] };
      suspendFrame(() => {
        if (!dragging.current) flagsAtStart.current = null;
      });
    },
    [getMinimized, suspendFrame],
  );

  return useMemo(
    () => ({ onDragStart, settle, suspendFrame, beginToggle }),
    [onDragStart, settle, suspendFrame, beginToggle],
  );
}

/**
 * The width of an element, followed whenever it is mounted and however it is
 * resized. `fallback` is the width until the first measurement.
 * @returns {[function, number]} a ref callback for the element, and its width in px
 */
export function useMeasuredWidth(fallback) {
  const [node, setNode] = useState(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    if (!node) return undefined;
    const measure = () => {
      if (node.clientWidth > 0) setWidth(node.clientWidth);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [node]);
  return [setNode, width > 0 ? width : fallback];
}
