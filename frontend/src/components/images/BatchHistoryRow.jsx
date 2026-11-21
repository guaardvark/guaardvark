import React from 'react';
import { Box, Chip, IconButton, Tooltip, Typography } from '@mui/material';
import { Close as CloseIcon, Image as ImageIcon, Replay as ReplayIcon } from '@mui/icons-material';
import EntityContextMenu from '../common/EntityContextMenu';
import useContextMenu from '../../hooks/useContextMenu';
import { useUiTranslation } from '../../i18n';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api';

/**
 * One batch as a row of the "Recent Batches" list: thumbnail, name, how many
 * images, retry, hide. The same batch as BatchHistoryCard, for a list that
 * shows many at once. Memoised for the same reason the card is.
 */
const BatchHistoryRow = React.memo(function BatchHistoryRow({
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
  const name = batch.display_name || t('images.batchCard.fallbackName', { id: batch.batch_id.slice(0, 8) });
  const isCompleted = batch.status === 'completed';
  const menu = useContextMenu();
  const open = () => (isCompleted ? onOpen(batch, 0) : onLoad(batch.batch_id));

  return (
    <>
      <Box
        role="button"
        tabIndex={0}
        onClick={open}
        onKeyDown={(e) => {
          if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) {
            e.preventDefault();
            open();
          }
        }}
        onContextMenu={(e) => menu.open(e, batch)}
        sx={{
          display: 'flex',
          alignItems: 'center',
          gap: 1.25,
          px: 1,
          py: 0.5,
          borderBottom: '1px solid',
          borderColor: 'divider',
          cursor: 'pointer',
          '&:hover': { bgcolor: 'action.hover' },
        }}
      >
        <Box
          sx={{
            width: 56, height: 42, flexShrink: 0, borderRadius: 1, overflow: 'hidden',
            bgcolor: 'grey.900', display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}
        >
          {imgCount > 0 ? (
            <img
              src={`${API_BASE}/batch-image/preview/${batch.batch_id}`}
              alt={t('images.batchCard.previewAlt')}
              loading="lazy"
              decoding="async"
              width={56}
              height={42}
              style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
              onError={(e) => { e.target.style.display = 'none'; }}
            />
          ) : (
            <ImageIcon sx={{ fontSize: 20, color: 'grey.600' }} />
          )}
        </Box>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography variant="body2" noWrap title={name} sx={{ fontWeight: 600 }}>
            {name}
          </Typography>
          {dateStr && (
            <Typography variant="caption" noWrap sx={{ color: "text.secondary", display: 'block', fontSize: '0.7rem' }}>
              {dateStr}
            </Typography>
          )}
        </Box>
        {!isCompleted && (
          <Chip
            label={batch.status}
            size="small"
            color={batch.status === 'error' ? 'error' : batch.status === 'cancelled' ? 'warning' : 'info'}
            sx={{ height: 18, fontSize: '0.6rem', flexShrink: 0 }}
          />
        )}
        <Typography variant="caption" sx={{ color: "text.secondary", flexShrink: 0, whiteSpace: 'nowrap' }}>
          {t('images.shared.imageCount', { count: imgCount })}
        </Typography>
        <Tooltip title={t('images.batchCard.adjustRetry')}>
          <IconButton
            size="small"
            aria-label={t('images.batchCard.adjustRetry')}
            onClick={(e) => { e.stopPropagation(); onAdjustRetry(batch.batch_id); }}
          >
            <ReplayIcon sx={{ fontSize: 18 }} />
          </IconButton>
        </Tooltip>
        <Tooltip title={t('images.batchCard.clearTooltip')}>
          <IconButton
            size="small"
            aria-label={t('images.batchCard.clearTooltip')}
            onClick={(e) => { e.stopPropagation(); onHide(batch.batch_id); }}
          >
            <CloseIcon sx={{ fontSize: 18 }} />
          </IconButton>
        </Tooltip>
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

export default BatchHistoryRow;
