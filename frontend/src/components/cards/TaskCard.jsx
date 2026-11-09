// A scheduler job on the Jobs page. The card opens the editor. Start, stop,
// and Edit sit in a row so they do not cover the name or the description.

import React from "react";
import {
  Card,
  Typography,
  Box,
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import StopIcon from "@mui/icons-material/Stop";
import { useUnifiedProgress } from "../../contexts/UnifiedProgressContext";
import ActionButton from "../settings/ui/ActionButton";
import StatusPill from "../settings/ui/StatusPill";
import { useUiTranslation } from "../../i18n";

const statusTone = (status) => {
  switch (status) {
    case "pending":
    case "queued":
    case "in-progress":
    case "running":
    case "processing":
      return "info";
    case "paused":
    case "cancelled":
    case "canceled":
      return "warn";
    case "completed":
    case "complete":
      return "ok";
    case "failed":
    case "error":
      return "error";
    default:
      return "neutral";
  }
};

const TaskCard = ({
  task,
  onEdit,
  onStartJob,
  onStopJob,
  availableProjects = [],
}) => {
  const { t } = useUiTranslation();
  const theme = useTheme();
  const { getProcess } = useUnifiedProgress();

  if (!task) return null;

  const processData = task.job_id && typeof getProcess === "function"
    ? getProcess(task.job_id)
    : null;
  const realTimeProgress = processData?.progress || 0;
  const cleanStatus = String(processData?.status || task.status || "unknown")
    .replace("CSV Gen", "")
    .trim()
    .toLowerCase();

  const isQueued = cleanStatus === "queued";
  const isRunning = ["in-progress", "running", "processing"].includes(cleanStatus);
  const isFinished = ["completed", "complete", "failed", "error", "cancelled", "canceled"].includes(cleanStatus);
  const canStop = Boolean(task.job_id);

  const statusLabelKey = cleanStatus === "processing" || cleanStatus === "in-progress"
    ? "running"
    : cleanStatus === "complete"
      ? "completed"
      : cleanStatus === "canceled"
        ? "cancelled"
        : cleanStatus === "error"
          ? "failed"
          : cleanStatus;
  const statusText = t(`layoutCards.task.status.${statusLabelKey}`, { defaultValue: cleanStatus });
  const pillLabel = task.retry_count > 0
    ? t("layoutCards.task.statusWithRetries", { status: statusText, retries: task.retry_count })
    : statusText;

  const projectName = (() => {
    if (task.project?.name) return task.project.name;
    if (task.project_id && availableProjects.length > 0) {
      const project = availableProjects.find((p) => p.id === task.project_id);
      return project?.name || t("layoutCards.task.projectNumber", { id: task.project_id });
    }
    return task.project_id
      ? t("layoutCards.task.projectNumber", { id: task.project_id })
      : t("layoutCards.task.noProject");
  })();

  const typeLabel = task.type
    ? t(`tasks.types.${task.type}`, { defaultValue: task.type.replaceAll("_", " ") })
    : "";

  const created = (() => {
    if (!task.created_at) return "";
    const date = new Date(task.created_at);
    if (Number.isNaN(date.getTime())) return "";
    return date.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  })();

  const borderColor = isRunning
    ? theme.palette.info.main
    : isQueued
      ? theme.palette.info.light
      : cleanStatus === "completed" || cleanStatus === "complete"
        ? theme.palette.success.main
        : cleanStatus === "failed" || cleanStatus === "error"
          ? theme.palette.error.main
          : theme.palette.divider;

  return (
    <Card
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        border: "1px solid",
        borderColor,
        borderRadius: 2,
        boxShadow: "none",
        "&:hover": { borderColor: theme.palette.text.secondary },
      }}
    >
      <Box
        onClick={() => onEdit?.(task)}
        sx={{
          flexGrow: 1,
          display: "flex",
          flexDirection: "column",
          gap: 0.75,
          p: 1.5,
          cursor: "pointer",
          minWidth: 0,
        }}
      >
        <Typography variant="subtitle1" noWrap title={task.name || t("layoutCards.task.untitled")} sx={{ fontWeight: 500 }}>
          {task.name || t("layoutCards.task.untitled")}
        </Typography>
        <Typography variant="caption" color="text.secondary" noWrap>
          {projectName} · {task.client_name || t("layoutCards.task.noClient")}
        </Typography>
        <Box sx={{ display: "flex", alignItems: "center", gap: 1, minWidth: 0 }}>
          <StatusPill label={pillLabel} tone={statusTone(cleanStatus)} />
          {isRunning && (
            <Typography variant="caption" color="text.secondary" noWrap>
              {processData?.message || t("layoutCards.task.processing")} ({Math.round(realTimeProgress)}%)
            </Typography>
          )}
          {isQueued && (
            <Typography variant="caption" color="info.main" noWrap>
              {t("layoutCards.task.queuedForExecution")}
            </Typography>
          )}
        </Box>
        <Typography
          variant="body2"
          color="text.secondary"
          title={task.description || t("layoutCards.task.noDescriptionAvailable")}
          sx={{
            fontStyle: task.description ? "normal" : "italic",
            overflow: "hidden",
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
          }}
        >
          {task.description || t("layoutCards.task.noDescription")}
        </Typography>
        {typeLabel && (
          <Typography variant="caption" color="text.secondary">
            {typeLabel}
          </Typography>
        )}
        {task.output_filename && (
          <Typography variant="caption" color="text.secondary" noWrap title={task.output_filename}>
            {task.output_filename}
          </Typography>
        )}
        <Box sx={{ mt: "auto", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 1, pt: 0.5 }}>
          <Typography variant="caption" color="text.secondary">{created}</Typography>
          <Box sx={{ display: "flex", gap: 0.5 }} onClick={(e) => e.stopPropagation()}>
            {!isFinished && !isRunning && !isQueued && (
              <ActionButton
                startIcon={<PlayArrowIcon />}
                tooltip={task.status === "pending" ? t("layoutCards.task.runTask") : t("layoutCards.task.startJob")}
                onClick={() => onStartJob?.(task.id, task)}
              >
                {t("tasks.menu.start")}
              </ActionButton>
            )}
            {(isRunning || isQueued) && (
              <ActionButton
                startIcon={<StopIcon />}
                disabled={!canStop}
                tooltip={canStop ? (isQueued ? t("layoutCards.task.cancelQueuedTask") : t("layoutCards.task.stopJob")) : t("layoutCards.task.noJobToStop")}
                onClick={() => onStopJob?.(task)}
              >
                {t("tasks.menu.cancel")}
              </ActionButton>
            )}
            <ActionButton kind="link" onClick={() => onEdit?.(task)}>
              {t("tasks.menu.edit")}
            </ActionButton>
          </Box>
        </Box>
      </Box>
    </Card>
  );
};

export default TaskCard;
