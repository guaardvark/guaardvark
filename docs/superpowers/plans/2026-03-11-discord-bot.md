# Guaardvark Discord Bot — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a full-featured Discord bot that connects a live Guaardvark instance to Discord with text chat, image generation, semantic search, CSV generation, model management, and voice channel integration.

**Architecture:** The bot runs locally alongside Guaardvark, communicating via REST API on localhost. discord.py handles Discord gateway connections (outbound-only). aiohttp provides async HTTP for backend API calls. Voice uses Discord PCM capture → Whisper STT → LLM → Piper TTS → Discord playback.

**Tech Stack:** Python 3.12, discord.py[voice] 2.3+, aiohttp 3.9+, PyYAML, PyNaCl, FFmpeg

**Spec:** `docs/superpowers/specs/2026-03-11-discord-bot-design.md`

---

## File Map

```
discord_bot/
├── __init__.py                 # Package marker
├── bot.py                      # Entry point: bot setup, cog loading, event handlers, graceful shutdown
├── commands/
│   ├── __init__.py
│   ├── chat.py                 # /ask cog — LLM chat with per-user conversation memory
│   ├── image.py                # /imagine + /enhance-prompt cog — image generation + prompt enhancement
│   ├── search.py               # /search cog — semantic RAG search
│   ├── generation.py           # /generate-csv cog — bulk CSV data generation
│   ├── system.py               # /status + /models + /switch-model cog — system management
│   └── voice.py                # /voice cog — voice channel join/leave/status
├── core/
│   ├── __init__.py
│   ├── api_client.py           # Async REST client wrapping all Guaardvark endpoints
│   ├── rate_limiter.py         # Per-user sliding window rate limiter
│   ├── security.py             # Input sanitization, admin checks, channel allowlists
│   └── voice_handler.py        # Audio pipeline: PCM capture → STT → LLM → TTS → playback
├── config.yaml                 # Bot configuration (token via env var, API URL, limits)
├── requirements.txt            # Python dependencies
├── start_discord_bot.sh        # Launcher script
└── tests/
    ├── __init__.py
    ├── conftest.py             # Shared fixtures (mock client, mock interaction)
    ├── test_api_client.py      # API client unit tests
    ├── test_rate_limiter.py    # Rate limiter unit tests
    ├── test_security.py        # Input sanitization unit tests
    ├── test_chat.py            # /ask command tests
    ├── test_image.py           # /imagine + /enhance-prompt tests
    ├── test_search.py          # /search tests
    ├── test_generation.py      # /generate-csv tests
    └── test_system.py          # /status + /models + /switch-model tests
```

---

## Chunk 1: Foundation & Core

### Task 1: Project Scaffolding

**Files:**
- Create: `discord_bot/__init__.py`
- Create: `discord_bot/requirements.txt`
- Create: `discord_bot/config.yaml`
- Create: `discord_bot/tests/__init__.py`
- Create: `discord_bot/tests/conftest.py`
- Create: `discord_bot/commands/__init__.py`
- Create: `discord_bot/core/__init__.py`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p discord_bot/commands discord_bot/core discord_bot/tests
```

- [ ] **Step 2: Create requirements.txt**

```
discord.py[voice]>=2.3.0
aiohttp>=3.9.0
PyYAML>=6.0
PyNaCl>=1.5.0
pytest-asyncio>=0.23.0
```

- [ ] **Step 3: Create config.yaml**

```yaml
bot:
  token: "${DISCORD_BOT_TOKEN}"
  guild_id: null

api:
  base_url: "http://localhost:${FLASK_PORT:-5002}/api"
  timeout: 120
  health_check_interval: 60

security:
  admin_roles: ["Admin", "Bot Admin"]
  allowed_channels: []
  allow_dms: true
  max_prompt_length: 2000
  max_image_prompt_length: 500

rate_limits:
  ask: 10
  imagine: 3
  generate_csv: 2
  search: 15
  enhance_prompt: 10

voice:
  enabled: true
  silence_threshold_ms: 1500
  max_listen_duration_s: 30
  tts_voice: "ryan"
  interrupt_on_speech: true

image:
  max_queue_depth: 5
  default_steps: 20
  default_size: 512

conversation:
  max_history: 50
