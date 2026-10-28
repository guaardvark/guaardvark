import React, { useState, useEffect, useCallback } from "react";
import {
  Box,
  Typography,
  Chip,
  Stack,
  Button,
  LinearProgress,
  Divider,
  CircularProgress,
  List,
  ListItem,
  ListItemText,
  ListItemIcon,
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import {
  Psychology as PsychologyIcon,
  PlayArrow as PlayIcon,
  CheckCircle as CheckIcon,
  Error as ErrorIcon,
  Schedule as ScheduleIcon,
} from "@mui/icons-material";
import DashboardCardWrapper from "./DashboardCardWrapper";
import { StatusChip, UNCLE_GOLD, getFamilyColors } from "../../utils/familyColors";
import { claudeAdvisorService } from "../../api/claudeAdvisorService";
import { selfImprovementService } from "../../api/selfImprovementService";
import io from "socket.io-client";
import { uiLocale, useUiTranslation } from "../../i18n";

// Stage color mapping
const STAGE_COLORS = {
  starting: "info",
  testing: "warning",
  analyzed: "info",
  fixing: "warning",
  complete: "success",
  error: "error",
};

const FamilySelfImprovementCard = React.forwardRef(
  (
    {
      style,
      isMinimized,
      onToggleMinimize,
      cardColor,
      onCardColorChange,
      ...props
    },
    ref,
  ) => {
    const { t } = useUiTranslation();
    const theme = useTheme();
    const _colors = getFamilyColors(theme);
    const [claudeStatus, setClaudeStatus] = useState(null);
    const [siStatus, setSiStatus] = useState(null);
    const [runs, setRuns] = useState([]);
    const [loading, setLoading] = useState(true);
    const [progress, setProgress] = useState(null); // { stage, detail, progress, ... }

    const fetchData = useCallback(async () => {
      try {
        const [cRes, sRes, rRes] = await Promise.allSettled([
          claudeAdvisorService.getStatus(),
          selfImprovementService.getStatus(),
          selfImprovementService.getRuns(5, 0),
        ]);
        if (cRes.status === "fulfilled") setClaudeStatus(cRes.value?.data);
        if (sRes.status === "fulfilled") setSiStatus(sRes.value?.data);
        if (rRes.status === "fulfilled") setRuns(rRes.value?.data?.runs || []);
      } catch (err) {
        console.error("Failed to fetch family status:", err);
      } finally {
        setLoading(false);
      }
    }, []);

    useEffect(() => {
      fetchData();
      const interval = setInterval(fetchData, 30000);
      return () => clearInterval(interval);
    }, [fetchData]);

    // Listen for real-time progress events
    useEffect(() => {
      const socket = io({ path: "/socket.io", transports: ["polling", "websocket"] });
      socket.on("self_improvement_progress", (data) => {
        setProgress(data);
        // When complete or error, refresh data after a short delay and clear progress
        if (data.stage === "complete" || data.stage === "error") {
          setTimeout(() => {
            fetchData();
            setProgress(null);
          }, 3000);
        }
      });
      return () => {
        socket.off("self_improvement_progress");
        socket.disconnect();
      };
    }, [fetchData]);

    const handleTrigger = async () => {
      try {
        setProgress({ stage: "starting", detail: t("dashboard.family.queuing"), progress: 0 });
        await selfImprovementService.triggerRun();
        setTimeout(fetchData, 3000);
      } catch (err) {
        console.error("Trigger failed:", err);
        setProgress(null);
      }
    };

    const usage = claudeStatus?.usage || {};
    const isRunning = progress && progress.stage !== "complete" && progress.stage !== "error";
    const canRunCheck = siStatus?.enabled && !siStatus?.codebase_locked && !isRunning;

    return (
      <DashboardCardWrapper
        ref={ref}
        style={style}
        isMinimized={isMinimized}
        onToggleMinimize={onToggleMinimize}
        cardColor={cardColor}
        onCardColorChange={onCardColorChange}
        title={t("dashboard.family.title")}
        titleBarActions={
          <PsychologyIcon fontSize="small" sx={{ color: UNCLE_GOLD, opacity: 0.8 }} />
        }
        {...props}
        contextMenuActions={[
          { label: t("common.refresh"), onClick: fetchData },
          canRunCheck && { label: t("dashboard.family.runCheck"), onClick: handleTrigger },
        ]}
      >
        {loading ? (
          <Box sx={{ display: "flex", justifyContent: "center", py: 2 }}>
            <CircularProgress size={22} />
          </Box>
        ) : (
          <Box sx={{ display: "flex", flexDirection: "column", gap: 1, pt: 0.5 }}>
            {/* Status Chips */}
            <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
              <StatusChip
                source="uncle_claude"
                status={claudeStatus?.available ? "connected" : "offline"}
                label={claudeStatus?.available ? t("dashboard.family.uncleOnline") : t("dashboard.family.uncleOffline")}
              />
              <StatusChip
                source="self_improvement"
                status={siStatus?.enabled ? "enabled" : "disabled"}
                label={siStatus?.enabled ? t("dashboard.family.siOn") : t("dashboard.family.siOff")}
              />
              {siStatus?.codebase_locked && (
                <StatusChip source="nephew" status="locked" label={t("dashboard.family.locked")} />
              )}
            </Stack>

            {/* Self-Check Progress Bar */}
            {progress && (
              <Box sx={{ mt: 0.5 }}>
                <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", mb: 0.5 }}>
                  <Typography variant="caption" fontWeight="bold" color={`${STAGE_COLORS[progress.stage] || "info"}.main`}>
                    {progress.stage === "starting" && t("dashboard.family.stages.starting")}
                    {progress.stage === "testing" && t("dashboard.family.stages.testing")}
                    {progress.stage === "analyzed" && t("dashboard.family.stages.analyzed")}
                    {progress.stage === "fixing" && t("dashboard.family.stages.fixing")}
                    {progress.stage === "complete" && t("dashboard.family.stages.complete")}
                    {progress.stage === "error" && t("dashboard.family.stages.error")}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {Math.round((progress.progress || 0) * 100)}%
                  </Typography>
                </Box>
                <LinearProgress
                  variant={isRunning ? "determinate" : "determinate"}
                  value={Math.round((progress.progress || 0) * 100)}
                  color={STAGE_COLORS[progress.stage] || "info"}
                  sx={{ height: 6, borderRadius: 3 }}
                />
                <Typography variant="caption" color="text.secondary" sx={{ mt: 0.25, display: "block" }}>
                  {progress.detail}
                </Typography>
              </Box>
            )}

            {/* Token Budget Mini Bar */}
            {claudeStatus?.available && (
              <Box>
                <Typography variant="caption" color="text.secondary">
                  {t("dashboard.family.tokens", { percent: usage.budget_used_percent || 0 })}
                </Typography>
                <LinearProgress
                  variant="determinate"
                  value={Math.min(usage.budget_used_percent || 0, 100)}
                  sx={{
                    height: 4,
                    borderRadius: 2,
                    bgcolor: "action.hover",
                    "& .MuiLinearProgress-bar": { bgcolor: UNCLE_GOLD },
                  }}
                />
              </Box>
            )}

            {/* Fixes count */}
            {(siStatus?.fixes_applied || 0) > 0 && (
              <Typography variant="caption" color="text.secondary">
                {t("dashboard.family.totalFixes", { count: siStatus.fixes_applied })}
              </Typography>
            )}

            <Divider />

            {/* Recent Runs */}
            <Typography variant="caption" fontWeight="bold">
              {t("dashboard.family.recentActivity")}
            </Typography>
            {runs.length === 0 ? (
              <Typography variant="caption" color="text.secondary">
                {t("dashboard.family.noRuns")}
              </Typography>
            ) : (
              <List dense disablePadding>
                {runs.slice(0, 3).map((run) => (
                  <ListItem key={run.id} disablePadding sx={{ py: 0.25 }}>
                    <ListItemIcon sx={{ minWidth: 24 }}>
                      {run.status === "success" ? (
                        <CheckIcon fontSize="small" color="success" />
                      ) : run.status === "failed" ? (
                        <ErrorIcon fontSize="small" color="error" />
                      ) : (
                        <ScheduleIcon fontSize="small" color="warning" />
                      )}
                    </ListItemIcon>
                    <ListItemText
                      primary={
                        <Typography variant="caption">
                          {run.trigger} — {run.status}
                        </Typography>
                      }
                      secondary={
                        <Typography variant="caption" color="text.secondary">
                          {run.timestamp ? new Date(run.timestamp).toLocaleString(uiLocale()) : ""}
                        </Typography>
                      }
                    />
                    {run.uncle_reviewed && (
                      <Chip label={t("dashboard.family.reviewed")} size="small" sx={{ bgcolor: UNCLE_GOLD, color: "#000", fontSize: "0.6rem", height: 18 }} />
                    )}
                  </ListItem>
                ))}
              </List>
            )}

            {/* Quick Actions */}
            {canRunCheck && (
              <Button
                size="small"
                variant="outlined"
                onClick={handleTrigger}
                startIcon={<PlayIcon />}
                sx={{ alignSelf: "flex-start" }}
              >
                {t("dashboard.family.runCheck")}
              </Button>
            )}
          </Box>
        )}
      </DashboardCardWrapper>
    );
  },
);

FamilySelfImprovementCard.displayName = "FamilySelfImprovementCard";
export default FamilySelfImprovementCard;
