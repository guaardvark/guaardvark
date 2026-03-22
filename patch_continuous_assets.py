import re

with open('frontend/src/components/voice/ContinuousVoiceChat.jsx', 'r') as f:
    content = f.read()

# Update MicVAD.new call to include asset paths and worklet URL
old_vad_call = """      vadRef.current = await MicVAD.new({
        stream: streamRef.current,
        positiveSpeechThreshold: 0.8,
        negativeSpeechThreshold: 0.65,
        minSpeechMs: 100, // Roughly 3 frames
        preSpeechPadMs: 300, // Roughly 10 frames
        redemptionMs: 900, // Roughly 30 frames
        onSpeechStart: () => {"""

new_vad_call = """      vadRef.current = await MicVAD.new({
        stream: streamRef.current,
        baseAssetPath: "/", // Public directory root
        onnxWASMBasePath: "/", // Public directory root
        positiveSpeechThreshold: 0.8,
        negativeSpeechThreshold: 0.65,
        minSpeechMs: 100,
        preSpeechPadMs: 300,
        redemptionMs: 900,
        onSpeechStart: () => {"""

content = content.replace(old_vad_call, new_vad_call)

with open('frontend/src/components/voice/ContinuousVoiceChat.jsx', 'w') as f:
    f.write(content)
