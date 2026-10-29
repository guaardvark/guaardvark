// frontend/src/components/images/BatchQueuePanel.jsx
//
// The image batch queue as a card: each batch in line with its place,
// progress, status and a cancel button while it can still be stopped. Image
// and infographic batches share one worker, so both pages list every batch
// and name the infographic ones. Rows come from useImageBatchQueue.

import React from 'react';
import {
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  IconButton,
  LinearProgress,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material';
import { Close as CloseIcon } from '@mui/icons-material';
import { POLLABLE_STATUSES, TERMINAL_BATCH_STATUSES } from '../../hooks/useImageBatchQueue';
import { useUiTranslation } from '../../i18n';

const statusColor = (status) => {
  if (status === 'running') return 'primary';
  if (status === 'completed') return 'success';
  if (status === 'cancelled') return 'warning';
  if (status === 'error') return 'error';
  return 'default';
};

/**
 * @param {Object} props
 * @param {Object[]} props.queue - rows from useImageBatchQueue
 * @param {(batchId: string) => void} props.onCancel
 * @param {() => void} props.onClearCompleted
 */
const BatchQueuePanel = ({ queue, onCancel, onClearCompleted }) => {
  const { t } = useUiTranslation();
  if (!queue.length) return null;

  return (
    <Card sx={{ mb: 3, boxShadow: 2, borderRadius: 2 }}>
      <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
        <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 2 }}>
          <Typography variant="h6" sx={{ fontWeight: 600 }}>
            {t('imagesBatch.queue.title')}
          </Typography>
          <Stack direction="row" spacing={1} alignItems="center">
            {queue.some((q) => TERMINAL_BATCH_STATUSES.has(q.status)) && (
              <Button
                size="small"
                variant="outlined"
                color="inherit"
                onClick={onClearCompleted}
                sx={{ textTransform: 'none', borderRadius: 1 }}
              >
                {t('imagesBatch.queue.clearCompleted')}
              </Button>
            )}
            <Chip
              label={t('imagesBatch.queue.active', { count: queue.filter((q) => POLLABLE_STATUSES.has(q.status)).length })}
              size="small"
              color="primary"
              variant="outlined"
            />
          </Stack>
        </Stack>
        <Stack spacing={1}>
          {queue.map((q, idx) => {
            const total = q.total_images || 0;
            const done = (q.completed_images || 0) + (q.failed_images || 0);
            const pct = total > 0 ? Math.round((done / total) * 100) : 0;
            const cancellable = POLLABLE_STATUSES.has(q.status);
            return (
              <Box
                key={q.batch_id}
                sx={{
                  p: 1.5,
                  border: '1px solid',
                  borderColor: q.is_running ? 'primary.main' : 'divider',
                  borderRadius: 1,
                  bgcolor: q.is_running ? 'action.hover' : 'transparent',
                }}
              >
                <Stack direction="row" alignItems="center" spacing={1.5}>
                  <Chip
                    label={`#${idx + 1}`}
                    size="small"
                    variant="outlined"
                    sx={{ minWidth: 44, fontFamily: 'monospace' }}
                  />
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    <Typography variant="body2" noWrap title={q.display_name || q.batch_id}>
                      {q.display_name || q.batch_id}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {q.failed_images > 0
                        ? t('imagesBatch.queue.progressFailed', { done, total, failed: q.failed_images })
                        : t('imagesBatch.queue.progress', { done, total })}
                    </Typography>
                  </Box>
                  {q.kind === 'infographic' && (
                    <Chip label={t('imagesBatch.queue.infographic')} size="small" variant="outlined" />
                  )}
                  <Chip label={(q.status || '').toUpperCase()} size="small" color={statusColor(q.status)} />
                  {cancellable && (
                    <Tooltip
                      title={q.status === 'running' ? t('imagesBatch.queue.cancelRunning') : t('imagesBatch.queue.remove')}
                      arrow
                    >
                      <IconButton
                        size="small"
                        onClick={() => onCancel(q.batch_id)}
                        aria-label={t('imagesBatch.queue.cancelAria')}
                      >
                        <CloseIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  )}
                </Stack>
                {q.status === 'running' && (
                  <LinearProgress variant="determinate" value={pct} sx={{ mt: 1, height: 4, borderRadius: 2 }} />
                )}
              </Box>
            );
          })}
        </Stack>
      </CardContent>
    </Card>
  );
};

export default BatchQueuePanel;
