// frontend/src/components/jobs/JobQueuePanel.jsx
//
// The job arbiter's line on the Activity page (backend/services/job_arbiter.py):
// what waits for the GPU in order, with its class and place, plus background
// work paused for a chat and what is running. Up/down and Run now are offered
// only while Settings → General → Job order is on; with it off the panel
// renders nothing and the page is as it was.
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  IconButton,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";
import {
  KeyboardArrowDown as DownIcon,
  KeyboardArrowUp as UpIcon,
} from "@mui/icons-material";
import { getJobQueue, moveQueuedJob, runQueuedJobNow } from "../../api/jobsService";
import { translate, useUiTranslation } from "../../i18n";

const POLL_ON_MS = 3000;
// While job order is off the panel only checks now and then whether it was switched on.
const POLL_OFF_MS = 30000;

const CLASS_COLORS = { interactive: "secondary", yours: "primary", background: "default" };

const formatWait = (seconds) => {
  if (seconds == null) return "—";
  if (seconds < 60) return translate("jobs.queue.waitSeconds", { seconds: Math.round(seconds) });
  if (seconds < 3600) return translate("jobs.queue.waitMinutes", { minutes: Math.floor(seconds / 60) });
  return translate("jobs.queue.waitHours", { hours: (seconds / 3600).toFixed(1) });
};

const ClassChip = ({ item, classes }) => {
  const info = classes.find((c) => c.id === item.class);
  return (
    <Tooltip title={info?.help || ""} arrow>
      <Chip
        size="small"
        label={item.class_label || item.class}
        color={CLASS_COLORS[item.class] || "default"}
        variant={item.class === "background" ? "outlined" : "filled"}
      />
    </Tooltip>
  );
};

