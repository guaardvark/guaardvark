import re

with open('frontend/src/components/voice/ContinuousVoiceChat.jsx', 'r') as f:
    content = f.read()

# Replace the old onSpeechStart and onSpeechEnd with new interruptible logic
old_vad_logic = """        onSpeechStart: () => {
          setSpeechDetected(true);
        },
        onSpeechEnd: (audioFloat32) => {
          setSpeechDetected(false);
          if (isAISpeaking || isMicMuted) return;

          try {
            const wavBuffer = utils.encodeWAV(audioFloat32);
            const audioBlob = new Blob([wavBuffer], { type: "audio/wav" });
            if (audioBlob.size >= 1000) {
              enqueueAudioSegment(audioBlob);
            }
          } catch (e) {
            console.error('ContinuousVoiceChat: Error encoding WAV', e);
          }
        },"""

new_vad_logic = """        onSpeechStart: () => {
          setSpeechDetected(true);
          // Intelligent Interruption: Stop AI if it's talking
          if (voiceService.getIsTTSPlaying()) {
            console.log("ContinuousVoiceChat: Interrupting AI playback");
            voiceService.stopPlayback();
          }
        },
        onSpeechEnd: (audioFloat32) => {
          setSpeechDetected(false);
          if (isMicMuted) return;

          try {
            const wavBuffer = utils.encodeWAV(audioFloat32);
            const audioBlob = new Blob([wavBuffer], { type: "audio/wav" });
            if (audioBlob.size >= 1000) {
              enqueueAudioSegment(audioBlob);
            }
          } catch (e) {
            console.error('ContinuousVoiceChat: Error encoding WAV', e);
          }
        },"""

content = content.replace(old_vad_logic, new_vad_logic)

with open('frontend/src/components/voice/ContinuousVoiceChat.jsx', 'w') as f:
    f.write(content)

