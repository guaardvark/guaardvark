// frontend/src/components/jobs/NightShiftSummary.jsx
//
// What the last night shift did: each job that ran since the window last
// opened, with its result or the reason it failed, linked to the job.
import React from "react";
import PropTypes from "prop-types";
import { Box, Link, Stack, Typography } from "@mui/material";
import { Link as RouterLink } from "react-router-dom";
import StatusPill from "../settings/ui/StatusPill";
import { TASK_PARAMS } from "../../utils/entityLinks";
import { uiLocale, useUiTranslation } from "../../i18n";

export const jobLink = (id) => `/tasks?${TASK_PARAMS.taskId}=${id}`;

const NightShiftSummary = ({ shift, dense = false, limit = 0 }) => {
  const { t } = useUiTranslation();
  const done = shift?.done || [];
  const failed = shift?.failed || [];
  const running = shift?.running || [];
  if (!shift?.opened || done.length + failed.length + running.length === 0) return null;

  // Failures first: they are what the person has to act on.
  const rows = [
    ...failed.map((job) => ({ job, tone: "error", label: t("jobs.status.failed"), text: job.error })),
    ...running.map((job) => ({ job, tone: "info", label: t("jobs.status.running"), text: null })),
    ...done.map((job) => ({ job, tone: "ok", label: t("jobs.status.completed"), text: job.result })),
  ];
  const shown = limit ? rows.slice(0, limit) : rows;
  const since = new Date(shift.opened).toLocaleString(uiLocale(), {
    weekday: "short", hour: "2-digit", minute: "2-digit",
  });

  return (
    <Box data-testid="night-shift-summary" sx={{ mb: dense ? 1 : 2 }}>
      <Typography variant={dense ? "caption" : "body2"} sx={{ fontWeight: 600, display: "block" }}>
        {failed.length
          ? t("jobs.nightShift.summaryWithFailed", { done: done.length, failed: failed.length, since })
          : t("jobs.nightShift.summary", { done: done.length, since })}
      </Typography>
      <Stack spacing={0.25} sx={{ mt: 0.5 }}>
        {shown.map(({ job, tone, label, text }) => (
          <Stack key={job.id} direction="row" spacing={1} sx={{ alignItems: "baseline", minWidth: 0 }}>
            <StatusPill label={label} tone={tone} />
            <Link
              component={RouterLink}
              to={jobLink(job.id)}
              underline="hover"
              className="non-draggable"
              sx={{ fontSize: dense ? "0.72rem" : "0.8rem", flexShrink: 0, maxWidth: "55%" }}
              noWrap
            >
              {job.name}
            </Link>
            {text && (
              <Typography
                variant="caption"
                noWrap
                title={text}
                sx={{ color: tone === "error" ? "error.main" : "text.secondary", minWidth: 0 }}
              >
                {text}
              </Typography>
            )}
          </Stack>
        ))}
        {shown.length < rows.length && (
          <Link component={RouterLink} to="/tasks" underline="hover" className="non-draggable" sx={{ fontSize: "0.72rem" }}>
            {t("jobs.nightShift.summaryMore", { count: rows.length - shown.length })}
          </Link>
        )}
      </Stack>
    </Box>
  );
};

NightShiftSummary.propTypes = {
  shift: PropTypes.object,
  dense: PropTypes.bool,
  limit: PropTypes.number,
};

export default NightShiftSummary;
