// frontend/src/hooks/useDeferredClick.js
//
// A plain click that waits out a possible double click: `schedule(fn)` runs fn
// after `delayMs` unless `cancel()` (called from onDoubleClick) comes first.
// Lets one item open its details on a click and its full-size view on a
// double click without the first click of the pair doing both.

import { useCallback, useEffect, useRef } from 'react';

// Long enough for an ordinary double click, short enough not to feel slow.
export const DOUBLE_CLICK_MS = 250;

export default function useDeferredClick(delayMs = DOUBLE_CLICK_MS) {
  const timer = useRef(null);
  const cancel = useCallback(() => {
    clearTimeout(timer.current);
    timer.current = null;
  }, []);
  const schedule = useCallback((fn) => {
    cancel();
    timer.current = setTimeout(() => {
      timer.current = null;
      fn();
    }, delayMs);
  }, [cancel, delayMs]);
  useEffect(() => cancel, [cancel]);
  return { schedule, cancel };
}
