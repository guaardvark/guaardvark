// frontend/src/components/media/MediaDetailsModal.jsx
//
// The Media page's details view for one generated item: the picture, clip or
// song with the settings it was made with (backend/services/media_recipe.py),
// and the ways to make it again:
//   Regenerate          the same settings with a new seed, queued at once
//   Same seed           the same settings and seed (shown when the seed is known)
//   Adjust & Retry      the page that made it, opened with this item's settings
//
// `target` is either a Library document or a clip of a video batch:
//   {kind: 'document', documentId, name, url}
//   {kind: 'clip', batchId, clips: [{itemId, url, name}], index}

import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material';
import {
  Autorenew as RegenerateIcon,
  ChevronLeft,
  ChevronRight,
  Close as CloseIcon,
  ContentCopy as CopyIcon,
  Download as DownloadIcon,
  OpenInFull as FullSizeIcon,
  Tune as AdjustIcon,
} from '@mui/icons-material';
import { getClipRecipe, getDocumentRecipe, regenerateMedia } from '../../api/mediaRecipeService';
import { useUiTranslation } from '../../i18n';

const saveFile = (url, name) => {
  const a = document.createElement('a');
  a.href = url;
  a.download = name || '';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
};

const copyText = (text) => {
  try {
    navigator.clipboard?.writeText(String(text));
  } catch (e) {
    // Clipboard refused (insecure origin): the text is still selectable.
  }
};

