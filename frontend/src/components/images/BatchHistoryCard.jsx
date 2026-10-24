import React from 'react';
import { Box, Button, Chip, IconButton, Tooltip, Typography } from '@mui/material';
import {
  Close as CloseIcon,
  Download,
  Image as ImageIcon,
  Settings as SettingsIcon,
  Visibility,
} from '@mui/icons-material';
import EntityContextMenu from '../common/EntityContextMenu';
import useContextMenu from '../../hooks/useContextMenu';
import { useUiTranslation } from '../../i18n';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api';

/**
 * One card in the "Recent Batches" grid.
 *
 * Memoised on purpose. This page re-renders on queue polls, socket progress events and
 * every control-panel keystroke; without memoisation each of those rebuilt every card in
 * the history — roughly fifteen emotion-styled MUI nodes and a Tooltip popper apiece —
 * which is what made the page crawl once batches piled up. All handler props are
 * useCallback-stable in the parent, so the shallow compare actually holds.
 */
const BatchHistoryCard = React.memo(function BatchHistoryCard({
  batch,
  dateStr,
  onOpen,
  onLoad,
  onHide,
  onDownload,
  onAdjustRetry,
}) {
  const { t } = useUiTranslation();
  const imgCount = batch.completed_images ?? batch.total_images ?? 0;
  const rawName = batch.display_name || t('images.batchCard.fallbackName', { id: batch.batch_id.slice(0, 8) });
  const label = rawName.length > 36 ? rawName.slice(0, 35).trimEnd() + '…' : rawName;
  const isCompleted = batch.status === 'completed';
  const menu = useContextMenu();

  return (
    <>
    <Box
      onClick={() => (isCompleted ? onOpen(batch, 0) : onLoad(batch.batch_id))}
      onContextMenu={(e) => menu.open(e, batch)}
      sx={{
        // Backend thumbnails are 256px max — don't upscale past that.
        maxWidth: 256,
        mx: 'auto',
        cursor: 'pointer',
        position: 'relative',
        borderRadius: 2,
        p: 1,
        bgcolor: 'background.paper',
        border: '1px solid',
        borderColor: 'divider',
        transition: 'transform 0.2s, box-shadow 0.2s',
        '&:hover': {
          transform: 'translateY(-2px)',
          boxShadow: 4,
          '& .batch-overlay': { opacity: 1 },
          '& .batch-delete': { opacity: 1 },
        },
      }}
    >
      {/* Stacked thumbnail preview */}
      <Box sx={{ position: 'relative', aspectRatio: '4/3', mb: 1 }}>
        {imgCount > 2 && (
          <Box sx={{
            position: 'absolute', top: -6, left: 6, right: -6, bottom: 6,
            bgcolor: 'grey.800', borderRadius: 1.5, border: 1, borderColor: 'grey.700',
          }} />
        )}
        {imgCount > 1 && (
          <Box sx={{
            position: 'absolute', top: -3, left: 3, right: -3, bottom: 3,
            bgcolor: 'grey.850', borderRadius: 1.5, border: 1, borderColor: 'grey.700',
          }} />
        )}
        <Box sx={{
          position: 'relative', width: '100%', height: '100%',
          bgcolor: 'grey.900', borderRadius: 1.5, overflow: 'hidden',
          border: 1, borderColor: 'grey.700',
        }}>
          {/*
            The preview is a plain <img>, not MUI's Box: Box treats width/height as
            style-system props, so they never reach the DOM as the intrinsic-size
            attributes the browser needs to reserve the box. Dropping Box here also
            spares one emotion-styled component per card.

            Without lazy loading, every batch in the history fetches and decodes its
            preview on mount, which is what makes the page crawl once a few dozen
            batches accumulate.
          */}
          {imgCount > 0 ? (
            <img
              src={`${API_BASE}/batch-image/preview/${batch.batch_id}`}
              alt={t('images.batchCard.previewAlt')}
              loading="lazy"
              decoding="async"
              width={256}
              height={192}
              style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
              onError={(e) => { e.target.style.display = 'none'; }}
            />
          ) : (
            <Box sx={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <ImageIcon sx={{ fontSize: 36, color: 'grey.600' }} />
            </Box>
          )}
          <Box className="batch-overlay" sx={{
            position: 'absolute', inset: 0,
            bgcolor: 'rgba(0,0,0,0.4)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            opacity: 0, transition: 'opacity 0.2s',
          }}>
            <Visibility sx={{ fontSize: 32, color: 'white' }} />
          </Box>
          <Chip
            label={t('images.shared.imageCount', { count: imgCount })}
            size="small"
            sx={{
              position: 'absolute', top: 6, right: 6,
              height: 20, fontSize: '0.65rem',
              bgcolor: 'rgba(0,0,0,0.7)', color: 'white',
              '& .MuiChip-label': { px: 0.75 },
            }}
          />
          {!isCompleted && (
            <Chip
              label={batch.status}
              size="small"
              color={batch.status === 'error' ? 'error' : batch.status === 'cancelled' ? 'warning' : 'info'}
              sx={{
                position: 'absolute', bottom: 6, left: 6,
                height: 18, fontSize: '0.6rem',
              }}
            />
          )}
          <Tooltip title={t('images.batchCard.clearTooltip')}>
            <IconButton
              size="small"
              className="batch-delete"
              onClick={(e) => { e.stopPropagation(); onHide(batch.batch_id); }}
              sx={{
                position: 'absolute', top: 4, left: 4,
                width: 24, height: 24,
                bgcolor: 'rgba(0,0,0,0.6)', color: 'white',
                opacity: 0, transition: 'opacity 0.2s',
                '&:hover': { bgcolor: 'error.main' },
              }}
            >
              <CloseIcon sx={{ fontSize: 16 }} />
            </IconButton>
          </Tooltip>
        </Box>
      </Box>

      <Box sx={{ pt: 0.5 }}>
        <Typography variant="subtitle2" noWrap title={rawName} sx={{ fontWeight: 600 }}>
          {label}
        </Typography>
        {dateStr && (
          <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.7rem', display: 'block', mb: 0.5 }}>
            {dateStr}
          </Typography>
        )}

        <Box sx={{ display: 'flex', gap: 0.5, mt: 1, flexWrap: 'wrap' }}>
          {isCompleted && (
            <>
              <Button
                size="small"
                variant="outlined"
                startIcon={<Visibility sx={{ fontSize: 14 }} />}
                onClick={(e) => { e.stopPropagation(); onOpen(batch, 0); }}
                sx={{ textTransform: 'none', borderRadius: 1, fontSize: '0.75rem', py: 0.2, px: 1 }}
              >
                {t('images.batchCard.browse')}
              </Button>
              <Button
                size="small"
                variant="outlined"
                startIcon={<Download sx={{ fontSize: 14 }} />}
                onClick={(e) => { e.stopPropagation(); onDownload(batch.batch_id); }}
                sx={{ textTransform: 'none', borderRadius: 1, fontSize: '0.75rem', py: 0.2, px: 1 }}
              >
                {t('images.shared.download')}
              </Button>
            </>
          )}
          <Button
            size="small"
            variant="outlined"
            startIcon={<SettingsIcon sx={{ fontSize: 14 }} />}
            onClick={(e) => { e.stopPropagation(); onAdjustRetry(batch.batch_id); }}
            sx={{ textTransform: 'none', borderRadius: 1, fontSize: '0.75rem', py: 0.2, px: 1 }}
          >
            {t('images.batchCard.adjustRetry')}
          </Button>
        </Box>
      </Box>
    </Box>
    <EntityContextMenu
      anchorPosition={menu.anchorPosition}
      onClose={menu.close}
      actions={
        menu.payload
          ? [
              isCompleted && { label: t('images.batchCard.browse'), onClick: () => onOpen(batch, 0) },
              isCompleted && { label: t('images.shared.download'), onClick: () => onDownload(batch.batch_id) },
              !isCompleted && { label: t('images.batchCard.loadBatch'), onClick: () => onLoad(batch.batch_id) },
              { label: t('images.batchCard.adjustRetry'), onClick: () => onAdjustRetry(batch.batch_id) },
              {
                label: t('images.batchCard.clearFromList'),
                onClick: () => onHide(batch.batch_id),
                color: 'error.main',
                dividerBefore: true,
              },
            ]
          : []
      }
    />
    </>
  );
});

export default BatchHistoryCard;