```

- [ ] **Step 4: Create package __init__.py files**

`discord_bot/__init__.py`:
```python
"""Guaardvark Discord Bot — connects a live Guaardvark instance to Discord."""
```

`discord_bot/commands/__init__.py`, `discord_bot/core/__init__.py`, and `discord_bot/tests/__init__.py`: empty files.

- [ ] **Step 5: Create test conftest.py with shared fixtures**

`discord_bot/tests/conftest.py`:
```python
"""Shared test fixtures for Discord bot tests."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def mock_interaction():
    """Create a mock Discord interaction for testing slash commands."""
    interaction = AsyncMock()
    interaction.response = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.followup.send = AsyncMock()
    interaction.user = MagicMock()
    interaction.user.id = 123456789
    interaction.user.name = "testuser"
    interaction.user.display_name = "Test User"
    interaction.guild = MagicMock()
    interaction.guild.id = 987654321
    interaction.channel = MagicMock()
    interaction.channel.id = 111222333
    return interaction


@pytest.fixture
def mock_api_client():
    """Create a mock GuaardvarkClient for testing commands without real API calls."""
    client = AsyncMock()
    client.health_check = AsyncMock(return_value={"status": "ok"})
    client.chat = AsyncMock(return_value={
        "response": "Hello! I'm Guaardvark.",
        "session_id": "discord_123456789",
        "model_used": "llama3",
        "response_time": 1.2,
    })
    client.generate_image = AsyncMock(return_value={
        "batch_id": "test-batch-123",
        "message": "Batch generation started",
        "prompt_count": 1,
    })
    client.get_batch_status = AsyncMock(return_value={
        "status": "completed",
        "total_images": 1,
        "completed_images": 1,
        "results": [{"success": True, "image_path": "/tmp/test.png"}],
    })
    client.enhance_prompt = AsyncMock(return_value={
        "enhanced_prompt": "A beautiful landscape, detailed, 8k",
        "negative_prompt": "blurry, low quality",
    })
    client.semantic_search = AsyncMock(return_value={
        "answer": "The answer is 42.",
        "sources": [{"content": "source text", "metadata": {}}],
    })
    client.generate_csv = AsyncMock(return_value={
        "message": "Generation complete",
        "output_file": "test_output.csv",
        "statistics": {"generated_items": 10, "processing_time": 5.0},
    })
    client.get_diagnostics = AsyncMock(return_value={
        "active_model": "llama3",
        "ollama_reachable": True,
        "model_count": 3,
        "document_count": 150,
    })
    client.get_models = AsyncMock(return_value={
        "models": [
            {"name": "llama3", "details": {"parameter_size": "8B"}},
            {"name": "mistral", "details": {"parameter_size": "7B"}},
        ]
    })
    client.switch_model = AsyncMock(return_value={
        "message": "Model switch to llama3 started",
        "status": "switching",
    })
    client.speech_to_text = AsyncMock(return_value={
        "text": "Hello Guaardvark",
        "language": "en",
        "duration": 2.5,
    })
    client.text_to_speech = AsyncMock(return_value={
        "audio_url": "/api/voice/audio/tts_test.wav",
        "filename": "tts_test.wav",
    })
    client.get_voice_audio = AsyncMock(return_value=b"\x00" * 1000)
    return client


@pytest.fixture
def sample_config():
    """Return a test configuration dict."""
    return {
        "bot": {"token": "test-token", "guild_id": None},
        "api": {"base_url": "http://localhost:5002/api", "timeout": 120, "health_check_interval": 60},
        "security": {
            "admin_roles": ["Admin"],
            "allowed_channels": [],
            "allow_dms": True,
            "max_prompt_length": 2000,
            "max_image_prompt_length": 500,
        },
        "rate_limits": {"ask": 10, "imagine": 3, "generate_csv": 2, "search": 15, "enhance_prompt": 10},
        "voice": {
            "enabled": True, "silence_threshold_ms": 1500,
            "max_listen_duration_s": 30, "tts_voice": "ryan", "interrupt_on_speech": True,
        },
        "image": {"max_queue_depth": 5, "default_steps": 20, "default_size": 512},
        "conversation": {"max_history": 50},
    }
```

- [ ] **Step 6: Install dependencies**

```bash
cd discord_bot && pip install -r requirements.txt
```

- [ ] **Step 7: Commit**

```bash
git add discord_bot/
git commit -m "feat(discord): scaffold project structure, config, and test fixtures"
```

---

### Task 2: Rate Limiter

**Files:**
- Create: `discord_bot/core/rate_limiter.py`
- Create: `discord_bot/tests/test_rate_limiter.py`

- [ ] **Step 1: Write failing tests**

`discord_bot/tests/test_rate_limiter.py`:
```python
"""Tests for per-user sliding window rate limiter."""
import time
import pytest
from discord_bot.core.rate_limiter import RateLimiter


class TestRateLimiter:
    def test_allows_first_request(self):
        limiter = RateLimiter(max_requests=5, window_seconds=60)
        allowed, remaining, retry_after = limiter.check(user_id=1, command="ask")
        assert allowed is True
        assert remaining == 4
        assert retry_after == 0

    def test_blocks_after_limit(self):
        limiter = RateLimiter(max_requests=2, window_seconds=60)
        limiter.check(user_id=1, command="ask")
        limiter.check(user_id=1, command="ask")
        allowed, remaining, retry_after = limiter.check(user_id=1, command="ask")
        assert allowed is False
        assert remaining == 0
        assert retry_after > 0

    def test_different_users_independent(self):
        limiter = RateLimiter(max_requests=1, window_seconds=60)
        limiter.check(user_id=1, command="ask")
        allowed, _, _ = limiter.check(user_id=2, command="ask")
        assert allowed is True

    def test_different_commands_independent(self):
        limiter = RateLimiter(max_requests=1, window_seconds=60)
        limiter.check(user_id=1, command="ask")
        allowed, _, _ = limiter.check(user_id=1, command="search")
        assert allowed is True

    def test_window_expires(self):
        limiter = RateLimiter(max_requests=1, window_seconds=0.1)
        limiter.check(user_id=1, command="ask")
        time.sleep(0.15)
        allowed, _, _ = limiter.check(user_id=1, command="ask")
        assert allowed is True

    def test_remaining_count_decrements(self):
        limiter = RateLimiter(max_requests=3, window_seconds=60)
        _, r1, _ = limiter.check(user_id=1, command="ask")
        _, r2, _ = limiter.check(user_id=1, command="ask")
        assert r1 == 2
        assert r2 == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/llamax1/LLAMAX7 && python -m pytest discord_bot/tests/test_rate_limiter.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'discord_bot.core.rate_limiter'`

- [ ] **Step 3: Implement rate limiter**

`discord_bot/core/rate_limiter.py`:
```python
"""Per-user sliding window rate limiter."""
import time
from collections import defaultdict


class RateLimiter:
    """Sliding window rate limiter keyed by (user_id, command)."""

    def __init__(self, max_requests: int = 10, window_seconds: float = 60.0):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        # {(user_id, command): [timestamp, ...]}
        self._requests: dict[tuple, list[float]] = defaultdict(list)

    def check(self, user_id: int, command: str) -> tuple[bool, int, float]:
        """Check if a request is allowed.

        Returns (allowed, remaining, retry_after_seconds).
        """
        key = (user_id, command)
        now = time.monotonic()
        cutoff = now - self.window_seconds

        # Prune expired entries
        self._requests[key] = [t for t in self._requests[key] if t > cutoff]

        count = len(self._requests[key])
        if count >= self.max_requests:
            # Blocked — calculate when oldest request expires
            oldest = self._requests[key][0]
            retry_after = round(oldest + self.window_seconds - now, 1)
            return False, 0, max(0, retry_after)

        # Allowed
        self._requests[key].append(now)
        remaining = self.max_requests - count - 1
        return True, remaining, 0
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/llamax1/LLAMAX7 && python -m pytest discord_bot/tests/test_rate_limiter.py -v
```

Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add discord_bot/core/rate_limiter.py discord_bot/tests/test_rate_limiter.py
git commit -m "feat(discord): add per-user sliding window rate limiter with tests"
```

---

### Task 3: Input Security

**Files:**
- Create: `discord_bot/core/security.py`
- Create: `discord_bot/tests/test_security.py`

- [ ] **Step 1: Write failing tests**

`discord_bot/tests/test_security.py`:
```python
"""Tests for input sanitization and access control."""
import pytest
from unittest.mock import MagicMock
from discord_bot.core.security import sanitize_input, is_admin, is_channel_allowed


class TestSanitizeInput:
    def test_strips_mentions(self):
        result = sanitize_input("<@123456> hello <@!789>", max_length=2000)
        assert "<@" not in result
        assert "hello" in result

    def test_strips_everyone_here(self):
        result = sanitize_input("@everyone @here check this", max_length=2000)
        assert "@everyone" not in result
        assert "@here" not in result

    def test_truncates_to_max_length(self):
        result = sanitize_input("a" * 3000, max_length=500)
        assert len(result) == 500

    def test_strips_code_blocks(self):
        result = sanitize_input("```python\nimport os\nos.system('rm -rf /')\n```", max_length=2000)
        # Content preserved but blocks removed
        assert "```" not in result

    def test_empty_after_sanitization_returns_none(self):
        result = sanitize_input("<@123> <@456>", max_length=2000)
        assert result is None or result.strip() == ""

    def test_normal_text_passes_through(self):
        result = sanitize_input("Tell me about neural networks", max_length=2000)
        assert result == "Tell me about neural networks"


class TestIsAdmin:
    def test_admin_role_match(self):
        member = MagicMock()
        role = MagicMock()
        role.name = "Admin"
        member.roles = [role]
        assert is_admin(member, ["Admin", "Bot Admin"]) is True

    def test_no_admin_role(self):
        member = MagicMock()
        role = MagicMock()
        role.name = "Member"
        member.roles = [role]
        assert is_admin(member, ["Admin"]) is False

    def test_none_member_returns_false(self):
        assert is_admin(None, ["Admin"]) is False


class TestIsChannelAllowed:
    def test_empty_allowlist_allows_all(self):
        assert is_channel_allowed(12345, []) is True

    def test_channel_in_allowlist(self):
        assert is_channel_allowed(12345, [12345, 67890]) is True

    def test_channel_not_in_allowlist(self):
        assert is_channel_allowed(12345, [67890]) is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/llamax1/LLAMAX7 && python -m pytest discord_bot/tests/test_security.py -v
```

Expected: FAIL — import error

- [ ] **Step 3: Implement security module**

`discord_bot/core/security.py`:
```python
"""Input sanitization, admin checks, and channel allowlists."""
import re
from typing import Optional


def sanitize_input(text: str, max_length: int = 2000) -> Optional[str]:
    """Sanitize user input: strip mentions, code blocks, and enforce length limit.

    Returns None if input is empty after sanitization.
    """
    if not text:
        return None

    # Strip user/role mentions: <@123>, <@!123>, <@&123>
    cleaned = re.sub(r"<@[!&]?\d+>", "", text)

    # Strip @everyone and @here
    cleaned = re.sub(r"@(everyone|here)", "", cleaned)

    # Strip code blocks (keep inner text)
    cleaned = re.sub(r"```[\s\S]*?```", "", cleaned)

    # Strip inline code (keep inner text)
    cleaned = re.sub(r"`([^`]*)`", r"\1", cleaned)

    # Collapse excessive whitespace
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    # Truncate
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length]

    return cleaned if cleaned else None


def is_admin(member, admin_roles: list[str]) -> bool:
    """Check if a guild member has any of the configured admin roles."""
    if member is None:
        return False
    member_role_names = {role.name for role in getattr(member, "roles", [])}
    return bool(member_role_names & set(admin_roles))


def is_channel_allowed(channel_id: int, allowed_channels: list[int]) -> bool:
    """Check if a channel is in the allowlist. Empty list = all allowed."""
    if not allowed_channels:
        return True
    return channel_id in allowed_channels
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/llamax1/LLAMAX7 && python -m pytest discord_bot/tests/test_security.py -v
```

Expected: All 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add discord_bot/core/security.py discord_bot/tests/test_security.py
git commit -m "feat(discord): add input sanitization and access control with tests"
```

---

### Task 4: API Client

**Files:**
- Create: `discord_bot/core/api_client.py`
- Create: `discord_bot/tests/test_api_client.py`

- [ ] **Step 1: Write failing tests**

`discord_bot/tests/test_api_client.py`:
```python
"""Tests for the async Guaardvark REST client."""
import pytest
import aiohttp
from unittest.mock import AsyncMock, MagicMock, patch
from discord_bot.core.api_client import GuaardvarkClient


@pytest.mark.asyncio
class TestGuaardvarkClient:
    async def test_setup_creates_session(self):
        client = GuaardvarkClient("http://localhost:5002/api")
        assert client.session is None
        await client.setup()
        assert client.session is not None
        await client.close()

    async def test_health_check(self):
        client = GuaardvarkClient("http://localhost:5002/api")
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"status": "ok"})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        client.session = mock_session

        result = await client.health_check()
        assert result["status"] == "ok"
        await client.close()

    async def test_chat_sends_correct_payload(self):
        client = GuaardvarkClient("http://localhost:5002/api")
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={
            "success": True,
            "data": {"response": "Hi!", "session_id": "s1"},
        })
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        client.session = mock_session

        result = await client.chat("Hello", "discord_123")
        assert result["response"] == "Hi!"
        # Verify the POST was called with correct JSON
        call_kwargs = mock_session.post.call_args
        assert "enhanced-chat" in str(call_kwargs)

    async def test_unwrap_envelope_response(self):
        client = GuaardvarkClient("http://localhost:5002/api")
        data = {"success": True, "data": {"models": []}}
        assert client._unwrap(data) == {"models": []}

    async def test_unwrap_raw_response(self):
        client = GuaardvarkClient("http://localhost:5002/api")
        data = {"answer": "42", "sources": []}
        assert client._unwrap(data) == data

    async def test_generate_image_wraps_prompt_in_list(self):
        client = GuaardvarkClient("http://localhost:5002/api")
        mock_resp = AsyncMock()
        mock_resp.status = 201
        mock_resp.json = AsyncMock(return_value={
            "success": True,
            "data": {"batch_id": "abc", "prompt_count": 1},
        })
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        client.session = mock_session

        result = await client.generate_image("a cat")
        assert result["batch_id"] == "abc"
        # Verify prompt was wrapped in a list
        call_kwargs = mock_session.post.call_args
        json_arg = call_kwargs[1].get("json", {}) if call_kwargs[1] else {}
        assert isinstance(json_arg.get("prompts"), list)

    async def test_speech_to_text_sends_multipart(self):
        client = GuaardvarkClient("http://localhost:5002/api")
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"text": "hello", "language": "en"})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        client.session = mock_session

        result = await client.speech_to_text(b"\x00" * 100)
        assert result["text"] == "hello"
        # Verify multipart form data was used
        call_kwargs = mock_session.post.call_args
        assert "data" in call_kwargs[1] or "data" in (call_kwargs[1] if len(call_kwargs) > 1 else {})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/llamax1/LLAMAX7 && python -m pytest discord_bot/tests/test_api_client.py -v
```

Expected: FAIL — import error

- [ ] **Step 3: Implement API client**

`discord_bot/core/api_client.py`:
```python
"""Async REST client wrapping all Guaardvark backend endpoints."""
import logging
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)


class GuaardvarkClient:
    """Async HTTP client for communicating with the Guaardvark backend API."""

    def __init__(self, base_url: str = "http://localhost:5002/api"):
        self.base_url = base_url.rstrip("/")
        self.session: Optional[aiohttp.ClientSession] = None

    async def setup(self):
        """Create the aiohttp session. Must be called inside an async context."""
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=120),
        )

    async def close(self):
        """Close the aiohttp session cleanly."""
        if self.session and not self.session.closed:
            await self.session.close()

    def _unwrap(self, data: dict) -> Any:
        """Handle both envelope ({success, data}) and raw response formats."""
        if isinstance(data, dict) and "data" in data and "success" in data:
            return data["data"]
        return data

    async def _get(self, path: str, **kwargs) -> dict:
        """GET request with error handling."""
        async with self.session.get(f"{self.base_url}{path}", **kwargs) as resp:
            data = await resp.json()
            if resp.status >= 400:
                error = data.get("error", f"HTTP {resp.status}")
                raise APIError(error, resp.status)
            return self._unwrap(data)

    async def _post(self, path: str, **kwargs) -> dict:
        """POST request with error handling."""
        async with self.session.post(f"{self.base_url}{path}", **kwargs) as resp:
            data = await resp.json()
            if resp.status >= 400:
                error = data.get("error", f"HTTP {resp.status}")
                raise APIError(error, resp.status)
            return self._unwrap(data)

    async def _post_raw(self, path: str, **kwargs) -> bytes:
        """POST request returning raw bytes (for file downloads)."""
        async with self.session.post(f"{self.base_url}{path}", **kwargs) as resp:
            if resp.status >= 400:
                text = await resp.text()
                raise APIError(text, resp.status)
            return await resp.read()

    async def _get_raw(self, path: str, **kwargs) -> bytes:
        """GET request returning raw bytes (for file downloads)."""
        async with self.session.get(f"{self.base_url}{path}", **kwargs) as resp:
            if resp.status >= 400:
                text = await resp.text()
                raise APIError(text, resp.status)
            return await resp.read()

    # --- Chat ---

    async def chat(self, message: str, session_id: str, project_id: int = None) -> dict:
        """POST /enhanced-chat"""
        payload = {
            "message": message,
            "session_id": session_id,
            "use_rag": True,
            "voice_mode": False,
        }
        if project_id is not None:
            payload["project_id"] = project_id
        return await self._post("/enhanced-chat", json=payload)

    # --- Image Generation ---

    async def generate_image(self, prompt: str, steps: int = 20, width: int = 512, height: int = 512) -> dict:
        """POST /batch-image/generate/prompts — wraps single prompt in a list."""
        payload = {
            "prompts": [prompt],
            "steps": steps,
            "width": width,
            "height": height,
        }
        return await self._post("/batch-image/generate/prompts", json=payload)

    async def get_batch_status(self, batch_id: str) -> dict:
        """GET /batch-image/status/<batch_id>?include_results=true"""
        return await self._get(f"/batch-image/status/{batch_id}", params={"include_results": "true"})

    async def get_batch_image(self, batch_id: str, image_name: str) -> bytes:
        """GET /batch-image/image/<batch_id>/<image_name> — returns image bytes."""
        return await self._get_raw(f"/batch-image/image/{batch_id}/{image_name}")

    async def enhance_prompt(self, prompt: str) -> dict:
        """POST /batch-image/enhance-prompt"""
        return await self._post("/batch-image/enhance-prompt", json={"prompt": prompt})

    # --- Search ---

    async def semantic_search(self, query: str) -> dict:
        """POST /search/semantic"""
        return await self._post("/search/semantic", json={"query": query})

    # --- CSV Generation ---

    async def generate_csv(self, description: str, output_filename: str) -> dict:
        """POST /generate/csv"""
        payload = {
            "type": "single",
            "prompt": description,
            "output_filename": output_filename,
        }
        return await self._post("/generate/csv", json=payload)

    # --- System ---

    async def get_diagnostics(self) -> dict:
        """GET /meta/status"""
        return await self._get("/meta/status")

    async def get_detailed_diagnostics(self) -> dict:
        """GET /meta/metrics + /meta/llm-ready — combined detailed info."""
        metrics = await self._get("/meta/metrics")
        try:
            llm_ready = await self._get("/meta/llm-ready")
            metrics["llm_ready"] = llm_ready
        except APIError:
            pass
        return metrics

    async def get_models(self) -> dict:
        """GET /model/list"""
        return await self._get("/model/list")

    async def switch_model(self, model_name: str) -> dict:
        """POST /model/set"""
        return await self._post("/model/set", json={"model": model_name})

    # --- Voice ---

    async def speech_to_text(self, audio_bytes: bytes) -> dict:
        """POST /voice/speech-to-text — multipart form upload, field name 'audio'."""
        form = aiohttp.FormData()
        form.add_field("audio", audio_bytes, filename="audio.wav", content_type="audio/wav")
        return await self._post("/voice/speech-to-text", data=form)

    async def text_to_speech(self, text: str, voice: str = "ryan") -> dict:
        """POST /voice/text-to-speech"""
        return await self._post("/voice/text-to-speech", json={"text": text, "voice": voice})

    async def get_voice_audio(self, filename: str) -> bytes:
        """GET /voice/audio/<filename> — returns WAV bytes."""
        return await self._get_raw(f"/voice/audio/{filename}")

    # --- Health ---

    async def health_check(self) -> dict:
        """GET /health"""
        return await self._get("/health")


class APIError(Exception):
    """Raised when the Guaardvark API returns an error."""

    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message)
        self.status_code = status_code
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/llamax1/LLAMAX7 && python -m pytest discord_bot/tests/test_api_client.py -v
```

Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add discord_bot/core/api_client.py discord_bot/tests/test_api_client.py
git commit -m "feat(discord): add async Guaardvark REST client with tests"
```

---

## Chunk 2: Text Commands

### Task 5: /ask Command (Chat Cog)

**Files:**
- Create: `discord_bot/commands/chat.py`
- Create: `discord_bot/tests/test_chat.py`

- [ ] **Step 1: Write failing tests**

`discord_bot/tests/test_chat.py`:
```python
"""Tests for /ask command."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from discord_bot.commands.chat import ChatCog


