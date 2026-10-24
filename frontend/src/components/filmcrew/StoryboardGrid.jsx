import React, { useState } from 'react';
import { formatUiError } from "../../utils/uiError";
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
  Tooltip,
  Alert
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import { useUiTranslation, translate } from '../../i18n';

const escapeXml = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

// Inline SVG so an empty shot never fetches from a third-party CDN.
const noStoryboardPlaceholder = () =>
  "data:image/svg+xml;utf8," +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180" viewBox="0 0 320 180">' +
    '<rect width="320" height="180" fill="#1e1e1e"/>' +
    '<text x="160" y="96" text-anchor="middle" font-family="sans-serif" font-size="16" fill="#888">' +
    escapeXml(translate('filmCrew.storyboard.noStoryboard')) +
    '</text>' +
    '</svg>'
  );

// The curator's advice on a frame, as one plain line for the card.
const curatorLine = (advice) => {
  if (advice.verdict === 'flag') {
    return advice.reason
      ? translate('filmCrew.storyboard.curatorFlaggedWhy', { reason: advice.reason })
      : translate('filmCrew.storyboard.curatorFlagged');
  }
  return advice.reason
    ? translate('filmCrew.storyboard.curatorApprovedWhy', { reason: advice.reason })
    : translate('filmCrew.storyboard.curatorApproved');
};

// Flagged by the curator and not approved by a person: rendering it needs a yes.
const isCuratorFlagged = (shot) =>
  shot.curator_advice?.verdict === 'flag' && !(shot.approved && shot.approved_by === 'person');

