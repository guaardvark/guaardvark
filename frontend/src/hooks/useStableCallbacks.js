// Functions for memoized children: each keeps one identity for the life of the
// component, and calling it runs the version from the latest render.

import { useMemo, useRef } from "react";

/**
 * @param {Object<string, Function>} callbacks  the same keys on every render
 * @returns {Object<string, Function>}
 */
export default function useStableCallbacks(callbacks) {
  const latest = useRef(callbacks);
  latest.current = callbacks;
  return useMemo(
    () => Object.fromEntries(Object.keys(latest.current).map((name) => [name, (...args) => latest.current[name](...args)])),
    [],
  );
}
