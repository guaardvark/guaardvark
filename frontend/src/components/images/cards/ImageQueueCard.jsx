// The Image Gen Queue card: what is rendering. The batch queue and the open
// batch with its progress.

import React, { memo } from "react";
import {
  Box,
  Card,
  CardContent,
  Typography,
  Button,
  Chip,
  LinearProgress,
} from '@mui/material';
import {
  Download,
  Settings as SettingsIcon,
} from '@mui/icons-material';
import BatchQueuePanel from '../BatchQueuePanel';
import { useUiTranslation } from '../../../i18n';
import { statusSx } from "../imageGenPage";

function ImageQueueCard({
  activeBatch,
  downloadResults,
  fetchQueue,
  handleAdjustRetry,
  handleClearCompletedQueue,
  queue,
  requestCancel,
  setActiveBatch,
}) {
  const { t } = useUiTranslation();
  return (
        <Box sx={statusSx}>
          {queue.length === 0 && !activeBatch && (
            <Typography variant="body2" color="text.secondary" sx={{ p: 1 }}>
              {t("layout.board.queueEmpty")}
            </Typography>
          )}
    {/* Batch Queue panel — live view of stacked jobs (mirrors Video Gen) */}
    <BatchQueuePanel
      queue={queue}
      onClearCompleted={handleClearCompletedQueue}
      onCancel={async (batchId) => {
        const ok = await requestCancel(batchId);
        fetchQueue();
        if (ok && activeBatch?.batch_id === batchId) {
          setActiveBatch((prev) => (prev ? { ...prev, status: 'cancelled' } : prev));
        }
      }}
    />

    {/* Active Batch Progress */}
    {
      activeBatch && (
        <Card sx={{
          mb: 3,
          boxShadow: 2,
          borderRadius: 2
        }}>
          <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
            <Typography
              variant="h6"
              sx={{
                fontWeight: 600,
                mb: 2,
                color: 'text.primary'
              }}
            >
              {t('imagesBatch.progress.title')}
            </Typography>

            <Box sx={{ mb: 2 }}>
              <Typography variant="body2" color="text.secondary">
                {t('imagesBatch.progress.batchId', { id: activeBatch.batch_id })}
              </Typography>
              <Chip
                label={activeBatch.status.toUpperCase()}
                color={activeBatch.status === 'running' ? 'primary' :
                  activeBatch.status === 'completed' ? 'success' : 'default'}
                size="small"
                sx={{ mt: 1 }}
              />
            </Box>

            <Box sx={{ mb: 2 }}>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 1 }}>
                <Typography variant="body2">
                  {t('imagesBatch.progress.count', { done: activeBatch.completed_images || 0, total: activeBatch.total_images || 0 })}
                </Typography>
                <Typography variant="body2">
                  {activeBatch.progress_percentage || 0}%
                </Typography>
              </Box>
              <LinearProgress
                variant="determinate"
                value={activeBatch.progress_percentage || 0}
              />
            </Box>

            <Box sx={{ display: 'flex', gap: 1.5, mt: 1, flexWrap: 'wrap' }}>
              {activeBatch.status === 'completed' && (
                <Button
                  startIcon={<Download />}
                  onClick={() => downloadResults(activeBatch.batch_id)}
                  variant="outlined"
                  size="small"
                >
                  {t('imagesBatch.progress.download')}
                </Button>
              )}
              {['completed', 'error', 'cancelled'].includes(activeBatch.status) && (
                <Button
                  startIcon={<SettingsIcon />}
                  onClick={() => handleAdjustRetry(activeBatch.batch_id)}
                  variant="outlined"
                  size="small"
                >
                  {t('imagesBatch.progress.adjustRetry')}
                </Button>
              )}
            </Box>
          </CardContent>
        </Card>
      )
    }

        </Box>
  );
}

export default memo(ImageQueueCard);
