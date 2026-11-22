// frontend/src/components/jobs/VideoJobModal.jsx
//
// The editor for one video job on the Jobs page: prompt, model, shape,
// length, steps, style and speed profile. JobEditorDialog supplies the frame
// (name, when, save). Choices come from the capability record the model
// declares (/api/batch-video/models); the backend checks the same record on save.
import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Box, InputAdornment, MenuItem, TextField } from "@mui/material";
import ChoiceChips from "../settings/ui/ChoiceChips";
import { Cluster } from "../settings/ui/SettingsCluster";
import JobEditorDialog, { Frame, fieldSx, numberOrNull } from "./JobEditorDialog";
import { BASE_URL } from "../../api/apiClient";
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
import { useUiTranslation } from "../../i18n";

const FALLBACK_RATIOS = ["16:9", "9:16", "1:1"];

const VideoJobModal = ({ open, task, onClose, onSaved, onRemove }) => {
  const { t } = useUiTranslation();
  const job = useMemo(() => (task ? readVideoJob(task) : null), [task]);
  const [models, setModels] = useState(null);
  const [fields, setFields] = useState(null);
  const [lengthTouched, setLengthTouched] = useState(false);

  useEffect(() => {
    if (!open || !job) return;
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
  }, [open, job]);

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

  const choices = (models || []).filter((m) => m.is_downloaded && modelFits(m.capabilities, job.role));
  const ratios = caps?.aspect_ratios?.length ? caps.aspect_ratios : FALLBACK_RATIOS;
  const styles = caps?.prompt_styles || (models || []).find((m) => m.capabilities?.prompt_styles)?.capabilities.prompt_styles || [];
  const profiles = Object.entries(caps?.speed_profiles || {});
  const range = lengthRange(caps);
  const floor = stepFloor(caps, fields.speed);
  const usualSteps = defaultSteps(caps, fields.speed);
  const shownSeconds = lengthTouched ? fields.seconds : secondsOf(job, caps);
  const manyPrompts = job.kind === "batch" && job.variant === "text" && !job.storyboard;

  const set = (patch) => setFields((prev) => ({ ...prev, ...patch }));

  // A different model declares different shapes, lengths, profiles and styles.
  const pickModel = (id) => {
    const next = (models || []).find((m) => m.id === id)?.capabilities || null;
    const patch = { model: id };
    if (next?.aspect_ratios?.length && fields.aspect && !next.aspect_ratios.includes(fields.aspect)) patch.aspect = "";
    if (fields.speed && !next?.speed_profiles?.[fields.speed]) patch.speed = "";
    if (fields.style && next?.prompt_styles && !next.prompt_styles.includes(fields.style)) patch.style = "";
    if (fields.steps != null) patch.steps = null;
    const limit = lengthRange(next);
    if (shownSeconds != null && limit.max && shownSeconds > limit.max) {
      patch.seconds = limit.max;
      setLengthTouched(true);
    }
    set(patch);
  };

  const edited = {
    prompts: manyPrompts ? fields.prompts.split("\n") : [fields.prompts],
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

  const problem = !fields.prompts.trim() ? t("jobs.jobEditor.needsPrompt")
    : shownSeconds != null && range.max && shownSeconds > range.max ? t("jobs.videoJob.tooLong", { max: range.max })
      : shownSeconds != null && shownSeconds < range.min ? t("jobs.videoJob.tooShort", { min: range.min })
        : null;

  const batchId = batchIdOf(task);
  const kindLine = [
    t(`jobs.videoJob.role.${job.role}`),
    job.references ? t("jobs.videoJob.references", { count: job.references }) : null,
    job.audio ? t("jobs.videoJob.withSound") : null,
  ].filter(Boolean).join(" · ");

  return (
    <JobEditorDialog
      open={open}
      task={task}
      testId="video-job"
      kindLine={kindLine}
      settingsDirty={settingsDirty}
      problem={problem}
      buildRequest={() => writeVideoJob(task, edited, caps)}
      asNewJob={(request, name) => asNewJob(task, request, name)}
      outputLink={batchId ? { to: `/video?batch=${encodeURIComponent(batchId)}`, label: t("jobs.videoJob.openClips") } : null}
      onClose={onClose}
      onSaved={onSaved}
      onRemove={onRemove}
    >
      {({ readOnly }) => (
        <>
          <Box sx={{ display: "flex", gap: 1.25, alignItems: "flex-start" }}>
            {job.firstImages.slice(0, 1).map((ref) => (
              <Frame key={ref} url={frameUrl(ref)} name={ref} label={
                job.firstImages.length > 1 ? t("jobs.videoJob.firstFrames", { count: job.firstImages.length }) : t("jobs.videoJob.firstFrame")
              } />
            ))}
            {job.lastImage && <Frame url={frameUrl(job.lastImage)} name={job.lastImage} label={t("jobs.videoJob.lastFrame")} />}
            <TextField
              multiline
              minRows={3}
              maxRows={9}
              fullWidth
              size="small"
              label={manyPrompts ? t("jobs.jobEditor.prompts") : job.storyboard ? t("jobs.videoJob.concept") : t("jobs.jobEditor.prompt")}
              value={fields.prompts}
              onChange={(e) => set({ prompts: e.target.value })}
              slotProps={{ input: { readOnly }, htmlInput: { "data-testid": "video-job-prompt" } }}
              sx={fieldSx}
            />
          </Box>

          <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr 1fr", sm: "2fr 1fr 1fr" }, gap: 1.25 }}>
            <TextField
              select
              size="small"
              label={t("jobs.jobEditor.model")}
              value={fields.model}
              onChange={(e) => pickModel(e.target.value)}
              disabled={readOnly || locked("model")}
              helperText={job.locked.includes("model") ? t("jobs.videoJob.modelLocked") : undefined}
              slotProps={{
                htmlInput: { "data-testid": "video-job-model" },
                inputLabel: { shrink: true },
                select: { displayEmpty: true },
              }}
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
              placeholder={t("jobs.jobEditor.default")}
              onChange={(e) => {
                setLengthTouched(true);
                set({ seconds: numberOrNull(e.target.value) });
              }}
              disabled={readOnly}
              helperText={range.max ? t("jobs.videoJob.lengthRange", { min: range.min, max: range.max }) : undefined}
              slotProps={{
                input: { endAdornment: <InputAdornment position="end">s</InputAdornment> },
                htmlInput: { min: range.min, max: range.max || undefined, step: 0.5, "data-testid": "video-job-length" },
                inputLabel: { shrink: true },
              }}
              sx={fieldSx}
            />
            <TextField
              size="small"
              type="number"
              label={t("jobs.jobEditor.steps")}
              value={fields.steps ?? ""}
              placeholder={usualSteps ? String(usualSteps) : t("jobs.jobEditor.default")}
              onChange={(e) => set({ steps: numberOrNull(e.target.value) })}
              onBlur={() => fields.steps != null && fields.steps < floor && set({ steps: floor })}
              disabled={readOnly}
              helperText={caps ? t("jobs.videoJob.stepsFloor", { min: floor }) : undefined}
              slotProps={{
                htmlInput: { min: floor, max: 100, step: 1, "data-testid": "video-job-steps" },
                inputLabel: { shrink: true },
              }}
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
                slotProps={{
                  htmlInput: { "data-testid": "video-job-style" },
                  inputLabel: { shrink: true },
                  select: { displayEmpty: true },
                }}
                sx={fieldSx}
              >
                {job.kind === "tool" && <MenuItem value="">{t("jobs.jobEditor.default")}</MenuItem>}
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
                slotProps={{
                  htmlInput: { "data-testid": "video-job-speed" },
                  inputLabel: { shrink: true },
                  select: { displayEmpty: true },
                }}
                sx={{ ...fieldSx, gridColumn: { sm: "span 2" } }}
              >
                <MenuItem value="">{t("jobs.jobEditor.modelDefault")}</MenuItem>
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
                label={t("jobs.jobEditor.seed")}
                value={fields.seed ?? ""}
                placeholder={t("jobs.jobEditor.randomSeed")}
                onChange={(e) => set({ seed: numberOrNull(e.target.value) })}
                disabled={readOnly}
                slotProps={{ htmlInput: { step: 1, "data-testid": "video-job-seed" }, inputLabel: { shrink: true } }}
                sx={fieldSx}
              />
            )}
            {job.has.negative && (
              <TextField
                size="small"
                label={t("jobs.jobEditor.negative")}
                value={fields.negative}
                onChange={(e) => set({ negative: e.target.value })}
                disabled={readOnly}
                placeholder={t("jobs.jobEditor.modelDefault")}
                slotProps={{ htmlInput: { "data-testid": "video-job-negative" }, inputLabel: { shrink: true } }}
                sx={{ ...fieldSx, gridColumn: { xs: "1 / -1", sm: "span 2" } }}
              />
            )}
          </Box>

          <Cluster label={t("jobs.videoJob.shape")} note={job.size && fields.aspect === job.aspect ? job.size : undefined}>
            <Box data-testid="video-job-shape">
              <ChoiceChips
                ariaLabel={t("jobs.videoJob.shape")}
                options={[
                  { value: "", label: job.role === "i2v" ? t("jobs.videoJob.shapeOfPicture") : t("jobs.jobEditor.modelDefault") },
                  ...ratios.map((ratio) => ({ value: ratio, label: ratio })),
                  ...(fields.aspect && !ratios.includes(fields.aspect) ? [{ value: fields.aspect, label: fields.aspect }] : []),
                ]}
                value={fields.aspect}
                onChange={(aspect) => set({ aspect })}
                disabled={readOnly}
              />
            </Box>
          </Cluster>
        </>
      )}
    </JobEditorDialog>
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
