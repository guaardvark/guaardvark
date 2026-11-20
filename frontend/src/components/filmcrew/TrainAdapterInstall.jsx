import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, LinearProgress, Typography } from '@mui/material';
import { getTrainAdapterStatus, installTrainAdapter } from '../../api/productionService';
import { useUiTranslation } from '../../i18n';

// The train base's training adapter: says whether it is on this machine and
// offers the one download that fetches it. Renders nothing for a base that
// declares no adapter.
export default function TrainAdapterInstall({ baseModelId }) {
  const { t } = useUiTranslation();
  const [status, setStatus] = useState(null);
  const timer = useRef(null);

  const load = useCallback(async () => {
    try {
      setStatus(await getTrainAdapterStatus(baseModelId));
    } catch {
      setStatus(null);
    }
  }, [baseModelId]);

  useEffect(() => { load(); }, [load]);

  const running = status?.install?.state === 'running';
  useEffect(() => {
    if (!running) return undefined;
    timer.current = setInterval(load, 1500);
    return () => clearInterval(timer.current);
  }, [running, load]);

  if (!status?.needed) return null;
  const name = status.name;

  if (status.installed) {
    return (
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
        {t('cast.trainingData.adapterInstalled', { name })}
      </Typography>
    );
  }

  const start = async () => {
    try {
      setStatus(await installTrainAdapter(baseModelId));
    } catch {
      load();
    }
  };

  return (
    <Alert
      severity="warning"
      sx={{ mb: 1.5, maxWidth: 720 }}
      action={(
        <Button color="inherit" size="small" onClick={start} disabled={running}>
          {t('cast.trainingData.adapterInstall')}
        </Button>
      )}
    >
      {running
        ? t('cast.trainingData.adapterInstalling', { name, progress: status.install.progress ?? 0 })
        : t('cast.trainingData.adapterMissing', {
          name, size: status.size_mb, license: status.license, source: status.source,
        })}
      {running && <LinearProgress variant="determinate" value={status.install.progress ?? 0} sx={{ mt: 1 }} />}
      {status.install?.state === 'failed' && (
        <Typography variant="caption" color="error" sx={{ display: 'block', mt: 0.5 }}>
          {t('cast.trainingData.adapterFailed', { error: status.install.error })}
        </Typography>
      )}
    </Alert>
  );
}
