"""Contracts shared by every connection provider.

A provider is a module exposing ``SPEC`` plus ``test()`` and — for the social
family — ``publish()``. Declaring capabilities as data rather than branching on
provider names in the UI is what keeps the Publish modal generic: it disables
incompatible targets and computes character limits straight from ``SPEC``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

# Connection families.
FAMILY_SOCIAL = "social"
FAMILY_AI_PROVIDER = "ai_provider"
FAMILY_MCP_SERVER = "mcp_server"
FAMILY_INTEGRATION = "integration"

# Authentication kinds.
AUTH_OAUTH2 = "oauth2"
AUTH_API_TOKEN = "api_token"
AUTH_APP_PASSWORD = "app_password"
AUTH_WEBHOOK_URL = "webhook_url"
AUTH_BROWSER_SESSION = "browser_session"

# Connection.status values.
STATUS_UNCONFIGURED = "unconfigured"
STATUS_CONNECTED = "connected"
STATUS_EXPIRED = "expired"
STATUS_ERROR = "error"
STATUS_DISABLED = "disabled"


@dataclass(frozen=True)
class Capabilities:
    """What a platform accepts. Drives client-side validation and the UI."""

    text: bool = True
    max_text_chars: Optional[int] = None
    images: bool = False
    max_images: int = 0
    max_image_bytes: Optional[int] = None
    video: bool = False
    max_video_bytes: Optional[int] = None
    # Longest clip the platform accepts, in seconds; None = no stated limit.
    max_video_seconds: Optional[float] = None
    audio: bool = False
    requires_media: bool = False
    requires_public_url: bool = False
    supports_link: bool = True
    supports_title: bool = False
    supports_tags: bool = False
    visibilities: Tuple[str, ...] = ()
    default_visibility: str = "private"
    accepted_mime: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "max_text_chars": self.max_text_chars,
            "images": self.images,
            "max_images": self.max_images,
            "max_image_bytes": self.max_image_bytes,
            "video": self.video,
            "max_video_bytes": self.max_video_bytes,
            "max_video_seconds": self.max_video_seconds,
            "audio": self.audio,
            "requires_media": self.requires_media,
            "requires_public_url": self.requires_public_url,
            "supports_link": self.supports_link,
            "supports_title": self.supports_title,
            "supports_tags": self.supports_tags,
            "visibilities": list(self.visibilities),
            "default_visibility": self.default_visibility,
            "accepted_mime": list(self.accepted_mime),
        }


@dataclass(frozen=True)
class CredentialField:
    """A secret input. Rendered as a password field with a masked hint."""

    name: str
    label: str
    required: bool = True
    help: str = ""
    placeholder: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "required": self.required,
            "help": self.help,
            "placeholder": self.placeholder,
            "secret": True,
        }


@dataclass(frozen=True)
class ConfigField:
    """A non-secret input. Stored on ``Connection.config``."""

    name: str
    label: str
    required: bool = False
    default: str = ""
    help: str = ""
    choices: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "required": self.required,
            "default": self.default,
            "help": self.help,
            "choices": list(self.choices),
            "secret": False,
        }


@dataclass(frozen=True)
class ProviderSpec:
    """Everything the API and UI need to render and validate a provider."""

    provider: str
    family: str
    label: str
    auth_kinds: Tuple[str, ...]
    credential_fields: Tuple[CredentialField, ...] = ()
    config_fields: Tuple[ConfigField, ...] = ()
    capabilities: Capabilities = Capabilities()
    env_keys: Tuple[str, ...] = ()
    hint_field: Optional[str] = None
    docs_url: str = ""
    setup_help: str = ""
    review_required: bool = False
    beta: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "family": self.family,
            "label": self.label,
            "auth_kinds": list(self.auth_kinds),
            "credential_fields": [f.to_dict() for f in self.credential_fields],
            "config_fields": [f.to_dict() for f in self.config_fields],
            "capabilities": self.capabilities.to_dict(),
            "env_keys": list(self.env_keys),
            "docs_url": self.docs_url,
            "setup_help": self.setup_help,
            "review_required": self.review_required,
            "beta": self.beta,
        }


@dataclass
class MediaItem:
    """A resolved local asset ready to upload."""

    path: str
    mime: str
    bytes: int
    document_id: Optional[int] = None
    role: str = "primary"
    width: Optional[int] = None
    height: Optional[int] = None
    duration_s: Optional[float] = None
    alt_text: Optional[str] = None
    # From the Document's metadata: the model whose license asks to be named
    # (MiniMax H3) and whether the clip carries a generated soundtrack.
    attribution: Optional[str] = None
    has_audio: bool = False

    @property
    def kind(self) -> str:
        if self.mime.startswith("image/"):
            return "image"
        if self.mime.startswith("video/"):
            return "video"
        if self.mime.startswith("audio/"):
            return "audio"
        return "other"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "document_id": self.document_id,
            "path": self.path,
            "mime": self.mime,
            "bytes": self.bytes,
            "role": self.role,
            "alt_text": self.alt_text,
        }


@dataclass
class PublishRequest:
    """One post, already validated against the target's capabilities."""

    record_id: int
    connection_id: int
    body: str = ""
    title: Optional[str] = None
    link_url: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    visibility: str = "private"
    media: List[MediaItem] = field(default_factory=list)
    config: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PublishResult:
    ok: bool
    remote_id: Optional[str] = None
    remote_url: Optional[str] = None
    message: str = ""


@dataclass
class ConnCtx:
    """What a provider receives: resolved secrets plus write-back callbacks.

    ``save_secrets`` exists so OAuth providers can persist a refreshed token
    without importing the store themselves.
    """

    connection: Dict[str, Any]
    config: Dict[str, Any]
    secrets: Dict[str, str]
    save_secrets: Callable[[Dict[str, str]], None] = lambda _values: None
    set_status: Callable[[str, Optional[str]], None] = lambda _status, _error: None


class ProviderError(Exception):
    """A provider call failed in a way worth showing the operator."""


ProgressFn = Callable[[int, str], None]
