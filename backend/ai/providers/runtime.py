"""Unified runtime AI provider resolution and chat execution (Phase 8C)."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.ai.model_manager import DEFAULT_PRIMARY_MODEL
from backend.ai.providers.base import (
    AIProvider,
    AIProviderRequest,
    PROVIDER_LOCAL,
    ProviderCancelledError,
    ProviderConfigurationError,
)
from backend.ai.providers.registry import get_shared_provider_registry
from backend.automation.emergency_stop import get_shared_emergency_stop_manager


def resolve_runtime_provider(
    profile_id: Optional[str] = None,
) -> Tuple[str, AIProvider, Dict[str, Any]]:
    """Resolve the active provider ID, adapter, and internal config for a profile."""
    reg = get_shared_provider_registry()
    pid = reg._resolve_profile_id(profile_id)
    cfg = reg._get_internal_profile_config(pid)
    active_id = str(cfg.get("active_provider_id") or PROVIDER_LOCAL)
    provider = reg.get_provider(active_id)
    return active_id, provider, cfg


def run_provider_chat(
    messages: List[Dict[str, str]],
    *,
    temperature: float = 0.5,
    max_tokens: Optional[int] = None,
    timeout_seconds: float = 60.0,
    json_mode: bool = False,
    require_tool_calling: bool = False,
    require_vision: bool = False,
    profile_id: Optional[str] = None,
    local_executor: Optional[Callable[[str, List[Dict[str, str]], float], str]] = None,
) -> str:
    """Execute chat completion through the profile's explicitly selected AI provider.

    - Never silently falls back to another provider on failure.
    - Enforces capability checks (vision, tool_calling, structured_output).
    - When `local` is active and `local_executor` is supplied, invokes `local_executor`
      with the configured local model so ModelManager tracking and test patches work.
    """
    if get_shared_emergency_stop_manager().is_emergency_stop_active():
        raise ProviderCancelledError("Inference cancelled: Global Emergency Stop is active.")

    active_id, provider, cfg = resolve_runtime_provider(profile_id)
    caps = provider.get_capabilities(cfg) if hasattr(provider, "get_capabilities") else provider.capabilities

    if require_vision and not caps.vision:
        raise ProviderConfigurationError(
            f"Active AI provider '{active_id}' does not support vision or image analysis."
        )
    if require_tool_calling and not caps.tool_calling:
        raise ProviderConfigurationError(
            f"Active AI provider '{active_id}' does not support native tool calling."
        )
    if json_mode and active_id != PROVIDER_LOCAL and not caps.structured_output:
        raise ProviderConfigurationError(
            f"Active AI provider '{active_id}' does not support structured JSON output."
        )

    if active_id == PROVIDER_LOCAL and local_executor is not None:
        local_model = str(cfg.get("local_model") or DEFAULT_PRIMARY_MODEL).strip() or DEFAULT_PRIMARY_MODEL
        return local_executor(local_model, messages, temperature)

    req = AIProviderRequest(
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        structured_schema={"type": "json_object"} if json_mode else None,
    )
    resp = provider.generate(req, config=cfg)
    return resp.content
