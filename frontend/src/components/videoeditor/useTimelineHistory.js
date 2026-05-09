import { useState, useRef, useCallback } from 'react';

export function useTimelineHistory(initialState) {
  const [timeline, setTimeline] = useState(initialState);
  const historyRef = useRef([]);
  const pendingSnapshotRef = useRef(false);

  const commitTimeline = useCallback((updater) => {
    setTimeline((prev) => {
      const nextState = typeof updater === 'function' ? updater(prev) : updater;
      
      // Only record history if we're not inside a debounced continuous action
      if (!pendingSnapshotRef.current) {
        historyRef.current.push(prev);
        if (historyRef.current.length > 20) {
          historyRef.current.shift();
        }
      }
      
      return nextState;
    });
  }, []);

  const handleUndo = useCallback(() => {
    if (historyRef.current.length === 0) return;
    const prev = historyRef.current.pop();
    setTimeline(prev);
  }, []);

  return { timeline, setTimeline, commitTimeline, handleUndo, historyRef, pendingSnapshotRef };
}
