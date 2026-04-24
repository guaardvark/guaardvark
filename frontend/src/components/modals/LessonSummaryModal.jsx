// LessonSummaryModal — shown after End Lesson OR reopened from the Memory
// management page for any memory with source="lesson_summary". Lets the user
// correct misperceived steps before they harden into future prompt context.
// PATCHes /api/memory/<id> with { content: JSON.stringify({title, steps}) }.
/* eslint-env browser */
import React, { useEffect, useState, useCallback } from 'react';
import PropTypes from 'prop-types';
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import ArrowUpwardIcon from '@mui/icons-material/ArrowUpward';
import ArrowDownwardIcon from '@mui/icons-material/ArrowDownward';
import AddIcon from '@mui/icons-material/Add';
import { BASE_URL } from '../../api/apiClient';

const renumber = (steps) =>
  steps.map((s, i) => ({ ...s, order: i + 1 }));

export default function LessonSummaryModal({
  open,
  onClose,
  memoryId,
  initialTitle,
  initialSteps,
  initialParameters,
  onSaved,
}) {
  const [title, setTitle] = useState('');
  const [steps, setSteps] = useState([]);
  const [parameters, setParameters] = useState([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!open) return;
    setTitle((initialTitle || '').trim());
    const seeded = Array.isArray(initialSteps)
      ? initialSteps.map((s, i) => ({
          order: s?.order ?? i + 1,
          text: (s?.text ?? String(s ?? '')).toString(),
        }))
      : [];
    setSteps(renumber(seeded));
    const seededParams = Array.isArray(initialParameters)
      ? initialParameters.map((p) => ({
          name: (p?.name || '').toString().trim(),
          description: (p?.description || '').toString(),
          example: (p?.example || '').toString(),
        }))
      : [];
    setParameters(seededParams);
    setError(null);
  }, [open, initialTitle, initialSteps, initialParameters]);

  const updateStep = useCallback((idx, text) => {
    setSteps((prev) => {
      const next = [...prev];
      next[idx] = { ...next[idx], text };
      return next;
    });
  }, []);

  const moveStep = useCallback((idx, dir) => {
    setSteps((prev) => {
      const target = idx + dir;
      if (target < 0 || target >= prev.length) return prev;
      const next = [...prev];
      [next[idx], next[target]] = [next[target], next[idx]];
      return renumber(next);
    });
  }, []);

  const removeStep = useCallback((idx) => {
    setSteps((prev) => renumber(prev.filter((_, i) => i !== idx)));
  }, []);

  const addStep = useCallback(() => {
    setSteps((prev) => renumber([...prev, { order: prev.length + 1, text: '' }]));
  }, []);

  const updateParam = useCallback((idx, field, value) => {
    setParameters((prev) => {
      const next = [...prev];
      next[idx] = { ...next[idx], [field]: value };
      return next;
    });
  }, []);

  const removeParam = useCallback((idx) => {
    setParameters((prev) => prev.filter((_, i) => i !== idx));
  }, []);

  const addParam = useCallback(() => {
    setParameters((prev) => [...prev, { name: '', description: '', example: '' }]);
  }, []);

  const handleSave = useCallback(async () => {
    if (!memoryId) {
      setError('Missing memory_id — cannot save.');
      return;
    }
    const cleanedSteps = steps
      .map((s) => ({ order: s.order, text: (s.text || '').trim() }))
      .filter((s) => s.text.length > 0);
    if (cleanedSteps.length === 0) {
      setError('At least one step is required.');
      return;
    }
    // Only keep params with a name. Names get normalized to snake_case_lower
    // so {Channel} and {channel} don't ship as two different placeholders.
    const cleanedParams = parameters
      .map((p) => ({
        name: (p.name || '').trim().toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^_+|_+$/g, ''),
        description: (p.description || '').trim(),
        example: (p.example || '').trim(),
      }))
      .filter((p) => p.name.length > 0);
    const cleanTitle = (title || '').trim() || 'Lesson';
    const payload = {
      content: JSON.stringify({
        title: cleanTitle,
        steps: renumber(cleanedSteps),
        parameters: cleanedParams,
      }),
    };

    setSaving(true);
    setError(null);
    try {
      const res = await fetch(`${BASE_URL}/memory/${memoryId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.success === false) {
        throw new Error(data?.error || `HTTP ${res.status}`);
      }
      if (onSaved) onSaved(data.memory);
      onClose && onClose();
    } catch (err) {
      setError(`Save failed: ${err.message}`);
    } finally {
      setSaving(false);
    }
  }, [memoryId, title, steps, parameters, onClose, onSaved]);

  return (
    <Dialog open={!!open} onClose={saving ? undefined : onClose} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ pb: 1 }}>
        Lesson Summary
        <Typography variant="caption" sx={{ display: 'block', color: 'text.secondary', fontSize: '0.75rem' }}>
          Fix anything the distiller got wrong — this is what future Gemma4 will read.
        </Typography>
      </DialogTitle>
      <DialogContent dividers>
        <TextField
          label="Title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          fullWidth
          size="small"
          sx={{ mb: 2 }}
          autoFocus
        />

        <Typography variant="subtitle2" sx={{ mb: 1, color: 'text.secondary' }}>
          Steps
        </Typography>

        {steps.length === 0 && (
          <Typography variant="body2" sx={{ color: 'text.secondary', fontStyle: 'italic', mb: 1 }}>
            No steps yet — add one below.
          </Typography>
        )}

        {steps.map((step, idx) => (
          <Box
            key={idx}
            sx={{
              display: 'flex',
              alignItems: 'flex-start',
              gap: 0.75,
              mb: 1,
              p: 0.5,
              borderRadius: 1,
              '&:hover': { bgcolor: 'action.hover' },
            }}
          >
            <Box
              sx={{
                minWidth: 24,
                height: 24,
                borderRadius: '50%',
                bgcolor: 'primary.dark',
                color: 'primary.contrastText',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: '0.75rem',
                fontWeight: 700,
                mt: 0.5,
              }}
            >
              {idx + 1}
            </Box>
            <TextField
              value={step.text}
              onChange={(e) => updateStep(idx, e.target.value)}
              multiline
              minRows={1}
              maxRows={4}
              size="small"
              fullWidth
              placeholder="Describe one action, imperative voice"
            />
            <Box sx={{ display: 'flex', flexDirection: 'column' }}>
              <Tooltip title="Move up">
                <span>
                  <IconButton
                    size="small"
                    onClick={() => moveStep(idx, -1)}
                    disabled={idx === 0}
                    sx={{ p: 0.25 }}
                  >
                    <ArrowUpwardIcon sx={{ fontSize: 16 }} />
                  </IconButton>
                </span>
              </Tooltip>
              <Tooltip title="Move down">
                <span>
                  <IconButton
                    size="small"
                    onClick={() => moveStep(idx, 1)}
                    disabled={idx === steps.length - 1}
                    sx={{ p: 0.25 }}
                  >
                    <ArrowDownwardIcon sx={{ fontSize: 16 }} />
                  </IconButton>
                </span>
              </Tooltip>
            </Box>
            <Tooltip title="Remove step">
              <IconButton size="small" onClick={() => removeStep(idx)} sx={{ p: 0.25, mt: 0.5 }}>
                <CloseIcon sx={{ fontSize: 16, color: 'text.secondary' }} />
              </IconButton>
            </Tooltip>
          </Box>
        ))}

        <Button
          size="small"
          startIcon={<AddIcon sx={{ fontSize: 16 }} />}
          onClick={addStep}
          sx={{ mt: 1, textTransform: 'none', fontSize: '0.8rem' }}
        >
          Add step
        </Button>

        {/* Parameters — the placeholder slots used inside steps like {channel},
            {search_term}. Makes the lesson reusable across different targets. */}
        <Typography variant="subtitle2" sx={{ mt: 3, mb: 0.5, color: 'text.secondary' }}>
          Parameters
        </Typography>
        <Typography variant="caption" sx={{ display: 'block', color: 'text.secondary', mb: 1, fontSize: '0.72rem' }}>
          Placeholders used inside steps (e.g. <code>{'{channel}'}</code>). Keeps the lesson generic so it applies to any target.
        </Typography>

        {parameters.length === 0 && (
          <Typography variant="body2" sx={{ color: 'text.secondary', fontStyle: 'italic', mb: 1, fontSize: '0.8rem' }}>
            No parameters — this lesson is fully concrete. Add one if any step should become a reusable slot.
          </Typography>
        )}

        {parameters.map((p, idx) => (
          <Box
            key={idx}
            sx={{
              display: 'flex',
              alignItems: 'flex-start',
              gap: 0.75,
              mb: 1,
              p: 0.5,
              borderRadius: 1,
              '&:hover': { bgcolor: 'action.hover' },
            }}
          >
            <TextField
              value={p.name}
              onChange={(e) => updateParam(idx, 'name', e.target.value)}
              size="small"
              placeholder="name"
              sx={{ width: 140 }}
              InputProps={{
                startAdornment: <Typography variant="caption" sx={{ color: 'text.secondary', mr: 0.25 }}>{'{'}</Typography>,
                endAdornment: <Typography variant="caption" sx={{ color: 'text.secondary', ml: 0.25 }}>{'}'}</Typography>,
              }}
            />
            <TextField
              value={p.description}
              onChange={(e) => updateParam(idx, 'description', e.target.value)}
              size="small"
              fullWidth
              placeholder="what this represents"
            />
            <TextField
              value={p.example}
              onChange={(e) => updateParam(idx, 'example', e.target.value)}
              size="small"
              placeholder="example"
              sx={{ width: 140 }}
            />
            <Tooltip title="Remove parameter">
              <IconButton size="small" onClick={() => removeParam(idx)} sx={{ p: 0.25, mt: 0.5 }}>
                <CloseIcon sx={{ fontSize: 16, color: 'text.secondary' }} />
              </IconButton>
            </Tooltip>
          </Box>
        ))}

        <Button
          size="small"
          startIcon={<AddIcon sx={{ fontSize: 16 }} />}
          onClick={addParam}
          sx={{ mt: 0.5, textTransform: 'none', fontSize: '0.8rem' }}
        >
          Add parameter
        </Button>

        {error && (
          <Typography variant="body2" sx={{ color: 'error.main', mt: 1.5, fontSize: '0.8rem' }}>
            {error}
          </Typography>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={saving}>
          Cancel
        </Button>
        <Button variant="contained" onClick={handleSave} disabled={saving}>
          {saving ? 'Saving…' : 'Save'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

LessonSummaryModal.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  memoryId: PropTypes.string,
  initialTitle: PropTypes.string,
  initialSteps: PropTypes.arrayOf(
    PropTypes.oneOfType([
      PropTypes.string,
      PropTypes.shape({ order: PropTypes.number, text: PropTypes.string }),
    ])
  ),
  initialParameters: PropTypes.arrayOf(
    PropTypes.shape({
      name: PropTypes.string,
      description: PropTypes.string,
      example: PropTypes.string,
    })
  ),
  onSaved: PropTypes.func,
};
