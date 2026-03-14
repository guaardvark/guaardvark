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
              startIcon={<PlayIcon />}
            >
              Run Self-Check
            </Button>
          )}
        </SettingsRow>
      </Wrapper>

      {siStatus?.codebase_locked && (
        <Alert severity="warning" variant="outlined" sx={{ mt: 2 }}>
          Codebase is locked. Autonomous edits are blocked.
        </Alert>
      )}
    </Box>
  );
}
