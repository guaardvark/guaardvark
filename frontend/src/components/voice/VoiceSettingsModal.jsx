import React, { useState, useEffect } from 'react';
import PropTypes from 'prop-types';
import voiceService from '../../api/voiceService';

/**
 * VoiceSettingsModal Component
 * Provides configuration options for voice chat settings
 */
const VoiceSettingsModal = ({ 
  isOpen = false, 
  onClose = () => {},
  onSave = () => {},
  initialSettings = {}
}) => {
  const [settings, setSettings] = useState({
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
    enableVisualization: true,
    autoSendEnabled: false,
    autoSendDelay: 2000,
    volumeSensitivity: 0.3,
    volumeThreshold: 0.1,
    ...initialSettings
  });

  const [availableVoices, setAvailableVoices] = useState([]);
  const [voiceStatus, setVoiceStatus] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState(null);
  const [recordingVolume, setRecordingVolume] = useState(0);
  const [isVolumeTestActive, setIsVolumeTestActive] = useState(false);

  // Load voice configuration on mount
  useEffect(() => {
    if (isOpen) {
      loadVoiceConfiguration();
    }
  }, [isOpen]);

  // Real-time volume monitoring
  useEffect(() => {
    let volumeInterval;
    
    if (isOpen && isVolumeTestActive) {
      volumeInterval = setInterval(() => {
        try {
          const volume = voiceService.calculateVolume();
          setRecordingVolume(volume);
        } catch (err) {
          console.warn('Error getting volume:', err);
          setRecordingVolume(0);
        }
      }, 100);
    }

    return () => {
      if (volumeInterval) {
        clearInterval(volumeInterval);
      }
    };
  }, [isOpen, isVolumeTestActive]);

  const loadVoiceConfiguration = async () => {
    setIsLoading(true);
    setError(null);
    
    try {
      const [status, voices] = await Promise.all([
        voiceService.getStatus(),
        voiceService.getVoices().catch(() => ({ voices: [] }))
      ]);

      setVoiceStatus(status);
      setAvailableVoices(voices.voices || voices.available_voices || []);
      // Note: models data is fetched but not currently displayed in UI
    } catch (err) {
      setError(err.message);
    } finally {
      setIsLoading(false);
    }
  };

  const handleSettingChange = (key, value) => {
    setSettings(prev => ({
      ...prev,
      [key]: value
    }));
  };

  const handleSave = () => {
    onSave(settings);
    onClose();
  };

  const handleReset = () => {
    setSettings({
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
      enableVisualization: true,
      autoSendEnabled: false,
      autoSendDelay: 2000,
      volumeSensitivity: 0.3,
      volumeThreshold: 0.1,
    });
  };

  const toggleVolumeTest = async () => {
    if (isVolumeTestActive) {
      setIsVolumeTestActive(false);
      try {
        await voiceService.stopRecording();
      } catch (err) {
        console.warn('Error stopping volume test:', err);
      }
    } else {
      try {
        await voiceService.resumeAudioContext();
        await voiceService.startRecording();
        setIsVolumeTestActive(true);
      } catch (err) {
        setError('Failed to start volume test: ' + err.message);
      }
    }
  };

  const getVolumeLevel = () => {
    const volume = recordingVolume || 0;
    const sensitivity = settings.volumeSensitivity || 0.3;
    const normalizedVolume = Math.min(volume / sensitivity, 1);
    const result = Math.max(0, normalizedVolume);
    return isNaN(result) ? 0 : result;
  };

  const getVolumeColor = () => {
    const level = getVolumeLevel();
    if (level < 0.3) return 'bg-green-500';
    if (level < 0.7) return 'bg-yellow-500';
    return 'bg-red-500';
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl max-w-2xl w-full mx-4 max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b">
          <h2 className="text-xl font-semibold text-gray-800">Voice Chat Settings</h2>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 transition-colors"
          >
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Content */}
        <div className="p-6 space-y-6">
          {/* Loading State */}
          {isLoading && (
            <div className="flex items-center justify-center py-8">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
              <span className="ml-2 text-gray-600">Loading voice settings...</span>
            </div>
          )}

          {/* Error State */}
          {error && (
            <div className="bg-red-50 border border-red-200 rounded-md p-4">
              <div className="flex">
                <svg className="w-5 h-5 text-red-400" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clipRule="evenodd" />
                </svg>
                <div className="ml-3">
                  <h3 className="text-sm font-medium text-red-800">Error loading voice settings</h3>
                  <p className="text-sm text-red-700 mt-1">{error}</p>
                </div>
              </div>
            </div>
          )}

          {/* Voice Status */}
          {voiceStatus && (
            <div className="bg-blue-50 border border-blue-200 rounded-md p-4">
              <h3 className="text-sm font-medium text-blue-800 mb-2">Voice System Status</h3>
              <div className="text-sm text-blue-700 space-y-1">
                <div>Status: <span className="font-semibold">{voiceStatus.status}</span></div>
                <div>Engine: <span className="font-semibold">{voiceStatus.engine}</span></div>
                <div>Speech Recognition: <span className="font-semibold">{voiceStatus.speech_recognition ? 'Available' : 'Unavailable'}</span></div>
                <div>Text to Speech: <span className="font-semibold">{voiceStatus.text_to_speech ? 'Available' : 'Unavailable'}</span></div>
              </div>
            </div>
          )}

          {!isLoading && !error && (
            <>
              {/* Voice Selection */}
              <div className="space-y-4">
                <h3 className="text-lg font-medium text-gray-800">Voice Selection</h3>
                
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-2">
                      Voice
                    </label>
                    <select
                      value={settings.voice}
                      onChange={(e) => handleSettingChange('voice', e.target.value)}
                      className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                    >
                      {availableVoices.map((voice) => (
                        <option key={voice.id || voice} value={voice.id || voice}>
                          {voice.name || (typeof voice === 'string' ? voice.charAt(0).toUpperCase() + voice.slice(1) : voice.id)}
                        </option>
                      ))}
                    </select>
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-2">
                      Recording Quality
                    </label>
                    <select
                      value={settings.recordingQuality}
                      onChange={(e) => handleSettingChange('recordingQuality', e.target.value)}
                      className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                    >
                      <option value="low">Low (64kbps)</option>
                      <option value="medium">Medium (128kbps)</option>
                      <option value="high">High (256kbps)</option>
                    </select>
                  </div>
                </div>
              </div>

              {/* Audio Processing */}
              <div className="space-y-4">
                <h3 className="text-lg font-medium text-gray-800">Audio Processing</h3>
                
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div className="space-y-3">
                    <label className="flex items-center">
                      <input
                        type="checkbox"
                        checked={settings.autoGainControl}
                        onChange={(e) => handleSettingChange('autoGainControl', e.target.checked)}
                        className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                      />
                      <span className="ml-2 text-sm text-gray-700">Auto Gain Control</span>
                    </label>

                    <label className="flex items-center">
                      <input
                        type="checkbox"
                        checked={settings.noiseSuppression}
                        onChange={(e) => handleSettingChange('noiseSuppression', e.target.checked)}
                        className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                      />
                      <span className="ml-2 text-sm text-gray-700">Noise Suppression</span>
                    </label>

                    <label className="flex items-center">
                      <input
                        type="checkbox"
                        checked={settings.echoCancellation}
                        onChange={(e) => handleSettingChange('echoCancellation', e.target.checked)}
                        className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                      />
                      <span className="ml-2 text-sm text-gray-700">Echo Cancellation</span>
                    </label>
                  </div>

                  <div className="space-y-3">
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">
                        Max Recording Duration (seconds)
                      </label>
                      <input
                        type="number"
                        min="10"
                        max="300"
                        value={settings.maxRecordingDuration}
                        onChange={(e) => handleSettingChange('maxRecordingDuration', parseInt(e.target.value))}
                        className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                      />
                    </div>
                  </div>
                </div>
              </div>

              {/* Recording Volume */}
              <div className="space-y-4">
                <h3 className="text-lg font-medium text-gray-800">Recording Volume</h3>
                
                <div className="space-y-4">
                  {/* Volume Test Button */}
                  <div className="flex items-center space-x-4">
                    <button
                      onClick={toggleVolumeTest}
                      className={`px-4 py-2 text-sm font-medium rounded-md transition-colors ${
                        isVolumeTestActive 
                          ? 'bg-red-600 text-white hover:bg-red-700' 
                          : 'bg-blue-600 text-white hover:bg-blue-700'
                      }`}
                    >
                      {isVolumeTestActive ? 'Stop Volume Test' : 'Start Volume Test'}
                    </button>
                    <span className="text-sm text-gray-600">
                      {isVolumeTestActive ? 'Speaking to test microphone...' : 'Test your microphone volume levels'}
                    </span>
                  </div>

                  {/* Volume Meter */}
                  <div className="space-y-2">
                    <label className="block text-sm font-medium text-gray-700">
                      Current Volume: {Math.round((recordingVolume || 0) * 100)}%
                    </label>
                    <div className="w-full bg-gray-200 rounded-full h-4 overflow-hidden">
                      <div 
                        className={`h-full transition-all duration-100 ${getVolumeColor()}`}
                        style={{ width: `${getVolumeLevel() * 100}%` }}
                      />
                    </div>
                    <div className="flex justify-between text-xs text-gray-500">
                      <span>Silent</span>
                      <span>Optimal</span>
                      <span>Too Loud</span>
                    </div>
                  </div>

                  {/* Volume Controls */}
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">
                        Volume Sensitivity: {Math.round(settings.volumeSensitivity * 100)}%
                      </label>
                      <input
                        type="range"
                        min="0.1"
                        max="1.0"
                        step="0.1"
                        value={settings.volumeSensitivity}
                        onChange={(e) => handleSettingChange('volumeSensitivity', parseFloat(e.target.value))}
                        className="w-full"
                      />
                      <p className="text-xs text-gray-500 mt-1">Adjusts how sensitive the volume meter is to input</p>
                    </div>

                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">
                        Volume Threshold: {Math.round(settings.volumeThreshold * 100)}%
                      </label>
                      <input
                        type="range"
                        min="0.01"
                        max="0.5"
                        step="0.01"
                        value={settings.volumeThreshold}
                        onChange={(e) => handleSettingChange('volumeThreshold', parseFloat(e.target.value))}
                        className="w-full"
                      />
                      <p className="text-xs text-gray-500 mt-1">Minimum volume level to trigger voice detection</p>
                    </div>
                  </div>

                  {/* Volume Status */}
                  <div className="bg-gray-50 border border-gray-200 rounded-md p-3">
                    <div className="flex items-center space-x-2">
                      <div className={`w-3 h-3 rounded-full ${
                        (recordingVolume || 0) > (settings.volumeThreshold || 0.1) ? 'bg-green-500' : 'bg-red-500'
                      }`}></div>
                      <span className="text-sm text-gray-700">
                        {(recordingVolume || 0) > (settings.volumeThreshold || 0.1) ? 'Voice detected' : 'No voice detected'}
                      </span>
                    </div>
                  </div>
                </div>
              </div>

              {/* Playback Settings */}
              <div className="space-y-4">
                <h3 className="text-lg font-medium text-gray-800">Playback Settings</h3>
                
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Volume: {Math.round(settings.playbackVolume * 100)}%
                    </label>
                    <input
                      type="range"
                      min="0"
                      max="1"
                      step="0.1"
                      value={settings.playbackVolume}
                      onChange={(e) => handleSettingChange('playbackVolume', parseFloat(e.target.value))}
                      className="w-full"
                    />
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Speed: {settings.playbackSpeed}x
                    </label>
                    <input
                      type="range"
                      min="0.5"
                      max="2"
                      step="0.1"
                      value={settings.playbackSpeed}
                      onChange={(e) => handleSettingChange('playbackSpeed', parseFloat(e.target.value))}
                      className="w-full"
                    />
                  </div>
                </div>
              </div>

              {/* Visualization Settings */}
              <div className="space-y-4">
                <h3 className="text-lg font-medium text-gray-800">Visualization</h3>
                
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div>
                    <label className="flex items-center mb-3">
                      <input
                        type="checkbox"
                        checked={settings.enableVisualization}
                        onChange={(e) => handleSettingChange('enableVisualization', e.target.checked)}
                        className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                      />
                      <span className="ml-2 text-sm text-gray-700">Enable Audio Visualization</span>
                    </label>
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-2">
                      Visualization Style
                    </label>
                    <select
                      value={settings.visualizationStyle}
                      onChange={(e) => handleSettingChange('visualizationStyle', e.target.value)}
                      disabled={!settings.enableVisualization}
                      className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-100"
                    >
                      <option value="waveform">Waveform</option>
                      <option value="bars">Frequency Bars</option>
                      <option value="circle">Circular</option>
                    </select>
                  </div>
                </div>
              </div>

              {/* Auto-send Settings */}
              <div className="space-y-4">
                <h3 className="text-lg font-medium text-gray-800">Auto-send</h3>
                
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div>
                    <label className="flex items-center mb-3">
                      <input
                        type="checkbox"
                        checked={settings.autoSendEnabled}
                        onChange={(e) => handleSettingChange('autoSendEnabled', e.target.checked)}
                        className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                      />
                      <span className="ml-2 text-sm text-gray-700">Auto-send after silence</span>
                    </label>
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Silence delay (ms)
                    </label>
                    <input
                      type="number"
                      min="500"
                      max="5000"
                      step="100"
                      value={settings.autoSendDelay}
                      onChange={(e) => handleSettingChange('autoSendDelay', parseInt(e.target.value))}
                      disabled={!settings.autoSendEnabled}
                      className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-100"
                    />
                  </div>
                </div>
              </div>
            </>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between p-6 border-t bg-gray-50">
          <button
            onClick={handleReset}
            className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            Reset to Defaults
          </button>
          
          <div className="flex space-x-3">
            <button
              onClick={onClose}
              className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              Cancel
            </button>
            <button
              onClick={handleSave}
              disabled={isLoading}
              className="px-4 py-2 text-sm font-medium text-white bg-blue-600 border border-transparent rounded-md hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
            >
              Save Settings
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

VoiceSettingsModal.propTypes = {
  isOpen: PropTypes.bool,
  onClose: PropTypes.func,
  onSave: PropTypes.func,
  initialSettings: PropTypes.object,
};

export default VoiceSettingsModal; 