// frontend/src/components/jobs/ScheduledJobsCard.jsx
//
// The Jobs page's Scheduled card: every job that has not started yet. The
// night shift's jobs come first in the order they will run, and are dragged
// by their handle to change it; jobs set for a time, held jobs and repeating
// jobs follow. The strip above sets the window and says why nothing is
// starting right now (backend/services/night_shift.py decides; this shows it).
import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  IconButton,
  Menu,
  MenuItem,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";
import DragIndicatorIcon from "@mui/icons-material/DragIndicator";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import PauseIcon from "@mui/icons-material/Pause";
import ScheduleIcon from "@mui/icons-material/Schedule";
import EditOutlinedIcon from "@mui/icons-material/EditOutlined";
import DeleteOutlineOutlinedIcon from "@mui/icons-material/DeleteOutlineOutlined";
import SettingChip from "../settings/ui/SettingChip";
import StatusPill from "../settings/ui/StatusPill";
import NightShiftSummary from "./NightShiftSummary";
import ScheduleJobDialog from "./ScheduleJobDialog";
import ActionButton from "../settings/ui/ActionButton";
import { AtTimeDialog, toLocalInput } from "./ScheduleButton";
import {
  scheduleJob,
  setNightShiftOrder,
  setNightShiftPaused,
  setNightShiftWindow,
  WHEN,
} from "../../api/nightShiftService";
import { kindLabel } from "./jobSummary";
import { translate, uiLocale, useUiTranslation } from "../../i18n";

const HANDOFF_KINDS = new Set([
  "infographic", "cast_lora", "model_training", "music_video", "music", "upscale", "swarm", "lesson_replay",
  "film_crew",
  "tool_call",
]);

// Render batches and the other kinds started from their own pages keep their
// settings in the page that made them (backend/services/job_handoff.py).
const editableInModal = (type) => type !== "image_batch" && type !== "video_batch" && !HANDOFF_KINDS.has(type);

const formatTime = (iso) =>
  iso
    ? new Date(iso).toLocaleString(uiLocale(), { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
    : "";

const whenLabel = (row) => {
  if (row.repeats) return translate("jobs.nightShift.when.repeats", { cron: row.repeats });
  if (row.when === WHEN.NIGHT_SHIFT) return translate("jobs.nightShift.when.night");
  if (row.when === WHEN.AT) return formatTime(row.at);
  return translate("jobs.nightShift.when.held");
};

// Moves `id` to where `overId` is.
export const moveBefore = (ids, id, overId) => {
  if (id === overId) return ids;
  const from = ids.indexOf(id);
  const to = ids.indexOf(overId);
  if (from < 0 || to < 0) return ids;
  const next = ids.slice();
  next.splice(from, 1);
  next.splice(to, 0, id);
  return next;
};

const TimeField = ({ label, value, onCommit, testId }) => {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  return (
    <Box
      component="input"
      type="time"
      aria-label={label}
      data-testid={testId}
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => draft && draft !== value && onCommit(draft)}
      onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
      sx={(theme) => ({
        font: "inherit",
        fontSize: "0.8rem",
        fontWeight: 600,
        color: "text.primary",
        bgcolor: "action.hover",
        border: `1px solid ${theme.palette.divider}`,
        borderRadius: "6px",
        px: 0.75,
        py: 0.25,
        colorScheme: theme.palette.mode,
      })}
    />
  );
};

TimeField.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.string.isRequired,
  onCommit: PropTypes.func.isRequired,
  testId: PropTypes.string,
};

