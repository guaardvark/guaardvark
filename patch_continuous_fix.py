import re

with open('frontend/src/components/voice/ContinuousVoiceChat.jsx', 'r') as f:
    content = f.read()

# Fix import
content = content.replace("import { micVAD, utils } from '@ricky0123/vad-web';",
                          "import { MicVAD, utils } from '@ricky0123/vad-web';")

# Fix startListening logic to use MicVAD.new and correct option names
# options: positiveSpeechThreshold, negativeSpeechThreshold, minSpeechMs, preSpeechPadMs, redemptionMs

old_vad_call = """      vadRef.current = await micVAD({
        stream: streamRef.current,
        positiveSpeechThreshold: 0.8,
        negativeSpeechThreshold: 0.65,
        minSpeechFrames: 3,
        preSpeechPadFrames: 10,
        redemptionFrames: 30, // ~900ms silence before segmenting
        onSpeechStart: () => {
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
        },
        onVADMisfire: () => {
          setSpeechDetected(false);
        }
      });"""

new_vad_call = """      vadRef.current = await MicVAD.new({
        stream: streamRef.current,
        positiveSpeechThreshold: 0.8,
        negativeSpeechThreshold: 0.65,
        minSpeechMs: 100, // Roughly 3 frames
        preSpeechPadMs: 300, // Roughly 10 frames
        redemptionMs: 900, // Roughly 30 frames
        onSpeechStart: () => {
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
        },
        onVADMisfire: () => {
          setSpeechDetected(false);
        }
      });"""

content = content.replace(old_vad_call, new_vad_call)

with open('frontend/src/components/voice/ContinuousVoiceChat.jsx', 'w') as f:
    f.write(content)
