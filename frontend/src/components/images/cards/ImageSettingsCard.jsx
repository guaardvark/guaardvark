// The Image Gen Advanced Settings card: model, style, quality, size and the
// rest of the generation controls.

import React, { memo } from "react";
import {
  Box,
  Typography,
  Button,
  TextField,
  Grid,
  Chip,
  Alert,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Slider,
  Switch,
  FormControlLabel,
  Stack,
  Tooltip,
} from '@mui/material';
import { ActionButton } from '../../settings/ui';
import {
  QWEN_IMAGE_21_GUIDANCE_MAX,
  MOSS_IMAGE_GUIDANCE_MAX,
  FLUX3_KLEIN_GUIDANCE,
  H3_STILL_GUIDANCE,
  ZIMAGE_GUIDANCE,
  MAX_QUANTITY,
  IMAGE_MAX_SEED,
  parseImageSeed,
  clampQuantity,
} from '../../../utils/batchImageSettings';
import { useUiTranslation } from '../../../i18n';

function ImageSettingsCard({
  applicableAdapters,
  autoEnhance,
  batchItems,
  castLocked,
  castLockedNote,
  comfyStills,
  dimensionPresets,
  directorEnabled,
  effectiveWorkers,
  encoderSlots,
  family,
  handleDirectorExpand,
  handleModelChange,
  handleQualityPresetChange,
  highResOkPixels,
  inputMode,
  installButton,
  installName,
  installNeed,
  installSizeGb,
  isExpanding,
  isFlux,
  isFlux3Klein,
  isH3Still,
  isModernDit,
  isMossImage,
  isQwenImage21,
  isZimage,
  lookAndFeel,
  modelOptions,
  oneAtATime,
  oneAtATimeNote,
  openModels,
  params,
  prosePrompts,
  qualityPresets,
  quantity,
  seedText,
  selectedAdapters,
  selectedEncoders,
  setAutoEnhance,
  setDirectorEnabled,
  setParams,
  setQuantity,
  setSeedText,
  setSelectedAdapters,
  setSelectedEncoders,
  styleOptions,
  unavailableModels,
  unusedNegAnat,
}) {
  const { t } = useUiTranslation();
  return (
    <Box sx={{ p: 1.5 }}>
      {inputMode === 'blueprint' && (
        <Typography variant="body2" sx={{ color: "text.secondary" }}>
          {t("imagesBatch.cards.noSettings")}
        </Typography>
      )}
    {inputMode !== 'blueprint' && (
      <>

        {/* Generation Settings */}
        <Box>
            <Grid container spacing={2}>
              <Grid size={{ xs: 12, sm: 6, md: 4 }}>
                <FormControl fullWidth>
                  <InputLabel>{t('imagesBatch.advanced.model')}</InputLabel>
                  <Select
                    value={params.model}
                    onChange={(e) => handleModelChange(e.target.value)}
                  >
                    {modelOptions.map(option => (
                      <MenuItem key={option.value} value={option.value}>
                        <Box>
                          <Typography variant="body2">{option.label}</Typography>
                          <Typography variant="caption" sx={{ color: "text.secondary" }}>
                            {option.description}
                          </Typography>
                        </Box>
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
                <ActionButton onClick={() => openModels()} sx={{ mt: 1 }}>{t('imagesBatch.current.manageModels')}</ActionButton>
                {installNeed && (
                  <Alert severity={installNeed.blocks ? 'warning' : 'info'} action={installButton} sx={{ mt: 1, borderRadius: 1 }}>
                    {installNeed.blocks
                      ? installSizeGb
                        ? t('imagesBatch.install.notInstalledSized', { model: installName, size: installSizeGb })
                        : t('imagesBatch.install.notInstalled', { model: installName })
                      : installSizeGb
                        ? t('imagesBatch.install.noModelSized', { model: installName, size: installSizeGb })
                        : t('imagesBatch.install.noModel', { model: installName })}
                  </Alert>
                )}
                {applicableAdapters.length > 0 && (
                  <Box sx={{ mt: 1 }}>
                    <Typography variant="caption" sx={{ color: "text.secondary", display: 'block', mb: 0.5 }}>
                      {t('imagesBatch.advanced.yourLoras')}
                    </Typography>
                    <Stack direction="row" spacing={0.5} useFlexGap sx={{ flexWrap: "wrap" }}>
                      {applicableAdapters.map((a) => {
                        const on = selectedAdapters.includes(a.id);
                        return (
                          <Chip
                            key={a.id}
                            label={a.name}
                            size="small"
                            color={on ? 'primary' : 'default'}
                            variant={on ? 'filled' : 'outlined'}
                            onClick={() =>
                              setSelectedAdapters((prev) =>
                                on ? prev.filter((id) => id !== a.id) : [...prev, a.id],
                              )
                            }
                          />
                        );
                      })}
                    </Stack>
                  </Box>
                )}
                {encoderSlots.length > 0 && (
                  <Box sx={{ mt: 1.5 }}>
                    <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: "wrap" }}>
                      {encoderSlots.map((group) => (
                        <FormControl key={group.slot} size="small" sx={{ minWidth: 220 }}>
                          <InputLabel>{t('imagesBatch.advanced.textEncoderSlot', { slot: group.label })}</InputLabel>
                          <Select
                            value={
                              group.encoders.some((e) => e.id === selectedEncoders[group.slot])
                                ? selectedEncoders[group.slot]
                                : ''
                            }
                            label={t('imagesBatch.advanced.textEncoderSlot', { slot: group.label })}
                            onChange={(e) =>
                              setSelectedEncoders((prev) => ({ ...prev, [group.slot]: e.target.value }))
                            }
                          >
                            <MenuItem value="">{t('imagesBatch.advanced.shippedEncoder')}</MenuItem>
                            {group.encoders.map((e) => (
                              <MenuItem key={e.id} value={e.id}>
                                {e.name}
                              </MenuItem>
                            ))}
                          </Select>
                        </FormControl>
                      ))}
                    </Stack>
                    <Typography variant="caption" sx={{ color: "text.secondary", display: 'block', mt: 0.5 }}>
                      {t('imagesBatch.advanced.encodersHint')}
                    </Typography>
                  </Box>
                )}
                {unavailableModels.length > 0 && (
                  <Box sx={{ mt: 1 }}>
                    <Typography variant="caption" sx={{ color: "text.secondary", display: 'block', mb: 0.5 }}>
                      {t('imagesBatch.advanced.unavailable')}
                    </Typography>
                    {unavailableModels.map((m) => (
                      <Tooltip key={m.id} title={m.reason || ''}>
                        <Typography
                          variant="caption"
                          color="text.disabled"
                          sx={{ display: 'block', lineHeight: 1.4 }}
                        >
                          • {m.label} — {m.reason}
                        </Typography>
                      </Tooltip>
                    ))}
                  </Box>
                )}
              </Grid>

              <Grid size={{ xs: 12, sm: 6, md: 4 }}>
                <FormControl fullWidth>
                  <InputLabel>{t('imagesBatch.advanced.qualityPreset')}</InputLabel>
                  <Select
                    value={params.quality_preset}
                    onChange={(e) => handleQualityPresetChange(e.target.value)}
                    disabled={castLocked}
                  >
                    {qualityPresets.map(option => (
                      <MenuItem key={option.value} value={option.value}>
                        <Box>
                          <Typography variant="body2">{option.label}</Typography>
                          <Typography variant="caption" sx={{ color: "text.secondary" }}>
                            {option.description}
                          </Typography>
                        </Box>
                      </MenuItem>
                    ))}
                  </Select>
                  {castLocked && (
                    <Typography variant="caption" sx={{ color: "text.secondary", mt: 0.5, display: 'block' }}>
                      {castLockedNote}
                    </Typography>
                  )}
                </FormControl>
              </Grid>

              <Grid size={12}>
                {/* auto_enhance picks the prompt-enhancement rung at render time
                    (stills_policy.resolve_enhance_mode): a chat-model prose rewrite
                    for a natural-language model, tag stuffing for a tag-style one.
                    It never touches steps, guidance or size — those come from the
                    model's recipe above. */}
                <FormControlLabel
                  control={
                    <Switch
                      checked={autoEnhance}
                      onChange={(e) => setAutoEnhance(e.target.checked)}
                      size="small"
                    />
                  }
                  label={
                    <Box>
                      <Typography variant="body2">{t('imagesBatch.enhance.label')}</Typography>
                      <Typography variant="caption" sx={{ color: "text.secondary" }}>
                        {!autoEnhance
                          ? t('imagesBatch.enhance.off')
                          : prosePrompts
                            ? (params.style === 'realistic'
                              ? t('imagesBatch.enhance.prose')
                              : t('imagesBatch.enhance.proseStyle', { style: params.style }))
                            : unusedNegAnat
                              ? t('imagesBatch.enhance.tagsNoNegative')
                              : t('imagesBatch.enhance.tags')}
                      </Typography>
                    </Box>
                  }
                />
              </Grid>

              <Grid size={{ xs: 12, sm: 6, md: 4 }}>
                <FormControl fullWidth>
                  <InputLabel>{t('imagesBatch.advanced.style')}</InputLabel>
                  <Select
                    value={params.style}
                    onChange={(e) => setParams({ ...params, style: e.target.value })}
                  >
                    {styleOptions.map(option => (
                      <MenuItem key={option.value} value={option.value}>
                        {option.label}
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
              </Grid>

              <Grid size={{ xs: 12, sm: 6, md: 4 }}>
                <TextField
                  fullWidth
                  label={t('imagesBatch.advanced.quantity')}
                  type="number"
                  value={quantity}
                  onChange={(e) => setQuantity(clampQuantity(e.target.value))}
                  slotProps={{ htmlInput: { min: 1, max: MAX_QUANTITY } }}
                />
                {/* /batch-image/expand-concept: the chat model turns the idea above
                    into one prompt per shot and fills the prompt box with them. */}
                <Tooltip
                  arrow
                  title={t('imagesBatch.director.expandTooltip')}
                >
                  <span>
                    <Button
                      variant="outlined"
                      size="small"
                      onClick={handleDirectorExpand}
                      disabled={isExpanding || !(batchItems || lookAndFeel).trim()}
                    >
                      {isExpanding ? t('imagesBatch.director.expanding') : t('imagesBatch.director.expand')}
                    </Button>
                  </span>
                </Tooltip>
                {/* director_mode: stills_policy picks the chat-model rewrite for every
                    prompt, whatever the model's default enhancement would be. */}
                <Tooltip
                  arrow
                  title={t('imagesBatch.director.rewriteTooltip')}
                >
                  <FormControlLabel
                    control={<Switch size="small" checked={directorEnabled} onChange={(e) => setDirectorEnabled(e.target.checked)} />}
                    label={t('imagesBatch.director.rewrite')}
                  />
                </Tooltip>
              </Grid>

              <Grid size={{ xs: 12, sm: 6, md: 4 }}>
                <FormControl fullWidth>
                  <InputLabel>{t('imagesBatch.dimensions.title')}</InputLabel>
                  <Select
                    value={`${params.width}x${params.height}`}
                    onChange={(e) => {
                      const [width, height] = e.target.value.split('x').map(Number);
                      setParams({ ...params, width, height });
                    }}
                    renderValue={(v) => {
                      const match = dimensionPresets.find(
                        (p) => `${p.width}x${p.height}` === v,
                      );
                      return match ? match.label : v;
                    }}
                  >
                    {!dimensionPresets.some(
                      (p) => p.width === params.width && p.height === params.height,
                    ) && (
                      <MenuItem value={`${params.width}x${params.height}`}>
                        {t('imagesBatch.dimensions.custom', { width: params.width, height: params.height })}
                      </MenuItem>
                    )}
                    {dimensionPresets.map(preset => (
                      <MenuItem key={`${preset.width}x${preset.height}`} value={`${preset.width}x${preset.height}`}>
                        {preset.label}
                      </MenuItem>
                    ))}
                  </Select>
                  {(comfyStills || isModernDit)
                    && (params.width * params.height > Math.max(1024 * 1024, highResOkPixels || 0)) && (
                    <Typography variant="caption" sx={{ color: "warning.main", mt: 0.5, display: 'block' }}>
                      {t('imagesBatch.dimensions.highResWarning')}
                    </Typography>
                  )}
                </FormControl>
              </Grid>

              <Grid size={12}>
                <Typography gutterBottom>
                  {castLocked
                    ? t('imagesBatch.steps.cast')
                    : isFlux
                      ? t('imagesBatch.steps.flux', { steps: params.steps })
                      : t('imagesBatch.steps.value', { steps: params.steps })}
                </Typography>
                <Slider
                  value={params.steps}
                  onChange={(e, value) => setParams({ ...params, steps: value, steps_explicit: true })}
                  disabled={castLocked}
                  min={1}
                  max={100}
                  step={1}
                  marks={[
                    { value: 8, label: '8' },
                    { value: 20, label: '20' },
                    { value: 28, label: '28' },
                    { value: 50, label: '50' },
                    { value: 100, label: '100' },
                  ]}
                />
              </Grid>

              <Grid size={12}>
                <Typography gutterBottom>
                  {castLocked
                    ? t('imagesBatch.guidance.cast')
                    : isZimage
                    ? t('imagesBatch.guidance.zimage')
                    : isFlux
                      ? t('imagesBatch.guidance.flux', { guidance: params.guidance })
                      : t('imagesBatch.guidance.scale', { guidance: params.guidance })}
                </Typography>
                {/* Z-Image is CFG-free: there is NO valid non-zero guidance. Any other value
                    produces washed-out, over-cooked output, so the control is disabled rather
                    than left free to drift off the only correct setting. `auto` is left
                    editable: the router may pick SDXL, which does use CFG. */}
                <Slider
                  value={isZimage ? ZIMAGE_GUIDANCE : isFlux3Klein ? FLUX3_KLEIN_GUIDANCE : isH3Still ? H3_STILL_GUIDANCE : params.guidance}
                  onChange={(e, value) => setParams({ ...params, guidance: value })}
                  min={0}
                  max={isFlux ? 6 : isQwenImage21 ? QWEN_IMAGE_21_GUIDANCE_MAX : isMossImage ? MOSS_IMAGE_GUIDANCE_MAX : 20}
                  step={0.5}
                  marks
                  disabled={isZimage || isFlux3Klein || isH3Still || castLocked}
                />
                {castLocked && (
                  <Typography variant="caption" sx={{ color: "text.secondary", display: 'block' }}>
                    {castLockedNote}
                  </Typography>
                )}
                {isZimage && !castLocked && (
                  <Typography variant="caption" sx={{ color: "text.secondary" }}>
                    {t('imagesBatch.guidance.zimageHelp')}
                  </Typography>
                )}
                {isQwenImage21 && !castLocked && (
                  <Typography variant="caption" sx={{ color: "text.secondary" }}>
                    {t('imagesBatch.guidance.qwenHelp')}
                  </Typography>
                )}
                {isFlux3Klein && !castLocked && (
                  <Typography variant="caption" sx={{ color: "text.secondary" }}>
                    {t('imagesBatch.guidance.flux3KleinHelp')}
                  </Typography>
                )}
                {isH3Still && !castLocked && (
                  <Typography variant="caption" sx={{ color: "text.secondary" }}>
                    {t('imagesBatch.guidance.h3StillHelp')}
                  </Typography>
                )}
                {isMossImage && !castLocked && (
                  <Typography variant="caption" sx={{ color: "text.secondary" }}>
                    {t('imagesBatch.guidance.mossHelp')}
                  </Typography>
                )}
                {family === 'auto' && (
                  <Typography variant="caption" sx={{ color: "text.secondary" }}>
                    {t('imagesBatch.guidance.autoHelp')}
                  </Typography>
                )}
              </Grid>

              <Grid size={12}>
                <TextField
                  size="small"
                  label={t('imagesBatch.seed.label')}
                  placeholder={t('imagesBatch.seed.placeholder')}
                  value={seedText}
                  slotProps={{ htmlInput: { inputMode: 'numeric' } }}
                  onChange={(e) => setSeedText(e.target.value.replace(/\D/g, '').slice(0, String(IMAGE_MAX_SEED).length))}
                  sx={{ width: 180 }}
                />
                <Typography variant="caption" sx={{ color: "text.secondary", display: 'block', mt: 0.5 }}>
                  {parseImageSeed(seedText) != null
                    ? t('imagesBatch.seed.fixed', { seed: parseImageSeed(seedText) })
                    : seedText.trim()
                      ? t('imagesBatch.seed.tooLarge', { max: IMAGE_MAX_SEED })
                      : t('imagesBatch.seed.blank')}
                </Typography>
              </Grid>

              <Grid size={12}>
                <Typography gutterBottom>
                  {t('imagesBatch.workers.max', { count: effectiveWorkers })}
                </Typography>
                <Slider
                  value={effectiveWorkers}
                  onChange={(e, value) => setParams({ ...params, max_workers: value })}
                  min={1}
                  max={4}
                  step={1}
                  marks
                  disabled={oneAtATime}
                />
                {oneAtATime && (
                  <Typography variant="caption" sx={{ color: "text.secondary", display: 'block' }}>
                    {oneAtATimeNote}
                  </Typography>
                )}
              </Grid>

              <Grid size={12}>
                <FormControlLabel
                  control={
                    <Switch
                      checked={params.generate_thumbnails}
                      onChange={(e) => setParams({ ...params, generate_thumbnails: e.target.checked })}
                    />
                  }
                  label={t('imagesBatch.advanced.thumbnails')}
                />
              </Grid>
            </Grid>
        </Box>
      </>
    )}

    </Box>
  );
}

export default memo(ImageSettingsCard);
