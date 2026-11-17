import { useCallback, useEffect, useRef } from "react";

// Within this many pixels of the bottom still counts as "at the bottom".
const PIN_SLACK_PX = 24;

/**
 * Keep a scrollable element pinned to its tail while content streams in,
 * and stop following once the reader scrolls up. Scrolling back to the
 * bottom resumes following.
 *
 * @param {boolean} active - follow only while this is true
 * @param {*} tick - any value that changes when new content arrives
 * @returns {{ ref: object, onScroll: Function }} spread onto the scroll element
 */
export default function useFollowTail(active, tick) {
  const ref = useRef(null);
  const pinnedRef = useRef(true);

  const onScroll = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight <= PIN_SLACK_PX;
  }, []);

  useEffect(() => {
    const el = ref.current;
    if (active && el && pinnedRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [active, tick]);

  return { ref, onScroll };
}
