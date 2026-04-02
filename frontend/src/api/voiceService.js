// frontend/src/api/voiceService.js
// Version 1.0: Voice chat API service

import { BASE_URL, handleResponse } from './apiClient';

/**
 * Voice API Service - Handles all voice-related API interactions
 * Supports streaming voice chat, status checks, and configuration
 */

class VoiceService {
  constructor() {
    this.isRecording = false;
    this.mediaRecorder = null;
    this.audioChunks = [];
    this.stream = null;
    this.onDataCallback = null;
    this.onErrorCallback = null;
    this.audioContext = null;
    this.audioAnalyzer = null;
    this.lastError = null;
    this.userInteractionAdded = false;
    this.isCleaningUp = false; // Flag to prevent duplicate cleanup calls

    // TTS visualization state
    this.isTTSPlaying = false;
    this.ttsAudioElement = null;
    this.ttsAnalyzer = null;
  }

  /**
   * Get voice API status and capabilities
   */
  async getStatus() {
    try {
      const response = await fetch(`${BASE_URL}/voice/status`);
      return await handleResponse(response);
    } catch (error) {
      console.error('Failed to get voice status:', error);
      throw error;
    }
  }

  /**
   * Get available voices
   */
  async getVoices() {
    try {
      const response = await fetch(`${BASE_URL}/voice/voices`);
      return await handleResponse(response);
    } catch (error) {
      console.error('Failed to get voices:', error);
      throw error;
    }
  }

  /**
   * Get available models
   */
  async getModels() {
    try {
      const response = await fetch(`${BASE_URL}/voice/models`);
      return await handleResponse(response);
    } catch (error) {
      console.error('Failed to get models:', error);
      throw error;
    }
  }

