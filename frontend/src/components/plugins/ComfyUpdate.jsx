// frontend/src/components/plugins/ComfyUpdate.jsx
/**
 * The Update action on the ComfyUI plugin card: the installed version, an
 * Update (or Restore) button behind a confirmation, progress while the job
 * runs, and the result when it ends. Nothing runs until the person confirms.
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Box, Collapse, LinearProgress, Typography } from '@mui/material';
import { ActionButton, ConfirmActionDialog, Hint, Line, StatusPill } from '../settings/ui';
import {
  checkComfyUpdate,
  getComfyUpdateStatus,
  repairComfyUpdate,
  restoreComfyPrevious,
  startComfyUpdate,
} from '../../api/pluginsService';
import { translate, useUiTranslation } from '../../i18n';

const POLL_MS = 1000;

const RESULT = {
  succeeded: { tone: 'ok', label: () => translate('plugins.comfyUpdate.resultUpdated') },
  rolled_back: { tone: 'warn', label: () => translate('plugins.comfyUpdate.resultSwitchedBack') },
  failed: { tone: 'error', label: () => translate('plugins.comfyUpdate.resultFailed') },
};

const versionArrow = (from, to) => `${from || '?'} → ${to || '?'}`;

const packageChange = ({ name, from, to }) => ({
  label: name,
  value: !from
    ? translate('plugins.comfyUpdate.packageNew', { version: to })
    : !to
      ? translate('plugins.comfyUpdate.packageRemoved', { version: from })
      : versionArrow(from, to),
});

const resultKey = (status) => `${status.state}:${status.finished_at || status.started_at || ''}`;

/**
 * State and actions for the ComfyUI update. Inert for any other plugin, so the
 * card can call it unconditionally.
 *
 * @param {object} plugin   the card's plugin (id, status)
 * @param {function} [showMessage]  snackbar (message, severity)
 */
export const useComfyUpdate = (plugin, showMessage) => {
  const active = plugin?.id === 'comfyui';
  const [check, setCheck] = useState(null);
  const [status, setStatus] = useState(null);
  const [dialog, setDialog] = useState(null);
  const [busy, setBusy] = useState(false);
  const [dismissed, setDismissed] = useState(null);
  const wasRunning = useRef(false);

  const refreshCheck = useCallback(async () => {
    if (!active) return;
    try {
      setCheck(await checkComfyUpdate());
    } catch {
      setCheck(null);
    }
  }, [active]);

  const refreshStatus = useCallback(async () => {
    if (!active) return;
    try {
      setStatus(await getComfyUpdateStatus());
    } catch {
      // Keep the last status; a transient error must not stop the progress view.
    }
  }, [active]);

  // The check depends on whether ComfyUI is running or busy, so it follows the card's status.
  useEffect(() => {
    refreshCheck();
  }, [refreshCheck, plugin?.status]);

  // A job started before this page was opened is picked up here.
  useEffect(() => {
    refreshStatus();
  }, [refreshStatus]);

  const running = status?.state === 'running';

  useEffect(() => {
    if (!running) return undefined;
    const timer = setInterval(refreshStatus, POLL_MS);
    return () => clearInterval(timer);
  }, [running, refreshStatus]);

  useEffect(() => {
    if (running) {
      wasRunning.current = true;
      return;
    }
    if (wasRunning.current) {
      wasRunning.current = false;
      refreshCheck();
    }
  }, [running, refreshCheck]);

  const confirm = useCallback(async () => {
    const start = { update: startComfyUpdate, restore: restoreComfyPrevious, repair: repairComfyUpdate }[dialog];
    if (!start) return;
    setBusy(true);
    try {
      const started = await start();
      setStatus(
        started?.state
          ? started
          : { state: 'running', percent: 0, phase_label: translate('plugins.comfyUpdate.starting') },
      );
      setDialog(null);
    } catch (err) {
      showMessage?.(err?.message || translate('plugins.comfyUpdate.couldNotStart'), 'error');
      setDialog(null);
      refreshCheck();
    } finally {
      setBusy(false);
    }
  }, [dialog, refreshCheck, showMessage]);

  const result = status && RESULT[status.state] && resultKey(status) !== dismissed ? status : null;

  // A crash mid-update left ComfyUI half switched; only Repair is offered until it is put back.
  const needsRepair = !running && (Boolean(check?.needs_repair) || status?.state === 'needs_repair');
  const repair = needsRepair
    ? {
        paths: check?.repair?.paths || [],
        message:
          check?.repair?.message || status?.message || translate('plugins.comfyUpdate.repairNeeded'),
      }
    : null;

  return {
    active,
    check,
    status,
    running,
    result,
    dialog,
    busy,
    installed: check?.installed || null,
    available: Boolean(check?.available) && !running && !needsRepair,
    previous: (!running && !needsRepair && check?.previous) || null,
    blocked: check?.busy || null,
    repair,
    openUpdate: () => setDialog('update'),
    openRestore: () => setDialog('restore'),
    openRepair: () => setDialog('repair'),
    closeDialog: () => !busy && setDialog(null),
    confirm,
    dismissResult: () => status && setDismissed(resultKey(status)),
  };
};

