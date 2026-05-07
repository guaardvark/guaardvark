import React, { useState } from 'react';
import {
  Grid,
  Card,
  CardMedia,
  CardContent,
  Typography,
  Box,
  Button,
  Chip,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  IconButton,
  Tooltip
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';

const StoryboardGrid = ({ productionId, currentStage, shots, onRegenerate, onApproveAll }) => {
  const [regenShot, setRegenShot] = useState(null);
  const [promptOverride, setPromptOverride] = useState('');
  const [loading, setLoading] = useState(false);

  const handleRegenClick = (shot) => {
    setRegenShot(shot);
    setPromptOverride('');
  };

  const handleConfirmRegen = async () => {
    setLoading(true);
    try {
      await onRegenerate(regenShot.id, { prompt_override: promptOverride });
      setRegenShot(null);
    } catch (err) {
      console.error('Regen failed', err);
    } finally {
      setLoading(false);
    }
  };

  const canApprove = currentStage === 'awaiting_approval';

  return (
    <Box sx={{ mt: 3 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
        <Typography variant="h6">Storyboard</Typography>
        {canApprove && (
          <Button 
            variant="contained" 
            color="success" 
            startIcon={<CheckCircleIcon />}
            onClick={onApproveAll}
          >
            Approve & Render
          </Button>
        )}
      </Box>
      <Grid container spacing={2}>
        {shots.map((shot) => (
          <Grid item xs={12} sm={6} md={4} key={shot.id}>
            <Card sx={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
              <Box sx={{ position: 'relative' }}>
                <CardMedia
                  component="img"
                  height="180"
                  image={shot.storyboard_image_path || 'https://via.placeholder.com/320x180?text=No+Storyboard'}
                  alt={`Shot ${shot.scene_number}.${shot.shot_number}`}
                  sx={{ backgroundColor: '#000' }}
                />
                <Box sx={{ position: 'absolute', top: 8, right: 8, display: 'flex', gap: 1 }}>
                  {shot.approved && (
                    <Chip 
                      label="Approved" 
                      color="success" 
                      size="small" 
                      sx={{ height: 24 }}
                    />
                  )}
                  <Tooltip title="Regenerate this shot">
                    <IconButton 
                      size="small" 
                      sx={{ bgcolor: 'rgba(255,255,255,0.8)', '&:hover': { bgcolor: 'white' } }}
                      onClick={() => handleRegenClick(shot)}
                    >
                      <RefreshIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                </Box>
              </Box>
              <CardContent sx={{ flexGrow: 1 }}>
                <Typography variant="caption" color="text.secondary" gutterBottom>
                  Scene {shot.scene_number} / Shot {shot.shot_number}
                </Typography>
                <Typography variant="body2" sx={{ 
                  display: '-webkit-box',
                  WebkitLineClamp: 3,
                  WebkitBoxOrient: 'vertical',
                  overflow: 'hidden',
                  mt: 1
                }}>
                  {shot.description}
                </Typography>
              </CardContent>
            </Card>
          </Grid>
        ))}
      </Grid>

      <Dialog open={!!regenShot} onClose={() => setRegenShot(null)}>
        <DialogTitle>Regenerate Shot {regenShot?.scene_number}.{regenShot?.shot_number}</DialogTitle>
        <DialogContent>
          <Typography variant="body2" sx={{ mb: 2 }}>
            Optionally override the prompt for this shot. If left blank, the original description will be used.
          </Typography>
          <TextField
            fullWidth
            multiline
            rows={4}
            label="Prompt Override"
            value={promptOverride}
            onChange={(e) => setPromptOverride(e.target.value)}
            placeholder={regenShot?.description}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRegenShot(null)}>Cancel</Button>
          <Button 
            onClick={handleConfirmRegen} 
            variant="contained" 
            disabled={loading}
          >
            {loading ? 'Rolling...' : 'Regenerate'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default StoryboardGrid;
