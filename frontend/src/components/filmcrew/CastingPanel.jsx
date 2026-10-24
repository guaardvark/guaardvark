import React, { useState, useEffect } from 'react';
import {
  Box,
  Typography,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Radio,
  RadioGroup,
  FormControlLabel,
  FormControl,
  Autocomplete,
  TextField,
  Button,
  Chip,
  Alert,
  LinearProgress,
} from '@mui/material';
import { listCastLibrary, listProductionSubjects, castSubject, confirmCasting } from '../../api/productionService';
import { dispatchWarning } from '../../api/taskQueue';
import { useUnifiedProgress } from '../../contexts/UnifiedProgressContext';
import DragDropImageUpload from './DragDropImageUpload';
import { kindLabel, trainingStatusLabel } from './CastLibraryView';
import { useUiTranslation } from '../../i18n';

const CastingPanel = ({ productionId, onCastingConfirmed }) => {
  const { t } = useUiTranslation();
  const [castingData, setCastingData] = useState({});
  const [castLibrary, setCastLibrary] = useState([]);
  const [subjectsToCast, setSubjectsToCast] = useState([]);
  const [loading, setLoading] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState(null);
  const [trainingJobs, setTrainingJobs] = useState({});
  const { activeProcesses } = useUnifiedProgress();

  useEffect(() => {
    let cancelled = false;
    const fetchData = async () => {
      if (!productionId) return;
      setLoading(true);
      try {
        const [library, prodSubjects] = await Promise.all([
          listCastLibrary(),
          listProductionSubjects(productionId),
        ]);
        if (cancelled) return;
        setCastLibrary(library.subjects || []);
        setSubjectsToCast(prodSubjects.subjects || []);
      } catch (err) {
        if (!cancelled) setError(t('filmCrew.casting.loadFailed'));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    fetchData();
    return () => { cancelled = true; };
  }, [productionId]);

  // Mirror CastMemberPage: surface live LoRA training progress for subjects
  // dispatched from this panel (production_api now returns unified job_id).
  useEffect(() => {
    const procs = Array.from(activeProcesses.values());
    const next = {};
    for (const subj of subjectsToCast) {
      const match = procs.find((p) => {
        const ad = p.additional_data || p.metadata || {};
        const isThisSubject = String(ad.subject_id || '') === String(subj.id);
        const isTrain = ad.operation === 'train_lora' || ad.kind === 'cast_training';
        return isThisSubject && isTrain;
      });
      if (match) {
        const st = (match.status || '').toLowerCase();
        if (!['complete', 'end', 'error', 'cancelled', 'failed'].includes(st)) {
          next[subj.id] = {
            id: match.job_id || match.id,
            progress: match.progress,
            message: match.message || match.status,
          };
        }
      }
    }
    setTrainingJobs(next);
  }, [activeProcesses, subjectsToCast]);

  const handleActionChange = (subjectId, action) => {
    setCastingData(prev => ({
      ...prev,
      [subjectId]: { ...prev[subjectId], action }
    }));
  };

  const handleLoraChange = (subjectId, lora) => {
    setCastingData(prev => ({
      ...prev,
      [subjectId]: { ...prev[subjectId], existing_lora_id: lora?.id }
    }));
  };

  const handleRefsUploaded = (subjectId, paths) => {
    // The drag-drop component already POSTed the files to /upload-refs and
    // returns the subject's authoritative ref_image_paths. We mirror that
    // into the cast form so the eventual /cast/<subject_id> call has them.
    setCastingData(prev => ({
      ...prev,
      [subjectId]: { ...prev[subjectId], ref_image_paths: paths }
    }));
  };

  // Does the in-form selection for a subject describe a complete cast action?
  const hasValidFormAction = (subj) => {
    const data = castingData[subj.id];
    return Boolean(
      data?.action &&
      (data.action !== 'use_existing_lora' || data.existing_lora_id) &&
      (data.action !== 'train_from_uploads' || (data.ref_image_paths && data.ref_image_paths.length > 0))
    );
  };

  // Is this subject an identity-locked cast member that needs a trained LoRA?
  // The backend sends the resolved `cast_required`; fall back to kind for any
  // older payload that predates it. Props/environments (cast_required=false)
  // are generated inline and never need casting.
  const isCastRequired = (subj) =>
    subj.cast_required ?? (subj.kind === 'character');

  // Mirror the backend's casting/confirm gate (production_api.py): a subject is
  // ready if it isn't an identity-locked cast member, OR it already has a
  // trained/in-progress LoRA on the server, OR the user just picked a valid
  // cast action for it. A cast-required subject whose prior training FAILED is
  // NOT server-ready and must be re-cast here — the case the old form-only
  // check missed, which enabled the button and produced a 400.
  const subjectReady = (subj) => {
    if (!isCastRequired(subj)) return true;
    const serverReady = Boolean(
      subj.lora_path || ['training', 'trained'].includes(subj.training_status)
    );
    return serverReady || hasValidFormAction(subj);
  };

  const notReadySubjects = () => subjectsToCast.filter((s) => !subjectReady(s));

  const isAllConfirmed = () => {
    if (subjectsToCast.length === 0) return false;
    return subjectsToCast.every(subjectReady);
  };

  const handleConfirm = async () => {
    setConfirming(true);
    setError(null);
    try {
      // Only POST a cast action for subjects the user actually re-cast in this
      // form. Already-server-ready subjects (trained / mid-training) have no
      // form action and would 400 the /cast endpoint with an undefined action.
      for (const subj of subjectsToCast) {
        if (hasValidFormAction(subj)) {
          const res = await castSubject(productionId, subj.id, castingData[subj.id]);
          const notStarted = dispatchWarning(res);
          if (notStarted) {
            // Its LoRA training was not queued, so casting cannot be confirmed yet.
            setError(notStarted);
            return;
          }
          if (res?.training_job_id) {
            setTrainingJobs((prev) => ({
              ...prev,
              [subj.id]: { id: res.training_job_id, message: t('filmCrew.casting.trainingStarting') },
            }));
          }
        }
      }
      const confirmed = await confirmCasting(productionId);
      await onCastingConfirmed(dispatchWarning(confirmed));
    } catch (err) {
      // Surface the backend's real reason instead of a generic failure. The
      // confirm endpoint returns {error, incomplete_subjects:[{name,...}]}.
      const data = err?.response?.data;
      let msg = data?.error || err?.message || t('filmCrew.casting.confirmFailed');
      const incomplete = data?.incomplete_subjects;
      if (Array.isArray(incomplete) && incomplete.length) {
        const names = incomplete
          .map((s) => `${s.name}${s.training_status ? ` (${s.training_status})` : ''}`)
          .join(', ');
        msg = `${msg}: ${names}`;
      }
      setError(msg);
    } finally {
      setConfirming(false);
    }
  };

  const STATUS_COLOR = {
    trained: 'success',
    training: 'info',
    failed: 'error',
  };

  return (
    <Box sx={{ mt: 3 }}>
      <Typography variant="h6" gutterBottom>{t('filmCrew.casting.title')}</Typography>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {!loading && subjectsToCast.length > 0 && notReadySubjects().length > 0 && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          {t('filmCrew.casting.pickAction', { names: notReadySubjects().map((s) => s.name).join(', ') })}
          {notReadySubjects().some((s) => s.training_status === 'failed') &&
            ` ${t('filmCrew.casting.recastFailed')}`}
        </Alert>
      )}
      {!loading && subjectsToCast.length === 0 && (
        <Alert severity="info" sx={{ mb: 2 }}>
          {t('filmCrew.casting.noSubjects')}
        </Alert>
      )}
      <TableContainer component={Paper}>
        <Table>
          <TableHead>
            <TableRow>
              <TableCell>{t('filmCrew.casting.subject')}</TableCell>
              <TableCell>{t('filmCrew.casting.kind')}</TableCell>
              <TableCell>{t('filmCrew.casting.action')}</TableCell>
              <TableCell>{t('filmCrew.casting.details')}</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {subjectsToCast.map((subj) => (
              <TableRow key={subj.id}>
                <TableCell>
                  {subj.name}
                  {subj.training_status && (
                    <Chip
                      label={trainingStatusLabel(subj.training_status)}
                      size="small"
                      color={STATUS_COLOR[subj.training_status] || 'default'}
                      variant="outlined"
                      sx={{ ml: 1 }}
                    />
                  )}
                </TableCell>
                <TableCell><Chip label={kindLabel(subj.kind)} size="small" /></TableCell>
                <TableCell>
                  {isCastRequired(subj) ? (
                    <FormControl component="fieldset">
                      <RadioGroup
                        row
                        value={castingData[subj.id]?.action || ''}
                        onChange={(e) => handleActionChange(subj.id, e.target.value)}
                      >
                        <FormControlLabel value="use_existing_lora" control={<Radio />} label={t('filmCrew.casting.existingLora')} />
                        <FormControlLabel value="train_from_uploads" control={<Radio />} label={t('filmCrew.casting.uploadPhotos')} />
                        <FormControlLabel value="train_from_generated" control={<Radio />} label={t('filmCrew.casting.aiGenerated')} />
                        <FormControlLabel value="as_described" control={<Radio />} label={t('filmCrew.casting.asDescribed')} />
                      </RadioGroup>
                    </FormControl>
                  ) : (
                    <Typography variant="body2" color="text.secondary">
                      {t('filmCrew.casting.inline')}
                    </Typography>
                  )}
                </TableCell>
                <TableCell sx={{ minWidth: 250 }}>
                  {isCastRequired(subj) && castingData[subj.id]?.action === 'use_existing_lora' && (
                    <Autocomplete
                      options={castLibrary.filter(l => l.lora_path)}
                      getOptionLabel={(option) => option.name}
                      renderInput={(params) => <TextField {...params} label={t('filmCrew.casting.selectLora')} size="small" />}
                      onChange={(_, newValue) => handleLoraChange(subj.id, newValue)}
                    />
                  )}
                  {castingData[subj.id]?.action === 'as_described' && (
                    <Typography variant="body2" color="text.secondary">
                      {t('filmCrew.casting.asDescribedHint')}
                    </Typography>
                  )}
                  {castingData[subj.id]?.action === 'train_from_uploads' && (
                    <DragDropImageUpload
                      subjectId={subj.id}
                      existingPaths={castingData[subj.id]?.ref_image_paths || []}
                      onUploaded={(paths) => handleRefsUploaded(subj.id, paths)}
                      helperText={t('filmCrew.casting.uploadHint')}
                    />
                  )}
                  {(subj.training_status === 'training' || trainingJobs[subj.id]) && (
                    <Box sx={{ mt: 1, maxWidth: 280 }}>
                      <LinearProgress
                        variant={trainingJobs[subj.id]?.progress != null ? 'determinate' : 'indeterminate'}
                        value={trainingJobs[subj.id]?.progress || 0}
                      />
                      <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 0.5 }}>
                        {trainingJobs[subj.id]?.message || t('filmCrew.casting.trainingInProgress')}
                      </Typography>
                    </Box>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
      <Box sx={{ mt: 3, display: 'flex', justifyContent: 'flex-end' }}>
        <Button 
          variant="contained" 
          color="primary" 
          disabled={confirming || !isAllConfirmed()}
          onClick={handleConfirm}
        >
          {confirming ? t('filmCrew.casting.confirming') : t('filmCrew.casting.confirm')}
        </Button>
      </Box>
    </Box>
  );
};

export default CastingPanel;
