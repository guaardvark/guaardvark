// frontend/src/components/jobs/ImageJobModal.jsx
//
// The editor for one image job on the Jobs page: prompt, model, size, steps
// and style. JobEditorDialog supplies the frame (name, when, save). The
// model list and each model's step range and largest picture come from
// /api/batch-image/models and /model-info.
import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Box, MenuItem, TextField } from "@mui/material";
import ChoiceChips from "../settings/ui/ChoiceChips";
import { Cluster } from "../settings/ui/SettingsCluster";
import JobEditorDialog, { fieldSx, numberOrNull } from "./JobEditorDialog";
import { BASE_URL } from "../../api/apiClient";
import {
  asNewImageJob,
  imageBatchIdOf,
  readImageJob,
  sizeChoices,
  sizeKey,
  writeImageJob,
} from "./imageJob";
import { useUiTranslation } from "../../i18n";

const getJson = async (path) => {
  const body = await (await fetch(`${BASE_URL}${path}`)).json();
  return body?.data ?? body;
};

const ImageJobModal = ({ open, task, onClose, onSaved, onRemove }) => {
  const { t } = useUiTranslation();
  const job = useMemo(() => (task ? readImageJob(task) : null), [task]);
  const [catalog, setCatalog] = useState(null); // { models, auto_model }
  const [limits, setLimits] = useState({}); // model id -> its model-info configuration
  const [fields, setFields] = useState(null);

  useEffect(() => {
    if (!open || !job) return;
    setFields({
      prompts: job.prompts.join("\n"),
      model: job.model,
      width: job.width,
      height: job.height,
      steps: job.steps,
      style: job.style,
      seed: job.seed,
      negative: job.negative,
    });
  }, [open, job]);

  useEffect(() => {
    if (!open || catalog) return undefined;
    let alive = true;
    getJson("/batch-image/models")
      .then((data) => alive && setCatalog({ models: data?.models || [], auto_model: data?.auto_model || null }))
      .catch(() => alive && setCatalog({ models: [], auto_model: null }));
    return () => {
      alive = false;
    };
  }, [open, catalog]);

  // Auto renders with the model the listing says it resolves to today.
  const resolved = fields ? (fields.model === "auto" ? catalog?.auto_model : fields.model) : null;
  useEffect(() => {
    if (!open || !resolved || limits[resolved] !== undefined) return undefined;
    let alive = true;
    getJson(`/batch-image/model-info/${encodeURIComponent(resolved)}`)
      .then((data) => alive && setLimits((prev) => ({ ...prev, [resolved]: data?.configuration || null })))
      .catch(() => alive && setLimits((prev) => ({ ...prev, [resolved]: null })));
    return () => {
      alive = false;
    };
  }, [open, resolved, limits]);

  if (!job || !fields) return null;

  const limit = (resolved && limits[resolved]) || null;
  const models = catalog?.models || [];
  const installed = models.filter((m) => m.is_downloaded);
  const nameOf = (id) => models.find((m) => m.id === id)?.name || id;
  const stepRange = limit?.steps_range || null;
  const sizes = sizeChoices({ ...job, width: fields.width, height: fields.height }, limit);
  const maxPixels = limit?.max_pixels || null;
  const set = (patch) => setFields((prev) => ({ ...prev, ...patch }));

  const edited = {
    prompts: job.kind === "batch" ? fields.prompts.split("\n") : [fields.prompts],
    model: fields.model,
    width: fields.width,
    height: fields.height,
    steps: fields.steps,
    style: fields.style,
    seed: fields.seed,
    negative: fields.negative,
  };
  const settingsDirty =
    fields.prompts !== job.prompts.join("\n") || fields.model !== job.model || fields.width !== job.width
    || fields.height !== job.height || fields.steps !== job.steps || fields.style !== job.style
    || fields.seed !== job.seed || fields.negative !== job.negative;

  const problem = !fields.prompts.trim() ? t("jobs.jobEditor.needsPrompt")
    : maxPixels && fields.width && fields.height && fields.width * fields.height > maxPixels
      ? t("jobs.imageJob.tooLarge", { max: Math.round(maxPixels / 100000) / 10 })
      : null;

  const count = job.kind === "batch" ? fields.prompts.split("\n").filter((p) => p.trim()).length : 1;
  const kindLine = [
    job.kind === "batch" ? t("jobs.imageJob.kindMany", { count }) : t("jobs.imageJob.kind"),
    job.cast ? t("jobs.imageJob.cast", { count: job.cast }) : null,
  ].filter(Boolean).join(" · ");
  const batchId = imageBatchIdOf(task);
  const modelLocked = job.locked.includes("model");

  return (
    <JobEditorDialog
      open={open}
      task={task}
      testId="image-job"
      kindLine={kindLine}
      settingsDirty={settingsDirty}
      problem={problem}
      buildRequest={() => writeImageJob(task, edited)}
      asNewJob={(request, name) => asNewImageJob(task, request, name)}
      outputLink={batchId ? { to: `/batch-images?batch=${encodeURIComponent(batchId)}`, label: t("jobs.imageJob.openImages") } : null}
      onClose={onClose}
      onSaved={onSaved}
      onRemove={onRemove}
    >
      {({ readOnly }) => (
        <>
          <TextField
            multiline
            minRows={3}
            maxRows={9}
            fullWidth
            size="small"
            label={job.kind === "batch" ? t("jobs.jobEditor.prompts") : t("jobs.jobEditor.prompt")}
            value={fields.prompts}
            onChange={(e) => set({ prompts: e.target.value })}
            InputProps={{ readOnly }}
            inputProps={{ "data-testid": "image-job-prompt" }}
            sx={fieldSx}
          />

          <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr 1fr", sm: "2fr 1fr 1fr" }, gap: 1.25 }}>
            <TextField
              select
              size="small"
              label={t("jobs.jobEditor.model")}
              value={fields.model}
              // Steps are counted for one model; another model starts from its own.
              onChange={(e) => set({ model: e.target.value, steps: null })}
              disabled={readOnly || modelLocked}
              helperText={modelLocked ? t("jobs.imageJob.modelLocked") : undefined}
              InputLabelProps={{ shrink: true }}
              inputProps={{ "data-testid": "image-job-model" }}
              sx={{ ...fieldSx, gridColumn: { xs: "1 / -1", sm: "auto" } }}
            >
              <MenuItem value="auto">
                {catalog?.auto_model ? t("jobs.imageJob.autoNamed", { model: nameOf(catalog.auto_model) }) : t("jobs.imageJob.auto")}
              </MenuItem>
              {installed.map((m) => <MenuItem key={m.id} value={m.id}>{m.name}</MenuItem>)}
              {fields.model !== "auto" && !installed.some((m) => m.id === fields.model) && (
                <MenuItem value={fields.model}>{nameOf(fields.model)}</MenuItem>
              )}
            </TextField>
            <TextField
              select
              size="small"
              label={t("jobs.jobEditor.style")}
              value={fields.style}
              onChange={(e) => set({ style: e.target.value })}
              disabled={readOnly}
              InputLabelProps={{ shrink: true }}
              inputProps={{ "data-testid": "image-job-style" }}
              sx={fieldSx}
            >
              {[...job.styles, ...(job.styles.includes(fields.style) ? [] : [fields.style])].map((style) => (
                <MenuItem key={style} value={style}>
                  {t(`jobs.imageJob.styles.${style}`, { defaultValue: style.replace(/[-_]/g, " ") })}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              size="small"
              type="number"
              label={t("jobs.jobEditor.steps")}
              value={fields.steps ?? ""}
              placeholder={limit?.recommended_steps ? String(limit.recommended_steps) : t("jobs.jobEditor.default")}
              onChange={(e) => set({ steps: numberOrNull(e.target.value) })}
              onBlur={() => {
                if (fields.steps == null || !stepRange) return;
                set({ steps: Math.max(stepRange[0], Math.min(stepRange[1], Math.round(fields.steps))) });
              }}
              disabled={readOnly}
              helperText={stepRange ? t("jobs.imageJob.stepsRange", { min: stepRange[0], max: stepRange[1] }) : undefined}
              InputLabelProps={{ shrink: true }}
              inputProps={{ min: stepRange?.[0] || 1, max: stepRange?.[1] || 100, step: 1, "data-testid": "image-job-steps" }}
              sx={fieldSx}
            />
            {job.has.seed && (
              <TextField
                size="small"
                type="number"
                label={t("jobs.jobEditor.seed")}
                value={fields.seed ?? ""}
                placeholder={t("jobs.jobEditor.randomSeed")}
                onChange={(e) => set({ seed: numberOrNull(e.target.value) })}
                disabled={readOnly}
                InputLabelProps={{ shrink: true }}
                inputProps={{ min: 0, step: 1, "data-testid": "image-job-seed" }}
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
                InputLabelProps={{ shrink: true }}
                inputProps={{ "data-testid": "image-job-negative" }}
                sx={{ ...fieldSx, gridColumn: { xs: "1 / -1", sm: "span 2" } }}
              />
            )}
          </Box>

          <Cluster label={t("jobs.imageJob.size")}>
            <Box data-testid="image-job-size">
              <ChoiceChips
                ariaLabel={t("jobs.imageJob.size")}
                options={sizes.map(([w, h]) => ({ value: sizeKey(w, h), label: `${w} × ${h}` }))}
                value={fields.width && fields.height ? sizeKey(fields.width, fields.height) : ""}
                onChange={(key) => {
                  const [width, height] = key.split("x").map(Number);
                  set({ width, height });
                }}
                disabled={readOnly}
              />
            </Box>
          </Cluster>
        </>
      )}
    </JobEditorDialog>
  );
};

ImageJobModal.propTypes = {
  open: PropTypes.bool.isRequired,
  task: PropTypes.object,
  onClose: PropTypes.func.isRequired,
  onSaved: PropTypes.func,
  onRemove: PropTypes.func,
};

export default ImageJobModal;