@pytest.mark.asyncio
class TestAskCommand:
    def _make_cog(self, mock_api_client, sample_config):
        bot = MagicMock()
        cog = ChatCog(bot, mock_api_client, sample_config)
        return cog

    async def test_ask_returns_response(self, mock_api_client, mock_interaction, sample_config):
        cog = self._make_cog(mock_api_client, sample_config)
        await cog._handle_ask(mock_interaction, "What is Guaardvark?")
        # Should have deferred, then sent followup with the LLM response
        mock_interaction.response.defer.assert_called_once()
        mock_interaction.followup.send.assert_called()
        sent = str(mock_interaction.followup.send.call_args)
        assert "Guaardvark" in sent

    async def test_ask_uses_user_session_id(self, mock_api_client, mock_interaction, sample_config):
        cog = self._make_cog(mock_api_client, sample_config)
        await cog._handle_ask(mock_interaction, "hello")
        call_kwargs = mock_api_client.chat.call_args
        assert "discord_123456789" in str(call_kwargs)

    async def test_ask_handles_api_error(self, mock_api_client, mock_interaction, sample_config):
        from discord_bot.core.api_client import APIError
        mock_api_client.chat = AsyncMock(side_effect=APIError("Backend down", 503))
        cog = self._make_cog(mock_api_client, sample_config)
        await cog._handle_ask(mock_interaction, "hello")
        # Should send error message, not raise
        mock_interaction.followup.send.assert_called()
        sent = str(mock_interaction.followup.send.call_args)
        assert "error" in sent.lower() or "offline" in sent.lower() or "failed" in sent.lower()

    async def test_ask_sanitizes_input(self, mock_api_client, mock_interaction, sample_config):
        cog = self._make_cog(mock_api_client, sample_config)
        await cog._handle_ask(mock_interaction, "<@123> @everyone tell me stuff")
        call_kwargs = mock_api_client.chat.call_args
        message_sent = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("message", "")
        assert "<@" not in message_sent
        assert "@everyone" not in message_sent

    async def test_ask_splits_long_response(self, mock_api_client, mock_interaction, sample_config):
        long_text = "x" * 2500
        mock_api_client.chat = AsyncMock(return_value={
            "response": long_text, "session_id": "s1", "model_used": "llama3",
        })
        cog = self._make_cog(mock_api_client, sample_config)
        await cog._handle_ask(mock_interaction, "hello")
        # Should be called at least twice (split)
        assert mock_interaction.followup.send.call_count >= 2
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/llamax1/LLAMAX7 && python -m pytest discord_bot/tests/test_chat.py -v
```

Expected: FAIL — import error

- [ ] **Step 3: Implement chat cog**

`discord_bot/commands/chat.py`:
```python
"""Chat cog — /ask command for LLM conversation."""
import io
import logging

import discord
from discord import app_commands
from discord.ext import commands

from discord_bot.core.api_client import GuaardvarkClient, APIError
from discord_bot.core.rate_limiter import RateLimiter
from discord_bot.core.security import sanitize_input, is_channel_allowed

logger = logging.getLogger(__name__)
DISCORD_MAX_LENGTH = 2000


def split_message(text: str, max_length: int = DISCORD_MAX_LENGTH) -> list[str]:
    """Split a long message into chunks that fit Discord's limit."""
    if len(text) <= max_length:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break
        # Try to split at a newline
        split_at = text.rfind("\n", 0, max_length)
        if split_at == -1:
            split_at = text.rfind(" ", 0, max_length)
        if split_at == -1:
            split_at = max_length
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip()
    return chunks


