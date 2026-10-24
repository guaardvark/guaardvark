import React, { useState, useEffect, useCallback } from "react";
import {
  Box,
  LinearProgress,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  Alert,
  CircularProgress,
} from "@mui/material";
import { PlayArrow as PlayIcon } from "@mui/icons-material";
import { useNavigate } from "react-router-dom";
import { UNCLE_GOLD } from "../../utils/familyColors";
import { ActionButton, Cluster, Hint, Line, Sep, SettingChip, StatusPill } from "./ui";
import { claudeAdvisorService } from "../../api/claudeAdvisorService";
import { formatUiError } from "../../utils/uiError";
import { uiLocale, useUiTranslation } from "../../i18n";

export default function UncleClaudeSection() {
  const { t } = useUiTranslation();
  const navigate = useNavigate();
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [testResult, setTestResult] = useState(null);
  const [testing, setTesting] = useState(false);
  // A failed status read or config save, said under the controls. Without it
  // a failed read showed "Not configured" and a failed save did nothing.
  const [failure, setFailure] = useState(null);

  const fetchStatus = useCallback(async () => {
    try {
      const [claudeRes] = await Promise.allSettled([claudeAdvisorService.getStatus()]);
      if (claudeRes.status === "fulfilled") {
        setStatus(claudeRes.value?.data);
      } else {
        setFailure(t("settingsAccess.uncleClaude.statusReadFailed", { error: formatUiError(claudeRes.reason) || t("settingsAccess.uncleClaude.requestFailed") }));
      }
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
      setTestResult({ success: true, message: res?.data?.response || t("settingsAccess.uncleClaude.connected") });
    } catch (err) {
      setTestResult({ success: false, message: err.message || t("settingsAccess.uncleClaude.connectionFailed") });
    } finally {
      setTesting(false);
    }
  };

  const handleEscalationModeChange = async (e) => {
    try {
      setFailure(null);
      await claudeAdvisorService.updateConfig({ escalation_mode: e.target.value });
      fetchStatus();
    } catch (err) {
      console.error("Failed to update escalation mode:", err);
      setFailure(t("settingsAccess.uncleClaude.escalationFailed", { error: formatUiError(err) || t("settingsAccess.uncleClaude.requestFailed") }));
    }
  };

  const handleToggleScheduledSends = async (next) => {
    try {
      setFailure(null);
      await claudeAdvisorService.updateConfig({ scheduled_sends: next });
      fetchStatus();
    } catch (err) {
      console.error("Failed to update scheduled sends:", err);
      setFailure(t("settingsAccess.uncleClaude.scheduledFailed", { error: formatUiError(err) || t("settingsAccess.uncleClaude.requestFailed") }));
    }
  };

  if (loading) {
    return <CircularProgress size={16} />;
  }

  const usage = status?.usage || {};
  const budgetPercent = usage.budget_used_percent || 0;
  const connection = !status && failure
    ? { tone: "error", label: t("settingsAccess.uncleClaude.statusUnknown") }
    : !status?.available
    ? { tone: "warn", label: t("settingsAccess.uncleClaude.notConfigured") }
    : testResult === null
      ? { tone: "ok", label: t("settingsAccess.uncleClaude.apiKeySet") }
      : testResult.success
        ? { tone: "ok", label: t("settingsAccess.uncleClaude.verified") }
        : { tone: "error", label: t("settingsAccess.uncleClaude.connectionFailed") };

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1.25 }}>
      <Cluster
        label="Uncle Claude"
        help={t("settingsAccess.uncleClaude.help")}
      >
        <Line>
          <StatusPill tone={connection.tone} label={connection.label} />
          {status?.model && <Hint>{status.model}</Hint>}
          <ActionButton
            onClick={handleTestConnection}
            loading={testing}
            disabled={!status?.available}
            startIcon={<PlayIcon />}
            tooltip={t("settingsAccess.uncleClaude.testTooltip")}
          >
            {t("settingsAccess.uncleClaude.testConnection")}
          </ActionButton>
          <Sep />
          <FormControl size="small" sx={{ minWidth: 210 }}>
            <InputLabel id="uncle-escalation-label">{t("settingsAccess.uncleClaude.escalation")}</InputLabel>
            <Select
              labelId="uncle-escalation-label"
              label={t("settingsAccess.uncleClaude.escalation")}
              value={status?.escalation_mode || "manual"}
              onChange={handleEscalationModeChange}
            >
              <MenuItem value="manual">{t("settingsAccess.uncleClaude.escalationManual")}</MenuItem>
              <MenuItem value="smart">{t("settingsAccess.uncleClaude.escalationSmart")}</MenuItem>
              <MenuItem value="always">{t("settingsAccess.uncleClaude.escalationAlways")}</MenuItem>
            </Select>
          </FormControl>
          <SettingChip
            label={t("settingsAccess.uncleClaude.scheduledSends")}
            on={!!status?.scheduled_sends}
            onToggle={handleToggleScheduledSends}
            disabled={!status?.available}
            tooltip={t("settingsAccess.uncleClaude.scheduledTooltip")}
          />
        </Line>
        {failure && (
          <Alert severity="error" sx={{ py: 0.25 }} onClose={() => setFailure(null)}>
            {failure}
          </Alert>
        )}
        {testResult && (
          <Alert severity={testResult.success ? "success" : "error"} sx={{ py: 0.25 }} onClose={() => setTestResult(null)}>
            {testResult.message}
          </Alert>
        )}
        <Line>
          <Hint>
            {t("settingsAccess.uncleClaude.usage", {
              used: (usage.total_tokens || 0).toLocaleString(uiLocale()),
              budget: (usage.monthly_budget || 0).toLocaleString(uiLocale()),
              percent: budgetPercent,
            })}
          </Hint>
          <LinearProgress
            variant="determinate"
            value={Math.min(budgetPercent, 100)}
            sx={{
              width: 140,
              height: 5,
              borderRadius: 3,
              bgcolor: "action.hover",
              "& .MuiLinearProgress-bar": {
                bgcolor: budgetPercent > 80 ? "error.main" : budgetPercent > 50 ? "warning.main" : UNCLE_GOLD,
              },
            }}
          />
        </Line>
      </Cluster>

      <Cluster
        label={t("settingsAccess.uncleClaude.selfImprovement")}
        help={t("settingsAccess.uncleClaude.selfImprovementHelp")}
      >
        <Line>
          <ActionButton onClick={() => navigate("/self-improvement")}>
            {t("settingsAccess.uncleClaude.openSelfImprovement")}
          </ActionButton>
        </Line>
      </Cluster>
    </Box>
  );
}