const JobQueuePanel = () => {
  const { t } = useUiTranslation();
  const [queue, setQueue] = useState(null);
  const [error, setError] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const timer = useRef(null);

  const refresh = useCallback(async () => {
    try {
      const data = await getJobQueue();
      setQueue(data);
      setError(null);
      return data;
    } catch (e) {
      // Keep the last snapshot; the next poll tries again.
      return null;
    }
  }, []);

  useEffect(() => {
    let alive = true;
    const loop = async () => {
      const data = await refresh();
      if (!alive) return;
      timer.current = setTimeout(loop, data?.enabled ? POLL_ON_MS : POLL_OFF_MS);
    };
    loop();
    return () => {
      alive = false;
      clearTimeout(timer.current);
    };
  }, [refresh]);

  const act = async (id, fn) => {
    setBusyId(id);
    try {
      const data = await fn();
      if (data) setQueue(data);
      setError(null);
    } catch (e) {
      setError(e.response?.data?.error || e.message || t("jobs.queue.actionFailed"));
      refresh();
    } finally {
      setBusyId(null);
    }
  };

  if (!queue?.enabled) return null;

  const classes = queue.classes || [];
  const queued = queue.queued || [];
  const paused = queue.paused || [];
  const running = queue.running || [];
  const holds = queue.holds || [];
  const empty = !queued.length && !paused.length && !running.length && !holds.length;

  return (
    <Box sx={{ maxWidth: 1400, mx: "auto", mt: 2, px: 2 }}>
      <Paper elevation={2} sx={{ p: 2, borderRadius: 2 }} data-testid="job-queue-panel">
        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" sx={{ gap: 1, mb: 1 }}>
          <Typography variant="subtitle1" fontWeight={600}>{t("jobs.queue.title")}</Typography>
          <Typography variant="caption" color="text.secondary" sx={{ flexGrow: 1 }}>
            {t("jobs.queue.intro")}
          </Typography>
          {classes.map((c) => (
            <Tooltip key={c.id} title={c.help} arrow>
              <Chip
                size="small"
                label={c.label}
                color={CLASS_COLORS[c.id] || "default"}
                variant={c.id === "background" ? "outlined" : "filled"}
              />
            </Tooltip>
          ))}
        </Stack>

        {error && <Alert severity="warning" sx={{ mb: 1 }} onClose={() => setError(null)}>{error}</Alert>}
        {queue.interactive_busy && (
          <Alert severity="info" sx={{ mb: 1 }}>
            {t("jobs.queue.interactiveBusy", { task: queue.interactive_busy })}
          </Alert>
        )}
        {holds.map((h) => (
          <Alert key={h.slot_id} severity="info" sx={{ mb: 1 }}>
            {t("jobs.queue.hold", {
              holder: h.holder || t("jobs.queue.swarmAgent"),
              model: String(h.slot_id || "").replace(/^ollama:/, ""),
            })}
          </Alert>
        ))}

        {empty && (
          <Typography variant="body2" color="text.secondary" sx={{ py: 1 }}>
            {t("jobs.queue.empty")}
          </Typography>
        )}

        {queued.length > 0 && (
          <Table size="small" aria-label={t("jobs.queue.waitingForGpu")}>
            <TableHead>
              <TableRow>
                <TableCell sx={{ fontWeight: 600, width: 48 }}>#</TableCell>
                <TableCell sx={{ fontWeight: 600 }}>{t("jobs.queue.waitingForGpu")}</TableCell>
                <TableCell sx={{ fontWeight: 600 }}>{t("jobs.queue.class")}</TableCell>
                <TableCell sx={{ fontWeight: 600 }}>{t("jobs.queue.status")}</TableCell>
                <TableCell sx={{ fontWeight: 600 }}>{t("jobs.queue.waited")}</TableCell>
                <TableCell sx={{ fontWeight: 600 }} align="right">{t("jobs.queue.order")}</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {queued.map((item, i) => (
                <TableRow key={item.id} data-testid="job-queue-row">
                  <TableCell>{item.position}</TableCell>
                  <TableCell>
                    {item.label}
                    {item.moved && (
                      <Typography component="span" variant="caption" color="text.secondary"> {t("jobs.queue.moved")}</Typography>
                    )}
                  </TableCell>
                  <TableCell><ClassChip item={item} classes={classes} /></TableCell>
                  <TableCell>
                    <Typography variant="caption">{item.run_now ? t("jobs.queue.goingAhead") : item.reason || t("jobs.queue.next")}</Typography>
                  </TableCell>
                  <TableCell>{formatWait(item.waited_s)}</TableCell>
                  <TableCell align="right" sx={{ whiteSpace: "nowrap" }}>
                    {item.can_run_now && (
                      <Button
                        size="small"
                        variant="outlined"
                        disabled={busyId === item.id}
                        onClick={() => act(item.id, () => runQueuedJobNow(item.id))}
                        sx={{ mr: 1 }}
                      >
                        {t("jobs.queue.runNow")}
                      </Button>
                    )}
                    <IconButton
                      size="small"
                      aria-label={t("jobs.queue.moveUp", { label: item.label })}
                      disabled={i === 0 || busyId === item.id || item.run_now}
                      onClick={() => act(item.id, () => moveQueuedJob(item.id, "up"))}
                    >
                      <UpIcon fontSize="small" />
                    </IconButton>
                    <IconButton
                      size="small"
                      aria-label={t("jobs.queue.moveDown", { label: item.label })}
                      disabled={i === queued.length - 1 || busyId === item.id || item.run_now}
                      onClick={() => act(item.id, () => moveQueuedJob(item.id, "down"))}
                    >
                      <DownIcon fontSize="small" />
                    </IconButton>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}

        {(paused.length > 0 || running.length > 0) && (
          <Stack spacing={0.5} sx={{ mt: queued.length ? 2 : 0 }}>
            {paused.map((item) => (
              <Stack key={item.id} direction="row" spacing={1} alignItems="center">
                <Chip size="small" label={t("jobs.queue.paused")} color="warning" variant="outlined" />
                <ClassChip item={item} classes={classes} />
                <Typography variant="body2">{item.label}</Typography>
                <Typography variant="caption" color="text.secondary">{item.reason}</Typography>
              </Stack>
            ))}
            {running.map((item) => (
              <Stack key={item.id} direction="row" spacing={1} alignItems="center">
                <Chip size="small" label={t("jobs.queue.running")} color="info" variant="outlined" />
                <ClassChip item={item} classes={classes} />
                <Typography variant="body2">{item.label}</Typography>
              </Stack>
            ))}
          </Stack>
        )}
      </Paper>
    </Box>
  );
};

export default JobQueuePanel;
