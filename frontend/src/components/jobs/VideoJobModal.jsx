// frontend/src/components/jobs/VideoJobModal.jsx
//
// One video job, opened from the Jobs page. A job that has not started is
// edited in place: prompt, model, shape, length, steps, style and when it
// runs. A finished job shows what it made and can be scheduled again with
// changed settings, as a new job. A job that is running is shown as it is.
// Choices come from the capability record the model declares
// (/api/batch-video/models); the backend checks the same record on save.
import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Link as RouterLink } from "react-router-dom";
import {
  Alert,
  Box,
  Dialog,
  DialogActions,
  DialogContent,
  IconButton,
  InputAdornment,
  InputBase,
  Link,
  MenuItem,
  TextField,
  Typography,
} from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import ActionButton from "../settings/ui/ActionButton";
import ChoiceChips from "../settings/ui/ChoiceChips";
import StatusPill from "../settings/ui/StatusPill";
import { Cluster, Hint } from "../settings/ui/SettingsCluster";
import { AtTimeDialog, toLocalInput } from "./ScheduleButton";
import { BASE_URL } from "../../api/apiClient";
import {
  createScheduledBatch,
  scheduleJob,
  scheduleTool,
  updateScheduledJob,
  WHEN,
} from "../../api/nightShiftService";
import {
  asNewJob,
  batchIdOf,
  defaultSteps,
  frameUrl,
  lengthRange,
  modelFits,
  readVideoJob,
  secondsOf,
  stepFloor,
  writeVideoJob,
} from "./videoJob";
import { uiLocale, useUiTranslation } from "../../i18n";

// "pending" is on its way to the queue: the scheduler takes it within seconds.
const STARTED = new Set(["pending", "queued", "in-progress", "running", "processing", "paused"]);
const FALLBACK_RATIOS = ["16:9", "9:16", "1:1"];

const toneOf = (status) =>
  status === "completed" || status === "complete" ? "ok"
    : status === "failed" || status === "error" ? "error"
      : STARTED.has(status) ? "info"
        : status === "cancelled" || status === "canceled" ? "warn"
          : "neutral";

// tasks.due_date is naive UTC.
const dueDate = (task) => (task?.due_date ? new Date(`${String(task.due_date).replace(/Z$/, "")}Z`) : null);

const savedChoice = (task) => {
  if (task?.schedule_type === WHEN.NIGHT_SHIFT) return { when: WHEN.NIGHT_SHIFT };
  if (task?.schedule_type === WHEN.AT && task.due_date) return { when: WHEN.AT, at: dueDate(task).toISOString() };
  return { when: WHEN.HOLD };
};

const sameChoice = (a, b) => a.when === b.when && (a.when !== WHEN.AT || a.at === b.at);

const numberOrNull = (text) => {
  if (text === "" || text === null || text === undefined) return null;
  const n = Number(text);
  return Number.isFinite(n) ? n : null;
};

const fieldSx = { "& .MuiInputBase-input": { fontSize: "0.85rem" } };

const Frame = ({ refValue, label }) => {
  const url = frameUrl(refValue);
  const name = String(refValue || "").split("/").pop();
  return (
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
          title={String(refValue)}
          sx={{
            width: 112, height: 72, borderRadius: 1, bgcolor: "action.hover", p: 0.75, overflow: "hidden",
            fontSize: "0.68rem", color: "text.secondary", wordBreak: "break-all", lineHeight: 1.25,
          }}
        >
          {name}
        </Box>
      )}
      <Hint sx={{ display: "block", mt: 0.25, fontSize: "0.66rem" }}>{label}</Hint>
    </Box>
  );
};

Frame.propTypes = { refValue: PropTypes.string, label: PropTypes.string.isRequired };

