import React from "react";
import {
  Typography,
  Box,
  Button,
  CircularProgress,
  Grid,
  Slider,
  Chip,
  Switch,
  FormControlLabel,
} from "@mui/material";
import MuiAlert from "@mui/material/Alert";
import FileDownloadIcon from "@mui/icons-material/FileDownload";

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
}) => {
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

      {/* Whisper.cpp Installation Alert */}
      {!isVoiceLoading && !voiceError && voiceStatus && voiceStatus.whisper_installed === false && (
        <MuiAlert
          severity="info"
          sx={{ mb: 2 }}
          action={
            <Button
              color="inherit"
              size="small"
              startIcon={isInstallingWhisper ? <CircularProgress size={16} color="inherit" /> : <FileDownloadIcon />}
              onClick={installWhisperCpp}
              disabled={isInstallingWhisper}
            >
              {isInstallingWhisper ? 'Building...' : 'Install Whisper'}
            </Button>
          }
        >
          Speech recognition (Whisper.cpp) is not installed. Install it to enable voice input.
        </MuiAlert>
      )}

      {/* Whisper Model Download Alert */}
      {!isVoiceLoading && !voiceError && voiceStatus && voiceStatus.whisper_installed === true && voiceStatus.whisper_models_available?.length === 0 && (
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
              {isInstallingWhisper ? 'Downloading...' : 'Download Model'}
            </Button>
          }
        >
          Whisper.cpp installed but no speech models found. Download a model to enable speech recognition.
        </MuiAlert>
      )}

      {/* FFmpeg Warning */}
      {!isVoiceLoading && !voiceError && voiceStatus && voiceStatus.ffmpeg_available === false && (
        <MuiAlert severity="warning" sx={{ mb: 2 }}>
          FFmpeg is not installed. Voice features require FFmpeg. Install it with: sudo apt install ffmpeg
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
              {isInstallingVoice ? 'Installing...' : 'Install'}
            </Button>
          }
        >
          No voice models installed. Install LibriTTS (English US) to enable Text-to-Speech.
        </MuiAlert>
      )}

      {/* Show install option in header area if some but not all models installed */}
      {!isVoiceLoading && !voiceError && voiceModelsStatus && voiceModelsStatus.installed_count > 0 && !voiceModelsStatus.models?.find(m => m.voice_id === 'libritts')?.installed && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
          <Typography variant="body2" color="text.secondary">
            LibriTTS (recommended voice) is not installed.
          </Typography>
          <Chip
            label={isInstallingVoice ? 'Installing...' : 'Install LibriTTS'}
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
              label="Text-to-Speech"
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
              label="Microphone"
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
          </Box>

          {/* Voice, Quality, and Audio in Grid */}
          <Grid container spacing={1.5} sx={{ mb: 3 }}>
            {/* Voice Selection */}
            <Grid item xs={12}>
              <Typography variant="body2" color="text.primary" sx={{ mb: 0.5, fontSize: '0.75rem' }}>Voice</Typography>
              <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap', alignItems: 'center' }}>
                {availableVoices.map((voice) => {
                  const isAvailable = voice.available !== false;
                  const isSelected = voiceSettings.voice === voice.id;
                  return (
                    <Chip
                      key={voice.id}
                      label={voice.name + (isAvailable ? '' : ' (not installed)')}
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
                  label={isVoiceTestPlaying ? "..." : "Test"}
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
              <Typography variant="body2" color="text.primary" sx={{ mb: 0.5, fontSize: '0.75rem' }}>Quality</Typography>
              <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                <Chip
                  label="Low"
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
                  label="Med"
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
                  label="High"
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
              <Typography variant="body2" color="text.primary" sx={{ mb: 0.5, fontSize: '0.75rem' }}>Audio</Typography>
              <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                <Chip
                  label="Gain"
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
                  label="Noise"
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
                  label="Echo"
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

          {/* Continuous Listening Settings */}
          <Typography variant="subtitle2" sx={{ mb: 1, fontSize: '0.85rem' }}>
            Listening
          </Typography>

          <Grid container spacing={2}>
            <Grid item xs={12} sm={6}>
              <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.75rem', mb: 0.5 }}>
                Threshold: {(voiceSettings.silenceThreshold || 0.05).toFixed(2)}
              </Typography>
              <Slider
                value={voiceSettings.silenceThreshold || 0.05}
                onChange={(e, value) => handleVoiceSettingChange('silenceThreshold', value)}
                min={0.01}
                max={0.2}
                step={0.01}
                valueLabelDisplay="auto"
                valueLabelFormat={(value) => value.toFixed(2)}
                size="small"
              />
            </Grid>

            <Grid item xs={12} sm={6}>
              <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.75rem', mb: 0.5 }}>
                Timeout: {((voiceSettings.silenceTimeout || 2000) / 1000).toFixed(1)}s
              </Typography>
              <Slider
                value={voiceSettings.silenceTimeout || 2000}
                onChange={(e, value) => handleVoiceSettingChange('silenceTimeout', value)}
                min={1000}
                max={5000}
                step={500}
                valueLabelDisplay="auto"
                valueLabelFormat={(value) => `${(value / 1000).toFixed(1)}s`}
                size="small"
              />
            </Grid>

            <Grid item xs={12}>
              <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.75rem', mb: 0.5 }}>
                Segment: {((voiceSettings.maxSegmentDuration || 30000) / 1000).toFixed(0)}s
              </Typography>
              <Slider
                value={voiceSettings.maxSegmentDuration || 30000}
                onChange={(e, value) => handleVoiceSettingChange('maxSegmentDuration', value)}
                min={10000}
                max={60000}
                step={5000}
                valueLabelDisplay="auto"
                valueLabelFormat={(value) => `${(value / 1000).toFixed(0)}s`}
                size="small"
                marks={[
                  { value: 10000, label: '10s' },
                  { value: 30000, label: '30s' },
                  { value: 60000, label: '60s' }
                ]}
              />
            </Grid>
          </Grid>

          {/* Wake Word Settings */}
          <Typography variant="subtitle2" sx={{ mt: 3, mb: 1, fontSize: '0.85rem' }}>
            Wake Word
          </Typography>

          <Box sx={{ mb: 2 }}>
            <FormControlLabel
              control={
                <Switch
                  checked={voiceSettings.wakeWordEnabled || false}
                  onChange={(e) => handleVoiceSettingChange('wakeWordEnabled', e.target.checked)}
                  size="small"
                />
              }
              label={
                <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.8rem' }}>
                  Enable wake word detection
                </Typography>
              }
            />
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', ml: 4.5, mt: -0.5 }}>
              Say &quot;Hey {systemName || 'Guaardvark'}&quot; to activate listening in Listener mode
            </Typography>
          </Box>

          {voiceSettings.wakeWordEnabled && (
            <Grid container spacing={2}>
              <Grid item xs={12} sm={6}>
                <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.75rem', mb: 0.5 }}>
                  Active duration: {((voiceSettings.activeListeningDuration || 30000) / 1000).toFixed(0)}s
                </Typography>
                <Slider
                  value={voiceSettings.activeListeningDuration || 30000}
                  onChange={(e, value) => handleVoiceSettingChange('activeListeningDuration', value)}
                  min={10000}
                  max={120000}
                  step={5000}
                  valueLabelDisplay="auto"
                  valueLabelFormat={(value) => `${(value / 1000).toFixed(0)}s`}
                  size="small"
                  marks={[
                    { value: 10000, label: '10s' },
                    { value: 30000, label: '30s' },
                    { value: 60000, label: '60s' },
                    { value: 120000, label: '120s' }
                  ]}
                />
              </Grid>
              <Grid item xs={12} sm={6}>
                <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.75rem', mb: 0.5 }}>
                  System name
                </Typography>
                <Chip
                  label={systemName || 'Guaardvark'}
                  size="small"
                  variant="outlined"
                  color="primary"
                />
                <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
                  Change in Settings &rarr; Branding
                </Typography>
              </Grid>
            </Grid>
          )}
        </>
      )}
    </>
  );
};

export default VoiceSettingsContent;