const MediaDetailsModal = ({ open, target, onClose, onPrev, onNext, hasPrev = false, hasNext = false, onFullSize }) => {
  const { t } = useUiTranslation();
  const navigate = useNavigate();
  const [clipIndex, setClipIndex] = useState(0);
  const [recipe, setRecipe] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(null); // 'new' | 'same' while a regenerate is being queued
  const [queued, setQueued] = useState(null);

  const isClip = target?.kind === 'clip';
  const clip = isClip ? target.clips?.[clipIndex] : null;

  useEffect(() => {
    if (isClip) setClipIndex(target.index || 0);
  }, [isClip, target]);

  useEffect(() => {
    if (!open || !target) return undefined;
    let alive = true;
    setRecipe(null);
    setError('');
    setQueued(null);
    setLoading(true);
    const load = isClip
      ? (clip ? getClipRecipe(target.batchId, clip.itemId) : Promise.resolve(null))
      : getDocumentRecipe(target.documentId);
    load
      .then((r) => { if (alive) setRecipe(r); })
      .catch((e) => { if (alive) setError(e.message); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [open, target, isClip, clip]);

  // Arrow keys step through the folder the item was opened from.
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      if (e.target?.closest?.('input, textarea')) return;
      if (e.key === 'ArrowLeft' && hasPrev) onPrev?.();
      if (e.key === 'ArrowRight' && hasNext) onNext?.();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, hasPrev, hasNext, onPrev, onNext]);

  const previewUrl = isClip ? clip?.url : (target?.url || recipe?.url);
  const name = isClip ? (clip?.name || recipe?.title) : (target?.name || recipe?.title);
  const media = recipe?.media || (isClip ? 'video' : 'image');

  const handleRegenerate = useCallback(async (sameSeed) => {
    setBusy(sameSeed ? 'same' : 'new');
    setError('');
    setQueued(null);
    try {
      const body = isClip
        ? { batch_id: target.batchId, item_id: clip.itemId, same_seed: sameSeed }
        : { document_id: target.documentId, same_seed: sameSeed };
      setQueued(await regenerateMedia(body));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  }, [isClip, target, clip]);

  const handleAdjust = () => {
    if (!recipe?.adjust) return;
    const query = new URLSearchParams(
      Object.entries(recipe.adjust.query || {}).filter(([, v]) => v != null).map(([k, v]) => [k, String(v)]),
    );
    onClose?.();
    navigate(`${recipe.adjust.path}?${query}`);
  };

  const formatValue = (key, value) => {
    if (Array.isArray(value)) return value.join(key === 'callouts' ? ' · ' : ', ');
    if (typeof value === 'boolean') return value ? t('images.details.yes') : t('images.details.no');
    if (key === 'duration_s' && typeof value === 'number') return t('images.details.seconds', { value: Math.round(value * 10) / 10 });
    return String(value);
  };

  const regen = recipe?.regenerate || {};
  const reasonText = regen.reason ? t(`images.details.reasons.${regen.reason}`) : '';

  const renderPreview = () => {
    if (!previewUrl) {
      return <Typography variant="body2" color="text.secondary">{t('images.details.noPreview')}</Typography>;
    }
    if (media === 'video') {
      return (
        <Box component="video" key={previewUrl} src={previewUrl} controls autoPlay
          sx={{ width: '100%', maxHeight: '62vh', borderRadius: 1, bgcolor: 'black' }} />
      );
    }
    if (media === 'audio') {
      return <Box component="audio" key={previewUrl} src={previewUrl} controls sx={{ width: '100%' }} />;
    }
    return (
      <Box component="img" src={previewUrl} alt={name || ''}
        onDoubleClick={onFullSize}
        sx={{ maxWidth: '100%', maxHeight: '62vh', objectFit: 'contain', borderRadius: 1, display: 'block', mx: 'auto', cursor: onFullSize ? 'zoom-in' : 'default' }} />
    );
  };

  return (
    <Dialog open={!!open} onClose={onClose} maxWidth="lg" fullWidth aria-labelledby="media-details-title">
      <DialogTitle id="media-details-title" sx={{ display: 'flex', alignItems: 'center', gap: 1, pr: 1 }}>
        {(onPrev || onNext) && (
          <IconButton size="small" onClick={onPrev} disabled={!hasPrev} aria-label={t('images.details.previous')}>
            <ChevronLeft />
          </IconButton>
        )}
        <Typography component="span" variant="h6" noWrap sx={{ flex: 1, minWidth: 0 }} title={name || ''}>
          {name}
        </Typography>
        {recipe?.kind && recipe.kind !== 'file' && (
          <Chip size="small" variant="outlined" label={t(`images.details.kinds.${recipe.kind}`)} />
        )}
        {(onPrev || onNext) && (
          <IconButton size="small" onClick={onNext} disabled={!hasNext} aria-label={t('images.details.next')}>
            <ChevronRight />
          </IconButton>
        )}
        <IconButton onClick={onClose} aria-label={t('images.details.close')}>
          <CloseIcon />
        </IconButton>
      </DialogTitle>
      <DialogContent dividers>
        <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: '3fr 2fr' }, gap: 2, alignItems: 'start' }}>
          <Box>
            {renderPreview()}
            {isClip && target.clips?.length > 1 && (
              <Stack direction="row" spacing={1} sx={{ mt: 1, flexWrap: 'wrap' }} useFlexGap>
                {target.clips.map((c, i) => (
                  <Chip
                    key={c.itemId}
                    size="small"
                    label={t('images.details.clip', { number: i + 1 })}
                    color={i === clipIndex ? 'primary' : 'default'}
                    variant={i === clipIndex ? 'filled' : 'outlined'}
                    onClick={() => setClipIndex(i)}
                  />
                ))}
              </Stack>
            )}
          </Box>

          <Box sx={{ minWidth: 0 }}>
            {loading && <CircularProgress size={24} />}
            {!loading && recipe && (
              <Stack spacing={1.5}>
                {recipe.prompt && (
                  <Box>
                    <Stack direction="row" alignItems="center" justifyContent="space-between">
                      <Typography variant="overline" color="text.secondary">{t('images.details.prompt')}</Typography>
                      <Tooltip title={t('images.details.copy')}>
                        <IconButton size="small" onClick={() => copyText(recipe.prompt)} aria-label={t('images.details.copyPrompt')}>
                          <CopyIcon fontSize="inherit" />
                        </IconButton>
                      </Tooltip>
                    </Stack>
                    <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', maxHeight: 160, overflowY: 'auto', p: 1, borderRadius: 1, bgcolor: 'action.hover' }}>
                      {recipe.prompt}
                    </Typography>
                  </Box>
                )}
                {recipe.rewritten_prompt && recipe.rewritten_prompt !== recipe.prompt && (
                  <Box>
                    <Typography variant="overline" color="text.secondary">{t('images.details.sentPrompt')}</Typography>
                    <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', maxHeight: 120, overflowY: 'auto', p: 1, borderRadius: 1, bgcolor: 'action.hover' }}>
                      {recipe.rewritten_prompt}
                    </Typography>
                  </Box>
                )}
                {recipe.lyrics && (
                  <Box>
                    <Typography variant="overline" color="text.secondary">{t('images.details.lyrics')}</Typography>
                    <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', maxHeight: 160, overflowY: 'auto', p: 1, borderRadius: 1, bgcolor: 'action.hover' }}>
                      {recipe.lyrics}
                    </Typography>
                  </Box>
                )}
                {recipe.rows?.length > 0 ? (
                  <Box component="dl" sx={{ display: 'grid', gridTemplateColumns: 'auto 1fr', columnGap: 2, rowGap: 0.5, m: 0 }}>
                    {recipe.rows.map((row) => (
                      <React.Fragment key={row.key}>
                        <Typography component="dt" variant="body2" color="text.secondary">
                          {t(`images.details.rows.${row.key}`)}
                        </Typography>
                        <Typography component="dd" variant="body2" sx={{ m: 0, wordBreak: 'break-word', display: 'flex', alignItems: 'center', gap: 0.5 }}>
                          {formatValue(row.key, row.value)}
                          {row.key === 'seed' && (
                            <IconButton size="small" onClick={() => copyText(row.value)} aria-label={t('images.details.copySeed')} sx={{ p: 0.25 }}>
                              <CopyIcon sx={{ fontSize: 14 }} />
                            </IconButton>
                          )}
                        </Typography>
                      </React.Fragment>
                    ))}
                  </Box>
                ) : (
                  <Typography variant="body2" color="text.secondary">{t('images.details.noSettings')}</Typography>
                )}
              </Stack>
            )}
            {error && <Alert severity="error" sx={{ mt: 1 }}>{error}</Alert>}
            {queued && (
              <Alert
                severity="success"
                sx={{ mt: 1 }}
                action={(
                  <Button color="inherit" size="small" onClick={() => { onClose?.(); navigate(queued.page); }}>
                    {t('images.details.open')}
                  </Button>
                )}
              >
                {t('images.details.queued', { page: t(`images.details.queues.${queued.queue}`) })}
              </Alert>
            )}
          </Box>
        </Box>
      </DialogContent>
      <DialogActions sx={{ flexWrap: 'wrap', gap: 1, px: 3, py: 1.5 }}>
        {previewUrl && (
          <Button startIcon={<DownloadIcon />} onClick={() => saveFile(previewUrl, name)}>
            {t('images.shared.download')}
          </Button>
        )}
        {onFullSize && media === 'image' && (
          <Button startIcon={<FullSizeIcon />} onClick={onFullSize}>{t('images.details.fullSize')}</Button>
        )}
        <Box sx={{ flex: 1 }} />
        {reasonText && !regen.available && (
          <Typography variant="caption" color="text.secondary">{reasonText}</Typography>
        )}
        <Button startIcon={<AdjustIcon />} onClick={handleAdjust} disabled={!recipe?.adjust}>
          {t('images.batchCard.adjustRetry')}
        </Button>
        {regen.same_seed && (
          <Tooltip title={t('images.details.sameSeedTooltip')}>
            <span>
              <Button onClick={() => handleRegenerate(true)} disabled={!!busy || !regen.available}>
                {busy === 'same' ? <CircularProgress size={16} /> : t('images.details.sameSeed')}
              </Button>
            </span>
          </Tooltip>
        )}
        <Button
          variant="contained"
          startIcon={busy === 'new' ? <CircularProgress size={16} color="inherit" /> : <RegenerateIcon />}
          onClick={() => handleRegenerate(false)}
          disabled={!!busy || !regen.available}
        >
          {t('images.details.regenerate')}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default MediaDetailsModal;
