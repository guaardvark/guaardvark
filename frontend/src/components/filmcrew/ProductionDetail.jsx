import React from 'react';
import {
  Box,
  Typography,
  Paper,
  Divider,
  CircularProgress,
  Alert,
  Button,
  Stack,
} from '@mui/material';
import StageProgress from './StageProgress';
import CastingPanel from './CastingPanel';
import StoryboardGrid from './StoryboardGrid';
import VoiceFallbackNotice from './VoiceFallbackNotice';
import { useUiTranslation, uiLocale } from '../../i18n';

const ProductionDetail = ({
  production,
  loading,
  error,
  notice,
  onDismissNotice,
  approving,
  onCastingConfirmed,
  onRegenerateShot,
  onApproveStoryboard,
  onRetry,
  onDelete,
  retrying,
  deleting,
}) => {
  const { t } = useUiTranslation();
  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%' }}>
        <CircularProgress />
      </Box>
    );
  }

  if (error && !production) {
    return (
      <Box sx={{ p: 3 }}>
        <Alert severity="error">{error}</Alert>
      </Box>
    );
  }

  if (!production) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%' }}>
        <Typography variant="body1" color="text.secondary">
          {t('filmCrew.detail.selectProduction')}
        </Typography>
      </Box>
    );
  }

  const isFailed = Boolean(production.status?.startsWith('failed'));
  const errText =
    production.error_blob?.error
    || production.error_blob?.message
    || (typeof production.error_blob === 'string' ? production.error_blob : null)
    || (isFailed ? t('filmCrew.detail.failedAtStage', { stage: production.current_stage }) : null);

  return (
    <Box sx={{ p: 3, height: '100%', overflowY: 'auto' }}>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {notice && (
        <Alert severity="warning" sx={{ mb: 2 }} onClose={onDismissNotice}>
          {notice}
        </Alert>
      )}
      <Box sx={{ mb: 3, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 2 }}>
        <Box>
          <Typography variant="h4" gutterBottom>{production.name}</Typography>
          <Typography variant="body2" color="text.secondary">
            {t('filmCrew.detail.id', { id: production.id })}
            {production.created_at ? ` | ${t('filmCrew.detail.created', { date: new Date(production.created_at).toLocaleString(uiLocale()) })}` : ''}
          </Typography>
        </Box>
        <Stack direction="row" spacing={1}>
          <Button
            size="small"
            variant="outlined"
            color="error"
            disabled={deleting}
            onClick={() => onDelete?.(production.id)}
          >
            {deleting ? t('filmCrew.detail.deleting') : t('filmCrew.detail.delete')}
          </Button>
        </Stack>
      </Box>

      {isFailed && (
        <Alert
          severity="error"
          sx={{ mb: 2 }}
          action={
            <Button color="inherit" size="small" disabled={retrying} onClick={() => onRetry?.(production.id)}>
              {retrying ? t('filmCrew.detail.retrying') : t('filmCrew.detail.retry')}
            </Button>
          }
        >
          <Typography variant="subtitle2" gutterBottom>{t('filmCrew.detail.failed')}</Typography>
          <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>
            {errText}
          </Typography>
        </Alert>
      )}

      {!isFailed && production.current_stage === 'screenwriting' && production.status === 'screenwriting' && (
        <Alert
          severity="info"
          sx={{ mb: 2 }}
          action={
            <Button color="inherit" size="small" disabled={retrying} onClick={() => onRetry?.(production.id)}>
              {retrying ? '…' : t('filmCrew.detail.redispatch')}
            </Button>
          }
        >
          {t('filmCrew.detail.screenwritingHint')}
        </Alert>
      )}

      {production.final_video && (
        <Paper sx={{ p: 2, mb: 3 }}>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
            <Typography variant="h6">{t('filmCrew.detail.finishedFilm')}</Typography>
            <Button
              size="small"
              variant="outlined"
              href={production.final_video.url}
              download={production.final_video.filename}
            >
              {t('filmCrew.detail.download')}
            </Button>
          </Box>
          <Box
            component="video"
            key={production.final_video.document_id}
            src={production.final_video.url}
            controls
            preload="metadata"
            aria-label={t('filmCrew.detail.filmAria', { name: production.name })}
            sx={{ width: '100%', maxHeight: 480, borderRadius: 1, bgcolor: 'common.black' }}
          />
        </Paper>
      )}

      <VoiceFallbackNotice shots={production.shots} />

      <Paper sx={{ p: 2, mb: 3 }}>
        <Typography variant="h6" gutterBottom>{t('filmCrew.detail.pipeline')}</Typography>
        <StageProgress
          currentStage={production.current_stage}
          status={production.status}
          errorBlob={production.error_blob}
        />
      </Paper>

      {production.current_stage === 'casting' && (
        <Paper sx={{ p: 2, mb: 3 }}>
          <CastingPanel
            productionId={production.id}
            onCastingConfirmed={onCastingConfirmed}
          />
        </Paper>
      )}

      {['storyboard_gen', 'awaiting_approval', 'rendering', 'complete'].includes(production.current_stage) && (
        <Paper sx={{ p: 2, mb: 3 }}>
          <StoryboardGrid
            currentStage={production.current_stage}
            shots={production.shots || []}
            onRegenerate={onRegenerateShot}
            onApproveAll={onApproveStoryboard}
            isApproving={approving}
          />
        </Paper>
      )}

      <Paper sx={{ p: 2 }}>
        <Typography variant="h6" gutterBottom>{t('filmCrew.detail.script')}</Typography>
        <Divider sx={{ my: 1 }} />
        <Typography variant="body1" sx={{ whiteSpace: 'pre-wrap', fontFamily: 'monospace', fontSize: '0.9rem' }}>
          {production.script_text}
        </Typography>
      </Paper>
    </Box>
  );
};

export default ProductionDetail;
