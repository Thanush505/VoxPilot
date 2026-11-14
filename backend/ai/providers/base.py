"""
VoxPilot - Three-Mode AI Provider Contract & Normalized Types (Phase 8A)

Defines the abstract provider interface, capability descriptors, normalized
request/response schemas, error hierarchy, and credential store interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

PROVIDER_LOCAL = "local"
PROVIDER_BYOK = "byok"
PROVIDER_VOXPILOT_CLOUD = "voxpilot_cloud"
VALID_PROVIDER_IDS = (PROVIDER_LOCAL, PROVIDER_BYOK, PROVIDER_VOXPILOT_CLOUD)

FORBIDDEN_SECRET_KEYS = frozenset({
    "api_key",
    "apikey",
    "secret",
    "secret_key",
    "access_token",
    "refresh_token",
    "bearer_token",
    "password",
    "private_key",
})


class AIProviderError(Exception):
    """Base normalized error raised by any AI provider adapter."""


class ProviderUnavailableError(AIProviderError):
    """Raised when a provider is unreachable, disabled, or not implemented."""


class ProviderNotConfiguredError(AIProviderError):
    """Raised when a provider lacks required non-secret config or credentials."""


class ProviderAuthenticationError(AIProviderError):
    """Raised when provider credentials are rejected or missing."""


class ProviderTimeoutError(AIProviderError):
    """Raised when provider inference exceeds timeout."""


class ProviderCancelledError(AIProviderError):
    """Raised when inference is cancelled (e.g. Emergency Stop)."""


class ProviderConfigurationError(AIProviderError):
    """Raised when provider configuration is invalid or attempts secret exposure."""


@dataclass(frozen=True)
class ProviderCapabilities:
    streaming: bool = False
    structured_output: bool = False
    tool_calling: bool = False
    vision: bool = False
    local_only: bool = False

    def to_dict(self) -> Dict[str, bool]:
        return {
            "streaming": self.streaming,
            "structured_output": self.structured_output,
            "tool_calling": self.tool_calling,
            "vision": self.vision,
            "local_only": self.local_only,
        }


@dataclass(frozen=True)
class ProviderModelInfo:
    id: str
    name: str
    context_length: Optional[int] = None
    supports_tools: bool = False
    supports_vision: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "context_length": self.context_length,
            "supports_tools": self.supports_tools,
            "supports_vision": self.supports_vision,
        }


@dataclass
class ProviderHealth:
    provider_id: str
    available: bool
    configured: bool
    status: str  # "ready", "unavailable", "not_configured", "error"
    reason: Optional[str] = None
    active_model: Optional[str] = None
    latency_ms: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "available": self.available,
            "configured": self.configured,
            "status": self.status,
            "reason": self.reason,
            "active_model": self.active_model,
            "latency_ms": self.latency_ms,
        }


@dataclass
class AIProviderRequest:
    messages: List[Dict[str, str]]
    model: Optional[str] = None
    temperature: float = 0.5
    max_tokens: Optional[int] = None
    timeout_seconds: float = 60.0
    stream: bool = False
    structured_schema: Optional[Dict[str, Any]] = None
    tools: Optional[List[Dict[str, Any]]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AIProviderResponse:
    content: str
    provider_id: str
    model: str
    finish_reason: str = "stop"
    usage: Dict[str, int] = field(default_factory=dict)
    raw_tool_calls: List[Dict[str, Any]] = field(default_factory=list)


class CredentialStoreInterface(ABC):
    """Abstract interface for future encrypted credential storage (Phase 8B).

    Implementations must use OS-backed encryption (e.g. Windows DPAPI / Credential Manager)
    and never store or return plaintext API keys in SQLite or API responses.
    """

    @abstractmethod
    def store_credential(self, profile_id: str, vendor: str, secret: str) -> str:
        """Encrypt and store secret; return an opaque credential_ref."""

    @abstractmethod
    def has_credential(self, profile_id: str, credential_ref: str) -> bool:
        """Check whether an encrypted credential exists without exposing it."""

    @abstractmethod
    def resolve_credential(self, profile_id: str, credential_ref: str) -> Optional[str]:
        """Decrypt credential strictly inside backend runtime memory for inference."""

    @abstractmethod
    def delete_credential(self, profile_id: str, credential_ref: str) -> bool:
        """Remove an encrypted credential."""


def assert_no_plaintext_secrets(config: Optional[Dict[str, Any]]) -> None:
    """Reject any configuration dict containing raw API key fields or secret-like values."""
    if not config:
        return
    for k, v in config.items():
        lk = str(k).lower().strip()
        if lk in FORBIDDEN_SECRET_KEYS:
            raise ProviderConfigurationError(
                f"Plaintext credential field '{k}' is not allowed in provider configuration."
            )
        if isinstance(v, dict):
            assert_no_plaintext_secrets(v)
        elif isinstance(v, str) and (
            v.startswith("sk-") or v.startswith("AIza") or v.startswith("ghp_")
        ):
            raise ProviderConfigurationError(
                "Raw secret-like values are not allowed in provider configuration."
            )


class AIProvider(ABC):
    """Abstract contract for Local, BYOK, and VoxPilot Cloud AI providers."""

    provider_id: str
    display_name: str
    capabilities: ProviderCapabilities

    @abstractmethod
    def health_check(self, config: Optional[Dict[str, Any]] = None) -> ProviderHealth:
        """Inspect provider readiness without mutating external state."""

    @abstractmethod
    def validate_config(self, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Validate non-secret provider configuration and return normalized copy."""

    @abstractmethod
    def list_models(self, config: Optional[Dict[str, Any]] = None) -> List[ProviderModelInfo]:
        """Return models available for this provider."""

    @abstractmethod
    def generate(
        self,
        request: AIProviderRequest,
        config: Optional[Dict[str, Any]] = None,
    ) -> AIProviderResponse:
        """Execute synchronous text/structured generation."""

    def stream_generate(
        self,
        request: AIProviderRequest,
        config: Optional[Dict[str, Any]] = None,
    ) -> Iterator[str]:
        """Stream generated tokens if supported by the provider."""
        raise ProviderUnavailableError(
            f"Streaming is not supported by provider '{self.provider_id}'."
        )
