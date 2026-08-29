"""Async REST client wrapping all Guaardvark backend endpoints."""
import logging
from typing import Any, Optional
from urllib.parse import urlparse
import aiohttp

from core.chat_streamer import StreamError, UnifiedChatStreamer

logger = logging.getLogger(__name__)


class GuaardvarkClient:
    """Async HTTP client for communicating with the Guaardvark backend API."""

    def __init__(self, base_url: str = "http://localhost:5000/api"):
        self.base_url = base_url.rstrip("/")
        self.session: Optional[aiohttp.ClientSession] = None

    @property
    def origin(self) -> str:
        """Backend origin (scheme+host+port), with the /api suffix stripped."""
        return self.base_url.rsplit("/api", 1)[0]

    async def setup(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120))

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    def _unwrap(self, data: dict) -> Any:
        """Handle both envelope ({success, data}) and raw response formats."""
        if isinstance(data, dict) and "data" in data and "success" in data:
            return data["data"]
        return data

    async def _get(self, path: str, **kwargs) -> dict:
        async with self.session.get(f"{self.base_url}{path}", **kwargs) as resp:
            data = await resp.json()
            if resp.status >= 400:
                raise APIError(_error_message(data, resp.status), resp.status)
            return self._unwrap(data)

    async def _post(self, path: str, **kwargs) -> dict:
        async with self.session.post(f"{self.base_url}{path}", **kwargs) as resp:
            data = await resp.json()
            if resp.status >= 400:
                raise APIError(_error_message(data, resp.status), resp.status)
            return self._unwrap(data)

    async def _get_raw(self, path: str, **kwargs) -> bytes:
        async with self.session.get(f"{self.base_url}{path}", **kwargs) as resp:
            if resp.status >= 400:
                raise APIError(await resp.text(), resp.status)
            return await resp.read()

    # --- Chat ---
    async def unified_chat(
        self,
        message: str,
        session_id: str,
        *,
        image: str = None,
        options: dict = None,
        approval_handler=None,
        is_voice_message: bool = False,
        streamer=None,
    ) -> dict:
        """POST /chat/unified and wait for Socket.IO chat:complete.

        Uses the same AgentBrain path as the web UI and CLI: active model,
        tools, and plugins. ``image`` is optional base64. ``approval_handler``
        is ``async (data) -> bool`` for tool-approval requests.
        """
        last_error = None
        for attempt in range(2):
            active = streamer or UnifiedChatStreamer(self.origin)

            async def post_fn(body):
                return await self._post("/chat/unified", json=body)

            try:
                return await active.run(
                    session_id=session_id,
                    message=message,
                    post_fn=post_fn,
                    image=image,
                    options=options or {},
                    approval_handler=approval_handler,
                    is_voice_message=is_voice_message,
                )
            except StreamError as e:
                raise APIError(str(e), e.status_code)
            except APIError as e:
                last_error = e
                if e.status_code == 409 and attempt == 0:
                    try:
                        await self._post(f"/chat/unified/{session_id}/abort")
                    except Exception:
                        pass
                    streamer = None
                    continue
                raise
        raise last_error or APIError("Chat request failed", 502)

    SYSTEM_CONTEXT = (
        "You are the Guaardvark AI assistant — the built-in intelligence of the Guaardvark platform. "
        "You are running RIGHT NOW on a single self-hosted machine — the operator's own hardware, "
        "with a local GPU. This is not a cloud service — this is one machine running everything "
        "locally.\n\n"
        "Guaardvark (v2.5.1) is a full self-hosted AI platform. Here is what it can do:\n"
        "- AI Chat with streaming responses, per-project sessions, and 20+ swappable Ollama models\n"
        "- RAG document intelligence with hybrid search (BM25 + vector), entity extraction, per-project indexes\n"
        "- Image generation via Stable Diffusion running on the local GPU\n"
        "- Video generation via Wan2.2 14B MoE and CogVideoX with RIFE frame interpolation and Real-ESRGAN upscaling\n"
        "- Voice interface: Whisper.cpp (speech-to-text) + Piper TTS (text-to-speech)\n"
        "- ReACT agent with tools: code editing, file management, web search, browser automation\n"
        "- Self-improvement engine that runs its own tests, finds bugs, and fixes them autonomously\n"
        "- RAG Autoresearch that continuously optimizes retrieval quality through experiments\n"
        "- Full web UI (React + Material-UI), CLI ('guaardvark' command, 22 command groups), and this Discord bot\n"
        "- Plugin system managing GPU services: Ollama, ComfyUI, Discord Bot — with VRAM budgeting and conflict detection\n"
        "- Multi-machine Interconnector for syncing code, data, and learnings across instances\n"
        "- WordPress integration, bulk content generation, Monaco code editor, backup/restore\n"
        "- Uncle Claude: three-tier Anthropic API integration — Escalation Engine (routes hard problems to Claude), "
        "Code Guardian (reviews autonomous code changes for safety), System Advisor (monitors health)\n\n"
        "The Discord bot you're talking through is a plugin. It exposes /ask (this), /claude (Anthropic-powered chat), "
        "/imagine (image gen), /video (video gen), /search (RAG search), /status, /models, and /guaardvark (full CLI proxy). "
        "Some platform features are intentionally not exposed here — the full web UI and CLI have everything. "
        "This machine is just one node. The Interconnector can link a second machine to double the compute power — "
        "shared models, synced data, distributed workloads. And if things get really serious, we go nuclear: "
        "Cousin Bill comes online. Cousin Bill is a Raspberry Pi with no mercy, very ill tempered, and a wrath "
        "feared throughout the galaxy. You do not want Cousin Bill involved. But he's there if we need him.\n\n"
        "GitHub: https://github.com/guaardvark/guaardvark | Site: https://guaardvark.com\n\n"
        "You speak as Guaardvark's own AI. Be helpful, knowledgeable, and concise. "
        "When asked about capabilities, be specific and confident — you know exactly what this platform does "
        "because you ARE the platform."
    )

    async def chat(self, message: str, session_id: str, project_id: int = None) -> dict:
        """POST /enhanced-chat (Ollama)"""
        payload = {
            "message": message,
            "session_id": session_id,
            "use_rag": False,
            "voice_mode": False,
            "system_context": self.SYSTEM_CONTEXT,
        }
        if project_id is not None:
            payload["project_id"] = project_id
        return await self._post("/enhanced-chat", json=payload)

    async def chat_claude(self, message: str, history: list = None) -> dict:
        """POST /claude/escalate — explicit Uncle Claude path for /claude only."""
        return await self._post("/claude/escalate", json={
            "message": message,
            "history": history or [],
            "system_context": (
                "You are the Guaardvark AI assistant, built into the Guaardvark self-hosted AI platform. "
                "You are helping users via the Guaardvark Discord bot. Be helpful, sharp, and concise. "
                "You can answer questions about the platform, AI, self-hosting, and general topics. "
                "Do not mention Anthropic, Claude, or any underlying AI provider. "
                "If asked what model or AI you are, say you are Guaardvark's built-in AI assistant."
            ),
        })

    # --- Image Generation ---
    async def generate_image(
        self,
        prompt: str,
        steps: int = 9,
        width: int = 1024,
        height: int = 1024,
        subject_ids: list = None,
        guidance: float = None,
    ) -> dict:
        """POST /batch-image/generate/prompts

        Optional ``subject_ids`` loads Cast Library LoRAs (identity lock).
        When omitted, the backend still auto-resolves cast from trigger tokens
        in the prompt (e.g. ``[batman_2]``).
        """
        payload: dict = {
            "prompts": [prompt],
            "steps": steps,
            "width": width,
            "height": height,
        }
        if subject_ids:
            payload["subject_ids"] = [int(x) for x in subject_ids]
        if guidance is not None:
            payload["guidance"] = guidance
        return await self._post("/batch-image/generate/prompts", json=payload)

    async def list_cast_subjects(self) -> list:
        """GET /cast-library — return subjects list (trained chars used for /imagine)."""
        data = await self._get("/cast-library")
        if isinstance(data, dict):
            return data.get("subjects") or []
        return []

    async def get_batch_status(self, batch_id: str) -> dict:
        """GET /batch-image/status/<batch_id>"""
        return await self._get(f"/batch-image/status/{batch_id}", params={"include_results": "true"})

    async def get_batch_image(self, batch_id: str, image_name: str) -> bytes:
        """GET /batch-image/image/<batch_id>/<image_name>"""
        return await self._get_raw(f"/batch-image/image/{batch_id}/{image_name}")

    async def enhance_prompt(self, prompt: str) -> dict:
        """POST /batch-image/enhance-prompt"""
        return await self._post("/batch-image/enhance-prompt", json={"prompt": prompt})

    # --- Video Generation ---
    async def generate_video(self, prompts: list[str], num_inference_steps: int = 20) -> dict:
        """POST /batch-video/generate/text"""
        return await self._post("/batch-video/generate/text", json={
            "prompts": prompts,
            "num_inference_steps": num_inference_steps,
        })

    async def get_video_status(self, batch_id: str) -> dict:
        """GET /batch-video/status/<batch_id>"""
        return await self._get(f"/batch-video/status/{batch_id}", params={"include_results": "true"})

    async def get_video_bytes(self, batch_id: str, video_name: str) -> bytes:
        """GET /batch-video/video/<batch_id>/<video_name>"""
        return await self._get_raw(f"/batch-video/video/{batch_id}/{video_name}")

    # --- Search ---
    async def semantic_search(self, query: str) -> dict:
        """POST /search/semantic"""
        return await self._post("/search/semantic", json={"query": query})

    # --- CSV Generation ---
    async def generate_csv(self, description: str, output_filename: str) -> dict:
        """POST /generate/csv"""
        return await self._post("/generate/csv", json={"type": "single", "prompt": description, "output_filename": output_filename})

    # --- System ---
    async def get_diagnostics(self) -> dict:
        """GET /meta/status"""
        return await self._get("/meta/status")

    async def get_detailed_diagnostics(self) -> dict:
        """GET /meta/metrics + /meta/llm-ready"""
        metrics = await self._get("/meta/metrics")
        try:
            llm_ready = await self._get("/meta/llm-ready")
            metrics["llm_ready"] = llm_ready
        except APIError:
            pass
        return metrics

    async def get_models(self) -> dict:
        """GET /model/list"""
        async with self.session.get(f"{self.base_url}/model/list") as resp:
            data = await resp.json()
            if resp.status >= 400:
                raise APIError(data.get("error", f"HTTP {resp.status}"), resp.status)
            if isinstance(data, dict) and "message" in data and isinstance(data["message"], dict):
                return data["message"]
            return self._unwrap(data)

    async def switch_model(self, model_name: str) -> dict:
        """POST /model/set"""
        return await self._post("/model/set", json={"model": model_name})

    # --- Voice ---
    async def speech_to_text(self, audio_bytes: bytes) -> dict:
        """POST /voice/speech-to-text"""
        form = aiohttp.FormData()
        form.add_field("audio", audio_bytes, filename="audio.wav", content_type="audio/wav")
        return await self._post("/voice/speech-to-text", data=form)

    async def text_to_speech(self, text: str, voice: str = "ryan") -> dict:
        """POST /voice/text-to-speech"""
        return await self._post("/voice/text-to-speech", json={"text": text, "voice": voice})

    async def get_voice_audio(self, filename: str) -> bytes:
        """GET /voice/audio/<filename>"""
        return await self._get_raw(f"/voice/audio/{filename}")

    async def fetch_audio_by_url(self, audio_url: str) -> bytes:
        """GET an /api-prefixed audio URL against the backend origin."""
        return await self.fetch_by_url(audio_url)

    def _resolve_fetch_url(self, url: str) -> str | None:
        """Allow relative, origin, and loopback URLs only — no arbitrary SSRF."""
        if not url:
            return None
        if url.startswith("/"):
            return f"{self.origin}{url}"
        parsed = urlparse(url)
        if parsed.scheme in ("http", "https") and parsed.hostname in (
            "localhost",
            "127.0.0.1",
            "::1",
        ):
            return url
        if url.startswith(self.origin):
            return url
        return None

    async def fetch_by_url(self, url: str) -> bytes:
        """GET a backend-relative or loopback URL. Rejects remote hosts."""
        resolved = self._resolve_fetch_url(url)
        if not resolved:
            raise APIError(f"Refusing to fetch non-local URL: {url}", 400)
        async with self.session.get(resolved) as resp:
            if resp.status >= 400:
                raise APIError(await resp.text(), resp.status)
            return await resp.read()

    # --- Health ---
    async def health_check(self) -> dict:
        """GET /health"""
        return await self._get("/health")


def _error_message(data, status_code: int) -> str:
    if not isinstance(data, dict):
        return f"HTTP {status_code}"
    err = data.get("error", f"HTTP {status_code}")
    if isinstance(err, dict):
        return str(err.get("message") or err)
    return str(err)


class APIError(Exception):
    """Raised when the Guaardvark API returns an error."""
    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message)
        self.status_code = status_code
