import React, { useEffect, useMemo, useState } from "react";
import { Alert, Box, Chip, LinearProgress, Stack, Typography } from "@mui/material";
import { useUiTranslation, translate } from "../../i18n";

const ACTIVE_STATUSES = new Set(["submitting", "queued", "running"]);

const statusColor = (status) => {
  if (status === "done") return "success";
  if (status === "failed") return "error";
  if (ACTIVE_STATUSES.has(status)) return "primary";
  return "default";
};

const statusLabel = (status) => {
  if (status === "submitting") return translate("videoEditor.plan.status.submitting");
  if (status === "queued") return translate("videoEditor.plan.status.queued");
  if (status === "running") return translate("videoEditor.plan.status.running");
  if (status === "done") return translate("videoEditor.plan.status.done");
  if (status === "failed") return translate("videoEditor.plan.status.failed");
  return translate("videoEditor.plan.status.idle");
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
  const { t, language } = useUiTranslation();
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
    if (!videoCount && !hasMasterSong) return t("videoEditor.plan.needClipsAndSong");
    if (!videoCount) return t("videoEditor.plan.needClip");
    if (!hasMasterSong) return t("videoEditor.plan.needSong");
    return null;
  }, [canPlan, hasMasterSong, isActive, planJob.result, videoCount, t, language]);

  return (
    <Stack spacing={compact ? 0.75 : 1} className="non-draggable">
      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
        <Chip
          size="small"
          color={statusColor(planJob.status)}
          label={t("videoEditor.plan.chip", { status: statusLabel(planJob.status) })}
          sx={{ height: 22 }}
        />
        <Typography variant="caption" color="text.secondary">
          {planJob.stageLabel || t("videoEditor.plan.status.idle")}
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
