// frontend/src/components/filmcrew/CastTrainingNotes.jsx
// What the Cast member page says about the last training run and about the
// post-train smoke render's identity score. Both read the subject as the Cast
// API serializes it (backend/api/cast_library_api.py _serialize).

import React from 'react';
import { Alert, Typography } from '@mui/material';
import { useUiTranslation } from '../../i18n';

// training_note codes the API sends for a 'trained' member, mapped to strings.
const TRAINING_NOTE_KEYS = {
  lora_saved_worker_stopped: 'cast.trainingData.noteLoraSavedWorkerStopped',
};

export const TrainingOutcomeNotice = ({ subject }) => {
  const { t } = useUiTranslation();
  if (!subject) return null;

  if (subject.training_status === 'failed' && subject.training_error) {
    return (
      <Alert severity="error" sx={{ mt: 1.5 }}>
        <Typography variant="body2" sx={{ fontWeight: 600 }}>
          {t('cast.trainingData.failed')}
        </Typography>
        <Typography
          variant="body2"
          component="pre"
          sx={{
            whiteSpace: 'pre-wrap',
            fontFamily: 'monospace',
            fontSize: '0.8rem',
            mt: 0.5,
            mb: 0.5,
          }}
        >
          {subject.display?.training_error ?? subject.training_error}
        </Typography>
        <Typography variant="caption">
          {t('cast.trainingData.failedHelp')}
        </Typography>
      </Alert>
    );
  }

  const noteKey = subject.training_status === 'trained' && TRAINING_NOTE_KEYS[subject.training_note];
  if (noteKey) {
    return (
      <Alert severity="info" sx={{ mt: 1.5 }}>
        {t(noteKey)}
      </Alert>
    );
  }
  return null;
};

export const SmokeIdentityNotice = ({ subject }) => {
  const { t } = useUiTranslation();
  const smoke = subject?.smoke_identity;
  if (!smoke?.ok) return null;

  // The record describes another LoRA (an earlier version, or one replaced by an
  // import) when the current default's own test render never finished.
  if (smoke.current === false) {
    if (!subject.lora_path) return null;
    return (
      <Typography variant="caption" sx={{ color: 'text.secondary', display: 'block', mb: 2 }}>
        {t('cast.generate.identityNotMeasuredReason', {
          reason: t('cast.generate.identityNoRenderForLora'),
        })}
      </Typography>
    );
  }

  if (smoke.status === 'measured') {
    const score = Number(smoke.score).toFixed(2);
    return (
      <Alert
        severity={Number(smoke.score) >= Number(smoke.threshold ?? 0.75) ? 'success' : 'warning'}
        sx={{ mb: 2 }}
      >
        {smoke.family
          ? t('cast.generate.smokeScoreFamily', { score, method: smoke.method, family: smoke.family })
          : t('cast.generate.smokeScore', { score, method: smoke.method })}
      </Alert>
    );
  }

  // No badge and no number: the score is withheld until the method is proven on
  // labelled pairs, and a colour would be a verdict.
  return (
    <Typography variant="caption" sx={{ color: 'text.secondary', display: 'block', mb: 2 }}>
      {smoke.reason
        ? t('cast.generate.identityNotMeasuredReason', { reason: smoke.reason })
        : t('cast.generate.identityNotMeasured')}
    </Typography>
  );
};