const ScheduledJobsCard = ({ status, error, refresh, onEdit, canEdit, describe, onRemove, onChanged }) => {
  const { t } = useUiTranslation();
  const [order, setOrder] = useState(null); // night-shift ids while a drag is in progress or being saved
  const [dragId, setDragId] = useState(null);
  const [actionError, setActionError] = useState(null);
  const [whenMenu, setWhenMenu] = useState(null); // { anchor, row }
  const [atRow, setAtRow] = useState(null);
  const [addOpen, setAddOpen] = useState(false);
  const rowRefs = useRef(new Map());
  const orderRef = useRef(null);

  const night = status?.waiting || [];
  const nightById = new Map(night.map((row) => [row.id, row]));
  const nightRows = order ? order.map((id) => nightById.get(id)).filter(Boolean) : night;
  const others = [...(status?.at || []), ...(status?.held || []), ...(status?.recurring || [])];
  const total = nightRows.length + others.length;

  const act = async (work) => {
    setActionError(null);
    try {
      await work();
      await refresh();
      onChanged?.();
    } catch (e) {
      setActionError(e?.message || String(e));
      await refresh();
    }
  };

  // Dragging: the handle takes the pointer, the row under it gives up its place.
  const startDrag = (event, id) => {
    if (event.button !== undefined && event.button !== 0) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture?.(event.pointerId);
    orderRef.current = night.map((row) => row.id);
    setOrder(orderRef.current);
    setDragId(id);
  };
  const drag = (event) => {
    if (dragId == null || !orderRef.current) return;
    const over = orderRef.current.find((id) => {
      const el = rowRefs.current.get(id);
      if (!el) return false;
      const box = el.getBoundingClientRect();
      return event.clientY >= box.top && event.clientY < box.bottom;
    });
    if (over == null || over === dragId) return;
    orderRef.current = moveBefore(orderRef.current, dragId, over);
    setOrder(orderRef.current);
  };
  const endDrag = async () => {
    if (dragId == null) return;
    const ids = orderRef.current || [];
    setDragId(null);
    const unchanged = ids.every((id, i) => night[i]?.id === id);
    if (!unchanged) await act(() => setNightShiftOrder(ids));
    orderRef.current = null;
    setOrder(null);
  };
  // Arrow keys on the handle move a row for people not using a pointer.
  const nudge = (id, step) => {
    const ids = night.map((row) => row.id);
    const from = ids.indexOf(id);
    const to = from + step;
    if (from < 0 || to < 0 || to >= ids.length) return;
    act(() => setNightShiftOrder(moveBefore(ids, id, ids[to])));
  };

  if (!status) {
    return (
      <Box sx={{ p: 1.5 }}>
        {error ? <Alert severity="error">{t("jobs.nightShift.loadFailed", { error })}</Alert> : null}
      </Box>
    );
  }

  const { window: hours, paused, hold, away_minutes: awayMinutes } = status;
  const state = paused
    ? { tone: "warn", label: t("jobs.nightShift.state.paused") }
    : night.length === 0
      ? { tone: "neutral", label: t("jobs.nightShift.state.empty") }
      : hold
        ? { tone: "info", label: t("jobs.nightShift.state.holding") }
        : { tone: "ok", label: t("jobs.nightShift.state.ready") };

  // `canEdit` lets the page offer its own editor for a kind this card would
  // otherwise leave to the page that made the job.
  const editable = (row) => Boolean(onEdit) && (editableInModal(row.type) || Boolean(canEdit?.(row)));

  // A row says what the job is: its first frame when it has one, a title that
  // tells it from its neighbours, and its kind and settings underneath.
  const rowBody = (row) => {
    const about = describe?.(row) || null;
    const title = about?.title || row.name;
    const settings = [kindLabel(about?.kind || row.type, t), ...(about?.details || [])].join(" · ");
    const open = editable(row) ? () => onEdit(row) : undefined;
    return (
      <Box
        onClick={open}
        data-testid={open ? `scheduled-open-${row.id}` : undefined}
        role={open ? "button" : undefined}
        tabIndex={open ? 0 : undefined}
        onKeyDown={open ? (e) => e.key === "Enter" && open() : undefined}
        sx={{ display: "flex", alignItems: "center", gap: 1, minWidth: 0, cursor: open ? "pointer" : "default" }}
      >
        {about?.thumb && (
          <Box
            component="img"
            src={about.thumb}
            alt=""
            loading="lazy"
            sx={{ width: 56, height: 36, objectFit: "cover", borderRadius: 0.75, flexShrink: 0, bgcolor: "action.hover" }}
          />
        )}
        <Box sx={{ minWidth: 0 }}>
          <Typography variant="body2" noWrap title={title} sx={open ? { "&:hover": { textDecoration: "underline" } } : undefined}>
            {title}
          </Typography>
          <Typography variant="caption" noWrap sx={{ color: "text.secondary", display: "block" }} title={settings}>
            {settings}
          </Typography>
        </Box>
      </Box>
    );
  };

  const renderRow = (row, draggable, place) => (
    <TableRow
      key={row.id}
      hover
      ref={(el) => (el ? rowRefs.current.set(row.id, el) : rowRefs.current.delete(row.id))}
      data-testid={`scheduled-row-${row.id}`}
      sx={dragId === row.id ? { bgcolor: "action.selected" } : undefined}
    >
      <TableCell sx={{ width: 28, px: 0.5 }}>
        {draggable && (
          <Tooltip title={dragId == null ? t("jobs.nightShift.dragHelp") : ""} disableFocusListener>
            <Box
              role="button"
              tabIndex={0}
              aria-label={t("jobs.nightShift.dragLabel", { name: row.name })}
              data-testid={`scheduled-handle-${row.id}`}
              onPointerDown={(e) => startDrag(e, row.id)}
              onPointerMove={drag}
              onPointerUp={endDrag}
              onPointerCancel={endDrag}
              onKeyDown={(e) => {
                if (e.key === "ArrowUp") { e.preventDefault(); nudge(row.id, -1); }
                if (e.key === "ArrowDown") { e.preventDefault(); nudge(row.id, 1); }
              }}
              sx={{ display: "flex", cursor: dragId === row.id ? "grabbing" : "grab", touchAction: "none", color: "text.secondary" }}
            >
              <DragIndicatorIcon fontSize="small" />
            </Box>
          </Tooltip>
        )}
      </TableCell>
      <TableCell sx={{ maxWidth: 0, width: "100%", py: 0.75 }}>
        {rowBody(row)}
      </TableCell>
      <TableCell sx={{ whiteSpace: "nowrap" }}>
        <Typography variant="caption">
          {draggable ? t("jobs.nightShift.placeInLine", { place: place + 1 }) : whenLabel(row)}
        </Typography>
        {row.late && <StatusPill label={t("jobs.nightShift.late")} tone="warn" sx={{ ml: 0.75 }} />}
      </TableCell>
      <TableCell align="right" sx={{ whiteSpace: "nowrap" }}>
        {!row.repeats && (
          <>
            <Tooltip title={t("jobs.nightShift.runNow")}>
              <IconButton
                size="small"
                aria-label={t("jobs.nightShift.runNowNamed", { name: row.name })}
                data-testid={`scheduled-run-${row.id}`}
                onClick={() => act(() => scheduleJob(row.id, { when: WHEN.NOW }))}
              >
                <PlayArrowIcon fontSize="small" />
              </IconButton>
            </Tooltip>
            {(row.when === WHEN.NIGHT_SHIFT || row.when === WHEN.AT) && (
              <Tooltip title={t("jobs.nightShift.pauseJob")}>
                <IconButton
                  size="small"
                  aria-label={t("jobs.nightShift.pauseJobNamed", { name: row.name })}
                  data-testid={`scheduled-pause-${row.id}`}
                  onClick={() => act(() => scheduleJob(row.id, { when: WHEN.HOLD }))}
                >
                  <PauseIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            )}
            <Tooltip title={t("jobs.nightShift.changeWhen")}>
              <IconButton
                size="small"
                aria-label={t("jobs.nightShift.changeWhenNamed", { name: row.name })}
                data-testid={`scheduled-when-${row.id}`}
                onClick={(e) => setWhenMenu({ anchor: e.currentTarget, row })}
              >
                <ScheduleIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          </>
        )}
        {editable(row) && (
          <Tooltip title={t("tasks.menu.edit")}>
            <IconButton size="small" aria-label={t("jobs.nightShift.editNamed", { name: row.name })} onClick={() => onEdit(row)}>
              <EditOutlinedIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        )}
        {onRemove && (
          <Tooltip title={t("jobs.nightShift.remove")}>
            <IconButton
              size="small"
              aria-label={t("jobs.nightShift.removeNamed", { name: row.name })}
              data-testid={`scheduled-remove-${row.id}`}
              onClick={() => onRemove(row)}
            >
              <DeleteOutlineOutlinedIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        )}
      </TableCell>
    </TableRow>
  );

  return (
    <Box sx={{ p: 1.5 }} data-testid="scheduled-jobs-card">
      <Stack direction="row" useFlexGap spacing={1} sx={{ alignItems: "center", flexWrap: "wrap", mb: 1 }}>
        <Typography variant="body2" sx={{ fontWeight: 600 }}>{t("jobs.nightShift.title")}</Typography>
        <TimeField
          label={t("jobs.nightShift.windowStart")}
          value={hours.start}
          testId="night-shift-start"
          onCommit={(start) => act(() => setNightShiftWindow(start, hours.end))}
        />
        <Typography variant="caption" sx={{ color: "text.secondary" }}>{t("jobs.nightShift.to")}</Typography>
        <TimeField
          label={t("jobs.nightShift.windowEnd")}
          value={hours.end}
          testId="night-shift-end"
          onCommit={(end) => act(() => setNightShiftWindow(hours.start, end))}
        />
        <Typography variant="caption" sx={{ color: "text.secondary" }}>
          {t("jobs.nightShift.rule", { minutes: awayMinutes })}
        </Typography>
        <Typography variant="caption" data-testid="night-shift-count" sx={{ color: "text.secondary" }}>
          {t("jobs.nightShift.count", { count: night.length })}
        </Typography>
        <Box sx={{ flex: 1 }} />
        <ActionButton
          onClick={() => setAddOpen(true)}
          tooltip={t("jobs.nightShift.scheduleJobHelp")}
          data-testid="schedule-job-open"
        >
          {t("jobs.nightShift.scheduleJob")}
        </ActionButton>
        <SettingChip
          label={t("jobs.nightShift.pauseChip")}
          on={Boolean(paused)}
          onToggle={(next) => act(() => setNightShiftPaused(next))}
          tooltip={t("jobs.nightShift.pauseHelp")}
          testId="night-shift-pause"
        />
      </Stack>
      <Stack direction="row" spacing={1} sx={{ alignItems: "center", mb: 1.5 }}>
        <StatusPill label={state.label} tone={state.tone} />
        {hold && night.length > 0 && (
          <Typography variant="caption" data-testid="night-shift-hold" sx={{ color: "text.secondary" }}>
            {t("jobs.nightShift.holdReason", { reason: hold })}
          </Typography>
        )}
        {!status.desktop_idle_available && (
          <Typography variant="caption" sx={{ color: "warning.main" }}>{t("jobs.nightShift.noDesktopIdle")}</Typography>
        )}
      </Stack>

      {(error || actionError) && (
        <Alert severity="error" sx={{ mb: 1.5 }} onClose={actionError ? () => setActionError(null) : undefined}>
          {actionError || t("jobs.nightShift.loadFailed", { error })}
        </Alert>
      )}

      <NightShiftSummary shift={status.shift} titleOf={(job) => describe?.(job)?.title} />

      {total === 0 ? (
        <Typography variant="body2" data-testid="scheduled-empty" sx={{ color: "text.secondary" }}>
          {t("jobs.nightShift.empty")}
        </Typography>
      ) : (
        <Table size="small" sx={{ tableLayout: "auto" }}>
          <TableBody>
            {nightRows.map((row, place) => renderRow(row, true, place))}
            {others.map((row) => renderRow(row, false))}
          </TableBody>
        </Table>
      )}

      <ScheduleJobDialog open={addOpen} onClose={() => setAddOpen(false)} onScheduled={() => act(async () => {})} />
      <Menu anchorEl={whenMenu?.anchor} open={Boolean(whenMenu)} onClose={() => setWhenMenu(null)}>
        {whenMenu?.row?.when !== WHEN.NIGHT_SHIFT && (
          <MenuItem
            data-testid="when-night-shift"
            onClick={() => {
              const { row } = whenMenu;
              setWhenMenu(null);
              act(() => scheduleJob(row.id, { when: WHEN.NIGHT_SHIFT }));
            }}
          >
            {t("jobs.nightShift.add")}
          </MenuItem>
        )}
        <MenuItem
          data-testid="when-at"
          onClick={() => {
            setAtRow(whenMenu.row);
            setWhenMenu(null);
          }}
        >
          {t("jobs.nightShift.atMenu")}
        </MenuItem>
      </Menu>
      <AtTimeDialog
        open={Boolean(atRow)}
        initial={atRow?.at ? toLocalInput(new Date(atRow.at)) : undefined}
        onClose={() => setAtRow(null)}
        onPick={(at) => {
          const row = atRow;
          setAtRow(null);
          act(() => scheduleJob(row.id, { when: WHEN.AT, at }));
        }}
      />
    </Box>
  );
};

ScheduledJobsCard.propTypes = {
  status: PropTypes.object,
  error: PropTypes.string,
  refresh: PropTypes.func.isRequired,
  onEdit: PropTypes.func,
  canEdit: PropTypes.func,
  describe: PropTypes.func,
  onRemove: PropTypes.func,
  onChanged: PropTypes.func,
};

export default ScheduledJobsCard;
