
import { useState, useCallback } from 'react';

export const useVoiceSettingsManager = (initialSettings = {}) => {
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
    enabledModels: ['ollama', 'openai', 'anthropic'],
    outputFormat: 'wav',
    compressionLevel: 'medium',
    enableAutoStop: true,
    autoStopThreshold: 2,
    enableVisualFeedback: true,
    ...initialSettings
  });

  const getAudioConstraints = useCallback(() => {
    const constraints = {
      audio: {
        echoCancellation: voiceSettings.echoCancellation,
        noiseSuppression: voiceSettings.noiseSuppression,
        autoGainControl: voiceSettings.autoGainControl,
        channelCount: 1,
        sampleRate: voiceSettings.recordingQuality === 'high' ? 48000 : 
                    voiceSettings.recordingQuality === 'medium' ? 44100 : 16000,
        volume: voiceSettings.recordingVolume
      }
    };
    return constraints;
  }, [voiceSettings]);

  const getRecordingOptions = useCallback(() => ({
    mimeType: getPreferredMimeType(),
    audioBitsPerSecond: voiceSettings.recordingQuality === 'high' ? 128000 : 
                       voiceSettings.recordingQuality === 'medium' ? 96000 : 64000
  }), [voiceSettings.recordingQuality]);

  const getPreferredMimeType = useCallback(() => {
    const types = [
      'audio/webm;codecs=opus',
      'audio/webm',
      'audio/mp4',
      'audio/ogg;codecs=opus',
      'audio/wav'
    ];
    
    for (const type of types) {
      if (MediaRecorder.isTypeSupported(type)) {
        return type;
      }
    }
    return 'audio/wav';
  }, []);

  const updateSettings = useCallback((newSettings) => {
    setVoiceSettings(prev => ({
      ...prev,
      ...newSettings
    }));
  }, []);

  const handleSettingsSave = useCallback((newSettings) => {
    console.log('VoiceSettingsManager: Saving settings:', newSettings);
    setVoiceSettings(newSettings);
    
    try {
      localStorage.setItem('guaardvark_voiceSettings', JSON.stringify(newSettings));
      window.dispatchEvent(new Event('voiceSettingsChanged'));
    } catch (error) {
      console.warn('VoiceSettingsManager: Failed to save settings to localStorage:', error);
    }
  }, []);

  const loadSettings = useCallback(() => {
    try {
      const saved = localStorage.getItem('guaardvark_voiceSettings');
      if (saved) {
        const parsed = JSON.parse(saved);
        setVoiceSettings(prev => ({
          ...prev,
          ...parsed
        }));
      }
    } catch (error) {
      console.warn('VoiceSettingsManager: Failed to load settings from localStorage:', error);
    }
  }, []);

  const getVoiceApiSettings = useCallback(() => ({
    voice: voiceSettings.voice,
    outputFormat: voiceSettings.outputFormat,
    playbackVolume: voiceSettings.playbackVolume,
    playbackSpeed: voiceSettings.playbackSpeed
  }), [voiceSettings]);

  const getRecorderSettings = useCallback(() => ({
    maxDuration: voiceSettings.maxRecordingDuration * 1000,
    autoStop: voiceSettings.enableAutoStop,
    autoStopThreshold: voiceSettings.autoStopThreshold,
    visualFeedback: voiceSettings.enableVisualFeedback,
    visualizationStyle: voiceSettings.visualizationStyle
  }), [voiceSettings]);

  const validateSettings = useCallback((settings) => {
    const errors = [];
    
    if (settings.recordingVolume < 0 || settings.recordingVolume > 2) {
      errors.push('Recording volume must be between 0 and 2');
    }
    
    if (settings.playbackVolume < 0 || settings.playbackVolume > 2) {
      errors.push('Playback volume must be between 0 and 2');
    }
    
    if (settings.playbackSpeed < 0.5 || settings.playbackSpeed > 2) {
      errors.push('Playback speed must be between 0.5 and 2');
    }
    
    if (settings.maxRecordingDuration < 5 || settings.maxRecordingDuration > 300) {
      errors.push('Max recording duration must be between 5 and 300 seconds');
    }
    
    return errors;
  }, []);

  return {
    voiceSettings,
    setVoiceSettings,
    updateSettings,
    handleSettingsSave,
    loadSettings,
    getAudioConstraints,
    getRecordingOptions,
    getPreferredMimeType,
    getVoiceApiSettings,
    getRecorderSettings,
    validateSettings
  };
};

export default useVoiceSettingsManager; 
