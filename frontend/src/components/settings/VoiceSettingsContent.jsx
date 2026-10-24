import React from "react";
import {
  Typography,
  Box,
  Button,
  CircularProgress,
  Grid,
  Chip,
  Tooltip,
} from "@mui/material";
import MuiAlert from "@mui/material/Alert";
import FileDownloadIcon from "@mui/icons-material/FileDownload";
import ContentCopyIcon from "@mui/icons-material/ContentCopy";
import VoiceListeningSettings from "../voice/VoiceListeningSettings";
import SpeechModelSelect from "../voice/SpeechModelSelect";
import { useUiTranslation } from "../../i18n";

const VoiceSettingsContent = ({
  voiceSettings,
  availableVoices,
  voiceStatus,
  voiceError,
  isVoiceLoading,
  isVoiceTestPlaying,
  isInstallingVoice,
  isInstallingWhisper,
  voiceModelsStatus,
  handleVoiceSettingChange,
  installWhisperCpp,
  installWhisperSpeechModel,
  installDefaultVoiceModel,
  testVoice,
  systemName,
  whisperManualInstall,
  onCopyWhisperCommand,
  onManageVoiceModels,
  showMessage,
}) => {
  const { t } = useUiTranslation();
  const whisperCliMissing = voiceStatus && voiceStatus.whisper_installed === false;
  const pendingManual = whisperManualInstall
    || (whisperCliMissing && voiceStatus?.can_auto_install === false && voiceStatus?.manual_command
      ? {
        command: voiceStatus.manual_command,
        reason: t("settingsSections.voice.manualReason"),
      }
      : null);
  const installTooltip = voiceStatus?.install_method === 'pkexec'
    ? t("settingsSections.voice.installTooltipPkexec")
    : t("settingsSections.voice.installTooltip");

  return (
    <>
      {isVoiceLoading && (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 2 }}>
          <CircularProgress />
        </Box>
      )}

      {voiceError && (
        <MuiAlert severity="error" sx={{ mb: 2 }}>
          {voiceError}
        </MuiAlert>
      )}

      {!isVoiceLoading && !voiceError && whisperCliMissing && (
        <>
          <MuiAlert
            severity="info"
            sx={{ mb: pendingManual ? 1 : 2 }}
            action={
              <Tooltip title={installTooltip}>
                <span>
                  <Button
                    color="inherit"
                    size="small"
                    startIcon={isInstallingWhisper ? <CircularProgress size={16} color="inherit" /> : <FileDownloadIcon />}
                    onClick={installWhisperCpp}
                    disabled={isInstallingWhisper}
                  >
                    {isInstallingWhisper ? t("settingsSections.voice.building") : t("settingsSections.voice.installWhisper")}
                  </Button>
                </span>
              </Tooltip>
            }
          >
            {t("settingsSections.voice.whisperMissing")}
          </MuiAlert>
          {pendingManual && (
            <MuiAlert severity="warning" sx={{ mb: 2 }}>
              <Typography variant="body2" sx={{ mb: 1 }}>{pendingManual.reason}</Typography>
              <Box
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 1,
                  p: 1,
                  borderRadius: 1,
                  bgcolor: 'action.hover',
                  fontFamily: 'monospace',
                  fontSize: '0.8rem',
                  overflowX: 'auto',
                }}
              >
                <Box component="code" sx={{ flex: 1, whiteSpace: 'pre' }}>
                  {pendingManual.command}
                </Box>
                <Tooltip title={t("settingsSections.voice.copyCommand")}>
                  <Button
                    size="small"
                    color="inherit"
                    startIcon={<ContentCopyIcon fontSize="small" />}
                    onClick={() => onCopyWhisperCommand?.(pendingManual.command)}
                    sx={{ flexShrink: 0 }}
                  >
                    {t("settingsSections.voice.copy")}
                  </Button>
                </Tooltip>
              </Box>
            </MuiAlert>
          )}
        </>
      )}

      {/* Speech model: its weights arrive only through this Install */}
      {!isVoiceLoading && !voiceError && voiceStatus && voiceStatus.speech_model_installed === false && (
        <MuiAlert
          severity="warning"
          sx={{ mb: 2 }}
          action={
            <Button
              color="inherit"
              size="small"
              startIcon={isInstallingWhisper ? <CircularProgress size={16} color="inherit" /> : <FileDownloadIcon />}
              onClick={installWhisperSpeechModel}
              disabled={isInstallingWhisper}
            >
              {t("settingsSections.voice.install")}
            </Button>
          }
        >
          {t("settingsSections.voice.speechModelMissing")}
        </MuiAlert>
      )}

      {/* FFmpeg Warning */}
      {!isVoiceLoading && !voiceError && voiceStatus && voiceStatus.ffmpeg_available === false && (
        <MuiAlert severity="warning" sx={{ mb: 2 }}>
          {t("settingsSections.voice.ffmpegMissing")}
        </MuiAlert>
      )}

      {/* Voice Model Installation Alert */}
      {!isVoiceLoading && !voiceError && voiceModelsStatus && voiceModelsStatus.installed_count === 0 && (
        <MuiAlert
          severity="warning"
          sx={{ mb: 2 }}
          action={
            <Button
              color="inherit"
              size="small"
              startIcon={isInstallingVoice ? <CircularProgress size={16} color="inherit" /> : <FileDownloadIcon />}
              onClick={installDefaultVoiceModel}
              disabled={isInstallingVoice}
            >
              {isInstallingVoice ? t("settingsSections.voice.installing") : t("settingsSections.voice.install")}
            </Button>
          }
        >
          {t("settingsSections.voice.noVoiceModels")}
        </MuiAlert>
      )}

      {/* Show install option in header area if some but not all models installed */}
      {!isVoiceLoading && !voiceError && voiceModelsStatus && voiceModelsStatus.installed_count > 0 && !voiceModelsStatus.models?.find(m => m.voice_id === 'libritts')?.installed && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
          <Typography variant="body2" color="text.secondary">
            {t("settingsSections.voice.libriTtsMissing")}
          </Typography>
          <Chip
            label={isInstallingVoice ? t("settingsSections.voice.installing") : t("settingsSections.voice.installLibriTts")}
            size="small"
            color="primary"
            variant="outlined"
            icon={isInstallingVoice ? <CircularProgress size={14} /> : <FileDownloadIcon sx={{ fontSize: 16 }} />}
            onClick={installDefaultVoiceModel}
            disabled={isInstallingVoice}
            sx={{ cursor: isInstallingVoice ? 'wait' : 'pointer' }}
          />
        </Box>
      )}

      {!isVoiceLoading && !voiceError && (
        <>
          {/* Main Controls */}
          <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', mb: 3 }}>
            <Chip
              label={t("settingsSections.voice.textToSpeech")}
              color={voiceSettings.ttsEnabled ? 'primary' : 'default'}
              onClick={() => handleVoiceSettingChange('ttsEnabled', !voiceSettings.ttsEnabled)}
              variant={voiceSettings.ttsEnabled ? 'filled' : 'outlined'}
              size="small"
              sx={{
                '& .MuiChip-label': {
                  color: voiceSettings.ttsEnabled ? 'inherit' : 'text.secondary'
                }
              }}
            />
            <Chip
              label={t("settingsSections.voice.microphone")}
              color={voiceSettings.micEnabled ? 'primary' : 'default'}
              onClick={() => handleVoiceSettingChange('micEnabled', !voiceSettings.micEnabled)}
              variant={voiceSettings.micEnabled ? 'filled' : 'outlined'}
              size="small"
              sx={{
                '& .MuiChip-label': {
                  color: voiceSettings.micEnabled ? 'inherit' : 'text.secondary'
                }
              }}
            />
            <Chip
              label={t("settingsSections.voice.narrateButtons")}
              color={voiceSettings.showNarrateButtons !== false ? 'primary' : 'default'}
              onClick={() => handleVoiceSettingChange('showNarrateButtons', voiceSettings.showNarrateButtons === false)}
              variant={voiceSettings.showNarrateButtons !== false ? 'filled' : 'outlined'}
              size="small"
              sx={{
                '& .MuiChip-label': {
                  color: voiceSettings.showNarrateButtons !== false ? 'inherit' : 'text.secondary'
                }
              }}
            />
          </Box>

          {/* Voice, Quality, and Audio in Grid */}
          <Grid container spacing={1.5} sx={{ mb: 3 }}>
            {/* Voice Selection */}
            <Grid item xs={12}>
              <Typography variant="body2" color="text.primary" sx={{ mb: 0.5, fontSize: '0.75rem' }}>{t("common.voice")}</Typography>
              <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap', alignItems: 'center' }}>
                {availableVoices.map((voice) => {
                  const isAvailable = voice.available !== false;
                  const isSelected = voiceSettings.voice === voice.id;
                  return (
                    <Chip
                      key={voice.id}
                      label={isAvailable ? voice.name : t("settingsSections.voice.voiceNotInstalled", { name: voice.name })}
                      size="small"
                      color={isSelected ? 'primary' : 'default'}
                      onClick={() => isAvailable && handleVoiceSettingChange('voice', voice.id)}
                      variant={isSelected ? 'filled' : 'outlined'}
                      disabled={!voiceSettings.ttsEnabled || !isAvailable}
                      sx={{
                        '& .MuiChip-label': {
                          color: isSelected ? 'inherit' : (isAvailable ? 'text.secondary' : 'text.disabled')
                        },
                        opacity: isAvailable ? 1 : 0.5
                      }}
                    />
                  );
                })}
                <Chip
                  label={isVoiceTestPlaying ? "..." : t("settingsSections.voice.test")}
                  onClick={() => testVoice(voiceSettings.voice)}
                  disabled={!voiceSettings.ttsEnabled || isVoiceTestPlaying}
                  size="small"
                  color={isVoiceTestPlaying ? "default" : "primary"}
                  variant={isVoiceTestPlaying ? "filled" : "outlined"}
                  sx={{ ml: 0.5 }}
                />
              </Box>
            </Grid>

            {/* Quality and Audio in same row */}
            <Grid item xs={6}>
              <Typography variant="body2" color="text.primary" sx={{ mb: 0.5, fontSize: '0.75rem' }}>{t("settingsSections.voice.quality")}</Typography>
              <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                <Chip
                  label={t("settingsSections.voice.low")}
                  size="small"
                  color={voiceSettings.recordingQuality === 'low' ? 'primary' : 'default'}
                  onClick={() => handleVoiceSettingChange('recordingQuality', 'low')}
                  variant={voiceSettings.recordingQuality === 'low' ? 'filled' : 'outlined'}
                  sx={{
                    '& .MuiChip-label': {
                      color: voiceSettings.recordingQuality === 'low' ? 'inherit' : 'text.secondary'
                    }
                  }}
                />
                <Chip
                  label={t("settingsSections.voice.medium")}
                  size="small"
                  color={voiceSettings.recordingQuality === 'medium' ? 'primary' : 'default'}
                  onClick={() => handleVoiceSettingChange('recordingQuality', 'medium')}
                  variant={voiceSettings.recordingQuality === 'medium' ? 'filled' : 'outlined'}
                  sx={{
                    '& .MuiChip-label': {
                      color: voiceSettings.recordingQuality === 'medium' ? 'inherit' : 'text.secondary'
                    }
                  }}
                />
                <Chip
                  label={t("settingsSections.voice.high")}
                  size="small"
                  color={voiceSettings.recordingQuality === 'high' ? 'primary' : 'default'}
                  onClick={() => handleVoiceSettingChange('recordingQuality', 'high')}
                  variant={voiceSettings.recordingQuality === 'high' ? 'filled' : 'outlined'}
                  sx={{
                    '& .MuiChip-label': {
                      color: voiceSettings.recordingQuality === 'high' ? 'inherit' : 'text.secondary'
                    }
                  }}
                />
              </Box>
            </Grid>

            <Grid item xs={6}>
              <Typography variant="body2" color="text.primary" sx={{ mb: 0.5, fontSize: '0.75rem' }}>{t("settingsSections.voice.audio")}</Typography>
              <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                <Chip
                  label={t("settingsSections.voice.gain")}
                  size="small"
                  color={voiceSettings.autoGainControl ? 'primary' : 'default'}
                  onClick={() => handleVoiceSettingChange('autoGainControl', !voiceSettings.autoGainControl)}
                  variant={voiceSettings.autoGainControl ? 'filled' : 'outlined'}
                  sx={{
                    '& .MuiChip-label': {
                      color: voiceSettings.autoGainControl ? 'inherit' : 'text.secondary'
                    }
                  }}
                />
                <Chip
                  label={t("settingsSections.voice.noise")}
                  size="small"
                  color={voiceSettings.noiseSuppression ? 'primary' : 'default'}
                  onClick={() => handleVoiceSettingChange('noiseSuppression', !voiceSettings.noiseSuppression)}
                  variant={voiceSettings.noiseSuppression ? 'filled' : 'outlined'}
                  sx={{
                    '& .MuiChip-label': {
                      color: voiceSettings.noiseSuppression ? 'inherit' : 'text.secondary'
                    }
                  }}
                />
                <Chip
                  label={t("settingsSections.voice.echo")}
                  size="small"
                  color={voiceSettings.echoCancellation ? 'primary' : 'default'}
                  onClick={() => handleVoiceSettingChange('echoCancellation', !voiceSettings.echoCancellation)}
                  variant={voiceSettings.echoCancellation ? 'filled' : 'outlined'}
                  sx={{
                    '& .MuiChip-label': {
                      color: voiceSettings.echoCancellation ? 'inherit' : 'text.secondary'
                    }
                  }}
                />
              </Box>
            </Grid>
          </Grid>

          {/* Speech recognition model (shared with the Voice page); re-read after each status load */}
          <Box sx={{ mb: 3 }}>
            <SpeechModelSelect
              reloadKey={voiceStatus}
              onManageModels={onManageVoiceModels}
              showMessage={showMessage}
            />
          </Box>

          {/* Listening: mic button mode, speech detection, wake phrase (shared with the Voice page) */}
          <VoiceListeningSettings
            settings={voiceSettings}
            onChange={handleVoiceSettingChange}
            systemName={systemName}
          />
        </>
      )}
    </>
  );
};

export default VoiceSettingsContent;
