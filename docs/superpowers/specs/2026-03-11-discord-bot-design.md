# Guaardvark Discord Bot — Design Spec

**Date:** 2026-03-11
**Status:** Approved
**Goal:** Full-featured Discord bot connecting a live Guaardvark instance to Discord, with text chat, image generation, semantic search, CSV generation, model management, and voice channel integration.

## Architecture Overview

The bot runs locally on the same machine as Guaardvark, communicating with the backend via REST API on `localhost:${FLASK_PORT}` (currently 5002). Discord connections are outbound-only — no ports exposed, no tunnels needed.

```
Discord Cloud ←── outbound WSS ──→ Bot Process (local)
                                        │
                                        ├── REST ──→ Guaardvark Backend (localhost:5002)
                                        │              ├── /api/enhanced-chat (LLM)
                                        │              ├── /api/batch-image/* (images)
                                        │              ├── /api/search (semantic search)
                                        │              ├── /api/generate (CSV)
                                        │              ├── /api/meta (diagnostics)
                                        │              ├── /api/model (model management)
                                        │              └── /api/voice/* (STT + TTS)
                                        │
                                        └── FFmpeg ──→ Discord Voice Gateway (voice channels)
```

## File Structure

```
discord_bot/
├── bot.py                  # Entry point, slash command registration, event handlers
├── commands/
│   ├── __init__.py
│   ├── chat.py             # /ask — LLM chat with conversation memory
│   ├── image.py            # /imagine, /enhance-prompt — image generation
│   ├── search.py           # /search — semantic RAG search
│   ├── generation.py       # /generate-csv — bulk CSV data generation
│   ├── system.py           # /status, /models, /switch-model — system management
│   └── voice.py            # /voice join, /voice leave — voice channel integration
├── core/
│   ├── __init__.py
│   ├── api_client.py       # Async Guaardvark REST client (aiohttp)
│   ├── rate_limiter.py     # Per-user sliding window rate limiter
│   ├── security.py         # Input sanitization, admin role checks, channel allowlists
│   └── voice_handler.py    # Audio pipeline: Discord PCM → Whisper STT → LLM → Piper TTS → Discord playback
├── config.yaml             # Bot token, API base URL, rate limits, allowed channels, admin roles
├── requirements.txt        # discord.py[voice], aiohttp, pyyaml, PyNaCl
└── start_discord_bot.sh    # Launcher script (venv activation, health check, start)
```

## Slash Commands

### `/ask <prompt>` — Chat with Guaardvark
- Calls `POST /api/enhanced-chat` with the user's prompt
- Maintains per-user conversation context (session ID = `discord_{user_id}`)
- Deferred response: sends "Thinking..." ephemeral, edits with final answer
- Markdown response split into chunks if >2000 chars (Discord limit)
- Max conversation history: 50 messages per user (oldest pruned)
- Rate limit: 10 requests/minute per user

### `/imagine <prompt>` — Generate Images
- Wraps single prompt in a list, calls `POST /api/batch-image/generate/prompts`
- Returns deferred response while GPU generates (typically 15-60s)
- Polls batch status via `GET /api/batch-image/status/<batch_id>` until complete
- Fetches result image via `GET /api/batch-image/image/<batch_id>/<image_name>`
- Uploads result image as Discord attachment
- Optional params: `--steps` (inference steps), `--size` (512/768/1024)
- Rate limit: 3 requests/minute per user (GPU-intensive)
- Queue depth limit: reject if >5 image jobs already queued

### `/enhance-prompt <prompt>` — Improve a Prompt
- Calls `POST /api/batch-image/enhance-prompt` to rewrite/improve a generation prompt
- Returns enhanced prompt as text (user can then use it with `/imagine`)
- Lightweight — no GPU needed

### `/search <query>` — Semantic Search
- Calls `POST /api/search/semantic` with the query
- Returns top 5 results with relevance scores, formatted as an embed
- Note: search is global (no per-project scoping from Discord)

### `/generate-csv <description>` — Bulk Data Generation
- Calls `POST /api/generate/csv` with description and auto-generated `output_filename`
- Long-running: deferred response with status polling
- Uploads result CSV as Discord file attachment
- Rate limit: 2 requests/minute per user

### `/status` — System Health
- Calls `GET /api/meta/status`
- Returns embed with: model loaded, GPU VRAM, CPU/RAM, uptime, Celery worker count
- Admin-only option: `--detailed` adds data from `GET /api/meta/metrics` and `GET /api/meta/llm-ready`

### `/models` — List Available Models
- Calls `GET /api/model/list`
- Returns embed with installed models, which is active, VRAM per model

### `/switch-model <model_name>` — Switch Active LLM
- Calls `POST /api/model/set`
- Admin-only command (requires configured admin role)
- Confirms switch with status message

### `/voice join` — Join Voice Channel
- Bot joins the invoking user's current voice channel
- Requires `Intents.voice_states` and `receive=True` on `VoiceClient.connect()`
- Begins listening pipeline: Discord audio → PCM → WAV → Whisper STT → LLM → Piper TTS → Discord playback
- Shows embed confirming connection

