import re

with open('frontend/src/components/voice/ContinuousVoiceChat.jsx', 'r') as f:
    content = f.read()

earcons_code = """
/**
 * Earcons for voice interaction feedback using Web Audio API
 */
const playEarcon = (type) => {
  try {
    const ctx = voiceService.getAudioContext();
    if (!ctx) return;
    const now = ctx.currentTime;

    if (type === 'wake') {
      // Improved "Guaardvark grunt" with random variation
      const baseFreq = 160 + Math.random() * 40;
      const osc = ctx.createOscillator();
      osc.type = 'sawtooth';
      osc.frequency.setValueAtTime(baseFreq, now);
      osc.frequency.exponentialRampToValueAtTime(baseFreq * 0.5, now + 0.2);

      const filter = ctx.createBiquadFilter();
      filter.type = 'lowpass';
      filter.frequency.setValueAtTime(800, now);
      filter.frequency.exponentialRampToValueAtTime(200, now + 0.2);
      filter.Q.value = 5;

      const gain = ctx.createGain();
      gain.gain.setValueAtTime(0, now);
      gain.gain.linearRampToValueAtTime(0.15, now + 0.03);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.2);

      osc.connect(filter);
      filter.connect(gain);
      gain.connect(ctx.destination);

      osc.start(now);
      osc.stop(now + 0.2);
    } 
    else if (type === 'thinking') {
      // Soft, high-pitched double ping
      const osc = ctx.createOscillator();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(880, now);
      osc.frequency.setValueAtTime(1108, now + 0.15); // A5 to C#6

      const gain = ctx.createGain();
      gain.gain.setValueAtTime(0, now);
      gain.gain.linearRampToValueAtTime(0.05, now + 0.05);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.1);
      
      gain.gain.setValueAtTime(0, now + 0.15);
      gain.gain.linearRampToValueAtTime(0.05, now + 0.2);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.3);

      osc.connect(gain);
      gain.connect(ctx.destination);

      osc.start(now);
      osc.stop(now + 0.35);
    }
    else if (type === 'finished') {
      // Gentle descending chime
      const osc = ctx.createOscillator();
      osc.type = 'triangle';
      osc.frequency.setValueAtTime(659.25, now); // E5
      osc.frequency.exponentialRampToValueAtTime(523.25, now + 0.3); // C5

      const filter = ctx.createBiquadFilter();
      filter.type = 'lowpass';
      filter.frequency.value = 1000;

      const gain = ctx.createGain();
      gain.gain.setValueAtTime(0, now);
      gain.gain.linearRampToValueAtTime(0.05, now + 0.05);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.4);

      osc.connect(filter);
      filter.connect(gain);
      gain.connect(ctx.destination);

      osc.start(now);
      osc.stop(now + 0.45);
    }
  } catch (e) {
    // Silently fail if audio context issues
  }
};
"""

# Replace playGuaardvarkGrunt with the new earcons code
content = re.sub(r'/\*\*\n \* Synthesize a short aardvark-like grunt.*?} catch \(e\) \{\n    // Non-critical — silently fail\n  \}\n\};\n', earcons_code, content, flags=re.MULTILINE | re.DOTALL)

# Replace playGuaardvarkGrunt() calls with playEarcon('wake')
content = content.replace('playGuaardvarkGrunt()', "playEarcon('wake')")

# Add playEarcon('thinking') when sending audio
content = re.sub(r'const enqueueAudioSegment = useCallback\(\(audioBlob\) => \{', 'const enqueueAudioSegment = useCallback((audioBlob) => {\n    playEarcon(\'thinking\');', content)

with open('frontend/src/components/voice/ContinuousVoiceChat.jsx', 'w') as f:
    f.write(content)
