import React, { useState, useEffect, useRef, useCallback } from 'react';
import PropTypes from 'prop-types';
import {
  Box,
  IconButton,
  Chip,
  CircularProgress,
  Tooltip,
  Typography,
  Alert,
  Paper
} from '@mui/material';
import {
  Mic as MicIcon,
  MicOff as MicOffIcon,
  Hearing as HearingIcon
} from '@mui/icons-material';
import { keyframes } from '@mui/material/styles';
import voiceService from '../../api/voiceService';
import { checkForWakeWord } from '../../utils/wakeWordMatcher';
import CanvasWaveform from './CanvasWaveform';

const pulseAnimation = keyframes`
  0% { transform: scale(1); opacity: 1; }
  50% { transform: scale(1.1); opacity: 0.8; }
  100% { transform: scale(1); opacity: 1; }
`;

const _waveformAnimation = keyframes`
  0%, 100% { height: 20%; }
  50% { height: 100%; }
`;

// Strip markdown/formatting for clean TTS input (from VoiceChat.jsx pattern)
const _cleanTextForTTS = (text) => {
  if (!text) return '';
  return text
    .replace(/\*{1,3}([^*]+)\*{1,3}/g, '$1')
    .replace(/_{1,3}([^_]+)_{1,3}/g, '$1')
    .replace(/#{1,6}\s*([^\n]+)/g, '$1')
    .replace(/```[^`]*```/g, '')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/<[^>]*>/g, '')
    .replace(/\s+/g, ' ')
    .replace(/[•\-*]\s*/g, '')
    .replace(/\n\s*\n/g, '. ')
    .replace(/\n/g, ', ')
    .replace(/\s*\.\s*\.\s*\./g, '. ')
    .replace(/[!]{2,}/g, '!')
    .replace(/[?]{2,}/g, '?')
    .trim()
    .replace(/([^.!?])\s*$/, '$1.');
};

/**
 * Synthesize a short aardvark-like grunt/snort using Web Audio API.
 * Low-frequency sawtooth sweep (180→120Hz) + noise burst through bandpass filter.
 * ~0.2s total duration. Non-critical — silently fails.
 */
const playGuaardvarkGrunt = () => {
  try {
    const ctx = voiceService.getAudioContext();
    if (!ctx) return;

    const now = ctx.currentTime;

    // Oscillator: sawtooth wave with frequency sweep for "grunt" character
    const osc = ctx.createOscillator();
    osc.type = 'sawtooth';
    osc.frequency.setValueAtTime(180, now);
    osc.frequency.linearRampToValueAtTime(120, now + 0.15);

    // Bandpass filter centered at 200Hz for warmth
    const bandpass = ctx.createBiquadFilter();
    bandpass.type = 'bandpass';
    bandpass.frequency.setValueAtTime(200, now);
    bandpass.Q.setValueAtTime(2, now);

    // Gain envelope: quick attack, short sustain, fast decay
    const oscGain = ctx.createGain();
    oscGain.gain.setValueAtTime(0, now);
    oscGain.gain.linearRampToValueAtTime(0.12, now + 0.02);
    oscGain.gain.setValueAtTime(0.12, now + 0.08);
    oscGain.gain.exponentialRampToValueAtTime(0.001, now + 0.2);

    osc.connect(bandpass);
    bandpass.connect(oscGain);
    oscGain.connect(ctx.destination);

    // Noise burst for "snort" texture
    const bufferSize = Math.floor(ctx.sampleRate * 0.05);
    const noiseBuffer = ctx.createBuffer(1, bufferSize, ctx.sampleRate);
    const noiseData = noiseBuffer.getChannelData(0);
    for (let i = 0; i < bufferSize; i++) {
      noiseData[i] = (Math.random() * 2 - 1) * 0.3;
    }

    const noiseSrc = ctx.createBufferSource();
    noiseSrc.buffer = noiseBuffer;

    const noiseFilter = ctx.createBiquadFilter();
    noiseFilter.type = 'bandpass';
    noiseFilter.frequency.setValueAtTime(250, now);
    noiseFilter.Q.setValueAtTime(1.5, now);

    const noiseGain = ctx.createGain();
    noiseGain.gain.setValueAtTime(0, now + 0.05);
    noiseGain.gain.linearRampToValueAtTime(0.08, now + 0.07);
    noiseGain.gain.exponentialRampToValueAtTime(0.001, now + 0.15);

    noiseSrc.connect(noiseFilter);
    noiseFilter.connect(noiseGain);
    noiseGain.connect(ctx.destination);

    osc.start(now);
    osc.stop(now + 0.2);
    noiseSrc.start(now + 0.05);
    noiseSrc.stop(now + 0.15);
  } catch (e) {
    // Non-critical — silently fail
  }
};

const ContinuousVoiceChat = React.forwardRef(({
  sessionId = 'default',
  onMessageReceived = () => {},
  onError = () => {},
  onStateChange = () => {},
  compact = true,
  // Wake word props
  wakeWordEnabled = false,
  systemName = 'Guaardvark',  // Wake word is always "Hey Guaardvark"
  onWakeWordDetected = () => {},
}, ref) => {
  // --- Voice settings loader ---
  const getVoiceSettings = useCallback(() => {
    try {
      const saved = localStorage.getItem('guaardvark_voiceSettings');
      if (!saved) return {};

      const parsed = JSON.parse(saved);
      if (typeof parsed !== 'object' || parsed === null) {
        console.warn('ContinuousVoiceChat: Invalid voice settings format, using defaults');
        return {};
      }
      return parsed;
    } catch (error) {
      console.error('ContinuousVoiceChat: Error loading voice settings:', error);
      return {};
    }
  }, []);

  // --- [Bug 4 fix] VAD config as ref, rebuilt on settings change ---
  const buildVadConfig = useCallback(() => {
    const settings = getVoiceSettings();
    return {
      energyThreshold: settings.silenceThreshold || 0.02,
      hysteresisRatio: 0.5,
      silenceConfirmationFrames: 2,
      speechConfirmationFrames: 2,
      thresholdDecayTimeout: 5000,
      minAdaptiveThreshold: 0.008,
      minSpeechDuration: 500,
      silenceDuration: settings.silenceTimeout || 800,
      maxSegmentDuration: settings.maxSegmentDuration || 10000,
      minPauseBetweenSegments: 1000,
      smoothingWindow: 5,
      adaptiveRate: 0.05,
      minChunkSize: 1000,
      maxChunkSize: 2000000,
      validateAudio: false,
      maxConsecutiveErrors: 5,
    };
  }, [getVoiceSettings]);

  const vadConfigRef = useRef(null);
  if (!vadConfigRef.current) {
    vadConfigRef.current = buildVadConfig();
  }

  // --- [Bug 1 fix] Playback settings as refs ---
  const voiceNameRef = useRef('libritts');
  const playbackVolumeRef = useRef(1.0);
  const playbackSpeedRef = useRef(1.0);

  // Initialize playback refs from settings
  useEffect(() => {
    const settings = getVoiceSettings();
    voiceNameRef.current = settings.voice || 'libritts';
    playbackVolumeRef.current = settings.playbackVolume ?? 1.0;
    playbackSpeedRef.current = settings.playbackSpeed ?? 1.0;
  }, [getVoiceSettings]);

  // --- [Bug 4 fix] Listen for settings changes ---
  useEffect(() => {
    const handleSettingsChanged = () => {
      vadConfigRef.current = buildVadConfig();
      adaptiveThresholdRef.current = vadConfigRef.current.energyThreshold;
      const settings = getVoiceSettings();
      voiceNameRef.current = settings.voice || 'libritts';
      playbackVolumeRef.current = settings.playbackVolume ?? 1.0;
      playbackSpeedRef.current = settings.playbackSpeed ?? 1.0;
      // Update active listening duration from settings
      activeListeningDurationRef.current = settings.activeListeningDuration || 30000;
      console.log('ContinuousVoiceChat: VAD + playback settings updated from settings change');
    };

    window.addEventListener('voiceSettingsChanged', handleSettingsChanged);
    return () => {
      window.removeEventListener('voiceSettingsChanged', handleSettingsChanged);
    };
  }, [buildVadConfig, getVoiceSettings]);

  // --- State ---
  const [isListening, setIsListening] = useState(false);
  const [isProcessing, _setIsProcessing] = useState(false);
  const [currentVolume, setCurrentVolume] = useState(0);
  const [speechDetected, setSpeechDetected] = useState(false);
  const [segmentCount, setSegmentCount] = useState(0);
  const [error, setError] = useState(null);
  const [processingQueue, setProcessingQueue] = useState(0);
  const [audioLevels, setAudioLevels] = useState(new Array(20).fill(0));
  const [keyboardShortcutEnabled, _setKeyboardShortcutEnabled] = useState(true);
  const [waveformActive, setWaveformActive] = useState(false);

  const [isMicMuted, setIsMicMuted] = useState(false);
  const [isAISpeaking, _setIsAISpeaking] = useState(false);

  // --- Wake word state ---
  const [listeningMode, setListeningMode] = useState('active'); // 'passive' | 'active'
  const listeningModeRef = useRef('active');
  const activeListeningTimeoutRef = useRef(null);
  const activeListeningDurationRef = useRef(30000);
  // Wake word is always "Guaardvark" regardless of systemName prop
  const systemNameRef = useRef('Guaardvark');

  // Initialize active listening duration from settings
  useEffect(() => {
    const settings = getVoiceSettings();
    activeListeningDurationRef.current = settings.activeListeningDuration || 30000;
  }, [getVoiceSettings]);

  // Set initial listening mode based on wakeWordEnabled
  useEffect(() => {
    console.log('ContinuousVoiceChat: wakeWordEnabled changed to:', wakeWordEnabled, 'systemName:', systemName);
    if (wakeWordEnabled) {
      listeningModeRef.current = 'passive';
      setListeningMode('passive');
    } else {
      listeningModeRef.current = 'active';
      setListeningMode('active');
    }
  }, [wakeWordEnabled, systemName]);

  // --- [Bug 5 fix] consecutiveErrors as ref + display state ---
  const consecutiveErrorsRef = useRef(0);
  const [consecutiveErrorDisplay, setConsecutiveErrorDisplay] = useState(0);

  const incrementErrors = useCallback(() => {
    consecutiveErrorsRef.current += 1;
    setConsecutiveErrorDisplay(consecutiveErrorsRef.current);
  }, []);

  const resetErrors = useCallback(() => {
    consecutiveErrorsRef.current = 0;
    setConsecutiveErrorDisplay(0);
  }, []);

  // --- Refs ---
  const mediaRecorderRef = useRef(null);
  const audioChunksRef = useRef([]);
  const currentSegmentChunksRef = useRef([]);
  const streamRef = useRef(null);

  const volumeHistoryRef = useRef([]);
  const speechStartTimeRef = useRef(null);
  const lastSpeechTimeRef = useRef(null);
  const lastSegmentTimeRef = useRef(null);
  const adaptiveThresholdRef = useRef(0.05);

  const consecutiveSilenceFramesRef = useRef(0);
  const consecutiveSpeechFramesRef = useRef(0);
  const lastThresholdResetTimeRef = useRef(Date.now());
  const confirmedSpeakingRef = useRef(false);

  const volumeMonitorRef = useRef(null);
  const vadTimerRef = useRef(null);

  const isMountedRef = useRef(true);
  const processingCountRef = useRef(0);

  // --- [Bug 2 fix] Serial processing queue ---
  const audioQueueRef = useRef([]);
  const isProcessingQueueRef = useRef(false);

  // Initialize adaptive threshold from config
  useEffect(() => {
    adaptiveThresholdRef.current = vadConfigRef.current.energyThreshold;
  }, []);

  // --- Wake word: activate/deactivate listening ---
  const activateListening = useCallback(() => {
    listeningModeRef.current = 'active';
    setListeningMode('active');
    playGuaardvarkGrunt();
    onWakeWordDetected();

    // Start active listening timeout
    if (activeListeningTimeoutRef.current) {
      clearTimeout(activeListeningTimeoutRef.current);
    }
    activeListeningTimeoutRef.current = setTimeout(() => {
      console.log('ContinuousVoiceChat: Active listening timeout, returning to passive');
      listeningModeRef.current = 'passive';
      setListeningMode('passive');
    }, activeListeningDurationRef.current);

    console.log('ContinuousVoiceChat: Wake word detected, active listening mode');
  }, [onWakeWordDetected]);

  const resetActiveListeningTimeout = useCallback(() => {
    if (!wakeWordEnabled) return;
    if (activeListeningTimeoutRef.current) {
      clearTimeout(activeListeningTimeoutRef.current);
    }
    activeListeningTimeoutRef.current = setTimeout(() => {
      console.log('ContinuousVoiceChat: Active listening timeout, returning to passive');
      listeningModeRef.current = 'passive';
      setListeningMode('passive');
    }, activeListeningDurationRef.current);
  }, [wakeWordEnabled]);

  // Cleanup active listening timeout on unmount
  useEffect(() => {
    return () => {
      if (activeListeningTimeoutRef.current) {
        clearTimeout(activeListeningTimeoutRef.current);
      }
    };
  }, []);

  // --- Process text directly as chat (for wake word remainder) ---
  // Routes through the same unified chat pipeline as active mode
  // instead of calling the old /enhanced-chat REST endpoint.
  const processTextAsChat = useCallback(async (text) => {
    try {
      // Send through normal chat pipeline (response: null = use streaming)
      onMessageReceived({
        transcription: text,
        response: null,
      });

      setSegmentCount(prev => prev + 1);
      setIsMicMuted(false);

      if (wakeWordEnabled) {
        resetActiveListeningTimeout();
      }
    } catch (err) {
      console.error('ContinuousVoiceChat: processTextAsChat error:', err);
      setIsMicMuted(false);
    }
  }, [onMessageReceived, wakeWordEnabled, resetActiveListeningTimeout]);

  // --- VAD Detection ---
  const detectVoiceActivity = useCallback((volume) => {
    const vad = vadConfigRef.current;

    if (isMicMuted || isAISpeaking) {
      return {
        isSpeaking: false,
        smoothedVolume: 0,
        threshold: adaptiveThresholdRef.current
      };
    }

    const now = Date.now();

    volumeHistoryRef.current.push(volume);
    if (volumeHistoryRef.current.length > vad.smoothingWindow) {
      volumeHistoryRef.current.shift();
    }

    const smoothedVolume = volumeHistoryRef.current.reduce((a, b) => a + b, 0) /
                           volumeHistoryRef.current.length;

    const speechStartThreshold = adaptiveThresholdRef.current;
    const silenceConfirmThreshold = adaptiveThresholdRef.current * vad.hysteresisRatio;

    if (smoothedVolume > speechStartThreshold) {
      consecutiveSpeechFramesRef.current++;
      consecutiveSilenceFramesRef.current = 0;
    } else if (smoothedVolume < silenceConfirmThreshold) {
      consecutiveSilenceFramesRef.current++;
      consecutiveSpeechFramesRef.current = 0;
    }

    let isSpeaking = confirmedSpeakingRef.current;

    if (!confirmedSpeakingRef.current) {
      if (consecutiveSpeechFramesRef.current >= vad.speechConfirmationFrames) {
        confirmedSpeakingRef.current = true;
        isSpeaking = true;
        // Talk-over interruption: if AI is speaking, silence it immediately
        if (voiceService.getIsTTSPlaying()) {
          console.log('ContinuousVoiceChat: Interrupting AI playback (user started speaking)');
          voiceService.stopPlayback();
        }
        console.log('ContinuousVoiceChat: Speech confirmed (hysteresis start)', {
          smoothedVolume: smoothedVolume.toFixed(4),
          speechThreshold: speechStartThreshold.toFixed(4),
          consecutiveFrames: consecutiveSpeechFramesRef.current
        });
      }
    } else {
      if (consecutiveSilenceFramesRef.current >= vad.silenceConfirmationFrames) {
        confirmedSpeakingRef.current = false;
        isSpeaking = false;
        console.log('ContinuousVoiceChat: Silence confirmed (hysteresis end)', {
          smoothedVolume: smoothedVolume.toFixed(4),
          silenceThreshold: silenceConfirmThreshold.toFixed(4),
          consecutiveFrames: consecutiveSilenceFramesRef.current
        });
      }
    }

    if (!isSpeaking) {
      adaptiveThresholdRef.current = Math.max(
        vad.minAdaptiveThreshold,
        adaptiveThresholdRef.current - (adaptiveThresholdRef.current * vad.adaptiveRate * 0.5)
      );

      if (now - lastThresholdResetTimeRef.current > vad.thresholdDecayTimeout) {
        if (adaptiveThresholdRef.current < vad.energyThreshold * 0.9) {
          console.log('ContinuousVoiceChat: Resetting adaptive threshold after prolonged silence');
          adaptiveThresholdRef.current = vad.energyThreshold;
        }
        lastThresholdResetTimeRef.current = now;
      }
    } else if (smoothedVolume > adaptiveThresholdRef.current * 2) {
      adaptiveThresholdRef.current = Math.min(
        vad.energyThreshold * 1.5,
        adaptiveThresholdRef.current + (adaptiveThresholdRef.current * vad.adaptiveRate * 0.3)
      );
      lastThresholdResetTimeRef.current = now;
    }

    if (isSpeaking) {
      if (!speechStartTimeRef.current) {
        speechStartTimeRef.current = now;
        console.log('ContinuousVoiceChat: Speech started');
      }
      lastSpeechTimeRef.current = now;

      const speechDuration = now - speechStartTimeRef.current;
      if (speechDuration >= vad.minSpeechDuration) {
        setSpeechDetected(true);
      }
    } else {
      if (speechStartTimeRef.current && lastSpeechTimeRef.current) {
        const silenceDuration = now - lastSpeechTimeRef.current;
        const speechDuration = lastSpeechTimeRef.current - speechStartTimeRef.current;

        const shouldSegment =
          speechDuration >= vad.minSpeechDuration &&
          silenceDuration >= vad.silenceDuration &&
          (!lastSegmentTimeRef.current ||
           (now - lastSegmentTimeRef.current) >= vad.minPauseBetweenSegments) &&
          currentSegmentChunksRef.current.length > 0;

        if (shouldSegment) {
          console.log('ContinuousVoiceChat: Natural pause detected, segmenting...', {
            speechDuration,
            silenceDuration,
            smoothedVolume: smoothedVolume.toFixed(4),
            threshold: adaptiveThresholdRef.current.toFixed(4),
            silenceThreshold: silenceConfirmThreshold.toFixed(4),
            chunkCount: currentSegmentChunksRef.current.length
          });

          segmentCurrentAudio();

          speechStartTimeRef.current = null;
          lastSpeechTimeRef.current = null;
          setSpeechDetected(false);
          consecutiveSilenceFramesRef.current = 0;
          consecutiveSpeechFramesRef.current = 0;
        }
      }
    }

    return {
      isSpeaking,
      smoothedVolume,
      threshold: adaptiveThresholdRef.current
    };
  }, [isMicMuted, isAISpeaking]);

  // --- Audio segmentation ---
  const segmentCurrentAudio = useCallback(async () => {
    const vad = vadConfigRef.current;

    if (currentSegmentChunksRef.current.length === 0) {
      console.log('ContinuousVoiceChat: No audio chunks to segment');
      return;
    }

    const now = Date.now();
    if (lastSegmentTimeRef.current && (now - lastSegmentTimeRef.current) < vad.minPauseBetweenSegments) {
      console.log('ContinuousVoiceChat: Skipping segment - too soon after last segment');
      return;
    }

    if (isAISpeaking || isMicMuted) {
      console.log('ContinuousVoiceChat: Skipping segment - AI is speaking or mic is muted');
      return;
    }

    try {
      const recorder = mediaRecorderRef.current;
      const mimeType = recorder?.mimeType || 'audio/webm;codecs=opus';

      // Stop MediaRecorder to finalize the WebM container (ensures valid EBML header).
      // Without this, chunks after the first segment lack the initialization segment
      // and produce "EBML header parsing failed" errors on the backend.
      if (recorder && recorder.state === 'recording') {
        await new Promise((resolve) => {
          recorder.onstop = resolve;
          recorder.stop();
        });
      }

      // Signal end of stream to backend to process this segment
      voiceService.stopVoiceStream();
      setIsMicMuted(true);
      processingCountRef.current += 1;
      setProcessingQueue(processingQueue + 1);

      const segmentChunks = [...currentSegmentChunksRef.current];
      currentSegmentChunksRef.current = [];
      audioChunksRef.current = [];

      // Restart MediaRecorder so next segment gets a fresh EBML header
      if (recorder && streamRef.current && streamRef.current.active) {
        try {
          // Start a new WebSocket stream for the new segment
          voiceService.startVoiceStream(sessionId, (text) => {
            if (text && text.trim()) {
              if (listeningModeRef.current === 'passive') {
                const wakeResult = checkForWakeWord(text, systemNameRef.current);
                if (wakeResult.detected) {
                  activateListening();
                  if (wakeResult.remainder.trim()) {
                    processTextAsChat(wakeResult.remainder.trim());
                  }
                }
              } else {
                onMessageReceived({
                  transcription: text.trim(),
                  response: null,
                });
                setSegmentCount(prev => prev + 1);
                if (wakeWordEnabled) resetActiveListeningTimeout();
              }
            }
            setIsMicMuted(false);
            processingCountRef.current = Math.max(0, processingCountRef.current - 1);
            setProcessingQueue(Math.max(0, processingQueue - 1));
          });
          recorder.start(100);
        } catch (restartErr) {
          console.warn('ContinuousVoiceChat: Could not restart MediaRecorder:', restartErr);
        }
      }

      lastSegmentTimeRef.current = now;

      const audioBlob = new Blob(segmentChunks, { type: mimeType });

      if (!audioBlob || audioBlob.size < vad.minChunkSize) {
        console.warn('ContinuousVoiceChat: Audio segment too small, skipping', {
          size: audioBlob?.size || 0,
          minSize: vad.minChunkSize
        });
        return;
      }

      if (audioBlob.size > vad.maxChunkSize) {
        console.warn('ContinuousVoiceChat: Audio segment too large, skipping', {
          size: audioBlob.size,
          maxSize: vad.maxChunkSize
        });
        return;
      }

      console.log('ContinuousVoiceChat: Segmented audio for processing', {
        size: audioBlob.size,
        chunks: segmentChunks.length,
        queueSize: audioQueueRef.current.length,
        listeningMode: listeningModeRef.current
      });

      resetErrors();

      // enqueueAudioSegment(audioBlob);

    } catch (err) {
      console.error('ContinuousVoiceChat: Error segmenting audio:', err);
      incrementErrors();

      if (consecutiveErrorsRef.current >= vadConfigRef.current.maxConsecutiveErrors) {
        console.error('ContinuousVoiceChat: Too many consecutive errors, stopping listening');
        setError('Too many audio processing errors. Please restart voice chat.');
        stopListening();
      }
    }
  }, [isAISpeaking, isMicMuted, incrementErrors, resetErrors]);


  const _validateAudioBlob = useCallback(async (audioBlob) => {
    try {
      if (!audioBlob || audioBlob.size === 0) {
        console.warn('ContinuousVoiceChat: Audio blob is empty or null');
        return false;
      }

      if (audioBlob.size < 500) {
        console.warn('ContinuousVoiceChat: Audio blob too small:', audioBlob.size);
        return false;
      }

      if (audioBlob.size > 5000000) {
        console.warn('ContinuousVoiceChat: Audio blob too large:', audioBlob.size);
        return false;
      }

      console.log('ContinuousVoiceChat: Audio blob passed minimal validation', {
        size: audioBlob.size,
        type: audioBlob.type || 'unknown'
      });

      return true;
    } catch (error) {
      console.warn('ContinuousVoiceChat: Audio validation error:', error);
      return true;
    }
  }, []);

  // --- Process segment: passive mode (wake word check) or active mode (full chat) ---
  const processAudioSegment = useCallback(async (audioBlob) => {
    processingCountRef.current += 1;
    setProcessingQueue(audioQueueRef.current.length + (isProcessingQueueRef.current ? 1 : 0));

    try {
      // --- PASSIVE MODE: transcribe only, check for wake word ---
      if (wakeWordEnabled && listeningModeRef.current === 'passive') {
        console.log('ContinuousVoiceChat: Passive mode - transcribing for wake word...');
        setIsMicMuted(true);

        try {
          const transcription = await voiceService.speechToText(audioBlob);
          const text = transcription?.text || transcription?.transcribed_text || '';

          if (!text.trim()) {
            setIsMicMuted(false);
            return;
          }

          console.log('ContinuousVoiceChat: Passive transcription:', JSON.stringify(text), 'checking against systemName:', JSON.stringify(systemNameRef.current));
          const wakeResult = checkForWakeWord(text, systemNameRef.current);
          console.log('ContinuousVoiceChat: Wake word check result:', JSON.stringify(wakeResult));

          if (wakeResult.detected) {
            console.log('ContinuousVoiceChat: Wake word detected!', {
              matchedPhrase: wakeResult.matchedPhrase,
              remainder: wakeResult.remainder
            });

            activateListening();

            // Process remainder as normal chat if there's content after wake word
            if (wakeResult.remainder.trim()) {
              await processTextAsChat(wakeResult.remainder.trim());
            } else {
              setIsMicMuted(false);
            }
          } else {
            // No wake word — discard silently
            setIsMicMuted(false);
          }
        } catch (err) {
          console.warn('ContinuousVoiceChat: Passive transcription failed:', err);
          setIsMicMuted(false);
        }
        return;
      }

      // --- ACTIVE MODE: transcribe, then send through normal chat pipeline for streaming ---
      console.log('ContinuousVoiceChat: Active mode - transcribing segment...');

      setIsMicMuted(true);

      const transcription = await voiceService.speechToText(audioBlob);
      const text = transcription?.text || transcription?.transcribed_text || '';

      if (text.trim()) {
        console.log('ContinuousVoiceChat: Active transcription:', text);

        // Send transcription through the normal chat pipeline (no aiResponse = uses streaming)
        onMessageReceived({
          transcription: text.trim(),
          response: null,
        });

        setSegmentCount(prev => prev + 1);
        setIsMicMuted(false);

        if (wakeWordEnabled) {
          resetActiveListeningTimeout();
        }
      } else {
        setIsMicMuted(false);
      }

    } catch (err) {
      console.error('ContinuousVoiceChat: Error processing segment:', err);

      const errorMessage = err.message || 'Failed to process speech';

      if (errorMessage.includes('Audio conversion failed') ||
          errorMessage.includes('Invalid data found')) {
        console.warn('ContinuousVoiceChat: Audio conversion error, likely corrupted audio');
        incrementErrors();

        if (consecutiveErrorsRef.current < vadConfigRef.current.maxConsecutiveErrors) {
          setIsMicMuted(false);
          return;
        }
      }

      if (!errorMessage.includes('No speech') &&
          !errorMessage.includes('too short') &&
          !errorMessage.includes('Audio conversion failed')) {
        setError(errorMessage);
        onError(err);
      }

      setIsMicMuted(false);

    } finally {
      processingCountRef.current = Math.max(0, processingCountRef.current - 1);
      setProcessingQueue(audioQueueRef.current.length + (isProcessingQueueRef.current ? 1 : 0));
    }
  }, [sessionId, onMessageReceived, onError, incrementErrors, wakeWordEnabled, activateListening, processTextAsChat, resetActiveListeningTimeout]);

  // --- [Bug 2 fix] Serial queue for segment processing ---
  // Use ref to always call the latest processAudioSegment (avoids stale closure)
  const processAudioSegmentRef = useRef(processAudioSegment);
  processAudioSegmentRef.current = processAudioSegment;

  const processNextInQueue = useCallback(async () => {
    if (isProcessingQueueRef.current || audioQueueRef.current.length === 0) {
      return;
    }
    isProcessingQueueRef.current = true;

    while (audioQueueRef.current.length > 0) {
      const blob = audioQueueRef.current.shift();
      setProcessingQueue(audioQueueRef.current.length + 1);
      await processAudioSegmentRef.current(blob);
    }

    isProcessingQueueRef.current = false;
    setProcessingQueue(0);
  }, []);

  const enqueueAudioSegment = useCallback((audioBlob) => {
    audioQueueRef.current.push(audioBlob);
    setProcessingQueue(audioQueueRef.current.length + (isProcessingQueueRef.current ? 1 : 0));
    processNextInQueue();
  }, [processNextInQueue]);

  // --- Start listening ---
  const startListening = useCallback(async () => {
    try {
      console.log('ContinuousVoiceChat: Starting continuous listening mode...');
      setError(null);

      // Set initial mode based on wake word
      if (wakeWordEnabled) {
        listeningModeRef.current = 'passive';
        setListeningMode('passive');
      } else {
        listeningModeRef.current = 'active';
        setListeningMode('active');
      }

      await voiceService.resumeAudioContext();

      streamRef.current = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          sampleRate: 16000,
          channelCount: 1
        }
      });

      const mimeTypes = [
        'audio/webm;codecs=opus',
        'audio/webm',
        'audio/mp4',
        'audio/ogg;codecs=opus'
      ];

      let mimeType = 'audio/webm;codecs=opus';
      for (const type of mimeTypes) {
        if (MediaRecorder.isTypeSupported(type)) {
          mimeType = type;
          console.log('ContinuousVoiceChat: Using MIME type:', type);
          break;
        }
      }

      mediaRecorderRef.current = new MediaRecorder(streamRef.current, {
        mimeType,
        audioBitsPerSecond: 128000
      });

      const vad = vadConfigRef.current;
      audioChunksRef.current = [];
      currentSegmentChunksRef.current = [];
      speechStartTimeRef.current = null;
      lastSpeechTimeRef.current = null;
      lastSegmentTimeRef.current = null;
      volumeHistoryRef.current = [];
      adaptiveThresholdRef.current = vad.energyThreshold;

      consecutiveSilenceFramesRef.current = 0;
      consecutiveSpeechFramesRef.current = 0;
      lastThresholdResetTimeRef.current = Date.now();
      confirmedSpeakingRef.current = false;

      mediaRecorderRef.current.ondataavailable = (event) => {
        if (event.data.size > 0) {
          audioChunksRef.current.push(event.data);
          currentSegmentChunksRef.current.push(event.data);
          // Stream chunk to backend
          voiceService.sendVoiceChunk(event.data);
        }
      };

      mediaRecorderRef.current.onerror = (error) => {
        console.error('ContinuousVoiceChat: MediaRecorder error:', error);
        setError('Recording error occurred');
        onError(error);
      };

      mediaRecorderRef.current.start(100);
      setIsListening(true);
      
      // Start WebSocket stream
      voiceService.startVoiceStream(sessionId, (text) => {
        if (text && text.trim()) {
          if (listeningModeRef.current === 'passive') {
            const wakeResult = checkForWakeWord(text, systemNameRef.current);
            if (wakeResult.detected) {
              activateListening();
              if (wakeResult.remainder.trim()) {
                processTextAsChat(wakeResult.remainder.trim());
              }
            }
          } else {
            onMessageReceived({
              transcription: text.trim(),
              response: null,
            });
            setSegmentCount(prev => prev + 1);
            if (wakeWordEnabled) resetActiveListeningTimeout();
          }
        }
        setIsMicMuted(false);
        processingCountRef.current = Math.max(0, processingCountRef.current - 1);
        setProcessingQueue(Math.max(0, processingQueue - 1));
      });

      const analyzer = await voiceService.createAudioAnalyzer(streamRef.current);

      if (!analyzer) {
        console.error('ContinuousVoiceChat: Failed to create audio analyzer');
        throw new Error('Failed to initialize audio analyzer');
      }

      await new Promise(resolve => setTimeout(resolve, 500));

      console.log('ContinuousVoiceChat: Audio analyzer ready, starting monitoring...');

      volumeMonitorRef.current = setInterval(() => {
        if (!isMountedRef.current) return;

        try {
          const volume = voiceService.calculateVolume();
          const validVolume = typeof volume === 'number' && !isNaN(volume) ? volume : 0;
          setCurrentVolume(validVolume);

          const levels = voiceService.getAudioLevels();
          if (levels && levels.length > 0) {
            const step = Math.floor(levels.length / 20);
            const sampledLevels = [];
            for (let i = 0; i < 20; i++) {
              const index = Math.min(i * step, levels.length - 1);
              sampledLevels.push(levels[index] || 0);
            }
            // Smooth levels to prevent glitchy pulsing
            setAudioLevels(prev => {
              const smoothed = [];
              for (let i = 0; i < 20; i++) {
                const target = sampledLevels[i] || 0;
                const current = prev[i] || 0;
                // Rise fast (0.4), fall slow (0.15) for natural feel
                const factor = target > current ? 0.4 : 0.15;
                smoothed.push(current + (target - current) * factor);
              }
              return smoothed;
            });

            const hasActiveData = sampledLevels.some(level => level > 0);
            if (hasActiveData && !waveformActive) {
              setWaveformActive(true);
              console.log('ContinuousVoiceChat: Waveform now receiving audio data');
            }
          }

          const vadResult = detectVoiceActivity(validVolume);

          if (Math.random() < 0.05) {
            console.log('ContinuousVoiceChat: VAD state:', {
              ...vadResult,
              volume: validVolume.toFixed(4),
              hasAudioLevels: levels && levels.length > 0,
              listeningMode: listeningModeRef.current
            });
          }
        } catch (err) {
          console.warn('ContinuousVoiceChat: Error in volume monitoring:', err);
        }
      }, 100);

      vadTimerRef.current = setInterval(() => {
        if (!isMountedRef.current || !speechStartTimeRef.current) return;

        const speechDuration = Date.now() - speechStartTimeRef.current;

        if (speechDuration >= vadConfigRef.current.maxSegmentDuration) {
          console.log('ContinuousVoiceChat: Max segment duration reached, force segmenting...');
          segmentCurrentAudio();
          speechStartTimeRef.current = null;
          lastSpeechTimeRef.current = null;
          setSpeechDetected(false);
        }
      }, 1000);

      console.log('ContinuousVoiceChat: Continuous listening started successfully');

    } catch (err) {
      console.error('ContinuousVoiceChat: Failed to start listening:', err);
      setError(err.message || 'Failed to start listening');
      setIsListening(false);
      onError(err);
      cleanup();
    }
  }, [detectVoiceActivity, segmentCurrentAudio, onError, wakeWordEnabled]);


  const stopListening = useCallback(async () => {
    console.log('ContinuousVoiceChat: Stopping continuous listening...');

    if (volumeMonitorRef.current) {
      clearInterval(volumeMonitorRef.current);
      volumeMonitorRef.current = null;
    }
    if (vadTimerRef.current) {
      clearInterval(vadTimerRef.current);
      vadTimerRef.current = null;
    }
    if (activeListeningTimeoutRef.current) {
      clearTimeout(activeListeningTimeoutRef.current);
      activeListeningTimeoutRef.current = null;
    }

    const hadRecording = mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive';
    if (hadRecording) {
      await new Promise((resolve) => {
        mediaRecorderRef.current.onstop = () => {
          console.log('ContinuousVoiceChat: MediaRecorder stopped, finalizing data...');
          resolve();
        };
        mediaRecorderRef.current.stop();
      });
    }

    // Process final segment from the stopped recorder (chunks form a complete WebM)
    if (currentSegmentChunksRef.current.length > 0 && speechStartTimeRef.current) {
      try {
        const segmentChunks = [...currentSegmentChunksRef.current];
        currentSegmentChunksRef.current = [];
        const mimeType = mediaRecorderRef.current?.mimeType || 'audio/webm;codecs=opus';
        const audioBlob = new Blob(segmentChunks, { type: mimeType });
        if (audioBlob.size >= (vadConfigRef.current?.minChunkSize || 1000)) {
          console.log('ContinuousVoiceChat: Processing final segment after stop...', {
            size: audioBlob.size, chunks: segmentChunks.length
          });
          // enqueueAudioSegment(audioBlob);
        }
      } catch (err) {
        console.error('ContinuousVoiceChat: Error processing final segment:', err);
      }
    }
    mediaRecorderRef.current = null;

    if (streamRef.current) {
      streamRef.current.getTracks().forEach(track => track.stop());
      streamRef.current = null;
    }

    setIsListening(false);
    setCurrentVolume(0);
    setSpeechDetected(false);
    setWaveformActive(false);
    setAudioLevels(new Array(20).fill(0));
    audioChunksRef.current = [];
    currentSegmentChunksRef.current = [];
    speechStartTimeRef.current = null;
    lastSpeechTimeRef.current = null;
    lastSegmentTimeRef.current = null;
    volumeHistoryRef.current = [];

    consecutiveSilenceFramesRef.current = 0;
    consecutiveSpeechFramesRef.current = 0;
    confirmedSpeakingRef.current = false;

    console.log('ContinuousVoiceChat: Stopped successfully');
  }, [enqueueAudioSegment]);


  const cleanup = useCallback(() => {
    console.log('ContinuousVoiceChat: Cleaning up resources...');

    if (volumeMonitorRef.current) {
      clearInterval(volumeMonitorRef.current);
      volumeMonitorRef.current = null;
    }

    if (vadTimerRef.current) {
      clearInterval(vadTimerRef.current);
      vadTimerRef.current = null;
    }

    if (activeListeningTimeoutRef.current) {
      clearTimeout(activeListeningTimeoutRef.current);
      activeListeningTimeoutRef.current = null;
    }

    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      try {
        mediaRecorderRef.current.stop();
      } catch (e) {
        console.warn('ContinuousVoiceChat: Error stopping MediaRecorder:', e);
      }
      mediaRecorderRef.current = null;
    }

    if (streamRef.current) {
      streamRef.current.getTracks().forEach(track => track.stop());
      streamRef.current = null;
    }

    audioChunksRef.current = [];
    currentSegmentChunksRef.current = [];
  }, []);

  const handleToggle = useCallback(() => {
    if (isListening) {
      stopListening();
    } else {
      setError(null);
      resetErrors();
      startListening();
    }
  }, [isListening, startListening, stopListening, resetErrors]);

  React.useImperativeHandle(ref, () => ({
    toggleListening: handleToggle,
    startListening,
    stopListening,
    muteMicrophone: () => setIsMicMuted(true),
    unmuteMicrophone: () => setIsMicMuted(false),
    getState: () => ({
      isListening,
      audioLevels,
      speechDetected,
      segmentCount,
      processingQueue,
      waveformActive,
      currentVolume,
      isMicMuted,
      isAISpeaking,
      consecutiveErrors: consecutiveErrorsRef.current,
      listeningMode,
    })
  }), [handleToggle, startListening, stopListening, isListening, audioLevels, speechDetected, segmentCount, processingQueue, waveformActive, currentVolume, isMicMuted, isAISpeaking, listeningMode]);

  // --- [Bug 3 fix] Spacebar toggles instead of push-to-talk ---
  useEffect(() => {
    if (!keyboardShortcutEnabled) return;

    const handleKeyDown = (event) => {
      const isInputFocused =
        document.activeElement?.tagName === 'INPUT' ||
        document.activeElement?.tagName === 'TEXTAREA' ||
        document.activeElement?.isContentEditable;

      const isModifierPressed = event.ctrlKey || event.altKey || event.metaKey;

      if (
        event.code === 'Space' &&
        !event.repeat &&
        !isInputFocused &&
        !isModifierPressed
      ) {
        event.preventDefault();
        handleToggle();
      }
    };

    document.addEventListener('keydown', handleKeyDown);

    return () => {
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [keyboardShortcutEnabled, handleToggle]);

  useEffect(() => {
    isMountedRef.current = true;

    return () => {
      isMountedRef.current = false;
      console.log('ContinuousVoiceChat: Component unmounting, cleaning up...');
      cleanup();
    };
  }, [cleanup]);

  const stateChangeTimeoutRef = useRef(null);

  useEffect(() => {
    if (stateChangeTimeoutRef.current) {
      clearTimeout(stateChangeTimeoutRef.current);
    }

    stateChangeTimeoutRef.current = setTimeout(() => {
      onStateChange({
        isListening,
        audioLevels,
        speechDetected,
        segmentCount,
        processingQueue,
        waveformActive,
        currentVolume,
        listeningMode,
      });
    }, 50);

    return () => {
      if (stateChangeTimeoutRef.current) {
        clearTimeout(stateChangeTimeoutRef.current);
      }
    };
  }, [isListening, audioLevels, speechDetected, segmentCount, processingQueue, waveformActive, currentVolume, listeningMode]);

  const formatDuration = (count) => {
    return `${count} sent`;
  };

  // --- Shared status chips ---
  const renderStatusChips = () => (
    <>
      {wakeWordEnabled && isListening && (
        <Chip
          label={listeningMode === 'active' ? 'Active' : 'Passive'}
          size="small"
          color={listeningMode === 'active' ? 'success' : 'default'}
          variant={listeningMode === 'active' ? 'filled' : 'outlined'}
          sx={{ fontSize: '0.7rem' }}
        />
      )}
      {isListening && (
        <Chip
          label={speechDetected ? 'Speaking' : 'Listening'}
          size="small"
          color={speechDetected ? 'error' : 'default'}
          variant="filled"
          sx={{
            fontFamily: 'monospace',
            minWidth: 80,
            animation: speechDetected ? `${pulseAnimation} 1s ease-in-out infinite` : 'none'
          }}
        />
      )}
      {segmentCount > 0 && (
        <Chip
          label={formatDuration(segmentCount)}
          size="small"
          color="success"
          variant="outlined"
          sx={{ fontSize: '0.7rem' }}
        />
      )}
      {processingQueue > 0 && (
        <Chip
          label={`${processingQueue} queued`}
          size="small"
          color="warning"
          variant="outlined"
          sx={{ fontSize: '0.7rem' }}
          icon={<CircularProgress size={12} />}
        />
      )}
      {isMicMuted && (
        <Chip label="Mic Muted" size="small" color="warning" variant="filled" sx={{ fontSize: '0.7rem' }} />
      )}
      {isAISpeaking && (
        <Chip label="AI Speaking" size="small" color="success" variant="filled" sx={{ fontSize: '0.7rem' }} />
      )}
      {consecutiveErrorDisplay > 0 && (
        <Chip label={`${consecutiveErrorDisplay} errors`} size="small" color="error" variant="filled" sx={{ fontSize: '0.7rem' }} />
      )}
    </>
  );

  // --- Shared waveform ---
  const renderWaveform = (height = 32) => (
    <Box
      sx={{
        display: 'flex',
        alignItems: 'center',
        height,
        px: 1,
        backgroundColor: 'background.paper',
        borderRadius: 1,
        border: 1,
        borderColor: speechDetected ? 'error.main' : (waveformActive ? 'primary.main' : 'grey.400'),
        minWidth: 80,
        position: 'relative'
      }}
    >
      <CanvasWaveform 
        isListening={isListening} 
        speechDetected={speechDetected} 
        waveformActive={waveformActive} 
        height={height} 
      />
      {!waveformActive && (
        <Box
          sx={{
            position: 'absolute',
            top: '50%',
            left: '50%',
            transform: 'translate(-50%, -50%)',
            display: 'flex',
            alignItems: 'center',
            gap: 0.5,
            fontSize: '0.65rem',
            color: 'text.secondary'
          }}
        >
          <CircularProgress size={10} />
          <Typography variant="caption" sx={{ fontSize: '0.65rem' }}>
            Initializing...
          </Typography>
        </Box>
      )}
    </Box>
  );

  // --- COMPACT RENDERING: circular waveform button ---
  if (compact) {
    const btnSize = 40;
    const isProcessingAudio = isProcessing || processingQueue > 0;

    return (
      <Tooltip title={isProcessingAudio ? 'Processing...' : isListening ? 'Stop listening' : 'Start voice chat'}>
        <IconButton
          onClick={handleToggle}
          disabled={processingQueue > 3}
          sx={{
            width: btnSize,
            height: btnSize,
            borderRadius: '50%',
            backgroundColor: isProcessingAudio
              ? '#7c4dff'
              : isListening
                ? (speechDetected ? '#ff1744' : '#2979ff')
                : 'transparent',
            color: isListening || isProcessingAudio ? '#fff' : 'text.primary',
            border: isListening || isProcessingAudio ? '2px solid transparent' : 'none',
            transition: 'all 0.2s ease',
            boxShadow: isProcessingAudio
              ? '0 0 16px rgba(124,77,255,0.6), 0 0 4px rgba(124,77,255,0.3) inset'
              : isListening && speechDetected
                ? '0 0 16px rgba(255,23,68,0.6), 0 0 4px rgba(255,23,68,0.3) inset'
                : isListening
                  ? '0 0 12px rgba(41,121,255,0.5)'
                  : 'none',
            animation: isProcessingAudio
              ? `${pulseAnimation} 1.5s ease-in-out infinite`
              : 'none',
            p: 0,
            '&:hover': {
              backgroundColor: isProcessingAudio
                ? '#651fff'
                : isListening
                  ? (speechDetected ? '#d50000' : '#2962ff')
                  : 'action.hover',
            },
          }}
        >
          <Box sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: btnSize * 0.6,
            height: btnSize * 0.6,
            color: 'inherit'
          }}>
            <CanvasWaveform 
              isListening={isListening} 
              speechDetected={speechDetected} 
              waveformActive={waveformActive} 
              compact={true}
            />
          </Box>
        </IconButton>
      </Tooltip>
    );
  }

  // --- FULL-SIZE (NON-COMPACT) RENDERING ---
  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      {/* Error */}
      {error && (
        <Alert severity="error" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {/* Controls Row */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
        <Tooltip
          title={
            isListening
              ? 'Click to stop listening'
              : 'Click to start continuous listening'
          }
        >
          <IconButton
            onClick={handleToggle}
            disabled={processingQueue > 3}
            color={isListening ? 'error' : 'primary'}
            size="large"
            sx={{
              border: 2,
              borderColor: isListening ? 'error.main' : 'primary.main',
              backgroundColor: isListening ? 'error.light' : 'transparent',
              animation: isListening ? `${pulseAnimation} 2s ease-in-out infinite` : 'none',
              width: 56,
              height: 56,
              '&:hover': {
                backgroundColor: isListening ? 'error.main' : 'primary.light',
                color: 'white'
              }
            }}
          >
            {isListening ? <MicIcon /> : <MicOffIcon />}
          </IconButton>
        </Tooltip>

        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, flexWrap: 'wrap' }}>
          {renderStatusChips()}
        </Box>

        {!isListening && keyboardShortcutEnabled && (
          <Typography variant="body2" color="text.secondary" sx={{ ml: 'auto' }}>
            Press <strong>Spacebar</strong> to toggle
          </Typography>
        )}
      </Box>

      {/* Waveform */}
      {isListening && (
        <Box sx={{ width: '100%' }}>
          {renderWaveform(64)}
        </Box>
      )}

      {/* Wake word info */}
      {wakeWordEnabled && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <HearingIcon fontSize="small" color="action" />
          <Typography variant="body2" color="text.secondary">
            {isListening
              ? listeningMode === 'passive'
                ? `Say "Hey ${systemName}" to activate`
                : `Listening actively (${Math.round(activeListeningDurationRef.current / 1000)}s timeout)`
              : `Wake word: "Hey ${systemName}"`
            }
          </Typography>
        </Box>
      )}

      {/* Not listening message */}
      {!isListening && (
        <Paper
          variant="outlined"
          sx={{
            p: 3,
            textAlign: 'center',
            backgroundColor: 'background.default',
          }}
        >
          <MicOffIcon sx={{ fontSize: 48, color: 'text.disabled', mb: 1 }} />
          <Typography variant="body1" color="text.secondary">
            Click the microphone button or press Spacebar to start listening
          </Typography>
          {wakeWordEnabled && (
            <Typography variant="body2" color="text.disabled" sx={{ mt: 1 }}>
              Wake word mode is enabled — listener will start in passive mode
            </Typography>
          )}
        </Paper>
      )}
    </Box>
  );
});

ContinuousVoiceChat.displayName = 'ContinuousVoiceChat';

ContinuousVoiceChat.propTypes = {
  sessionId: PropTypes.string,
  onMessageReceived: PropTypes.func,
  onError: PropTypes.func,
  onStateChange: PropTypes.func,
  compact: PropTypes.bool,
  wakeWordEnabled: PropTypes.bool,
  systemName: PropTypes.string,
  onWakeWordDetected: PropTypes.func,
};

export default ContinuousVoiceChat;
