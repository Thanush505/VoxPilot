"""VoxPilot Three-Mode AI Provider Package (Phase 8A)."""

from backend.ai.providers.base import (
    AIProvider,
    AIProviderError,
    AIProviderRequest,
    AIProviderResponse,
    CredentialStoreInterface,
    PROVIDER_BYOK,
    PROVIDER_LOCAL,
    PROVIDER_VOXPILOT_CLOUD,
    ProviderAuthenticationError,
    ProviderCancelledError,
    ProviderCapabilities,
    ProviderConfigurationError,
    ProviderHealth,
    ProviderModelInfo,
    ProviderNotConfiguredError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    VALID_PROVIDER_IDS,
    assert_no_plaintext_secrets,
)
from backend.ai.providers.credentials import WindowsDPAPIProviderCredentialStore, sanitize_secret
from backend.ai.providers.local_ollama import LocalOllamaProvider
from backend.ai.providers.openai_adapter import DEFAULT_OPENAI_MODEL, OpenAIBYOKAdapter
from backend.ai.providers.placeholders import BYOKProvider, VoxPilotCloudProvider
from backend.ai.providers.registry import AIProviderRegistry, get_shared_provider_registry
from backend.ai.providers.runtime import resolve_runtime_provider, run_provider_chat

__all__ = [
    "AIProvider",
    "AIProviderError",
    "AIProviderRegistry",
    "AIProviderRequest",
    "AIProviderResponse",
    "BYOKProvider",
    "CredentialStoreInterface",
    "DEFAULT_OPENAI_MODEL",
    "LocalOllamaProvider",
    "OpenAIBYOKAdapter",
    "PROVIDER_BYOK",
    "PROVIDER_LOCAL",
    "PROVIDER_VOXPILOT_CLOUD",
    "ProviderAuthenticationError",
    "ProviderCancelledError",
    "ProviderCapabilities",
    "ProviderConfigurationError",
    "ProviderHealth",
    "ProviderModelInfo",
    "ProviderNotConfiguredError",
    "ProviderTimeoutError",
    "ProviderUnavailableError",
    "VALID_PROVIDER_IDS",
    "VoxPilotCloudProvider",
    "WindowsDPAPIProviderCredentialStore",
    "assert_no_plaintext_secrets",
    "get_shared_provider_registry",
    "resolve_runtime_provider",
    "run_provider_chat",
    "sanitize_secret",
]