const LogTail = ({ lines }) => {
  const { t } = useUiTranslation();
  const [open, setOpen] = useState(false);
  if (!lines?.length) return null;
  return (
    <>
      <ActionButton kind="link" onClick={() => setOpen((v) => !v)} sx={{ alignSelf: 'flex-start' }}>
        {open ? t('plugins.comfyUpdate.hideDetails') : t('plugins.comfyUpdate.showDetails')}
      </ActionButton>
      <Collapse in={open}>
        <Box
          data-testid="comfyui-update-log"
          sx={{
            maxHeight: 160,
            overflow: 'auto',
            bgcolor: '#1e1e1e',
            color: '#d4d4d4',
            fontFamily: 'monospace',
            fontSize: '0.7rem',
            p: 1,
            borderRadius: 1,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-all',
          }}
        >
          {lines.join('\n')}
        </Box>
      </Collapse>
    </>
  );
};

const Paths = ({ paths }) => (
  <Typography component="span" sx={{ fontFamily: 'monospace', fontSize: '0.7rem', wordBreak: 'break-all' }}>
    {paths.join(', ')}
  </Typography>
);

const RepairDialog = ({ comfy }) => {
  const { t } = useUiTranslation();
  const { check, repair, dialog, busy, closeDialog, confirm } = comfy;
  const facts = [];
  if (check?.previous) {
    facts.push({ label: t('plugins.comfyUpdate.factVersion'), value: versionArrow(check.installed, check.previous) });
  }
  if (repair?.paths?.length) {
    facts.push({ label: t('plugins.comfyUpdate.factMovesBack'), value: <Paths paths={repair.paths} /> });
  }
  return (
    <ConfirmActionDialog
      open={dialog === 'repair'}
      title={t('plugins.comfyUpdate.repairTitle')}
      description={t('plugins.comfyUpdate.repairDescription')}
      facts={facts}
      keeps={check?.keeps?.length ? check.keeps.join(', ') : undefined}
      confirmLabel={t('plugins.comfyUpdate.repairNow')}
      busy={busy}
      reversible
      onConfirm={confirm}
      onClose={closeDialog}
    />
  );
};

const UpdateDialog = ({ comfy }) => {
  const { t } = useUiTranslation();
  const { check, dialog, busy, closeDialog, confirm } = comfy;
  if (dialog === 'repair') return <RepairDialog comfy={comfy} />;
  if (!check) return null;
  const restore = dialog === 'restore';
  const target = restore ? check.previous : check.target;
  const facts = [{ label: t('plugins.comfyUpdate.factVersion'), value: versionArrow(check.installed, target) }];
  if (!restore) {
    facts.push(...(check.package_changes || []).map(packageChange));
    if (check.download) facts.push({ label: t('plugins.comfyUpdate.factDownload'), value: check.download });
  }
  if (typeof check.disk_free_gb === 'number') {
    facts.push({
      label: t('plugins.comfyUpdate.factFreeDisk'),
      value: t('plugins.comfyUpdate.freeDiskValue', { size: check.disk_free_gb.toFixed(1) }),
    });
  }
  const description = restore
    ? t('plugins.comfyUpdate.restoreDescription', { target, installed: check.installed })
    : t('plugins.comfyUpdate.updateDescription', { target, installed: check.installed });
  return (
    <ConfirmActionDialog
      open={dialog !== null}
      title={
        restore
          ? t('plugins.comfyUpdate.restoreTitle', { target })
          : t('plugins.comfyUpdate.updateTitle', { target })
      }
      description={description}
      facts={facts}
      keeps={check.keeps?.length ? check.keeps.join(', ') : undefined}
      confirmLabel={restore ? t('plugins.comfyUpdate.restoreNow') : t('plugins.comfyUpdate.updateNow')}
      busy={busy}
      reversible
      onConfirm={confirm}
      onClose={closeDialog}
    />
  );
};