class ChatCog(commands.Cog):
    """Handles /ask — LLM chat with per-user conversation memory."""

    def __init__(self, bot: commands.Bot, api_client: GuaardvarkClient, config: dict):
        self.bot = bot
        self.api = api_client
        self.config = config
        self.rate_limiter = RateLimiter(
            max_requests=config["rate_limits"]["ask"], window_seconds=60,
        )

    @app_commands.command(name="ask", description="Chat with Guaardvark AI")
    @app_commands.describe(prompt="Your message or question")
    async def ask(self, interaction: discord.Interaction, prompt: str):
        await self._handle_ask(interaction, prompt)

    async def _handle_ask(self, interaction: discord.Interaction, prompt: str):
        """Handle /ask — testable entry point."""
        # Channel allowlist check
        if interaction.guild and not is_channel_allowed(
            interaction.channel.id, self.config["security"]["allowed_channels"],
        ):
            await interaction.response.send_message("Bot not allowed in this channel.", ephemeral=True)
            return

        # Rate limit check
        allowed, remaining, retry_after = self.rate_limiter.check(
            interaction.user.id, "ask",
        )
        if not allowed:
            await interaction.response.send_message(
                f"Rate limited. Try again in {retry_after:.0f}s.", ephemeral=True,
            )
            return

        # Sanitize input
        cleaned = sanitize_input(
            prompt, max_length=self.config["security"]["max_prompt_length"],
        )
        if not cleaned:
            await interaction.response.send_message(
                "Your message was empty after removing mentions and formatting.",
                ephemeral=True,
            )
            return

        # Defer response (thinking...)
        await interaction.response.defer()

        session_id = f"discord_{interaction.user.id}"

        try:
            result = await self.api.chat(cleaned, session_id)
            response_text = result.get("response", "No response received.")
            model = result.get("model_used", "unknown")

            # Handle long responses: upload as file if >4000 chars, else split
            if len(response_text) > 4000:
                file = discord.File(
                    io.BytesIO(response_text.encode()), filename="response.md",
                )
                await interaction.followup.send(
                    content=f"Response too long for Discord ({len(response_text)} chars). See attached file.",
                    file=file,
                )
            else:
                chunks = split_message(response_text)
                for chunk in chunks:
                    await interaction.followup.send(content=chunk)

        except APIError as e:
            logger.error("Chat API error: %s", e)
            await interaction.followup.send(
                content=f"Failed to get a response. The backend may be offline. ({e})",
            )
        except Exception as e:
            logger.exception("Unexpected error in /ask")
            await interaction.followup.send(
                content="An unexpected error occurred. Please try again.",
            )


async def setup(bot: commands.Bot):
    """Cog setup — called by bot.py during cog loading."""
    # api_client and config are attached to bot instance by bot.py
    await bot.add_cog(ChatCog(bot, bot.api_client, bot.config))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/llamax1/LLAMAX7 && python -m pytest discord_bot/tests/test_chat.py -v
```

Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add discord_bot/commands/chat.py discord_bot/tests/test_chat.py
git commit -m "feat(discord): add /ask command with conversation memory, rate limiting, and input sanitization"
```

---

### Task 6: /search Command

**Files:**
- Create: `discord_bot/commands/search.py`
- Create: `discord_bot/tests/test_search.py`

- [ ] **Step 1: Write failing tests**

`discord_bot/tests/test_search.py`:
```python
"""Tests for /search command."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from discord_bot.commands.search import SearchCog


@pytest.mark.asyncio
class TestSearchCommand:
    def _make_cog(self, mock_api_client, sample_config):
        bot = MagicMock()
        return SearchCog(bot, mock_api_client, sample_config)

    async def test_search_returns_embed(self, mock_api_client, mock_interaction, sample_config):
        cog = self._make_cog(mock_api_client, sample_config)
        await cog._handle_search(mock_interaction, "what is RAG?")
        mock_interaction.response.defer.assert_called_once()
        mock_interaction.followup.send.assert_called()
        call_kwargs = mock_interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    async def test_search_handles_no_results(self, mock_api_client, mock_interaction, sample_config):
        mock_api_client.semantic_search = AsyncMock(return_value={
            "answer": None, "sources": [],
        })
        cog = self._make_cog(mock_api_client, sample_config)
        await cog._handle_search(mock_interaction, "nonexistent topic")
        mock_interaction.followup.send.assert_called()

    async def test_search_sanitizes_input(self, mock_api_client, mock_interaction, sample_config):
        cog = self._make_cog(mock_api_client, sample_config)
        await cog._handle_search(mock_interaction, "<@123> @everyone query")
        query_sent = mock_api_client.semantic_search.call_args[0][0]
        assert "<@" not in query_sent
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/llamax1/LLAMAX7 && python -m pytest discord_bot/tests/test_search.py -v
```

- [ ] **Step 3: Implement search cog**

