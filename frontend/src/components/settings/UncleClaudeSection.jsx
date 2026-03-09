import React, { useState, useEffect, useCallback } from "react";
import {
  Box,
  Typography,
  Switch,
  Button,
  Chip,
  LinearProgress,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  Alert,
  CircularProgress,
  Divider,
  Stack,
  Tooltip,
} from "@mui/material";
import {
  Psychology as PsychologyIcon,
  Shield as ShieldIcon,
  Lock as LockIcon,
  LockOpen as LockOpenIcon,
  PlayArrow as PlayIcon,
  CheckCircle as CheckIcon,
  Error as ErrorIcon,
  Warning as WarningIcon,
} from "@mui/icons-material";
import { claudeAdvisorService } from "../../api/claudeAdvisorService";
import { selfImprovementService } from "../../api/selfImprovementService";

export default function UncleClaudeSection() {
  const [status, setStatus] = useState(null);
  const [siStatus, setSiStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [testResult, setTestResult] = useState(null);
  const [testing, setTesting] = useState(false);

  const fetchStatus = useCallback(async () => {
    try {
      const [claudeRes, siRes] = await Promise.all([
        claudeAdvisorService.getStatus(),
        selfImprovementService.getStatus(),
      ]);
      setStatus(claudeRes?.data);
      setSiStatus(siRes?.data);
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
      const newEnabled = !(siStatus?.enabled);
      await selfImprovementService.toggle(newEnabled);
      fetchStatus();
    } catch (err) {
      console.error("Failed to toggle self-improvement:", err);
    }
  };

  const handleToggleCodebaseLock = async () => {
    try {
      const newLocked = !(siStatus?.codebase_locked);
      await selfImprovementService.lockCodebase(newLocked);
      fetchStatus();
    } catch (err) {
      console.error("Failed to toggle codebase lock:", err);
    }
  };

  const handleTriggerRun = async () => {
    try {
      await selfImprovementService.triggerRun();
      setTimeout(fetchStatus, 3000);
    } catch (err) {
      console.error("Failed to trigger self-improvement:", err);
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

  return (
    <Box sx={{ mt: 3 }}>
      <Divider sx={{ mb: 2 }} />
      <Typography variant="h6" sx={{ mb: 2, display: "flex", alignItems: "center", gap: 1 }}>
        <PsychologyIcon /> Uncle Claude (Mentor API)
      </Typography>

      {/* Connection Status */}
      <Stack direction="row" spacing={2} alignItems="center" sx={{ mb: 2 }}>
        <Chip
          label={status?.available ? "Connected" : "Offline"}
          color={status?.available ? "success" : "default"}
          size="small"
          icon={status?.available ? <CheckIcon /> : <ErrorIcon />}
        />
        {status?.model && (
          <Typography variant="body2" color="text.secondary">
            Model: {status.model}
          </Typography>
        )}
        <Button
          size="small"
          variant="outlined"
          onClick={handleTestConnection}
          disabled={testing}
          startIcon={testing ? <CircularProgress size={16} /> : <PlayIcon />}
        >
          Test Connection
        </Button>
      </Stack>

      {testResult && (
        <Alert severity={testResult.success ? "success" : "error"} sx={{ mb: 2 }} onClose={() => setTestResult(null)}>
          {testResult.message}
        </Alert>
      )}

      {/* Token Budget */}
      <Box sx={{ mb: 2 }}>
        <Typography variant="body2" gutterBottom>
          Token Budget: {(usage.total_tokens || 0).toLocaleString()} / {(usage.monthly_budget || 0).toLocaleString()}
        </Typography>
        <LinearProgress
          variant="determinate"
          value={Math.min(budgetPercent, 100)}
          color={budgetPercent > 80 ? "error" : budgetPercent > 50 ? "warning" : "primary"}
          sx={{ height: 8, borderRadius: 4 }}
        />
        <Typography variant="caption" color="text.secondary">
          {budgetPercent}% used this month
        </Typography>
      </Box>

      {/* Escalation Mode */}
      <FormControl size="small" sx={{ mb: 2, minWidth: 200 }}>
        <InputLabel>Escalation Mode</InputLabel>
        <Select
          value={status?.escalation_mode || "manual"}
          label="Escalation Mode"
          onChange={handleEscalationModeChange}
        >
          <MenuItem value="manual">Manual (user triggers)</MenuItem>
          <MenuItem value="smart">Smart (auto when local fails)</MenuItem>
          <MenuItem value="always">Always (every query)</MenuItem>
        </Select>
      </FormControl>

      <Divider sx={{ my: 2 }} />

      {/* Self-Improvement Controls */}
      <Typography variant="subtitle1" sx={{ mb: 1, display: "flex", alignItems: "center", gap: 1 }}>
        <ShieldIcon /> Self-Improvement & Kill Switch
      </Typography>

      <Stack spacing={1.5}>
        <Stack direction="row" alignItems="center" justifyContent="space-between">
          <Typography variant="body2">Self-Improvement</Typography>
          <Switch
            checked={siStatus?.enabled || false}
            onChange={handleToggleSelfImprovement}
            color="primary"
          />
        </Stack>

        <Stack direction="row" alignItems="center" justifyContent="space-between">
          <Stack direction="row" alignItems="center" spacing={1}>
            {siStatus?.codebase_locked ? <LockIcon color="error" /> : <LockOpenIcon color="success" />}
            <Typography variant="body2">
              Codebase {siStatus?.codebase_locked ? "Locked" : "Unlocked"}
            </Typography>
          </Stack>
          <Tooltip title={siStatus?.codebase_locked ? "Unlock codebase to allow autonomous edits" : "Lock codebase to prevent autonomous edits"}>
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

        {siStatus?.enabled && !siStatus?.codebase_locked && (
          <Button
            size="small"
            variant="outlined"
            onClick={handleTriggerRun}
            startIcon={<PlayIcon />}
          >
            Trigger Self-Check Now
          </Button>
        )}

        {siStatus?.last_run && (
          <Typography variant="caption" color="text.secondary">
            Last run: {new Date(siStatus.last_run.timestamp).toLocaleString()}
            ({siStatus.last_run.status}) | Total fixes: {siStatus.total_fixes || 0}
          </Typography>
        )}

        {siStatus?.codebase_locked && (
          <Alert severity="warning" variant="outlined" sx={{ mt: 1 }}>
            Codebase is locked. Autonomous edits are blocked. Use Settings or remove data/.codebase_lock to unlock.
          </Alert>
        )}
      </Stack>
    </Box>
  );
}
