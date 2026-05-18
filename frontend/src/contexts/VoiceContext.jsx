import React, { createContext, useContext, useState, useEffect, useRef, useCallback } from "react";
import voiceService, {
  speechToText,
  textToSpeech,
  getAvailableVoices,
  getVoiceStatus,
  playAudio,
} from "../api/voiceService";
import { BACKEND_URL } from "../api/apiClient";

const VoiceContext = createContext();
export const useVoice = () => useContext(VoiceContext);

export const VoiceProvider = ({ children }) => {
  const [isRecording, setIsRecording] = useState(false);
  const [isPlaying, setIsPlaying] = useState(false);
  const [transcribedText, setTranscribedText] = useState("");
  const [recordingError, setRecordingError] = useState(null);
  const [voiceStatus, setVoiceStatus] = useState(null);
  const [availableVoices, setAvailableVoices] = useState([]);
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

  const speak = useCallback(async (text) => {
    if (!text || !ttsEnabled) return;
    try {
      incrementActiveTTS();
      const cleanedText = cleanTextForSpeech(text);
      console.log('TTS: Original text:', text.substring(0, 100) + '...');
      console.log('TTS: Cleaned text:', cleanedText.substring(0, 100) + '...');
      
      const result = await textToSpeech(cleanedText, selectedVoice);
      if (result.audio_url) {
        const audioUrl = `${BACKEND_URL}${result.audio_url}`;
        await playAudio(audioUrl);
      }
    } catch (error) {
      console.error("TTS failed:", error);
    } finally {
      decrementActiveTTS();
    }
  }, [selectedVoice, ttsEnabled, incrementActiveTTS, decrementActiveTTS, cleanTextForSpeech]);

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
    
    try {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        const errorMsg = "Your browser doesn't support microphone access.";
        setMicPermissionError(errorMsg);
        setMicPermissionState('denied');
        setMicEnabled(false);
        return;
      }
      
      if (navigator.permissions && navigator.permissions.query) {
        try {
          const permissionStatus = await navigator.permissions.query({ name: 'microphone' });
          
          if (!isMountedRef.current) return;
          
          if (permissionStatus.state === 'granted') {
            setMicPermissionState('granted');
            setMicEnabled(true);
            return;
          } else if (permissionStatus.state === 'denied') {
            setMicPermissionState('denied');
            setMicEnabled(false);
            return;
          }
        } catch (permErr) {
          console.warn("VoiceContext: Permissions API not fully supported:", permErr);
        }
      }
      
      try {
        const testStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        
        if (!isMountedRef.current) {
          testStream.getTracks().forEach(track => track.stop());
          return;
        }
        
        testStream.getTracks().forEach(track => track.stop());
        
        setMicPermissionState('granted');
        setMicEnabled(true);
        setMicPermissionError(null);
        
      } catch (getUserMediaError) {
        if (!isMountedRef.current) return;
        
        let errorMessage = "Microphone access failed. ";
        
        if (getUserMediaError.name === 'NotAllowedError') {
          errorMessage += "Permission denied. Please allow microphone access.";
          setMicPermissionState('denied');
        } else if (getUserMediaError.name === 'NotFoundError') {
          errorMessage += "No microphone found.";
          setMicPermissionState('denied');
        } else {
          errorMessage += "Please try again.";
          setMicPermissionState('denied');
        }
        
        setMicPermissionError(errorMessage);
        setMicEnabled(false);
      }
      
    } catch (error) {
      if (!isMountedRef.current) return;
      
      setMicPermissionError("Failed to check microphone permissions.");
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
      
      setMicPermissionError("Failed to get microphone permission.");
      setMicPermissionState('denied');
      setMicEnabled(false);
      
      return false;
    } finally {
      if (isMountedRef.current) {
        setIsCheckingPermissions(false);
      }
    }
  }, []);

  useEffect(() => {
    const initializeVoice = async () => {
      if (!isMountedRef.current) return;
      
      try {
        const status = await getVoiceStatus();
        if (!isMountedRef.current) return;
        setVoiceStatus(status);

        const voices = await getAvailableVoices();
        if (!isMountedRef.current) return;
        setAvailableVoices(voices.voices || []);

        if (voices.voices && voices.voices.length > 0) {
          if (!isMountedRef.current) return;
          
          const isCurrentVoiceAvailable = voices.voices.some(v => v.id === selectedVoice);
          if (!isCurrentVoiceAvailable) {
            const defaultVoice = voices.voices.find(v => v.id === "libritts") || voices.voices[0];
            if (defaultVoice) {
              if (!isMountedRef.current) return;
              setSelectedVoice(defaultVoice.id);
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
    };

    initializeVoice();
  }, [checkMicrophonePermissions, selectedVoice]);

  useEffect(() => {
    const handleStorageChange = (e) => {
      if (e.key === 'guaardvark_voiceSettings') {
        try {
          const voiceSettings = JSON.parse(e.newValue || '{}');
          if (voiceSettings.ttsEnabled !== undefined) {
            setTtsEnabled(voiceSettings.ttsEnabled);
          }
          if (voiceSettings.voice !== undefined) {
            setSelectedVoice(voiceSettings.voice);
          }
        } catch (error) {
          console.warn('VoiceContext: Failed to parse voice settings from storage change:', error);
        }
      }
    };

    window.addEventListener('storage', handleStorageChange);
    return () => window.removeEventListener('storage', handleStorageChange);
  }, []);

  const startRecording = useCallback(async () => {
    if (!isMountedRef.current) return;
    
    if (!micEnabled) {
      if (micPermissionState === 'denied') {
        setRecordingError("Microphone access denied. Please enable permissions.");
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
        let errorMessage = "Failed to start recording. ";
        
        if (error.name === 'NotAllowedError') {
          errorMessage += "Microphone permission denied.";
          setMicPermissionState('denied');
          setMicEnabled(false);
        } else if (error.name === 'NotFoundError') {
          errorMessage += "No microphone found.";
        } else {
          errorMessage += "Please try again.";
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
              setRecordingError("No speech detected.");
            }
          }
        } else {
          setRecordingError("No audio data recorded.");
        }
        
      } catch (error) {
        console.warn("Error stopping recording:", error);
        setRecordingError("Recording failed.");
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
    selectedVoice,
    ttsEnabled,
    micEnabled,
    
    micPermissionState,
    micPermissionError,
    isCheckingPermissions,
    
    startRecording,
    stopRecording,
    speak,
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
