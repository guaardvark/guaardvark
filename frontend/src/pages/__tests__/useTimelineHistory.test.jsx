import { renderHook, act } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { useTimelineHistory } from '../../components/videoeditor/useTimelineHistory';

describe('useTimelineHistory', () => {
  const initialState = {
    video: null,
    textElements: [],
    audio: null,
  };

  it('undo reverts a property edit', () => {
    const { result } = renderHook(() => useTimelineHistory(initialState));

    // add a text element
    act(() => {
      result.current.commitTimeline({
        ...initialState,
        textElements: [{ id: '1', fontSize: 48 }],
      });
    });

    expect(result.current.timeline.textElements[0].fontSize).toBe(48);

    // update its fontSize
    act(() => {
      result.current.commitTimeline((prev) => ({
        ...prev,
        textElements: [{ id: '1', fontSize: 24 }],
      }));
    });

    expect(result.current.timeline.textElements[0].fontSize).toBe(24);

    // hit Cmd+Z (undo)
    act(() => {
      result.current.handleUndo();
    });

    // assert fontSize reverted to original
    expect(result.current.timeline.textElements[0].fontSize).toBe(48);
  });

  it('undo reverts adding a video', () => {
    const { result } = renderHook(() => useTimelineHistory(initialState));

    // call handleAddMedia (which calls commitTimeline)
    act(() => {
      result.current.commitTimeline({
        ...initialState,
        video: { documentId: 'vid-1' },
      });
    });

    expect(result.current.timeline.video).not.toBeNull();

    // hit Cmd+Z
    act(() => {
      result.current.handleUndo();
    });

    // assert timeline.video is back to null
    expect(result.current.timeline.video).toBeNull();
  });

  it('undo stack is capped at 20', () => {
    const { result } = renderHook(() => useTimelineHistory(initialState));

    // perform 25 mutations
    for (let i = 1; i <= 25; i++) {
      act(() => {
        result.current.commitTimeline({
          ...initialState,
          textElements: [{ id: String(i) }],
        });
      });
    }

    // assert historyRef.current.length <= 20
    expect(result.current.historyRef.current.length).toBeLessThanOrEqual(20);
    expect(result.current.historyRef.current.length).toBe(20);

    // If we undo, it should give us the 24th mutation (since 25th is current)
    act(() => {
      result.current.handleUndo();
    });
    expect(result.current.timeline.textElements[0].id).toBe('24');
  });
});