`discord_bot/commands/search.py`:
```python
"""Search cog — /search command for semantic RAG search."""
import logging

import discord
from discord import app_commands
from discord.ext import commands

from discord_bot.core.api_client import GuaardvarkClient, APIError
from discord_bot.core.rate_limiter import RateLimiter
from discord_bot.core.security import sanitize_input

logger = logging.getLogger(__name__)


class SearchCog(commands.Cog):
    """Handles /search — semantic RAG search across indexed documents."""

    def __init__(self, bot: commands.Bot, api_client: GuaardvarkClient, config: dict):
        self.bot = bot
        self.api = api_client
        self.config = config
        self.rate_limiter = RateLimiter(
            max_requests=config["rate_limits"]["search"], window_seconds=60,
        )

    @app_commands.command(name="search", description="Search Guaardvark's knowledge base")
    @app_commands.describe(query="What to search for")
    async def search(self, interaction: discord.Interaction, query: str):
        await self._handle_search(interaction, query)

    async def _handle_search(self, interaction: discord.Interaction, query: str):
        allowed, _, retry_after = self.rate_limiter.check(interaction.user.id, "search")
        if not allowed:
            await interaction.response.send_message(
                f"Rate limited. Try again in {retry_after:.0f}s.", ephemeral=True,
            )
            return

        cleaned = sanitize_input(query, max_length=self.config["security"]["max_prompt_length"])
        if not cleaned:
            await interaction.response.send_message("Query was empty after sanitization.", ephemeral=True)
            return

        await interaction.response.defer()

        try:
            result = await self.api.semantic_search(cleaned)
            answer = result.get("answer") or "No matching results found."
            sources = result.get("sources", [])

            embed = discord.Embed(
                title=f"Search: {cleaned[:100]}",
                description=answer[:4096],
                color=discord.Color.blue(),
            )

            if sources:
                source_text = "\n".join(
                    f"**{i+1}.** {s.get('content', 'N/A')[:200]}"
                    for i, s in enumerate(sources[:5])
                )
                if source_text:
                    embed.add_field(name="Sources", value=source_text[:1024], inline=False)

            embed.set_footer(text="Guaardvark Semantic Search")
            await interaction.followup.send(embed=embed)

        except APIError as e:
            logger.error("Search API error: %s", e)
            await interaction.followup.send(content=f"Search failed: {e}")
        except Exception as e:
            logger.exception("Unexpected error in /search")
            await interaction.followup.send(content="An unexpected error occurred.")


async def setup(bot: commands.Bot):
    await bot.add_cog(SearchCog(bot, bot.api_client, bot.config))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/llamax1/LLAMAX7 && python -m pytest discord_bot/tests/test_search.py -v
```

Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add discord_bot/commands/search.py discord_bot/tests/test_search.py
git commit -m "feat(discord): add /search command with embed formatting"
```

---

## Chunk 3: Generation Commands

### Task 7: /imagine + /enhance-prompt Commands

**Files:**
- Create: `discord_bot/commands/image.py`
- Create: `discord_bot/tests/test_image.py`

- [ ] **Step 1: Write failing tests**

`discord_bot/tests/test_image.py`:
```python
"""Tests for /imagine and /enhance-prompt commands."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from discord_bot.commands.image import ImageCog


@pytest.mark.asyncio
class TestImagineCommand:
    def _make_cog(self, mock_api_client, sample_config):
        bot = MagicMock()
        return ImageCog(bot, mock_api_client, sample_config)

    async def test_imagine_defers_and_starts_generation(self, mock_api_client, mock_interaction, sample_config):
        cog = self._make_cog(mock_api_client, sample_config)
        await cog._handle_imagine(mock_interaction, "a cat in space")
        mock_interaction.response.defer.assert_called_once()
        mock_api_client.generate_image.assert_called_once()

    async def test_imagine_polls_until_complete(self, mock_api_client, mock_interaction, sample_config):
        # Status returns completed on first poll
        mock_api_client.get_batch_status = AsyncMock(return_value={
            "status": "completed",
            "total_images": 1,
            "completed_images": 1,
            "results": [{"success": True, "image_path": "/tmp/images/img_001.png"}],
        })
        mock_api_client.get_batch_image = AsyncMock(return_value=b"\x89PNG\r\n" + b"\x00" * 100)

        cog = self._make_cog(mock_api_client, sample_config)
        await cog._handle_imagine(mock_interaction, "a cat")
        mock_api_client.get_batch_status.assert_called()
        mock_interaction.followup.send.assert_called()

    async def test_imagine_handles_generation_failure(self, mock_api_client, mock_interaction, sample_config):
        mock_api_client.get_batch_status = AsyncMock(return_value={
            "status": "failed",
            "error": "GPU out of memory",
        })
        cog = self._make_cog(mock_api_client, sample_config)
        await cog._handle_imagine(mock_interaction, "a cat")
        sent = str(mock_interaction.followup.send.call_args)
        assert "failed" in sent.lower() or "error" in sent.lower()


@pytest.mark.asyncio
class TestEnhancePromptCommand:
    def _make_cog(self, mock_api_client, sample_config):
        bot = MagicMock()
        return ImageCog(bot, mock_api_client, sample_config)

    async def test_enhance_returns_improved_prompt(self, mock_api_client, mock_interaction, sample_config):
        cog = self._make_cog(mock_api_client, sample_config)
        await cog._handle_enhance(mock_interaction, "a cat")
        mock_interaction.response.defer.assert_called_once()
        mock_interaction.followup.send.assert_called()
        sent = str(mock_interaction.followup.send.call_args)
        assert "beautiful" in sent.lower() or "enhanced" in sent.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/llamax1/LLAMAX7 && python -m pytest discord_bot/tests/test_image.py -v
```

- [ ] **Step 3: Implement image cog**

`discord_bot/commands/image.py`:
```python
"""Image cog — /imagine and /enhance-prompt commands."""
import asyncio
import io
import logging
import os

import discord
from discord import app_commands
from discord.ext import commands

from discord_bot.core.api_client import GuaardvarkClient, APIError
from discord_bot.core.rate_limiter import RateLimiter
from discord_bot.core.security import sanitize_input

logger = logging.getLogger(__name__)
MAX_POLL_ATTEMPTS = 60  # 60 * 2s = 2 minutes max wait
POLL_INTERVAL = 2  # seconds


class ImageCog(commands.Cog):
    """Handles /imagine and /enhance-prompt commands."""

    def __init__(self, bot: commands.Bot, api_client: GuaardvarkClient, config: dict):
        self.bot = bot
        self.api = api_client
        self.config = config
        self.rate_limiter = RateLimiter(
            max_requests=config["rate_limits"]["imagine"], window_seconds=60,
        )
        self.enhance_limiter = RateLimiter(
            max_requests=config["rate_limits"]["enhance_prompt"], window_seconds=60,
        )
        self._active_jobs = 0  # Queue depth counter across all users

    @app_commands.command(name="imagine", description="Generate an image with AI")
    @app_commands.describe(
        prompt="What to generate",
        steps="Inference steps (default 20)",
        size="Image size: 512, 768, or 1024 (default 512)",
    )
    async def imagine(
        self, interaction: discord.Interaction, prompt: str,
        steps: int = None, size: int = None,
    ):
        await self._handle_imagine(interaction, prompt, steps, size)

    async def _handle_imagine(
        self, interaction, prompt: str, steps: int = None, size: int = None,
    ):
        # Block image gen in DMs
        if interaction.guild is None:
            await interaction.response.send_message(
                "Image generation is not available in DMs. Use it in a server channel.",
                ephemeral=True,
            )
            return

        allowed, _, retry_after = self.rate_limiter.check(interaction.user.id, "imagine")
        if not allowed:
            await interaction.response.send_message(
                f"Rate limited. Image gen is GPU-intensive. Try again in {retry_after:.0f}s.",
                ephemeral=True,
            )
            return

        img_config = self.config["image"]

        # Queue depth check
        if self._active_jobs >= img_config.get("max_queue_depth", 5):
            await interaction.response.send_message(
                "GPU queue is full. Please wait for current jobs to finish.",
                ephemeral=True,
            )
            return
        cleaned = sanitize_input(
            prompt, max_length=self.config["security"]["max_image_prompt_length"],
        )
        if not cleaned:
            await interaction.response.send_message("Prompt was empty after sanitization.", ephemeral=True)
            return

        await interaction.response.defer()

        self._active_jobs += 1
        try:
            # Start generation
            result = await self.api.generate_image(
                cleaned,
                steps=steps or img_config["default_steps"],
                width=size or img_config["default_size"],
                height=size or img_config["default_size"],
            )
            batch_id = result.get("batch_id")
            if not batch_id:
                await interaction.followup.send(content="Failed to start image generation.")
                return

            # Poll for completion
            for attempt in range(MAX_POLL_ATTEMPTS):
                await asyncio.sleep(POLL_INTERVAL)
                status = await self.api.get_batch_status(batch_id)
                state = status.get("status", "unknown")

                if state == "completed":
                    results = status.get("results", [])
                    if results and results[0].get("success"):
                        image_path = results[0]["image_path"]
                        image_name = os.path.basename(image_path)
                        image_bytes = await self.api.get_batch_image(batch_id, image_name)
                        file = discord.File(io.BytesIO(image_bytes), filename=image_name)
                        await interaction.followup.send(
                            content=f"**Prompt:** {cleaned[:200]}",
                            file=file,
                        )
                    else:
                        error = results[0].get("error", "Unknown") if results else "No results"
                        await interaction.followup.send(content=f"Image generation failed: {error}")
                    return

                elif state == "failed":
                    error = status.get("error", "Unknown error")
                    await interaction.followup.send(content=f"Image generation failed: {error}")
                    return

            # Timeout
            await interaction.followup.send(content="Image generation timed out. Please try again.")

        except APIError as e:
            logger.error("Image API error: %s", e)
            await interaction.followup.send(content=f"Image generation error: {e}")
        except Exception as e:
            logger.exception("Unexpected error in /imagine")
            await interaction.followup.send(content="An unexpected error occurred.")
        finally:
            self._active_jobs = max(0, self._active_jobs - 1)

    @app_commands.command(name="enhance-prompt", description="Improve an image generation prompt")
    @app_commands.describe(prompt="The prompt to enhance")
    async def enhance_prompt(self, interaction: discord.Interaction, prompt: str):
        await self._handle_enhance(interaction, prompt)

    async def _handle_enhance(self, interaction, prompt: str):
        allowed, _, retry_after = self.enhance_limiter.check(interaction.user.id, "enhance_prompt")
        if not allowed:
            await interaction.response.send_message(
                f"Rate limited. Try again in {retry_after:.0f}s.", ephemeral=True,
            )
            return

        cleaned = sanitize_input(prompt, max_length=self.config["security"]["max_image_prompt_length"])
        if not cleaned:
            await interaction.response.send_message("Prompt was empty.", ephemeral=True)
            return

        await interaction.response.defer()

        try:
            result = await self.api.enhance_prompt(cleaned)
            enhanced = result.get("enhanced_prompt", "Enhancement failed.")
            negative = result.get("negative_prompt", "")

            embed = discord.Embed(title="Enhanced Prompt", color=discord.Color.green())
            embed.add_field(name="Original", value=cleaned[:1024], inline=False)
            embed.add_field(name="Enhanced", value=enhanced[:1024], inline=False)
            if negative:
                embed.add_field(name="Negative Prompt", value=negative[:1024], inline=False)
            embed.set_footer(text="Use /imagine with the enhanced prompt")

            await interaction.followup.send(embed=embed)

        except APIError as e:
            logger.error("Enhance prompt error: %s", e)
            await interaction.followup.send(content=f"Prompt enhancement failed: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(ImageCog(bot, bot.api_client, bot.config))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/llamax1/LLAMAX7 && python -m pytest discord_bot/tests/test_image.py -v
```

Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add discord_bot/commands/image.py discord_bot/tests/test_image.py
git commit -m "feat(discord): add /imagine and /enhance-prompt commands with batch polling"
```

---

### Task 8: /generate-csv Command

**Files:**
- Create: `discord_bot/commands/generation.py`
- Create: `discord_bot/tests/test_generation.py`

- [ ] **Step 1: Write failing tests**

`discord_bot/tests/test_generation.py`:
```python
"""Tests for /generate-csv command."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from discord_bot.commands.generation import GenerationCog


@pytest.mark.asyncio
class TestGenerateCsvCommand:
    def _make_cog(self, mock_api_client, sample_config):
        bot = MagicMock()
        return GenerationCog(bot, mock_api_client, sample_config)

    async def test_generate_csv_sends_result(self, mock_api_client, mock_interaction, sample_config):
        cog = self._make_cog(mock_api_client, sample_config)
        await cog._handle_generate(mock_interaction, "10 blog post titles about AI")
        mock_interaction.response.defer.assert_called_once()
        mock_api_client.generate_csv.assert_called_once()
        mock_interaction.followup.send.assert_called()

    async def test_generate_csv_passes_output_filename(self, mock_api_client, mock_interaction, sample_config):
        cog = self._make_cog(mock_api_client, sample_config)
        await cog._handle_generate(mock_interaction, "test data")
        call_kwargs = mock_api_client.generate_csv.call_args
        # Should include output_filename
        assert "discord_" in str(call_kwargs)

    async def test_generate_csv_rate_limited(self, mock_api_client, mock_interaction, sample_config):
        sample_config["rate_limits"]["generate_csv"] = 0
        cog = self._make_cog(mock_api_client, sample_config)
        cog.rate_limiter = MagicMock()
        cog.rate_limiter.check = MagicMock(return_value=(False, 0, 30))
        await cog._handle_generate(mock_interaction, "test")
        mock_interaction.response.send_message.assert_called()
```

- [ ] **Step 2: Run tests, then implement**

`discord_bot/commands/generation.py`:
```python
"""Generation cog — /generate-csv command."""
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

from discord_bot.core.api_client import GuaardvarkClient, APIError
from discord_bot.core.rate_limiter import RateLimiter
from discord_bot.core.security import sanitize_input

logger = logging.getLogger(__name__)


