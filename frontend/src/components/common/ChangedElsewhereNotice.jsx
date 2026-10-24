// Shown when fields the person is editing were saved with other values from
// somewhere else (another tab, an agent, a background job). The page never
// picks a side on its own: Reload takes the saved version, Keep mine keeps
// the edit so the next save writes it over that change. Pair with
// hooks/useServerSyncedForm, whose `conflicts` and `resolve` this takes.
import React from 'react';
import PropTypes from 'prop-types';
import { Alert, Box, Button } from '@mui/material';
import { useUiTranslation } from '../../i18n';

const ChangedElsewhereNotice = ({ conflicts, labels = {}, onReload, onKeep, sx }) => {
  const { t } = useUiTranslation();
  const keys = Object.keys(conflicts || {});
  if (!keys.length) return null;
  const names = [...new Set(keys.map((k) => labels[k] || k))].join(', ');
  return (
    <Alert
      severity="warning"
      sx={sx}
      action={(
        <Box sx={{ display: 'flex', gap: 0.5 }}>
          <Button color="inherit" size="small" onClick={onReload}>{t('common.reload')}</Button>
          <Button color="inherit" size="small" onClick={onKeep}>{t('layoutCommon.changedElsewhere.keepMine')}</Button>
        </Box>
      )}
    >
      {t('layoutCommon.changedElsewhere.message', { names })}
    </Alert>
  );
};

ChangedElsewhereNotice.propTypes = {
  conflicts: PropTypes.object,
  labels: PropTypes.object,
  onReload: PropTypes.func.isRequired,
  onKeep: PropTypes.func.isRequired,
  sx: PropTypes.object,
};

export default ChangedElsewhereNotice;
