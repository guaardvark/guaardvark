// The Video Gen Settings card: how to make it. Pipeline, model, duration, size,
// quality and the advanced controls, with the Add to queue button.

import React, { memo } from "react";
import {
  Box,
  TextField,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Collapse,
} from "@mui/material";
import GpuGateBanner from "../../common/GpuGateBanner";
import {
  SettingChip,
  ChoiceChips,
  ActionButton,
  SettingsPanel,
  Cluster,
  Line,
  Hint,
} from "../../settings/ui";
import {
  WAN5B_SAMPLER_PROFILES,
  MOTION_PRESETS,
  OUTPUT_QUALITY_TIERS,
  DEFAULT_KEYFRAME_MODEL,
  PROMPT_STYLES,
  VIDEO_SIZE_PRESETS,
  MODEL_OPTIONS,
  isCogVideoXModel,
  MAX_SEED,
  isLtxModel,
  isMinimaxModel,
} from "../../../constants/videoGeneratorPresets";
import { comfyUiLink } from "../../../utils/comfyUiLink";
import VideoGenEffectiveSettings from "../VideoGenEffectiveSettings";
import { Trans } from "../../../i18n";
import { stripGlyph, composeCardSx } from "../videoGenPage";
import { useUiTranslation } from "../../../i18n";

