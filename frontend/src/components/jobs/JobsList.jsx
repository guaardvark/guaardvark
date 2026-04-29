// frontend/src/components/jobs/JobsList.jsx
//
// Shared list component for the Jobs and Activity pages. Both pages are
// thin wrappers that pre-filter `kinds` and supply a title; everything
// else (filters, tabs, list, detail drawer, live updates) lives here.
//
// Reads from /api/jobs/* (Phase 3) and listens to the canonical jobs:*
// socket channel via UnifiedProgressContext.unifiedJobs (Phase 4).
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Box,
  Paper,
  Typography,
  Stack,
  Chip,
  CircularProgress,
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
} from "@mui/material";
import {
  Close as CloseIcon,
  Refresh as RefreshIcon,
  Search as SearchIcon,
} from "@mui/icons-material";
import {
  listJobs,
  listJobHistory,
  jobsSummary,
  JOB_STATUSES,
} from "../../api/jobsService";
import { useUnifiedProgress } from "../../contexts/UnifiedProgressContext";

const STATUS_COLORS = {
  pending: "default",
  running: "info",
  paused: "warning",
  completed: "success",
  failed: "error",
  cancelled: "default",
};

const _formatDuration = (seconds) => {
  if (seconds == null) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
  return `${(seconds / 3600).toFixed(1)}h`;
};

const _formatRelative = (iso) => {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    const diff = (Date.now() - d.getTime()) / 1000;
    if (diff < 60) return "just now";
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return d.toLocaleDateString();
  } catch {
    return iso;
  }
};