  /**
   * Generate narration audio from a multi-section script.
   * @param {string|string[]} script - Full script text or array of sections
   * @param {Object} options - Optional settings
   * @param {string} options.voice - Voice model name (default: 'libritts')
   * @param {number} options.speed - Playback speed (default: 1.0)
   * @param {number} options.pause_between_sections - Silence gap in seconds (default: 1.0)
   * @param {string} options.output_format - 'wav' or 'mp3' (default: 'wav')
   * @returns {Promise<{audio_url, filename, duration_seconds, sections, voice}>}
   */
  async narrate(script, options = {}) {
    try {
      const body = {
        script,
        voice: options.voice || 'libritts',
        speed: options.speed || 1.0,
        pause_between_sections: options.pause_between_sections || 1.0,
        output_format: options.output_format || 'wav',
      };
      if (options.engine) body.engine = options.engine;

      const response = await fetch(`${BASE_URL}/voice/narrate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      return await handleResponse(response);
    } catch (error) {
      console.error('Failed to generate narration:', error);
      throw error;
    }
  }

  /**
   * Get available Bark TTS voices (expressive engine)
   */
  async getBarkVoices() {
    try {
      const response = await fetch(`${BASE_URL}/voice/bark-voices`);
      return await handleResponse(response);
    } catch (error) {
      console.error('Failed to get Bark voices:', error);
      throw error;
    }
  }

  /**
   * PERFORMANCE MONITORING: Get system status
   */
  async getSystemStatus() {
    try {
      const response = await fetch(`${BASE_URL}/voice/system-status`);
      return await handleResponse(response);
    } catch (error) {
      console.error('Failed to get system status:', error);
      throw error;
    }
  }

  /**
   * EMERGENCY KILL SWITCH: Kill all LLM processes
   */
  async killAllProcesses() {
    try {
      const response = await fetch(`${BASE_URL}/voice/kill-all-processes`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
      });
      return await handleResponse(response);
    } catch (error) {
      console.error('Failed to kill processes:', error);
      throw error;
    }
  }

  /**
   * Cleanup dead processes
   */
  async cleanupProcesses() {
    try {
      const response = await fetch(`${BASE_URL}/voice/cleanup-processes`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
      });
      return await handleResponse(response);
    } catch (error) {
      console.error('Failed to cleanup processes:', error);
      throw error;
    }
  }

  /**
   * Stream voice chat - send audio and get response
   */
  async streamVoiceChat(audioBlob, sessionId = 'default') {
    try {
      const formData = new FormData();
      formData.append('audio', audioBlob, 'audio.webm');
      formData.append('session_id', sessionId);

      const response = await fetch(`${BASE_URL}/voice/stream`, {
        method: 'POST',
        body: formData,
        // Don't set Content-Type header - let browser set it with boundary
      });

      return await handleResponse(response);
    } catch (error) {
      console.error('Failed to stream voice chat:', error);
      throw error;
    }
  }

  /**
   * Convert text to speech
   */
  async textToSpeech(text, voice = 'libritts', engine = null) {
    try {
      const body = { text, voice };
      if (engine) body.engine = engine;

      const response = await fetch(`${BASE_URL}/voice/text-to-speech`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(body),
      });

      return await handleResponse(response);
    } catch (error) {
      console.error('Failed to convert text to speech:', error);
      throw error;
    }
  }

  /**
   * Convert speech to text
   */
  async speechToText(audioBlob) {
    try {
      const formData = new FormData();
      formData.append('audio', audioBlob, 'audio.webm');

      const response = await fetch(`${BASE_URL}/voice/speech-to-text`, {
        method: 'POST',
        body: formData,
        // Don't set Content-Type header - let browser set it with boundary
      });

      return await handleResponse(response);
    } catch (error) {
      console.error('Failed to convert speech to text:', error);
      throw error;
    }
  }

  /**
   * Start audio recording
   */
  async startRecording(options = {}) {
    try {
      // Request microphone access
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          sampleRate: 16000,
          ...options.audio
        }
      });

      // Determine the best MIME type
      const mimeTypes = [
        'audio/webm;codecs=opus',
        'audio/webm',
        'audio/mp4',
        'audio/ogg;codecs=opus',
        'audio/wav'
      ];

      let mimeType = 'audio/webm';
      for (const type of mimeTypes) {
        if (MediaRecorder.isTypeSupported(type)) {
          mimeType = type;
          break;
        }
      }

      // Create MediaRecorder
      this.mediaRecorder = new MediaRecorder(this.stream, {
        mimeType,
        audioBitsPerSecond: 128000,
        ...options.recorder
      });

      this.audioChunks = [];
      this.isRecording = true;

      // Set up event handlers
      this.mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          this.audioChunks.push(event.data);
          if (this.onDataCallback) {
            this.onDataCallback(event.data);
          }
        }
      };

      this.mediaRecorder.onstop = () => {
        this.isRecording = false;
        if (this.stream) {
          this.stream.getTracks().forEach(track => track.stop());
          this.stream = null;
        }
      };

      this.mediaRecorder.onerror = (error) => {
        console.error('MediaRecorder error:', error);
        this.isRecording = false;
        if (this.onErrorCallback) {
          this.onErrorCallback(error);
        }
      };

      // Start recording
      this.mediaRecorder.start(options.timeslice || 1000); // 1 second chunks by default

      // Ensure audio analyzer is fully connected before returning
      console.log('VoiceService: Setting up audio analyzer for volume detection...');
      const analyzer = await this.createAudioAnalyzer(this.stream);
      if (!analyzer) {
        console.warn('VoiceService: Failed to setup audio analyzer - volume detection may not work');
      } else {
        console.log('VoiceService: Audio analyzer setup complete and connected');
        
        // Wait a brief moment for the analyzer to fully connect and stabilize
        await new Promise(resolve => setTimeout(resolve, 200));
      }

      return {
        mimeType,
        stream: this.stream,
        recorder: this.mediaRecorder,
        analyzer: analyzer
      };
    } catch (error) {
      console.error('Failed to start recording:', error);
      this.isRecording = false;
      throw error;
    }
  }

  /**
   * Stop audio recording and return the audio blob
   */
  async stopRecording() {
    return new Promise((resolve, reject) => {
      if (!this.mediaRecorder || !this.isRecording) {
        reject(new Error('No active recording'));
        return;
      }

      this.mediaRecorder.onstop = () => {
        this.isRecording = false;
        
        // Create blob from chunks first
        const audioBlob = new Blob(this.audioChunks, { 
          type: this.mediaRecorder.mimeType 
        });
        
        this.audioChunks = [];
        
        // Delay stream cleanup to allow final audio processing
        setTimeout(() => {
          // Disconnect audio analyzer first to prevent conflicts
          if (this.audioAnalyzer) {
            try {
              this.audioAnalyzer.source.disconnect();
              this.audioAnalyzer = null;
              console.log('VoiceService: Audio analyzer disconnected before stream cleanup');
            } catch (error) {
              console.warn('VoiceService: Failed to disconnect audio analyzer:', error);
            }
          }
          
          // Stop all tracks after analyzer is disconnected
          if (this.stream) {
            this.stream.getTracks().forEach(track => track.stop());
            this.stream = null;
            console.log('VoiceService: Stream tracks stopped after delay');
          }
        }, 100); // 100ms delay to ensure final processing completes
        
        resolve(audioBlob);
      };

      this.mediaRecorder.stop();
    });
  }

  /**
   * Get current audio blob without stopping recording
   */
  getCurrentAudioBlob() {
    if (this.audioChunks.length === 0) {
      return null;
    }

    return new Blob(this.audioChunks, { 
      type: this.mediaRecorder?.mimeType || 'audio/webm' 
    });
  }

  /**
   * Set data callback for real-time audio data
   */
  setOnDataCallback(callback) {
    this.onDataCallback = callback;
  }

  /**
   * Set error callback
   */
  setOnErrorCallback(callback) {
    this.onErrorCallback = callback;
  }

  /**
   * Get current error state
   */
  getLastError() {
    return this.lastError;
  }

  /**
   * Set and propagate error
   */
  setError(error) {
    this.lastError = error;
    if (this.onErrorCallback) {
      this.onErrorCallback(error);
    }
  }

  /**
   * Check if recording is active
   */
  getIsRecording() {
    return this.isRecording;
  }

  /**
   * Get audio stream for visualization
   */
  getAudioStream() {
    return this.stream;
  }

  /**
   * Check microphone permissions
   */
  async checkMicrophonePermission() {
    try {
      if (!navigator.permissions || !navigator.permissions.query) {
        return 'unknown';
      }
      const permission = await navigator.permissions.query({ name: 'microphone' });
      return permission.state;
    } catch (error) {
      console.warn('Could not check microphone permission:', error);
      return 'unknown';
    }
  }

  /**
   * Request microphone permission
   */
  async requestMicrophonePermission() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      stream.getTracks().forEach(track => track.stop());
      return 'granted';
    } catch (error) {
      console.error('Microphone permission denied:', error);
      return 'denied';
    }
  }

  /**
   * Play audio from URL with optional visualization support
   */
  async playAudio(audioUrl, options = {}) {
    // Ensure AudioContext is running before routing audio through it.
    // resume() is async; not awaiting it causes the context to remain suspended
    // when audio.play() fires, resulting in silent playback.
    if (this.audioContext && this.audioContext.state === 'suspended') {
      try {
        await this.audioContext.resume();
        console.log('VoiceService: AudioContext resumed before playback, state:', this.audioContext.state);
      } catch (e) {
        console.warn('VoiceService: Could not resume AudioContext before playback:', e);
      }
    }

    return new Promise((resolve, reject) => {
      const audio = new Audio(audioUrl);
      audio.crossOrigin = 'anonymous'; // Required for Web Audio API analysis

      audio.onload = () => {
        console.log('Audio loaded successfully');
      };

      audio.oncanplaythrough = () => {
        console.log('Audio can play through');
      };

      audio.onended = () => {
        console.log('Audio playback ended');
        this.isTTSPlaying = false;
        this.ttsAudioElement = null;
        // Clean up TTS analyzer
        if (this.ttsAnalyzer) {
          try {
            this.ttsAnalyzer.source.disconnect();
          } catch (e) {
            console.warn('VoiceService: Error disconnecting TTS analyzer:', e);
          }
          this.ttsAnalyzer = null;
        }
        resolve();
      };

      audio.onerror = (error) => {
        console.error('Audio playback error:', error);
        this.isTTSPlaying = false;
        this.ttsAudioElement = null;
        reject(error);
      };

      // Set options
      if (options.volume !== undefined) {
        audio.volume = Math.max(0, Math.min(1, options.volume));
      }

      if (options.playbackRate !== undefined) {
        audio.playbackRate = Math.max(0.25, Math.min(4, options.playbackRate));
      }

      // Store reference for TTS visualization
      this.ttsAudioElement = audio;
      this.isTTSPlaying = true;

      // Setup TTS audio analyzer for visualization if enabled
      if (options.enableVisualization !== false) {
        this.setupTTSAnalyzer(audio);
      }

      // Play the audio
      audio.play().catch((err) => {
        this.isTTSPlaying = false;
        this.ttsAudioElement = null;
        reject(err);
      });
    });
  }

  /**
   * Setup audio analyzer for TTS output visualization
   */
  setupTTSAnalyzer(audioElement) {
    try {
      const audioContext = this.getAudioContext();
      if (!audioContext) {
        console.warn('VoiceService: Cannot setup TTS analyzer - no audio context');
        return null;
      }

      // Note: resume() is async but we can't await here (synchronous method).
      // The pre-flight resume in playAudio() handles this before we get here.
      // This is a best-effort resume for edge cases.
      if (audioContext.state === 'suspended') {
        audioContext.resume().catch(e =>
          console.warn('VoiceService: setupTTSAnalyzer resume failed:', e)
        );
      }

      // Create analyzer for TTS audio
      const analyzer = audioContext.createAnalyser();
      analyzer.fftSize = 512;
      analyzer.smoothingTimeConstant = 0.3;
      analyzer.minDecibels = -90;
      analyzer.maxDecibels = -10;

      // Create source from audio element
      const source = audioContext.createMediaElementSource(audioElement);
      source.connect(analyzer);
      analyzer.connect(audioContext.destination);

      this.ttsAnalyzer = {
        analyzer,
        source,
        bufferLength: analyzer.frequencyBinCount,
        dataArray: new Uint8Array(analyzer.frequencyBinCount),
        timeArray: new Uint8Array(analyzer.fftSize),
      };

      console.log('VoiceService: TTS audio analyzer setup successfully');
      return this.ttsAnalyzer;
    } catch (error) {
      console.error('VoiceService: Failed to setup TTS analyzer:', error);
      return null;
    }
  }

  /**
   * Get TTS audio levels for visualization
   */
  getTTSAudioLevels() {
    if (!this.ttsAnalyzer || !this.isTTSPlaying) {
      return [];
    }

    try {
      const { analyzer, dataArray } = this.ttsAnalyzer;
      analyzer.getByteFrequencyData(dataArray);
      const levels = Array.from(dataArray).map(value => value / 255);
      return levels;
    } catch (error) {
      console.warn('VoiceService: Error getting TTS audio levels:', error);
      return [];
    }
  }

  /**
   * Calculate TTS volume for visualization
   */
  calculateTTSVolume() {
    if (!this.ttsAnalyzer || !this.isTTSPlaying) {
      return 0;
    }

    try {
      const { analyzer, timeArray } = this.ttsAnalyzer;
      analyzer.getByteTimeDomainData(timeArray);

      let sum = 0;
      for (let i = 0; i < timeArray.length; i++) {
        const sample = (timeArray[i] - 128) / 128;
        sum += sample * sample;
      }
      const rms = Math.sqrt(sum / timeArray.length);
      return Math.min(1, rms * 2);
    } catch (error) {
      return 0;
    }
  }

  /**
   * Check if TTS is currently playing
   */
  getIsTTSPlaying() {
    return this.isTTSPlaying || false;
  }

  /**
   * Stop current TTS playback immediately.
   * Used for talk-over interruption — when the user starts speaking
   * while the AI is still talking, silence the AI instantly.
   */
  stopPlayback() {
    if (this.ttsAudioElement) {
      try {
        this.ttsAudioElement.pause();
        this.ttsAudioElement.currentTime = 0;
        this.ttsAudioElement.removeAttribute('src');
        this.ttsAudioElement.load();
      } catch (e) {
        // Ignore cleanup errors
      }
      this.ttsAudioElement = null;
      this.isTTSPlaying = false;
    }
  }

  /**
   * Create audio URL from blob
   */
  createAudioUrl(audioBlob) {
    return URL.createObjectURL(audioBlob);
  }

  /**
   * Cleanup audio URL
   */
  cleanupAudioUrl(audioUrl) {
    if (audioUrl) {
      URL.revokeObjectURL(audioUrl);
    }
  }

  /**
   * Get audio context for visualization - unified method with proper lifecycle
   */
  getAudioContext() {
    if (!this.audioContext) {
      try {
        this.audioContext = new (window.AudioContext || window.webkitAudioContext)();
        console.log('VoiceService: Created new audio context, state:', this.audioContext.state);
        
        // Add user interaction handler if context is suspended
        if (this.audioContext.state === 'suspended') {
          console.log('VoiceService: Audio context suspended - requires user interaction');
          this.addUserInteractionHandler();
        }
      } catch (error) {
        console.error('VoiceService: Failed to create audio context:', error);
        
        // Propagate error to UI
        if (this.onErrorCallback) {
          this.onErrorCallback(error);
        }
        
        return null;
      }
    }
    
    return this.audioContext;
  }

  /**
   * Add event listeners to resume audio context on user interaction
   */
  addUserInteractionHandler() {
    if (this.userInteractionAdded) return;
    
    const resumeAudioContext = () => {
      if (this.audioContext && this.audioContext.state === 'suspended') {
        console.log('VoiceService: User interaction detected, resuming audio context');
        this.audioContext.resume().then(() => {
          console.log('VoiceService: Audio context resumed successfully');
        }).catch(err => {
          console.warn('VoiceService: Failed to resume audio context on user interaction:', err);
        });
      }
    };

    // Add listeners for common user interactions
    const events = ['click', 'keydown', 'touchstart'];
    events.forEach(event => {
      document.addEventListener(event, resumeAudioContext, { once: true, passive: true });
    });
    
    this.userInteractionAdded = true;
    console.log('VoiceService: Added user interaction handlers for audio context resume');
  }

  /**
   * Resume audio context - must be called from user interaction
   */
  async resumeAudioContext() {
    const audioContext = this.getAudioContext();
    if (audioContext.state === 'suspended') {
      console.log('VoiceService: Resuming suspended audio context...');
      try {
        await audioContext.resume();
        console.log('VoiceService: Audio context resumed, state:', audioContext.state);
        return true;
      } catch (error) {
        console.error('VoiceService: Failed to resume audio context:', error);
        
        // Propagate error to UI
        if (this.onErrorCallback) {
          this.onErrorCallback(error);
        }
        
        return false;
      }
    }
    return true;
  }

  /**
   * Create audio analyzer for visualization - unified method with proper stream connection
   */
  async createAudioAnalyzer(stream) {
    try {
      console.log('VoiceService: Creating audio analyzer for stream:', stream);
      
      if (!stream) {
        const error = new Error('VoiceService: No stream provided to createAudioAnalyzer');
        console.error(error.message);
        if (this.onErrorCallback) {
          this.onErrorCallback(error);
        }
        return null;
      }
      
      // ENHANCED: Validate stream has active tracks
      const audioTracks = stream.getAudioTracks();
      if (audioTracks.length === 0) {
        console.error('VoiceService: Stream has no audio tracks');
        return null;
      }
      
      const activeTrack = audioTracks.find(track => track.readyState === 'live');
      if (!activeTrack) {
        console.error('VoiceService: No active audio tracks in stream');
        return null;
      }
      
      console.log('VoiceService: Stream validation passed - active audio track found:', {
        trackId: activeTrack.id,
        label: activeTrack.label,
        readyState: activeTrack.readyState,
        enabled: activeTrack.enabled
      });
      
      const audioContext = this.getAudioContext();
      
      if (!audioContext) {
        const error = new Error('VoiceService: Failed to get audio context');
        console.error(error.message);
        if (this.onErrorCallback) {
          this.onErrorCallback(error);
        }
        return null;
      }
      
      // ENHANCED: Validate audio context state and ensure it's running
      if (audioContext.state !== 'running') {
        console.warn('VoiceService: Audio context not running:', audioContext.state);
        // Try to resume it synchronously to ensure analyzer gets proper data
        try {
          await audioContext.resume();
          console.log('VoiceService: Audio context resumed successfully');
        } catch (err) {
          console.error('VoiceService: Failed to resume audio context:', err);
          // Return null if we can't get the audio context running
          return null;
        }
      }
      
      // Clean up existing analyzer
      if (this.audioAnalyzer) {
        try {
          this.audioAnalyzer.source.disconnect();
          console.log('VoiceService: Cleaned up existing audio analyzer');
        } catch (e) {
          console.warn('VoiceService: Failed to disconnect existing analyzer:', e);
        }
      }
      
      // Create new analyzer with optimized settings for volume detection
      const analyzer = audioContext.createAnalyser();
      analyzer.fftSize = 512; // Better resolution for volume detection
      analyzer.smoothingTimeConstant = 0.3; // More responsive for real-time volume
      analyzer.minDecibels = -90;
      analyzer.maxDecibels = -10;
      
      // ENHANCED: Validate analyzer creation
      if (!analyzer || analyzer.fftSize === 0) {
        console.error('VoiceService: Failed to create valid audio analyzer');
        return null;
      }
      
      // Connect stream to analyzer with validation
      console.log('VoiceService: Connecting stream to analyzer');
      const source = audioContext.createMediaStreamSource(stream);
      
      // ENHANCED: Validate stream connection
      if (!source) {
        throw new Error('VoiceService: Failed to create media stream source');
      }
      
      // ENHANCED: Add connection validation
      console.log('VoiceService: Created media stream source, connecting to analyzer...');
      source.connect(analyzer);
      console.log('VoiceService: Stream connected to analyzer successfully');
      
      // ENHANCED: Test the connection with proper timing
      const testDataArray = new Uint8Array(analyzer.frequencyBinCount);
      const testTimeArray = new Uint8Array(analyzer.fftSize);
      
      // Give the analyzer more time to start receiving data and wait for audio context to be fully running
      setTimeout(() => {
        analyzer.getByteFrequencyData(testDataArray);
        analyzer.getByteTimeDomainData(testTimeArray);
        
        const hasFrequencyData = testDataArray.some(value => value > 0);
        const hasTimeData = testTimeArray.some(value => value !== 128);
        const hasData = hasFrequencyData || hasTimeData;
        
        console.log('VoiceService: Audio analyzer validation:', {
          audioContextState: audioContext.state,
          hasFrequencyData,
          hasTimeData,
          hasData,
          fftSize: analyzer.fftSize,
          frequencyBinCount: analyzer.frequencyBinCount,
          streamActive: stream.active,
          trackCount: stream.getAudioTracks().length
        });
        
        if (!hasData) {
          console.warn('VoiceService: Audio analyzer not receiving data - volume detection may not work');
          console.log('VoiceService: Troubleshooting - Check microphone permissions and user interaction');
        } else {
          console.log('VoiceService: Audio analyzer receiving data successfully');
        }
      }, 300);
      
      this.audioAnalyzer = {
        analyzer,
        source,
        bufferLength: analyzer.frequencyBinCount,
        dataArray: new Uint8Array(analyzer.frequencyBinCount),
        timeArray: new Uint8Array(analyzer.fftSize),
        stream: stream // Store reference to stream
      };
      
      console.log('VoiceService: Audio analyzer created successfully');
      console.log('VoiceService: FFT Size:', analyzer.fftSize);
      console.log('VoiceService: Frequency bin count:', analyzer.frequencyBinCount);
      
      return this.audioAnalyzer;
    } catch (error) {
      console.error('VoiceService: Failed to create audio analyzer:', error);
      if (this.onErrorCallback) {
        this.onErrorCallback(error);
      }
      return null;
    }
  }

  /**
   * Get current audio analyzer
   */
  getAudioAnalyzer() {
    return this.audioAnalyzer;
  }

  /**
   * Calculate volume from audio analyzer
   */
  calculateVolume() {
    if (!this.audioAnalyzer) {
      console.warn('VoiceService: No audio analyzer available for volume calculation');
      return 0;
    }

    try {
      const { analyzer, dataArray, timeArray } = this.audioAnalyzer;
      
      if (!analyzer || !dataArray || !timeArray) {
        console.warn('VoiceService: Invalid audio analyzer components');
        return 0;
      }
      
      // ENHANCED: Add analyzer state checking
      if (analyzer.fftSize === 0) {
        console.warn('VoiceService: Audio analyzer not properly initialized (fftSize = 0)');
        return 0;
      }
      
      // Get frequency data for visualization
      analyzer.getByteFrequencyData(dataArray);
      
      // Get time domain data for volume calculation
      analyzer.getByteTimeDomainData(timeArray);
      
      // ENHANCED: Validate data arrays have content
      const hasFrequencyData = dataArray.some(value => value > 0);
      const hasTimeData = timeArray.some(value => value !== 128); // 128 is silence in time domain
      
      if (!hasFrequencyData && !hasTimeData) {
        // This suggests audio context issues or microphone permissions
        const audioContext = this.getAudioContext();
        console.warn('VoiceService: No audio data detected in analyzer - check stream connection', {
          audioContextState: audioContext?.state,
          analyzerExists: !!this.audioAnalyzer,
          streamActive: this.audioAnalyzer?.stream?.active
        });
        
        // Try to resume audio context if suspended
        if (audioContext && audioContext.state === 'suspended') {
          console.log('VoiceService: Attempting to resume suspended audio context');
          audioContext.resume().catch(err => 
            console.warn('VoiceService: Failed to resume audio context:', err)
          );
        }
        
        return 0;
      }
      
      // Method 1: RMS of time domain data (more reliable for volume)
      let sum = 0;
      let validSamples = 0;
      for (let i = 0; i < timeArray.length; i++) {
        const sample = (timeArray[i] - 128) / 128; // Convert to -1 to 1 range
        if (!isNaN(sample) && isFinite(sample)) {
          sum += sample * sample;
          validSamples++;
        }
      }
      
      if (validSamples === 0) {
        console.warn('VoiceService: No valid audio samples in time domain data');
        return 0;
      }
      
      const rms = Math.sqrt(sum / validSamples);
      
      // Method 2: Average frequency magnitude (backup method)
      let freqSum = 0;
      let validFreqSamples = 0;
      for (let i = 0; i < dataArray.length; i++) {
        if (!isNaN(dataArray[i]) && isFinite(dataArray[i])) {
          freqSum += dataArray[i];
          validFreqSamples++;
        }
      }
      
      const avgFreq = validFreqSamples > 0 ? freqSum / validFreqSamples / 255 : 0;
      
      // ENHANCED: Combine both methods with better weighting
      const combinedVolume = Math.max(rms * 1.5, avgFreq * 0.8); // Favor RMS method
      const volumeLevel = Math.min(1, Math.max(0, combinedVolume));
      
      // ENHANCED: Better validation and logging
      if (isNaN(volumeLevel) || volumeLevel < 0 || volumeLevel > 1) {
        console.warn('VoiceService: Invalid volume level calculated:', {
          volumeLevel,
          rms,
          avgFreq,
          combinedVolume,
          validSamples,
          validFreqSamples
        });
        return 0;
      }
      
      // ENHANCED: Add periodic detailed logging for debugging
      if (Math.random() < 0.05) { // 5% chance for detailed logging
        console.log('VoiceService: Volume calculation details:', {
          volumeLevel: (volumeLevel || 0).toFixed(4),
          rms: (rms || 0).toFixed(4),
          avgFreq: (avgFreq || 0).toFixed(4),
          combinedVolume: (combinedVolume || 0).toFixed(4),
          hasFrequencyData,
          hasTimeData,
          validSamples,
          validFreqSamples,
          fftSize: analyzer.fftSize,
          frequencyBinCount: analyzer.frequencyBinCount
        });
      }
      
      return volumeLevel;
    } catch (error) {
      console.error('VoiceService: Volume calculation error:', error);
      if (this.onErrorCallback) {
        this.onErrorCallback(error);
      }
      return 0;
    }
  }

  /**
   * Get audio levels for visualization
   */
  getAudioLevels() {
    if (!this.audioAnalyzer) {
      console.warn('VoiceService: No audio analyzer available for audio levels');
      return [];
    }

    try {
      const { analyzer, dataArray } = this.audioAnalyzer;
      
      if (!analyzer || !dataArray) {
        console.warn('VoiceService: Invalid audio analyzer components for audio levels');
        return [];
      }
      
      analyzer.getByteFrequencyData(dataArray);
      const levels = Array.from(dataArray).map(value => value / 255);
      
      // Validate levels
      if (levels.some(level => isNaN(level) || level < 0 || level > 1)) {
        console.warn('VoiceService: Invalid audio levels detected');
        return [];
      }
      
      return levels;
    } catch (error) {
      console.error('VoiceService: Audio levels error:', error);
      if (this.onErrorCallback) {
        this.onErrorCallback(error);
      }
      return [];
    }
  }

  /**
   * Cleanup resources with proper lifecycle management
   */
  cleanup() {
    // Prevent duplicate cleanup calls
    if (this.isCleaningUp) {
      return;
    }
    this.isCleaningUp = true;
    
    console.log('VoiceService: Starting cleanup...');
    
    if (this.isRecording) {
      console.log('VoiceService: Stopping recording during cleanup');
      this.stopRecording().catch(console.error);
    }
    
    if (this.stream) {
      console.log('VoiceService: Stopping media stream tracks');
      this.stream.getTracks().forEach(track => track.stop());
      this.stream = null;
    }
    
    if (this.audioAnalyzer) {
      console.log('VoiceService: Disconnecting audio analyzer');
      try {
        this.audioAnalyzer.source.disconnect();
      } catch (error) {
        console.warn('VoiceService: Failed to disconnect audio analyzer:', error);
      }
      this.audioAnalyzer = null;
    }
    
    // Don't close audio context immediately - let it be reused
    // Audio context will be closed when the page unloads or by browser GC
    if (this.audioContext) {
      console.log('VoiceService: Suspending audio context (not closing for reuse)');
      try {
        this.audioContext.suspend();
      } catch (error) {
        console.warn('VoiceService: Failed to suspend audio context:', error);
      }
      // Don't set to null - keep for reuse
    }
    
    this.audioChunks = [];
    this.onDataCallback = null;
    this.onErrorCallback = null;
    
    // Reset user interaction handler flag so it can be re-added if needed
    this.userInteractionAdded = false;
    
    console.log('VoiceService: Cleanup completed');
    this.isCleaningUp = false;
  }

  /**
   * Force cleanup - closes audio context completely
   */
  forceCleanup() {
    this.cleanup();

    if (this.audioContext) {
      console.log('VoiceService: Force closing audio context');
      try {
        this.audioContext.close();
      } catch (error) {
        console.warn('VoiceService: Failed to close audio context:', error);
      }
      this.audioContext = null;
    }
  }

  /**
   * Get detailed voice models status including installation state
   */
  async getVoiceModelsStatus() {
    try {
      const response = await fetch(`${BASE_URL}/voice/voice-models-status`);
      return await handleResponse(response);
    } catch (error) {
      console.error('Failed to get voice models status:', error);
      throw error;
    }
  }

  /**
   * Install a voice model from HuggingFace
   * @param {string} voiceId - The voice ID to install (e.g., "libritts", "ryan")
   * @returns {Promise<object>} - Installation result
   */
  async installVoiceModel(voiceId = 'libritts') {
    try {
      const response = await fetch(`${BASE_URL}/voice/install-voice-model`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ voice_id: voiceId }),
      });
      return await handleResponse(response);
    } catch (error) {
      console.error('Failed to install voice model:', error);
      throw error;
    }
  }

  /**
   * Install Whisper.cpp (clone from GitHub and build from source)
   * This enables speech recognition on machines without pre-installed whisper.
   * @returns {Promise<object>} - Installation result
   */
  async installWhisper() {
    try {
      const response = await fetch(`${BASE_URL}/voice/install-whisper`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
      });
      return await handleResponse(response);
    } catch (error) {
      console.error('Failed to install Whisper.cpp:', error);
      throw error;
    }
  }

  /**
   * Install a Whisper speech recognition model
   * @param {string} modelId - The model ID to download (e.g., "tiny.en", "base")
   * @returns {Promise<object>} - Installation result
   */
  async installWhisperModel(modelId = 'tiny.en') {
    try {
      const response = await fetch(`${BASE_URL}/voice/install-whisper-model`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ model_id: modelId }),
      });
      return await handleResponse(response);
    } catch (error) {
      console.error('Failed to install whisper model:', error);
      throw error;
    }
  }
}

// Create singleton instance
const voiceService = new VoiceService();

// Named exports for backward compatibility
export const speechToText = (audioBlob) => voiceService.speechToText(audioBlob);
export const textToSpeech = (text, voice = 'libritts') => voiceService.textToSpeech(text, voice);
export const getAvailableVoices = () => voiceService.getVoices();
export const getVoiceStatus = () => voiceService.getStatus();
export const playAudio = (audioUrl, options = {}) => voiceService.playAudio(audioUrl, options);
export const getVoiceModelsStatus = () => voiceService.getVoiceModelsStatus();
export const installVoiceModel = (voiceId) => voiceService.installVoiceModel(voiceId);
export const installWhisper = () => voiceService.installWhisper();
export const installWhisperModel = (modelId) => voiceService.installWhisperModel(modelId);

export default voiceService;