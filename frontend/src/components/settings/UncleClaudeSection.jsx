import React, { useState, useEffect, useCallback } from "react";
import {
  Box,
  Typography,
  Switch,
  Button,
  LinearProgress,
  Select,
  MenuItem,
  FormControl,
  Alert,
  CircularProgress,
  Stack,
  Tooltip,
} from "@mui/material";
import {
  Psychology as PsychologyIcon,
  Shield as ShieldIcon,
  Lock as LockIcon,
  LockOpen as LockOpenIcon,
  PlayArrow as PlayIcon,
} from "@mui/icons-material";
import SettingsSection from "./SettingsSection";
import SettingsRow from "./SettingsRow";
import { StatusChip, UNCLE_GOLD } from "../../utils/familyColors";
import { claudeAdvisorService } from "../../api/claudeAdvisorService";
import { selfImprovementService } from "../../api/selfImprovementService";

export default function UncleClaudeSection({ compact = false }) {
  const [status, setStatus] = useState(null);
  const [siStatus, setSiStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [testResult, setTestResult] = useState(null);
  const [testing, setTesting] = useState(false);
  const [selfCheckRunning, setSelfCheckRunning] = useState(false);
  const [selfCheckResult, setSelfCheckResult] = useState(null);

  const fetchStatus = useCallback(async () => {
    try {
      const [claudeRes, siRes] = await Promise.allSettled([
        claudeAdvisorService.getStatus(),
        selfImprovementService.getStatus(),
      ]);
      if (claudeRes.status === "fulfilled") setStatus(claudeRes.value?.data);
      if (siRes.status === "fulfilled") setSiStatus(siRes.value?.data);
    } catch (err) {
      console.error("Failed to fetch Uncle Claude status:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  const handleTestConnection = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const res = await claudeAdvisorService.testConnection();
      setTestResult({ success: true, message: res?.data?.response || "Connected" });
    } catch (err) {
      setTestResult({ success: false, message: err.message || "Connection failed" });
    } finally {
      setTesting(false);
    }
  };

  const handleEscalationModeChange = async (e) => {
    try {
      await claudeAdvisorService.updateConfig({ escalation_mode: e.target.value });
      fetchStatus();
    } catch (err) {
      console.error("Failed to update escalation mode:", err);
    }
  };

  const handleToggleSelfImprovement = async () => {
    try {
      await selfImprovementService.toggle(!siStatus?.enabled);
      fetchStatus();
    } catch (err) {
      console.error("Failed to toggle self-improvement:", err);
    }
  };

  const handleToggleCodebaseLock = async () => {
    try {
      await selfImprovementService.lockCodebase(!siStatus?.codebase_locked);
      fetchStatus();
    } catch (err) {
      console.error("Failed to toggle codebase lock:", err);
    }
  };

  const handleTriggerRun = async () => {
    setSelfCheckRunning(true);
    setSelfCheckResult(null);
    try {
      const res = await selfImprovementService.triggerRun();
      const taskId = res?.data?.task_id;
      // Poll for completion by checking if a new run appears
      const startTime = Date.now();
      const pollInterval = setInterval(async () => {
        try {
          const runsRes = await selfImprovementService.getRuns(1, 0);
          const latestRun = runsRes?.data?.runs?.[0];
          if (latestRun && new Date(latestRun.timestamp).getTime() > startTime - 5000) {
            // New run appeared — show results
            clearInterval(pollInterval);
            setSelfCheckRunning(false);
            setSelfCheckResult(latestRun);
            fetchStatus();
          } else if (Date.now() - startTime > 120000) {
            // Timeout after 2 minutes
            clearInterval(pollInterval);
            setSelfCheckRunning(false);
            setSelfCheckResult({ status: "timeout", error_message: "Self-check timed out after 2 minutes. Check logs for details." });
          }
        } catch (pollErr) {
          console.error("Poll error:", pollErr);
        }
      }, 3000);
    } catch (err) {
      console.error("Failed to trigger self-improvement:", err);
      setSelfCheckRunning(false);
      setSelfCheckResult({ status: "error", error_message: err.message || "Failed to dispatch self-check" });
    }
  };

  if (loading) {
    return (
      <Box sx={{ py: 2 }}>
        <CircularProgress size={24} />
      </Box>
    );
  }

  const usage = status?.usage || {};
  const budgetPercent = usage.budget_used_percent || 0;

  const Wrapper = compact ? Box : SettingsSection;
  const wrapperProps = (title) => compact ? {} : { title };

  return (
    <Box sx={compact ? {} : { mt: 3 }}>
      <Wrapper {...wrapperProps("UNCLE CLAUDE (MENTOR API)")}>
        {/* Connection Status */}
        <SettingsRow label="Connection" icon={<PsychologyIcon />}>
          <Stack direction="row" spacing={1} alignItems="center">
            <StatusChip
              source="uncle_claude"
              status={status?.available ? "connected" : "offline"}
              label={status?.available ? "Connected" : "Offline"}
            />
            {status?.model && (
              <Typography variant="caption" color="text.secondary">
                {status.model}
              </Typography>
            )}
            <Button
              size="small"
              variant="outlined"
              onClick={handleTestConnection}
              disabled={testing}
              startIcon={testing ? <CircularProgress size={14} /> : <PlayIcon />}
              sx={{ ml: 1 }}
            >
              Test
            </Button>
          </Stack>
        </SettingsRow>

        {testResult && (
          <Alert
            severity={testResult.success ? "success" : "error"}
            sx={{ my: 1 }}
            onClose={() => setTestResult(null)}
          >
            {testResult.message}
          </Alert>
        )}

        {/* Token Budget */}
        <SettingsRow label="Token Budget" stacked>
          <Box>
            <Stack direction="row" justifyContent="space-between" sx={{ mb: 0.5 }}>
              <Typography variant="caption">
                {(usage.total_tokens || 0).toLocaleString()} / {(usage.monthly_budget || 0).toLocaleString()}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                {budgetPercent}% used
              </Typography>
            </Stack>
            <LinearProgress
              variant="determinate"
              value={Math.min(budgetPercent, 100)}
              sx={{
                height: 6,
                borderRadius: 3,
                bgcolor: "action.hover",
                "& .MuiLinearProgress-bar": {
                  bgcolor: budgetPercent > 80 ? "error.main" : budgetPercent > 50 ? "warning.main" : UNCLE_GOLD,
                },
              }}
            />
          </Box>
        </SettingsRow>

        {/* Escalation Mode */}
        <SettingsRow label="Escalation Mode">
          <FormControl size="small" sx={{ minWidth: 180 }}>
            <Select
              value={status?.escalation_mode || "manual"}
              onChange={handleEscalationModeChange}
            >
              <MenuItem value="manual">Manual (user triggers)</MenuItem>
              <MenuItem value="smart">Smart (auto when local fails)</MenuItem>
              <MenuItem value="always">Always (every query)</MenuItem>
            </Select>
          </FormControl>
        </SettingsRow>
      </Wrapper>

      <Wrapper {...wrapperProps("SELF-IMPROVEMENT & KILL SWITCH")} sx={compact ? { mt: 2 } : { mt: 3 }}>
        {/* Self-Improvement Toggle */}
        <SettingsRow label="Self-Improvement" icon={<ShieldIcon />}>
          <Switch
            checked={siStatus?.enabled || false}
            onChange={handleToggleSelfImprovement}
            color="primary"
          />
        </SettingsRow>

        {/* Codebase Lock */}
        <SettingsRow label="Codebase Protection">
          <Stack direction="row" spacing={1} alignItems="center">
            <StatusChip
              source="nephew"
              status={siStatus?.codebase_locked ? "locked" : "enabled"}
              label={siStatus?.codebase_locked ? "Locked" : "Unlocked"}
            />
            <Tooltip title={siStatus?.codebase_locked ? "Unlock to allow autonomous edits" : "Lock to prevent autonomous edits"}>
              <Button
                size="small"
                variant={siStatus?.codebase_locked ? "contained" : "outlined"}
                color={siStatus?.codebase_locked ? "error" : "primary"}
                onClick={handleToggleCodebaseLock}
                startIcon={siStatus?.codebase_locked ? <LockOpenIcon /> : <LockIcon />}
              >
                {siStatus?.codebase_locked ? "Unlock" : "Lock"}
              </Button>
            </Tooltip>
          </Stack>
        </SettingsRow>

        {/* Trigger Self-Check */}
        <SettingsRow
          label={
            siStatus?.last_run
              ? `Last run: ${new Date(siStatus.last_run.timestamp).toLocaleString()} (${siStatus.last_run.status}) | Fixes: ${siStatus.total_fixes || 0}`
              : "No runs yet"
          }
        >
          {siStatus?.enabled && !siStatus?.codebase_locked && (
            <Button
              size="small"
              variant="outlined"
              onClick={handleTriggerRun}
              disabled={selfCheckRunning}
              startIcon={selfCheckRunning ? <CircularProgress size={14} /> : <PlayIcon />}
            >
              {selfCheckRunning ? "Running..." : "Run Self-Check"}
            </Button>
          )}
        </SettingsRow>

        {/* Self-Check Output */}
        {(selfCheckRunning || selfCheckResult) && (
          <Box sx={{
            mt: 1, mx: 1, p: 1.5,
            bgcolor: "background.default",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            fontFamily: "monospace",
            fontSize: "0.8rem",
            maxHeight: 300,
            overflow: "auto",
          }}>
            {selfCheckRunning && (
              <Box sx={{ display: "flex", alignItems: "center", gap: 1, color: "text.secondary" }}>
                <CircularProgress size={14} />
                <Typography variant="body2" fontFamily="monospace" fontSize="0.8rem">
                  Self-check in progress... Running tests and analyzing codebase.
                </Typography>
              </Box>
            )}
            {selfCheckResult && (
              <Box>
                <Box sx={{ display: "flex", justifyContent: "space-between", mb: 1 }}>
                  <Typography variant="body2" fontFamily="monospace" fontSize="0.8rem" fontWeight="bold"
                    color={selfCheckResult.status === "success" ? "success.main" : selfCheckResult.status === "error" || selfCheckResult.status === "timeout" ? "error.main" : "warning.main"}>
                    Status: {selfCheckResult.status?.toUpperCase()}
                  </Typography>
                  {selfCheckResult.duration_seconds && (
                    <Typography variant="body2" fontFamily="monospace" fontSize="0.8rem" color="text.secondary">
                      {selfCheckResult.duration_seconds.toFixed(1)}s
                    </Typography>
                  )}
                  <Button size="small" onClick={() => setSelfCheckResult(null)} sx={{ minWidth: 0, p: 0.5 }}>
                    ✕
                  </Button>
                </Box>

                {selfCheckResult.error_message && (
                  <Typography variant="body2" fontFamily="monospace" fontSize="0.8rem" color="error.main" sx={{ mb: 1 }}>
                    Error: {selfCheckResult.error_message}
                  </Typography>
                )}

                {selfCheckResult.test_results_before && (
                  <Box sx={{ mb: 1 }}>
                    <Typography variant="body2" fontFamily="monospace" fontSize="0.8rem" color="text.secondary">
                      Tests: {selfCheckResult.test_results_before.return_code === 0 ? "✓ PASSED" : `✗ FAILED (${selfCheckResult.test_results_before.total_failures} failures)`}
                    </Typography>
                    {selfCheckResult.test_results_before.failures?.length > 0 && (
                      <Box sx={{ pl: 2, mt: 0.5 }}>
                        {selfCheckResult.test_results_before.failures.map((f, i) => (
                          <Typography key={i} variant="body2" fontFamily="monospace" fontSize="0.75rem" color="error.light">
                            • {typeof f === "string" ? f : f.test || f.name || JSON.stringify(f)}
                          </Typography>
                        ))}
                      </Box>
                    )}
                  </Box>
                )}

                {selfCheckResult.changes_made?.length > 0 ? (
                  <Box sx={{ mb: 1 }}>
                    <Typography variant="body2" fontFamily="monospace" fontSize="0.8rem" color="text.secondary">
                      Changes ({selfCheckResult.changes_made.length}):
                    </Typography>
                    {selfCheckResult.changes_made.map((c, i) => (
                      <Typography key={i} variant="body2" fontFamily="monospace" fontSize="0.75rem" color="info.light" sx={{ pl: 2 }}>
                        • {typeof c === "string" ? c : c.file || c.description || JSON.stringify(c)}
                      </Typography>
                    ))}
                  </Box>
                ) : selfCheckResult.status === "success" ? (
                  <Typography variant="body2" fontFamily="monospace" fontSize="0.8rem" color="text.secondary">
                    No issues found. Codebase is healthy.
                  </Typography>
                ) : null}

                {selfCheckResult.uncle_reviewed && selfCheckResult.uncle_feedback && (
                  <Box sx={{ mt: 1, pt: 1, borderTop: "1px solid", borderColor: "divider" }}>
                    <Typography variant="body2" fontFamily="monospace" fontSize="0.8rem" color={UNCLE_GOLD}>
                      Uncle Claude: {selfCheckResult.uncle_feedback}
                    </Typography>
                  </Box>
                )}
              </Box>
            )}
          </Box>
        )}
      </Wrapper>

      {siStatus?.codebase_locked && (
        <Alert severity="warning" variant="outlined" sx={{ mt: 2 }}>
          Codebase is locked. Autonomous edits are blocked.
        </Alert>
      )}
    </Box>
  );
}
