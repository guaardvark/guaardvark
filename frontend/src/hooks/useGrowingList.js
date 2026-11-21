// Draw a long list a screenful at a time: the first `initial` items, then
// `step` more each time the end of the list comes near the visible area.
// Mounting hundreds of cards at once holds the main thread for most of a
// second on every visit to the page.

import { useEffect, useRef, useState } from "react";

/** Styles for the marker element; its parent needs `position: relative`. */
export const growMarkerStyle = {
  position: "absolute",
  left: 0,
  bottom: 0,
  width: 1,
  // Reaches up from the end of the list so more is drawn before the end shows.
  height: 600,
  pointerEvents: "none",
};

/**
 * @param {Array} items
 * @param {{initial?: number, step?: number}} [options]
 * @returns {{shown: Array, markerRef: object, hasMore: boolean}} put `markerRef`
 *   on an element styled with `growMarkerStyle` right after the list
 */
export default function useGrowingList(items, { initial = 24, step = 24 } = {}) {
  // Without a way to tell when the end comes near, the whole list is drawn.
  const canWatch = typeof IntersectionObserver !== "undefined";
  const [count, setCount] = useState(initial);
  const markerRef = useRef(null);
  const total = items.length;
  const hasMore = canWatch && count < total;

  useEffect(() => {
    const marker = markerRef.current;
    if (!hasMore || !marker) return undefined;
    // Re-made after each step, so a marker that is still in view asks again.
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) setCount((n) => n + step);
    });
    observer.observe(marker);
    return () => observer.disconnect();
  }, [hasMore, count, step]);

  return { shown: hasMore ? items.slice(0, count) : items, markerRef, hasMore };
}
