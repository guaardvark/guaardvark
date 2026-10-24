// frontend/src/components/modals/IndexingDialog.jsx
// Per-website Google Search Console / Indexing API control panel.
// Shows queue + quota status, lets the user submit now (sitemap sync + submit),
// and toggle the auto-drip schedule.
import React, { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  FormControlLabel,
  Stack,
  Switch,
  Tooltip,
  Typography,
} from "@mui/material";
import CloudUploadIcon from "@mui/icons-material/CloudUpload";
import {
  getIndexingStatus,
  submitToIndex,
  updateIndexingConfig,
} from "../../api/searchConsoleService";
import { translate, uiLocale, useUiTranslation } from "../../i18n";

const IndexingDialog = ({ open, onClose, website, onFeedback }) => {
  const { t } = useUiTranslation();
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  const websiteId = website?.id;

  const loadStatus = useCallback(async () => {
    if (!websiteId) return;
    setLoading(true);
    setError(null);
    try {
      const data = await getIndexingStatus(websiteId);
      setStatus(data);
    } catch (err) {
      setError(err.message || translate("dialogs.indexing.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [websiteId]);

  useEffect(() => {
    if (open) loadStatus();
  }, [open, loadStatus]);

  const handleSubmitNow = async () => {
    if (!websiteId) return;
    setSubmitting(true);
    try {
      const res = await submitToIndex(websiteId, { sync: true });
      onFeedback?.({
        open: true,
        message:
          res?.message ||
          t("dialogs.indexing.queued"),
        severity: "success",
      });
      // Reflect the just-synced queue counts.
      if (res?.status) setStatus(res.status);
      else loadStatus();
    } catch (err) {
      onFeedback?.({
        open: true,
        message: t("dialogs.indexing.submitFailed", { error: err.message || t("dialogs.indexing.unknownError") }),
        severity: "error",
      });
    } finally {
      setSubmitting(false);
    }
  };

  const handleToggleDrip = async (e) => {
    const enabled = e.target.checked;
    try {
      await updateIndexingConfig(websiteId, { enabled });
      setStatus((prev) => (prev ? { ...prev, enabled } : prev));
      onFeedback?.({
        open: true,
        message: enabled
          ? t("dialogs.indexing.dripEnabled")
          : t("dialogs.indexing.dripDisabled"),
        severity: "info",
      });
    } catch (err) {
      onFeedback?.({
        open: true,
        message: t("dialogs.indexing.updateFailed", { error: err.message || t("dialogs.indexing.unknownError") }),
        severity: "error",
      });
    }
  };

  const queue = status?.queue || {};

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>
        {t("dialogs.indexing.title")}
        {website?.url ? (
          <Typography variant="body2" color="text.secondary" noWrap>
            {website.url}
          </Typography>
        ) : null}
      </DialogTitle>
      <DialogContent dividers>
        {loading ? (
          <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
            <CircularProgress />
          </Box>
        ) : error ? (
          <Alert severity="error">{error}</Alert>
        ) : status ? (
          <Stack spacing={2}>
            {!status.credentials_ok && (
              <Alert severity="warning">
                {t("dialogs.indexing.noCredentials")}
              </Alert>
            )}

            <Box>
              <Typography variant="subtitle2" gutterBottom>
                {t("dialogs.indexing.todaysQuota")}
              </Typography>
              <Typography variant="h5">
                {status.submitted_today}
                <Typography component="span" variant="body2" color="text.secondary">
                  {" "}
                  {t("dialogs.indexing.quotaDetail", { cap: status.daily_cap, remaining: status.quota_remaining })}
                </Typography>
              </Typography>
            </Box>

            <Divider />

            <Box>
              <Typography variant="subtitle2" gutterBottom>
                {t("dialogs.indexing.queue")}
              </Typography>
              <Stack direction="row" spacing={1}>
                <Chip label={t("dialogs.indexing.pending", { count: queue.pending || 0 })} color="default" size="small" />
                <Chip label={t("dialogs.indexing.indexed", { count: queue.success || 0 })} color="success" size="small" />
                <Chip label={t("dialogs.indexing.failed", { count: queue.failed || 0 })} color="error" size="small" />
              </Stack>
              {status.last_sitemap_sync && (
                <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 1 }}>
                  {t("dialogs.indexing.lastSync", { date: new Date(status.last_sitemap_sync).toLocaleString(uiLocale()) })}
                </Typography>
              )}
            </Box>

            <Divider />

            <Tooltip title={t("dialogs.indexing.dripTooltip")}>
              <FormControlLabel
                control={
                  <Switch
                    checked={!!status.enabled}
                    onChange={handleToggleDrip}
                  />
                }
                label={t("dialogs.indexing.dripLabel")}
              />
            </Tooltip>
          </Stack>
        ) : (
          <Typography color="text.secondary">{t("dialogs.indexing.noStatus")}</Typography>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>{t("dialogs.shared.close")}</Button>
        <Button
          variant="contained"
          startIcon={
            submitting ? <CircularProgress size={16} color="inherit" /> : <CloudUploadIcon />
          }
          onClick={handleSubmitNow}
          disabled={submitting || loading || (status && !status.credentials_ok)}
        >
          {submitting ? t("dialogs.indexing.queuing") : t("dialogs.indexing.submitNow")}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default IndexingDialog;