### `/voice leave` — Leave Voice Channel
- Bot disconnects from voice channel
- Cleans up audio pipeline resources

### `/voice status` — Voice Session Info
- Shows current voice channel, active speakers, conversation history length

## Core Module Details

### `api_client.py` — Async REST Client

Session must be created inside async context (not in `__init__`) per aiohttp best practices.

Backend uses two response formats: some endpoints wrap in `success_response()` envelope (`{success: true, data: ...}`), others return raw JSON. The client must handle both.

```python
class GuaardvarkClient:
    def __init__(self, base_url="http://localhost:5002/api"):
        self.base_url = base_url
        self.session = None  # Created in setup()

    async def setup(self):
        """Must be called before first use (inside async context)."""
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120))

    async def close(self):
        """Call on shutdown for clean cleanup."""
        if self.session:
            await self.session.close()

    async def _unwrap(self, response):
        """Handle both envelope and raw response formats."""
        data = await response.json()
        if isinstance(data, dict) and "data" in data and "success" in data:
            return data["data"]  # Unwrap envelope
        return data

    async def chat(self, message, session_id, project_id=None):
        """POST /enhanced-chat"""

    async def generate_image(self, prompt, steps=20, size=512):
        """POST /batch-image/generate/prompts — wraps prompt in list"""

    async def get_batch_status(self, batch_id):
        """GET /batch-image/status/<batch_id>"""

    async def get_batch_image(self, batch_id, image_name):
        """GET /batch-image/image/<batch_id>/<image_name> — returns image bytes"""

    async def enhance_prompt(self, prompt):
        """POST /batch-image/enhance-prompt"""

    async def semantic_search(self, query, limit=5):
        """POST /search/semantic"""

    async def generate_csv(self, description, output_filename):
        """POST /generate/csv — output_filename is required"""

    async def get_diagnostics(self):
        """GET /meta/status"""

    async def get_detailed_diagnostics(self):
        """GET /meta/metrics + GET /meta/llm-ready"""

    async def get_models(self):
        """GET /model/list"""

    async def switch_model(self, model_name):
        """POST /model/set"""

    async def speech_to_text(self, audio_bytes):
        """POST /voice/speech-to-text — multipart form, field name: 'audio'"""

    async def text_to_speech(self, text, voice="ryan"):
        """POST /voice/text-to-speech — returns {audio_url, filename, voice, engine}"""

    async def get_voice_audio(self, filename):
        """GET /voice/audio/{filename} — returns WAV bytes"""

    async def health_check(self):
        """GET /health"""
```

### `rate_limiter.py` — Per-User Rate Limiting

Sliding window counter per user per command type (in-memory, resets on bot restart):
- `/ask`: 10/min
- `/imagine`: 3/min
- `/generate-csv`: 2/min
- `/search`: 15/min
- `/enhance-prompt`: 10/min
- `/voice`: no rate limit (natural human speed limiting)

Returns remaining quota in response embeds. Cooldown message when limit hit.

### `security.py` — Input Sanitization & Access Control

- **Input sanitization**: Strip Discord mentions, limit prompt length (2000 chars for text, 500 for image prompts), reject injection patterns
- **Admin checks**: `/switch-model` requires admin role (configured in config.yaml)
- **Channel allowlists**: Optionally restrict bot to specific channels
- **DM handling**: Bot responds to DMs with a subset of commands (no image gen in DMs to prevent abuse)

### `voice_handler.py` — Voice Channel Audio Pipeline

The voice pipeline handles the full loop:

1. **Audio capture**: discord.py `VoiceClient` with `receive=True` receives Opus audio per user → decode to PCM (requires PyNaCl for voice decryption)
2. **VAD + Buffering**: Accumulate PCM frames, detect speech end (1.5s silence threshold)
3. **STT**: Convert PCM buffer to WAV → `POST /api/voice/speech-to-text` (multipart form, field name: `audio`)
4. **LLM**: Send transcribed text to `POST /api/enhanced-chat` with voice session ID
5. **TTS**: Send LLM response to `POST /api/voice/text-to-speech` → get `audio_url`
6. **Fetch audio**: `GET /api/voice/audio/{filename}` → download WAV bytes
7. **Playback**: Convert WAV to Opus via FFmpeg → stream to Discord voice channel via `VoiceClient`

Key details:
- Per-user audio streams (discord.py provides separate streams per speaker)
- Conversation context maintained per voice session
- FFmpeg required for WAV→Opus conversion (already installed on the machine)
- Voice activity detection (VAD) prevents processing silence
- Cancellation: if user speaks while bot is responding, interrupt playback
- discord.py audio receive requires `Intents.voice_states` and PyNaCl for decryption

```
Discord Voice → Opus decode → PCM buffer → silence detect → WAV file
    → POST /voice/speech-to-text (field: "audio") → text
    → POST /enhanced-chat → response text
    → POST /voice/text-to-speech → audio_url
    → GET /voice/audio/{filename} → WAV bytes
    → FFmpeg WAV→Opus → Discord Voice playback
```

