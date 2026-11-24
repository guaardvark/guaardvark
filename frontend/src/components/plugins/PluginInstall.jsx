// frontend/src/components/plugins/PluginInstall.jsx
// A plugin's declared Install step (plugin.json "install"), and for the GPU
// Embedding plugin, which path the indexer is embedding through right now.

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Box, Button, LinearProgress, Typography } from '@mui/material';
import { getEmbeddingPath, getPluginInstall, startPluginInstall } from '../../api/pluginsService';
import { useUiTranslation } from '../../i18n';

const POLL_MS = 2000;

const LogTail = ({ text }) => (text ? (
  <Box component="pre" sx={{
    m: 0, p: 1, maxHeight: 140, overflow: 'auto', fontSize: 11, borderRadius: 1,
    bgcolor: 'action.hover', whiteSpace: 'pre-wrap', wordBreak: 'break-all',
  }}
  >
    {text}
  </Box>
) : null);

/**
 * Install button and progress for a plugin whose manifest declares an Install step.
 * Renders nothing for other plugins and once the plugin is installed.
 *
 * @param {{plugin: object, showMessage?: function}} props
 */
export const PluginInstallSection = ({ plugin, showMessage }) => {
  const { t } = useUiTranslation();
  const [status, setStatus] = useState(plugin?.install || null);
  const [busy, setBusy] = useState(false);
  const timer = useRef(null);

  useEffect(() => { setStatus(plugin?.install || null); }, [plugin?.install]);
  useEffect(() => () => clearTimeout(timer.current), []);

  const poll = useCallback(async () => {
    try {
      const next = (await getPluginInstall(plugin.id))?.data;
      if (!next) return;
      setStatus(next);
      if (next.state === 'installing') {
        timer.current = setTimeout(poll, POLL_MS);
      } else if (next.state === 'installed') {
        showMessage?.(t('plugins.install.done', { name: plugin.name || plugin.id }), 'success');
      } else {
        showMessage?.(t('plugins.install.failed', { name: plugin.name || plugin.id, code: next.exit_code }), 'error');
      }
    } catch {
      timer.current = setTimeout(poll, POLL_MS * 2);
    }
  }, [plugin?.id, plugin?.name, showMessage, t]);

  const install = async () => {
    setBusy(true);
    try {
      const res = await startPluginInstall(plugin.id);
      setStatus(res?.data || { ...status, state: 'installing' });
      timer.current = setTimeout(poll, POLL_MS);
    } catch (e) {
      showMessage?.(e.message, 'error');
    } finally {
      setBusy(false);
    }
  };

  if (!status || status.state === 'installed') return null;
  const installing = status.state === 'installing';

  return (
    <Box data-testid="plugin-install" sx={{ display: 'flex', flexDirection: 'column', gap: 0.75, mb: 1 }}>
      <Typography variant="body2" sx={{ color: status.state === 'failed' ? 'error.main' : 'text.secondary' }}>
        {status.state === 'failed'
          ? t('plugins.install.failedHint', { code: status.exit_code })
          : t('plugins.install.notInstalled')}
      </Typography>
      {status.downloads && (
        <Typography variant="caption" sx={{ color: 'text.secondary' }}>
          {t('plugins.install.downloads', { what: status.downloads })}
        </Typography>
      )}
      {installing && <LinearProgress />}
      {(installing || status.state === 'failed') && <LogTail text={status.log_tail} />}
      <Box>
        <Button size="small" variant="contained" onClick={install} disabled={busy || installing}>
          {installing ? t('plugins.install.installing') : t('plugins.install.install')}
        </Button>
      </Box>
    </Box>
  );
};

/**
 * For the GPU Embedding plugin: whether the indexer embeds through it, and if
 * not, the reason the router gave. Read from /api/model/embedding/path.
 *
 * @param {{plugin: object}} props
 */
export const EmbeddingPathSection = ({ plugin }) => {
  const { t } = useUiTranslation();
  const [path, setPath] = useState(null);
  const active = plugin?.id === 'gpu_embedding' && plugin?.status === 'running';

  useEffect(() => {
    if (!active) {
      setPath(null);
      return undefined;
    }
    let stop = false;
    const load = async () => {
      try {
        const res = await getEmbeddingPath();
        if (!stop) setPath(res?.data || null);
      } catch {
        if (!stop) setPath(null);
      }
    };
    load();
    const id = setInterval(load, 30000);
    return () => { stop = true; clearInterval(id); };
  }, [active]);

  if (!active || !path) return null;
  return (
    <Box data-testid="embedding-path" sx={{ mb: 1 }}>
      <Typography variant="body2" sx={{ color: path.in_use ? 'success.main' : 'warning.main' }}>
        {path.in_use
          ? t('plugins.embeddingPath.inUse', { model: path.served_model, dim: path.dimension })
          : t('plugins.embeddingPath.notUsed', { reason: path.reason })}
      </Typography>
    </Box>
  );
};