const JobsList = ({ title, subtitle, kinds }) => {
  const { unifiedJobs } = useUnifiedProgress();

  const [tab, setTab] = useState(0);  // 0 = Active, 1 = History
  const [statusFilter, setStatusFilter] = useState(new Set());
  const [search, setSearch] = useState("");
  const [activeRows, setActiveRows] = useState([]);
  const [historyRows, setHistoryRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [summary, setSummary] = useState(null);
  const [selected, setSelected] = useState(null);

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
      setError(e.response?.data?.error || e.message || "Failed to load jobs");
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
      setError(e.response?.data?.error || e.message || "Failed to load history");
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
        <Box>
          <Typography variant="h5" fontWeight="bold">{title}</Typography>
          {subtitle && (
            <Typography variant="body2" color="text.secondary">{subtitle}</Typography>
          )}
        </Box>

        {/* Status chips — click to filter */}
        <Paper elevation={2} sx={{ p: 2, borderRadius: 2 }}>
          <Stack direction="row" spacing={1} flexWrap="wrap" sx={{ gap: 1 }}>
            {JOB_STATUSES.map((s) => {
              const count = statusCounts[s] || 0;
              const active = statusFilter.has(s);
              return (
                <Chip
                  key={s}
                  label={`${s} (${count})`}
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
              placeholder="Search by label, kind, status..."
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
              title="Refresh"
            >
              <RefreshIcon fontSize="small" />
            </IconButton>
          </Stack>
        </Paper>

        {/* Tabs: Active / History */}
        <Paper elevation={2} sx={{ borderRadius: 2 }}>
          <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ borderBottom: 1, borderColor: "divider" }}>
            <Tab label={`Active (${activeRows.length})`} />
            <Tab label={`History (${historyRows.length})`} />
          </Tabs>

          {error && <Alert severity="error" sx={{ m: 2 }}>{error}</Alert>}
          {loading && <LinearProgress />}

          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell sx={{ fontWeight: 600 }}>Label</TableCell>
                <TableCell sx={{ fontWeight: 600 }}>Kind</TableCell>
                <TableCell sx={{ fontWeight: 600 }}>Status</TableCell>
                <TableCell sx={{ fontWeight: 600, width: 200 }}>Progress</TableCell>
                <TableCell sx={{ fontWeight: 600 }}>Duration</TableCell>
                <TableCell sx={{ fontWeight: 600 }}>{tab === 0 ? "Started" : "Finished"}</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {visibleRows.length === 0 && !loading && (
                <TableRow>
                  <TableCell colSpan={6}>
                    <Typography variant="body2" color="text.secondary" sx={{ p: 2, textAlign: "center" }}>
                      {tab === 0 ? "Nothing currently running." : "No history yet."}
                    </Typography>
                  </TableCell>
                </TableRow>
              )}
              {visibleRows.map((j) => (
                <TableRow
                  key={j.id}
                  hover
                  onClick={() => setSelected(j)}
                  sx={{ cursor: "pointer" }}
                >
                  <TableCell>{j.label}</TableCell>
                  <TableCell>
                    <Chip label={j.kind} size="small" variant="outlined" />
                  </TableCell>
                  <TableCell>
                    <Chip label={j.status} color={STATUS_COLORS[j.status] || "default"} size="small" />
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

      {/* Detail drawer */}
      <Drawer anchor="right" open={!!selected} onClose={() => setSelected(null)}>
        <Box sx={{ width: 420, p: 3 }}>
          <Stack direction="row" justifyContent="space-between" alignItems="center" mb={2}>
            <Typography variant="h6" fontWeight="bold">Job detail</Typography>
            <IconButton onClick={() => setSelected(null)} size="small">
              <CloseIcon />
            </IconButton>
          </Stack>
          {selected && (
            <Stack spacing={2}>
              <Box>
                <Typography variant="caption" color="text.secondary">ID</Typography>
                <Typography variant="body2" sx={{ fontFamily: "monospace" }}>{selected.id}</Typography>
              </Box>
              <Box>
                <Typography variant="caption" color="text.secondary">Label</Typography>
                <Typography variant="body2">{selected.label}</Typography>
              </Box>
              <Stack direction="row" spacing={2}>
                <Box>
                  <Typography variant="caption" color="text.secondary">Kind</Typography>
                  <Typography variant="body2">{selected.kind}</Typography>
                </Box>
                <Box>
                  <Typography variant="caption" color="text.secondary">Status</Typography>
                  <Chip label={selected.status} color={STATUS_COLORS[selected.status]} size="small" />
                </Box>
              </Stack>
              <Box>
                <Typography variant="caption" color="text.secondary">Progress</Typography>
                <Typography variant="body2">{selected.progress != null ? `${Math.round(selected.progress)}%` : "—"}</Typography>
              </Box>
              <Stack direction="row" spacing={2}>
                <Box>
                  <Typography variant="caption" color="text.secondary">Started</Typography>
                  <Typography variant="body2">{_formatRelative(selected.started_at)}</Typography>
                </Box>
                {selected.finished_at && (
                  <Box>
                    <Typography variant="caption" color="text.secondary">Finished</Typography>
                    <Typography variant="body2">{_formatRelative(selected.finished_at)}</Typography>
                  </Box>
                )}
                <Box>
                  <Typography variant="caption" color="text.secondary">Duration</Typography>
                  <Typography variant="body2">{_formatDuration(selected.duration_s)}</Typography>
                </Box>
              </Stack>
              {selected.error_message && (
                <Box>
                  <Typography variant="caption" color="error.main">Error</Typography>
                  <Typography variant="body2" sx={{ whiteSpace: "pre-wrap" }}>{selected.error_message}</Typography>
                </Box>
              )}
              {selected.metadata && Object.keys(selected.metadata).length > 0 && (
                <Box>
                  <Typography variant="caption" color="text.secondary">Metadata</Typography>
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
              {/* Cancel button placeholder — wired in Phase 7 */}
              {selected.cancellable && (
                <Alert severity="info" variant="outlined" sx={{ fontSize: "0.8rem" }}>
                  Cancel-by-id transport ships in Tasks/Jobs Phase 7.
                </Alert>
              )}
            </Stack>
          )}
        </Box>
      </Drawer>
    </Box>
  );
};

export default JobsList;
