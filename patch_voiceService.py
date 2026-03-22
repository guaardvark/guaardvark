import re

with open('frontend/src/api/voiceService.js', 'r') as f:
    content = f.read()

# Add stopPlayback method
stop_playback_code = """
  /**
   * Stop current TTS playback immediately
   */
  stopPlayback() {
    if (this.ttsAudioElement) {
      this.ttsAudioElement.pause();
      this.ttsAudioElement.currentTime = 0;
      this.ttsAudioElement.removeAttribute('src'); // Release the resource
      this.ttsAudioElement.load();
    }
    this.isTTSPlaying = false;
    if (this.ttsAnalyzer) {
      try {
        this.ttsAnalyzer.source.disconnect();
      } catch (e) {
        console.warn('VoiceService: Error disconnecting TTS analyzer:', e);
      }
      this.ttsAnalyzer = null;
    }
    console.log('VoiceService: Playback stopped manually');
  }

  /**
   * Check if TTS is currently playing
"""

content = content.replace("  /**\n   * Check if TTS is currently playing", stop_playback_code)

# Make sure it's exported at the bottom
if 'export const stopPlayback' not in content:
    content = content.replace("export const playAudio =", "export const stopPlayback = () => voiceService.stopPlayback();\nexport const playAudio =")

with open('frontend/src/api/voiceService.js', 'w') as f:
    f.write(content)

