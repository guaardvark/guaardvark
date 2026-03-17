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
    async def generate_image(
        self, prompt: str, steps: int = 20, width: int = 512, height: int = 512
    ) -> dict:
        """POST /batch-image/generate/prompts"""
        return await self._post(
            "/batch-image/generate/prompts",
            json={
                "prompts": [prompt],
                "steps": steps,
                "width": width,
                "height": height,
            },
        )

    async def get_batch_status(self, batch_id: str) -> dict:
        """GET /batch-image/status/<batch_id>"""
        return await self._get(
            f"/batch-image/status/{batch_id}", params={"include_results": "true"}
        )

    async def get_batch_image(self, batch_id: str, image_name: str) -> bytes:
        """GET /batch-image/image/<batch_id>/<image_name>"""
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
        return await self._post(
            "/generate/csv",
            json={
                "type": "single",
                "prompt": description,
                "output_filename": output_filename,
            },
        )

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
        return await self._get("/model/list")

    async def switch_model(self, model_name: str) -> dict:
        """POST /model/set"""
        return await self._post("/model/set", json={"model": model_name})

    # --- Voice ---
    async def speech_to_text(self, audio_bytes: bytes) -> dict:
        """POST /voice/speech-to-text"""
        form = aiohttp.FormData()
        form.add_field(
            "audio", audio_bytes, filename="audio.wav", content_type="audio/wav"
        )
        return await self._post("/voice/speech-to-text", data=form)

    async def text_to_speech(self, text: str, voice: str = "ryan") -> dict:
        """POST /voice/text-to-speech"""
        return await self._post(
            "/voice/text-to-speech", json={"text": text, "voice": voice}
        )

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
