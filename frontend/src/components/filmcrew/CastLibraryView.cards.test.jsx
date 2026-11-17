import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../../contexts/LayoutContext', () => {
  const layout = {
    gridSettings: {
      CONTAINER_PADDING_PX: 4,
      CARD_MARGIN_PX: 8,
      COLS_COUNT: 175,
      ROW_HEIGHT_PX: 10,
      cardGridW: 35,
      cardGridH: 49,
      cardMinGridW: 30,
    },
  };
  return { useDashboardWidth: () => 1750, useLayout: () => layout };
});

vi.mock('../../api/productionService', () => ({
  listCastLibrary: vi.fn(async () => ({
    subjects: [
      { id: 5, name: 'Sage Harlow', kind: 'character', training_status: 'trained' },
      { id: 6, name: 'Brass lamp', kind: 'prop', training_status: 'untrained' },
    ],
  })),
  createCastSubject: vi.fn(),
  deleteCastSubject: vi.fn(),
}));

import CastLibraryView from './CastLibraryView';

const card = (id) => document.querySelector(`[data-card-id="${id}"]`);

describe('CastLibraryView as cards', () => {
  beforeEach(() => {
    global.ResizeObserver = class {
      observe() {}
      disconnect() {}
    };
    global.fetch = vi.fn(async () => ({ ok: false, status: 404, statusText: 'NOT FOUND', json: async () => ({}) }));
  });
  afterEach(() => {
    delete global.ResizeObserver;
  });

  it('lists each kind in its own card and offers a new subject of an empty kind', async () => {
    render(
      <MemoryRouter>
        <CastLibraryView asCards />
      </MemoryRouter>,
    );
    await screen.findByText('Sage Harlow');
    expect(within(card('character')).getByText('Sage Harlow')).toBeInTheDocument();
    expect(within(card('prop')).getByText('Brass lamp')).toBeInTheDocument();
    expect(within(card('character')).queryByText('Brass lamp')).toBeNull();

    fireEvent.click(within(card('environment')).getByRole('button', { name: 'New Subject' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('environment')).toBeInTheDocument();
  });
});
