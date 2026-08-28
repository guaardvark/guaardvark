import React from 'react';
import { render } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import FloatingChatProvider from '../FloatingChatProvider';

// Mock react-router-dom
vi.mock('react-router-dom', () => ({
  useLocation: vi.fn(),
}));

// Mock hooks
vi.mock('../../../hooks/usePageContext', () => ({
  usePageContext: vi.fn(() => ({ page: 'Unknown' })),
}));

// Mock the components it renders so we don't have to deal with their complex trees
vi.mock('../FloatingChatFAB', () => ({
  default: () => <div data-testid="fab" />,
}));
vi.mock('../FloatingChatCard', () => ({
  default: () => <div data-testid="card" />,
}));

// Mock Zustand store
const mockSetIsOpen = vi.fn();
const mockToggleOpen = vi.fn();
const mockSetPageContext = vi.fn();

vi.mock('../../../stores/useFloatingChatStore', () => ({
  useFloatingChatStore: vi.fn((selector) => {
    const state = {
      isOpen: true,
      setIsOpen: mockSetIsOpen,
      toggleOpen: mockToggleOpen,
      setPageContext: mockSetPageContext,
    };
    return selector(state);
  }),
}));

import { useLocation } from 'react-router-dom';

describe('FloatingChatProvider', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders components when not on /chat', () => {
    useLocation.mockReturnValue({ pathname: '/dashboard' });
    const { queryByTestId } = render(<FloatingChatProvider />);
    
    expect(queryByTestId('fab')).not.toBeNull();
    expect(queryByTestId('card')).not.toBeNull();
    // It should not close the card automatically
    expect(mockSetIsOpen).not.toHaveBeenCalled();
  });

  it('hides components and closes the card when on /chat', () => {
    useLocation.mockReturnValue({ pathname: '/chat' });
    const { queryByTestId } = render(<FloatingChatProvider />);
    
    // Should be unmounted
    expect(queryByTestId('fab')).toBeNull();
    expect(queryByTestId('card')).toBeNull();
    
    // Should have called setIsOpen(false) to ensure it stays closed when navigating away
    expect(mockSetIsOpen).toHaveBeenCalledWith(false);
  });
});
