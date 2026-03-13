import {
  Alert,
  Box,
  IconButton,
  Tooltip,
  Typography,
  Zoom,
} from "@mui/material";
import { keyframes, styled } from "@mui/material/styles";
import PropTypes from "prop-types";
import React, { useCallback, useEffect, useRef, useState } from "react";
import voiceService from "../../api/voiceService";
import AudioVisualizer from "./AudioVisualizer";

const pulseAnimation = keyframes`
  0% { transform: scale(1); opacity: 0.8; }
  50% { transform: scale(1.1); opacity: 0.6; }
  100% { transform: scale(1); opacity: 0.8; }
`;

const waveformAnimation = keyframes`
  0%, 100% { height: 20%; }
  50% { height: 100%; }
`;

const glowAnimation = keyframes`
  0% { box-shadow: 0 0 5px rgba(25, 118, 210, 0.3); }
  50% { box-shadow: 0 0 20px rgba(25, 118, 210, 0.8); }
  100% { box-shadow: 0 0 5px rgba(25, 118, 210, 0.3); }
`;

const VoiceButtonContainer = styled(Box, {
  shouldForwardProp: (prop) => prop !== "isRecording" && prop !== "volumeLevel",
})(({ theme, isRecording, volumeLevel }) => ({
  position: "relative",
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  transition: "all 0.3s ease",
  ...(isRecording && {
    animation: `${pulseAnimation} 1s ease-in-out infinite`,
    filter: `drop-shadow(0 0 ${5 + volumeLevel * 15}px rgba(25, 118, 210, ${
      0.3 + volumeLevel * 0.7
    }))`,
  }),
}));

const SoundwaveButton = styled(IconButton, {
  shouldForwardProp: (prop) => prop !== "isRecording" && prop !== "volumeLevel",
})(({ theme, isRecording, volumeLevel }) => ({
  width: 56,
  height: 56,
  borderRadius: "50%",
  backgroundColor: isRecording
    ? theme.palette.error.main
    : theme.palette.primary.main,
  color: "white",
  transition: "all 0.3s ease",
  position: "relative",
  overflow: "hidden",

  "&:hover": {
    backgroundColor: isRecording
      ? theme.palette.error.dark
      : theme.palette.primary.dark,
    transform: "scale(1.05)",
  },

  "&:active": {
    transform: "scale(0.95)",
  },

  ...(isRecording && {
    boxShadow: `0 0 ${10 + volumeLevel * 20}px rgba(244, 67, 54, ${
      0.4 + volumeLevel * 0.6
    })`,
    animation: `${glowAnimation} 0.5s ease-in-out infinite`,
  }),
}));

const SoundwaveIcon = ({ isRecording, audioLevels = [], size = 24 }) => {
  const numBars = 5;

  const idleHeights = [0.25, 0.4, 0.3, 0.45, 0.25];

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        gap: 0.5,
        width: size,
        height: size,
      }}
    >
      {Array.from({ length: numBars }).map((_, index) => {
        const realLevel = audioLevels[index] || 0;
        const barHeight = isRecording && audioLevels.length > 0
          ? Math.max(0.15, realLevel)
          : idleHeights[index];

        return (
          <Box
            key={index}
            sx={{
              width: 2,
              backgroundColor: "currentColor",
              borderRadius: 1,
              transition: "height 40ms ease-out",
              height: `${barHeight * size}px`,
            }}
          />
        );
      })}
    </Box>
  );
};

const VolumeRings = ({ isRecording, volumeLevel }) => {
  if (!isRecording) return null;

  const rings = [
    { scale: 1.2, opacity: 0.6, delay: 0 },
    { scale: 1.4, opacity: 0.4, delay: 0.1 },
    { scale: 1.6, opacity: 0.2, delay: 0.2 },
  ];

  return (
    <>
      {rings.map((ring, index) => (
        <Box
          key={index}
          sx={{
            position: "absolute",
            top: -8,
            left: -8,
            right: -8,
            bottom: -8,
            border: "2px solid rgba(25, 118, 210, 0.3)",
            borderRadius: "50%",
            transform: `scale(${ring.scale + volumeLevel * 0.3})`,
            opacity: ring.opacity * (0.5 + volumeLevel * 0.5),
            animation: `${pulseAnimation} ${
              1.5 + ring.delay
            }s ease-in-out infinite ${ring.delay}s`,
            pointerEvents: "none",
          }}
        />
      ))}
    </>
  );
};