### `config.yaml` — Configuration

```yaml
bot:
  token: "${DISCORD_BOT_TOKEN}"  # Environment variable reference
  guild_id: null                  # null = global commands (slower to register)
  prefix: "!"                     # Legacy prefix (slash commands primary)

api:
  base_url: "http://localhost:${FLASK_PORT:-5002}/api"
  timeout: 120                    # Seconds (image gen can take a while)
  health_check_interval: 60       # Seconds between health pings

security:
  admin_roles: ["Admin", "Bot Admin"]
  allowed_channels: []            # Empty = all channels allowed
  allow_dms: true
  max_prompt_length: 2000
  max_image_prompt_length: 500

rate_limits:
  ask: 10        # per minute per user
  imagine: 3
  generate_csv: 2
  search: 15
  enhance_prompt: 10

voice:
  enabled: true
  silence_threshold_ms: 1500      # Silence before processing speech
  max_listen_duration_s: 30       # Max single utterance length
  tts_voice: "ryan"
  interrupt_on_speech: true       # Cancel bot speech when user talks

image:
  max_queue_depth: 5              # Reject if more than 5 image jobs queued
  default_steps: 20
  default_size: 512

conversation:
  max_history: 50                 # Max messages per user before pruning oldest
```

## Error Handling

- **Backend unreachable**: Bot sends "Guaardvark is offline" ephemeral message, retries health check
- **GPU busy**: Image/video commands return "GPU is busy, please try again in a moment"
- **Rate limited**: User-friendly message with remaining cooldown time
- **Oversized response**: Split into multiple messages, or upload as text file if >4000 chars
- **Voice errors**: Graceful disconnect from voice channel, notify user in text channel
- **Timeout**: 120s timeout for all API calls, 30s for voice STT
- **Graceful shutdown**: SIGINT/SIGTERM handler disconnects from voice channels, closes aiohttp session, logs out cleanly

## Dependencies

```
discord.py[voice]>=2.3.0    # Discord API wrapper with voice support
aiohttp>=3.9.0              # Async HTTP client for Guaardvark API
PyYAML>=6.0                 # Config file parsing
PyNaCl>=1.5.0               # Required by discord.py for voice receive + decryption
```

System requirements (already installed):
- FFmpeg (for voice audio conversion)
- Python 3.12+

## Startup & Operations

`start_discord_bot.sh`:
1. Activate Python venv (shared with backend, or separate)
2. Health-check Guaardvark backend (`curl localhost:${FLASK_PORT}/api/health`)
3. Start bot process: `python -m discord_bot.bot`
4. PID file to `pids/discord_bot.pid` for `stop.sh` integration
5. Logs to `logs/discord_bot.log` (consistent with other Guaardvark services)

Integration with existing `start.sh`:
- Optional flag: `./start.sh --discord` starts the bot alongside backend/frontend
- `stop.sh` kills bot process via PID file (follows existing environment-aware pattern using `GUAARDVARK_ROOT`)

## Testing Plan

1. **Unit tests**: Mock aiohttp responses, test rate limiter, test input sanitization
2. **Integration tests**: Real API calls to running Guaardvark instance
3. **Manual Discord tests**:
   - `/ask` — verify response, conversation memory, long response splitting
   - `/imagine` — verify deferred response, image upload, queue limiting
   - `/search` — verify embed formatting, relevance scores
   - `/status` — verify system info accuracy
   - `/voice join` + speak — verify transcription → response → playback loop
   - Rate limiting — verify cooldown messages
   - Admin commands — verify role checks

## Security Considerations

- Bot token stored in environment variable, never in config file
- Input sanitization on all user input before forwarding to API
- No file system access from Discord commands (API only)
- Rate limiting prevents resource exhaustion
- Admin role required for model switching
- Channel allowlists for production servers
- Voice audio is ephemeral (temp files cleaned up after processing)

## API Endpoint Reference

Verified against actual backend codebase:

| Bot Command | HTTP Method | Endpoint Path |
|-------------|-------------|---------------|
| `/ask` | POST | `/api/enhanced-chat` |
| `/imagine` | POST | `/api/batch-image/generate/prompts` |
| (poll status) | GET | `/api/batch-image/status/<batch_id>` |
| (fetch image) | GET | `/api/batch-image/image/<batch_id>/<name>` |
| `/enhance-prompt` | POST | `/api/batch-image/enhance-prompt` |
| `/search` | POST | `/api/search/semantic` |
| `/generate-csv` | POST | `/api/generate/csv` |
| `/status` | GET | `/api/meta/status` |
| `/status --detailed` | GET | `/api/meta/metrics` + `/api/meta/llm-ready` |
| `/models` | GET | `/api/model/list` |
| `/switch-model` | POST | `/api/model/set` |
| (voice STT) | POST | `/api/voice/speech-to-text` |
| (voice TTS) | POST | `/api/voice/text-to-speech` |
| (voice audio) | GET | `/api/voice/audio/<filename>` |
| (health) | GET | `/api/health` |