/**
 * The card section below the version chips: buttons, progress or result.
 *
 * @param {ReturnType<typeof useComfyUpdate>} comfy
 */
export const ComfyUpdateSection = ({ comfy }) => {
  const { t } = useUiTranslation();
  const { active, check, status, running, result, available, previous, blocked, repair } = comfy;
  if (!active || (!check && !running && !result && !repair)) return null;

  const percent = Math.max(0, Math.min(100, Number(status?.percent) || 0));

  return (
    <Box data-testid="comfyui-update" sx={{ display: 'flex', flexDirection: 'column', gap: 0.75, mb: 1 }}>
      {repair && (
        <Box data-testid="comfyui-update-repair" sx={{ display: 'flex', flexDirection: 'column', gap: 0.5 }}>
          <Hint sx={{ color: 'warning.main' }}>{repair.message}</Hint>
          {repair.paths.length > 0 && <Paths paths={repair.paths} />}
          <Line>
            <ActionButton
              onClick={comfy.openRepair}
              disabled={Boolean(blocked)}
              tooltip={blocked || t('plugins.comfyUpdate.repairTooltip')}
            >
              {t('plugins.comfyUpdate.repair')}
            </ActionButton>
          </Line>
        </Box>
      )}

      {(available || previous) && (
        <Line>
          {available && (
            <ActionButton
              onClick={comfy.openUpdate}
              disabled={Boolean(blocked)}
              tooltip={blocked || t('plugins.comfyUpdate.updateTooltip', { version: check.target })}
            >
              {t('plugins.comfyUpdate.updateTo', { version: check.target })}
            </ActionButton>
          )}
          {previous && (
            <ActionButton
              kind="link"
              onClick={comfy.openRestore}
              disabled={Boolean(blocked)}
              tooltip={blocked || t('plugins.comfyUpdate.restoreTooltip', { version: previous })}
            >
              {t('plugins.comfyUpdate.restore', { version: previous })}
            </ActionButton>
          )}
        </Line>
      )}

      {running && (
        <Box data-testid="comfyui-update-progress" sx={{ display: 'flex', flexDirection: 'column', gap: 0.5 }}>
          <Typography variant="caption" color="text.secondary">
            {t('plugins.comfyUpdate.progress', {
              versions: versionArrow(status.from, status.to),
              phase: status.phase_label || t('plugins.comfyUpdate.working'),
              percent,
            })}
          </Typography>
          <LinearProgress variant={percent > 0 ? 'determinate' : 'indeterminate'} value={percent} />
          <LogTail lines={status.log_tail} />
        </Box>
      )}

      {!running && result && (
        <Box data-testid="comfyui-update-result" sx={{ display: 'flex', flexDirection: 'column', gap: 0.5 }}>
          <Line>
            <StatusPill label={RESULT[result.state].label()} tone={RESULT[result.state].tone} />
            {result.message && <Hint>{result.message}</Hint>}
            <ActionButton kind="link" onClick={comfy.dismissResult}>
              {t('plugins.comfyUpdate.dismiss')}
            </ActionButton>
          </Line>
          <LogTail lines={result.log_tail} />
        </Box>
      )}

      <UpdateDialog comfy={comfy} />
    </Box>
  );
};

export default ComfyUpdateSection;
