// frontend/src/components/voice/BackgroundWaveform.jsx
// Background waveform visualization for VoiceChat mode
// Displays audio levels for both user speech (mic) and AI speech (TTS)

import React, { useEffect, useRef, useState, useCallback } from 'react';
import PropTypes from 'prop-types';
import { Box } from '@mui/material';
import { keyframes, useTheme } from '@mui/material/styles';
import voiceService from '../../api/voiceService';

// Subtle pulse animation for bars
const pulseAnimation = keyframes`
  0%, 100% { opacity: 0.3; }
  50% { opacity: 0.6; }
`;

/**
 * BackgroundWaveform Component
 * Displays a full-width waveform visualization at the bottom of the chat page
 * Shows audio activity from both microphone (user) and TTS (AI)
 */
const BackgroundWaveform = ({
  isVoiceChatActive = false,
  isUserSpeaking = false,
  isAISpeaking = false,
  micAudioLevels = [],
  numBars = 64,
  height = 120,
  userColor: userColorProp,
  aiColor: aiColorProp,
  idleColor: idleColorProp,
  opacity = 0.4,
}) => {
  const theme = useTheme();
  const userColor = userColorProp || theme.palette.primary.main; // Blue for user
  const aiColor = aiColorProp || theme.palette.success.main; // Green for AI
  const idleColor = idleColorProp || theme.palette.text.secondary; // Gray for idle
  const [ttsAudioLevels, setTtsAudioLevels] = useState([]);
  const [ttsVolume, setTtsVolume] = useState(0);
  const [isActive, setIsActive] = useState(false);
  const animationFrameRef = useRef(null);
  const containerRef = useRef(null);
  const timeRef = useRef(0);

  // Monitor TTS audio levels and volume
  useEffect(() => {
    if (!isVoiceChatActive) {
      setTtsAudioLevels([]);
      setTtsVolume(0);
      return;
    }

    const monitorTTSAudio = () => {
      timeRef.current = Date.now() / 1000; // Update time for volume meter animation
      
      if (voiceService.getIsTTSPlaying()) {
        const levels = voiceService.getTTSAudioLevels();
        const volume = voiceService.calculateTTSVolume();
        
        if (levels && levels.length > 0) {
          // Sample the levels to match numBars
          const step = Math.max(1, Math.floor(levels.length / numBars));
          const sampledLevels = [];
          for (let i = 0; i < numBars; i++) {
            const idx = Math.min(i * step, levels.length - 1);
            sampledLevels.push(levels[idx] || 0);
          }
          setTtsAudioLevels(sampledLevels);
        } else {
          // Clear levels when not available
          setTtsAudioLevels([]);
        }
        // Always update volume for fallback visualization
        setTtsVolume(volume || 0);
      } else {
        setTtsAudioLevels([]);
        setTtsVolume(0);
      }

      if (isVoiceChatActive) {
        animationFrameRef.current = requestAnimationFrame(monitorTTSAudio);
      }
    };

    monitorTTSAudio();

    return () => {
      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current);
      }
    };
  }, [isVoiceChatActive, numBars]);

  // Determine if we should show the waveform
  useEffect(() => {
    const hasActivity = isUserSpeaking || isAISpeaking || micAudioLevels.some(l => l > 0.01) || ttsAudioLevels.some(l => l > 0.01) || ttsVolume > 0.01;
    setIsActive(isVoiceChatActive || hasActivity);
  }, [isVoiceChatActive, isUserSpeaking, isAISpeaking, micAudioLevels, ttsAudioLevels, ttsVolume]);

  // Generate volume meter levels from a single volume value
  const generateVolumeMeterLevels = useCallback((volume) => {
    const levels = [];
    const baseLevel = Math.max(0, Math.min(1, volume));
    const time = timeRef.current; // Use ref for time to avoid unnecessary recalculations
    
    // Create a waveform-like pattern with variation
    for (let i = 0; i < numBars; i++) {
      // Use sine wave pattern with time-based variation for natural animation
      const phase = (i / numBars) * Math.PI * 4 + time * 2;
      const variation = 0.4 + 0.6 * Math.abs(Math.sin(phase));
      // Add some frequency variation for more interesting pattern
      const freqVariation = 0.7 + 0.3 * Math.abs(Math.sin(phase * 0.7 + i * 0.5));
      const level = baseLevel * variation * freqVariation;
      levels.push(Math.max(0, Math.min(1, level)));
    }
    
    return levels;
  }, [numBars]);

  // Get the current audio levels to display
  const getCurrentLevels = useCallback(() => {
    // Prioritize TTS audio when AI is speaking
    if (isAISpeaking) {
      if (ttsAudioLevels.length > 0) {
        return { levels: ttsAudioLevels, source: 'ai' };
      }
      // Fallback: use volume meter when audio levels aren't available
      if (ttsVolume > 0) {
        const volumeMeterLevels = generateVolumeMeterLevels(ttsVolume);
        return { levels: volumeMeterLevels, source: 'ai' };
      }
    }
    // Use microphone audio when user is speaking
    if (isUserSpeaking && micAudioLevels.length > 0) {
      // Sample mic levels to numBars
      const step = Math.max(1, Math.floor(micAudioLevels.length / numBars));
      const sampledLevels = [];
      for (let i = 0; i < numBars; i++) {
        const idx = Math.min(i * step, micAudioLevels.length - 1);
        sampledLevels.push(micAudioLevels[idx] || 0);
      }
      return { levels: sampledLevels, source: 'user' };
    }
    // Mix both if available
    if (ttsAudioLevels.length > 0 && micAudioLevels.length > 0) {
      const mixedLevels = [];
      const micStep = Math.max(1, Math.floor(micAudioLevels.length / numBars));
      for (let i = 0; i < numBars; i++) {
        const micIdx = Math.min(i * micStep, micAudioLevels.length - 1);
        const ttsLevel = ttsAudioLevels[i] || 0;
        const micLevel = micAudioLevels[micIdx] || 0;
        mixedLevels.push(Math.max(ttsLevel, micLevel));
      }
      return { levels: mixedLevels, source: 'mixed' };
    }
    if (micAudioLevels.length > 0) {
      const step = Math.max(1, Math.floor(micAudioLevels.length / numBars));
      const sampledLevels = [];
      for (let i = 0; i < numBars; i++) {
        const idx = Math.min(i * step, micAudioLevels.length - 1);
        sampledLevels.push(micAudioLevels[idx] || 0);
      }
      return { levels: sampledLevels, source: 'user' };
    }
    if (ttsAudioLevels.length > 0) {
      return { levels: ttsAudioLevels, source: 'ai' };
    }
    // Fallback: use volume meter if TTS is playing but no levels
    if (ttsVolume > 0) {
      const volumeMeterLevels = generateVolumeMeterLevels(ttsVolume);
      return { levels: volumeMeterLevels, source: 'ai' };
    }
    // Return empty/idle levels
    return { levels: new Array(numBars).fill(0), source: 'idle' };
  }, [micAudioLevels, ttsAudioLevels, ttsVolume, isUserSpeaking, isAISpeaking, numBars, generateVolumeMeterLevels]);

  const { levels, source } = getCurrentLevels();

  // Determine color based on source
  const getBarColor = () => {
    switch (source) {
      case 'ai':
        return aiColor;
      case 'user':
        return userColor;
      case 'mixed':
        return userColor; // Default to user color for mixed
      default:
        return idleColor;
    }
  };

  if (!isActive) {
    return null;
  }

  return (
    <Box
      ref={containerRef}
      sx={{
        position: 'absolute',
        bottom: 0,
        left: 0,
        right: 0,
        height: height,
        display: 'flex',
        alignItems: 'flex-end',
        justifyContent: 'center',
        gap: '2px',
        padding: '0 16px',
        pointerEvents: 'none', // Don't block interactions
        zIndex: 0, // Behind content
        overflow: 'hidden',
        opacity: opacity,
        transition: 'opacity 0.3s ease',
      }}
    >
      {levels.map((level, index) => {
        // Use power function for better sensitivity - makes small values more visible
        // Apply square root to amplify lower values, then scale up
        const amplifiedLevel = Math.pow(Math.max(0, Math.min(1, level)), 0.6);
        const barHeight = Math.max(4, amplifiedLevel * height * 1.5);
        const hasActivity = level > 0.01;

        return (
          <Box
            key={index}
            sx={{
              width: `calc((100% - ${(numBars - 1) * 2}px) / ${numBars})`,
              minWidth: 2,
              maxWidth: 12,
              height: barHeight,
              backgroundColor: getBarColor(),
              borderRadius: '2px 2px 0 0',
              transition: 'height 50ms ease-out, background-color 0.3s ease',
              animation: !hasActivity && isVoiceChatActive ? `${pulseAnimation} 2s ease-in-out infinite` : 'none',
              animationDelay: `${index * 30}ms`,
            }}
          />
        );
      })}
    </Box>
  );
};

BackgroundWaveform.propTypes = {
  isVoiceChatActive: PropTypes.bool,
  isUserSpeaking: PropTypes.bool,
  isAISpeaking: PropTypes.bool,
  micAudioLevels: PropTypes.arrayOf(PropTypes.number),
  numBars: PropTypes.number,
  height: PropTypes.number,
  userColor: PropTypes.string,
  aiColor: PropTypes.string,
  idleColor: PropTypes.string,
  opacity: PropTypes.number,
};

export default BackgroundWaveform;
