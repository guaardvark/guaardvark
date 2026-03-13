// frontend/src/components/voice/BackgroundWaveform.jsx
// Full-window background waveform visualization for VoiceChat mode
// Renders a subtle, transparent wave effect behind all chat content

import React, { useEffect, useRef, useState, useCallback } from 'react';
import PropTypes from 'prop-types';
import { Box } from '@mui/material';
import { useTheme } from '@mui/material/styles';
import voiceService from '../../api/voiceService';

const BackgroundWaveform = ({
  isVoiceChatActive = false,
  isUserSpeaking = false,
  isAISpeaking = false,
  micAudioLevels = [],
  fullWindow = false,
  numBars = 80,
  userColor: userColorProp,
  aiColor: aiColorProp,
  idleColor: idleColorProp,
}) => {
  const theme = useTheme();
  const userColor = userColorProp || theme.palette.primary.main;
  const aiColor = aiColorProp || theme.palette.success.main;
  const idleColor = idleColorProp || theme.palette.text.disabled;
  const [ttsAudioLevels, setTtsAudioLevels] = useState([]);
  const [ttsVolume, setTtsVolume] = useState(0);
  const [isActive, setIsActive] = useState(false);
  const animationFrameRef = useRef(null);
  const canvasRef = useRef(null);
  const timeRef = useRef(0);
  const smoothedLevelsRef = useRef(new Float32Array(numBars));
  const fadeRef = useRef(0); // 0 = hidden, 1 = fully visible

  // Monitor TTS audio levels
  useEffect(() => {
    if (!isVoiceChatActive) {
      setTtsAudioLevels([]);
      setTtsVolume(0);
      return;
    }

    const monitorTTSAudio = () => {
      timeRef.current = Date.now() / 1000;

      if (voiceService.getIsTTSPlaying()) {
        const levels = voiceService.getTTSAudioLevels();
        const volume = voiceService.calculateTTSVolume();

        if (levels && levels.length > 0) {
          const step = Math.max(1, Math.floor(levels.length / numBars));
          const sampledLevels = [];
          for (let i = 0; i < numBars; i++) {
            const idx = Math.min(i * step, levels.length - 1);
            sampledLevels.push(levels[idx] || 0);
          }
          setTtsAudioLevels(sampledLevels);
        } else {
          setTtsAudioLevels([]);
        }
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
      if (animationFrameRef.current) cancelAnimationFrame(animationFrameRef.current);
    };
  }, [isVoiceChatActive, numBars]);

  // Determine activity state
  useEffect(() => {
    const hasActivity = isUserSpeaking || isAISpeaking
      || micAudioLevels.some(l => l > 0.01)
      || ttsAudioLevels.some(l => l > 0.01)
      || ttsVolume > 0.01;
    setIsActive(isVoiceChatActive || hasActivity);
  }, [isVoiceChatActive, isUserSpeaking, isAISpeaking, micAudioLevels, ttsAudioLevels, ttsVolume]);

  // Generate volume meter levels from a single volume value
  const generateVolumeMeterLevels = useCallback((volume) => {
    const levels = [];
    const baseLevel = Math.max(0, Math.min(1, volume));
    const time = timeRef.current;
    for (let i = 0; i < numBars; i++) {
      const phase = (i / numBars) * Math.PI * 4 + time * 2;
      const variation = 0.4 + 0.6 * Math.abs(Math.sin(phase));
      const freqVariation = 0.7 + 0.3 * Math.abs(Math.sin(phase * 0.7 + i * 0.5));
      levels.push(Math.max(0, Math.min(1, baseLevel * variation * freqVariation)));
    }
    return levels;
  }, [numBars]);

  // Get current audio levels
  const getCurrentLevels = useCallback(() => {
    if (isAISpeaking) {
      if (ttsAudioLevels.length > 0) return { levels: ttsAudioLevels, source: 'ai' };
      if (ttsVolume > 0) return { levels: generateVolumeMeterLevels(ttsVolume), source: 'ai' };
    }
    if (isUserSpeaking && micAudioLevels.length > 0) {
      const step = Math.max(1, Math.floor(micAudioLevels.length / numBars));
      const sampled = [];
      for (let i = 0; i < numBars; i++) {
        sampled.push(micAudioLevels[Math.min(i * step, micAudioLevels.length - 1)] || 0);
      }
      return { levels: sampled, source: 'user' };
    }
    if (micAudioLevels.length > 0) {
      const step = Math.max(1, Math.floor(micAudioLevels.length / numBars));
      const sampled = [];
      for (let i = 0; i < numBars; i++) {
        sampled.push(micAudioLevels[Math.min(i * step, micAudioLevels.length - 1)] || 0);
      }
      return { levels: sampled, source: 'user' };
    }
    if (ttsAudioLevels.length > 0) return { levels: ttsAudioLevels, source: 'ai' };
    if (ttsVolume > 0) return { levels: generateVolumeMeterLevels(ttsVolume), source: 'ai' };
    return { levels: new Array(numBars).fill(0), source: 'idle' };
  }, [micAudioLevels, ttsAudioLevels, ttsVolume, isUserSpeaking, isAISpeaking, numBars, generateVolumeMeterLevels]);

  // Canvas-based rendering for full-window mode (smoother, no DOM thrashing)
  useEffect(() => {
    if (!fullWindow) return;
    const canvas = canvasRef.current;
    if (!canvas) return;

    let running = true;

    const draw = () => {
      if (!running) return;
      const ctx = canvas.getContext('2d');
      const rect = canvas.parentElement?.getBoundingClientRect();
      if (!rect) { requestAnimationFrame(draw); return; }

      // Match canvas resolution to container
      const dpr = window.devicePixelRatio || 1;
      const w = rect.width;
      const h = rect.height;
      if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
        canvas.width = w * dpr;
        canvas.height = h * dpr;
        canvas.style.width = `${w}px`;
        canvas.style.height = `${h}px`;
        ctx.scale(dpr, dpr);
      }

      ctx.clearRect(0, 0, w, h);

      // Smoothly fade in/out
      const targetFade = isActive ? 1 : 0;
      fadeRef.current += (targetFade - fadeRef.current) * 0.08;
      if (fadeRef.current < 0.005 && !isActive) {
        fadeRef.current = 0;
        requestAnimationFrame(draw);
        return;
      }

      const { levels, source } = getCurrentLevels();
      const smoothed = smoothedLevelsRef.current;

      // Pick color
      let color;
      switch (source) {
        case 'ai': color = aiColor; break;
        case 'user': color = userColor; break;
        default: color = idleColor; break;
      }

      // Parse color for alpha compositing
      const parseColor = (c) => {
        const el = document.createElement('div');
        el.style.color = c;
        document.body.appendChild(el);
        const computed = getComputedStyle(el).color;
        document.body.removeChild(el);
        const match = computed.match(/(\d+)/g);
        return match ? match.map(Number) : [128, 128, 128];
      };

      const [r, g, b] = parseColor(color);
      const barGap = 3;
      const barWidth = Math.max(2, (w - (numBars - 1) * barGap) / numBars);
      const maxBarHeight = h * 0.85;
      const centerY = h * 0.5;

      // Idle breathing - tiny bars with slow sine movement
      const time = Date.now() / 1000;
      const hasRealActivity = levels.some(l => l > 0.02);

      for (let i = 0; i < numBars; i++) {
        const rawLevel = levels[i] || 0;

        // Idle: gentle breathing sine wave
        let idleLevel = 0;
        if (isVoiceChatActive && !hasRealActivity) {
          const phase = (i / numBars) * Math.PI * 2 + time * 0.8;
          idleLevel = 0.03 + 0.02 * Math.sin(phase);
        }

        const targetLevel = Math.max(rawLevel, idleLevel);

        // Smooth with different speeds for rise/fall
        const smoothing = targetLevel > smoothed[i] ? 0.3 : 0.1;
        smoothed[i] += (targetLevel - smoothed[i]) * smoothing;

        const amplified = Math.pow(Math.max(0, Math.min(1, smoothed[i])), 0.55);
        const barH = Math.max(2, amplified * maxBarHeight);

        // Alpha: subtle base + stronger when active
        const baseAlpha = hasRealActivity ? 0.12 : 0.06;
        const peakAlpha = hasRealActivity ? 0.35 : 0.1;
        const alpha = (baseAlpha + amplified * (peakAlpha - baseAlpha)) * fadeRef.current;

        const x = i * (barWidth + barGap);

        // Draw bar from center, extending both up and down
        const halfBar = barH / 2;
        const radius = Math.min(barWidth / 2, 3);

        ctx.beginPath();
        ctx.roundRect(x, centerY - halfBar, barWidth, barH, radius);
        ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${alpha})`;
        ctx.fill();
      }

      requestAnimationFrame(draw);
    };

    requestAnimationFrame(draw);
    return () => { running = false; };
  }, [fullWindow, isActive, isVoiceChatActive, getCurrentLevels, numBars, userColor, aiColor, idleColor]);

  if (!fullWindow) {
    // Legacy non-fullwindow mode (not used, kept for backwards compat)
    return null;
  }

  return (
    <Box
      sx={{
        position: 'absolute',
        inset: 0,
        pointerEvents: 'none',
        zIndex: 0,
        overflow: 'hidden',
      }}
    >
      <canvas
        ref={canvasRef}
        style={{
          position: 'absolute',
          inset: 0,
          width: '100%',
          height: '100%',
        }}
      />
    </Box>
  );
};

BackgroundWaveform.propTypes = {
  isVoiceChatActive: PropTypes.bool,
  isUserSpeaking: PropTypes.bool,
  isAISpeaking: PropTypes.bool,
  micAudioLevels: PropTypes.arrayOf(PropTypes.number),
  fullWindow: PropTypes.bool,
  numBars: PropTypes.number,
  userColor: PropTypes.string,
  aiColor: PropTypes.string,
  idleColor: PropTypes.string,
};

export default BackgroundWaveform;
