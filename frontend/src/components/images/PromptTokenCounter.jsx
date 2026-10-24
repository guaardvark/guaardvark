import React, { useEffect, useState } from 'react';
import { Tooltip, Typography } from '@mui/material';
import { uiLocale, useUiTranslation } from '../../i18n';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api';
const DEBOUNCE_MS = 400;

const fmt = (n) => Number(n).toLocaleString(uiLocale());

/**
 * Tokens the longest of `prompts` uses out of what `model` reads. The backend
 * counts with the model's own tokenizer; models without a cap show nothing.
 */
export default function PromptTokenCounter({ prompts, model, sx }) {
  const { t } = useUiTranslation();
  const longest = (prompts || []).reduce((a, p) => (p.length > a.length ? p : a), '');
  const many = new Set(prompts || []).size > 1;
  const [count, setCount] = useState(null);

  useEffect(() => {
    if (!longest.trim()) {
      setCount(null);
      return undefined;
    }
    const ctrl = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const resp = await fetch(`${API_BASE}/batch-image/prompt-tokens`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt: longest, model: model || 'auto' }),
          signal: ctrl.signal,
        });
        const body = await resp.json();
        setCount(resp.ok ? body.data : null);
      } catch (err) {
        if (err.name !== 'AbortError') setCount(null);
      }
    }, DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      ctrl.abort();
    };
  }, [longest, model]);

  if (!count || !count.limit || count.tokens == null) return null;

  const { tokens, limit } = count;
  const over = tokens > limit;
  const near = !over && tokens > limit * 0.9;
  const label = many
    ? t('images.tokenCounter.longest', { tokens: fmt(tokens), limit: fmt(limit) })
    : t('images.tokenCounter.count', { tokens: fmt(tokens), limit: fmt(limit) });
  return (
    <Tooltip title={t('images.tokenCounter.tooltip')}>
      <Typography
        variant="caption"
        data-testid="prompt-token-counter"
        sx={{ display: 'block', color: over ? 'error.main' : near ? 'warning.main' : 'text.secondary', ...sx }}
      >
        {over
          ? t('images.tokenCounter.over', { label, limit: fmt(limit) })
          : label}
      </Typography>
    </Tooltip>
  );
}
