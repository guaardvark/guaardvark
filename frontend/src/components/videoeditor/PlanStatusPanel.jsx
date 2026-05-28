import React, { useEffect, useMemo, useState } from "react";
import { Alert, Box, Chip, LinearProgress, Stack, Typography } from "@mui/material";

const ACTIVE_STATUSES = new Set(["submitting", "queued", "running"]);

const statusColor = (status) => {
  if (status === "done") return "success";
  if (status === "failed") return "error";
  if (ACTIVE_STATUSES.has(status)) return "primary";
  return "default";
};

const statusLabel = (status) => {
  if (status === "submitting") return "Submitting";
  if (status === "queued") return "Queued";
  if (status === "running") return "Running";
  if (status === "done") return "Ready";
  if (status === "failed") return "Failed";
  return "Idle";
};

const formatElapsed = (ms) => {
  if (!ms || ms < 0) return "0s";
  const seconds = Math.floor(ms / 1000);
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return minutes ? `${minutes}m ${rest}s` : `${rest}s`;
};

const PlanStatusPanel = ({
  planJob,
  canPlan,
  videoCount,
  hasMasterSong,
  warnings = [],
  compact = false,
}) => {
  const [now, setNow] = useState(Date.now());
  const isActive = ACTIVE_STATUSES.has(planJob.status);
  const progressPercent = Math.round((planJob.progress || 0) * 100);

  useEffect(() => {
    if (!isActive) return undefined;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [isActive]);

  const elapsed = useMemo(() => {
    if (!planJob.startedAt) return null;
    return formatElapsed((planJob.finishedAt || now) - planJob.startedAt);
  }, [planJob.startedAt, planJob.finishedAt, now]);

  const readiness = useMemo(() => {
    if (canPlan || isActive || planJob.result) return null;
    if (!videoCount && !hasMasterSong) return "Add video clips and choose a master soundtrack to enable Plan.";
    if (!videoCount) return "Add at least one video clip to enable Plan.";
    if (!hasMasterSong) return "Choose one audio clip as the master soundtrack to enable Plan.";
    return null;
  }, [canPlan, hasMasterSong, isActive, planJob.result, videoCount]);

  return (
    <Stack spacing={compact ? 0.75 : 1} className="non-draggable">
      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
        <Chip
          size="small"
          color={statusColor(planJob.status)}
          label={`Plan: ${statusLabel(planJob.status)}`}
          sx={{ height: 22 }}
        />
        <Typography variant="caption" color="text.secondary">
          {planJob.stageLabel || "Idle"}
        </Typography>
        {elapsed && (
          <Typography variant="caption" color="text.secondary">
            {elapsed}
          </Typography>
        )}
        {planJob.job?.id && (
          <Typography variant="caption" color="text.secondary" sx={{ fontFamily: "monospace" }}>
            {planJob.job.id.slice(0, 8)}
          </Typography>
        )}
      </Stack>

      {(isActive || planJob.status === "done") && (
        <Box>
          <LinearProgress
            variant="determinate"
            value={planJob.status === "done" ? 100 : progressPercent}
          />
          <Typography variant="caption" color="text.secondary">
            {planJob.status === "done" ? "100" : progressPercent}%
          </Typography>
        </Box>
      )}

      {readiness && <Alert severity="info" sx={{ py: 0 }}>{readiness}</Alert>}
      {planJob.error && <Alert severity="error" sx={{ py: 0 }}>{planJob.error}</Alert>}
      {warnings.length > 0 && (
        <Alert severity="warning" sx={{ py: 0 }}>
          {warnings.slice(0, 3).map((warning, index) => (
            <div key={index}>{warning}</div>
          ))}
        </Alert>
      )}
    </Stack>
  );
};

export default PlanStatusPanel;
