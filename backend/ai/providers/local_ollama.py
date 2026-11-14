"""
VoxPilot - Local Ollama AI Provider Adapter (Phase 8A)

Wraps the existing OllamaLLM, ModelManager, and HardwareMonitor without
rewriting or breaking existing local inference behavior.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from backend.agent.llm import LLMError, OllamaLLM
from backend.ai.hardware_monitor import get_shared_hardware_monitor
from backend.ai.model_manager import DEFAULT_PRIMARY_MODEL, get_shared_model_manager
from backend.ai.providers.base import (
    AIProvider,
    AIProviderRequest,
    AIProviderResponse,
    PROVIDER_LOCAL,
    ProviderCapabilities,
    ProviderCancelledError,
    ProviderConfigurationError,
    ProviderHealth,
    ProviderModelInfo,
    ProviderUnavailableError,
    assert_no_plaintext_secrets,
)
from backend.automation.emergency_stop import get_shared_emergency_stop_manager


class LocalOllamaProvider(AIProvider):
    """Adapter around VoxPilot's existing local Ollama runtime."""

    provider_id = PROVIDER_LOCAL
    display_name = "Local AI (Ollama)"
    capabilities = ProviderCapabilities(
        streaming=False,
        structured_output=False,
        tool_calling=False,
        vision=False,
        local_only=True,
    )

    def validate_config(self, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        assert_no_plaintext_secrets(config)
        cfg = dict(config or {})
        model = str(cfg.get("local_model") or cfg.get("model") or DEFAULT_PRIMARY_MODEL).strip()
        if not model:
            raise ProviderConfigurationError("Local Ollama model name cannot be empty.")
        return {"local_model": model}

    def health_check(self, config: Optional[Dict[str, Any]] = None) -> ProviderHealth:
        clean = self.validate_config(config)
        target_model = clean["local_model"]
        t0 = time.monotonic()
        try:
            hw = get_shared_hardware_monitor().get_hardware_snapshot(include_ollama=True)
            latency_ms = round((time.monotonic() - t0) * 1000.0, 2)
            ollama_status = getattr(hw, "ollama", None)
            reachable = bool(getattr(ollama_status, "reachable", False))
            if not reachable:
                return ProviderHealth(
                    provider_id=self.provider_id,
                    available=False,
                    configured=True,
                    status="unavailable",
                    reason="Local Ollama service is not reachable on localhost:11434.",
                    active_model=target_model,
                    latency_ms=latency_ms,
                )
            return ProviderHealth(
                provider_id=self.provider_id,
                available=True,
                configured=True,
                status="ready",
                reason=None,
                active_model=target_model,
                latency_ms=latency_ms,
            )
        except Exception as err:
            return ProviderHealth(
                provider_id=self.provider_id,
                available=False,
                configured=True,
                status="unavailable",
                reason=f"Ollama health check failed: {err}",
                active_model=target_model,
            )

    def list_models(self, config: Optional[Dict[str, Any]] = None) -> List[ProviderModelInfo]:
        try:
            models = get_shared_model_manager().list_installed_models()
            return [
                ProviderModelInfo(
                    id=m.name,
                    name=m.name,
                    supports_tools=False,
                    supports_vision=False,
                )
                for m in models
            ]
        except Exception:
            clean = self.validate_config(config)
            return [ProviderModelInfo(id=clean["local_model"], name=clean["local_model"])]

    def generate(
        self,
        request: AIProviderRequest,
        config: Optional[Dict[str, Any]] = None,
    ) -> AIProviderResponse:
        if get_shared_emergency_stop_manager().is_emergency_stop_active():
            raise ProviderCancelledError("Inference cancelled: Global Emergency Stop is active.")

        clean = self.validate_config(config)
        model_name = request.model or clean["local_model"]
        llm = OllamaLLM(model=model_name)
        try:
            content = llm._call_ollama_chat(request.messages, temperature=request.temperature)
            return AIProviderResponse(
                content=content,
                provider_id=self.provider_id,
                model=model_name,
                finish_reason="stop",
            )
        except LLMError as err:
            raise ProviderUnavailableError(str(err)) from err
