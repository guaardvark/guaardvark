import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  Box, Paper, Typography, Button, Alert, IconButton, Chip, Divider,
  ToggleButtonGroup, ToggleButton
} from '@mui/material';
import {
  Mic as MicIcon,
  Delete as DeleteIcon,
  VolumeUp as VolumeUpIcon,
  Clear as ClearIcon,
  Hearing as HearingIcon,
  RecordVoiceOver as RecordVoiceOverIcon
} from '@mui/icons-material';
import VoiceChat from '../components/voice/VoiceChat';
import ContinuousVoiceChat from '../components/voice/ContinuousVoiceChat';
import { useAppStore } from '../stores/useAppStore';

/**
 * VoiceChatPage Component
 * Dedicated page for voice chat functionality with Standard and Listener modes
 */
const VoiceChatPage = () => {
  const { user } = useAppStore();
  const systemName = useAppStore((state) => state.systemName);
  const [sessionId, setSessionId] = useState('voice-chat-session');
  const [messages, setMessages] = useState([]);
  const [errors, setErrors] = useState([]);
  const [voiceMode, setVoiceMode] = useState('standard'); // 'standard' | 'listener'
  const continuousVoiceChatRef = useRef(null);

  // Load voice settings for wake word
  const getVoiceSettings = useCallback(() => {
    try {
      const saved = localStorage.getItem('guaardvark_voiceSettings');
      if (!saved) return {};
      const parsed = JSON.parse(saved);
      return (typeof parsed === 'object' && parsed !== null) ? parsed : {};
    } catch { return {}; }
  }, []);

  // Generate unique session ID on mount
  useEffect(() => {
    const uniqueSessionId = `voice-chat-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
    setSessionId(uniqueSessionId);
  }, []);

  // Listen for chat history cleared events
  useEffect(() => {
    const handleChatHistoryCleared = (event) => {
      console.log("VoiceChatPage: Chat history cleared event received", event.detail);
      setMessages([]);
      setErrors([]);
    };

    window.addEventListener('chatHistoryCleared', handleChatHistoryCleared);

    return () => {
      window.removeEventListener('chatHistoryCleared', handleChatHistoryCleared);
    };
  }, []);

  // Handle new voice messages
  const handleMessageReceived = (message) => {
    const newMessage = {
      id: Date.now(),
      timestamp: new Date().toISOString(),
      type: 'voice',
      ...message
    };

    setMessages(prev => [...prev, newMessage]);
    setErrors([]);
  };

  // Handle errors
  const handleError = (error) => {
    const errorMessage = {
      id: Date.now(),
      timestamp: new Date().toISOString(),
      message: error.message || 'An error occurred',
      type: 'error'
    };

    setErrors(prev => [...prev, errorMessage]);
  };

  const clearMessages = () => {
    setMessages([]);
    setErrors([]);
  };

  const clearErrors = () => {
    setErrors([]);
  };

  const handleModeChange = (event, newMode) => {
    if (newMode !== null) {
      setVoiceMode(newMode);
    }
  };

  const handleWakeWordDetected = useCallback(() => {
    console.log('VoiceChatPage: Wake word detected!');
  }, []);

  // Reactive voice settings — update when voiceSettingsChanged fires
  const [voiceSettings, setVoiceSettings] = useState(() => getVoiceSettings());

  useEffect(() => {
    const handleSettingsChanged = () => {
      setVoiceSettings(getVoiceSettings());
    };
    window.addEventListener('voiceSettingsChanged', handleSettingsChanged);
    return () => window.removeEventListener('voiceSettingsChanged', handleSettingsChanged);
  }, [getVoiceSettings]);

  return (
    <Paper
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "calc(100vh - 104px)",
        overflow: "hidden",
      }}
    >
      {/* Header */}
      <Box
        sx={{
          p: 3,
          borderBottom: 1,
          borderColor: "divider",
          backgroundColor: "background.paper",
        }}
      >
        <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between", mb: 2 }}>
          <Box sx={{ display: "flex", alignItems: "center", gap: 2 }}>
            <MicIcon sx={{ fontSize: 32, color: "primary.main" }} />
            <Box>
              <Typography variant="h4" component="h1" fontWeight="bold">
                Voice Chat
              </Typography>
              <Typography variant="body2" color="text.secondary">
                Have a natural conversation with your AI assistant using voice
              </Typography>
            </Box>
          </Box>

          <Box sx={{ display: "flex", alignItems: "center", gap: 2 }}>
            {/* Mode Toggle */}
            <ToggleButtonGroup
              value={voiceMode}
              exclusive
              onChange={handleModeChange}
              size="small"
              sx={{ height: 32 }}
            >
              <ToggleButton value="standard" sx={{ px: 2, textTransform: 'none', fontSize: '0.8rem' }}>
                <RecordVoiceOverIcon sx={{ fontSize: 16, mr: 0.5 }} />
                Standard
              </ToggleButton>
              <ToggleButton value="listener" sx={{ px: 2, textTransform: 'none', fontSize: '0.8rem' }}>
                <HearingIcon sx={{ fontSize: 16, mr: 0.5 }} />
                Listener
              </ToggleButton>
            </ToggleButtonGroup>

            <Chip
              label={`Session: ${sessionId.slice(-8)}`}
              variant="outlined"
              size="small"
            />
            {user && (
              <Chip
                label={`User: ${user.name || 'Anonymous'}`}
                variant="outlined"
                size="small"
                color="primary"
              />
            )}
          </Box>
        </Box>
      </Box>

      {/* Error Messages */}
      {errors.length > 0 && (
        <Box sx={{ p: 2, backgroundColor: "error.dark", borderBottom: 1, borderColor: "divider" }}>
          {errors.map((error) => (
            <Alert
              key={error.id}
              severity="error"
              sx={{ mb: 1 }}
              action={
                <IconButton
                  aria-label="close"
                  color="inherit"
                  size="small"
                  onClick={() => setErrors(prev => prev.filter(e => e.id !== error.id))}
                >
                  <ClearIcon fontSize="inherit" />
                </IconButton>
              }
            >
              <Typography variant="body2" fontWeight="medium">
                Voice Chat Error
              </Typography>
              <Typography variant="body2" sx={{ mt: 0.5 }}>
                {error.message}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                {new Date(error.timestamp).toLocaleString()}
              </Typography>
            </Alert>
          ))}

          {errors.length > 1 && (
            <Box sx={{ display: "flex", justifyContent: "center", mt: 1 }}>
              <Button
                onClick={clearErrors}
                color="error"
                size="small"
                variant="text"
              >
                Clear all errors
              </Button>
            </Box>
          )}
        </Box>
      )}

      {/* Main Content */}
      <Box sx={{ flex: 1, overflow: "auto", p: 3 }}>
        {/* Voice Chat Component - mode-dependent */}
        <Paper
          elevation={2}
          sx={{
            p: 3,
            mb: 3,
            backgroundColor: "background.default",
          }}
        >
          {voiceMode === 'standard' ? (
            <VoiceChat
              sessionId={sessionId}
              onMessageReceived={handleMessageReceived}
              onError={handleError}
            />
          ) : (
            <ContinuousVoiceChat
              ref={continuousVoiceChatRef}
              sessionId={sessionId}
              onMessageReceived={handleMessageReceived}
              onError={handleError}
              onWakeWordDetected={handleWakeWordDetected}
              wakeWordEnabled={voiceSettings.wakeWordEnabled || false}
              systemName={systemName || 'Guaardvark'}
              compact={false}
            />
          )}
        </Paper>

        {/* Message Summary */}
        {messages.length > 0 && (
          <Paper
            elevation={2}
            sx={{
              p: 3,
              mb: 3,
              backgroundColor: "background.paper",
            }}
          >
            <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between", mb: 2 }}>
              <Typography variant="h6" component="h2">
                Session Summary ({messages.length} messages)
              </Typography>
              <Button
                onClick={clearMessages}
                color="error"
                size="small"
                variant="outlined"
                startIcon={<DeleteIcon />}
              >
                Clear All
              </Button>
            </Box>

            <Divider sx={{ mb: 2 }} />

            <Box sx={{ maxHeight: 400, overflow: "auto" }}>
              {messages.map((message) => (
                <Box key={message.id} sx={{ mb: 3, pl: 2, borderLeft: 4, borderColor: "primary.main" }}>
                  <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between", mb: 1 }}>
                    <Typography variant="subtitle2" fontWeight="medium">
                      Voice Message
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {new Date(message.timestamp).toLocaleString()}
                    </Typography>
                  </Box>

                  <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
                    <Paper
                      variant="outlined"
                      sx={{
                        p: 2,
                        backgroundColor: "info.dark",
                        backgroundImage: 'none',
                        borderColor: "info.main",
                        color: '#fff',
                      }}
                    >
                      <Typography variant="body2" fontWeight="medium" color="info.light" gutterBottom>
                        Transcription:
                      </Typography>
                      <Typography variant="body2">
                        {message.transcription}
                      </Typography>
                    </Paper>

                    <Paper
                      variant="outlined"
                      sx={{
                        p: 2,
                        backgroundColor: "success.dark",
                        backgroundImage: 'none',
                        borderColor: "success.main",
                        color: '#fff',
                      }}
                    >
                      <Typography variant="body2" fontWeight="medium" color="success.light" gutterBottom>
                        Response:
                      </Typography>
                      <Typography variant="body2">
                        {message.response}
                      </Typography>
                    </Paper>

                    {message.audioUrl && (
                      <Box sx={{ display: "flex", alignItems: "center", gap: 1, mt: 1 }}>
                        <VolumeUpIcon fontSize="small" color="primary" />
                        <Typography variant="body2" color="text.secondary">
                          Audio:
                        </Typography>
                        <audio controls style={{ height: 32 }}>
                          <source src={message.audioUrl} type="audio/wav" />
                          Your browser does not support the audio element.
                        </audio>
                      </Box>
                    )}
                  </Box>
                </Box>
              ))}
            </Box>
          </Paper>
        )}

        {/* Usage Tips */}
        <Paper
          elevation={1}
          sx={{
            p: 3,
            backgroundColor: "info.dark",
            backgroundImage: 'none',
            borderColor: "info.main",
            color: '#fff',
          }}
        >
          <Typography variant="h6" gutterBottom color="info.light">
            Voice Chat Tips
          </Typography>
          <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" }, gap: 3 }}>
            <Box>
              <Typography variant="subtitle2" gutterBottom color="info.light">
                Standard Mode:
              </Typography>
              <Box component="ul" sx={{ m: 0, pl: 2 }}>
                <Typography component="li" variant="body2">
                  Click &quot;Start Recording&quot; to begin
                </Typography>
                <Typography component="li" variant="body2">
                  Speak clearly into your microphone
                </Typography>
                <Typography component="li" variant="body2">
                  Click &quot;Send&quot; when finished
                </Typography>
                <Typography component="li" variant="body2">
                  Use &quot;Cancel&quot; to discard recording
                </Typography>
              </Box>
            </Box>

            <Box>
              <Typography variant="subtitle2" gutterBottom color="info.light">
                Listener Mode:
              </Typography>
              <Box component="ul" sx={{ m: 0, pl: 2 }}>
                <Typography component="li" variant="body2">
                  Press Spacebar or click to toggle listening
                </Typography>
                <Typography component="li" variant="body2">
                  Speaks continuously — auto-segments on pauses
                </Typography>
                <Typography component="li" variant="body2">
                  Mic auto-mutes during AI response playback
                </Typography>
                <Typography component="li" variant="body2">
                  Enable wake word in Voice Settings for hands-free
                </Typography>
              </Box>
            </Box>

            <Box>
              <Typography variant="subtitle2" gutterBottom color="info.light">
                Settings:
              </Typography>
              <Box component="ul" sx={{ m: 0, pl: 2 }}>
                <Typography component="li" variant="body2">
                  Click settings icon to configure
                </Typography>
                <Typography component="li" variant="body2">
                  Adjust voice and quality
                </Typography>
                <Typography component="li" variant="body2">
                  Enable auto-send after silence
                </Typography>
                <Typography component="li" variant="body2">
                  Choose visualization style
                </Typography>
              </Box>
            </Box>

            <Box>
              <Typography variant="subtitle2" gutterBottom color="info.light">
                Troubleshooting:
              </Typography>
              <Box component="ul" sx={{ m: 0, pl: 2 }}>
                <Typography component="li" variant="body2">
                  Allow microphone permissions
                </Typography>
                <Typography component="li" variant="body2">
                  Check audio input levels
                </Typography>
                <Typography component="li" variant="body2">
                  Ensure stable internet connection
                </Typography>
                <Typography component="li" variant="body2">
                  Try refreshing if issues persist
                </Typography>
              </Box>
            </Box>
          </Box>
        </Paper>
      </Box>
    </Paper>
  );
};

export default VoiceChatPage;
