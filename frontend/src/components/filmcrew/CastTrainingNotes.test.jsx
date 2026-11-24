import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { TrainingOutcomeNotice, SmokeIdentityNotice } from './CastTrainingNotes';

const NOTE = /Training finished and the LoRA file was saved complete/;

describe('TrainingOutcomeNotice', () => {
  it('shows the saved-LoRA note on a trained member as information', () => {
    render(<TrainingOutcomeNotice subject={{
      training_status: 'trained',
      training_error: 'Training finished and the LoRA was saved (worker stopped during the test render). Ready to use.',
      training_note: 'lora_saved_worker_stopped',
    }} />);
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent(NOTE);
    expect(alert.className).toMatch(/Info/);
    expect(screen.queryByText('Training failed')).toBeNull();
  });

  it('shows nothing for a normal trained member, or one with an old error and no note', () => {
    const { container, rerender } = render(<TrainingOutcomeNotice subject={{ training_status: 'trained' }} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<TrainingOutcomeNotice subject={{
      training_status: 'trained', training_error: 'Cancelled by user', training_note: null,
    }} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('keeps the failure alert for a failed member', () => {
    render(<TrainingOutcomeNotice subject={{ training_status: 'failed', training_error: 'CUDA not visible' }} />);
    const alert = screen.getByRole('alert');
    expect(alert.className).toMatch(/Error/);
    expect(alert).toHaveTextContent('Training failed');
    expect(alert).toHaveTextContent('CUDA not visible');
  });

  it('ignores a note code on a member that is not trained', () => {
    const { container } = render(<TrainingOutcomeNotice subject={{
      training_status: 'training', training_note: 'lora_saved_worker_stopped',
    }} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('SmokeIdentityNotice', () => {
  const measured = {
    ok: true, status: 'measured', score: 0.82, threshold: 0.75, method: 'embed', current: true,
  };

  it('shows the score measured on the default LoRA', () => {
    render(<SmokeIdentityNotice subject={{ lora_path: '/l/v2.safetensors', smoke_identity: measured }} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Post-train smoke identity score: 0.82 (embed)');
  });

  it('withholds a score that belongs to another LoRA', () => {
    render(<SmokeIdentityNotice subject={{
      lora_path: '/l/v2.safetensors', smoke_identity: { ...measured, current: false },
    }} />);
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.queryByText(/0\.82/)).toBeNull();
    expect(screen.getByText('Identity: not measured — the current LoRA has no finished test render')).toBeInTheDocument();
  });

  it('says nothing about another LoRA when the member has no default', () => {
    const { container } = render(<SmokeIdentityNotice subject={{ smoke_identity: { ...measured, current: false } }} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('keeps the withheld wording for a current record without a number', () => {
    render(<SmokeIdentityNotice subject={{
      lora_path: '/l/v1.safetensors',
      smoke_identity: { ok: true, status: 'not_measured', reason: 'no reference photos on disk', current: true },
    }} />);
    expect(screen.getByText('Identity: not measured — no reference photos on disk')).toBeInTheDocument();
  });

  it('shows a record from an older API without the current flag as before', () => {
    render(<SmokeIdentityNotice subject={{
      lora_path: '/l/v1.safetensors', smoke_identity: { ...measured, current: undefined },
    }} />);
    expect(screen.getByRole('alert')).toHaveTextContent('0.82');
  });
});
