import React, { useState, useEffect, useRef, useCallback } from 'react';
import PropTypes from 'prop-types';
import { 
  Box, 
  Paper, 
  Typography, 
  Button, 
  IconButton, 
  Chip,
  Alert,
  CircularProgress,
  Divider
} from '@mui/material';
import { 
  Mic as MicIcon, 
  MicOff as MicOffIcon,
  Send as SendIcon,
  Cancel as CancelIcon,
  Settings as SettingsIcon,
  VolumeUp as VolumeUpIcon
} from '@mui/icons-material';
import voiceService from '../../api/voiceService';
import { BACKEND_URL } from '../../api/apiClient';
import AudioVisualizer from './AudioVisualizer';
import VolumeMeter from './VolumeMeter';
import VoiceSettingsModal from './VoiceSettingsModal';

const VoiceChat = ({ 
  sessionId = 'default',
  onMessageReceived = () => {},
  onError = () => {},
  className = ''
}) => {
  const [voiceSettings, setVoiceSettings] = useState({
    voice: 'libritts',
    recordingQuality: 'medium',
    recordingVolume: 1.0,
    autoGainControl: true,
    noiseSuppression: true,
    echoCancellation: true,
    visualizationStyle: 'waveform',
    maxRecordingDuration: 60,
    playbackVolume: 1.0,
    playbackSpeed: 1.0,
    enableVisualization: true,
    autoSendEnabled: false,
    autoSendDelay: 2000,
    volumeSensitivity: 0.3,
    volumeThreshold: 0.1,
  });

  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const [processingStage, setProcessingStage] = useState('');
  const [lastTranscription, setLastTranscription] = useState('');
  const [lastResponse, setLastResponse] = useState('');
  const [isPlayingResponse, setIsPlayingResponse] = useState(false);
  const [conversationHistory, setConversationHistory] = useState([]);
  const [currentAudioUrl, setCurrentAudioUrl] = useState(null);
  const [stoppedAudioBlob, setStoppedAudioBlob] = useState(null);

  const [isRecording, setIsRecording] = useState(false);
  const [recordingVolume, setRecordingVolume] = useState(0);
  const [duration, setDuration] = useState(0);
  const [audioLevels, setAudioLevels] = useState([]);
  const [recordingError, setRecordingError] = useState(null);
  const [permission, setPermission] = useState('unknown');

  const silenceTimeoutRef = useRef(null);
  const processingTimeoutRef = useRef(null);
  const volumeMonitoringRef = useRef(null);
  const durationTimerRef = useRef(null);
  const startTimeRef = useRef(null);

  useEffect(() => {
    let volumeInterval;
    
    if (isRecording) {
      volumeInterval = setInterval(() => {
        try {
          const volume = voiceService.calculateVolume();
          const levels = voiceService.getAudioLevels();
          setRecordingVolume(volume);
          setAudioLevels(levels);
          
          if (Math.random() < 0.1) {
            console.log('VoiceChat: Volume detected (direct voiceService):', {
              volume: (volume || 0).toFixed(3),
              audioLevels: levels.length,
              timestamp: Date.now()
            });
          }
        } catch (err) {
          console.warn('VoiceChat: Error getting volume:', err);
          setRecordingVolume(0);
        }
      }, 100);
    }

    return () => {
      if (volumeInterval) {
        clearInterval(volumeInterval);
      }
    };
  }, [isRecording]);

  useEffect(() => {
    return () => {
      if (silenceTimeoutRef.current) {
        clearTimeout(silenceTimeoutRef.current);
      }
      if (processingTimeoutRef.current) {
        clearTimeout(processingTimeoutRef.current);
      }
      if (volumeMonitoringRef.current) {
        clearInterval(volumeMonitoringRef.current);
      }
      if (durationTimerRef.current) {
        clearInterval(durationTimerRef.current);
      }
      
      voiceService.cleanup();
    };
  }, []);

  useEffect(() => {
    if (isRecording && startTimeRef.current) {
      durationTimerRef.current = setInterval(() => {
        const elapsed = Date.now() - startTimeRef.current;
        setDuration(elapsed);
        
        if (elapsed >= voiceSettings.maxRecordingDuration * 1000) {
          handleStopRecording();
        }
      }, 100);
    } else {
      if (durationTimerRef.current) {
        clearInterval(durationTimerRef.current);
        durationTimerRef.current = null;
      }
    }

    return () => {
      if (durationTimerRef.current) {
        clearInterval(durationTimerRef.current);
      }
    };
  }, [isRecording, voiceSettings.maxRecordingDuration]);

  useEffect(() => {
    const checkPermission = async () => {
      try {
        const permissionState = await voiceService.checkMicrophonePermission();
        setPermission(permissionState);
      } catch (err) {
        console.error('Failed to check microphone permission:', err);
        setPermission('denied');
      }
    };
    checkPermission();
  }, []);

  useEffect(() => {
    if (isRecording) {
      console.log('VoiceChat: Recording status:', {
        isRecording: isRecording,
        volume: recordingVolume || 0,
        audioLevels: audioLevels?.length || 0,
        duration: duration,
        error: recordingError
      });
    }
  }, [isRecording, recordingVolume, audioLevels, duration, recordingError]);

  const handleStartRecording = async () => {
    try {
      console.log('VoiceChat: Starting recording...');
      
      const resumed = await voiceService.resumeAudioContext();
      if (!resumed) {
        console.warn('VoiceChat: Audio context could not be resumed');
      }
      
      setIsRecording(true);
      startTimeRef.current = Date.now();
      setRecordingError(null);
      setPermission('unknown');
      await voiceService.startRecording();
      console.log('VoiceChat: Recording started successfully');
    } catch (error) {
      console.error('VoiceChat: Failed to start recording:', error);
      setRecordingError(error.message || 'Failed to start recording');
      onError(error);
    }
  };

  useEffect(() => {
    const currentVolume = recordingVolume || 0;
    if (voiceSettings.autoSendEnabled && isRecording && currentVolume < 0.1) {
      if (silenceTimeoutRef.current) {
        clearTimeout(silenceTimeoutRef.current);
      }
      
      silenceTimeoutRef.current = setTimeout(() => {
        if (isRecording) {
          handleStopAndSend();
        }
      }, voiceSettings.autoSendDelay);
    } else if (silenceTimeoutRef.current) {
      clearTimeout(silenceTimeoutRef.current);
      silenceTimeoutRef.current = null;
    }

    return () => {
      if (silenceTimeoutRef.current) {
        clearTimeout(silenceTimeoutRef.current);
      }
    };
  }, [isRecording, recordingVolume, voiceSettings.autoSendEnabled, voiceSettings.autoSendDelay]);

  const handleVoiceStream = useCallback(async (audioBlob) => {
    try {
      setIsProcessing(true);
      setProcessingStage('Transcribing audio...');

      if (!audioBlob || audioBlob.size < 1000) {
        throw new Error('Recording too short or empty. Please record for at least 1-2 seconds.');
      }

      if (audioBlob.size < 5000) {
        console.warn('VoiceChat: Very small audio file detected, may be silent');
      }

      console.log('VoiceChat: Processing audio blob', {
        size: audioBlob.size,
        type: audioBlob.type,
        duration: duration,
        recordingVolume: recordingVolume || 0
      });

      const maxVolume = recordingVolume || 0;
      if (maxVolume < 0.01) {
        console.warn('VoiceChat: No significant volume detected during recording');
      }

      const result = await voiceService.streamVoiceChat(audioBlob, sessionId);
      
      setProcessingStage('Processing response...');
      
      const newEntry = {
        id: Date.now(),
        timestamp: new Date().toISOString(),
        userAudio: audioBlob,
        transcription: result.transcribed_text,
        llmResponse: result.llm_response,
        audioUrl: result.audio_url ? `${BACKEND_URL}${result.audio_url}` : null,
      };

      setConversationHistory(prev => [...prev, newEntry]);
      setLastTranscription(result.transcribed_text);
      setLastResponse(result.llm_response);

      let audioUrl = null;
      
      if (result.audio_url) {
        audioUrl = `${BACKEND_URL}${result.audio_url}`;
        console.log('VoiceChat: Using backend-provided audio URL:', audioUrl);
      } else if (result.tts_handled_by === 'frontend' && result.llm_response) {
        console.log('VoiceChat: Backend expects frontend TTS, generating audio...');
        setProcessingStage('Generating speech...');
        
        try {
          const cleanedResponse = result.llm_response
            .replace(/\*{1,3}([^*]+)\*{1,3}/g, '$1')
            .replace(/_{1,3}([^_]+)_{1,3}/g, '$1')
            .replace(/#{1,6}\s*([^\n]+)/g, '$1')
            .replace(/```[^`]*```/g, '')
            .replace(/`([^`]+)`/g, '$1')
            .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
            .replace(/<[^>]*>/g, '')
            .replace(/\s+/g, ' ')
            .replace(/[•\-\*]\s*/g, '')
            .replace(/\n\s*\n/g, '. ')
            .replace(/\n/g, ', ')
            .replace(/\s*\.\s*\.\s*\./g, '. ')
            .replace(/[!]{2,}/g, '!')
            .replace(/[?]{2,}/g, '?')
            .trim()
            .replace(/([^.!?])\s*$/, '$1.');
          
          console.log('VoiceChat: Cleaning text for TTS - Original:', result.llm_response.substring(0, 100) + '...');
          console.log('VoiceChat: Cleaned text:', cleanedResponse.substring(0, 100) + '...');
          
          const ttsResult = await voiceService.textToSpeech(cleanedResponse, voiceSettings.voice);
          if (ttsResult.audio_url) {
            audioUrl = `${BACKEND_URL}${ttsResult.audio_url}`;
            console.log('VoiceChat: Frontend TTS generated audio URL:', audioUrl);
          } else {
            console.warn('VoiceChat: TTS API did not return audio URL');
          }
        } catch (ttsError) {
          console.error('VoiceChat: Frontend TTS failed:', ttsError);
        }
      } else {
        console.warn('VoiceChat: No audio URL provided and no TTS handling specified');
      }

      if (audioUrl) {
        setCurrentAudioUrl(audioUrl);
        await playResponseAudio(audioUrl);
      } else {
        console.log('VoiceChat: No audio to play - response available as text only');
      }

      onMessageReceived({
        transcription: result.transcribed_text,
        response: result.llm_response,
        audioUrl: audioUrl,
      });

    } catch (error) {
      console.error('Voice stream error:', error);
      
      if (error.message && error.message.includes('No speech detected')) {
        const enhancedError = new Error(
          'No speech detected in recording. Please try:\n' +
          '• Speaking louder and clearer\n' +
          '• Getting closer to the microphone\n' +
          '• Reducing background noise\n' +
          '• Recording for 2-5 seconds minimum\n' +
          '• Checking your microphone volume settings'
        );
        enhancedError.type = 'speech_detection_failure';
        onError(enhancedError);
      } else {
        onError(error);
      }
    } finally {
      setIsProcessing(false);
      setProcessingStage('');
    }
  }, [sessionId, onMessageReceived, onError, voiceSettings.voice]);

  const playResponseAudio = async (audioUrl) => {
    try {
      setIsPlayingResponse(true);
      await voiceService.playAudio(audioUrl, {
        volume: voiceSettings.playbackVolume,
        playbackRate: voiceSettings.playbackSpeed,
      });
    } catch (error) {
      console.error('Error playing response audio:', error);
    } finally {
      setIsPlayingResponse(false);
    }
  };

  const handleStopRecording = async () => {
    try {
      console.log('VoiceChat: Stopping recording...');
      setIsRecording(false);
      const audioBlob = await voiceService.stopRecording();
      
      if (audioBlob) {
        console.log('VoiceChat: Recording stopped successfully', {
          size: audioBlob.size,
          type: audioBlob.type,
          duration: duration
        });
        setStoppedAudioBlob(audioBlob);
        return audioBlob;
      } else {
        console.warn('VoiceChat: No audio blob returned from recorder');
        return null;
      }
    } catch (error) {
      console.error('VoiceChat: Failed to stop recording:', error);
      setRecordingError(error.message || 'Failed to stop recording');
      setIsRecording(false);
      onError(error);
      return null;
    }
  };

  const handleCancelRecording = () => {
    console.log('VoiceChat: Cancelling recording...');
    setIsRecording(false);
    setRecordingVolume(0);
    setDuration(0);
    setAudioLevels([]);
    setRecordingError(null);
    
    try {
      voiceService.cleanup();
    } catch (error) {
      console.warn('VoiceChat: Error during recording cleanup:', error);
    }
    
    setStoppedAudioBlob(null);
    if (silenceTimeoutRef.current) {
      clearTimeout(silenceTimeoutRef.current);
      silenceTimeoutRef.current = null;
    }
  };

  const handleSendStoppedAudio = async () => {
    if (!stoppedAudioBlob) {
      onError(new Error('No audio to send. Please record first.'));
      return;
    }

    console.log('VoiceChat: Sending stopped audio blob', {
      size: stoppedAudioBlob.size,
      type: stoppedAudioBlob.type
    });

    await handleVoiceStream(stoppedAudioBlob);
    setStoppedAudioBlob(null);
  };

  const handleStopAndSend = async () => {
    if (duration < 1000) {
      onError(new Error('Please record for at least 1 second before sending.'));
      return;
    }

    const audioBlob = await handleStopRecording();
    if (audioBlob) {
      await handleVoiceStream(audioBlob);
      setStoppedAudioBlob(null);
    }
  };

  const handleSettingsSave = (newSettings) => {
    setVoiceSettings(newSettings);
    localStorage.setItem('guaardvark_voiceSettings', JSON.stringify(newSettings));
    window.dispatchEvent(new Event('voiceSettingsChanged'));
  };

  useEffect(() => {
    const savedSettings = localStorage.getItem('guaardvark_voiceSettings');
    if (savedSettings) {
      try {
        const parsed = JSON.parse(savedSettings);
        setVoiceSettings(prev => ({ ...prev, ...parsed }));
      } catch (error) {
        console.error('Failed to load voice settings:', error);
      }
    }
  }, []);

  useEffect(() => {
    return () => {
      if (silenceTimeoutRef.current) {
        clearTimeout(silenceTimeoutRef.current);
      }
      if (processingTimeoutRef.current) {
        clearTimeout(processingTimeoutRef.current);
      }
      if (durationTimerRef.current) {
        clearInterval(durationTimerRef.current);
      }
      if (volumeMonitoringRef.current) {
        clearInterval(volumeMonitoringRef.current);
      }
      
      if (currentAudioUrl) {
        voiceService.cleanupAudioUrl(currentAudioUrl);
      }
      
      try {
        voiceService.cleanup();
      } catch (error) {
        console.warn('VoiceChat: Error during cleanup:', error);
      }
    };
  }, [currentAudioUrl]);

  const getRecordingButtonVariant = () => {
    if (isProcessing) return 'contained';
    if (isRecording) return 'contained';
    return 'outlined';
  };

  const getRecordingButtonColor = () => {
    if (isProcessing) return 'warning';
    if (isRecording) return 'error';
    return 'primary';
  };

  const getRecordingButtonText = () => {
    if (isProcessing) return processingStage || 'Processing...';
    if (isRecording) {
      const minDuration = 1000;
      if (duration < minDuration) {
        return `Recording... ${(duration / 1000).toFixed(1)}s (speak for ${Math.ceil((minDuration - duration) / 1000)}s more)`;
      }
      return `Recording... ${(duration / 1000).toFixed(1)}s (ready to send)`;
    }
    return 'Start Recording';
  };

  const volumePercentage = Math.round((recordingVolume || 0) * 100);

  return (
    <Box className={className} sx={{ width: '100%' }}>
      {}
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 3 }}>
        <Typography variant="h5" component="h2" fontWeight="bold">
          Voice Assistant
        </Typography>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
          {}
          <Chip
            label={`Volume: ${volumePercentage}%`}
            color={isRecording && volumePercentage > 0 ? 'success' : 'default'}
            variant={isRecording ? 'filled' : 'outlined'}
            size="small"
          />
          <IconButton
          onClick={() => setIsSettingsOpen(true)}
            color="primary"
            size="medium"
        >
            <SettingsIcon />
          </IconButton>
        </Box>
      </Box>

      {}
      {recordingError && (
        <Alert severity="error" sx={{ mb: 2 }}>
          <Typography variant="body2">
            <strong>Recording Error:</strong> {recordingError}
          </Typography>
        </Alert>
      )}

      {}
      {isRecording && (
        <Alert severity="info" sx={{ mb: 2 }}>
          <Typography variant="body2">
                         Debug: Volume={(recordingVolume || 0).toFixed(3)}, AudioLevels={audioLevels?.length || 0}, IsRecording={String(isRecording)}
          </Typography>
        </Alert>
      )}

      {}
      <Paper
        elevation={1}
        sx={{
          p: 3,
          mb: 3,
          backgroundColor: 'background.paper',
          border: isRecording ? '2px solid' : '1px solid',
          borderColor: isRecording ? 'error.main' : 'divider',
        }}
      >
        <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 3 }}>
          
          {}
          {voiceSettings.enableVisualization && (
            <Box sx={{ width: '100%', maxWidth: 500 }}>
              <AudioVisualizer
                audioLevels={audioLevels}
                volume={recordingVolume}
                isRecording={isRecording}
                width={400}
                height={120}
                style={voiceSettings.visualizationStyle}
              />
            </Box>
          )}

          {}
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 3, width: '100%', maxWidth: 400 }}>
            <VolumeMeter
              volume={recordingVolume}
              isRecording={isRecording}
              orientation="horizontal"
              size="medium"
              color="gradient"
            />
            
            {}
                          <Chip
                label={`${(duration / 1000).toFixed(1)}s`}
                variant={isRecording ? 'filled' : 'outlined'}
                color={isRecording ? 'error' : 'default'}
                sx={{ fontFamily: 'monospace' }}
              />
          </Box>

          {}
          <Box sx={{ display: 'flex', gap: 2, alignItems: 'center', flexWrap: 'wrap', justifyContent: 'center' }}>
            {!isRecording && !isProcessing && !stoppedAudioBlob && (
              <Button
                variant={getRecordingButtonVariant()}
                color={getRecordingButtonColor()}
                onClick={handleStartRecording}
                disabled={permission === 'denied'}
                startIcon={<MicIcon />}
                size="large"
              >
                {getRecordingButtonText()}
              </Button>
            )}

            {isRecording && (
              <>
                <Button
                  variant="contained"
                  color="warning"
                  onClick={handleStopRecording}
                  startIcon={<MicOffIcon />}
                  size="large"
                >
                  Stop Recording
                </Button>
                <Button
                  variant="contained"
                  color="success"
                  onClick={handleStopAndSend}
                  disabled={duration < 1000}
                  startIcon={<SendIcon />}
                  size="large"
                >
                  Stop & Send
                </Button>
                <Button
                  variant="outlined"
                  color="secondary"
                  onClick={handleCancelRecording}
                  startIcon={<CancelIcon />}
                  size="large"
                >
                  Cancel
                </Button>
              </>
            )}

            {stoppedAudioBlob && !isProcessing && (
              <>
                <Button
                  variant="contained"
                  color="success"
                  onClick={handleSendStoppedAudio}
                  startIcon={<SendIcon />}
                  size="large"
                >
                  Send Audio ({Math.round(stoppedAudioBlob.size / 1024)}KB)
                </Button>
                <Button
                  variant="outlined"
                  color="secondary"
                  onClick={() => setStoppedAudioBlob(null)}
                  startIcon={<CancelIcon />}
                  size="large"
                >
                  Discard
                </Button>
                <Button
                  variant="outlined"
                  color="primary"
                  onClick={handleStartRecording}
                  startIcon={<MicIcon />}
                  size="large"
                >
                  Record Again
                </Button>
              </>
            )}

            {isProcessing && (
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                <CircularProgress size={24} />
                <Typography variant="body2" color="text.secondary">
                  {processingStage}
                </Typography>
              </Box>
            )}
          </Box>

          {}
          <Box sx={{ textAlign: 'center' }}>
            <Typography variant="body2" color="text.secondary">
              {isRecording ? `Recording in progress... (Volume: ${volumePercentage}%)` : 
                               stoppedAudioBlob ? `Audio ready to send (${Math.round(stoppedAudioBlob.size / 1024)}KB, ${(duration / 1000).toFixed(1)}s)` :
               isProcessing ? 'Processing your request...' :
               permission === 'denied' ? 'Microphone access denied' :
               'Ready to record'}
            </Typography>
          </Box>
        </Box>
      </Paper>

      {}
      {(lastTranscription || lastResponse) && (
        <Paper
          elevation={1}
          sx={{
            p: 3,
            mb: 3,
            backgroundColor: 'background.default',
          }}
        >
          <Typography variant="h6" gutterBottom>
            Last Interaction
          </Typography>
          
          {lastTranscription && (
            <Box sx={{ mb: 2 }}>
              <Typography variant="subtitle2" color="primary" gutterBottom>
                You said:
              </Typography>
              <Typography variant="body2" sx={{ fontStyle: 'italic' }}>
                &quot;{lastTranscription}&quot;
              </Typography>
            </Box>
          )}
          
          {lastResponse && (
            <Box>
              <Typography variant="subtitle2" color="secondary" gutterBottom>
                Assistant responded:
              </Typography>
              <Typography variant="body2">
                {lastResponse}
              </Typography>
              {currentAudioUrl && (
                <Box sx={{ mt: 2, display: 'flex', alignItems: 'center', gap: 1 }}>
                  <VolumeUpIcon fontSize="small" />
                  <audio controls style={{ height: 32 }}>
                    <source src={currentAudioUrl} type="audio/wav" />
                    Your browser does not support the audio element.
                  </audio>
                </Box>
              )}
            </Box>
          )}
        </Paper>
      )}

      {}
      {conversationHistory.length > 0 && (
        <Paper
          elevation={1}
          sx={{
            p: 3,
            backgroundColor: 'background.paper',
          }}
        >
          <Typography variant="h6" gutterBottom>
            Conversation History ({conversationHistory.length})
          </Typography>
          <Divider sx={{ mb: 2 }} />
          <Box sx={{ maxHeight: 400, overflow: 'auto' }}>
            {conversationHistory.map((entry) => (
              <Box key={entry.id} sx={{ mb: 3, p: 2, border: 1, borderColor: 'divider', borderRadius: 1 }}>
                <Typography variant="caption" color="text.secondary" gutterBottom>
                  {new Date(entry.timestamp).toLocaleString()}
                </Typography>
                
                <Box sx={{ mt: 1 }}>
                  <Typography variant="body2" color="primary" gutterBottom>
                    <strong>You:</strong> {entry.transcription}
                  </Typography>
                  <Typography variant="body2" color="secondary">
                    <strong>Assistant:</strong> {entry.llmResponse}
                  </Typography>
                </Box>
              </Box>
            ))}
          </Box>
        </Paper>
      )}

      {}
      <VoiceSettingsModal
        isOpen={isSettingsOpen}
        onClose={() => setIsSettingsOpen(false)}
        onSave={handleSettingsSave}
        initialSettings={voiceSettings}
      />
    </Box>
  );
};

VoiceChat.propTypes = {
  sessionId: PropTypes.string,
  onMessageReceived: PropTypes.func,
  onError: PropTypes.func,
  className: PropTypes.string,
};

export default VoiceChat; 
