// frontend/src/components/website/IndexingPanel.jsx
// Full per-website Google Indexing panel: quota + queue status, submit/sync controls,
// auto-drip toggle, and the per-URL submission log (the data IndexingDialog never showed).
// Used by the Indexing tab of WebsiteDetailPage. Reads only existing endpoints — no backend
// change required.
import React, { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  FormControl,
  FormControlLabel,
  IconButton,
  MenuItem,
  Paper,
  Select,
  Stack,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";
import CloudUploadIcon from "@mui/icons-material/CloudUpload";
import RefreshIcon from "@mui/icons-material/Refresh";
import {
  getIndexingStatus,
  getSubmissions,
  submitToIndex,
  updateIndexingConfig,
} from "../../api/searchConsoleService";
import { translate, uiLocale, useUiTranslation, Trans } from "../../i18n";

const statusChip = () => ({
  success: { label: translate("websites.indexing.status.indexed"), color: "success" },
  pending: { label: translate("websites.indexing.status.pending"), color: "default" },
  failed: { label: translate("websites.indexing.status.failed"), color: "error" },
});

const formatTime = (iso) => {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString(uiLocale());
  } catch {
    return iso;
  }
};

const IndexingPanel = ({ website, onFeedback }) => {
  const { t } = useUiTranslation();
  const websiteId = website?.id;

  const [status, setStatus] = useState(null);
  const [submissions, setSubmissions] = useState([]);
  const [statusFilter, setStatusFilter] = useState("");
  const [loading, setLoading] = useState(false);
  const [logLoading, setLogLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  const loadStatus = useCallback(async () => {
    if (!websiteId) return;
    setLoading(true);
    setError(null);
    try {
      setStatus(await getIndexingStatus(websiteId));
    } catch (err) {
      setError(err.message || t("websites.indexing.statusLoadFailed"));
    } finally {
      setLoading(false);
    }
  }, [websiteId]);

  const loadSubmissions = useCallback(async () => {
    if (!websiteId) return;
    setLogLoading(true);
    try {
      const params = { limit: 200 };
      if (statusFilter) params.status = statusFilter;
      setSubmissions(await getSubmissions(websiteId, params));
    } catch (err) {
      // The log is supplementary — surface as feedback, not a blocking error.
      onFeedback?.({
        open: true,
        message: t("websites.indexing.logLoadFailed", {
          error: err.message || t("websites.indexing.unknownError"),
        }),
        severity: "warning",
      });
    } finally {
      setLogLoading(false);
    }
  }, [websiteId, statusFilter, onFeedback]);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  useEffect(() => {
    loadSubmissions();
  }, [loadSubmissions]);

  const handleSubmitNow = async () => {
    if (!websiteId) return;
    setSubmitting(true);
    try {
      const res = await submitToIndex(websiteId, { sync: true });
      onFeedback?.({
        open: true,
        message:
          res?.message || t("websites.indexing.submitQueued"),
        severity: "success",
      });
      if (res?.status) setStatus(res.status);
      else loadStatus();
      loadSubmissions();
    } catch (err) {
      onFeedback?.({
        open: true,
        message: t("websites.indexing.submitFailed", {
          error: err.message || t("websites.indexing.unknownError"),
        }),
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
          ? t("websites.indexing.dripEnabled")
          : t("websites.indexing.dripDisabled"),
        severity: "info",
      });
    } catch (err) {
      onFeedback?.({
        open: true,
        message: t("websites.indexing.settingFailed", {
          error: err.message || t("websites.indexing.unknownError"),
        }),
        severity: "error",
      });
    }
  };

  const queue = status?.queue || {};

  if (loading && !status) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
        <CircularProgress />
      </Box>
    );
  }

  if (error) {
    return <Alert severity="error">{error}</Alert>;
  }

  if (!status) {
    return <Typography color="text.secondary">{t("websites.indexing.noStatus")}</Typography>;
  }

  return (
    <Stack spacing={2}>
      {!status.credentials_ok && (
        <Alert severity="warning">
          {t("websites.indexing.noCredentials")}
        </Alert>
      )}

      {/* Quota + queue + controls */}
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Stack
          direction={{ xs: "column", sm: "row" }}
          spacing={2}
          justifyContent="space-between"
          alignItems={{ xs: "flex-start", sm: "center" }}
        >
          <Box>
            <Typography variant="subtitle2" gutterBottom>
              {t("websites.indexing.quotaTitle")}
            </Typography>
            <Typography variant="h5">
              <Trans
                i18nKey="websites.indexing.quota"
                values={{
                  submitted: status.submitted_today,
                  cap: status.daily_cap,
                  remaining: status.quota_remaining,
                }}
                components={{
                  detail: <Typography component="span" variant="body2" color="text.secondary" />,
                }}
              />
            </Typography>
          </Box>
          <Button
            variant="contained"
            startIcon={
              submitting ? <CircularProgress size={16} color="inherit" /> : <CloudUploadIcon />
            }
            onClick={handleSubmitNow}
            disabled={submitting || !status.credentials_ok}
          >
            {submitting ? t("websites.indexing.queuing") : t("websites.indexing.submitNow")}
          </Button>
        </Stack>

        <Divider sx={{ my: 2 }} />

        <Typography variant="subtitle2" gutterBottom>
          {t("websites.indexing.queue")}
        </Typography>
        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
          <Chip label={t("websites.indexing.queuePending", { count: queue.pending || 0 })} size="small" />
          <Chip
            label={t("websites.indexing.queueIndexed", { count: queue.success || 0 })}
            color="success"
            size="small"
          />
          <Chip
            label={t("websites.indexing.queueFailed", { count: queue.failed || 0 })}
            color="error"
            size="small"
          />
        </Stack>

        <Stack direction="row" spacing={3} sx={{ mt: 1.5 }} flexWrap="wrap" useFlexGap>
          <Typography variant="caption" color="text.secondary">
            {t("websites.indexing.lastSitemapSync", { time: formatTime(status.last_sitemap_sync) })}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {t("websites.indexing.lastRun", { time: formatTime(status.last_run_at) })}
          </Typography>
        </Stack>

        <Tooltip title={t("websites.indexing.dripTooltip")}>
          <FormControlLabel
            sx={{ mt: 1 }}
            control={<Switch checked={!!status.enabled} onChange={handleToggleDrip} />}
            label={t("websites.indexing.dripLabel")}
          />
        </Tooltip>
      </Paper>

      {/* Per-URL submission log */}
      <Box>
        <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
          <Typography variant="subtitle2" sx={{ flexGrow: 1 }}>
            {t("websites.indexing.logTitle")}
          </Typography>
          <FormControl size="small" sx={{ minWidth: 130 }}>
            <Select
              value={statusFilter}
              displayEmpty
              onChange={(e) => setStatusFilter(e.target.value)}
            >
              <MenuItem value="">{t("websites.indexing.filterAll")}</MenuItem>
              <MenuItem value="pending">{t("websites.indexing.filterPending")}</MenuItem>
              <MenuItem value="success">{t("websites.indexing.filterIndexed")}</MenuItem>
              <MenuItem value="failed">{t("websites.indexing.filterFailed")}</MenuItem>
            </Select>
          </FormControl>
          <Tooltip title={t("websites.indexing.refreshLog")}>
            <span>
              <IconButton size="small" onClick={loadSubmissions} disabled={logLoading}>
                <RefreshIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
        </Stack>

        <Paper variant="outlined" sx={{ overflow: "hidden" }}>
          <TableContainer sx={{ maxHeight: 420 }}>
            <Table stickyHeader size="small">
              <TableHead>
                <TableRow>
                  <TableCell sx={{ fontWeight: "bold" }}>{t("websites.indexing.columns.url")}</TableCell>
                  <TableCell sx={{ fontWeight: "bold" }}>{t("websites.indexing.columns.status")}</TableCell>
                  <TableCell sx={{ fontWeight: "bold" }} align="right">
                    {t("websites.indexing.columns.http")}
                  </TableCell>
                  <TableCell sx={{ fontWeight: "bold" }}>{t("websites.indexing.columns.submitted")}</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {submissions.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={4}>
                      <Typography
                        variant="body2"
                        color="text.secondary"
                        sx={{ py: 2, textAlign: "center", fontStyle: "italic" }}
                      >
                        {logLoading
                          ? t("websites.indexing.loading")
                          : t("websites.indexing.empty")}
                      </Typography>
                    </TableCell>
                  </TableRow>
                ) : (
                  submissions.map((row) => {
                    const chip = statusChip()[row.status] || {
                      label: row.status,
                      color: "default",
                    };
                    return (
                      <TableRow key={row.id} hover>
                        <TableCell
                          sx={{
                            maxWidth: 360,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                          }}
                        >
                          <Tooltip
                            title={
                              row.error
                                ? t("websites.indexing.urlError", { url: row.url, error: row.error })
                                : row.url
                            }
                          >
                            <Typography variant="body2">{row.url}</Typography>
                          </Tooltip>
                        </TableCell>
                        <TableCell>
                          <Tooltip title={row.error || ""} disableHoverListener={!row.error}>
                            <Chip label={chip.label} color={chip.color} size="small" />
                          </Tooltip>
                        </TableCell>
                        <TableCell align="right">{row.http_status ?? "—"}</TableCell>
                        <TableCell>{formatTime(row.submitted_at || row.created_at)}</TableCell>
                      </TableRow>
                    );
                  })
                )}
              </TableBody>
            </Table>
          </TableContainer>
        </Paper>
      </Box>
    </Stack>
  );
};

export default IndexingPanel;
