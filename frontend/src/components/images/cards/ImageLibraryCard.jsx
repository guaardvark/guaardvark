// The Image Gen Results card: what has been made. The open batch's images
// and the recent batches.

import React, { memo } from "react";
import {
  Box,
  Card,
  CardContent,
  Typography,
  Button,
  Grid,
  Alert,
  ImageList,
  ImageListItem,
  ImageListItemBar,
  IconButton,
  Stack,
} from '@mui/material';
import {
  Visibility,
  Refresh as RefreshIcon,
  Image as ImageIcon,
} from '@mui/icons-material';
import BatchHistoryCard from '../BatchHistoryCard';
import { useUiTranslation } from '../../../i18n';
import { API_BASE, HISTORY_PAGE, encodeFilename, statusSx } from "../imageGenPage";

function ImageLibraryCard({
  activeBatch,
  batchHistory,
  downloadResults,
  formatImageDate,
  generatedImages,
  handleAdjustRetry,
  handleClearBatchList,
  hideBatch,
  historyError,
  historyShown,
  imageListCols,
  liveBatchHistory,
  loadBatchById,
  loadBatchHistory,
  openHistoryBatchGallery,
  openImageViewer,
  setHistoryShown,
}) {
  const { t } = useUiTranslation();
  return (
        <Box sx={statusSx}>
    {/* Generated Images Gallery */}
    {
      (generatedImages.length > 0 || (activeBatch && activeBatch.results && activeBatch.results.some(r => r.success && r.image_path))) && (
        <Card sx={{
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
              {t('imagesBatch.gallery.title', { count: generatedImages.length || (activeBatch?.results?.filter(r => r.success && r.image_path).length || 0) })}
            </Typography>

            <ImageList
              cols={imageListCols}
              gap={8}
              sx={{
                '& .MuiImageListItem-root': {
                  borderRadius: 1,
                  overflow: 'hidden'
                }
              }}
            >
              {generatedImages.map((image) => {
                // Prefer embedded batchId (from the result that created this image) to avoid
                // race conditions between setActiveBatch and setGeneratedImages during live polling.
                // Falls back to activeBatch for older data.
                const batchIdForUrl = image.batchId || (activeBatch && activeBatch.batch_id);
                // Always try thumbnail first if we have a filename for it.
                // Otherwise use the main image name (serving logic will fallback if needed).
                let thumbnailUrl = '';
                if (batchIdForUrl) {
                  const thumbName = image.thumbnailFilename || image.imageFilename;
                  if (thumbName) {
                    thumbnailUrl = `${API_BASE}/batch-image/image/${batchIdForUrl}/${encodeFilename(thumbName)}?thumbnail=true`;
                  }
                }

                return (
                  <ImageListItem key={image.id}>
                    <img
                      src={thumbnailUrl}
                      alt={image.prompt || t('imagesBatch.gallery.generatedImage')}
                      loading="lazy"
                      decoding="async"
                      style={{ cursor: 'pointer' }}
                      onClick={() => openImageViewer(image)}
                      onError={(e) => {
                        console.error('Failed to load image thumbnail:', image.id, thumbnailUrl);
                        const batchIdForUrl = image.batchId || (activeBatch && activeBatch.batch_id);
                        // Robust fallback: always try the full original image (no ?thumbnail) before hiding.
                        // This ensures we show *something* even if dedicated thumbnail is missing or 404s.
                        if (image.imageFilename && batchIdForUrl && !e.target.dataset.fallbackAttempted) {
                          e.target.src = `${API_BASE}/batch-image/image/${batchIdForUrl}/${encodeFilename(image.imageFilename)}`;
                          e.target.dataset.fallbackAttempted = 'true';
                        } else if (!e.target.dataset.hidden) {
                          // Only hide as last resort
                          e.target.style.display = 'none';
                          e.target.dataset.hidden = 'true';
                        }
                      }}
                      role="button"
                      tabIndex={0}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault();
                          openImageViewer(image);
                        }
                      }}
                    />
                    <ImageListItemBar
                      title={image.prompt ? image.prompt.substring(0, 30) + '...' : t('imagesBatch.gallery.noPrompt')}
                      subtitle={[
                        image.metadata?.seed_used != null ? t('imagesBatch.gallery.seed', { seed: image.metadata.seed_used }) : null,
                        image.rewrittenPrompt ? t('imagesBatch.gallery.rewritten') : null,
                      ].filter(Boolean).join(' · ') || undefined}
                      actionIcon={
                        <IconButton
                          sx={{ color: 'rgba(255, 255, 255, 0.54)' }}
                          onClick={() => openImageViewer(image)}
                          aria-label={t('imagesBatch.gallery.viewFull', { name: image.prompt || t('imagesBatch.gallery.generatedImage') })}
                        >
                          <Visibility />
                        </IconButton>
                      }
                    />
                  </ImageListItem>
                );
              })}
            </ImageList>
          </CardContent>
        </Card>
      )
    }
    {/* Batch History — Stacked Thumbnail Gallery */}
    <Card sx={{
      mt: 3,
      boxShadow: 2,
      borderRadius: 2
    }}>
      <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
        <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
          <Typography variant="h6" sx={{ fontWeight: 600 }}>
            {t('imagesBatch.history.title')}
          </Typography>
          <Stack direction="row" spacing={1} alignItems="center">
            {batchHistory.length > 0 && (
              <Button
                size="small"
                variant="outlined"
                color="inherit"
                onClick={handleClearBatchList}
                sx={{ textTransform: 'none', borderRadius: 1 }}
              >
                {t('imagesBatch.history.clear')}
              </Button>
            )}
            <IconButton size="small" onClick={loadBatchHistory} title={t('imagesBatch.history.refresh')}>
              <RefreshIcon />
            </IconButton>
          </Stack>
        </Stack>

        <Box sx={{ maxHeight: 520, overflowY: 'auto', pr: 0.5 }}>
          <Grid container spacing={2}>
            {liveBatchHistory.slice(0, historyShown).map((batch) => (
              <Grid item xs={6} sm={4} md={3} key={batch.batch_id}>
                <BatchHistoryCard
                  batch={batch}
                  dateStr={formatImageDate(batch.created_at || batch.start_time || batch.end_time)}
                  onOpen={openHistoryBatchGallery}
                  onLoad={loadBatchById}
                  onHide={hideBatch}
                  onDownload={downloadResults}
                  onAdjustRetry={handleAdjustRetry}
                />
              </Grid>
            ))}
          </Grid>
          {liveBatchHistory.length > historyShown && (
            <Box sx={{ textAlign: 'center', mt: 1.5 }}>
              <Button size="small" onClick={() => setHistoryShown((n) => n + HISTORY_PAGE)}>
                {t('imagesBatch.history.showMore', { count: Math.min(HISTORY_PAGE, liveBatchHistory.length - historyShown) })}
              </Button>
            </Box>
          )}

          {historyError && (
            <Alert
              severity="error"
              sx={{ mb: 1 }}
              action={<Button color="inherit" size="small" onClick={loadBatchHistory}>{t('imagesBatch.history.retry')}</Button>}
            >
              {t('imagesBatch.history.loadFailed', { reason: historyError })}
            </Alert>
          )}
          {batchHistory.length === 0 && !historyError && (
            <Box sx={{ textAlign: 'center', py: 4 }}>
              <ImageIcon sx={{ fontSize: 48, color: 'text.disabled', mb: 1 }} />
              <Typography variant="body2" color="text.secondary">
                {t('imagesBatch.history.empty')}
              </Typography>
            </Box>
          )}
        </Box>
      </CardContent>
    </Card>
        </Box>
  );
}

export default memo(ImageLibraryCard);
