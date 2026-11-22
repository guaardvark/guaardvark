// A job on the Jobs page: what it is (picture or kind, title, settings), where
// it stands (status, when it runs or what it made), and the two things done
// to it most. The whole card opens the job's editor.

import React, { useState } from "react";
import { Box, Card, IconButton, LinearProgress, Tooltip, Typography } from "@mui/material";
import { useTheme } from "@mui/material/styles";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import StopIcon from "@mui/icons-material/Stop";
import MovieOutlinedIcon from "@mui/icons-material/MovieOutlined";
import ImageOutlinedIcon from "@mui/icons-material/ImageOutlined";
import AssignmentOutlinedIcon from "@mui/icons-material/AssignmentOutlined";
import { useUnifiedProgress } from "../../contexts/UnifiedProgressContext";
import StatusPill from "../settings/ui/StatusPill";
import { jobSummary, kindLabel } from "../jobs/jobSummary";
import { uiLocale, useUiTranslation } from "../../i18n";

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

const KIND_ICON = { video: MovieOutlinedIcon, images: ImageOutlinedIcon };

const shortTime = (date) =>
  date && !Number.isNaN(date.getTime())
    ? date.toLocaleString(uiLocale(), { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })
    : "";

// tasks.due_date and updated_at hold UTC without a zone; created_at and last_run_at are this machine's time.
const utc = (value) => (value ? new Date(`${String(value).replace(/Z$/, "")}Z`) : null);
const local = (value) => (value ? new Date(value) : null);

const TaskCard = ({ task, onEdit, onStartJob, onStopJob }) => {
  const { t } = useUiTranslation();
  const theme = useTheme();
  const { getProcess } = useUnifiedProgress();
  // A preview that is not there (a batch whose files were deleted) gives way to the kind's icon.
  const [brokenThumb, setBrokenThumb] = useState(null);

  if (!task) return null;

  const processData = task.job_id && typeof getProcess === "function" ? getProcess(task.job_id) : null;
  const cleanStatus = String(processData?.status || task.status || "unknown")
    .replace("CSV Gen", "")
    .trim()
    .toLowerCase();

  const isQueued = cleanStatus === "queued";
  const isRunning = ["in-progress", "running", "processing"].includes(cleanStatus);
  const isWaiting = cleanStatus === "waiting";
  const isDone = ["completed", "complete"].includes(cleanStatus);
  const isFailed = ["failed", "error"].includes(cleanStatus);
  const isFinished = isDone || isFailed || ["cancelled", "canceled"].includes(cleanStatus);
  const canStop = Boolean(task.job_id);
  const progress = Math.round(processData?.progress ?? task.progress ?? 0);

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

  const summary = jobSummary(task, t);
  const title = summary.title || t("layoutCards.task.untitled");
  const KindIcon = KIND_ICON[summary.kind] || AssignmentOutlinedIcon;
  const settings = [kindLabel(summary.kind, t), ...summary.details].join(" · ");
  // The prompt is the title of a job nothing named; do not say it twice.
  const text = summary.text && summary.text !== title ? summary.text : "";

  // One line on where the job stands: what it made, why it failed, or when it runs.
  const standing = isFailed && task.error_message ? { text: task.error_message, color: "error.main" }
    : isRunning ? { text: processData?.message || t("jobs.card.startedAt", { time: shortTime(local(task.last_run_at)) }) }
      : isQueued ? { text: t("layoutCards.task.queuedForExecution") }
        : isWaiting ? {
          text: task.schedule_type === "night_shift" ? t("jobs.nightShift.when.night")
            : task.schedule_type === "at" && task.due_date ? t("jobs.card.at", { time: shortTime(utc(task.due_date)) })
              : t("jobs.card.held"),
        }
          // The pill beside it already says how it ended.
          : isFinished ? { text: shortTime(utc(task.updated_at)) }
            : { text: t("jobs.card.created", { time: shortTime(local(task.created_at)) }) };

  const borderColor = isRunning
    ? theme.palette.info.main
    : isFailed
      ? theme.palette.error.main
      : theme.palette.divider;

  return (
    <Card
      data-testid={`job-card-${task.id}`}
      sx={{
        position: "relative",
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
        sx={{ display: "flex", gap: 1.25, p: 1.25, cursor: "pointer", minWidth: 0, height: "100%" }}
      >
        {summary.thumb && brokenThumb !== summary.thumb ? (
          <Box
            component="img"
            src={summary.thumb}
            alt=""
            loading="lazy"
            onError={() => setBrokenThumb(summary.thumb)}
            sx={{ width: 96, height: 72, objectFit: "cover", borderRadius: 1, flexShrink: 0, bgcolor: "action.hover" }}
          />
        ) : (
          <Box
            sx={{
              width: 44, height: 44, borderRadius: 1, flexShrink: 0, bgcolor: "action.hover", color: "text.secondary",
              display: "flex", alignItems: "center", justifyContent: "center",
            }}
          >
            <KindIcon fontSize="small" />
          </Box>
        )}
        <Box sx={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 0.4 }}>
          <Typography variant="body2" noWrap title={title} sx={{ fontWeight: 600 }}>
            {title}
          </Typography>
          <Typography variant="caption" color="text.secondary" noWrap title={settings}>
            {settings}
          </Typography>
          {text && (
            <Typography
              variant="caption"
              color="text.secondary"
              title={text}
              sx={{ overflow: "hidden", display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", lineHeight: 1.35 }}
            >
              {text}
            </Typography>
          )}
          <Box sx={{ mt: "auto", pt: 0.5, display: "flex", alignItems: "center", gap: 1, minWidth: 0 }}>
            {!isWaiting && <StatusPill label={pillLabel} tone={statusTone(cleanStatus)} />}
            <Typography variant="caption" color={standing.color || "text.secondary"} noWrap title={standing.text} sx={{ flex: 1, minWidth: 0 }}>
              {isRunning && progress > 0 ? `${progress}% · ` : ""}{standing.text}
            </Typography>
            <Box sx={{ display: "flex", flexShrink: 0 }} onClick={(e) => e.stopPropagation()}>
              {!isFinished && !isRunning && !isQueued && (
                <Tooltip title={isWaiting ? t("jobs.card.runNow") : t("layoutCards.task.startJob")}>
                  <IconButton
                    size="small"
                    aria-label={isWaiting ? t("jobs.card.runNow") : t("layoutCards.task.startJob")}
                    onClick={() => onStartJob?.(task.id, task)}
                  >
                    <PlayArrowIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              )}
              {(isRunning || isQueued) && (
                <Tooltip title={canStop ? (isQueued ? t("layoutCards.task.cancelQueuedTask") : t("layoutCards.task.stopJob")) : t("layoutCards.task.noJobToStop")}>
                  <span>
                    <IconButton
                      size="small"
                      disabled={!canStop}
                      aria-label={t("layoutCards.task.stopJob")}
                      onClick={() => onStopJob?.(task)}
                    >
                      <StopIcon fontSize="small" />
                    </IconButton>
                  </span>
                </Tooltip>
              )}
            </Box>
          </Box>
        </Box>
      </Box>
      {isRunning && (
        <LinearProgress
          variant={progress > 0 ? "determinate" : "indeterminate"}
          value={progress}
          sx={{ position: "absolute", left: 0, right: 0, bottom: 0, height: 3 }}
        />
      )}
    </Card>
  );
};

export default TaskCard;
