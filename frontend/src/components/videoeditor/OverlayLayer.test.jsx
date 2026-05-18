import React from 'react';
import { render, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import OverlayLayer from './OverlayLayer';

describe('OverlayLayer', () => {
  const defaultTextElements = [
    { id: '1', text: 'First', x: 10, y: 20, rotation: 0, fontSize: 24, fontColor: 'white' },
    { id: '2', text: 'Second', x: 30, y: 40, rotation: 0, fontSize: 24, fontColor: 'white' },
  ];

  it('renders all text elements', () => {
    const { getByTestId } = render(
      <OverlayLayer
        textElements={defaultTextElements}
        selectedTextId={null}
        onSelectText={vi.fn()}
        onMoveText={vi.fn()}
      />
    );

    const el1 = getByTestId('overlay-text-1');
    expect(el1).toHaveStyle('left: 10px');
    expect(el1).toHaveStyle('top: 20px');

    const el2 = getByTestId('overlay-text-2');
    expect(el2).toHaveStyle('left: 30px');
    expect(el2).toHaveStyle('top: 40px');
  });

  it('onMoveText fires once on mouseup with final coords, not on every mousemove', () => {
    const onMoveText = vi.fn();
    const { getByTestId } = render(
      <OverlayLayer
        textElements={defaultTextElements}
        selectedTextId={null}
        onSelectText={vi.fn()}
        onMoveText={onMoveText}
      />
    );

    const el = getByTestId('overlay-text-1');

    // mousedown on element
    fireEvent.mouseDown(el, { clientX: 100, clientY: 100 });

    // mousemove three times
    fireEvent.mouseMove(window, { clientX: 200, clientY: 150 });
    fireEvent.mouseMove(window, { clientX: 250, clientY: 175 });
    fireEvent.mouseMove(window, { clientX: 300, clientY: 200 });

    // check that onMoveText hasn't been called yet
    expect(onMoveText).not.toHaveBeenCalled();

    // The element should have moved visually (local state)
    // original x=10, y=20. dx = 200, dy = 100.
    // So final local state x = 10 + 200 = 210, y = 20 + 100 = 120.
    expect(el).toHaveStyle('left: 210px');
    expect(el).toHaveStyle('top: 120px');

    // mouseup
    fireEvent.mouseUp(window, { clientX: 300, clientY: 200 });

    // Assert onMoveText called exactly once
    expect(onMoveText).toHaveBeenCalledTimes(1);
    expect(onMoveText).toHaveBeenCalledWith('1', 210, 120);
  });

  it('removes window listeners on unmount', () => {
    const onMoveText = vi.fn();
    const { getByTestId, unmount } = render(
      <OverlayLayer
        textElements={defaultTextElements}
        selectedTextId={null}
        onSelectText={vi.fn()}
        onMoveText={onMoveText}
      />
    );

    const el = getByTestId('overlay-text-1');
    fireEvent.mouseDown(el, { clientX: 100, clientY: 100 });

    // Unmount while dragging
    unmount();

    // Fire mousemove and mouseup on window
    fireEvent.mouseMove(window, { clientX: 200, clientY: 200 });
    fireEvent.mouseUp(window, { clientX: 200, clientY: 200 });

    expect(onMoveText).not.toHaveBeenCalled();
  });
});
