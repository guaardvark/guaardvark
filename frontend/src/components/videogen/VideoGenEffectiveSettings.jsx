import React from "react";
import { Box, Chip, Typography } from "@mui/material";
import {
  MODEL_OPTIONS,
  modelChipLabel,
  PROMPT_STYLES,
} from "../../constants/videoGeneratorPresets";
import { useUiTranslation } from "../../i18n";

/**
 * Compact "what will actually run" summary derived from computedParams.
 * Family chip is MODEL_OPTIONS[computedParams.model].type — never a CogVideoX default.
 */
export default function VideoGenEffectiveSettings({
  model,
  computedParams,
  cinematicKeyframe,
  selectedSubjectIds = [],
  keyframeModel,
  directorMode,
  faceRestore,
  capabilities = null,
}) {
  const { t } = useUiTranslation();
  if (!computedParams) return null;
  const runningId = computedParams.model || model;
  const family = MODEL_OPTIONS[runningId]?.type;
  const familyColor =
    family === "ltx" ? "warning"
    : family === "minimax" ? "success"
    : family === "wan" ? "secondary"
    : family === "hunyuan" ? "info"
    : family === "cogvideox" ? "primary"
    : "default";
  return (
    <Box sx={{ mb: 2 }}>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ mb: 1, display: "block", fontWeight: 500 }}
      >
        {t("video.effective.title")}
      </Typography>
      <Box
        sx={{
          display: "flex",
          gap: 1,
          flexWrap: "wrap",
          alignItems: "center",
          p: 1.5,
          borderRadius: 1,
          bgcolor: "action.hover",
        }}
      >
        <Chip
          size="small"
          color={familyColor}
          label={modelChipLabel(runningId)}
          data-testid="effective-model"
          data-model={runningId}
          title={MODEL_OPTIONS[runningId]?.label || runningId}
          sx={{ fontWeight: 600 }}
        />
        <Chip size="small" variant="outlined" label={t("video.effective.steps", { count: computedParams.num_inference_steps })} />
        {computedParams.speed_profile && computedParams.speed_profile !== "standard" && (
          <Chip
            size="small"
            variant="outlined"
            color="success"
            label={capabilities?.speed_profiles?.[computedParams.speed_profile]?.label || computedParams.speed_profile}
          />
        )}
        {capabilities?.audio_out && (
          <Chip size="small" variant="outlined" color="success" label={t("video.effective.nativeAudio")} />
        )}
        {computedParams.style_embedding && (
          <Chip size="small" variant="outlined" label={t("video.effective.styleEmbedding", { style: computedParams.style_embedding })} />
        )}
        <Chip size="small" variant="outlined" label={t("video.effective.frames", { count: computedParams.duration_frames })} />
        <Chip size="small" variant="outlined" label={t("video.effective.fps", { fps: computedParams.fps })} />
        <Chip
          size="small"
          variant="outlined"
          label={t("video.effective.seconds", { seconds: (computedParams.duration_frames / computedParams.fps).toFixed(1) })}
        />
        <Chip
          size="small"
          variant="outlined"
          label={`${computedParams.width}×${computedParams.height}`}
        />
        {(cinematicKeyframe || selectedSubjectIds.length > 0) && (
          <Chip size="small" variant="outlined" color="secondary" label={t("video.effective.keyframeModel", { model: keyframeModel })} />
        )}
        {directorMode && (
          <Chip size="small" variant="outlined" color="secondary" label={t("video.effective.director")} />
        )}
        {faceRestore && (
          <Chip size="small" variant="outlined" color="success" label={t("video.effective.faceRestore")} />
        )}
        {computedParams.interpolation_multiplier > 1 && (
          <Chip
            size="small"
            variant="outlined"
            color="info"
            label={t("video.effective.interpolation", { multiplier: computedParams.interpolation_multiplier })}
          />
        )}
        {computedParams.upscale && (
          <Chip size="small" variant="outlined" color="secondary" label={t("video.effective.upscale")} />
        )}
        {computedParams.enhance_prompt && computedParams.prompt_style !== "none" && (
          <Chip
            size="small"
            variant="outlined"
            color="warning"
            label={t("video.effective.promptStyle", { style: PROMPT_STYLES[computedParams.prompt_style]?.label || computedParams.prompt_style })}
          />
        )}
        {computedParams.feta_weight && (
          <Chip
            size="small"
            variant="outlined"
            color="success"
            label={`FETA ${computedParams.feta_weight}`}
          />
        )}
        {(computedParams.subject_ids || []).length > 0 && (
          <Chip
            size="small"
            variant="outlined"
            color="secondary"
            label={t("video.effective.cast", { count: computedParams.subject_ids.length })}
          />
        )}
      </Box>
    </Box>
  );
}
