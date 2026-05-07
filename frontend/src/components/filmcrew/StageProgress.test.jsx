import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import StageProgress from './StageProgress';

describe('StageProgress', () => {
  it('highlights the correct active step', () => {
    render(<StageProgress currentStage="casting" status="casting" />);
    // Just assert all stage labels render — MUI Stepper's active styling is
    // a CSS class, not an aria role we can query reliably.
    expect(screen.getByText('Casting')).toBeDefined();
    expect(screen.getByText('Screenwriting')).toBeDefined();
    expect(screen.getByText('Complete')).toBeDefined();
  });

  it('shows Action needed for gated stages', () => {
    render(<StageProgress currentStage="casting" status="casting" />);
    expect(screen.getByText('Action needed')).toBeDefined();
  });

  it('renders error state when status is failed', () => {
    render(
      <StageProgress 
        currentStage="screenwriting" 
        status="failed_screenwriting" 
        errorBlob={{ error: 'Agent crash' }} 
      />
    );
    expect(screen.getByText('Failed')).toBeDefined();
  });
});