const VideoJobModal = ({ open, task, onClose, onSaved, onRemove }) => {
  const { t } = useUiTranslation();
  const job = useMemo(() => (task ? readVideoJob(task) : null), [task]);
  const [models, setModels] = useState(null);
  const [name, setName] = useState("");
  const [fields, setFields] = useState(null);
  const [lengthTouched, setLengthTouched] = useState(false);
  const [choice, setChoice] = useState({ when: WHEN.NIGHT_SHIFT });
  const [atOpen, setAtOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const status = String(task?.status || "").toLowerCase();
  const waiting = status === "waiting";
  const started = STARTED.has(status);
  const finished = !waiting && !started;

  useEffect(() => {
    if (!open || !job) return;
    setName(task.name || "");
    setFields({
      prompts: job.prompts.join("\n"),
      model: job.model,
      aspect: job.aspect,
      seconds: null,
      steps: job.steps,
      style: job.style,
      speed: job.speed,
      seed: job.seed,
      negative: job.negative,
    });
    setLengthTouched(false);
    setChoice(waiting ? savedChoice(task) : { when: WHEN.NIGHT_SHIFT });
    setError(null);
    setBusy(false);
  }, [open, task, job, waiting]);

  useEffect(() => {
    if (!open || models) return undefined;
    let alive = true;
    fetch(`${BASE_URL}/batch-video/models`)
      .then((res) => res.json())
      .then((body) => alive && setModels((body?.data?.models || body?.models || []).filter((m) => m.capabilities?.modes?.length)))
      .catch(() => alive && setModels([]));
    return () => {
      alive = false;
    };
  }, [open, models]);

  if (!job || !fields) return null;

  const entry = (models || []).find((m) => m.id === fields.model) || null;
  const caps = entry?.capabilities || null;
  const locked = (key) => job.locked.includes(key);
  const readOnly = started || busy;

  const choices = (models || []).filter((m) => m.is_downloaded && modelFits(m.capabilities, job.role));
  const ratios = caps?.aspect_ratios?.length ? caps.aspect_ratios : FALLBACK_RATIOS;
  const styles = caps?.prompt_styles || (models || []).find((m) => m.capabilities?.prompt_styles)?.capabilities.prompt_styles || [];
  const profiles = Object.entries(caps?.speed_profiles || {});
  const range = lengthRange(caps);
  const floor = stepFloor(caps, fields.speed);
  const usualSteps = defaultSteps(caps, fields.speed);
  const shownSeconds = lengthTouched ? fields.seconds : secondsOf(job, caps);

  const set = (patch) => setFields((prev) => ({ ...prev, ...patch }));

  // A different model declares different shapes, lengths, profiles and styles.
  const pickModel = (id) => {
    const next = (models || []).find((m) => m.id === id)?.capabilities || null;
    const patch = { model: id };
    if (next?.aspect_ratios?.length && fields.aspect && !next.aspect_ratios.includes(fields.aspect)) patch.aspect = "";
    if (fields.speed && !next?.speed_profiles?.[fields.speed]) patch.speed = "";
    if (fields.style && next?.prompt_styles && !next.prompt_styles.includes(fields.style)) patch.style = "";
    if (fields.steps != null) patch.steps = null;
    const seconds = shownSeconds;
    const limit = lengthRange(next);
    if (seconds != null && limit.max && seconds > limit.max) {
      patch.seconds = limit.max;
      setLengthTouched(true);
    }
    set(patch);
  };

  const edited = {
    prompts: job.kind === "batch" && job.variant === "text" && !job.storyboard ? fields.prompts.split("\n") : [fields.prompts],
    model: fields.model,
    aspect: fields.aspect,
    seconds: lengthTouched ? fields.seconds : undefined,
    steps: fields.steps,
    style: fields.style,
    speed: fields.speed,
    seed: fields.seed,
    negative: fields.negative,
  };
  const settingsDirty =
    fields.prompts !== job.prompts.join("\n") || fields.model !== job.model || fields.aspect !== job.aspect
    || lengthTouched || fields.steps !== job.steps || fields.style !== job.style || fields.speed !== job.speed
    || fields.seed !== job.seed || fields.negative !== job.negative;
  const nameDirty = name.trim() !== (task.name || "");
  const whenDirty = waiting && !sameChoice(choice, savedChoice(task));
  const dirty = settingsDirty || nameDirty || whenDirty;

  const problem = !fields.prompts.trim() ? t("jobs.videoJob.needsPrompt")
    : !name.trim() ? t("jobs.videoJob.needsName")
      : shownSeconds != null && range.max && shownSeconds > range.max ? t("jobs.videoJob.tooLong", { max: range.max })
        : shownSeconds != null && shownSeconds < range.min ? t("jobs.videoJob.tooShort", { min: range.min })
          : null;

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      if (waiting) {
        if (settingsDirty || nameDirty) {
          await updateScheduledJob(task.id, {
            name: nameDirty ? name.trim() : undefined,
            request: settingsDirty ? writeVideoJob(task, edited, caps) : undefined,
          });
        }
        if (whenDirty) await scheduleJob(task.id, choice);
      } else {
        const request = settingsDirty ? writeVideoJob(task, edited, caps) : task.workflow_config_parsed?.request;
        const later = choice.when === WHEN.NOW ? { when: WHEN.NIGHT_SHIFT } : choice;
        const body = asNewJob(task, request, name.trim());
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
      label: choice.when === WHEN.AT ? new Date(choice.at).toLocaleString(uiLocale(), { dateStyle: "medium", timeStyle: "short" }) : t("jobs.nightShift.atMenu"),
    },
    ...(waiting ? [{ value: WHEN.HOLD, label: t("jobs.nightShift.when.held"), tooltip: t("jobs.nightShift.pauseJob") }] : []),
  ];

  const batchId = batchIdOf(task);
  const kindLine = [
    t(`jobs.videoJob.role.${job.role}`),
    job.references ? t("jobs.videoJob.references", { count: job.references }) : null,
    job.audio ? t("jobs.videoJob.withSound") : null,
  ].filter(Boolean).join(" · ");
  const manyPrompts = job.kind === "batch" && job.variant === "text" && !job.storyboard;

  return (
    <Dialog open={open} onClose={busy ? undefined : onClose} fullWidth maxWidth="sm" data-testid="video-job-modal">
      <Box sx={{ display: "flex", alignItems: "center", gap: 1, px: 2, pt: 1.25, pb: 0.75 }}>
        <InputBase
          value={name}
          onChange={(e) => setName(e.target.value)}
          readOnly={readOnly}
          inputProps={{ "aria-label": t("jobs.videoJob.name"), "data-testid": "video-job-name" }}
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
        {started && <Alert severity="info" sx={{ py: 0 }}>{t("jobs.videoJob.started")}</Alert>}
        {error && <Alert severity="error" sx={{ py: 0 }} data-testid="video-job-error">{error}</Alert>}

        <Box sx={{ display: "flex", gap: 1.25, alignItems: "flex-start" }}>
          {job.firstImages.slice(0, 1).map((ref) => (
            <Frame key={ref} refValue={ref} label={
              job.firstImages.length > 1 ? t("jobs.videoJob.firstFrames", { count: job.firstImages.length }) : t("jobs.videoJob.firstFrame")
            } />
          ))}
          {job.lastImage && <Frame refValue={job.lastImage} label={t("jobs.videoJob.lastFrame")} />}
          <TextField
            multiline
            minRows={3}
            maxRows={9}
            fullWidth
            size="small"
            label={manyPrompts ? t("jobs.videoJob.prompts") : job.storyboard ? t("jobs.videoJob.concept") : t("jobs.videoJob.prompt")}
            value={fields.prompts}
            onChange={(e) => set({ prompts: e.target.value })}
            InputProps={{ readOnly }}
            inputProps={{ "data-testid": "video-job-prompt" }}
            sx={fieldSx}
          />
        </Box>

        <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr 1fr", sm: "2fr 1fr 1fr" }, gap: 1.25 }}>
          <TextField
            select
            size="small"
            label={t("jobs.videoJob.model")}
            value={fields.model}
            onChange={(e) => pickModel(e.target.value)}
            disabled={readOnly || locked("model")}
            helperText={job.locked.includes("model") && !started ? t("jobs.videoJob.modelLocked") : undefined}
            SelectProps={{ displayEmpty: true }}
            InputLabelProps={{ shrink: true }}
            inputProps={{ "data-testid": "video-job-model" }}
            sx={{ ...fieldSx, gridColumn: { xs: "1 / -1", sm: "auto" } }}
          >
            {!job.locked.includes("model") && <MenuItem value="">{t("jobs.videoJob.activeModel")}</MenuItem>}
            {choices.map((m) => <MenuItem key={m.id} value={m.id}>{m.name}</MenuItem>)}
            {fields.model && !choices.some((m) => m.id === fields.model) && (
              <MenuItem value={fields.model}>{entry?.name || fields.model}</MenuItem>
            )}
          </TextField>
          <TextField
            size="small"
            type="number"
            label={t("jobs.videoJob.length")}
            value={shownSeconds ?? ""}
            placeholder={t("jobs.videoJob.default")}
            onChange={(e) => {
              setLengthTouched(true);
              set({ seconds: numberOrNull(e.target.value) });
            }}
            disabled={readOnly}
            helperText={range.max ? t("jobs.videoJob.lengthRange", { min: range.min, max: range.max }) : undefined}
            InputLabelProps={{ shrink: true }}
            InputProps={{ endAdornment: <InputAdornment position="end">s</InputAdornment> }}
            inputProps={{ min: range.min, max: range.max || undefined, step: 0.5, "data-testid": "video-job-length" }}
            sx={fieldSx}
          />
          <TextField
            size="small"
            type="number"
            label={t("jobs.videoJob.steps")}
            value={fields.steps ?? ""}
            placeholder={usualSteps ? String(usualSteps) : t("jobs.videoJob.default")}
            onChange={(e) => set({ steps: numberOrNull(e.target.value) })}
            onBlur={() => fields.steps != null && fields.steps < floor && set({ steps: floor })}
            disabled={readOnly}
            helperText={caps ? t("jobs.videoJob.stepsFloor", { min: floor }) : undefined}
            InputLabelProps={{ shrink: true }}
            inputProps={{ min: floor, max: 100, step: 1, "data-testid": "video-job-steps" }}
            sx={fieldSx}
          />
          {styles.length > 0 && (
            <TextField
              select
              size="small"
              label={t("jobs.videoJob.style")}
              value={styles.includes(fields.style) ? fields.style : ""}
              onChange={(e) => set({ style: e.target.value })}
              disabled={readOnly}
              SelectProps={{ displayEmpty: true }}
              InputLabelProps={{ shrink: true }}
              inputProps={{ "data-testid": "video-job-style" }}
              sx={fieldSx}
            >
              {job.kind === "tool" && <MenuItem value="">{t("jobs.videoJob.default")}</MenuItem>}
              {styles.map((style) => (
                <MenuItem key={style} value={style}>
                  {t(`jobs.videoJob.styles.${style}`, { defaultValue: style.replace(/_/g, " ") })}
                </MenuItem>
              ))}
            </TextField>
          )}
          {(profiles.length > 0 || fields.speed) && (
            <TextField
              select
              size="small"
              label={t("jobs.videoJob.speed")}
              value={fields.speed}
              onChange={(e) => set({ speed: e.target.value, steps: null })}
              disabled={readOnly || locked("speed")}
              SelectProps={{ displayEmpty: true }}
              InputLabelProps={{ shrink: true }}
              inputProps={{ "data-testid": "video-job-speed" }}
              sx={{ ...fieldSx, gridColumn: { sm: "span 2" } }}
            >
              <MenuItem value="">{t("jobs.videoJob.modelDefault")}</MenuItem>
              {profiles.map(([id, spec]) => <MenuItem key={id} value={id}>{spec.label || id}</MenuItem>)}
              {fields.speed && !profiles.some(([id]) => id === fields.speed) && (
                <MenuItem value={fields.speed}>{fields.speed}</MenuItem>
              )}
            </TextField>
          )}
          {job.has.seed && (
            <TextField
              size="small"
              type="number"
              label={t("jobs.videoJob.seed")}
              value={fields.seed ?? ""}
              placeholder={t("jobs.videoJob.randomSeed")}
              onChange={(e) => set({ seed: numberOrNull(e.target.value) })}
              disabled={readOnly}
              InputLabelProps={{ shrink: true }}
              inputProps={{ step: 1, "data-testid": "video-job-seed" }}
              sx={fieldSx}
            />
          )}
          {job.has.negative && (
            <TextField
              size="small"
              label={t("jobs.videoJob.negative")}
              value={fields.negative}
              onChange={(e) => set({ negative: e.target.value })}
              disabled={readOnly}
              InputLabelProps={{ shrink: true }}
              placeholder={t("jobs.videoJob.modelDefault")}
              inputProps={{ "data-testid": "video-job-negative" }}
              sx={{ ...fieldSx, gridColumn: { xs: "1 / -1", sm: "span 2" } }}
            />
          )}
        </Box>

        <Cluster label={t("jobs.videoJob.shape")} note={job.size && fields.aspect === job.aspect ? job.size : undefined}>
          <Box data-testid="video-job-shape">
            <ChoiceChips
              ariaLabel={t("jobs.videoJob.shape")}
              options={[
                { value: "", label: job.role === "i2v" ? t("jobs.videoJob.shapeOfPicture") : t("jobs.videoJob.modelDefault") },
                ...ratios.map((ratio) => ({ value: ratio, label: ratio })),
                ...(fields.aspect && !ratios.includes(fields.aspect) ? [{ value: fields.aspect, label: fields.aspect }] : []),
              ]}
              value={fields.aspect}
              onChange={(aspect) => set({ aspect })}
              disabled={readOnly}
            />
          </Box>
        </Cluster>

        {!started && (
          <Cluster label={finished ? t("jobs.videoJob.runAgainWhen") : t("jobs.nightShift.whenLabel")}>
            <Box data-testid="video-job-when">
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

        {(task.result || task.error_message || batchId) && (
          <Box sx={{ borderTop: 1, borderColor: "divider", pt: 1 }}>
            {task.error_message && (
              <Typography variant="caption" color="error.main" sx={{ display: "block" }} data-testid="video-job-failure">
                {task.error_message}
              </Typography>
            )}
            {task.result && (
              <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
                {task.result}
              </Typography>
            )}
            {batchId && (
              <Link component={RouterLink} to={`/video?batch=${encodeURIComponent(batchId)}`} variant="caption" onClick={onClose}>
                {t("jobs.videoJob.openClips")}
              </Link>
            )}
          </Box>
        )}
      </DialogContent>

      <DialogActions sx={{ px: 2, pb: 1.5, pt: 0, justifyContent: "space-between" }}>
        <Box>
          {waiting && onRemove && (
            <ActionButton kind="link" onClick={() => onRemove(task)} disabled={busy} data-testid="video-job-remove">
              {t("jobs.nightShift.remove")}
            </ActionButton>
          )}
        </Box>
        <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
          {problem && !started && <Hint sx={{ color: "warning.main" }}>{problem}</Hint>}
          <ActionButton kind="link" onClick={onClose} disabled={busy}>
            {t("common.close")}
          </ActionButton>
          {waiting && (
            <ActionButton
              kind={dirty ? "primary" : "neutral"}
              onClick={save}
              disabled={!dirty || Boolean(problem)}
              loading={busy}
              data-testid="video-job-save"
            >
              {t("dialogs.taskAction.saveChanges")}
            </ActionButton>
          )}
          {finished && (
            <ActionButton
              kind={settingsDirty ? "primary" : "neutral"}
              onClick={save}
              disabled={Boolean(problem)}
              loading={busy}
              tooltip={t("jobs.videoJob.runAgainHelp")}
              data-testid="video-job-again"
            >
              {choice.when === WHEN.NOW ? t("jobs.videoJob.runAgainNow") : t("jobs.videoJob.scheduleAgain")}
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

VideoJobModal.propTypes = {
  open: PropTypes.bool.isRequired,
  task: PropTypes.object,
  onClose: PropTypes.func.isRequired,
  onSaved: PropTypes.func,
  onRemove: PropTypes.func,
};

export default VideoJobModal;