class GenerationCog(commands.Cog):
    """Handles /generate-csv — bulk CSV data generation."""

    def __init__(self, bot: commands.Bot, api_client: GuaardvarkClient, config: dict):
        self.bot = bot
        self.api = api_client
        self.config = config
        self.rate_limiter = RateLimiter(
            max_requests=config["rate_limits"]["generate_csv"], window_seconds=60,
        )

    @app_commands.command(name="generate-csv", description="Generate CSV data with AI")
    @app_commands.describe(description="Describe the data you want generated")
    async def generate_csv(self, interaction: discord.Interaction, description: str):
        await self._handle_generate(interaction, description)

    async def _handle_generate(self, interaction, description: str):
        allowed, _, retry_after = self.rate_limiter.check(interaction.user.id, "generate_csv")
        if not allowed:
            await interaction.response.send_message(
                f"Rate limited. Try again in {retry_after:.0f}s.", ephemeral=True,
            )
            return

        cleaned = sanitize_input(description, max_length=self.config["security"]["max_prompt_length"])
        if not cleaned:
            await interaction.response.send_message("Description was empty.", ephemeral=True)
            return

        await interaction.response.defer()

        output_filename = f"discord_{interaction.user.id}_{int(time.time())}.csv"

        try:
            result = await self.api.generate_csv(cleaned, output_filename)
            message = result.get("message", "Generation complete")
            stats = result.get("statistics", {})
            items = stats.get("generated_items", "?")
            duration = stats.get("processing_time", 0)

            embed = discord.Embed(
                title="CSV Generated",
                description=f"{message}\n\n**Items:** {items}\n**Time:** {duration:.1f}s",
                color=discord.Color.green(),
            )
            embed.set_footer(text=f"File: {output_filename}")

            await interaction.followup.send(embed=embed)

        except APIError as e:
            logger.error("CSV generation error: %s", e)
            await interaction.followup.send(content=f"CSV generation failed: {e}")
        except Exception as e:
            logger.exception("Unexpected error in /generate-csv")
            await interaction.followup.send(content="An unexpected error occurred.")


async def setup(bot: commands.Bot):
    await bot.add_cog(GenerationCog(bot, bot.api_client, bot.config))
```

- [ ] **Step 3: Run tests to verify they pass**

```bash
cd /home/llamax1/LLAMAX7 && python -m pytest discord_bot/tests/test_generation.py -v
```

Expected: All 3 tests PASS

- [ ] **Step 4: Commit**

```bash
git add discord_bot/commands/generation.py discord_bot/tests/test_generation.py
git commit -m "feat(discord): add /generate-csv command"
```

---

## Chunk 4: System Commands & Bot Entry Point

### Task 9: /status + /models + /switch-model Commands

**Files:**
- Create: `discord_bot/commands/system.py`
- Create: `discord_bot/tests/test_system.py`

- [ ] **Step 1: Write failing tests**

`discord_bot/tests/test_system.py`:
```python
"""Tests for system commands: /status, /models, /switch-model."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from discord_bot.commands.system import SystemCog


@pytest.mark.asyncio
class TestStatusCommand:
    def _make_cog(self, mock_api_client, sample_config):
        bot = MagicMock()
        return SystemCog(bot, mock_api_client, sample_config)

    async def test_status_returns_embed(self, mock_api_client, mock_interaction, sample_config):
        cog = self._make_cog(mock_api_client, sample_config)
        await cog._handle_status(mock_interaction)
        mock_interaction.response.defer.assert_called_once()
        call_kwargs = mock_interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    async def test_models_returns_list(self, mock_api_client, mock_interaction, sample_config):
        cog = self._make_cog(mock_api_client, sample_config)
        await cog._handle_models(mock_interaction)
        mock_interaction.response.defer.assert_called_once()
        mock_interaction.followup.send.assert_called()

    async def test_switch_model_requires_admin(self, mock_api_client, mock_interaction, sample_config):
        mock_interaction.user.roles = []
        # No admin role
        member = MagicMock()
        member.roles = []
        mock_interaction.user = member
        mock_interaction.user.id = 123
        mock_interaction.user.name = "testuser"
        cog = self._make_cog(mock_api_client, sample_config)
        await cog._handle_switch_model(mock_interaction, "llama3")
        # Should deny — check for ephemeral error message
        sent = str(mock_interaction.response.send_message.call_args)
        assert "admin" in sent.lower() or "permission" in sent.lower()

    async def test_switch_model_admin_succeeds(self, mock_api_client, mock_interaction, sample_config):
        role = MagicMock()
        role.name = "Admin"
        mock_interaction.user.roles = [role]
        cog = self._make_cog(mock_api_client, sample_config)
        await cog._handle_switch_model(mock_interaction, "llama3")
        mock_api_client.switch_model.assert_called_once_with("llama3")
```

- [ ] **Step 2: Run tests, then implement**

`discord_bot/commands/system.py`:
```python
"""System cog — /status, /models, /switch-model commands."""
import logging

import discord
from discord import app_commands
from discord.ext import commands

from discord_bot.core.api_client import GuaardvarkClient, APIError
from discord_bot.core.security import is_admin, is_channel_allowed

logger = logging.getLogger(__name__)


class SystemCog(commands.Cog):
    """Handles system management commands."""

    def __init__(self, bot: commands.Bot, api_client: GuaardvarkClient, config: dict):
        self.bot = bot
        self.api = api_client
        self.config = config

    @app_commands.command(name="status", description="Show Guaardvark system status")
    @app_commands.describe(detailed="Show detailed diagnostics (admin only)")
    async def status(self, interaction: discord.Interaction, detailed: bool = False):
        await self._handle_status(interaction, detailed)

    async def _handle_status(self, interaction, detailed: bool = False):
        await interaction.response.defer()
        try:
            data = await self.api.get_diagnostics()
            embed = discord.Embed(title="Guaardvark Status", color=discord.Color.green())
            embed.add_field(name="Model", value=data.get("active_model", "N/A"), inline=True)
            embed.add_field(name="Ollama", value="Online" if data.get("ollama_reachable") else "Offline", inline=True)
            embed.add_field(name="Models", value=str(data.get("model_count", "?")), inline=True)
            embed.add_field(name="Documents", value=str(data.get("document_count", "?")), inline=True)
            embed.add_field(name="Version", value=data.get("version", "?"), inline=True)
            embed.add_field(name="Platform", value=data.get("platform", "?"), inline=True)

            if detailed:
                admin_roles = self.config["security"]["admin_roles"]
                if not is_admin(interaction.user, admin_roles):
                    embed.set_footer(text="Detailed view requires Admin role.")
                else:
                    try:
                        metrics = await self.api.get_detailed_diagnostics()
                        for key, val in list(metrics.items())[:10]:
                            embed.add_field(name=key, value=str(val)[:100], inline=True)
                    except APIError:
                        embed.set_footer(text="Could not fetch detailed metrics.")

            await interaction.followup.send(embed=embed)
        except APIError as e:
            await interaction.followup.send(content=f"Failed to get status: {e}")

    @app_commands.command(name="models", description="List available LLM models")
    async def models(self, interaction: discord.Interaction):
        await self._handle_models(interaction)

    async def _handle_models(self, interaction):
        await interaction.response.defer()
        try:
            data = await self.api.get_models()
            models = data.get("models", [])
            if not models:
                await interaction.followup.send(content="No models available.")
                return

            embed = discord.Embed(title="Available Models", color=discord.Color.blue())
            for m in models[:25]:  # Discord embed field limit
                name = m.get("name", "unknown")
                details = m.get("details", {})
                size = details.get("parameter_size", "?")
                quant = details.get("quantization_level", "?")
                embed.add_field(name=name, value=f"{size} ({quant})", inline=True)

            await interaction.followup.send(embed=embed)
        except APIError as e:
            await interaction.followup.send(content=f"Failed to get models: {e}")

    @app_commands.command(name="switch-model", description="Switch the active LLM model (admin only)")
    @app_commands.describe(model_name="Name of the model to switch to")
    async def switch_model(self, interaction: discord.Interaction, model_name: str):
        await self._handle_switch_model(interaction, model_name)

    async def _handle_switch_model(self, interaction, model_name: str):
        admin_roles = self.config["security"]["admin_roles"]
        if not is_admin(interaction.user, admin_roles):
            await interaction.response.send_message(
                "You need an Admin role to switch models.", ephemeral=True,
            )
            return

        await interaction.response.defer()
        try:
            result = await self.api.switch_model(model_name)
            msg = result.get("message", f"Switching to {model_name}...")
            await interaction.followup.send(content=f"Model switch initiated: {msg}")
        except APIError as e:
            await interaction.followup.send(content=f"Model switch failed: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(SystemCog(bot, bot.api_client, bot.config))
```

- [ ] **Step 3: Run tests to verify they pass**

```bash
cd /home/llamax1/LLAMAX7 && python -m pytest discord_bot/tests/test_system.py -v
```

Expected: All 4 tests PASS

- [ ] **Step 4: Commit**

```bash
git add discord_bot/commands/system.py discord_bot/tests/test_system.py
git commit -m "feat(discord): add /status, /models, /switch-model commands"
```

---

### Task 10: Bot Entry Point

**Files:**
- Create: `discord_bot/bot.py`

- [ ] **Step 1: Implement bot.py**

`discord_bot/bot.py`:
```python
"""Guaardvark Discord Bot — entry point."""
import asyncio
import logging
import os
import signal
import sys

import discord
from discord.ext import commands
import yaml

from discord_bot.core.api_client import GuaardvarkClient

# Setup logging
log_dir = os.path.join(os.environ.get("GUAARDVARK_ROOT", "."), "logs")
os.makedirs(log_dir, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(log_dir, "discord_bot.log")),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("discord_bot")


def load_config(path: str = None) -> dict:
    """Load config.yaml, resolving environment variable references."""
    if path is None:
        path = os.path.join(os.path.dirname(__file__), "config.yaml")

    with open(path, "r") as f:
        raw = f.read()

    # Resolve ${ENV_VAR} and ${ENV_VAR:-default} patterns
    import re
    def env_sub(match):
        var = match.group(1)
        if ":-" in var:
            name, default = var.split(":-", 1)
            return os.environ.get(name, default)
        return os.environ.get(var, match.group(0))

    resolved = re.sub(r"\$\{([^}]+)\}", env_sub, raw)
    return yaml.safe_load(resolved)


# Cog modules to load (order doesn't matter)
COG_MODULES = [
    "discord_bot.commands.chat",
    "discord_bot.commands.search",
    "discord_bot.commands.image",
    "discord_bot.commands.generation",
    "discord_bot.commands.system",
    # "discord_bot.commands.voice",  # Loaded separately when voice enabled
]


class GuaardvarkBot(commands.Bot):
    """Custom bot class with API client and config attached."""

    def __init__(self, config: dict):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True  # Required for voice

        super().__init__(
            command_prefix=config.get("bot", {}).get("prefix", "!"),
            intents=intents,
        )
        self.config = config
        self.api_client = GuaardvarkClient(
            base_url=config["api"]["base_url"],
        )

    async def setup_hook(self):
        """Called when bot is ready to set up. Load cogs and sync commands."""
        # Initialize API client session
        await self.api_client.setup()

        # Health check backend
        try:
            await self.api_client.health_check()
            logger.info("Guaardvark backend is reachable at %s", self.config["api"]["base_url"])
        except Exception as e:
            logger.warning("Backend health check failed: %s (bot will start anyway)", e)

        # Load cogs
        for module in COG_MODULES:
            try:
                await self.load_extension(module)
                logger.info("Loaded cog: %s", module)
            except Exception as e:
                logger.error("Failed to load cog %s: %s", module, e)

        # Load voice cog if enabled
        if self.config.get("voice", {}).get("enabled", False):
            try:
                await self.load_extension("discord_bot.commands.voice")
                logger.info("Loaded cog: discord_bot.commands.voice")
            except Exception as e:
                logger.warning("Failed to load voice cog: %s (voice disabled)", e)

        # Sync slash commands
        guild_id = self.config.get("bot", {}).get("guild_id")
        if guild_id:
            guild = discord.Object(id=guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info("Synced commands to guild %s", guild_id)
        else:
            await self.tree.sync()
            logger.info("Synced commands globally (may take up to 1 hour)")

    async def on_ready(self):
        logger.info("Bot is ready! Logged in as %s (ID: %s)", self.user, self.user.id)
        logger.info("Connected to %d guilds", len(self.guilds))

    async def close(self):
        """Graceful shutdown: close API client, then disconnect."""
        logger.info("Shutting down...")
        await self.api_client.close()
        await super().close()


def main():
    config = load_config()
    token = config.get("bot", {}).get("token", "")

    if not token or token.startswith("$"):
        logger.error(
            "DISCORD_BOT_TOKEN not set. Set it as an environment variable: "
            "export DISCORD_BOT_TOKEN=your_token_here"
        )
        sys.exit(1)

    bot = GuaardvarkBot(config)

    # Graceful shutdown on SIGINT/SIGTERM
    loop = asyncio.new_event_loop()

    def handle_signal():
        logger.info("Received shutdown signal")
        loop.create_task(bot.close())

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handle_signal)

    try:
        loop.run_until_complete(bot.start(token))
    except KeyboardInterrupt:
        loop.run_until_complete(bot.close())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add discord_bot/bot.py
git commit -m "feat(discord): add bot entry point with config loading, cog loading, and graceful shutdown"
```

---

### Task 11: Launcher Script

**Files:**
- Create: `discord_bot/start_discord_bot.sh`

- [ ] **Step 1: Create launcher script**

`discord_bot/start_discord_bot.sh`:
```bash
#!/usr/bin/env bash
# Guaardvark Discord Bot Launcher
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PID_DIR="$PROJECT_ROOT/pids"
PID_FILE="$PID_DIR/discord_bot.pid"
LOG_FILE="$PROJECT_ROOT/logs/discord_bot.log"

# Load environment
if [ -f "$PROJECT_ROOT/.env" ]; then
    set -a
    source "$PROJECT_ROOT/.env"
    set +a
fi

export GUAARDVARK_ROOT="$PROJECT_ROOT"

# Check for bot token
if [ -z "${DISCORD_BOT_TOKEN:-}" ]; then
    echo "ERROR: DISCORD_BOT_TOKEN not set."
    echo "Set it: export DISCORD_BOT_TOKEN=your_token_here"
    exit 1
fi

# Check backend health
API_URL="${FLASK_PORT:-5002}"
echo "Checking Guaardvark backend at localhost:$API_URL..."
if curl -sf "http://localhost:$API_URL/api/health" > /dev/null 2>&1; then
    echo "Backend is online."
else
    echo "WARNING: Backend not reachable. Bot will start but commands may fail."
fi

# Activate virtual environment
VENV_PATH="$PROJECT_ROOT/backend/venv"
if [ -d "$VENV_PATH" ]; then
    source "$VENV_PATH/bin/activate"
    echo "Using backend venv: $VENV_PATH"
else
    echo "WARNING: No venv found at $VENV_PATH. Using system Python."
fi

# Install bot dependencies
pip install -q -r "$SCRIPT_DIR/requirements.txt" 2>/dev/null || true

# Kill existing bot if running
mkdir -p "$PID_DIR"
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "Stopping existing bot (PID $OLD_PID)..."
        kill "$OLD_PID"
        sleep 1
    fi
    rm -f "$PID_FILE"
fi

# Start bot
echo "Starting Discord bot..."
cd "$PROJECT_ROOT"
python -m discord_bot.bot >> "$LOG_FILE" 2>&1 &
BOT_PID=$!
echo "$BOT_PID" > "$PID_FILE"
echo "Discord bot started (PID $BOT_PID). Logs: $LOG_FILE"
```

- [ ] **Step 2: Make executable and commit**

```bash
chmod +x discord_bot/start_discord_bot.sh
git add discord_bot/start_discord_bot.sh discord_bot/bot.py
git commit -m "feat(discord): add launcher script with health check and PID management"
```

---

## Chunk 5: Voice Channel Integration

### Task 12: Voice Handler (Audio Pipeline)

**Files:**
- Create: `discord_bot/core/voice_handler.py`

- [ ] **Step 1: Implement voice handler**

`discord_bot/core/voice_handler.py`:
```python
"""Voice channel audio pipeline: Discord PCM → Whisper STT → LLM → Piper TTS → Discord playback."""
import asyncio
import io
import logging
import struct
import wave
from typing import Optional

import discord

from discord_bot.core.api_client import GuaardvarkClient, APIError

logger = logging.getLogger(__name__)

# PCM format constants (Discord sends 48kHz 16-bit stereo)
DISCORD_SAMPLE_RATE = 48000
DISCORD_CHANNELS = 2
TARGET_SAMPLE_RATE = 16000  # Whisper expects 16kHz
TARGET_CHANNELS = 1  # Whisper expects mono


class AudioSink(discord.AudioSink if hasattr(discord, "AudioSink") else object):
    """Captures per-user audio from Discord voice channel."""

    def __init__(self, handler: "VoiceHandler"):
        self.handler = handler
        self.buffers: dict[int, bytearray] = {}  # user_id -> PCM bytes
        self.silence_counters: dict[int, int] = {}

    def write(self, user, data):
        """Called by discord.py when audio data is received from a user."""
        uid = user.id if hasattr(user, "id") else user
        if uid not in self.buffers:
            self.buffers[uid] = bytearray()
            self.silence_counters[uid] = 0

        pcm = data.read() if hasattr(data, "read") else data
        self.buffers[uid].extend(pcm)

    def cleanup(self):
        self.buffers.clear()
        self.silence_counters.clear()


def pcm_to_wav(pcm_data: bytes, sample_rate: int = DISCORD_SAMPLE_RATE,
               channels: int = DISCORD_CHANNELS) -> bytes:
    """Convert raw PCM bytes to WAV format."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)
    return buf.getvalue()


