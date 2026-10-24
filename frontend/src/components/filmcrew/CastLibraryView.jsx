import React, { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Box,
  Typography,
  Card,
  CardContent,
  CardActions,
  Button,
  Chip,
  IconButton,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  MenuItem,
  CircularProgress,
  Alert
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import AddIcon from '@mui/icons-material/Add';
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome';
import { listCastLibrary, createCastSubject, deleteCastSubject } from '../../api/productionService';
import DragDropImageUpload from './DragDropImageUpload';
import EntityContextMenu from '../common/EntityContextMenu';
import useContextMenu from '../../hooks/useContextMenu';
import { formatUiError } from '../../utils/uiError';
import { useUiTranslation, translate } from '../../i18n';

// The server's reason from an axios error, else the error's own message.
const reason = (err) => formatUiError(err?.response?.data) || formatUiError(err) || translate('filmCrew.castLibrary.requestFailed');

const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api';

// Thumbnail with graceful fallback to the subject's initials when no ref image
// is on disk (preview endpoint 404s).
export const SubjectThumb = ({ subject }) => {
  const [failed, setFailed] = useState(false);
  const initials = (subject.name || '?').split(/\s+/).map(w => w[0]).join('').slice(0, 2).toUpperCase();
  return (
    <Box sx={{ position: 'relative', width: '100%', height: 160, bgcolor: 'action.hover',
               display: 'flex', alignItems: 'center', justifyContent: 'center', overflow: 'hidden' }}>
      <Typography variant="h4" color="text.disabled">{initials}</Typography>
      {!failed && (
        <Box component="img" src={`${API_BASE}/cast-library/subjects/${subject.id}/preview`}
             onError={() => setFailed(true)} alt={subject.name}
             sx={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover' }} />
      )}
    </Box>
  );
};

const KINDS = ['character', 'environment', 'prop'];

/** A subject kind as shown; the value itself is what the backend stores. */
export const kindLabel = (kind) => ({
  character: translate('filmCrew.kinds.character'),
  environment: translate('filmCrew.kinds.environment'),
  prop: translate('filmCrew.kinds.prop'),
})[kind] || kind;

/** A LoRA training status as shown; unknown statuses show as they came. */
export const trainingStatusLabel = (status) => ({
  untrained: translate('filmCrew.trainingStatus.untrained'),
  training: translate('filmCrew.trainingStatus.training'),
  trained: translate('filmCrew.trainingStatus.trained'),
  failed: translate('filmCrew.trainingStatus.failed'),
})[status] || status;

// `onOpenSubject` lets a host page (the Cast & LoRA Studio) intercept a card
// click. When omitted (e.g. the Film Crew tab), clicking opens the studio detail
// route directly — so cast management always lands in the dedicated studio, never
// the old dead /images link.
const CastLibraryView = ({ onOpenSubject } = {}) => {
  const { t } = useUiTranslation();
  const [subjects, setSubjects] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: '', kind: 'character', description: '', trigger_word: '' });
  const [submitting, setSubmitting] = useState(false);
  // Why the list could not be loaded; shown instead of the empty-library text.
  const [loadError, setLoadError] = useState(null);
  const uploaderRef = useRef(null);
  const navigate = useNavigate();
  const cardMenu = useContextMenu();

  const openDetail = onOpenSubject || ((subject) => navigate(`/cast/${subject.id}`));

  useEffect(() => {
    loadLibrary();
  }, []);

  const loadLibrary = async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await listCastLibrary();
      setSubjects(data.subjects || []);
    } catch (err) {
      setLoadError(t('filmCrew.castLibrary.loadFailed', { reason: reason(err) }));
    } finally {
      setLoading(false);
    }
  };

  const handleCreate = async () => {
    setSubmitting(true);
    try {
      // Two-phase: create the row first, then flush any staged drag-drop
      // images straight into /upload-refs against the new id.
      const subj = await createCastSubject(form);
      if (uploaderRef.current?.hasStagedFiles()) {
        await uploaderRef.current.flushTo(subj.id);
      }
      setOpen(false);
      setForm({ name: '', kind: 'character', description: '', trigger_word: '' });
      loadLibrary();
    } catch (err) {
      setError(t('filmCrew.castLibrary.createFailed', { reason: reason(err) }));
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (id) => {
    if (window.confirm(t('filmCrew.castLibrary.deleteConfirm'))) {
      try {
        await deleteCastSubject(id);
        loadLibrary();
      } catch (err) {
        setError(t('filmCrew.castLibrary.deleteFailed', { reason: reason(err) }));
      }
    }
  };

  return (
    <Box sx={{ p: 3 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 3 }}>
        <Typography variant="h5">{t('filmCrew.castLibrary.title')}</Typography>
        <Button 
          variant="contained" 
          startIcon={<AddIcon />}
          onClick={() => setOpen(true)}
        >
          {t('filmCrew.castLibrary.newSubject')}
        </Button>
      </Box>

      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}

      {loading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', p: 5 }}>
          <CircularProgress />
        </Box>
      ) : loadError ? (
        <Alert
          severity="error"
          action={<Button color="inherit" size="small" onClick={loadLibrary}>{t('filmCrew.castLibrary.retry')}</Button>}
        >
          {loadError}
        </Alert>
      ) : subjects.length === 0 ? (
        <Typography color="text.secondary" sx={{ textAlign: 'center', p: 5 }}>
          {t('filmCrew.castLibrary.empty')}
        </Typography>
      ) : (
        <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 2 }}>
          {subjects.map((s) => (
            <Card key={s.id} variant="outlined"
                  onClick={() => openDetail(s)}
                  onContextMenu={(e) => cardMenu.open(e, s)}
                  sx={{ display: 'flex', flexDirection: 'column', cursor: 'pointer',
                        '&:hover': { borderColor: 'primary.main' } }}>
              <SubjectThumb subject={s} />
              <CardContent sx={{ flexGrow: 1, pb: 1 }}>
                <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 1 }}>
                  <Typography variant="subtitle1" noWrap sx={{ fontWeight: 'bold' }}>{s.name}</Typography>
                  <IconButton size="small"
                              onClick={(e) => { e.stopPropagation(); handleDelete(s.id); }}
                              color="inherit" aria-label={t('filmCrew.castLibrary.removeAria')}>
                    <CloseIcon fontSize="small" />
                  </IconButton>
                </Box>
                <Box sx={{ display: 'flex', gap: 0.5, mt: 0.5, flexWrap: 'wrap' }}>
                  <Chip label={kindLabel(s.kind)} size="small" variant="outlined" color={s.kind === 'character' ? 'primary' : 'secondary'} />
                  <Chip label={trainingStatusLabel(s.training_status)} size="small" color={s.training_status === 'trained' ? 'success' : (s.training_status === 'training' ? 'warning' : 'default')} />
                </Box>
                {s.trigger_word && (
                  <Typography variant="caption" sx={{ display: 'block', mt: 0.5, fontFamily: 'monospace' }} color="text.secondary">
                    {s.trigger_word}
                  </Typography>
                )}
              </CardContent>
              <CardActions sx={{ pt: 0 }}>
                <Button
                  size="small"
                  startIcon={<AutoAwesomeIcon />}
                  onClick={(e) => { e.stopPropagation(); openDetail(s); }}
                >
                  {t('filmCrew.castLibrary.openStudio')}
                </Button>
              </CardActions>
            </Card>
          ))}
        </Box>
      )}

      <EntityContextMenu
        anchorPosition={cardMenu.anchorPosition}
        onClose={cardMenu.close}
        actions={
          cardMenu.payload
            ? [
                { label: t('filmCrew.castLibrary.openStudio'), onClick: () => openDetail(cardMenu.payload) },
                {
                  label: t('filmCrew.castLibrary.remove'),
                  onClick: () => handleDelete(cardMenu.payload.id),
                  color: 'error.main',
                  dividerBefore: true,
                },
              ]
            : []
        }
      />

      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>{t('filmCrew.castLibrary.addTitle')}</DialogTitle>
        <DialogContent>
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, mt: 1 }}>
            <TextField 
              label={t('filmCrew.castLibrary.name')} 
              fullWidth 
              value={form.name} 
              onChange={e => setForm({...form, name: e.target.value})}
            />
            {form.kind === 'character' && (
              <TextField
                label={t('filmCrew.castLibrary.triggerWord')}
                fullWidth
                value={form.trigger_word}
                onChange={e => setForm({...form, trigger_word: e.target.value})}
                helperText={t('filmCrew.castLibrary.triggerWordHelp')}
              />
            )}
            <TextField
              select
              label={t('filmCrew.castLibrary.kind')}
              value={form.kind}
              onChange={e => setForm({...form, kind: e.target.value})}
              fullWidth
            >
              {KINDS.map((option) => (
                <MenuItem key={option} value={option}>
                  {kindLabel(option)}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              label={t('filmCrew.castLibrary.description')}
              fullWidth
              multiline
              rows={2}
              value={form.description}
              onChange={e => setForm({...form, description: e.target.value})}
            />
            <Box>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
                {t('filmCrew.castLibrary.referenceImages')}
              </Typography>
              <DragDropImageUpload
                ref={uploaderRef}
                helperText={t('filmCrew.castLibrary.uploadAfterSave')}
              />
            </Box>
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => { setOpen(false); setForm({ name: '', kind: 'character', description: '', trigger_word: '' }); }}>
            {t('filmCrew.castLibrary.cancel')}
          </Button>
          <Button onClick={handleCreate} variant="contained" disabled={submitting || !form.name}>
            {submitting ? t('filmCrew.castLibrary.saving') : t('filmCrew.castLibrary.save')}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default CastLibraryView;
