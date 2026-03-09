import { useState, useEffect } from 'react';

const VOICE_SETTINGS_KEY = 'guaardvark_voiceSettings';

/**
 * Shared hook for reactive voice settings from localStorage.
 * Re-reads when 'voiceSettingsChanged' window event fires.
 */
export function useVoiceSettings() {
  const [settings, setSettings] = useState(() => {
    try {
      const stored = localStorage.getItem(VOICE_SETTINGS_KEY);
      return stored ? JSON.parse(stored) : {};
    } catch {
      return {};
    }
  });

  useEffect(() => {
    const handleChange = () => {
      try {
        const stored = localStorage.getItem(VOICE_SETTINGS_KEY);
        setSettings(stored ? JSON.parse(stored) : {});
      } catch {
        setSettings({});
      }
    };
    window.addEventListener('voiceSettingsChanged', handleChange);
    return () => window.removeEventListener('voiceSettingsChanged', handleChange);
  }, []);

  return settings;
}

export default useVoiceSettings;
