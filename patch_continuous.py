import re

with open('frontend/src/components/voice/ContinuousVoiceChat.jsx', 'r') as f:
    content = f.read()

# Add imports
content = content.replace("import { checkForWakeWord } from '../../utils/wakeWordMatcher';",
                          "import { checkForWakeWord } from '../../utils/wakeWordMatcher';\nimport { micVAD, utils } from '@ricky0123/vad-web';")

# Delete old VAD functions
content = re.sub(r'const detectVoiceActivity = useCallback\(\(volume\).*?^  \}, \[isMicMuted, isAISpeaking\]\);\n\n', '', content, flags=re.MULTILINE | re.DOTALL)
content = re.sub(r'const segmentCurrentAudio = useCallback\(async \(\).*?^  \}, \[isAISpeaking, isMicMuted, incrementErrors, resetErrors\]\);\n\n', '', content, flags=re.MULTILINE | re.DOTALL)

# Find and replace startListening
start_listen_replacement = """const vadRef = useRef(null);

  const startListening = useCallback(async () => {
    try {
      console.log('ContinuousVoiceChat: Starting continuous listening mode...');
      setError(null);

      if (wakeWordEnabled) {
        listeningModeRef.current = 'passive';
        setListeningMode('passive');
      } else {
        listeningModeRef.current = 'active';
        setListeningMode('active');
      }

      await voiceService.resumeAudioContext();

      streamRef.current = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          sampleRate: 16000,
          channelCount: 1
        }
      });

      const vad = vadConfigRef.current;
      consecutiveSilenceFramesRef.current = 0;
      consecutiveSpeechFramesRef.current = 0;
      confirmedSpeakingRef.current = false;

      setIsListening(true);
      
      const analyzer = await voiceService.createAudioAnalyzer(streamRef.current);
      if (!analyzer) {
        throw new Error('Failed to initialize audio analyzer');
      }

      volumeMonitorRef.current = setInterval(() => {
        if (!isMountedRef.current) return;
        try {
          const volume = voiceService.calculateVolume();
          setCurrentVolume(volume);

          const levels = voiceService.getAudioLevels();
          if (levels && levels.length > 0) {
            const step = Math.floor(levels.length / 20);
            const sampledLevels = [];
            for (let i = 0; i < 20; i++) {
              sampledLevels.push(levels[Math.min(i * step, levels.length - 1)] || 0);
            }
            setAudioLevels(sampledLevels);
            if (sampledLevels.some(l => l > 0) && !waveformActive) {
              setWaveformActive(true);
            }
          }
        } catch (err) {}
      }, 100);

      vadRef.current = await micVAD({
        stream: streamRef.current,
        positiveSpeechThreshold: 0.8,
        negativeSpeechThreshold: 0.65,
        minSpeechFrames: 3,
        preSpeechPadFrames: 10,
        redemptionFrames: 30, // ~900ms silence before segmenting
        onSpeechStart: () => {
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
        },
        onVADMisfire: () => {
          setSpeechDetected(false);
        }
      });

      vadRef.current.start();
      console.log('ContinuousVoiceChat: Continuous listening started successfully');

    } catch (err) {
      console.error('ContinuousVoiceChat: Failed to start listening:', err);
      setError(err.message || 'Failed to start listening');
      setIsListening(false);
      onError(err);
      cleanup();
    }
  }, [enqueueAudioSegment, wakeWordEnabled, isAISpeaking, isMicMuted, onError]);"""

content = re.sub(r'const startListening = useCallback\(async \(\).*?^  \}, \[detectVoiceActivity, segmentCurrentAudio, onError, wakeWordEnabled\]\);\n', start_listen_replacement + '\n', content, flags=re.MULTILINE | re.DOTALL)

# Find and replace stopListening
stop_listen_replacement = """const stopListening = useCallback(async () => {
    console.log('ContinuousVoiceChat: Stopping continuous listening...');

    if (volumeMonitorRef.current) {
      clearInterval(volumeMonitorRef.current);
      volumeMonitorRef.current = null;
    }
    if (activeListeningTimeoutRef.current) {
      clearTimeout(activeListeningTimeoutRef.current);
      activeListeningTimeoutRef.current = null;
    }

    if (vadRef.current) {
      vadRef.current.pause();
      vadRef.current = null;
    }

    if (streamRef.current) {
      streamRef.current.getTracks().forEach(track => track.stop());
      streamRef.current = null;
    }

    setIsListening(false);
    setCurrentVolume(0);
    setSpeechDetected(false);
    setWaveformActive(false);
    setAudioLevels(new Array(20).fill(0));
    consecutiveSilenceFramesRef.current = 0;
    consecutiveSpeechFramesRef.current = 0;
    confirmedSpeakingRef.current = false;

    console.log('ContinuousVoiceChat: Stopped successfully');
  }, []);"""

content = re.sub(r'const stopListening = useCallback\(async \(\).*?^  \}, \[enqueueAudioSegment\]\);\n', stop_listen_replacement + '\n', content, flags=re.MULTILINE | re.DOTALL)

# Find and replace cleanup
cleanup_replacement = """const cleanup = useCallback(() => {
    console.log('ContinuousVoiceChat: Cleaning up resources...');

    if (volumeMonitorRef.current) clearInterval(volumeMonitorRef.current);
    if (activeListeningTimeoutRef.current) clearTimeout(activeListeningTimeoutRef.current);

    if (vadRef.current) {
      try { vadRef.current.pause(); } catch(e){}
      vadRef.current = null;
    }

    if (streamRef.current) {
      streamRef.current.getTracks().forEach(track => track.stop());
      streamRef.current = null;
    }
  }, []);"""

content = re.sub(r'const cleanup = useCallback\(\(\).*?^  \}, \[\]\);\n', cleanup_replacement + '\n', content, flags=re.MULTILINE | re.DOTALL)

# We also need to fix processAudioSegment where we don't need audioQueue anymore, but we can leave it

with open('frontend/src/components/voice/ContinuousVoiceChat.jsx', 'w') as f:
    f.write(content)
