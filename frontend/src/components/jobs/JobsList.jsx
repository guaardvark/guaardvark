// frontend/src/components/jobs/JobsList.jsx
//
// Shared list component for the Jobs and Activity pages. Both pages are
// thin wrappers that pre-filter `kinds` and supply a title; everything
// else (filters, tabs, list, detail drawer, live updates) lives here.
//
// Reads from /api/jobs/* (Phase 3) and listens to the canonical jobs:*
// socket channel via UnifiedProgressContext.unifiedJobs (Phase 4).
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { formatUiError } from "../../utils/uiError";
import {
  Box,
  Paper,
  Typography,
  Stack,
  Chip,
  Tabs,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  LinearProgress,
  Drawer,
  IconButton,
  Alert,
  TextField,
  InputAdornment,
  Menu,
  MenuItem,
} from "@mui/material";
import {
  Close as CloseIcon,
  Refresh as RefreshIcon,
  Search as SearchIcon,
  DeleteOutline as DeleteIcon,
} from "@mui/icons-material";
import {
  listJobs,
  listJobHistory,
  jobsSummary,
  cancelJob,
  clearJobHistory,
  JOB_STATUSES,
} from "../../api/jobsService";
import * as apiService from "../../api";
import { Button } from "@mui/material";
import { useUnifiedProgress } from "../../contexts/UnifiedProgressContext";
import EntityContextMenu from "../common/EntityContextMenu";
import useContextMenu from "../../hooks/useContextMenu";
import copyText from "../../utils/copyText";
import { translate, uiLocale, useUiTranslation } from "../../i18n";

const STATUS_COLORS = {
  pending: "default",
  running: "info",
  paused: "warning",
  completed: "success",
  failed: "error",
  cancelled: "default",
};

// A job status as shown; the value from the API stays the one compared and filtered on.
const jobStatusLabel = (status) =>
  ({
    pending: translate("jobs.status.pending"),
    running: translate("jobs.status.running"),
    paused: translate("jobs.status.paused"),
    completed: translate("jobs.status.completed"),
    failed: translate("jobs.status.failed"),
    cancelled: translate("jobs.status.cancelled"),
  })[status] ?? status;

const _formatDuration = (seconds) => {
  if (seconds == null) return "—";
  if (seconds < 60) return translate("jobs.duration.seconds", { seconds: Math.round(seconds) });
  if (seconds < 3600) {
    return translate("jobs.duration.minutesSeconds", {
      minutes: Math.floor(seconds / 60),
      seconds: Math.round(seconds % 60),
    });
  }
  return translate("jobs.duration.hours", { hours: (seconds / 3600).toFixed(1) });
};

// Indexing jobs that are pending, failed or paused can be re-queued.
const canResumeIndexing = (job) =>
  ["pending", "failed", "paused"].includes(job.status) &&
  Boolean(
    job.kind?.includes("index") ||
      job.label?.toLowerCase().includes("index") ||
      job.metadata?.process_type === "indexing",
  );

const _formatRelative = (iso) => {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    const diff = (Date.now() - d.getTime()) / 1000;
    if (diff < 60) return translate("jobs.when.justNow");
    if (diff < 3600) return translate("jobs.when.minutesAgo", { minutes: Math.floor(diff / 60) });
    if (diff < 86400) return translate("jobs.when.hoursAgo", { hours: Math.floor(diff / 3600) });
    return d.toLocaleDateString(uiLocale());
  } catch {
    return iso;
  }
};

