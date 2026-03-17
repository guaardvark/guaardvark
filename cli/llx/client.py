import httpx
from pathlib import Path
from typing import Any

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from llx.config import get_server_url, get_api_key, get_timeout
from llx.global_opts import get_global_timeout


class LlxError(Exception):
    def __init__(self, message: str, status_code: int = 0):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class LlxConnectionError(LlxError):
    pass


class LlxClient:

    def __init__(
        self,
        server_url: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
    ):
        self.server_url = server_url or get_server_url()
        api_key = api_key or get_api_key()
        timeout = timeout or get_global_timeout() or get_timeout()
        headers = {}
        if api_key:
            headers["X-API-Key"] = api_key
        self.http = httpx.Client(
            base_url=self.server_url,
            timeout=float(timeout),
            headers=headers,
        )

    def _handle_response(self, resp: httpx.Response) -> dict:
        try:
            data = resp.json()
        except Exception:
            if resp.status_code >= 400:
                raise LlxError(f"Server returned {resp.status_code}", resp.status_code)
            return {"raw": resp.text}

        if resp.status_code >= 400:
            msg = data.get("error") or data.get("message") or f"HTTP {resp.status_code}"
            raise LlxError(msg, resp.status_code)

        return data

    def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            resp = self._request_with_retry(method, path, **kwargs)
            return self._handle_response(resp)
        except httpx.ConnectError:
            raise LlxConnectionError(
                f"Cannot connect to Guaardvark at {self.server_url}. "
                "Is the server running? Try: ./start.sh"
            )
        except httpx.TimeoutException:
            raise LlxError(
                "Request timed out. The server may be busy processing a long operation.",
                408,
            )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type((httpx.ConnectError, httpx.TimeoutException)),
        reraise=True,
    )
    def _request_with_retry(self, method: str, path: str, **kwargs):
        return self.http.request(method, path, **kwargs)

    def get(self, endpoint: str, **params) -> dict:
        return self._request("GET", endpoint, params=params)

    def post(self, path: str, json: dict | None = None, **kwargs) -> dict:
        return self._request("POST", path, json=json, **kwargs)

    def put(self, path: str, json: dict | None = None) -> dict:
        return self._request("PUT", path, json=json)

    def delete(self, path: str) -> dict:
        return self._request("DELETE", path)

    def upload(self, path: str, file_path: Path, **extra_fields) -> dict:
        with open(file_path, "rb") as f:
            files = {"file": (file_path.name, f)}
            data = {k: str(v) for k, v in extra_fields.items() if v is not None}
            try:
                resp = self.http.post(path, files=files, data=data)
                return self._handle_response(resp)
            except httpx.ConnectError:
                raise LlxConnectionError(
                    f"Cannot connect to Guaardvark at {self.server_url}. "
                    "Is the server running? Try: ./start.sh"
                )

    def upload_with_progress(
        self, path: str, file_path: Path, console=None, **extra_fields
    ) -> dict:
        """Upload a file with a Rich progress bar."""
        from rich.progress import (
            Progress,
            SpinnerColumn,
            BarColumn,
            TextColumn,
            TransferSpeedColumn,
        )

        file_size = file_path.stat().st_size
        data = {k: str(v) for k, v in extra_fields.items() if v is not None}

        with Progress(
            SpinnerColumn(),
            TextColumn("[llx.brand]{task.description}"),
            BarColumn(),
            TextColumn("[llx.dim]{task.completed}/{task.total} bytes[/llx.dim]"),
            TransferSpeedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(f"Uploading {file_path.name}", total=file_size)

            class ProgressReader:
                def __init__(self, fp, callback):
                    self.fp = fp
                    self.callback = callback

                def read(self, size=-1):
                    chunk = self.fp.read(size)
                    if chunk:
                        self.callback(len(chunk))
                    return chunk

            with open(file_path, "rb") as f:
                reader = ProgressReader(f, lambda n: progress.update(task, advance=n))
                files = {"file": (file_path.name, reader)}
                try:
                    resp = self.http.post(path, files=files, data=data)
                    return self._handle_response(resp)
                except httpx.ConnectError:
                    raise LlxConnectionError(
                        f"Cannot connect to Guaardvark at {self.server_url}. "
                        "Is the server running? Try: ./start.sh"
                    )

    def download(self, path: str, dest: Path) -> Path:
        try:
            resp = self.http.get(path)
            if resp.status_code >= 400:
                raise LlxError(
                    f"Download failed: HTTP {resp.status_code}", resp.status_code
                )
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(resp.content)
            return dest
        except httpx.ConnectError:
            raise LlxConnectionError(
                f"Cannot connect to Guaardvark at {self.server_url}. "
                "Is the server running? Try: ./start.sh"
            )


def get_client(server: str | None = None) -> LlxClient:
    return LlxClient(server_url=server)
