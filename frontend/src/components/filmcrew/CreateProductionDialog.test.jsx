import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import CreateProductionDialog from './CreateProductionDialog';

// Mock the project service
vi.mock('../../api/projectService', () => ({
  getProjects: vi.fn(() => Promise.resolve([{ id: 1, name: 'Test Project' }]))
}));

describe('CreateProductionDialog', () => {
  it('calls onCreated with form data when submitted', async () => {
    const onCreated = vi.fn();
    render(<CreateProductionDialog open={true} onClose={() => {}} onCreated={onCreated} />);
    
    fireEvent.change(screen.getByLabelText(/Production Name/i), { target: { value: 'My Movie' } });
    fireEvent.change(screen.getByLabelText(/Script Text/i), { target: { value: 'INT. COFFEE SHOP' } });
    
    const submitBtn = screen.getByText(/Roll Cameras/i);
    fireEvent.click(submitBtn);
    
    await waitFor(() => {
      expect(onCreated).toHaveBeenCalledWith({
        name: 'My Movie',
        script_text: 'INT. COFFEE SHOP',
        project_id: null
      });
    });
  });

  it('validates required fields', () => {
    render(<CreateProductionDialog open={true} onClose={() => {}} onCreated={() => {}} />);
    const submitBtn = screen.getByText(/Roll Cameras/i);
    expect(submitBtn).toBeDisabled();
  });
});
