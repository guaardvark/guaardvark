// frontend/src/components/jobs/JobEditorDialog.jsx
//
// The frame every job editor on the Jobs page shares: the name, the status,
// when the job runs, what it made, and the save. A job that has not started
// is changed in place; a finished one is saved again as a new job; a running
// one is shown as it is. The editor for a kind of job (VideoJobModal,
// ImageJobModal) supplies its fields and the request they make.
import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { Link as RouterLink } from "react-router-dom";
import { Alert, Box, Dialog, DialogActions, DialogContent, IconButton, InputBase, Link, Typography } from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import ActionButton from "../settings/ui/ActionButton";
import ChoiceChips from "../settings/ui/ChoiceChips";
import StatusPill from "../settings/ui/StatusPill";
import { Cluster, Hint } from "../settings/ui/SettingsCluster";
import { AtTimeDialog, toLocalInput } from "./ScheduleButton";
import {
  createScheduledBatch,
  scheduleJob,
  scheduleTool,
  updateScheduledJob,
  WHEN,
} from "../../api/nightShiftService";
import { uiLocale, useUiTranslation } from "../../i18n";

// "pending" is on its way to the queue: the scheduler takes it within seconds.
const STARTED = new Set(["pending", "queued", "in-progress", "running", "processing", "paused"]);

/** Where a job stands for its editor: "waiting", "started" or "finished". */
export const jobPhase = (task) => {
  const status = String(task?.status || "").toLowerCase();
  return status === "waiting" ? "waiting" : STARTED.has(status) ? "started" : "finished";
};

const toneOf = (status) =>
  status === "completed" || status === "complete" ? "ok"
    : status === "failed" || status === "error" ? "error"
      : STARTED.has(status) ? "info"
        : status === "cancelled" || status === "canceled" ? "warn"
          : "neutral";

// tasks.due_date is naive UTC.
const savedChoice = (task) => {
  if (task?.schedule_type === WHEN.NIGHT_SHIFT) return { when: WHEN.NIGHT_SHIFT };
  if (task?.schedule_type === WHEN.AT && task.due_date) {
    return { when: WHEN.AT, at: new Date(`${String(task.due_date).replace(/Z$/, "")}Z`).toISOString() };
  }
  return { when: WHEN.HOLD };
};

const sameChoice = (a, b) => a.when === b.when && (a.when !== WHEN.AT || a.at === b.at);

export const numberOrNull = (text) => {
  if (text === "" || text === null || text === undefined) return null;
  const n = Number(text);
  return Number.isFinite(n) ? n : null;
};

export const fieldSx = { "& .MuiInputBase-input": { fontSize: "0.85rem" } };

/** A picture the job starts from: shown when the browser can load it, named when only the backend can. */
export const Frame = ({ url, name, label }) => (
  <Box sx={{ width: 112, flexShrink: 0 }}>
    {url ? (
      <Box
        component="img"
        src={url}
        alt={label}
        sx={{ width: 112, height: 72, objectFit: "cover", borderRadius: 1, display: "block", bgcolor: "action.hover" }}
      />
    ) : (
      <Box
        title={name}
        sx={{
          width: 112, height: 72, borderRadius: 1, bgcolor: "action.hover", p: 0.75, overflow: "hidden",
          fontSize: "0.68rem", color: "text.secondary", wordBreak: "break-all", lineHeight: 1.25,
        }}
      >
        {String(name || "").split("/").pop()}
      </Box>
    )}
    <Hint sx={{ display: "block", mt: 0.25, fontSize: "0.66rem" }}>{label}</Hint>
  </Box>
);

Frame.propTypes = { url: PropTypes.string, name: PropTypes.string, label: PropTypes.string.isRequired };

/**
 * @param {object}   task            the scheduler row (GET /api/tasks)
 * @param {string}   testId          prefix for the dialog's test ids
 * @param {string}   kindLine        one quiet line saying what the job makes
 * @param {boolean}  settingsDirty   a field the editor owns differs from the saved job
 * @param {string}   [problem]       why the fields cannot be saved as they are
 * @param {function} buildRequest    the job's request with the fields written in
 * @param {function} asNewJob        (request, name) => the body that saves it as a new job
 * @param {{to:string,label:string}} [outputLink]  where a started job's output shows
 * @param {function} children        ({ readOnly }) => the editor's fields
 */
