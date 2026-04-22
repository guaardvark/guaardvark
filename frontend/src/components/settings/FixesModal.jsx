import React, { useState, useEffect, useCallback, useMemo } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Box,
  Stack,
  Chip,
  Typography,
  CircularProgress,
  Alert,
  List,
  ListItemButton,
  ListItemText,
  Divider,
  IconButton,
  Tooltip,
} from "@mui/material";
import {
  Close as CloseIcon,
  CheckCircle as CheckCircleIcon,
  Cancel as CancelIcon,
  PlayArrow as PlayIcon,
  Refresh as RefreshIcon,
  BugReport as BugReportIcon,
} from "@mui/icons-material";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { a11yDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { selfImprovementService } from "../../api/selfImprovementService";

// severity → chip color. The backend uses critical/high/medium/low; anything else falls to default.
const SEVERITY_COLOR = {
  critical: "error",
  high: "error",
  medium: "warning",
  low: "info",
};

// status → chip color. "applied" is the happy ending, "rejected" the sad one.
const STATUS_COLOR = {
  proposed: "default",
  triaged: "info",
  approved: "primary",
  applied: "success",
  rejected: "error",
};

const STATUS_FILTERS = ["all", "proposed", "approved", "applied", "rejected"];

function shortPath(fullPath) {
  if (!fullPath) return "";
  const parts = fullPath.split("/");
  return parts.length > 2 ? `…/${parts.slice(-2).join("/")}` : fullPath;
}

function formatRelative(iso) {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  const diffSec = Math.floor((Date.now() - then) / 1000);
  if (diffSec < 60) return `${diffSec}s ago`;
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
  return `${Math.floor(diffSec / 86400)}d ago`;
}

export default function FixesModal({ open, onClose, showMessage }) {
  const [fixes, setFixes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [statusFilter, setStatusFilter] = useState("all");
  const [selectedId, setSelectedId] = useState(null);
  const [actionBusy, setActionBusy] = useState(false);

  const fetchFixes = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await selfImprovementService.listPendingFixes({ limit: 100 });
      const data = Array.isArray(res?.data) ? res.data : [];
      setFixes(data);
    } catch (err) {
      setError(err?.message || "Failed to load pending fixes");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!open) {
      setSelectedId(null);
      setStatusFilter("all");
      return;
    }
    fetchFixes();
  }, [open, fetchFixes]);

  const filtered = useMemo(() => {
    if (statusFilter === "all") return fixes;
    return fixes.filter((f) => f.status === statusFilter);
  }, [fixes, statusFilter]);

  const selected = useMemo(
    () => fixes.find((f) => f.id === selectedId) || null,
    [fixes, selectedId],
  );

  const notify = (msg, sev = "success") => {
    if (showMessage) showMessage(msg, sev);
  };

  const handleApprove = async (fix) => {
    setActionBusy(true);
    try {
      await selfImprovementService.approveFix(fix.id);
      notify("Fix approved");
      await fetchFixes();
    } catch (err) {
      notify(err?.message || "Approve failed", "error");
    } finally {
      setActionBusy(false);
    }
  };

  const handleReject = async (fix) => {
    setActionBusy(true);
    try {
      await selfImprovementService.rejectFix(fix.id);
      notify("Fix rejected");
      await fetchFixes();
    } catch (err) {
      notify(err?.message || "Reject failed", "error");
    } finally {
      setActionBusy(false);
    }
  };

  const handleApply = async (fix) => {
    setActionBusy(true);
    try {
      await selfImprovementService.applyFix(fix.id);
      notify("Fix applied to filesystem");
      await fetchFixes();
    } catch (err) {
      notify(err?.message || "Apply failed", "error");
    } finally {
      setActionBusy(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="lg" fullWidth>
      <DialogTitle sx={{ display: "flex", alignItems: "center", gap: 1, pr: 6 }}>
        <BugReportIcon fontSize="small" />
        <Typography variant="h6" component="span">
          Self-Improvement Fixes
        </Typography>
        <Typography variant="caption" color="text.secondary" sx={{ ml: 1 }}>
          {fixes.length} total
        </Typography>
        <Box sx={{ flex: 1 }} />
        <Tooltip title="Refresh">
          <IconButton size="small" onClick={fetchFixes} disabled={loading}>
            <RefreshIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <IconButton
          size="small"
          onClick={onClose}
          sx={{ position: "absolute", right: 8, top: 8 }}
        >
          <CloseIcon fontSize="small" />
        </IconButton>
      </DialogTitle>

      <DialogContent dividers sx={{ p: 0, height: "70vh", display: "flex", flexDirection: "column" }}>
        {/* Filter bar */}
        <Stack direction="row" spacing={1} sx={{ p: 1.5, borderBottom: 1, borderColor: "divider" }}>
          {STATUS_FILTERS.map((s) => {
            const count = s === "all" ? fixes.length : fixes.filter((f) => f.status === s).length;
            return (
              <Chip
                key={s}
                label={`${s} (${count})`}
                size="small"
                color={statusFilter === s ? "primary" : "default"}
                variant={statusFilter === s ? "filled" : "outlined"}
                onClick={() => setStatusFilter(s)}
                sx={{ textTransform: "capitalize" }}
              />
            );
          })}
        </Stack>

        {/* Main content — list + detail */}
        {loading ? (
          <Box sx={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
            <CircularProgress size={32} />
          </Box>
        ) : error ? (
          <Alert severity="error" sx={{ m: 2 }}>{error}</Alert>
        ) : filtered.length === 0 ? (
          <Box sx={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", p: 3 }}>
            <Typography variant="body2" color="text.secondary">
              {fixes.length === 0
                ? "No fixes yet. Run a self-check to generate some."
                : `No ${statusFilter} fixes.`}
            </Typography>
          </Box>
        ) : (
          <Box sx={{ flex: 1, display: "flex", minHeight: 0 }}>
            {/* Left — list */}
            <Box sx={{ width: 340, borderRight: 1, borderColor: "divider", overflowY: "auto" }}>
              <List disablePadding>
                {filtered.map((fix) => (
                  <React.Fragment key={fix.id}>
                    <ListItemButton
                      selected={selectedId === fix.id}
                      onClick={() => setSelectedId(fix.id)}
                    >
                      <ListItemText
                        primary={
                          <Stack direction="row" spacing={0.5} alignItems="center">
                            <Chip
                              label={fix.severity}
                              size="small"
                              color={SEVERITY_COLOR[fix.severity] || "default"}
                              sx={{ height: 18, fontSize: "0.65rem", textTransform: "uppercase" }}
                            />
                            <Typography variant="body2" noWrap sx={{ fontFamily: "monospace" }}>
                              {shortPath(fix.file_path)}
                            </Typography>
                          </Stack>
                        }
                        secondary={
                          <Stack direction="row" spacing={1} alignItems="center">
                            <Chip
                              label={fix.status}
                              size="small"
                              color={STATUS_COLOR[fix.status] || "default"}
                              sx={{ height: 16, fontSize: "0.6rem" }}
                            />
                            <Typography variant="caption" color="text.secondary">
                              {formatRelative(fix.created_at)}
                            </Typography>
                          </Stack>
                        }
                        secondaryTypographyProps={{ component: "div" }}
                      />
                    </ListItemButton>
                    <Divider />
                  </React.Fragment>
                ))}
              </List>
            </Box>

            {/* Right — detail */}
            <Box sx={{ flex: 1, overflowY: "auto", p: 2 }}>
              {!selected ? (
                <Typography variant="body2" color="text.secondary" sx={{ textAlign: "center", mt: 4 }}>
                  Pick a fix on the left to see the diff and what broke.
                </Typography>
              ) : (
                <Stack spacing={2}>
                  <Box>
                    <Typography variant="caption" color="text.secondary">File</Typography>
                    <Typography variant="body2" sx={{ fontFamily: "monospace", wordBreak: "break-all" }}>
                      {selected.file_path}
                    </Typography>
                  </Box>

                  <Stack direction="row" spacing={1}>
                    <Chip
                      label={`severity: ${selected.severity}`}
                      size="small"
                      color={SEVERITY_COLOR[selected.severity] || "default"}
                    />
                    <Chip
                      label={`status: ${selected.status}`}
                      size="small"
                      color={STATUS_COLOR[selected.status] || "default"}
                    />
                    {selected.run_id && (
                      <Chip label={`run #${selected.run_id}`} size="small" variant="outlined" />
                    )}
                  </Stack>

                  <Box>
                    <Typography variant="caption" color="text.secondary">What broke / why</Typography>
                    <Typography variant="body2" sx={{ whiteSpace: "pre-wrap" }}>
                      {selected.fix_description || "(no description)"}
                    </Typography>
                  </Box>

                  <Box>
                    <Typography variant="caption" color="text.secondary">Proposed diff</Typography>
                    <Box sx={{
                      border: 1, borderColor: "divider", borderRadius: 1, overflow: "hidden",
                      maxHeight: 340,
                    }}>
                      <SyntaxHighlighter
                        language="diff"
                        style={a11yDark}
                        customStyle={{ margin: 0, fontSize: "0.75rem", maxHeight: 340 }}
                        wrapLongLines
                      >
                        {selected.proposed_diff || "(diff unavailable)"}
                      </SyntaxHighlighter>
                    </Box>
                  </Box>

                  {selected.reviewed_by && (
                    <Box>
                      <Typography variant="caption" color="text.secondary">
                        Reviewed by {selected.reviewed_by}
                        {selected.reviewed_at ? ` • ${formatRelative(selected.reviewed_at)}` : ""}
                      </Typography>
                      {selected.review_notes && (
                        <Typography variant="body2" sx={{ whiteSpace: "pre-wrap", mt: 0.5 }}>
                          {selected.review_notes}
                        </Typography>
                      )}
                    </Box>
                  )}

                  {selected.applied_at && (
                    <Alert severity="success" variant="outlined">
                      Applied {formatRelative(selected.applied_at)}
                    </Alert>
                  )}
                </Stack>
              )}
            </Box>
          </Box>
        )}
      </DialogContent>

      <DialogActions sx={{ justifyContent: "space-between", px: 2 }}>
        <Typography variant="caption" color="text.secondary">
          {selected ? `Fix #${selected.id}` : "No fix selected"}
        </Typography>
        <Stack direction="row" spacing={1}>
          {selected && (selected.status === "proposed" || selected.status === "triaged") && (
            <>
              <Button
                size="small"
                color="error"
                startIcon={<CancelIcon />}
                disabled={actionBusy}
                onClick={() => handleReject(selected)}
              >
                Reject
              </Button>
              <Button
                size="small"
                variant="contained"
                color="primary"
                startIcon={<CheckCircleIcon />}
                disabled={actionBusy}
                onClick={() => handleApprove(selected)}
              >
                Approve
              </Button>
            </>
          )}
          {selected && selected.status === "approved" && (
            <Button
              size="small"
              variant="contained"
              color="success"
              startIcon={<PlayIcon />}
              disabled={actionBusy}
              onClick={() => handleApply(selected)}
            >
              Apply to Filesystem
            </Button>
          )}
          <Button size="small" onClick={onClose}>Close</Button>
        </Stack>
      </DialogActions>
    </Dialog>
  );
}
