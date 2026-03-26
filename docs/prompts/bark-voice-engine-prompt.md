# Bark Voice Engine Integration — Session Prompt

Paste this into a new Claude Code session:

---

## Task

Add Bark (by Suno) as a second TTS engine alongside Piper in Guaardvark. Bark can do expressive speech — laughs, emotions, dramatic delivery, singing, sound effects — things Piper can't do.

## Context

- Guaardvark is a self-hosted AI workstation. See `/home/llamax1/LLAMAX7/CLAUDE.md` for full project context.
- The existing voice API is at `backend/api/voice_api.py` — it has Piper TTS working via `POST /api/voice/text-to-speech` and a new `POST /api/voice/narrate` endpoint.
- Piper voices are defined in `PIPER_VOICES` dict in `voice_api.py` around line 744.
- The narrate endpoint at line ~1412 generates multi-section narration with silence gaps via pydub.
- Frontend voice service is at `frontend/src/api/voiceService.js`.
- The `NarrateButton` component is at `frontend/src/components/common/NarrateButton.jsx`.
- GPU: single 16GB VRAM. Bark needs GPU. Must evict other models (Ollama) before loading Bark, and unload Bark immediately after generation. See memory file `feedback_gpu_vram_management.md` for the pattern.

## What to Build

### Backend

1. **Install Bark:** `pip install git+https://github.com/suno-ai/bark.git` (or latest stable). Add to `requirements.txt`.

2. **Create `backend/services/bark_tts_service.py`** — a service that:
   - Loads Bark model on-demand (lazy loading, not at startup)
   - Evicts Ollama first (`requests.post("http://localhost:11434/api/generate", json={"model": "current_model", "keep_alive": 0})`)
   - Generates speech from text with Bark's speaker presets
   - Supports Bark's special tokens: `[laughter]`, `[sighs]`, `[gasps]`, `[clears throat]`, `...` for hesitation
   - Unloads model immediately after generation (delete references, `torch.cuda.empty_cache()`, `gc.collect()`)
   - Returns WAV audio bytes

3. **Add Bark voices to voice_api.py:**
   - Add a `BARK_VOICES` dict with Bark's speaker presets (v2/en_speaker_0 through v2/en_speaker_9, plus any notable ones)
   - Add `engine` parameter to `/text-to-speech` and `/narrate` endpoints: `"piper"` (default, fast) or `"bark"` (expressive, slower)
   - When engine=bark, route through bark_tts_service instead of Piper subprocess

4. **Add endpoint `POST /api/voice/bark-voices`** — returns available Bark speaker presets with descriptions.

### Frontend

5. **Update `NarrateButton.jsx`** — add optional engine toggle (small chip/switch: "Fast" vs "Expressive")
6. **Update `voiceService.js`** — pass `engine` parameter through narrate() and textToSpeech()

### Key Constraints

- **GPU VRAM:** Must evict Ollama before loading Bark, must unload Bark immediately after. Never leave Bark resident.
- **Timeout:** Bark is slow (~10-30 seconds per short clip). Set timeout to 120 seconds for narrate endpoint when using Bark.
- **Fallback:** If Bark fails or GPU unavailable, fall back to Piper automatically.
- **Don't break Piper:** Piper must remain the default fast engine. Bark is the optional expressive engine.

### Bark Special Token Examples

Users should be able to write scripts like:
```
[clears throat] Ladies and gentlemen... [laughter] you won't believe what happened next.
[sighs] It was a dark and stormy night...
```

Bark interprets these tokens natively — no extra processing needed.

### Testing

- Test with: `curl -X POST http://localhost:5002/api/voice/narrate -H "Content-Type: application/json" -d '{"script": "[laughter] Well well well... what do we have here?", "engine": "bark"}'`
- Verify GPU is released after generation
- Verify Piper still works after Bark unloads