const JobEditorDialog = ({
  open, task, testId, kindLine, settingsDirty, problem, buildRequest, asNewJob, outputLink, onClose, onSaved,
  onRemove, children,
}) => {
  const { t } = useUiTranslation();
  const [name, setName] = useState("");
  const [choice, setChoice] = useState({ when: WHEN.NIGHT_SHIFT });
  const [atOpen, setAtOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const status = String(task?.status || "").toLowerCase();
  const phase = jobPhase(task);
  const waiting = phase === "waiting";
  const started = phase === "started";
  const finished = phase === "finished";

  useEffect(() => {
    if (!open || !task) return;
    setName(task.name || "");
    setChoice(jobPhase(task) === "waiting" ? savedChoice(task) : { when: WHEN.NIGHT_SHIFT });
    setError(null);
    setBusy(false);
  }, [open, task]);

  if (!task) return null;

  const readOnly = started || busy;
  const nameDirty = name.trim() !== (task.name || "");
  const whenDirty = waiting && !sameChoice(choice, savedChoice(task));
  const dirty = settingsDirty || nameDirty || whenDirty;
  const blocked = problem || (!name.trim() ? t("jobs.jobEditor.needsName") : null);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      if (waiting) {
        if (settingsDirty || nameDirty) {
          await updateScheduledJob(task.id, {
            name: nameDirty ? name.trim() : undefined,
            request: settingsDirty ? buildRequest() : undefined,
          });
        }
        if (whenDirty) await scheduleJob(task.id, choice);
      } else {
        const request = settingsDirty ? buildRequest() : task.workflow_config_parsed?.request;
        // "Now" is a job saved for the night shift and started at once.
        const later = choice.when === WHEN.NOW ? { when: WHEN.NIGHT_SHIFT } : choice;
        const body = asNewJob(request, name.trim());
        const saved = body.tool ? await scheduleTool(body, later) : await createScheduledBatch(body, later);
        if (choice.when === WHEN.NOW && saved?.task_id) await scheduleJob(saved.task_id, { when: WHEN.NOW });
      }
      onSaved?.();
      onClose();
    } catch (e) {
      setError(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const whenOptions = [
    ...(finished ? [{ value: WHEN.NOW, label: t("jobs.nightShift.whenNow") }] : []),
    { value: WHEN.NIGHT_SHIFT, label: t("jobs.nightShift.when.night"), tooltip: t("jobs.nightShift.addHelp") },
    {
      value: WHEN.AT,
      label: choice.when === WHEN.AT
        ? new Date(choice.at).toLocaleString(uiLocale(), { dateStyle: "medium", timeStyle: "short" })
        : t("jobs.nightShift.atMenu"),
    },
    ...(waiting ? [{ value: WHEN.HOLD, label: t("jobs.nightShift.when.held"), tooltip: t("jobs.nightShift.pauseJob") }] : []),
  ];

  return (
    <Dialog open={open} onClose={busy ? undefined : onClose} fullWidth maxWidth="sm" data-testid={`${testId}-modal`}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 1, px: 2, pt: 1.25, pb: 0.75 }}>
        <InputBase
          value={name}
          onChange={(e) => setName(e.target.value)}
          readOnly={readOnly}
          inputProps={{ "aria-label": t("jobs.jobEditor.name"), "data-testid": `${testId}-name` }}
          sx={{
            flex: 1, minWidth: 0, fontSize: "1.05rem", fontWeight: 500, borderRadius: 1, px: 0.75, ml: -0.75,
            "&:hover, &.Mui-focused": readOnly ? {} : { bgcolor: "action.hover" },
          }}
        />
        <StatusPill label={t(`layoutCards.task.status.${status}`, { defaultValue: status })} tone={toneOf(status)} />
        <IconButton size="small" onClick={onClose} disabled={busy} aria-label={t("common.close")}>
          <CloseIcon fontSize="small" />
        </IconButton>
      </Box>

      <DialogContent sx={{ px: 2, pt: 0.5, pb: 1.5, display: "flex", flexDirection: "column", gap: 1.5 }}>
        <Hint>{kindLine}</Hint>
        {started && <Alert severity="info" sx={{ py: 0 }}>{t("jobs.jobEditor.started")}</Alert>}
        {error && <Alert severity="error" sx={{ py: 0 }} data-testid={`${testId}-error`}>{error}</Alert>}

        {children({ readOnly })}

        {!started && (
          <Cluster label={finished ? t("jobs.jobEditor.runAgainWhen") : t("jobs.nightShift.whenLabel")}>
            <Box data-testid={`${testId}-when`}>
              <ChoiceChips
                ariaLabel={t("jobs.nightShift.whenLabel")}
                options={whenOptions}
                value={choice.when}
                onChange={(next) => (next === WHEN.AT ? setAtOpen(true) : setChoice({ when: next }))}
                disabled={busy}
              />
            </Box>
          </Cluster>
        )}

        {(task.result || task.error_message || outputLink) && (
          <Box sx={{ borderTop: 1, borderColor: "divider", pt: 1 }}>
            {task.error_message && (
              <Typography variant="caption" color="error.main" sx={{ display: "block" }} data-testid={`${testId}-failure`}>
                {task.error_message}
              </Typography>
            )}
            {task.result && (
              <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
                {task.result}
              </Typography>
            )}
            {outputLink && (
              <Link component={RouterLink} to={outputLink.to} variant="caption" onClick={onClose}>
                {outputLink.label}
              </Link>
            )}
          </Box>
        )}
      </DialogContent>

      <DialogActions sx={{ px: 2, pb: 1.5, pt: 0, justifyContent: "space-between" }}>
        <Box>
          {waiting && onRemove && (
            <ActionButton kind="link" onClick={() => onRemove(task)} disabled={busy} data-testid={`${testId}-remove`}>
              {t("jobs.nightShift.remove")}
            </ActionButton>
          )}
        </Box>
        <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
          {blocked && !started && <Hint sx={{ color: "warning.main" }}>{blocked}</Hint>}
          <ActionButton kind="link" onClick={onClose} disabled={busy}>
            {t("common.close")}
          </ActionButton>
          {waiting && (
            <ActionButton
              kind={dirty ? "primary" : "neutral"}
              onClick={save}
              disabled={!dirty || Boolean(blocked)}
              loading={busy}
              data-testid={`${testId}-save`}
            >
              {t("dialogs.taskAction.saveChanges")}
            </ActionButton>
          )}
          {finished && (
            <ActionButton
              kind={settingsDirty ? "primary" : "neutral"}
              onClick={save}
              disabled={Boolean(blocked)}
              loading={busy}
              tooltip={t("jobs.jobEditor.runAgainHelp")}
              data-testid={`${testId}-again`}
            >
              {choice.when === WHEN.NOW ? t("jobs.jobEditor.runAgainNow") : t("jobs.jobEditor.scheduleAgain")}
            </ActionButton>
          )}
        </Box>
      </DialogActions>

      <AtTimeDialog
        open={atOpen}
        initial={choice.when === WHEN.AT ? toLocalInput(new Date(choice.at)) : undefined}
        onClose={() => setAtOpen(false)}
        onPick={(at) => {
          setAtOpen(false);
          setChoice({ when: WHEN.AT, at });
        }}
      />
    </Dialog>
  );
};

JobEditorDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  task: PropTypes.object,
  testId: PropTypes.string.isRequired,
  kindLine: PropTypes.string,
  settingsDirty: PropTypes.bool.isRequired,
  problem: PropTypes.string,
  buildRequest: PropTypes.func.isRequired,
  asNewJob: PropTypes.func.isRequired,
  outputLink: PropTypes.shape({ to: PropTypes.string.isRequired, label: PropTypes.string.isRequired }),
  onClose: PropTypes.func.isRequired,
  onSaved: PropTypes.func,
  onRemove: PropTypes.func,
  children: PropTypes.func.isRequired,
};

export default JobEditorDialog;
