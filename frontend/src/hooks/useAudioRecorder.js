import { useState, useEffect, useRef, useCallback } from 'react';
import voiceService from '../api/voiceService';

/**
 * Custom hook for audio recording with voice service integration
 * Provides state management, error handling, and audio visualization support
 */
const useAudioRecorder = (options = {}) => {
  const [isRecording, setIsRecording] = useState(false);
  const [isPaused, setIsPaused] = useState(false);
  const [audioBlob, setAudioBlob] = useState(null);
  const [audioUrl, setAudioUrl] = useState(null);
  const [duration, setDuration] = useState(0);
  const [error, setError] = useState(null);
  const [permission, setPermission] = useState('unknown');
  const [isInitialized, setIsInitialized] = useState(false);
  const [audioLevels, setAudioLevels] = useState([]);
  const [volume, setVolume] = useState(0);
  const [recordingVolume, setRecordingVolume] = useState(0); // Add recordingVolume property

  const durationIntervalRef = useRef(null);
  const animationFrameRef = useRef(null);
  const startTimeRef = useRef(null);
  const mediaStreamRef = useRef(null);
  const volumeRef = useRef(0); // Track volume with ref for real-time updates

  // Default options
  const defaultOptions = {
    timeslice: 1000,
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
      sampleRate: 16000,
    },
    enableVisualization: true,
    maxDuration: 60000, // 60 seconds max
    ...options,
  };

  /**
   * Initialize the audio recorder
   */
  const initialize = useCallback(async () => {
    try {
      setError(null);
      
      // Check microphone permission
      const permissionState = await voiceService.checkMicrophonePermission();
      setPermission(permissionState);
      
      if (permissionState === 'denied') {
        throw new Error('Microphone permission denied');
      }
      
      if (permissionState === 'prompt') {
        const requestResult = await voiceService.requestMicrophonePermission();
        setPermission(requestResult);
        
        if (requestResult === 'denied') {
          throw new Error('Microphone permission denied');
        }
      }
      
      setIsInitialized(true);
    } catch (err) {
      setError(err.message);
      setIsInitialized(false);
    }
  }, []);

  /**
   * Resume audio context with user interaction
   */
  const resumeAudioContext = useCallback(async () => {
    try {
      console.log('useAudioRecorder: Resuming audio context...');
      const resumed = await voiceService.resumeAudioContext();
      if (resumed) {
        console.log('useAudioRecorder: Audio context resumed successfully');
      } else {
        console.error('useAudioRecorder: Failed to resume audio context');
      }
      return resumed;
    } catch (err) {
      console.error('useAudioRecorder: Audio context resume error:', err);
      return false;
    }
  }, []);

  /**
   * Start recording audio
   */
  const startRecording = useCallback(async () => {
    try {
      if (!isInitialized) {
        await initialize();
      }
      
      if (isRecording) {
        console.warn('Recording already in progress');
        return;
      }

      console.log('Starting audio recording...');
      setError(null);
      setAudioBlob(null);
      setAudioUrl(null);
      setDuration(0);
      setAudioLevels([]);
      setVolume(0);
      setRecordingVolume(0);
      volumeRef.current = 0;

      // Set up error callback
      voiceService.setOnErrorCallback((error) => {
        setError(error.message);
        setIsRecording(false);
      });

      // Resume audio context first (required for user interaction)
      const resumed = await resumeAudioContext();
      if (!resumed) {
        throw new Error('Failed to resume audio context - microphone access may be blocked');
      }

      // Use EXACT same approach as working VoiceSettingsModal
      console.log('useAudioRecorder: Starting recording with simplified approach...');
      const recordingInfo = await voiceService.startRecording();
      console.log('Voice service started recording:', recordingInfo);
      
      setIsRecording(true);
      setIsPaused(false);
      startTimeRef.current = Date.now();
      mediaStreamRef.current = recordingInfo.stream;

      // Start duration timer
      durationIntervalRef.current = setInterval(() => {
        if (startTimeRef.current) {
          const elapsed = Date.now() - startTimeRef.current;
          setDuration(elapsed);
          
          // Auto-stop at max duration
          if (elapsed >= defaultOptions.maxDuration) {
            stopRecording();
          }
        }
      }, 100);

      // Verify audio analyzer is ready before starting volume monitoring
      console.log('useAudioRecorder: Verifying audio analyzer is ready...');
      const analyzer = voiceService.getAudioAnalyzer();
      if (analyzer) {
        console.log('useAudioRecorder: Audio analyzer confirmed ready, starting volume monitoring...');
        startVolumeMonitoring();
      } else {
        console.warn('useAudioRecorder: Audio analyzer not ready, volume monitoring may not work');
        // Still start monitoring - it will handle the missing analyzer gracefully
        startVolumeMonitoring();
      }

    } catch (err) {
      console.error('Failed to start recording:', err);
      setError(err.message);
      setIsRecording(false);
    }
  }, [isInitialized, isRecording, defaultOptions.maxDuration, initialize, resumeAudioContext, stopRecording]);

  /**
   * Stop recording audio
   */
  const stopRecording = useCallback(async () => {
    try {
      if (!isRecording) {
        console.warn('No recording in progress');
        return null;
      }

      console.log('Stopping audio recording...');

      // Clear timers and volume monitoring interval
      if (durationIntervalRef.current) {
        clearInterval(durationIntervalRef.current);
        durationIntervalRef.current = null;
      }
      
      if (animationFrameRef.current) {
        clearInterval(animationFrameRef.current);
        animationFrameRef.current = null;
      }

      // Stop recording
      const blob = await voiceService.stopRecording();
      
      setIsRecording(false);
      setIsPaused(false);
      setAudioBlob(blob);
      
      // Create audio URL for playback
      const url = voiceService.createAudioUrl(blob);
      setAudioUrl(url);
      
      // Reset volume
      setVolume(0);
      setRecordingVolume(0);
      volumeRef.current = 0;
      
      // Cleanup
      startTimeRef.current = null;
      mediaStreamRef.current = null;
      
      // Audio analyzer cleanup handled by voiceService
      console.log('useAudioRecorder: Audio analyzer cleanup handled by voiceService');
      
      return blob;
    } catch (err) {
      console.error('Failed to stop recording:', err);
      setError(err.message);
      setIsRecording(false);
      return null;
    }
  }, [isRecording]);

  /**
   * Pause recording (if supported)
   */
  const pauseRecording = useCallback(() => {
    if (isRecording && !isPaused) {
      // MediaRecorder pause/resume is not widely supported
      // For now, we'll implement this as a stop/start cycle
      console.warn('Pause/resume not fully supported by MediaRecorder API');
      setIsPaused(true);
    }
  }, [isRecording, isPaused]);

  /**
   * Resume recording (if supported)
   */
  const resumeRecording = useCallback(() => {
    if (isRecording && isPaused) {
      setIsPaused(false);
    }
  }, [isRecording, isPaused]);

  /**
   * Cancel recording
   */
  const cancelRecording = useCallback(() => {
    if (isRecording) {
      try {
        console.log('Cancelling recording...');
        voiceService.cleanup();
        setIsRecording(false);
        setIsPaused(false);
        setAudioBlob(null);
        setAudioUrl(null);
        setDuration(0);
        setAudioLevels([]);
        setVolume(0);
        setRecordingVolume(0);
        volumeRef.current = 0;
        
        // Clear timers and volume monitoring interval
      if (durationIntervalRef.current) {
        clearInterval(durationIntervalRef.current);
        durationIntervalRef.current = null;
      }
      
      if (animationFrameRef.current) {
        clearInterval(animationFrameRef.current);
        animationFrameRef.current = null;
      }

        // Audio analyzer cleanup handled by voiceService
        console.log('useAudioRecorder: Audio analyzer cleanup handled by voiceService');
      
      startTimeRef.current = null;
        mediaStreamRef.current = null;
        
      } catch (err) {
        console.error('Failed to cancel recording:', err);
      }
    }
  }, [isRecording]);

  /**
   * Get current audio blob
   */
  const getCurrentAudioBlob = useCallback(() => {
      return voiceService.getCurrentAudioBlob();
  }, []);

  /**
   * Clear audio data
   */
  const clearAudio = useCallback(() => {
    setAudioBlob(null);
    if (audioUrl) {
      voiceService.cleanupAudioUrl(audioUrl);
      setAudioUrl(null);
    }
    setAudioLevels([]);
    setVolume(0);
    setRecordingVolume(0);
    volumeRef.current = 0;
  }, [audioUrl]);

  /**
   * Start volume monitoring using EXACT same approach as VoiceSettingsModal
   */
  const startVolumeMonitoring = useCallback(() => {
    // Clear any existing interval
    if (animationFrameRef.current) {
      clearInterval(animationFrameRef.current);
    }

    console.log('useAudioRecorder: Starting volume monitoring with EXACT VoiceSettingsModal approach...');
    
    // ENHANCED: Add initial validation
    const analyzer = voiceService.getAudioAnalyzer();
    if (!analyzer) {
      console.error('useAudioRecorder: Cannot start volume monitoring - no audio analyzer available');
      return;
    }
    
    console.log('useAudioRecorder: Audio analyzer confirmed for volume monitoring:', {
      fftSize: analyzer.analyzer?.fftSize,
      frequencyBinCount: analyzer.analyzer?.frequencyBinCount,
      hasStream: !!analyzer.stream
    });
    
    let consecutiveErrors = 0;
    const maxConsecutiveErrors = 5;
    
    // Use setInterval exactly like VoiceSettingsModal (100ms interval)
    animationFrameRef.current = setInterval(() => {
      if (!isRecording) {
        console.log('useAudioRecorder: Stopping volume monitoring - recording stopped');
        clearInterval(animationFrameRef.current);
        animationFrameRef.current = null;
        return;
      }

      try {
        // Get volume using EXACT same method as VoiceSettingsModal
        const volume = voiceService.calculateVolume();
        
        // Get audio levels for visualization
        const levels = voiceService.getAudioLevels();
        
        // ENHANCED: Reset error counter on successful calculation
        consecutiveErrors = 0;
        
        // Update volume states with proper synchronization
        // Only update if volume is valid and recording is still active
        if (typeof volume === 'number' && !isNaN(volume) && isRecording) {
          volumeRef.current = volume;
          setVolume(volume);
          setRecordingVolume(volume);
        } else {
          // Keep previous value if invalid
          const safeVolume = typeof volume === 'number' && !isNaN(volume) ? volume : 0;
          volumeRef.current = safeVolume;
          setVolume(safeVolume);
          setRecordingVolume(safeVolume);
        }
        
        // Update audio levels for visualization
        setAudioLevels(levels);
        
        // ENHANCED: More frequent logging for debugging volume issues
        if (Math.random() < 0.2) { // 20% chance to log (increased for debugging)
          console.log('useAudioRecorder: Volume detected (modal approach):', {
            volume: (volume || 0).toFixed(3),
            audioLevels: levels.length,
            timestamp: Date.now(),
            isRecording,
            consecutiveErrors
          });
        }
        
        // ENHANCED: Log significant volume changes
        const volumeChange = Math.abs(volume - volumeRef.current);
        if (volumeChange > 0.1) {
          console.log('useAudioRecorder: Significant volume change detected:', {
            previousVolume: volumeRef.current.toFixed(3),
            newVolume: (volume || 0).toFixed(3),
            change: volumeChange.toFixed(3)
          });
        }
        
      } catch (err) {
        consecutiveErrors++;
        console.error('useAudioRecorder: Volume monitoring error:', err, {
          consecutiveErrors,
          isRecording,
          analyzerAvailable: !!voiceService.getAudioAnalyzer()
        });
        
        // ENHANCED: Stop monitoring if too many consecutive errors
        if (consecutiveErrors >= maxConsecutiveErrors) {
          console.error('useAudioRecorder: Stopping volume monitoring due to too many errors');
          clearInterval(animationFrameRef.current);
          animationFrameRef.current = null;
          setError(`Volume monitoring failed: ${err.message}`);
          return;
        }
        
        setRecordingVolume(0);
      }
    }, 100); // Same 100ms interval as VoiceSettingsModal
  }, [isRecording, setError]);

  /**
   * Format duration for display
   */
  const formatDuration = useCallback((ms) => {
    const seconds = Math.floor(ms / 1000);
    const minutes = Math.floor(seconds / 60);
    const remainingSeconds = seconds % 60;
    
    return `${minutes}:${remainingSeconds.toString().padStart(2, '0')}`;
  }, []);

  /**
   * Get recording status
   */
  const getStatus = useCallback(() => {
    if (error) return 'error';
    if (!isInitialized) return 'uninitialized';
    if (isRecording && isPaused) return 'paused';
    if (isRecording) return 'recording';
    if (audioBlob) return 'recorded';
    return 'ready';
  }, [error, isInitialized, isRecording, isPaused, audioBlob]);

  /**
   * Check if recording is available
   */
  const isAvailable = useCallback(() => {
    return (
      typeof navigator !== 'undefined' &&
      navigator.mediaDevices &&
      navigator.mediaDevices.getUserMedia &&
      typeof MediaRecorder !== 'undefined'
    );
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (durationIntervalRef.current) {
        clearInterval(durationIntervalRef.current);
      }
      
      if (animationFrameRef.current) {
        clearInterval(animationFrameRef.current);
      }
      
      if (audioUrl) {
        voiceService.cleanupAudioUrl(audioUrl);
      }
      
      // Audio analyzer and context cleanup handled by voiceService
      console.log('useAudioRecorder: Audio cleanup handled by voiceService');
      
      voiceService.cleanup();
    };
  }, [audioUrl]);

  // Initialize on mount
  useEffect(() => {
    if (isAvailable()) {
      initialize();
    } else {
      setError('Audio recording not supported in this browser');
    }
  }, [initialize, isAvailable]);

  return {
    // State
    isRecording,
    isPaused,
    audioBlob,
    audioUrl,
    duration: formatDuration(duration),
    durationMs: duration,
    error,
    permission,
    isInitialized,
    audioLevels,
    volume,
    recordingVolume, // Add recordingVolume to returned object
    status: getStatus(),
    
    // Actions
    startRecording,
    stopRecording,
    pauseRecording,
    resumeRecording,
    cancelRecording,
    getCurrentAudioBlob,
    clearAudio,
    initialize,
    
    // Utilities
    isAvailable: isAvailable(),
    formatDuration,
    
    // Volume reference for real-time access
    getCurrentVolumeRef: () => volumeRef.current,
  };
};

export default useAudioRecorder; 