const JobsList = ({ title: _title, subtitle: _subtitle, kinds }) => {
  const { t } = useUiTranslation();
  const { unifiedJobs } = useUnifiedProgress();

  const [tab, setTab] = useState(0);  // 0 = Active, 1 = History
  const [statusFilter, setStatusFilter] = useState(new Set());
  const [search, setSearch] = useState("");
  const [activeRows, setActiveRows] = useState([]);
  const [historyRows, setHistoryRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [_summary, setSummary] = useState(null);
  const [selected, setSelected] = useState(null);

  const [clearMenuAnchorEl, setClearMenuAnchorEl] = useState(null);
  const isClearMenuOpen = Boolean(clearMenuAnchorEl);

  const [indexingPaused, setIndexingPaused] = useState(false);
  const rowMenu = useContextMenu();

  const handleClearHistory = async (kindsToClear) => {
    setLoading(true);
    setError(null);
    try {
      await clearJobHistory({ kinds: kindsToClear });
      await refreshHistory();
    } catch (e) {
      console.error("JobsList: clear history failed:", e);
      setError(formatUiError(e.response?.data?.error) || e.message || t("jobs.list.clearHistoryFailed"));
      setLoading(false);
    }
    setClearMenuAnchorEl(null);
  };

  // Initial load + manual refresh button. Live updates from the socket
  // mutate `activeRows` directly; this is the snapshot fetch.
  const refreshActive = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listJobs({ kinds, limit: 200 });
      setActiveRows(data?.jobs || []);
    } catch (e) {
      console.error("JobsList: list failed:", e);
      setError(formatUiError(e.response?.data?.error) || e.message || t("jobs.list.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [kinds]);

  const refreshHistory = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // history endpoint takes a single kind; fetch per kind and merge.
      // Cheaper than a general endpoint that ignores kind, but fine for
      // dashboard sizes; can be optimized when histories grow.
      const all = [];
      for (const k of kinds) {
        const data = await listJobHistory({ kind: k, limit: 200 });
        all.push(...(data?.history || []));
      }
      // Sort merged history by finished_at desc.
      all.sort((a, b) => new Date(b.finished_at) - new Date(a.finished_at));
      setHistoryRows(all);
    } catch (e) {
      console.error("JobsList: history failed:", e);
      setError(formatUiError(e.response?.data?.error) || e.message || t("jobs.list.historyFailed"));
    } finally {
      setLoading(false);
    }
  }, [kinds]);

  const refreshSummary = useCallback(async () => {
    try {
      const data = await jobsSummary();
      setSummary(data);
    } catch (e) {
      // Summary is decoration; non-fatal.
      console.warn("JobsList: summary failed:", e);
    }
  }, []);

  useEffect(() => {
    if (tab === 0) refreshActive();
    else refreshHistory();
    refreshSummary();
  }, [tab, refreshActive, refreshHistory, refreshSummary]);

  // Fetch global indexing pause state to integrate with job list
  const fetchIndexingPauseState = useCallback(async () => {
    try {
      const paused = await apiService.getIndexingPaused();
      setIndexingPaused(!!paused);
    } catch (e) {
      // non fatal
    }
  }, []);

  useEffect(() => {
    fetchIndexingPauseState();
  }, [fetchIndexingPauseState]);

  // Live updates from the canonical jobs:* socket channel (Phase 4).
  // unifiedJobs is a Map<job.id, jobDict>; fold its entries into activeRows
  // when on the Active tab so the list reflects in-flight progress.
  useEffect(() => {
    if (tab !== 0 || !unifiedJobs) return;
    setActiveRows((prev) => {
      const byId = new Map(prev.map((j) => [j.id, j]));
      for (const [, job] of unifiedJobs) {
        if (!kinds.includes(job.kind)) continue;
        byId.set(job.id, job);
      }
      // Sort by started_at desc; terminal jobs sink to the bottom of the active list
      // briefly while the user-facing transition happens, then disappear on next refresh.
      return Array.from(byId.values()).sort((a, b) => {
        const ta = a.finished_at || a.started_at || "";
        const tb = b.finished_at || b.started_at || "";
        return tb.localeCompare(ta);
      });
    });
  }, [unifiedJobs, kinds, tab]);

  // Filter the visible rows by status chip selection + free-text search.
  const visibleRows = useMemo(() => {
    const source = tab === 0 ? activeRows : historyRows;
    const q = search.trim().toLowerCase();
    return source.filter((j) => {
      if (statusFilter.size > 0 && !statusFilter.has(j.status)) return false;
      if (q) {
        const hay = `${j.label || ""} ${j.kind} ${j.status}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [activeRows, historyRows, tab, statusFilter, search]);

  // Status counts for the header chip row, derived from the active rows so
  // the chips match what the user sees in the list right now.
  const statusCounts = useMemo(() => {
    const counts = {};
    const src = tab === 0 ? activeRows : historyRows;
    for (const j of src) counts[j.status] = (counts[j.status] || 0) + 1;
    return counts;
  }, [activeRows, historyRows, tab]);

  const handleCancelJob = async (job) => {
    try {
      const res = await cancelJob(job.id);
      if (res?.cancelled) {
        // Optimistic — mark cancelled in the local list.
        setActiveRows((prev) => prev.map((r) =>
          r.id === job.id ? { ...r, status: "cancelled", cancellable: false } : r
        ));
        setSelected((cur) => (cur && cur.id === job.id ? { ...cur, status: "cancelled", cancellable: false } : cur));
      } else {
        setError(res?.reason || t("jobs.list.cancelRefused"));
      }
    } catch (e) {
      setError(formatUiError(e.response?.data?.error) || e.message || t("jobs.list.cancelFailed"));
    }
  };

  const handleResumeIndexing = async () => {
    try {
      const res = await apiService.resumePendingIndexing();
      alert(res?.message || t("jobs.list.resumeTriggered"));
      await refreshActive();
      setSelected(null);
    } catch (e) {
      alert(t("jobs.list.resumeFailed", { error: e.message || e }));
    }
  };

  const toggleStatus = (status) => {
    setStatusFilter((prev) => {
      const next = new Set(prev);
      if (next.has(status)) next.delete(status);
      else next.add(status);
      return next;
    });
  };

  return (
    <Box sx={{ maxWidth: 1400, mx: "auto", mt: 2, px: 2 }}>
      <Stack spacing={2}>
        {/* Page title comes from PageLayout — don't duplicate it here. */}

        {/* Status chips — click to filter */}
        <Paper elevation={2} sx={{ p: 2, borderRadius: 2 }}>
          <Stack direction="row" spacing={1} flexWrap="wrap" sx={{ gap: 1 }}>
            {JOB_STATUSES.map((s) => {
              const count = statusCounts[s] || 0;
              const active = statusFilter.has(s);
              return (
                <Chip
                  key={s}
                  label={t("jobs.list.statusChip", { status: jobStatusLabel(s), count })}
                  color={active ? STATUS_COLORS[s] : "default"}
                  variant={active ? "filled" : "outlined"}
                  onClick={() => toggleStatus(s)}
                  size="small"
                />
              );
            })}
            <Box sx={{ flexGrow: 1 }} />
            <TextField
              size="small"
              placeholder={t("jobs.list.searchPlaceholder")}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <SearchIcon fontSize="small" />
                  </InputAdornment>
                ),
              }}
              sx={{ minWidth: 280 }}
            />
            <IconButton
              size="small"
              onClick={() => (tab === 0 ? refreshActive() : refreshHistory())}
              title={t("common.refresh")}
            >
              <RefreshIcon fontSize="small" />
            </IconButton>
            {/* Integrate with the global indexing pause + add 'resume all pending' */}
            {indexingPaused && <Chip label={t("jobs.list.indexingPaused")} color="warning" size="small" />}
            <Button
              size="small"
              variant="outlined"
              color="success"
              onClick={async () => {
                try {
                  const res = await apiService.resumePendingIndexing();
                  alert(res?.message || t("jobs.list.resumeAllStarted"));
                  await refreshActive();
                } catch (e) {
                  alert(t("jobs.list.resumeAllFailed", { error: e.message || e }));
                }
              }}
              disabled={loading}
            >
              {t("jobs.list.resumeAll")}
            </Button>
          </Stack>
        </Paper>

        {/* Tabs: Active / History */}
        <Paper elevation={2} sx={{ borderRadius: 2 }}>
          <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ borderBottom: 1, borderColor: "divider", pr: 2 }}>
            <Tabs value={tab} onChange={(_, v) => setTab(v)}>
              <Tab label={t("jobs.list.activeTab", { count: activeRows.length })} />
              <Tab label={t("jobs.list.historyTab", { count: historyRows.length })} />
            </Tabs>
            {tab === 1 && historyRows.length > 0 && (
              <Box>
                <Button
                  size="small"
                  color="error"
                  startIcon={<DeleteIcon />}
                  onClick={(e) => setClearMenuAnchorEl(e.currentTarget)}
                >
                  {t("jobs.list.clearHistory")}
                </Button>
                <Menu
                  anchorEl={clearMenuAnchorEl}
                  open={isClearMenuOpen}
                  onClose={() => setClearMenuAnchorEl(null)}
                >
                  <MenuItem onClick={() => handleClearHistory(kinds)}>
                    <Typography color="error">{t("jobs.list.clearAllShown")}</Typography>
                  </MenuItem>
                  {kinds.map((k) => (
                    <MenuItem key={k} onClick={() => handleClearHistory([k])}>
                      {t("jobs.list.clearKind", { kind: k })}
                    </MenuItem>
                  ))}
                </Menu>
              </Box>
            )}
          </Stack>

          {error && <Alert severity="error" sx={{ m: 2 }}>{error}</Alert>}
          {loading && <LinearProgress />}

          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell sx={{ fontWeight: 600 }}>{t("jobs.list.label")}</TableCell>
                <TableCell sx={{ fontWeight: 600 }}>{t("jobs.list.kind")}</TableCell>
                <TableCell sx={{ fontWeight: 600 }}>{t("jobs.list.status")}</TableCell>
                <TableCell sx={{ fontWeight: 600, width: 200 }}>{t("jobs.list.progress")}</TableCell>
                <TableCell sx={{ fontWeight: 600 }}>{t("jobs.list.duration")}</TableCell>
                <TableCell sx={{ fontWeight: 600 }}>{tab === 0 ? t("jobs.list.started") : t("jobs.list.finished")}</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {visibleRows.length === 0 && !loading && (
                <TableRow>
                  <TableCell colSpan={6}>
                    <Typography variant="body2" color="text.secondary" sx={{ p: 2, textAlign: "center" }}>
                      {tab === 0 ? t("jobs.list.emptyActive") : t("jobs.list.emptyHistory")}
                    </Typography>
                  </TableCell>
                </TableRow>
              )}
              {visibleRows.map((j) => (
                <TableRow
                  key={j.id}
                  hover
                  onClick={() => setSelected(j)}
                  onContextMenu={(e) => rowMenu.open(e, j)}
                  sx={{ cursor: "pointer" }}
                >
                  <TableCell>{j.label}</TableCell>
                  <TableCell>
                    <Chip label={j.kind} size="small" variant="outlined" />
                  </TableCell>
                  <TableCell>
                    <Chip label={jobStatusLabel(j.status)} color={STATUS_COLORS[j.status] || "default"} size="small" />
                  </TableCell>
                  <TableCell>
                    {j.progress != null ? (
                      <Stack direction="row" spacing={1} alignItems="center">
                        <Box sx={{ width: 100 }}>
                          <LinearProgress
                            variant="determinate"
                            value={Math.max(0, Math.min(100, j.progress))}
                            sx={{ height: 6, borderRadius: 3 }}
                          />
                        </Box>
                        <Typography variant="caption">{Math.round(j.progress)}%</Typography>
                      </Stack>
                    ) : (
                      <Typography variant="caption" color="text.secondary">—</Typography>
                    )}
                  </TableCell>
                  <TableCell>{_formatDuration(j.duration_s)}</TableCell>
                  <TableCell>{_formatRelative(tab === 0 ? j.started_at : j.finished_at)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Paper>
      </Stack>

      <EntityContextMenu
        anchorPosition={rowMenu.anchorPosition}
        onClose={rowMenu.close}
        actions={
          rowMenu.payload
            ? [
                { label: t("jobs.list.details"), onClick: () => setSelected(rowMenu.payload) },
                { label: t("jobs.list.copyId"), onClick: () => copyText(rowMenu.payload.id) },
                canResumeIndexing(rowMenu.payload) && {
                  label: t("jobs.list.resumeReindex"),
                  onClick: handleResumeIndexing,
                  dividerBefore: true,
                },
                rowMenu.payload.cancellable && {
                  label: t("jobs.list.cancelJob"),
                  onClick: () => handleCancelJob(rowMenu.payload),
                  color: "error.main",
                  dividerBefore: !canResumeIndexing(rowMenu.payload),
                },
              ]
            : []
        }
      />

      {/* Detail drawer */}
      <Drawer anchor="right" open={!!selected} onClose={() => setSelected(null)}>
        <Box sx={{ width: 420, p: 3 }}>
          <Stack direction="row" justifyContent="space-between" alignItems="center" mb={2}>
            <Typography variant="h6" fontWeight="bold">{t("jobs.detail.title")}</Typography>
            <IconButton onClick={() => setSelected(null)} size="small">
              <CloseIcon />
            </IconButton>
          </Stack>
          {selected && (
            <Stack spacing={2}>
              <Box>
                <Typography variant="caption" color="text.secondary">{t("jobs.detail.id")}</Typography>
                <Typography variant="body2" sx={{ fontFamily: "monospace" }}>{selected.id}</Typography>
              </Box>
              <Box>
                <Typography variant="caption" color="text.secondary">{t("jobs.list.label")}</Typography>
                <Typography variant="body2">{selected.label}</Typography>
              </Box>
              <Stack direction="row" spacing={2}>
                <Box>
                  <Typography variant="caption" color="text.secondary">{t("jobs.list.kind")}</Typography>
                  <Typography variant="body2">{selected.kind}</Typography>
                </Box>
                <Box>
                  <Typography variant="caption" color="text.secondary">{t("jobs.list.status")}</Typography>
                  <Chip label={jobStatusLabel(selected.status)} color={STATUS_COLORS[selected.status]} size="small" />
                </Box>
              </Stack>
              <Box>
                <Typography variant="caption" color="text.secondary">{t("jobs.list.progress")}</Typography>
                <Typography variant="body2">{selected.progress != null ? `${Math.round(selected.progress)}%` : "—"}</Typography>
              </Box>
              <Stack direction="row" spacing={2}>
                <Box>
                  <Typography variant="caption" color="text.secondary">{t("jobs.list.started")}</Typography>
                  <Typography variant="body2">{_formatRelative(selected.started_at)}</Typography>
                </Box>
                {selected.finished_at && (
                  <Box>
                    <Typography variant="caption" color="text.secondary">{t("jobs.list.finished")}</Typography>
                    <Typography variant="body2">{_formatRelative(selected.finished_at)}</Typography>
                  </Box>
                )}
                <Box>
                  <Typography variant="caption" color="text.secondary">{t("jobs.list.duration")}</Typography>
                  <Typography variant="body2">{_formatDuration(selected.duration_s)}</Typography>
                </Box>
              </Stack>
              {selected.error_message && (
                <Box>
                  <Typography variant="caption" color="error.main">{t("jobs.detail.error")}</Typography>
                  <Typography variant="body2" sx={{ whiteSpace: "pre-wrap" }}>{selected.error_message}</Typography>
                </Box>
              )}
              {selected.metadata && Object.keys(selected.metadata).length > 0 && (
                <Box>
                  <Typography variant="caption" color="text.secondary">{t("jobs.detail.metadata")}</Typography>
                  <Box
                    component="pre"
                    sx={{
                      fontSize: "0.75rem",
                      p: 1.5,
                      bgcolor: "action.hover",
                      borderRadius: 1,
                      overflow: "auto",
                      maxHeight: 320,
                    }}
                  >
                    {JSON.stringify(selected.metadata, null, 2)}
                  </Box>
                </Box>
              )}
              {selected.cancellable && (
                <Button
                  variant="outlined"
                  color="error"
                  fullWidth
                  onClick={() => handleCancelJob(selected)}
                >
                  {t("jobs.list.cancelJob")}
                </Button>
              )}
              {/* Resume/retry for pending or failed indexing-related jobs */}
              {canResumeIndexing(selected) && (
                <Button
                  variant="outlined"
                  color="success"
                  fullWidth
                  onClick={handleResumeIndexing}
                >
                  {t("jobs.list.resumeReindex")}
                </Button>
              )}
            </Stack>
          )}
        </Box>
      </Drawer>
    </Box>
  );
};

export default JobsList;
