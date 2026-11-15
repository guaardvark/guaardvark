// The Video Gen Prompt card: what to make. Text prompts, start and end frames
// or a reference board, the look, and the preview of what will be sent.

import React, { memo } from "react";
import {
  Box,
  Typography,
  TextField,
  IconButton,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  CircularProgress,
} from "@mui/material";
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
  isMinimaxModel,
} from "../../../constants/videoGeneratorPresets";
import { previewLabel } from "../../../utils/videoPreviewPrompts";
import ReferenceBoard from "../ReferenceBoard";
import FrameSlot from "../FrameSlot";
import {
  Close as CloseIcon,
} from "@mui/icons-material";
import { Trans } from "../../../i18n";
import { composeCardSx } from "../videoGenPage";
import { useUiTranslation } from "../../../i18n";

function VideoPromptCard({
  applyPromptPreset,
  canQueue,
  controlsDisabled,
  dragActive,
  endFrame,
  endFrameModes,
  enhancePrompt,
  fetchPromptPreview,
  fidelityMode,
  fileInputRef,
  handleDragLeave,
  handleDragOver,
  handleDrop,
  handleEndFrameUpload,
  handleFileUpload,
  handleGenerate,
  highConsistencyMode,
  inputMode,
  insertPromptTag,
  isGenerating,
  isUploading,
  isUploadingEndFrame,
  lookAndFeel,
  lowVramClampApplies,
  lowVramMode,
  model,
  modelCaps,
  modelMeta,
  negativePrompt,
  openGallery,
  parsedPrompts,
  previewCounts,
  previewEnhanceOff,
  previewEnhanced,
  previewLoading,
  previewVerbatim,
  promptGallery,
  promptInputRef,
  promptPresets,
  promptsText,
  refBoard,
  refFit,
  refLimits,
  refModelIds,
  refProblems,
  removeSelectedImage,
  selectedImages,
  selectedPromptPreset,
  setEndFrame,
  setEnhancePrompt,
  setError,
  setFidelityMode,
  setHighConsistencyMode,
  setInputMode,
  setLookAndFeel,
  setLowVramMode,
  setNegativePrompt,
  setPreviewEnhanced,
  setPromptsText,
  setRefBoard,
  setShowPreview,
  showPreview,
  storyboardMode,
  storyboardShots,
  verbatimPrompts,
}) {
  const { t } = useUiTranslation();
  const composeSx = composeCardSx(controlsDisabled);
  return (
        <Box sx={composeSx}>
    <SettingsPanel
      id="videogen-mode"
      title={t("videoGenerator.mode.title")}
      description={t("videoGenerator.mode.description")}
      actions={
        <ActionButton
          kind={canQueue ? "primary" : "neutral"}
          onClick={handleGenerate}
          loading={isGenerating}
          disabled={!canQueue || controlsDisabled}
        >
          {t("videoGenerator.mode.addToQueue")}
        </ActionButton>
      }
    >
      <Line>
        <SettingChip
          label={t("videoGenerator.mode.lowVram")}
          on={lowVramMode}
          note={lowVramMode ? (lowVramClampApplies ? t("videoGenerator.mode.nextJob") : t("videoGenerator.mode.notForModel")) : undefined}
          tooltip={lowVramClampApplies || !lowVramMode
            ? t("videoGenerator.mode.lowVramTooltip")
            : t("videoGenerator.mode.lowVramOtherModel")}
          onToggle={(next) => {
            setLowVramMode(next);
            localStorage.setItem("lowVramMode", next.toString());
            if (next && highConsistencyMode) setHighConsistencyMode(false);
          }}
        />
        <SettingChip
          label={t("videoGenerator.mode.highConsistency")}
          on={highConsistencyMode}
          tooltip={t("videoGenerator.mode.highConsistencyTooltip")}
          onToggle={(next) => {
            setHighConsistencyMode(next);
            if (next && lowVramMode) {
              setLowVramMode(false);
              localStorage.setItem("lowVramMode", "false");
            }
          }}
        />
      </Line>
    </SettingsPanel>

    <SettingsPanel id="videogen-prompt" title={t("videoGenerator.prompt.title")}>
      <Cluster label={t("videoGenerator.prompt.input")}>
        <ChoiceChips
          ariaLabel={t("videoGenerator.prompt.inputMode")}
          value={inputMode}
          onChange={setInputMode}
          options={[
            { value: "text", label: t("videoGenerator.prompt.modeText") },
            { value: "image", label: t("videoGenerator.prompt.modeImage") },
            ...(refModelIds.length > 0
              ? [{ value: "references", label: t("videoGenerator.prompt.modeReferences"), tooltip: t("videoGenerator.prompt.modeReferencesTooltip") }]
              : []),
          ]}
        />
      </Cluster>
      {inputMode === "text" && promptPresets.length > 0 && (
        <Cluster label={t("videoGenerator.prompt.preset")}>
          <Line nowrap>
            <FormControl size="small" className="grow">
              <InputLabel>{t("videoGenerator.prompt.promptPreset")}</InputLabel>
              <Select
                value={selectedPromptPreset}
                onChange={(e) => applyPromptPreset(e.target.value)}
                label={t("videoGenerator.prompt.promptPreset")}
              >
                <MenuItem value="">{t("videoGenerator.common.none")}</MenuItem>
                {promptPresets.map((p) => (
                  <MenuItem key={p.slug} value={p.slug}>
                    {p.title}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          </Line>
          {promptGallery?.source?.url && (
            <Hint>
              <Trans
                i18nKey="videoGenerator.prompt.galleryHint"
                components={{ galleryLink: <a href={promptGallery.source.url} target="_blank" rel="noreferrer noopener" /> }}
              />
            </Hint>
          )}
        </Cluster>
      )}
      {inputMode === "references" ? (
        <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
          <ReferenceBoard
            value={refBoard}
            onChange={setRefBoard}
            limits={refLimits}
            fit={refFit}
            onInsertTag={insertPromptTag}
            onError={setError}
          />
          <TextField
            label={t("videoGenerator.prompt.describeVideo")}
            multiline
            minRows={3}
            maxRows={8}
            value={promptsText}
            onChange={(e) => setPromptsText(e.target.value)}
            inputRef={promptInputRef}
            placeholder={t("videoGenerator.prompt.referencesPlaceholder")}
            helperText={enhancePrompt
              ? t("videoGenerator.prompt.referencesHelp")
              : t("videoGenerator.prompt.referencesHelpVerbatim")}
            fullWidth
            size="small"
          />
          {refProblems.length > 0 && (refBoard.images.length + refBoard.videos.length + refBoard.audios.length) > 0 && (
            <Hint sx={{ color: "warning.main" }}>{refProblems.join(" ")}</Hint>
          )}
        </Box>
      ) : inputMode === "text" ? (
        <TextField
          label={storyboardMode ? t("videoGenerator.prompt.storyboardConcept") : t("videoGenerator.prompt.whatToSee")}
          multiline
          minRows={storyboardMode ? 2 : 3}
          maxRows={storyboardMode ? 4 : 6}
          value={promptsText}
          onChange={(e) => setPromptsText(e.target.value)}
          placeholder={
            storyboardMode
              ? t("videoGenerator.prompt.storyboardPlaceholder")
              : t("videoGenerator.prompt.textPlaceholder")
          }
          helperText={
            storyboardMode
              ? t("videoGenerator.prompt.storyboardHelp", { count: storyboardShots })
              : parsedPrompts.length > 1
                ? t("videoGenerator.prompt.clipsHelp", { count: parsedPrompts.length })
                : undefined
          }
          fullWidth
          size="small"
        />
      ) : (
        <Box>
          <TextField
            label={selectedImages.length === 0 && endFrame && endFrameModes.includes("l2v")
              ? t("videoGenerator.prompt.describeEndFrame")
              : t("videoGenerator.prompt.describeMotion")}
            multiline
            minRows={2}
            maxRows={4}
            value={promptsText}
            onChange={(e) => setPromptsText(e.target.value)}
            placeholder={t("videoGenerator.prompt.motionPlaceholder")}
            fullWidth
            size="small"
            sx={{ mb: 1.5 }}
          />
          <Box
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
            sx={{
              border: "2px dashed",
              borderColor: dragActive ? "primary.main" : "divider",
              borderRadius: "8px",
              p: 2,
              textAlign: "center",
              bgcolor: dragActive ? "action.hover" : "transparent",
              cursor: "pointer",
            }}
          >
            {isUploading ? (
              <CircularProgress size={28} />
            ) : (
              <Typography variant="body2" color="text.secondary">
                {t("videoGenerator.prompt.dropImages")}
              </Typography>
            )}
          </Box>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept="image/*"
            style={{ display: "none" }}
            onChange={(e) => {
              if (e.target.files && e.target.files.length > 0) {
                handleFileUpload(Array.from(e.target.files));
                e.target.value = "";
              }
            }}
          />
          <Line sx={{ mt: 1 }}>
            <ActionButton onClick={openGallery}>{t("videoGenerator.prompt.selectFromGallery")}</ActionButton>
          </Line>
          {endFrameModes.length > 0 && (
            <Box sx={{ mt: 1.5 }}>
              <FrameSlot
                label={t("videoGenerator.prompt.endFrame")}
                frameName={t("videoGenerator.prompt.endFrameName")}
                value={endFrame}
                onChange={setEndFrame}
                onUploadFile={handleEndFrameUpload}
                uploading={isUploadingEndFrame}
                hint={endFrameModes.includes("l2v")
                  ? t("videoGenerator.prompt.endFrameHintL2v")
                  : t("videoGenerator.prompt.endFrameHint")}
              />
            </Box>
          )}
          {selectedImages.length > 0 && (
            <Box sx={{ mt: 1, display: "flex", gap: 1, flexWrap: "wrap" }}>
              {selectedImages.map((img) => (
                <Box key={img.id} sx={{ position: "relative", width: 72, height: 72, borderRadius: "6px", overflow: "hidden" }}>
                  <Box component="img" src={img.thumbnailUrl} alt={img.name} sx={{ width: "100%", height: "100%", objectFit: "cover" }} />
                  <IconButton
                    size="small"
                    onClick={() => removeSelectedImage(img.id)}
                    sx={{ position: "absolute", top: 2, right: 2, bgcolor: "rgba(0,0,0,0.6)", color: "white", p: 0.25 }}
                  >
                    <CloseIcon sx={{ fontSize: 14 }} />
                  </IconButton>
                </Box>
              ))}
            </Box>
          )}
        </Box>
      )}
      <TextField
        label={t("videoGenerator.prompt.lookAndFeel")}
        multiline
        minRows={2}
        maxRows={4}
        value={lookAndFeel}
        onChange={(e) => setLookAndFeel(e.target.value)}
        placeholder={t("videoGenerator.prompt.lookAndFeelPlaceholder")}
        helperText={lookAndFeel.trim() ? t("videoGenerator.prompt.appendedTo", { count: parsedPrompts.length || 0 }) : t("videoGenerator.prompt.lookAndFeelHelp")}
        fullWidth
        size="small"
      />
      <TextField
        label={t("videoGenerator.prompt.negative")}
        multiline
        minRows={2}
        maxRows={4}
        value={negativePrompt}
        disabled={modelCaps?.cfg === false || isMinimaxModel(model, modelMeta[model])}
        onChange={(e) => setNegativePrompt(e.target.value)}
        placeholder={t("videoGenerator.prompt.negativePlaceholder")}
        helperText={
          modelCaps?.cfg === false || isMinimaxModel(model, modelMeta[model])
            ? t("videoGenerator.prompt.negativeUnused")
            : undefined
        }
        fullWidth
        size="small"
      />
      <Line>
        <SettingChip
          label={t("videoGenerator.prompt.exactText")}
          on={fidelityMode}
          tooltip={t("videoGenerator.prompt.exactTextTooltip")}
          onToggle={(next) => {
            setFidelityMode(next);
            setPreviewEnhanced("");
            setShowPreview(false);
          }}
        />
        <SettingChip
          label={t("videoGenerator.prompt.enhance")}
          on={enhancePrompt}
          note={enhancePrompt && verbatimPrompts && inputMode !== "references" ? t("videoGenerator.prompt.enhanceSkipped") : undefined}
          tooltip={verbatimPrompts && inputMode !== "references"
            ? t("videoGenerator.prompt.enhanceVerbatim")
            : t("videoGenerator.prompt.enhanceTooltip")}
          onToggle={setEnhancePrompt}
        />
        <ActionButton
          onClick={fetchPromptPreview}
          loading={previewLoading}
          disabled={!((inputMode === "text" ? parsedPrompts.length : promptsText.trim().length) > 0)}
        >
          {t("videoGenerator.prompt.previewEnhanced")}
        </ActionButton>
      </Line>
      {showPreview && previewEnhanced && (
        <TextField
          label={previewLabel({
            shown: previewCounts.shown,
            total: previewCounts.total,
            verbatim: previewVerbatim,
            enhanceOff: previewEnhanceOff,
            storyboard: previewCounts.storyboard,
          })}
          value={previewEnhanced}
          multiline
          minRows={2}
          maxRows={previewCounts.total > 1 ? 16 : undefined}
          fullWidth
          size="small"
          InputProps={{ readOnly: true }}
        />
      )}
    </SettingsPanel>

        </Box>
  );
}

export default memo(VideoPromptCard);