const StoryboardGrid = ({ currentStage, shots, onRegenerate, onApproveAll, isApproving }) => {
  const { t } = useUiTranslation();
  const [regenShot, setRegenShot] = useState(null);
  const [promptOverride, setPromptOverride] = useState('');
  const [loading, setLoading] = useState(false);
  const [regenError, setRegenError] = useState(null);
  const [confirmingFlagged, setConfirmingFlagged] = useState(false);

  const flaggedShots = shots.filter(isCuratorFlagged);

  const handleApproveClick = () => {
    if (flaggedShots.length > 0) {
      setConfirmingFlagged(true);
      return;
    }
    onApproveAll();
  };

  const handleRenderAnyway = () => {
    setConfirmingFlagged(false);
    onApproveAll({ confirmFlagged: true });
  };

  const handleRegenClick = (shot) => {
    setRegenShot(shot);
    setPromptOverride('');
    setRegenError(null);
  };

  const handleCloseRegen = () => {
    setRegenShot(null);
    setRegenError(null);
  };

  const handleConfirmRegen = async () => {
    setLoading(true);
    setRegenError(null);
    try {
      await onRegenerate(regenShot.id, { prompt_override: promptOverride });
      setRegenShot(null);
    } catch (err) {
      // Keep dialog open with the error visible — closing silently and not
      // regenerating leaves the user wondering why nothing happened.
      setRegenError(formatUiError(err.response?.data?.error) || t('filmCrew.storyboard.regenFailed'));
    } finally {
      setLoading(false);
    }
  };

  const canApprove = currentStage === 'awaiting_approval';
  const canRegenerate = currentStage === 'awaiting_approval';

  return (
    <Box sx={{ mt: 3 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
        <Typography variant="h6">{t('filmCrew.storyboard.title')}</Typography>
        {canApprove && (
          <Button
            variant="contained"
            color="success"
            startIcon={<CheckCircleIcon />}
            onClick={handleApproveClick}
            disabled={isApproving}
          >
            {isApproving ? t('filmCrew.storyboard.approving') : t('filmCrew.storyboard.approveAll')}
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
                  image={shot.storyboard_image_url || shot.storyboard_image_path || noStoryboardPlaceholder()}
                  alt={t('filmCrew.storyboard.shotAlt', { scene: shot.scene_number, shot: shot.shot_number })}
                  sx={{ backgroundColor: '#000' }}
                />
                <Box sx={{ position: 'absolute', top: 8, right: 8, display: 'flex', gap: 1 }}>
                  {shot.approved && (
                    <Chip
                      label={shot.approved_by === 'curator' ? t('filmCrew.storyboard.preTicked') : t('filmCrew.storyboard.approved')}
                      color="success"
                      variant={shot.approved_by === 'curator' ? 'outlined' : 'filled'}
                      size="small"
                      sx={{ height: 24, bgcolor: shot.approved_by === 'curator' ? 'background.paper' : undefined }}
                    />
                  )}
                  <Tooltip title={canRegenerate ? t('filmCrew.storyboard.regenerateShot') : t('filmCrew.storyboard.regenerateUnavailable')}>
                    <span>
                      <IconButton
                        size="small"
                        aria-label={t('filmCrew.storyboard.regenerateShot')}
                        disabled={!canRegenerate}
                        sx={{ bgcolor: 'rgba(255,255,255,0.8)', '&:hover': { bgcolor: 'white' } }}
                        onClick={() => handleRegenClick(shot)}
                      >
                        <RefreshIcon fontSize="small" />
                      </IconButton>
                    </span>
                  </Tooltip>
                </Box>
              </Box>
              <CardContent sx={{ flexGrow: 1 }}>
                <Typography variant="caption" color="text.secondary" gutterBottom>
                  {t('filmCrew.storyboard.sceneShot', { scene: shot.scene_number, shot: shot.shot_number })}
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
                {shot.curator_advice?.verdict && (
                  <Typography
                    variant="caption"
                    display="block"
                    color={shot.curator_advice.verdict === 'flag' ? 'warning.main' : 'success.main'}
                    sx={{ mt: 1 }}
                  >
                    {curatorLine(shot.curator_advice)}
                  </Typography>
                )}
              </CardContent>
            </Card>
          </Grid>
        ))}
      </Grid>

      <Dialog open={!!regenShot} onClose={handleCloseRegen}>
        <DialogTitle>{t('filmCrew.storyboard.regenTitle', { scene: regenShot?.scene_number, shot: regenShot?.shot_number })}</DialogTitle>
        <DialogContent>
          <Typography variant="body2" sx={{ mb: 2 }}>
            {t('filmCrew.storyboard.regenHint')}
          </Typography>
          {regenError && (
            <Alert severity="error" sx={{ mb: 2 }} onClose={() => setRegenError(null)}>
              {regenError}
            </Alert>
          )}
          <TextField
            fullWidth
            multiline
            rows={4}
            label={t('filmCrew.storyboard.promptOverride')}
            value={promptOverride}
            onChange={(e) => setPromptOverride(e.target.value)}
            placeholder={regenShot?.description}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={handleCloseRegen}>{t('filmCrew.storyboard.cancel')}</Button>
          <Button
            onClick={handleConfirmRegen}
            variant="contained"
            disabled={loading}
          >
            {loading ? t('filmCrew.storyboard.rolling') : t('filmCrew.storyboard.regenerate')}
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog open={confirmingFlagged} onClose={() => setConfirmingFlagged(false)}>
        <DialogTitle>{t('filmCrew.storyboard.renderAnywayTitle')}</DialogTitle>
        <DialogContent>
          <Typography variant="body2" sx={{ mb: 1 }}>
            {t('filmCrew.storyboard.flaggedCount', { count: flaggedShots.length })}
          </Typography>
          {flaggedShots.map((shot) => (
            <Typography key={shot.id} variant="body2" sx={{ ml: 2 }}>
              {t('filmCrew.storyboard.sceneShot', { scene: shot.scene_number, shot: shot.shot_number })}
              {shot.curator_advice?.reason ? `: ${shot.curator_advice.reason}` : ''}
            </Typography>
          ))}
          <Typography variant="body2" sx={{ mt: 2 }}>
            {t('filmCrew.storyboard.renderAnywayHint')}
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmingFlagged(false)}>{t('filmCrew.storyboard.cancel')}</Button>
          <Button onClick={handleRenderAnyway} variant="contained" color="warning">
            {t('filmCrew.storyboard.renderAnyway')}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default StoryboardGrid;
