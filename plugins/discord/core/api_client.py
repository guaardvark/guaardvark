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
                raise APIError(data.get("error", f"HTTP {resp.status}"), resp.status)
            return self._unwrap(data)

    async def _post(self, path: str, **kwargs) -> dict:
        async with self.session.post(f"{self.base_url}{path}", **kwargs) as resp:
            data = await resp.json()
            if resp.status >= 400:
                raise APIError(data.get("error", f"HTTP {resp.status}"), resp.status)
            return self._unwrap(data)

    async def _get_raw(self, path: str, **kwargs) -> bytes:
        async with self.session.get(f"{self.base_url}{path}", **kwargs) as resp:
            if resp.status >= 400:
                raise APIError(await resp.text(), resp.status)
            return await resp.read()

    # --- Chat ---
    SYSTEM_CONTEXT = (
        "You are the Guaardvark AI assistant — the built-in intelligence of the Guaardvark platform. "
        "You are running RIGHT NOW on a developer's personal desktop: AMD Ryzen 7 9800X3D, 64GB RAM, "
        "NVIDIA RTX 4070 Ti SUPER (16GB VRAM), 1.8TB storage. This is not a cloud service — this is "
        "one machine, self-hosted, running everything locally.\n\n"
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
        """POST /claude/escalate — route chat through Uncle Claude."""
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
    async def generate_image(self, prompt: str, steps: int = 20, width: int = 512, height: int = 512) -> dict:
        """POST /batch-image/generate/prompts"""
        return await self._post("/batch-image/generate/prompts", json={"prompts": [prompt], "steps": steps, "width": width, "height": height})

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

    # --- Health ---
    async def health_check(self) -> dict:
        """GET /health"""
        return await self._get("/health")


class APIError(Exception):
    """Raised when the Guaardvark API returns an error."""
    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message)
        self.status_code = status_code