function VideoSettingsCard({
  activeSpeedProfile,
  advancedParams,
  allowedAspectRatios,
  anyModelReady,
  applicableAdapters,
  applicableEncoders,
  aspectRatio,
  audioGuide,
  availableModels,
  blockDetail,
  blockReason,
  canQueue,
  castError,
  castIdentityLocked,
  castSubjects,
  cinematicKeyframe,
  comfyUiUrl,
  computedParams,
  controlsDisabled,
  directorGuidance,
  directorMode,
  durationPreset,
  durationPresets,
  faceRestoreAvailable,
  faceRestoreModelReady,
  faceRestoreNodeAvailable,
  fetaEnabled,
  fetaWeight,
  gpuBusy,
  handleAudioGuideUpload,
  handleGenerate,
  highConsistencyMode,
  inputMode,
  isGenerating,
  isUploadingAudioGuide,
  keyframeModel,
  keyframeModelOptions,
  model,
  modelCaps,
  modelMeta,
  motionPreset,
  offeredStyles,
  postUpscale,
  profileSteps,
  promptControls,
  promptStyle,
  qualityChipOptions,
  qualityPreset,
  qualityTier,
  selectedSubjectIds,
  setAdvancedParams,
  setAspectRatio,
  setAudioGuide,
  setCastReload,
  setCinematicKeyframe,
  setDirectorGuidance,
  setDirectorMode,
  setDurationPreset,
  setFetaEnabled,
  setFetaWeight,
  setHighlightModelId,
  setKeyframeModel,
  setModel,
  setMotionPreset,
  setPostUpscale,
  setPromptStyle,
  setQualityPreset,
  setQualityTier,
  setSelectedSubjectIds,
  setStoryboardMode,
  setStoryboardShots,
  setVideoModelsModalOpen,
  setVideoSize,
  storyboardMode,
  storyboardShots,
  videoSize,
  voiceClips,
}) {
  const { t } = useUiTranslation();
  const composeSx = composeCardSx(controlsDisabled);
  return (
        <Box sx={composeSx}>
    {inputMode !== "references" && (
    <SettingsPanel
      id="videogen-pipeline"
      title={t("videoGenerator.pipeline.title")}
      description={t("videoGenerator.pipeline.description")}
    >
      <Line>
        <SettingChip
          label={t("videoGenerator.pipeline.director")}
          on={directorMode}
          tooltip={t("videoGenerator.pipeline.directorTooltip")}
          onToggle={setDirectorMode}
        />
        <SettingChip
          label={t("videoGenerator.pipeline.keyframe")}
          on={cinematicKeyframe || selectedSubjectIds.length > 0}
          disabled={selectedSubjectIds.length > 0}
          tooltip={
            selectedSubjectIds.length > 0
              ? t("videoGenerator.pipeline.keyframeCast")
              : t("videoGenerator.pipeline.keyframeTooltip")
          }
          onToggle={setCinematicKeyframe}
        />
        <SettingChip
          label={t("videoGenerator.pipeline.storyboard")}
          on={storyboardMode}
          tooltip={t("videoGenerator.pipeline.storyboardTooltip")}
          onToggle={setStoryboardMode}
        />
        {storyboardMode && (
          <TextField
            type="number"
            label={t("videoGenerator.pipeline.shots")}
            value={storyboardShots}
            onChange={(e) => setStoryboardShots(Math.max(1, Math.min(50, parseInt(e.target.value, 10) || 1)))}
            size="small"
            sx={{ width: 96 }}
            inputProps={{ min: 1, max: 50 }}
          />
        )}
      </Line>
      {(directorMode || storyboardMode) && (
        <TextField
          value={directorGuidance}
          onChange={(e) => setDirectorGuidance(e.target.value)}
          placeholder={t("videoGenerator.pipeline.guidancePlaceholder")}
          size="small"
          fullWidth
        />
      )}
      <Collapse in={cinematicKeyframe || selectedSubjectIds.length > 0}>
        {castIdentityLocked ? (
          <Hint>{t("videoGenerator.pipeline.keyframeFromCast")}</Hint>
        ) : (
          <FormControl size="small" fullWidth sx={{ mt: 0.5 }}>
            <InputLabel>{t("videoGenerator.pipeline.keyframeModel")}</InputLabel>
            <Select
              value={keyframeModel === "from-lora" ? DEFAULT_KEYFRAME_MODEL : keyframeModel}
              onChange={(e) => setKeyframeModel(e.target.value)}
              label={t("videoGenerator.pipeline.keyframeModel")}
            >
              {keyframeModelOptions.map(([key, cfg]) => (
                <MenuItem key={key} value={key}>
                  {cfg.label}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        )}
      </Collapse>
      <FormControl size="small" fullWidth>
        <InputLabel>{t("videoGenerator.pipeline.cast")}</InputLabel>
        <Select
          multiple
          value={selectedSubjectIds}
          onChange={(e) => {
            const v = e.target.value;
            setSelectedSubjectIds(typeof v === "string" ? v.split(",").map(Number) : v);
          }}
          label={t("videoGenerator.pipeline.cast")}
          renderValue={(sel) =>
            castSubjects.filter((s) => sel.includes(s.id)).map((s) => s.name).join(", ") || t("videoGenerator.common.none")
          }
        >
          {castSubjects.map((s) => (
            <MenuItem key={s.id} value={s.id}>
              {s.name}{s.trigger_word ? ` (${s.trigger_word})` : ""}
            </MenuItem>
          ))}
        </Select>
      </FormControl>
      <Hint>
        {castError ? (
          <Box component="span" sx={{ color: "error.main" }}>
            <Trans
              i18nKey="videoGenerator.pipeline.castLoadFailed"
              values={{ reason: castError }}
              components={{
                retry: (
                  <Box
                    component="button"
                    type="button"
                    onClick={() => setCastReload((n) => n + 1)}
                    sx={{ background: "none", border: 0, p: 0, color: "inherit", textDecoration: "underline", cursor: "pointer", font: "inherit" }}
                  />
                ),
              }}
            />
          </Box>
        ) : castSubjects.length === 0
          ? t("videoGenerator.pipeline.noCast")
          : t("videoGenerator.pipeline.castHelp")}
      </Hint>
    </SettingsPanel>
    )}

    <SettingsPanel id="videogen-clip" title={t("videoGenerator.clip.title")}>
      <Cluster label={t("videoGenerator.clip.model")}>
        <Line nowrap>
          <FormControl size="small" className="grow">
            <InputLabel>{t("videoGenerator.clip.model")}</InputLabel>
            <Select value={model} onChange={(e) => setModel(e.target.value)} label={t("videoGenerator.clip.model")}>
              {availableModels.map(([key, opt]) => (
                <MenuItem key={key} value={key}>
                  {opt.label}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
          <ActionButton onClick={() => setVideoModelsModalOpen(true)}>{t("videoGenerator.clip.manageModels")}</ActionButton>
          <ActionButton
            kind="link"
            tooltip={t("videoGenerator.clip.comfyTooltip", { url: comfyUiLink(window.location.hostname, comfyUiUrl) })}
            onClick={() => window.open(comfyUiLink(window.location.hostname, comfyUiUrl), "_blank", "noopener")}
          >
            ComfyUI
          </ActionButton>
        </Line>
        {applicableAdapters.length > 0 && (
          <>
            <Line wrap>
              {applicableAdapters.map((a) => {
                const on = (advancedParams.adapters || []).includes(a.id);
                return (
                  <SettingChip
                    key={a.id}
                    label={a.name}
                    on={on}
                    onToggle={() => {
                      setAdvancedParams((prev) => {
                        const cur = prev.adapters || [];
                        return {
                          ...prev,
                          adapters: on ? cur.filter((id) => id !== a.id) : [...cur, a.id],
                        };
                      });
                    }}
                  />
                );
              })}
            </Line>
            <Hint>{t("videoGenerator.clip.adaptersHint")}</Hint>
          </>
        )}
        {applicableEncoders.length > 0 && (
          <>
            <Line>
              <FormControl size="small" sx={{ minWidth: 260 }}>
                <InputLabel>{t("videoGenerator.clip.textEncoder")}</InputLabel>
                <Select
                  value={applicableEncoders.some((e) => e.id === advancedParams.text_encoder) ? advancedParams.text_encoder : ""}
                  label={t("videoGenerator.clip.textEncoder")}
                  onChange={(e) => setAdvancedParams((prev) => ({ ...prev, text_encoder: e.target.value }))}
                >
                  <MenuItem value="">{t("videoGenerator.clip.shippedEncoder")}</MenuItem>
                  {applicableEncoders.map((e) => (
                    <MenuItem key={e.id} value={e.id}>
                      {e.name}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
            </Line>
            <Hint>{t("videoGenerator.clip.encodersHint")}</Hint>
          </>
        )}
        {modelMeta[model]?.license?.name && (
          <Hint>
            {modelMeta[model].license.attribution} · {modelMeta[model].license.name}. {modelMeta[model].license.note}
          </Hint>
        )}
        {anyModelReady === false && (
          <Hint>{t("videoGenerator.clip.noModel")}</Hint>
        )}
      </Cluster>
      {qualityChipOptions.length > 0 && (
        <Cluster
          label={t("videoGenerator.clip.steps")}
          note={profileSteps.fixed
            ? t("videoGenerator.clip.stepsFixed", { profile: profileSteps.label })
            : t("videoGenerator.clip.stepsFloor")}
        >
          <ChoiceChips
            ariaLabel={t("videoGenerator.clip.qualitySteps")}
            value={qualityPreset}
            onChange={setQualityPreset}
            disabled={profileSteps.fixed}
            options={qualityChipOptions}
          />
        </Cluster>
      )}
      <Cluster label={t("videoGenerator.clip.duration")}>
        <ChoiceChips
          ariaLabel={t("videoGenerator.clip.duration")}
          value={durationPreset}
          onChange={setDurationPreset}
          options={Object.entries(durationPresets).map(([key, preset]) => ({
            value: key,
            label: stripGlyph(preset.label),
            tooltip: preset.description,
          }))}
        />
      </Cluster>
      <Cluster label={t("videoGenerator.clip.motion")} note={promptControls.motion.note}>
        <ChoiceChips
          ariaLabel={t("videoGenerator.clip.motion")}
          value={motionPreset}
          onChange={setMotionPreset}
          disabled={!promptControls.motion.active}
          options={Object.entries(MOTION_PRESETS).map(([key, preset]) => ({
            value: key,
            label: stripGlyph(preset.label),
            tooltip: preset.description,
          }))}
        />
      </Cluster>
      {isCogVideoXModel(model, modelMeta[model]) && !MODEL_OPTIONS[model]?.canvas ? (
        <Hint>{t("videoGenerator.clip.cogFixed", { width: computedParams.width, height: computedParams.height })}</Hint>
      ) : (
        <>
          <Cluster label={t("videoGenerator.clip.aspect")}>
            <ChoiceChips
              ariaLabel={t("videoGenerator.clip.aspectRatio")}
              value={aspectRatio}
              onChange={setAspectRatio}
              options={Object.entries(allowedAspectRatios).map(([key, preset]) => ({
                value: key,
                label: preset.label,
                tooltip: preset.description,
              }))}
            />
          </Cluster>
          <Cluster label={t("videoGenerator.clip.size")} note={t("videoGenerator.clip.renders", { width: computedParams.width, height: computedParams.height })}>
            <ChoiceChips
              ariaLabel={t("videoGenerator.clip.videoSize")}
              value={videoSize}
              onChange={setVideoSize}
              disabled={isLtxModel(model, modelMeta[model]) || isMinimaxModel(model, modelMeta[model]) || !!MODEL_OPTIONS[model]?.canvas}
              options={Object.entries(VIDEO_SIZE_PRESETS).map(([key, preset]) => ({
                value: key,
                label: stripGlyph(preset.label),
                tooltip: preset.description,
              }))}
            />
          </Cluster>
        </>
      )}
      <Cluster label={t("videoGenerator.clip.output")}>
        <ChoiceChips
          ariaLabel={t("videoGenerator.clip.outputQuality")}
          value={qualityTier}
          onChange={setQualityTier}
          options={Object.entries(OUTPUT_QUALITY_TIERS).map(([key, tier]) => ({
            value: key,
            label: tier.label,
            tooltip: tier.description,
          }))}
        />
      </Cluster>
    </SettingsPanel>

    <SettingsPanel id="videogen-finish" title={t("videoGenerator.finish.title")}>
      <Line>
        <SettingChip
          label={t("videoGenerator.finish.fixAnatomy")}
          on={advancedParams.face_restore}
          disabled={!faceRestoreAvailable}
          tooltip={
            faceRestoreAvailable
              ? t("videoGenerator.finish.faceRestore")
              : !faceRestoreNodeAvailable
                ? t("videoGenerator.finish.nodeMissing")
                : t("videoGenerator.finish.weightsMissing")
          }
          onToggle={(next) => setAdvancedParams({ ...advancedParams, face_restore: next })}
          onSettings={
            faceRestoreNodeAvailable && faceRestoreModelReady === false
              ? () => {
                  setHighlightModelId("codeformer");
                  setVideoModelsModalOpen(true);
                }
              : undefined
          }
        />
        <SettingChip
          label={t("videoGenerator.finish.upscale")}
          on={postUpscale || qualityTier === "cinema"}
          disabled={qualityTier === "cinema"}
          tooltip={qualityTier === "cinema" ? t("videoGenerator.finish.upscaleCinema") : t("videoGenerator.finish.upscaleTooltip")}
          onToggle={setPostUpscale}
        />
        <SettingChip
          label={t("videoGenerator.finish.pngFrames")}
          on={advancedParams.generate_frames_only}
          tooltip={t("videoGenerator.finish.pngFramesTooltip")}
          onToggle={(next) => setAdvancedParams({ ...advancedParams, generate_frames_only: next })}
        />
        {isCogVideoXModel(model, modelMeta[model]) && (
          <SettingChip
            label="FETA"
            on={fetaEnabled || qualityTier === "cinema" || highConsistencyMode}
            disabled={qualityTier === "cinema" || highConsistencyMode}
            tooltip={t("videoGenerator.finish.fetaTooltip")}
            onToggle={setFetaEnabled}
          />
        )}
      </Line>
      {isCogVideoXModel(model, modelMeta[model]) && (fetaEnabled || qualityTier === "cinema" || highConsistencyMode) && (
        <TextField
          size="small"
          label={t("videoGenerator.finish.fetaWeight")}
          type="number"
          inputProps={{ step: 0.1, min: 0.1, max: 3.0 }}
          value={fetaWeight}
          onChange={(e) => setFetaWeight(Number(e.target.value))}
          sx={{ width: 140 }}
        />
      )}
      {MODEL_OPTIONS[model]?.samplerProfiles && (
        <FormControl size="small" fullWidth>
          <InputLabel>{t("videoGenerator.finish.samplerProfile")}</InputLabel>
          <Select
            value={advancedParams.wan_sampler_profile}
            onChange={(e) => setAdvancedParams({ ...advancedParams, wan_sampler_profile: e.target.value })}
            label={t("videoGenerator.finish.samplerProfile")}
          >
            {MODEL_OPTIONS[model].samplerProfiles.map((key) => (
              <MenuItem key={key} value={key}>
                {WAN5B_SAMPLER_PROFILES[key]?.label || key}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      )}
      {activeSpeedProfile && (
        <FormControl size="small" fullWidth>
          <InputLabel>{t("videoGenerator.finish.speedProfile")}</InputLabel>
          <Select
            value={activeSpeedProfile}
            onChange={(e) => setAdvancedParams({ ...advancedParams, speed_profile: e.target.value })}
            label={t("videoGenerator.finish.speedProfile")}
          >
            {Object.entries(modelCaps.speed_profiles).map(([key, spec]) => (
              <MenuItem key={key} value={key}>
                {spec.label || key}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      )}
      {modelCaps?.style_embeddings?.length > 0 && (
        <FormControl size="small" fullWidth>
          <InputLabel>{t("videoGenerator.finish.styleEmbedding")}</InputLabel>
          <Select
            value={advancedParams.style_embedding || ""}
            onChange={(e) => setAdvancedParams({ ...advancedParams, style_embedding: e.target.value })}
            label={t("videoGenerator.finish.styleEmbedding")}
          >
            <MenuItem value="">{t("videoGenerator.common.none")}</MenuItem>
            {modelCaps.style_embeddings.map((emb) => (
              <MenuItem key={emb.id} value={emb.id}>{emb.label}</MenuItem>
            ))}
          </Select>
        </FormControl>
      )}
      {modelCaps?.audio_in && inputMode !== "references" && (
        <Cluster label={t("videoGenerator.finish.audioGuide")}>
          <Line nowrap>
            <FormControl size="small" className="grow">
              <InputLabel>{t("videoGenerator.finish.audioToPerform")}</InputLabel>
              <Select
                value={audioGuide?.path || ""}
                onChange={(e) => {
                  const clip = voiceClips.find((c) => c.path === e.target.value);
                  setAudioGuide(clip ? { path: clip.path, name: clip.filename } : null);
                }}
                label={t("videoGenerator.finish.audioToPerform")}
              >
                <MenuItem value="">{t("videoGenerator.common.none")}</MenuItem>
                {voiceClips.map((c) => (
                  <MenuItem key={c.id || c.path} value={c.path}>{c.filename}</MenuItem>
                ))}
              </Select>
            </FormControl>
            <ActionButton component="label" loading={isUploadingAudioGuide}>
              {t("videoGenerator.finish.upload")}
              <input
                type="file"
                accept="audio/*"
                hidden
                onChange={(e) => {
                  handleAudioGuideUpload(e.target.files?.[0]);
                  e.target.value = "";
                }}
              />
            </ActionButton>
          </Line>
        </Cluster>
      )}
      <Cluster label={t("videoGenerator.finish.promptTuning")}>
        <Line>
          <TextField
            size="small"
            label={t("videoGenerator.finish.guidance")}
            type="number"
            inputProps={{ step: 0.5, min: 1, max: 20 }}
            value={advancedParams.guidance_scale}
            disabled={modelCaps?.cfg === false || isMinimaxModel(model, modelMeta[model])}
            onChange={(e) => setAdvancedParams({ ...advancedParams, guidance_scale: Number(e.target.value) })}
            sx={{ width: 120 }}
          />
          <FormControl size="small" sx={{ minWidth: 180 }} disabled={!promptControls.style.active}>
            <InputLabel>{t("videoGenerator.finish.promptStyle")}</InputLabel>
            <Select value={promptStyle} onChange={(e) => setPromptStyle(e.target.value)} label={t("videoGenerator.finish.promptStyle")}>
              {Object.entries(PROMPT_STYLES)
                .filter(([key]) => !offeredStyles || offeredStyles.includes(key))
                .map(([key, preset]) => (
                  <MenuItem key={key} value={key}>{preset.label}</MenuItem>
                ))}
            </Select>
          </FormControl>
          <TextField
            size="small"
            label={t("videoGenerator.finish.seed")}
            placeholder={t("videoGenerator.finish.seedRandom")}
            value={advancedParams.seed ?? ""}
            inputProps={{ inputMode: "numeric" }}
            onChange={(e) => {
              const digits = e.target.value.replace(/\D/g, "").slice(0, String(MAX_SEED).length);
              setAdvancedParams((prev) => ({ ...prev, seed: digits }));
            }}
            sx={{ width: 150 }}
          />
          <TextField
            label={t("videoGenerator.finish.framesPerBatch")}
            type="number"
            size="small"
            inputProps={{ min: 1, max: 8 }}
            value={advancedParams.frames_per_batch}
            onChange={(e) => {
              const v = Math.max(1, parseInt(e.target.value || "1", 10));
              setAdvancedParams((prev) => ({ ...prev, frames_per_batch: v }));
            }}
            sx={{ width: 140 }}
          />
        </Line>
        {!promptControls.style.active && <Hint>{promptControls.style.note}.</Hint>}
        {computedParams.seed != null && (
          <Hint>{t("videoGenerator.finish.seedFixed", { seed: computedParams.seed })}</Hint>
        )}
        {computedParams.seed == null && String(advancedParams.seed ?? "").trim() !== "" && (
          <Hint>{t("videoGenerator.finish.seedTooBig", { max: MAX_SEED })}</Hint>
        )}
      </Cluster>
      <VideoGenEffectiveSettings
        model={model}
        computedParams={computedParams}
        capabilities={modelCaps}
        cinematicKeyframe={cinematicKeyframe}
        selectedSubjectIds={selectedSubjectIds}
        keyframeModel={keyframeModel}
        directorMode={directorMode}
        faceRestore={advancedParams.face_restore}
      />
      <GpuGateBanner gpuBusy={gpuBusy} blockReason={blockReason} blockDetail={blockDetail} queueMode />
      <ActionButton
        kind={canQueue ? "primary" : "neutral"}
        onClick={handleGenerate}
        loading={isGenerating}
        disabled={!canQueue || controlsDisabled}
        sx={{ height: 36, alignSelf: "flex-start" }}
      >
        {t("videoGenerator.mode.addToQueue")}
      </ActionButton>
    </SettingsPanel>
        </Box>
  );
}

export default memo(VideoSettingsCard);
