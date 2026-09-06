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
import StoryboardProgress from './StoryboardProgress';
import RenderProgress from './RenderProgress';

const ProductionDetail = ({
  production,
  loading,
  error,
  approving,
  onCastingConfirmed,
  onRegenerateShot,
  onApproveStoryboard,
  onRetry,
  onDelete,
  retrying,
  deleting,
}) => {
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
          Select a production to view details.
        </Typography>
      </Box>
    );
  }

  const isFailed = Boolean(production.status?.startsWith('failed'));
  const errText =
    production.error_blob?.error
    || production.error_blob?.message
    || (typeof production.error_blob === 'string' ? production.error_blob : null)
    || (isFailed ? `Pipeline failed at stage: ${production.current_stage}` : null);

  return (
    <Box sx={{ p: 3, height: '100%', overflowY: 'auto' }}>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      <Box sx={{ mb: 3, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 2 }}>
        <Box>
          <Typography variant="h4" gutterBottom>{production.name}</Typography>
          <Typography variant="body2" color="text.secondary">
            ID: {production.id}
            {production.created_at ? ` | Created: ${new Date(production.created_at).toLocaleString()}` : ''}
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
            {deleting ? 'Deleting…' : 'Delete'}
          </Button>
        </Stack>
      </Box>

      {isFailed && (
        <Alert
          severity="error"
          sx={{ mb: 2 }}
          action={
            <Button color="inherit" size="small" disabled={retrying} onClick={() => onRetry?.(production.id)}>
              {retrying ? 'Retrying…' : 'Retry stage'}
            </Button>
          }
        >
          <Typography variant="subtitle2" gutterBottom>Production failed</Typography>
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
              {retrying ? '…' : 'Re-dispatch'}
            </Button>
          }
        >
          Screenwriting in progress (or stuck). Use Re-dispatch if nothing happens after a few minutes.
        </Alert>
      )}

      <Paper sx={{ p: 2, mb: 3 }}>
        <Typography variant="h6" gutterBottom>Pipeline Progress</Typography>
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
        <>
          {production.current_stage === 'storyboard_gen' && !isFailed && (
            <StoryboardProgress productionId={production.id} />
          )}
          {production.current_stage === 'rendering' && !isFailed && (
            <RenderProgress productionId={production.id} />
          )}
          <Paper sx={{ p: 2, mb: 3 }}>
            <StoryboardGrid
              currentStage={production.current_stage}
              shots={production.shots || []}
              onRegenerate={onRegenerateShot}
              onApproveAll={onApproveStoryboard}
              isApproving={approving}
            />
          </Paper>
        </>
      )}

      <Paper sx={{ p: 2 }}>
        <Typography variant="h6" gutterBottom>Script</Typography>
        <Divider sx={{ my: 1 }} />
        <Typography variant="body1" sx={{ whiteSpace: 'pre-wrap', fontFamily: 'monospace', fontSize: '0.9rem' }}>
          {production.script_text}
        </Typography>
      </Paper>
    </Box>
  );
};

export default ProductionDetail;