const VoiceChatButton = ({
  onTranscriptionReceived = () => {},
  onError = () => {},
  onStateChange = () => {},
  disabled = false,
  sessionId = "default",
  size = "medium", // 'small', 'medium', 'large'
  compact = false,
  className = "",
}) => {
  const [isRecording, setIsRecording] = useState(false);
  const [recordingVolume, setRecordingVolume] = useState(0);
  const [audioLevels, setAudioLevels] = useState([]);
  const [duration, setDuration] = useState(0);
  const [recordingError, setRecordingError] = useState(null);
  const [isProcessing, setIsProcessing] = useState(false);
  const [permission, setPermission] = useState("unknown");
  const [ttsEnabled, setTtsEnabled] = useState(false);
  const [micEnabled, setMicEnabled] = useState(true);
  const [voiceSettings, setVoiceSettings] = useState({
    enableVisualization: false,
    visualizationStyle: 'waveform',
    micEnabled: true,
    ttsEnabled: false,
    voice: 'libritts',
    recordingQuality: 'medium',
    recordingVolume: 1.0,
    autoGainControl: true,
    noiseSuppression: true,
    echoCancellation: true,
    maxRecordingDuration: 60,
    playbackVolume: 1.0,
    playbackSpeed: 1.0,
    autoSendEnabled: false,
    autoSendDelay: 2000,
    volumeSensitivity: 0.3,
    volumeThreshold: 0.1,
  });

  const startTimeRef = useRef(null);
  const durationTimerRef = useRef(null);
  const volumeMonitorRef = useRef(null);

  const buttonSizes = {
    small: { width: 40, height: 40, iconSize: 20 },
    medium: { width: 56, height: 56, iconSize: 24 },
    large: { width: 72, height: 72, iconSize: 32 },
  };

  const buttonSize = buttonSizes[size] || buttonSizes.medium;

  const handleStopRecording = useCallback(async () => {
    try {
      console.log("VoiceChatButton: Stopping recording...");

      if (isProcessing) {
        console.warn(
          "VoiceChatButton: Already processing, ignoring duplicate stop request"
        );
        return;
      }

      const now = Date.now();
      const lastProcessTime = localStorage.getItem(`voice_last_process_${sessionId}`);
      if (lastProcessTime && (now - parseInt(lastProcessTime)) < 2000) {
        console.warn("VoiceChatButton: Preventing rapid successive processing calls");
        return;
      }

      setIsRecording(false);
      setIsProcessing(true);
      localStorage.setItem(`voice_last_process_${sessionId}`, now.toString());

      const audioBlob = await voiceService.stopRecording();

      if (!audioBlob || audioBlob.size < 1000) {
        throw new Error(
          "Recording too short or empty. Please record for at least 1-2 seconds."
        );
      }

      console.log("VoiceChatButton: Processing audio blob", {
        size: audioBlob.size,
        type: audioBlob.type,
        duration: duration,
      });

      const result = await voiceService.streamVoiceChat(audioBlob, sessionId);

      console.log("VoiceChatButton: Voice API response:", result);

      if (!result || typeof result !== 'object') {
        throw new Error("Invalid API response format. Please try again.");
      }

      const transcription =
        result.transcribed_text || result.transcript || result.text;
      const llmResponse = result.llm_response || result.response;

      if (!transcription || typeof transcription !== 'string') {
        throw new Error("No transcription received from API. Please try speaking again.");
      }

      console.log("VOICECHATBUTTON: Extracted from API response:", {
        hasTranscription: !!transcription,
        transcriptionLength: transcription?.length || 0,
        hasLlmResponse: !!llmResponse,
        llmResponseLength: llmResponse?.length || 0,
        transcriptionPreview: transcription
          ? transcription.substring(0, 50) + "..."
          : null,
        llmResponsePreview: llmResponse
          ? llmResponse.substring(0, 100) + "..."
          : null,
      });

      if (transcription && transcription.trim()) {
        console.log("VoiceChatButton: Transcription received:", transcription);

        if (onTranscriptionReceived) {
          console.log("VOICECHATBUTTON: Sending to ChatInput:", {
            userMessage: transcription.trim().substring(0, 50) + "...",
            hasAiResponse: !!(llmResponse || null),
            aiResponseLength: (llmResponse || "").length,
            isVoiceStream: true,
          });

          const messageKey = `voice:${transcription.trim()}`;
          const recentKey = `recent_voice_messages_${sessionId}`;
          
          try {
            const recentData = localStorage.getItem(recentKey);
            const recentMessages = recentData ? JSON.parse(recentData) : {};
            
            if (recentMessages[messageKey] && (now - recentMessages[messageKey]) < 5000) {
              console.warn("VoiceChatButton: Preventing duplicate voice message send");
              return;
            }
            
            recentMessages[messageKey] = now;
            
            Object.keys(recentMessages).forEach(key => {
              if (now - recentMessages[key] > 10000) {
                delete recentMessages[key];
              }
            });
            
            localStorage.setItem(recentKey, JSON.stringify(recentMessages));
          } catch (e) {
            console.warn("Failed to track voice message:", e);
          }

          onTranscriptionReceived({
            userMessage: transcription.trim(),
            aiResponse: llmResponse || null,
            isVoiceStream: true,
          });
        }

        if (llmResponse && llmResponse.trim()) {
          console.log(
            "VoiceChatButton: Playing TTS for AI response:",
            llmResponse
          );
          console.log(
            "VoiceChatButton: TTS will be handled by parent component"
          );
        }
      } else {
        throw new Error(
          "No speech detected in audio. Please try speaking louder or closer to the microphone."
        );
      }
    } catch (error) {
      console.error("VoiceChatButton: Error processing recording:", error);
      
      let errorMessage = "Failed to process recording";
      if (error.message) {
        errorMessage = error.message;
      } else if (error instanceof TypeError && error.message.includes('fetch')) {
        errorMessage = "Network error. Please check your connection and try again.";
      } else if (error.name === 'AbortError') {
        errorMessage = "Request was cancelled. Please try again.";
      } else if (error.response) {
        const status = error.response.status;
        if (status === 400) {
          errorMessage = "Invalid audio format. Please try recording again.";
        } else if (status === 413) {
          errorMessage = "Audio file too large. Please record a shorter message.";
        } else if (status === 500) {
          errorMessage = "Server error. Please try again in a moment.";
        } else {
          errorMessage = `Server error (${status}). Please try again.`;
        }
      }
      
      setRecordingError(errorMessage);
      if (onError) {
        const enhancedError = new Error(errorMessage);
        enhancedError.originalError = error;
        onError(enhancedError);
      }
    } finally {
      setIsProcessing(false);
    }
  }, [duration, sessionId, onTranscriptionReceived, onError, isProcessing]);

  useEffect(() => {
    const checkPermission = async () => {
      try {
        const permissionState = await voiceService.checkMicrophonePermission();
        setPermission(permissionState);
      } catch (err) {
        console.error("Failed to check microphone permission:", err);
        setPermission("denied");
      }
    };
    checkPermission();
  }, []);

  useEffect(() => {
    const loadVoiceSettings = () => {
      try {
        const stored = localStorage.getItem("guaardvark_voiceSettings");
        if (stored) {
          const parsed = JSON.parse(stored);
          setTtsEnabled(parsed.ttsEnabled !== false);
          setMicEnabled(parsed.micEnabled !== false);
          setVoiceSettings(prev => ({
            ...prev,
            enableVisualization: parsed.enableVisualization ?? false,
            visualizationStyle: parsed.visualizationStyle || 'waveform',
            micEnabled: parsed.micEnabled !== false,
            ttsEnabled: parsed.ttsEnabled !== false,
            voice: parsed.voice || 'libritts',
            recordingQuality: parsed.recordingQuality || 'medium',
            recordingVolume: parsed.recordingVolume ?? 1.0,
            autoGainControl: parsed.autoGainControl !== false,
            noiseSuppression: parsed.noiseSuppression !== false,
            echoCancellation: parsed.echoCancellation !== false,
            maxRecordingDuration: parsed.maxRecordingDuration || 60,
            playbackVolume: parsed.playbackVolume ?? 1.0,
            playbackSpeed: parsed.playbackSpeed ?? 1.0,
            autoSendEnabled: parsed.autoSendEnabled === true,
            autoSendDelay: parsed.autoSendDelay || 2000,
            volumeSensitivity: parsed.volumeSensitivity ?? 0.3,
            volumeThreshold: parsed.volumeThreshold ?? 0.1,
          }));
        } else {
          setMicEnabled(true);
          setTtsEnabled(false);
        }
      } catch (error) {
        console.warn("VoiceChatButton: Failed to load voice settings:", error);
        setMicEnabled(true);
        setTtsEnabled(false);
      }
    };

    loadVoiceSettings();

    const handleStorageChange = (e) => {
      if (e.key === "guaardvark_voiceSettings" || !e.key) {
        loadVoiceSettings();
      }
    };

    window.addEventListener("storage", handleStorageChange);
    
    window.addEventListener("voiceSettingsChanged", loadVoiceSettings);
    
    return () => {
      window.removeEventListener("storage", handleStorageChange);
      window.removeEventListener("voiceSettingsChanged", loadVoiceSettings);
    };
  }, []);

  useEffect(() => {
    onStateChange({
      isRecording,
      volume: recordingVolume,
      audioLevels,
      speechDetected: recordingVolume > 0.1,
      isProcessing,
    });
  }, [isRecording, recordingVolume, audioLevels, isProcessing, onStateChange]);

  useEffect(() => {
    if (isRecording) {
      volumeMonitorRef.current = setInterval(() => {
        try {
          const volume = voiceService.calculateVolume();
          setRecordingVolume(volume || 0);

          const levels = voiceService.getAudioLevels();
          if (levels && levels.length > 0) {
            const numBars = 5;
            const sampledLevels = [];
            for (let i = 0; i < numBars; i++) {
              const startIdx = Math.floor((i / numBars) * levels.length);
              const endIdx = Math.floor(((i + 1) / numBars) * levels.length);
              let sum = 0;
              let count = 0;
              for (let j = startIdx; j < endIdx && j < levels.length; j++) {
                sum += levels[j];
                count++;
              }
              sampledLevels.push(count > 0 ? sum / count : 0);
            }
            setAudioLevels(sampledLevels);
          } else {
            setAudioLevels([]);
          }
        } catch (err) {
          console.warn("VoiceChatButton: Error getting volume/levels:", err);
          setRecordingVolume(0);
          setAudioLevels([]);
        }
      }, 50);
    } else {
      if (volumeMonitorRef.current) {
        clearInterval(volumeMonitorRef.current);
        volumeMonitorRef.current = null;
      }
      setRecordingVolume(0);
      setAudioLevels([]);
    }

    return () => {
      if (volumeMonitorRef.current) {
        clearInterval(volumeMonitorRef.current);
      }
    };
  }, [isRecording]);

  useEffect(() => {
    if (isRecording) {
      startTimeRef.current = Date.now();
      durationTimerRef.current = setInterval(() => {
        const elapsed = Date.now() - startTimeRef.current;
        setDuration(elapsed);

        if (elapsed >= 60000 && !isProcessing) {
          console.log("VoiceChatButton: Auto-stopping recording at 60 seconds");
          handleStopRecording();
        }
      }, 100);
    } else {
      if (durationTimerRef.current) {
        clearInterval(durationTimerRef.current);
        durationTimerRef.current = null;
      }
      setDuration(0);
    }

    return () => {
      if (durationTimerRef.current) {
        clearInterval(durationTimerRef.current);
        durationTimerRef.current = null;
      }
    };
  }, [isRecording, isProcessing, handleStopRecording]);

  const handleStartRecording = useCallback(async () => {
    try {
      console.log("VoiceChatButton: Starting recording...");
      setRecordingError(null);
      setIsProcessing(false);

      const resumed = await voiceService.resumeAudioContext();
      if (!resumed) {
        throw new Error("Failed to initialize audio. Please check your microphone permissions.");
      }

      await voiceService.startRecording();
      setIsRecording(true);
      startTimeRef.current = Date.now();

      console.log("VoiceChatButton: Recording started successfully");
    } catch (error) {
      console.error("VoiceChatButton: Failed to start recording:", error);
      
      let errorMessage = "Failed to start recording";
      if (error.name === 'NotAllowedError' || error.name === 'PermissionDeniedError') {
        errorMessage = "Microphone permission denied. Please enable microphone access in your browser settings.";
      } else if (error.name === 'NotFoundError' || error.name === 'DevicesNotFoundError') {
        errorMessage = "No microphone found. Please connect a microphone and try again.";
      } else if (error.name === 'NotReadableError' || error.name === 'TrackStartError') {
        errorMessage = "Microphone is being used by another application. Please close other apps and try again.";
      } else if (error.message) {
        errorMessage = error.message;
      }
      
      setRecordingError(errorMessage);
      setPermission("denied");
      if (onError) {
        const enhancedError = new Error(errorMessage);
        enhancedError.originalError = error;
        onError(enhancedError);
      }
    }
  }, [onError]);

  const handleClick = useCallback(() => {
    if (disabled || isProcessing || !micEnabled || permission === "denied") return;

    if (isRecording) {
      handleStopRecording();
    } else {
      handleStartRecording();
    }
  }, [
    disabled,
    isProcessing,
    micEnabled,
    permission,
    isRecording,
    handleStartRecording,
    handleStopRecording,
  ]);

  const formatDuration = (ms) => {
    const seconds = Math.floor(ms / 1000);
    return `${seconds}s`;
  };

  const getTooltip = () => {
    if (!micEnabled) return "Voice input disabled in Settings";
    if (disabled) return "Voice chat disabled";
    if (isProcessing) return "Processing...";
    if (isRecording)
      return `Recording... (${formatDuration(duration)}) - Click to stop`;
    if (permission === "denied") return "Microphone permission denied";
    return "Click to start voice recording";
  };

  // --- COMPACT: circular waveform button matching ContinuousVoiceChat style ---
  if (compact) {
    const btnSize = 40;
    const numBars = 7;
    const idleHeights = [0.22, 0.38, 0.28, 0.45, 0.28, 0.38, 0.22];

    return (
      <Tooltip title={isProcessing ? 'Processing...' : isRecording ? 'Stop recording' : 'Push to talk'}>
        <IconButton
          onClick={handleClick}
          disabled={disabled || (isProcessing && !isRecording) || permission === 'denied' || !micEnabled}
          sx={{
            width: btnSize,
            height: btnSize,
            borderRadius: '50%',
            backgroundColor: isProcessing
              ? '#7c4dff'
              : isRecording
                ? '#ff1744'
                : 'transparent',
            color: isRecording || isProcessing ? '#fff' : 'text.primary',
            border: isRecording || isProcessing ? '2px solid transparent' : 'none',
            transition: 'all 0.2s ease',
            boxShadow: isProcessing
              ? '0 0 16px rgba(124,77,255,0.6), 0 0 4px rgba(124,77,255,0.3) inset'
              : isRecording
                ? `0 0 ${10 + recordingVolume * 14}px rgba(255,23,68,0.6)`
                : 'none',
            animation: isProcessing
              ? `${pulseAnimation} 1.5s ease-in-out infinite`
              : 'none',
            p: 0,
            '&:hover': {
              backgroundColor: isProcessing
                ? '#651fff'
                : isRecording
                  ? '#d50000'
                  : 'action.hover',
            },
          }}
        >
          <Box sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '2.5px',
            width: btnSize * 0.6,
            height: btnSize * 0.6,
          }}>
            {Array.from({ length: numBars }).map((_, i) => {
              const levelIndex = Math.floor((i / numBars) * (audioLevels.length || 1));
              const realLevel = audioLevels[levelIndex] || 0;
              const barHeight = isRecording && !isProcessing
                ? Math.max(0.15, Math.min(1, realLevel * 2.0))
                : idleHeights[i];
              return (
                <Box
                  key={i}
                  sx={{
                    width: 3,
                    borderRadius: 2,
                    backgroundColor: 'currentColor',
                    transition: 'height 50ms ease-out',
                    height: isProcessing ? '40%' : `${barHeight * btnSize * 0.55}px`,
                    minHeight: 3,
                    opacity: 0.95,
                    ...(isProcessing && {
                      animation: `${waveformAnimation} ${0.6 + i * 0.12}s ease-in-out infinite`,
                      animationDelay: `${i * 0.08}s`,
                    }),
                  }}
                />
              );
            })}
          </Box>
        </IconButton>
      </Tooltip>
    );
  }

  return (
    <Box
      className={className}
      sx={{
        position: "relative",
        display: "inline-flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 1,
      }}
    >
      {}
      {recordingError && (
        <Zoom in={true}>
          <Alert
            severity="error"
            sx={{
              position: "absolute",
              top: -60,
              minWidth: 200,
              fontSize: "0.75rem",
              zIndex: 1000,
            }}
            onClose={() => setRecordingError(null)}
          >
            {recordingError}
          </Alert>
        </Zoom>
      )}

      {}
      {voiceSettings.enableVisualization && isRecording && (
        <Box sx={{ width: '100%', maxWidth: 300, mb: 1 }}>
          <AudioVisualizer
            audioLevels={audioLevels}
            volume={recordingVolume}
            isRecording={isRecording}
            width={300}
            height={80}
            style={voiceSettings.visualizationStyle}
          />
        </Box>
      )}

      {}
      <VoiceButtonContainer
        isRecording={isRecording}
        volumeLevel={recordingVolume}
      >
        <VolumeRings isRecording={isRecording} volumeLevel={recordingVolume} />

        <Tooltip title={getTooltip()} placement="top">
          <span>
            <SoundwaveButton
              onClick={handleClick}
              disabled={
                disabled ||
                isProcessing ||
                permission === "denied" ||
                !micEnabled
              }
              isRecording={isRecording}
              volumeLevel={recordingVolume}
              aria-label={
                isRecording ? "Stop voice recording" : "Start voice recording"
              }
              aria-pressed={isRecording}
              aria-disabled={
                disabled ||
                isProcessing ||
                permission === "denied" ||
                !micEnabled
              }
              role="button"
              sx={{
                width: buttonSize.width,
                height: buttonSize.height,
              }}
            >
              <SoundwaveIcon
                isRecording={isRecording}
                audioLevels={audioLevels}
                size={buttonSize.iconSize}
              />
            </SoundwaveButton>
          </span>
        </Tooltip>
      </VoiceButtonContainer>

      {}
      {isRecording && (
        <Typography
          variant="caption"
          color="error"
          sx={{
            fontWeight: "bold",
            animation: `${pulseAnimation} 1s ease-in-out infinite`,
          }}
        >
          {formatDuration(duration)}
        </Typography>
      )}

      {}
      {isProcessing && (
        <Typography
          variant="caption"
          color="primary"
          sx={{ fontWeight: "bold" }}
        >
          Processing...
        </Typography>
      )}
    </Box>
  );
};

VoiceChatButton.propTypes = {
  sessionId: PropTypes.string,
  onTranscriptionReceived: PropTypes.func,
  onError: PropTypes.func,
  onStateChange: PropTypes.func,
  disabled: PropTypes.bool,
  size: PropTypes.oneOf(["small", "medium", "large"]),
  compact: PropTypes.bool,
  className: PropTypes.string,
};

export default VoiceChatButton;
