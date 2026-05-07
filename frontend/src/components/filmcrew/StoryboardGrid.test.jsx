import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import StoryboardGrid from './StoryboardGrid';

describe('StoryboardGrid', () => {
  const mockShots = [
    { id: 1, scene_number: 1, shot_number: 1, description: 'Close up of hero', approved: false }
  ];

  it('renders "Approve & Render" button only when stage is awaiting_approval', () => {
    const { rerender } = render(
      <StoryboardGrid currentStage="storyboard_gen" shots={mockShots} />
    );
    expect(screen.queryByText(/Approve & Render/i)).toBeNull();

    rerender(<StoryboardGrid currentStage="awaiting_approval" shots={mockShots} />);
    expect(screen.getByText(/Approve & Render/i)).toBeDefined();
  });

  it('opens regenerate dialog when Refresh icon is clicked', () => {
    render(<StoryboardGrid currentStage="awaiting_approval" shots={mockShots} />);
    const regenBtn = screen.getByRole('button', { name: /Regenerate this shot/i });
    fireEvent.click(regenBtn);
    expect(screen.getByText(/Regenerate Shot 1.1/i)).toBeDefined();
  });
});
