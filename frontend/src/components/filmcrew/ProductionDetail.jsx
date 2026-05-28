import React from 'react';
import {
  Box,
  Typography,
  Paper,
  Divider,
  CircularProgress,
  Alert
} from '@mui/material';
import StageProgress from './StageProgress';
import CastingPanel from './CastingPanel';
import StoryboardGrid from './StoryboardGrid';

const ProductionDetail = ({
  production,
  loading,
  error,
  approving,
  onCastingConfirmed,
  onRegenerateShot,
  onApproveStoryboard
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

  return (
    <Box sx={{ p: 3, height: '100%', overflowY: 'auto' }}>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      <Box sx={{ mb: 3 }}>
        <Typography variant="h4" gutterBottom>{production.name}</Typography>
        <Typography variant="body2" color="text.secondary">
          ID: {production.id}
          {production.created_at ? ` | Created: ${new Date(production.created_at).toLocaleString()}` : ''}
        </Typography>
      </Box>

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
