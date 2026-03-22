# Session Summary: Vision & Voice Pipeline Overhaul

## 1. Vision Pipeline Optimizations
*   **Redundant Decoding Eliminated:** Refactored `StreamManager`, `ChangeDetector`, and `FrameAnalyzer` to share a single PIL Image object. Previously, every frame was being decoded from base64 multiple times per analysis loop, causing excessive CPU spikes.
*   **Parallel Inference Support:** Replaced the global `threading.Lock` in `FrameAnalyzer` with a `threading.Semaphore` controlled by a new `max_parallel` configuration key.
*   **Configurable Prompts:** Added `change_detected_prompt` to allow customization of how the AI describes visual changes vs. background monitoring.
*   **API Efficiency:** The `/analyze` endpoint now decodes the frame once before passing it to the inference engine.

## 2. Voice & Wake Word Enhancements
*   **Engine Upgrade (`faster-whisper`):** Switched the backend from `whisper.cpp` CLI calls to a persistent `faster-whisper` (CTranslate2) implementation. 
    *   **Result:** ~4x faster transcription and zero subprocess startup overhead.
*   **ML-based local VAD:** Integrated `@ricky0123/vad-web` (Silero VAD) in the frontend.
    *   **Result:** High-accuracy human speech detection entirely in the browser, reducing unnecessary server traffic and improving privacy.
*   **Intelligent Interruption:** Implemented a "talk-over" feature. The moment the local VAD detects speech, it triggers `voiceService.stopPlayback()`, instantly silencing the AI so the user can speak.
*   **Synthesized Earcons:** Added a suite of Web Audio API cues:
    *   **Wake:** A low-frequency sawtooth pulse.
    *   **Thinking:** A soft double-ping (played as soon as audio is dispatched).
    *   **Finished:** A gentle descending chime.

## 3. Infrastructure & Deployment Fixes
*   **Vite/WASM Interop:** Resolved complex module loading errors (`NS_ERROR_CORRUPTED_CONTENT` and `Dynamic require not supported`) by:
    *   Configuring a `resolve.alias` in `vite.config.js` to map `onnxruntime-web/wasm` to its ESM distribution.
    *   Manually deploying `.wasm`, `.mjs`, and `.onnx` assets to the `frontend/public/` directory to bypass Vite's asset optimization which was corrupting MIME types.
    *   Adjusting `optimizeDeps` to ensure proper CommonJS-to-ESM conversion for the VAD library.

## Key Files Modified
- **Backend:** `api/voice_api.py`, `utils/faster_whisper_utils.py`, `utils/vision_analyzer.py`, `plugins/vision_pipeline/service/*`
- **Frontend:** `src/api/voiceService.js`, `src/components/voice/ContinuousVoiceChat.jsx`, `vite.config.js`
