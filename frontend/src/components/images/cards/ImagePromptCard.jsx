// The Image Gen Prompt card: what to make. Single, bulk, CSV or blueprint
// input, the look, the negative prompt, and Start.

import React, { memo } from "react";
import {
  Box,
  Typography,
  Button,
  TextField,
  Alert,
  FormControl,
  Select,
  MenuItem,
} from '@mui/material';
import {
  Upload,
  PlayArrow,
  GetApp,
  Cancel,
} from '@mui/icons-material';
import CharacterPicker from '../../filmcrew/CharacterPicker';
import { POLLABLE_STATUSES } from '../../../hooks/useImageBatchQueue';
import DeferredPromptField from '../DeferredPromptField';
import PromptTokenCounter from '../PromptTokenCounter';
import GpuGateBanner from '../../common/GpuGateBanner';
import { Hint, SettingChip } from '../../settings/ui';
import { useUiTranslation } from '../../../i18n';

function ImagePromptCard({
  activeBatch,
  batchItems,
  blockDetail,
  blockReason,
  blueprintFile,
  blueprintFileInputRef,
  cancelGeneration,
  castSubjectIds,
  csvFile,
  downloadTemplate,
  enhanceAnatomy,
  fileInputRef,
  finalPrompts,
  gpuBusy,
  handleBlueprintFileUpload,
  handleFileUpload,
  handlePresetChange,
  inputMode,
  installBlocksStart,
  installButton,
  installName,
  installNeed,
  loading,
  lookAndFeel,
  negativePrompt,
  params,
  promptFlushRef,
  prosePrompts,
  queue,
  selectedPreset,
  setBatchItems,
  setCastSubjectIds,
  setEnhanceAnatomy,
  setInputMode,
  setLookAndFeel,
  setNegativePrompt,
  setShowPromptPreview,
  startGeneration,
  uniquePromptCount,
  unusedNegAnat,
}) {
  const { t } = useUiTranslation();
  return (
    <Box sx={{ p: 1.5 }}>
    {/* Content Preset Selector. Only tag-style models take it, so it
        appears when one is chosen. */}
    {prosePrompts ? null : (
    <Box sx={{ mb: 3, p: 2, bgcolor: 'primary.50', borderRadius: 2, border: '1px solid', borderColor: 'primary.200' }}>
      <Typography variant="subtitle2" sx={{ mb: 1.5, fontWeight: 600, color: 'primary.main' }}>
        {t('imagesBatch.contentType.title')}
      </Typography>
      <FormControl fullWidth size="small">
        <Select
          value={selectedPreset}
          onChange={(e) => handlePresetChange(e.target.value)}
          sx={{ bgcolor: 'background.paper' }}
        >
          <MenuItem value="auto">
            <Box>
              <Typography variant="body2" sx={{ fontWeight: 500 }}>{t('imagesBatch.contentType.auto')}</Typography>
              <Typography variant="caption" color="text.secondary">
                {t('imagesBatch.contentType.autoHelp')}
              </Typography>
            </Box>
          </MenuItem>
          <MenuItem value="person_portrait">
            <Box>
              <Typography variant="body2" sx={{ fontWeight: 500 }}>{t('imagesBatch.contentType.portrait')}</Typography>
              <Typography variant="caption" color="text.secondary">
                {t('imagesBatch.contentType.portraitHelp')}
              </Typography>
            </Box>
          </MenuItem>
          <MenuItem value="person_full_body">
            <Box>
              <Typography variant="body2" sx={{ fontWeight: 500 }}>{t('imagesBatch.contentType.fullBody')}</Typography>
              <Typography variant="caption" color="text.secondary">
                {t('imagesBatch.contentType.fullBodyHelp')}
              </Typography>
            </Box>
          </MenuItem>
          <MenuItem value="person_working">
            <Box>
              <Typography variant="body2" sx={{ fontWeight: 500 }}>{t('imagesBatch.contentType.working')}</Typography>
              <Typography variant="caption" color="text.secondary">
                {t('imagesBatch.contentType.workingHelp')}
              </Typography>
            </Box>
          </MenuItem>
          <MenuItem value="product_photo">
            <Box>
              <Typography variant="body2" sx={{ fontWeight: 500 }}>{t('imagesBatch.contentType.product')}</Typography>
              <Typography variant="caption" color="text.secondary">
                {t('imagesBatch.contentType.productHelp')}
              </Typography>
            </Box>
          </MenuItem>
          <MenuItem value="landscape">
            <Box>
              <Typography variant="body2" sx={{ fontWeight: 500 }}>{t('imagesBatch.contentType.landscape')}</Typography>
              <Typography variant="caption" color="text.secondary">
                {t('imagesBatch.contentType.landscapeHelp')}
              </Typography>
            </Box>
          </MenuItem>
          <MenuItem value="infographic_preset">
            <Box>
              <Typography variant="body2" sx={{ fontWeight: 500 }}>{t('imagesBatch.contentType.infographic')}</Typography>
              <Typography variant="caption" color="text.secondary">
                {t('imagesBatch.contentType.infographicHelp')}
              </Typography>
            </Box>
          </MenuItem>
          <MenuItem value="general">
            <Box>
              <Typography variant="body2" sx={{ fontWeight: 500 }}>{t('imagesBatch.contentType.general')}</Typography>
              <Typography variant="caption" color="text.secondary">
                {t('imagesBatch.contentType.generalHelp')}
              </Typography>
            </Box>
          </MenuItem>
        </Select>
      </FormControl>
    </Box>
    )}

    {/* Input Mode Selection */}
    <Box sx={{ mb: 3 }}>
      <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 500 }}>
        {t('imagesBatch.input.title')}
      </Typography>
      <Box sx={{ display: 'flex', gap: 1 }}>
        <Button
          variant={inputMode === 'single' ? 'contained' : 'outlined'}
          onClick={() => setInputMode('single')}
          size="small"
          sx={{
            flex: 1,
            textTransform: 'none',
            fontWeight: inputMode === 'single' ? 600 : 400
          }}
        >
          {t('imagesBatch.input.single')}
        </Button>
        <Button
          variant={inputMode === 'bulk' ? 'contained' : 'outlined'}
          onClick={() => setInputMode('bulk')}
          size="small"
          sx={{
            flex: 1,
            textTransform: 'none',
            fontWeight: inputMode === 'bulk' ? 600 : 400
          }}
        >
          {t('imagesBatch.input.bulk')}
        </Button>
        <Button
          variant={inputMode === 'csv' ? 'contained' : 'outlined'}
          onClick={() => setInputMode('csv')}
          size="small"
          sx={{
            flex: 1,
            textTransform: 'none',
            fontWeight: inputMode === 'csv' ? 600 : 400
          }}
        >
          {t('imagesBatch.input.csv')}
        </Button>
        <Button
          variant={inputMode === 'blueprint' ? 'contained' : 'outlined'}
          onClick={() => setInputMode('blueprint')}
          size="small"
          sx={{
            flex: 1,
            textTransform: 'none',
            fontWeight: inputMode === 'blueprint' ? 600 : 400
          }}
        >
          {t('imagesBatch.input.blueprint')}
        </Button>
      </Box>
    </Box>

    {/* Cast (optional) - for single prompt and bulk */}
    {(inputMode === 'single' || inputMode === 'bulk') && (
      <Box sx={{ mb: 2 }}>
        <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 500 }}>
          {t('imagesBatch.cast.title')}
        </Typography>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
          {t('imagesBatch.cast.help')}
        </Typography>
        <CharacterPicker
          value={castSubjectIds}
          onChange={setCastSubjectIds}
          onlyTrained
        />
      </Box>
    )}

    {/* Single Prompt Input (default) */}
    {inputMode === 'single' && (
      <Box>
        <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 500 }}>
          {t('imagesBatch.input.single')}
        </Typography>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
          {t('imagesBatch.single.help')}
        </Typography>
        <DeferredPromptField
          fullWidth
          multiline
          rows={8}
          placeholder={t('imagesBatch.single.placeholder')}
          value={batchItems}
          onCommit={setBatchItems}
          flushRef={promptFlushRef}
          variant="outlined"
          sx={{
            mb: 2,
            '& .MuiOutlinedInput-root': {
              borderRadius: 1,
              fontFamily: 'monospace',
              fontSize: '0.9rem'
            }
          }}
        />
        <PromptTokenCounter prompts={finalPrompts} model={params.model} sx={{ mt: -1.5, mb: 1 }} />
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
          {t('imagesBatch.single.note')}
        </Typography>
      </Box>
    )}

    {/* Bulk Input */}
    {inputMode === 'bulk' && (
      <Box>
        <Typography variant="subtitle2" sx={{ mb: 2, fontWeight: 500 }}>
          {t('imagesBatch.bulk.title')}
        </Typography>
        <DeferredPromptField
          fullWidth
          multiline
          rows={8}
          placeholder={t('imagesBatch.bulk.placeholder')}
          value={batchItems}
          onCommit={setBatchItems}
          flushRef={promptFlushRef}
          variant="outlined"
          sx={{
            mb: 2,
            '& .MuiOutlinedInput-root': {
              borderRadius: 1,
              fontFamily: 'monospace',
              fontSize: '0.9rem'
            }
          }}
        />
        <PromptTokenCounter prompts={finalPrompts} model={params.model} sx={{ mt: -1.5, mb: 1 }} />
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
          <Typography variant="caption" color="text.secondary">
            {t('imagesBatch.bulk.ready', { count: uniquePromptCount })}
          </Typography>
          <Button
            variant="text"
            size="small"
            onClick={() => {
              setBatchItems(
                'A majestic mountain landscape at sunset\n' +
                'A cat sitting on a windowsill\n' +
                'Abstract geometric patterns in blue\n' +
                'Portrait of a wise old wizard\n' +
                'A futuristic city skyline'
              );
              setLookAndFeel('photorealistic, professional photography, sharp focus, natural lighting');
            }}
            sx={{
              textTransform: 'none',
              fontSize: '0.75rem',
              minWidth: 'auto',
              px: 1,
              py: 0.25
            }}
          >
            {t('imagesBatch.bulk.loadExamples')}
          </Button>
        </Box>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
          {t('imagesBatch.bulk.tip')}
        </Typography>
      </Box>
    )}

    {/* Look & Feel (shared for single + bulk) */}
    {(inputMode === 'single' || inputMode === 'bulk') && (
      <Box sx={{ mt: 3 }}>
        <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 500 }}>
          {t('imagesBatch.look.title')}
        </Typography>
        <TextField
          fullWidth
          multiline
          rows={3}
          placeholder={t('imagesBatch.look.placeholder')}
          value={lookAndFeel}
          onChange={(e) => setLookAndFeel(e.target.value)}
          variant="outlined"
          sx={{
            mb: 1,
            '& .MuiOutlinedInput-root': {
              borderRadius: 1
            }
          }}
        />
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <Typography variant="caption" color="text.secondary">
            {inputMode === 'single' ? t('imagesBatch.look.single') : t('imagesBatch.look.bulk', { count: uniquePromptCount })}
          </Typography>
          <Button
            variant="outlined"
            size="small"
            onClick={() => setShowPromptPreview(true)}
            sx={{
              textTransform: 'none',
              fontSize: '0.75rem',
              minWidth: 'auto',
              px: 1.5,
              py: 0.5
            }}
          >
            {t('imagesBatch.look.preview')}
          </Button>
        </Box>
        <TextField
          fullWidth
          multiline
          minRows={2}
          maxRows={4}
          label={t('imagesBatch.negative.label')}
          placeholder="blurry, distorted hands, extra fingers"
          value={negativePrompt}
          disabled={unusedNegAnat}
          onChange={(e) => setNegativePrompt(e.target.value)}
          helperText={
            unusedNegAnat
              ? t('imagesBatch.negative.unused')
              : undefined
          }
          sx={{ mt: 2 }}
        />
        <Box sx={{ mt: 1.5, display: 'flex', flexDirection: 'column', gap: 0.75 }}>
          {/* The backend adds the anatomy phrasing only on the tag path
              (offline_image_generator.enhance_prompt_for_quality); a prose
              model, and Auto while it resolves to one, skips it. */}
          <SettingChip
            label={t('imagesBatch.anatomy.label')}
            on={enhanceAnatomy}
            disabled={prosePrompts}
            tooltip={
              prosePrompts
                ? t('imagesBatch.anatomy.unusedTooltip')
                : t('imagesBatch.anatomy.tooltip')
            }
            onToggle={setEnhanceAnatomy}
          />
          {(prosePrompts || unusedNegAnat) && (
            <Hint>
              {prosePrompts && unusedNegAnat
                ? t('imagesBatch.anatomy.unusedBoth')
                : prosePrompts
                  ? t('imagesBatch.anatomy.unused')
                  : t('imagesBatch.negative.unused')}
            </Hint>
          )}
        </Box>
      </Box>
    )}

    {/* CSV Upload */}
    {inputMode === 'csv' && (
      <Box>
        <Typography variant="subtitle2" sx={{ mb: 2, fontWeight: 500 }}>
          {t('imagesBatch.csv.title')}
        </Typography>
        <input
          type="file"
          accept=".csv"
          onChange={handleFileUpload}
          ref={fileInputRef}
          style={{ display: 'none' }}
        />

        <Button
          startIcon={<Upload />}
          onClick={() => fileInputRef.current?.click()}
          variant="outlined"
          fullWidth
          sx={{
            mb: 2,
            textTransform: 'none',
            borderRadius: 1,
            py: 1.5
          }}
        >
          {t('imagesBatch.csv.upload')}
        </Button>

        {csvFile && (
          <Alert severity="info" sx={{ mb: 2, borderRadius: 1 }}>
            {t('imagesBatch.csv.selected', { name: csvFile.name })}
          </Alert>
        )}

        <Button
          startIcon={<GetApp />}
          onClick={downloadTemplate}
          variant="text"
          size="small"
          sx={{
            textTransform: 'none',
            borderRadius: 1
          }}
        >
          {t('imagesBatch.csv.template')}
        </Button>
      </Box>
    )}

    {/* Offline Blueprint Upload */}
    {inputMode === 'blueprint' && (
      <Box>
        <Typography variant="subtitle2" sx={{ mb: 2, fontWeight: 500 }}>
          {t('imagesBatch.blueprint.title')}
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          {t('imagesBatch.blueprint.help')}
        </Typography>
        <input
          type="file"
          accept=".csv"
          onChange={handleBlueprintFileUpload}
          ref={blueprintFileInputRef}
          style={{ display: 'none' }}
        />

        <Button
          startIcon={<Upload />}
          onClick={() => blueprintFileInputRef.current?.click()}
          variant="outlined"
          fullWidth
          sx={{
            mb: 2,
            textTransform: 'none',
            borderRadius: 1,
            py: 1.5
          }}
        >
          {t('imagesBatch.blueprint.upload')}
        </Button>

        {blueprintFile && (
          <Alert severity="info" sx={{ mb: 2, borderRadius: 1 }}>
            {t('imagesBatch.csv.selected', { name: blueprintFile.name })}
          </Alert>
        )}

        <Typography variant="caption" color="text.secondary">
          {t('imagesBatch.blueprint.format')}
        </Typography>
      </Box>
    )}

    {/* Action Buttons — queue mode: never blocked by an in-flight batch */}
    <Box sx={{ mt: 3, display: 'flex', flexDirection: 'column', gap: 1 }}>
      <GpuGateBanner gpuBusy={gpuBusy} blockReason={blockReason} blockDetail={blockDetail} queueMode />
      {installNeed && (
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 1 }}>
          <Hint>
            {installNeed.blocks
              ? t('imagesBatch.install.startOff', { model: installName })
              : t('imagesBatch.install.noModelShort')}
          </Hint>
          {installButton}
        </Box>
      )}
      <Box sx={{ display: 'flex', gap: 2 }}>
        <Button
          variant="contained"
          onClick={startGeneration}
          disabled={loading || installBlocksStart}
          startIcon={<PlayArrow />}
          fullWidth
          size="large"
          sx={{
            textTransform: 'none',
            borderRadius: 1,
            py: 1.5,
            fontWeight: 600
          }}
        >
          {loading
            ? t('imagesBatch.actions.queuing')
            : (queue.some((q) => POLLABLE_STATUSES.has(q.status))
              ? t('imagesBatch.actions.addToQueue')
              : t('imagesBatch.actions.start'))}
        </Button>

        {activeBatch && POLLABLE_STATUSES.has(activeBatch.status) && (
          <Button
            variant="outlined"
            onClick={cancelGeneration}
            startIcon={<Cancel />}
            color="error"
            size="large"
            sx={{
              textTransform: 'none',
              borderRadius: 1,
              py: 1.5,
              fontWeight: 600
            }}
          >
            {t('imagesBatch.actions.cancel')}
          </Button>
        )}
      </Box>
    </Box>
    </Box>
  );
}

export default memo(ImagePromptCard);
