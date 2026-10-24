import React, { createContext, useContext, useState, useEffect, useRef, useCallback } from "react";
import voiceService, {
  speechToText,
  textToSpeech,
  getAvailableVoices,
  getVoiceStatus,
  playAudio,
} from "../api/voiceService";
import { BACKEND_URL, handleResponse } from "../api/apiClient";
import { useHealth } from "./HealthContext";
import { chunkForSpeech, describeSpeakError, speakInChunks } from "../utils/replySpeech";
import {
  VOICE_SETTINGS_EVENT,
  VOICE_SETTINGS_KEY,
  readVoiceSettings,
} from "../config/voiceDefaults";
import { translate } from "../i18n";

const VoiceContext = createContext();
export const useVoice = () => useContext(VoiceContext);

const debugLog = (...args) => {
  if (import.meta.env.DEV) {
    console.debug(...args);
  }
};

export const VoiceProvider = ({ children }) => {
  const { isBackendOffline } = useHealth();
  const [isRecording, setIsRecording] = useState(false);
  const [isPlaying, setIsPlaying] = useState(false);
  const [transcribedText, setTranscribedText] = useState("");
  const [recordingError, setRecordingError] = useState(null);
  const [voiceStatus, setVoiceStatus] = useState(null);
  const [availableVoices, setAvailableVoices] = useState([]);
  // Which engine /voices says will speak ("kokoro" or "piper-tts") and its default voice.
  const [voiceEngine, setVoiceEngine] = useState(null);
  const [defaultVoice, setDefaultVoice] = useState(null);
  // One plain sentence when the last reply could not be spoken; null otherwise.
  const [speakError, setSpeakError] = useState(null);
  const [selectedVoice, setSelectedVoice] = useState(() => {
    try {
      const voiceSettings = localStorage.getItem('guaardvark_voiceSettings');
      if (voiceSettings) {
        const parsed = JSON.parse(voiceSettings);
        return parsed.voice || "libritts";
      }
      return "libritts";
    } catch (error) {
      console.warn('Failed to load voice selection from SettingsPage:', error);
      return "libritts";
    }
  });
  const [ttsEnabled, setTtsEnabled] = useState(() => {
    try {
      const voiceSettings = localStorage.getItem('guaardvark_voiceSettings');
      if (voiceSettings) {
        const parsed = JSON.parse(voiceSettings);
        // Use !== false so that ttsEnabled defaults to true when not explicitly set.
        // This matches VoiceChatButton's interpretation and ensures voice chat
        // works out of the box without requiring a trip to Settings first.
        return parsed.ttsEnabled !== false;
      }
      const saved = localStorage.getItem('guaardvark_ttsEnabled');
      return saved !== null ? JSON.parse(saved) : true;
    } catch (error) {
      console.warn('Failed to load TTS setting from voice settings:', error);
      return true;
    }
  });
  const [micEnabled, setMicEnabled] = useState(() => {
    try {
      const saved = localStorage.getItem('guaardvark_micEnabled');
      return saved !== null ? JSON.parse(saved) : true;
    } catch (error) {
      console.warn('Failed to load microphone setting from localStorage:', error);
      return true;
    }
  });
  
  const [micPermissionState, setMicPermissionState] = useState('unknown');
  const [micPermissionError, setMicPermissionError] = useState(null);
  const [isCheckingPermissions, setIsCheckingPermissions] = useState(false);

  const audioChunksRef = useRef([]);
  const isMountedRef = useRef(true);
  const cleanupTimeoutRef = useRef(null);
  
  const ttsQueueRef = useRef([]);
  const currentTTSRequestRef = useRef(null);
  const ttsAbortControllerRef = useRef(null);
  const activeTTSCountRef = useRef(0);

  const cleanupMediaResources = useCallback(() => {
    try {
      if (voiceService.getIsRecording()) {
        voiceService.stopRecording();
      }
      voiceService.cleanup();
    } catch (error) {
      console.warn("VoiceContext: Error cleaning up voiceService:", error);
    }
    
    audioChunksRef.current = [];

    if (cleanupTimeoutRef.current) {
      clearTimeout(cleanupTimeoutRef.current);
      cleanupTimeoutRef.current = null;
    }
  }, []);

  const updateTTSPlayingState = useCallback(() => {
    if (!isMountedRef.current) return;
    setIsPlaying(activeTTSCountRef.current > 0);
  }, []);

  const incrementActiveTTS = useCallback(() => {
    activeTTSCountRef.current += 1;
    updateTTSPlayingState();
  }, [updateTTSPlayingState]);

  const decrementActiveTTS = useCallback(() => {
    activeTTSCountRef.current = Math.max(0, activeTTSCountRef.current - 1);
    updateTTSPlayingState();
  }, [updateTTSPlayingState]);

  const cleanTextForSpeech = useCallback((text) => {
    if (!text) return "";
    
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
      .replace(/([^.!?])\s*$/, '$1.'); // Add period if doesn't end with punctuation
  }, []);

  /**
   * The audio URL for one chunk of text, from the configured voice.
   * With `options.stream` the backend answers with WAV bytes when Audio
   * Foundry speaks, and with the usual JSON (or an error) when Piper does;
   * both are handled, so a stream request never plays an error as audio.
   */
  const synthesizeChunk = useCallback(async (chunk, options) => {
    const result = await textToSpeech(chunk, selectedVoice, null, options);
    if (result.stream && result.response) {
      const response = result.response;
      const type = response.headers?.get?.("content-type") || "";
      if (response.ok && type.startsWith("audio/")) {
        return URL.createObjectURL(await response.blob());
      }
      const parsed = await handleResponse(response);
      return parsed.audio_url ? `${BACKEND_URL}${parsed.audio_url}` : null;
    }
    return result.audio_url ? `${BACKEND_URL}${result.audio_url}` : null;
  }, [selectedVoice]);

  /**
   * Speak a reply. The text is spoken whole, in short chunks: the next chunk is
   * synthesized while the current one plays. Resolves to {spoken, total,
   * cancelled, error}; a failure also sets `speakError`, one plain sentence.
   */
  const speak = useCallback(async (text, options = {}) => {
    if (!text || !ttsEnabled) return { spoken: 0, total: 0, cancelled: false, error: null };
    // A stopPlayback() while the audio is still being made cancels this one too.
    const epoch = voiceService.playbackEpoch;
    const isCancelled = () => voiceService.playbackEpoch !== epoch;
    incrementActiveTTS();
    try {
      const cleanedText = cleanTextForSpeech(text);
      const chunks = chunkForSpeech(cleanedText);
      debugLog('TTS text prepared', {
        originalLength: text.length,
        cleanedLength: cleanedText.length,
        chunks: chunks.length,
      });
      if (isMountedRef.current) setSpeakError(null);

      const result = await speakInChunks(chunks, {
        synthesize: (chunk) => synthesizeChunk(chunk, options),
        play: playAudio,
        isCancelled,
      });
      if (result.error) {
        console.error("TTS failed:", result.error);
        if (isMountedRef.current) setSpeakError(describeSpeakError(result.error, result));
      }
      return result;
    } finally {
      decrementActiveTTS();
    }
  }, [ttsEnabled, incrementActiveTTS, decrementActiveTTS, cleanTextForSpeech, synthesizeChunk]);

  const clearSpeakError = useCallback(() => setSpeakError(null), []);

  const clearTranscription = useCallback(() => {
    setTranscribedText("");
    setRecordingError(null);
  }, []);

  const cancelCurrentTTS = useCallback(() => {
    if (ttsAbortControllerRef.current) {
      ttsAbortControllerRef.current.abort();
      ttsAbortControllerRef.current = null;
    }
    activeTTSCountRef.current = 0;
    updateTTSPlayingState();
  }, [updateTTSPlayingState]);

  const clearTTSQueue = useCallback(() => {
    cancelCurrentTTS();
    ttsQueueRef.current = [];
    currentTTSRequestRef.current = null;
  }, [cancelCurrentTTS]);

  useEffect(() => {
    // Set on every mount: React's development mode mounts, unmounts and mounts
    // again, and a flag left false makes every state update below a no-op
    // (isPlaying never turns on, so the mic would not pause for a spoken reply).
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      cleanupMediaResources();
      clearTTSQueue();
    };
  }, [cleanupMediaResources, clearTTSQueue]);

  const checkMicrophonePermissions = useCallback(async () => {
    if (!isMountedRef.current) return;
    
    setIsCheckingPermissions(true);
    setMicPermissionError(null);
    
    // Reads the permission without asking for it: the browser's prompt
    // belongs to the moment someone clicks a mic button, not to page load.
    try {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        const errorMsg = window.isSecureContext === false
          ? translate("messages.voice.micNeedsSecureContext")
          : translate("messages.voice.micUnsupported");
        setMicPermissionError(errorMsg);
        setMicPermissionState('unavailable');
        return;
      }

      if (navigator.permissions && navigator.permissions.query) {
        try {
          const permissionStatus = await navigator.permissions.query({ name: 'microphone' });

          if (!isMountedRef.current) return;
          setMicPermissionState(permissionStatus.state);
          return;
        } catch (permErr) {
          console.warn("VoiceContext: Permissions API not fully supported:", permErr);
        }
      }
      setMicPermissionState('unknown');
    } catch (error) {
      if (!isMountedRef.current) return;
      
      setMicPermissionError(translate("messages.voice.permissionCheckFailed"));
      setMicPermissionState('denied');
      setMicEnabled(false);
    } finally {
      if (isMountedRef.current) {
        setIsCheckingPermissions(false);
      }
    }
  }, []);

  const requestMicrophonePermission = useCallback(async () => {
    if (!isMountedRef.current) return false;
    
    setIsCheckingPermissions(true);
    setMicPermissionError(null);
    
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      
      if (!isMountedRef.current) {
        stream.getTracks().forEach(track => track.stop());
        return false;
      }
      
      stream.getTracks().forEach(track => track.stop());
      
      setMicPermissionState('granted');
      setMicEnabled(true);
      setMicPermissionError(null);
      
      return true;
      
    } catch (error) {
      if (!isMountedRef.current) return false;
      
      setMicPermissionError(translate("messages.voice.permissionRequestFailed"));
      setMicPermissionState('denied');
      setMicEnabled(false);
      
      return false;
    } finally {
      if (isMountedRef.current) {
        setIsCheckingPermissions(false);
      }
    }
  }, []);

  // One GET /voices. /text-to-speech picks its engine per request (Kokoro while
  // Audio Foundry runs with a voice pack, else Piper), so the list is re-read
  // when the mic popover opens, not only at mount. Resolves to the response,
  // or null once unmounted; a failed request rejects.
  const refreshVoices = useCallback(async () => {
    const voices = await getAvailableVoices();
    if (!isMountedRef.current) return null;
    setAvailableVoices(voices.voices || []);
    setVoiceEngine(voices.engine || null);
    setDefaultVoice(voices.default_voice || null);
    return voices;
  }, []);

  const initializeVoice = useCallback(async () => {
    if (!isMountedRef.current) return;

    try {
      const status = await getVoiceStatus();
      if (!isMountedRef.current) return;
      setVoiceStatus(status);

      const voices = await refreshVoices();
      if (!voices) return;

      if (voices.voices && voices.voices.length > 0) {
        if (!isMountedRef.current) return;

        // The saved voice may belong to the other engine (a Piper id while
        // Kokoro speaks, or the reverse): speak with this engine's default.
        const isCurrentVoiceAvailable = voices.voices.some(v => v.id === selectedVoice && v.available !== false);
        if (!isCurrentVoiceAvailable) {
          const fallback = voices.voices.find(v => v.id === voices.default_voice)
            || voices.voices.find(v => v.available !== false);
          if (fallback) {
            if (!isMountedRef.current) return;
            setSelectedVoice(fallback.id);
          }
        }
      }

      if (!isMountedRef.current) return;
      await checkMicrophonePermissions();

    } catch (error) {
      console.error("VoiceContext: Failed to initialize voice features:", error);
      if (isMountedRef.current) {
        setVoiceStatus({ status: "error", error: error.message });
      }
    }
  }, [checkMicrophonePermissions, refreshVoices, selectedVoice]);

  useEffect(() => {
    initializeVoice();
  }, [initializeVoice]);

  // Auto-recover when the backend comes back: re-run voice init so voiceStatus stops
  // being stuck on { status: "error" } after a backend blip, without a manual reload.
  const voiceWasOfflineRef = useRef(false);
  useEffect(() => {
    if (isBackendOffline) {
      voiceWasOfflineRef.current = true;
    } else if (voiceWasOfflineRef.current) {
      voiceWasOfflineRef.current = false;
      initializeVoice();
    }
  }, [isBackendOffline, initializeVoice]);

  useEffect(() => {
    const applySettings = () => {
      const voiceSettings = readVoiceSettings();
      setTtsEnabled(voiceSettings.ttsEnabled);
      setSelectedVoice(voiceSettings.voice);
    };
    const handleStorageChange = (e) => {
      if (e.key === VOICE_SETTINGS_KEY) applySettings();
    };

    // 'storage' fires only in other tabs; same-tab writers send VOICE_SETTINGS_EVENT.
    window.addEventListener('storage', handleStorageChange);
    window.addEventListener(VOICE_SETTINGS_EVENT, applySettings);
    return () => {
      window.removeEventListener('storage', handleStorageChange);
      window.removeEventListener(VOICE_SETTINGS_EVENT, applySettings);
    };
  }, []);

  const startRecording = useCallback(async () => {
    if (!isMountedRef.current) return;
    
    if (!micEnabled) {
      if (micPermissionState === 'denied') {
        setRecordingError(translate("messages.voice.micAccessDenied"));
        return;
      }
      
        const granted = await requestMicrophonePermission();
        if (!granted) {
        return;
      }
    }

    cleanupMediaResources();

    try {
      setRecordingError(null);
      
      await voiceService.startRecording({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          sampleRate: 16000
        }
      });
      
      if (!isMountedRef.current) {
        await voiceService.stopRecording();
        return;
      }
      
      if (isMountedRef.current) {
        setIsRecording(true);
      }
      
    } catch (error) {
      if (isMountedRef.current) {
        let errorMessage;
        
        if (error.name === 'NotAllowedError') {
          errorMessage = translate("messages.voice.startFailedPermission");
          setMicPermissionState('denied');
          setMicEnabled(false);
        } else if (error.name === 'NotFoundError') {
          errorMessage = translate("messages.voice.startFailedNoMic");
        } else {
          errorMessage = translate("messages.voice.startFailedRetry");
        }
        
        setRecordingError(errorMessage);
      }
      cleanupMediaResources();
    }
  }, [micEnabled, micPermissionState, cleanupMediaResources, requestMicrophonePermission]);

  const stopRecording = useCallback(async () => {
    if (!isMountedRef.current) return;
    
    if (isRecording) {
      try {
        const result = await voiceService.stopRecording();
        setIsRecording(false);
        
        if (result && result.size > 0) {
          const audioBlob = result;
          const fileExtension = audioBlob.type.includes('webm') ? "webm" : 
                               audioBlob.type.includes('mp4') ? "mp4" : 
                               audioBlob.type.includes('ogg') ? "ogg" : "webm";
          const audioFile = new File([audioBlob], `recording.${fileExtension}`, { type: audioBlob.type });
          
          const transcription = await speechToText(audioFile);
          if (isMountedRef.current) {
            if (transcription.text && transcription.text.trim()) {
              setTranscribedText(transcription.text);
            } else {
              setRecordingError(translate("messages.voice.noSpeech"));
            }
          }
        } else {
          setRecordingError(translate("messages.voice.noAudio"));
        }
        
      } catch (error) {
        console.warn("Error stopping recording:", error);
        setRecordingError(translate("messages.voice.recordingFailed"));
      }
      
      cleanupTimeoutRef.current = setTimeout(() => {
        if (isMountedRef.current) {
          cleanupMediaResources();
        }
      }, 1000);
    } else {
      cleanupMediaResources();
    }
  }, [isRecording, cleanupMediaResources]);

  const value = {
    isRecording,
    isPlaying,
    transcribedText,
    recordingError,
    
    voiceStatus,
    availableVoices,
    voiceEngine,
    defaultVoice,
    selectedVoice,
    ttsEnabled,
    micEnabled,
    speakError,

    micPermissionState,
    micPermissionError,
    isCheckingPermissions,

    startRecording,
    stopRecording,
    speak,
    clearSpeakError,
    refreshVoices,
    clearTranscription,
    setSelectedVoice,
    setTtsEnabled,
    setMicEnabled,
    
    checkMicrophonePermissions,
    requestMicrophonePermission,
    
    cancelCurrentTTS,
    clearTTSQueue,
    
    call: {},
    callAccepted: false,
    myVideo: { current: null },
    userVideo: { current: null },
    stream: null,
    name: "",
    setName: () => {},
    callEnded: false,
    me: "",
    callUser: () => {},
    leaveCall: () => {},
    answerCall: () => {},
  };

  return (
    <VoiceContext.Provider value={value}>{children}</VoiceContext.Provider>
  );
};

export const useVoiceContext = () => {
  const voice = useVoice();
  
  return {
    availableVoices: voice.availableVoices,
    voiceURI: voice.selectedVoice,
    setVoiceURI: voice.setSelectedVoice,
    speak: voice.speak,
    ttsSupported: voice.voiceStatus?.text_to_speech || false,
    isPlaying: voice.isPlaying,
    ttsEnabled: voice.ttsEnabled,
    setTtsEnabled: voice.setTtsEnabled,
  };
};