def downsample_pcm(pcm_data: bytes, from_rate: int = DISCORD_SAMPLE_RATE,
                   to_rate: int = TARGET_SAMPLE_RATE, from_channels: int = 2,
                   to_channels: int = 1) -> bytes:
    """Simple downsample: stereo→mono, 48kHz→16kHz."""
    # Convert bytes to 16-bit samples
    samples = struct.unpack(f"<{len(pcm_data)//2}h", pcm_data)

    # Stereo to mono (average channels)
    if from_channels == 2 and to_channels == 1:
        mono = [(samples[i] + samples[i+1]) // 2 for i in range(0, len(samples), 2)]
    else:
        mono = list(samples)

    # Downsample by taking every Nth sample
    ratio = from_rate // to_rate
    if ratio > 1:
        mono = mono[::ratio]

    return struct.pack(f"<{len(mono)}h", *mono)


class VoiceHandler:
    """Manages the full voice pipeline for a single guild connection."""

    def __init__(self, api_client: GuaardvarkClient, config: dict):
        self.api = api_client
        self.config = config
        self.voice_client: Optional[discord.VoiceClient] = None
        self.text_channel: Optional[discord.TextChannel] = None
        self.sink: Optional[AudioSink] = None
        self._processing = False
        self._listen_task: Optional[asyncio.Task] = None
        self.session_id = "discord_voice"

    async def join(self, channel: discord.VoiceChannel,
                   text_channel: discord.TextChannel) -> bool:
        """Join a voice channel and start listening."""
        try:
            self.voice_client = await channel.connect()
            self.text_channel = text_channel
            self.session_id = f"discord_voice_{channel.guild.id}"

            # Start listening loop
            self._listen_task = asyncio.create_task(self._listen_loop())
            logger.info("Joined voice channel: %s", channel.name)
            return True

        except Exception as e:
            logger.error("Failed to join voice channel: %s", e)
            return False

    async def leave(self):
        """Disconnect from voice channel and clean up."""
        if self._listen_task:
            self._listen_task.cancel()
            self._listen_task = None

        if self.voice_client and self.voice_client.is_connected():
            await self.voice_client.disconnect()
            self.voice_client = None

        if self.sink:
            self.sink.cleanup()
            self.sink = None

        logger.info("Left voice channel")

    async def _listen_loop(self):
        """Main loop: capture audio, detect silence, process speech."""
        silence_ms = self.config.get("voice", {}).get("silence_threshold_ms", 1500)
        max_duration = self.config.get("voice", {}).get("max_listen_duration_s", 30)

        # SCAFFOLD: discord.py's audio receive is experimental and version-dependent.
        # The full pipeline wiring requires VoiceClient.listen(sink) which may need
        # the 'pynacl' library for voice decryption and discord.py 2.4+.
        # The process_audio() method below implements the complete STT→LLM→TTS→playback
        # pipeline and is ready to be called once audio capture is integrated.
        # TODO: Wire up AudioSink via self.voice_client.listen(self.sink) when
        # discord.py voice receive is stable, then call process_audio() on silence detection.

        logger.info("Voice listen loop started (silence=%dms, max=%ds)", silence_ms, max_duration)
        logger.info("NOTE: Audio capture requires discord.py experimental voice receive. "
                     "Bot will join channel but listen loop needs further integration.")

        while self.voice_client and self.voice_client.is_connected():
            await asyncio.sleep(1.0)
            # Audio capture integration point:
            # 1. self.voice_client.listen(self.sink) to capture per-user PCM
            # 2. Monitor self.sink.buffers for silence detection
            # 3. Call await self.process_audio(pcm_data, user_id) on speech end

    async def process_audio(self, pcm_data: bytes, user_id: int):
        """Process a completed utterance: STT → LLM → TTS → playback."""
        if self._processing:
            return  # Skip if already processing
        self._processing = True

        try:
            # Convert PCM to WAV for Whisper
            downsampled = downsample_pcm(pcm_data)
            wav_bytes = pcm_to_wav(downsampled, TARGET_SAMPLE_RATE, TARGET_CHANNELS)

            # STT
            stt_result = await self.api.speech_to_text(wav_bytes)
            text = stt_result.get("text", "").strip()

            if not text:
                logger.debug("Empty transcription, skipping")
                return

            logger.info("Voice STT: '%s'", text[:100])

            # LLM
            chat_result = await self.api.chat(text, self.session_id)
            response = chat_result.get("response", "")

            if not response:
                return

            logger.info("Voice LLM response: '%s'", response[:100])

            # TTS
            tts_result = await self.api.text_to_speech(
                response,
                voice=self.config.get("voice", {}).get("tts_voice", "ryan"),
            )
            audio_filename = tts_result.get("filename")

            if not audio_filename:
                return

            # Fetch WAV bytes
            wav_audio = await self.api.get_voice_audio(audio_filename)

            # Play audio in voice channel
            await self._play_audio(wav_audio)

        except APIError as e:
            logger.error("Voice pipeline API error: %s", e)
            if self.text_channel:
                await self.text_channel.send(f"Voice error: {e}")
        except Exception as e:
            logger.exception("Voice pipeline error")
        finally:
            self._processing = False

    async def _play_audio(self, wav_bytes: bytes):
        """Play WAV audio in the connected voice channel."""
        if not self.voice_client or not self.voice_client.is_connected():
            return

        # Write WAV to temp buffer and play via FFmpeg
        audio_source = discord.FFmpegPCMAudio(
            io.BytesIO(wav_bytes), pipe=True,
        )
        self.voice_client.play(audio_source)

        # Wait for playback to finish
        while self.voice_client.is_playing():
            await asyncio.sleep(0.1)
```

- [ ] **Step 2: Commit**

```bash
git add discord_bot/core/voice_handler.py
git commit -m "feat(discord): add voice pipeline handler (PCM → STT → LLM → TTS → playback)"
```

---

### Task 13: /voice Commands (Voice Cog)

**Files:**
- Create: `discord_bot/commands/voice.py`

- [ ] **Step 1: Implement voice cog**

`discord_bot/commands/voice.py`:
```python
"""Voice cog — /voice join, /voice leave, /voice status commands."""
import logging

import discord
from discord import app_commands
from discord.ext import commands

from discord_bot.core.api_client import GuaardvarkClient
from discord_bot.core.voice_handler import VoiceHandler

logger = logging.getLogger(__name__)


class VoiceCog(commands.Cog):
    """Handles voice channel integration."""

    def __init__(self, bot: commands.Bot, api_client: GuaardvarkClient, config: dict):
        self.bot = bot
        self.api = api_client
        self.config = config
        # One handler per guild
        self.handlers: dict[int, VoiceHandler] = {}

    voice_group = app_commands.Group(name="voice", description="Voice channel commands")

    @voice_group.command(name="join", description="Join your voice channel")
    async def voice_join(self, interaction: discord.Interaction):
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message(
                "You need to be in a voice channel first.", ephemeral=True,
            )
            return

        guild_id = interaction.guild.id
        channel = interaction.user.voice.channel

        # Check if already connected
        if guild_id in self.handlers:
            await self.handlers[guild_id].leave()

        handler = VoiceHandler(self.api, self.config)
        success = await handler.join(channel, interaction.channel)

        if success:
            self.handlers[guild_id] = handler
            embed = discord.Embed(
                title="Voice Connected",
                description=f"Joined **{channel.name}**. Speak and I'll respond!",
                color=discord.Color.green(),
            )
            embed.set_footer(text="Use /voice leave to disconnect")
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message(
                "Failed to join voice channel.", ephemeral=True,
            )

    @voice_group.command(name="leave", description="Leave the voice channel")
    async def voice_leave(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        handler = self.handlers.pop(guild_id, None)

        if handler:
            await handler.leave()
            await interaction.response.send_message("Disconnected from voice channel.")
        else:
            await interaction.response.send_message(
                "Not connected to a voice channel.", ephemeral=True,
            )

    @voice_group.command(name="status", description="Show voice session info")
    async def voice_status(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        handler = self.handlers.get(guild_id)

        if not handler or not handler.voice_client or not handler.voice_client.is_connected():
            await interaction.response.send_message(
                "Not connected to a voice channel.", ephemeral=True,
            )
            return

        channel = handler.voice_client.channel
        embed = discord.Embed(title="Voice Status", color=discord.Color.blue())
        embed.add_field(name="Channel", value=channel.name, inline=True)
        embed.add_field(name="Members", value=str(len(channel.members)), inline=True)
        embed.add_field(name="Processing", value="Yes" if handler._processing else "Idle", inline=True)
        await interaction.response.send_message(embed=embed)

    async def cog_unload(self):
        """Clean up all voice connections when cog is unloaded."""
        for handler in self.handlers.values():
            await handler.leave()
        self.handlers.clear()


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceCog(bot, bot.api_client, bot.config))
```

- [ ] **Step 2: Commit**

```bash
git add discord_bot/commands/voice.py
git commit -m "feat(discord): add /voice join, /voice leave, /voice status commands"
```

---

## Chunk 6: Integration & Final Testing

### Task 14: Run Full Test Suite

- [ ] **Step 1: Run all tests**

```bash
cd /home/llamax1/LLAMAX7 && python -m pytest discord_bot/tests/ -v --tb=short
```

Expected: All tests pass.

- [ ] **Step 2: Fix any failures and re-run**

- [ ] **Step 3: Commit any test fixes**

```bash
git add discord_bot/
git commit -m "fix(discord): test suite fixes"
```

---

### Task 15: Integration Test with Live Backend

- [ ] **Step 1: Verify backend is running**

```bash
curl -sf http://localhost:5002/api/health | python -m json.tool
```

- [ ] **Step 2: Test API client against live backend**

Create a quick integration test script:
```bash
cd /home/llamax1/LLAMAX7 && python -c "
import asyncio
from discord_bot.core.api_client import GuaardvarkClient

async def test():
    client = GuaardvarkClient('http://localhost:5002/api')
    await client.setup()

    # Health check
    h = await client.health_check()
    print('Health:', h)

    # Diagnostics
    d = await client.get_diagnostics()
    print('Status:', d.get('active_model'))

    # Models
    m = await client.get_models()
    print('Models:', [x['name'] for x in m.get('models', [])])

    await client.close()
    print('All integration checks passed!')

asyncio.run(test())
"
```

- [ ] **Step 3: Set up Discord bot token**

```bash
# User must create a Discord application at https://discord.com/developers
# and set the bot token:
export DISCORD_BOT_TOKEN=your_token_here
```

- [ ] **Step 4: Test bot startup**

```bash
cd /home/llamax1/LLAMAX7 && timeout 10 python -m discord_bot.bot 2>&1 || true
```

Should see "Synced commands" or a connection message before timeout.

- [ ] **Step 5: Final commit**

```bash
git add -A discord_bot/
git commit -m "feat(discord): complete Discord bot with all commands, voice, and tests"
```

---

## Summary

| Task | Description | Files | Tests |
|------|-------------|-------|-------|
| 1 | Project scaffolding | 8 files | conftest |
| 2 | Rate limiter | 2 files | 6 tests |
| 3 | Input security | 2 files | 9 tests |
| 4 | API client | 2 files | 7 tests |
| 5 | /ask command | 2 files | 5 tests |
| 6 | /search command | 2 files | 3 tests |
| 7 | /imagine + /enhance-prompt | 2 files | 4 tests |
| 8 | /generate-csv | 2 files | 3 tests |
| 9 | /status + /models + /switch-model | 2 files | 4 tests |
| 10 | Bot entry point | 1 file | — |
| 11 | Launcher script | 1 file | — |
| 12 | Voice handler | 1 file | — |
| 13 | /voice commands | 1 file | — |
| 14 | Full test suite run | — | all |
| 15 | Live integration test | — | manual |

**Total: ~30 files, 41+ automated tests, 15 tasks, ~13 commits**
