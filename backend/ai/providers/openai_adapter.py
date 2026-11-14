"""OpenAI BYOK Provider Adapter using httpx REST API (Phase 8B)."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

import httpx

from backend.ai.providers.base import (
    AIProviderError,
    AIProviderRequest,
    AIProviderResponse,
    PROVIDER_BYOK,
    ProviderAuthenticationError,
    ProviderCancelledError,
    ProviderCapabilities,
    ProviderConfigurationError,
    ProviderHealth,
    ProviderModelInfo,
    ProviderNotConfiguredError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from backend.ai.providers.credentials import sanitize_secret
from backend.automation.emergency_stop import get_shared_emergency_stop_manager

logger = logging.getLogger(__name__)

DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_OPENAI_TIMEOUT_SECONDS = 30.0

CURATED_OPENAI_MODELS: List[ProviderModelInfo] = [
    ProviderModelInfo(
        id="gpt-4o-mini",
        name="GPT-4o mini",
        context_length=128000,
    ),
    ProviderModelInfo(
        id="gpt-4o",
        name="GPT-4o",
        context_length=128000,
    ),
    ProviderModelInfo(
        id="gpt-4.1-mini",
        name="GPT-4.1 mini",
        context_length=1047576,
    ),
    ProviderModelInfo(
        id="gpt-4.1",
        name="GPT-4.1",
        context_length=1047576,
    ),
    ProviderModelInfo(
        id="o4-mini",
        name="o4-mini",
        context_length=200000,
    ),
]


class OpenAIBYOKAdapter:
    """Direct REST adapter for OpenAI using user-supplied BYOK API keys."""

    vendor = "openai"
    display_name = "OpenAI (BYOK)"
    capabilities = ProviderCapabilities(
        streaming=False,
        structured_output=True,
        tool_calling=False,
        vision=False,
        local_only=False,
    )

    def __init__(
        self,
        base_url: str = DEFAULT_OPENAI_BASE_URL,
        default_timeout_seconds: float = DEFAULT_OPENAI_TIMEOUT_SECONDS,
        client_factory: Optional[Callable[[float], httpx.Client]] = None,
    ) -> None:
        self.base_url = (base_url or DEFAULT_OPENAI_BASE_URL).rstrip("/")
        self.default_timeout_seconds = max(1.0, float(default_timeout_seconds))
        self._client_factory = client_factory

    def _create_client(self, timeout_seconds: float) -> httpx.Client:
        timeout = max(1.0, float(timeout_seconds))
        if self._client_factory is not None:
            return self._client_factory(timeout)
        return httpx.Client(timeout=timeout)

    @staticmethod
    def _extract_error_details(response: httpx.Response, api_key: str) -> tuple[str, str]:
        code = ""
        message = ""
        try:
            payload = response.json()
            err = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(err, dict):
                code = str(err.get("code") or err.get("type") or "").strip()
                message = str(err.get("message") or "").strip()
        except Exception:
            message = (response.text or "").strip()
        return code, sanitize_secret(message, api_key)[:300]

    def _raise_for_http_error(self, response: httpx.Response, api_key: str) -> None:
        status = response.status_code
        code, detail = self._extract_error_details(response, api_key)

        if status in (401, 403):
            raise ProviderAuthenticationError(
                "OpenAI authentication failed. Check that your API key is valid and active."
            )
        if status == 429:
            if code == "insufficient_quota" or "quota" in detail.lower():
                raise ProviderUnavailableError(
                    "OpenAI quota exceeded or billing is not active for this API key."
                )
            raise ProviderUnavailableError(
                "OpenAI rate limit reached. Please wait a moment and try again."
            )
        if status in (400, 404):
            msg = detail or f"OpenAI rejected the request (HTTP {status})."
            raise ProviderConfigurationError(msg)
        if status >= 500:
            raise ProviderUnavailableError(
                f"OpenAI service is temporarily unavailable (HTTP {status})."
            )
        raise AIProviderError(
            f"OpenAI request failed (HTTP {status}): {detail or 'Unexpected response.'}"
        )

    def list_models(self) -> List[ProviderModelInfo]:
        """Return curated OpenAI models supported for BYOK."""
        return list(CURATED_OPENAI_MODELS)

    def validate_credential(
        self,
        api_key: str,
        model: Optional[str] = None,
        timeout_seconds: float = 15.0,
    ) -> ProviderHealth:
        """Explicitly validate an OpenAI API key against GET /v1/models."""
        cleaned_key = (api_key or "").strip()
        if not cleaned_key:
            raise ProviderNotConfiguredError("No OpenAI API key is configured.")

        target_model = (model or DEFAULT_OPENAI_MODEL).strip() or DEFAULT_OPENAI_MODEL
        headers = {
            "Authorization": f"Bearer {cleaned_key}",
            "Content-Type": "application/json",
        }
        t0 = time.monotonic()
        try:
            with self._create_client(timeout_seconds) as client:
                response = client.get(f"{self.base_url}/models", headers=headers)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"OpenAI connection validation timed out after {timeout_seconds:.0f}s."
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                f"Unable to reach OpenAI API: {sanitize_secret(str(exc), cleaned_key)}"
            ) from exc

        latency_ms = round((time.monotonic() - t0) * 1000.0, 2)
        if response.status_code >= 400:
            self._raise_for_http_error(response, cleaned_key)

        return ProviderHealth(
            provider_id=PROVIDER_BYOK,
            available=True,
            configured=True,
            status="ready",
            reason="OpenAI API key validated successfully.",
            active_model=target_model,
            latency_ms=latency_ms,
        )

    def generate(
        self,
        request: AIProviderRequest,
        api_key: str,
        model: Optional[str] = None,
    ) -> AIProviderResponse:
        """Execute a chat completion against OpenAI."""
        stop_mgr = get_shared_emergency_stop_manager()
        if stop_mgr.is_emergency_stop_active():
            raise ProviderCancelledError("Emergency stop is active; OpenAI request cancelled.")

        cleaned_key = (api_key or "").strip()
        if not cleaned_key:
            raise ProviderNotConfiguredError("No OpenAI API key is configured for this profile.")

        if not request.messages:
            raise ProviderConfigurationError("AIProviderRequest.messages cannot be empty.")

        target_model = (request.model or model or DEFAULT_OPENAI_MODEL).strip() or DEFAULT_OPENAI_MODEL
        timeout_seconds = max(
            1.0, float(request.timeout_seconds or self.default_timeout_seconds)
        )

        messages: List[Dict[str, str]] = [
            {"role": str(m.get("role", "user")), "content": str(m.get("content", ""))}
            for m in request.messages
        ]

        body: Dict[str, Any] = {
            "model": target_model,
            "messages": messages,
        }
        if request.temperature is not None:
            body["temperature"] = float(request.temperature)
        if request.max_tokens is not None:
            body["max_tokens"] = int(request.max_tokens)
        if request.structured_schema is not None:
            body["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {cleaned_key}",
            "Content-Type": "application/json",
        }

        try:
            with self._create_client(timeout_seconds) as client:
                response = client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=body,
                )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"OpenAI request timed out after {timeout_seconds:.0f}s."
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                f"OpenAI network error: {sanitize_secret(str(exc), cleaned_key)}"
            ) from exc

        if stop_mgr.is_emergency_stop_active():
            raise ProviderCancelledError("Emergency stop triggered during OpenAI request.")

        if response.status_code >= 400:
            self._raise_for_http_error(response, cleaned_key)

        try:
            data = response.json()
        except Exception as exc:
            raise AIProviderError("OpenAI returned an invalid JSON response.") from exc

        choices = data.get("choices") if isinstance(data, dict) else None
        if not isinstance(choices, list) or not choices:
            raise AIProviderError("OpenAI response contained no completion choices.")

        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise AIProviderError("OpenAI response message content was empty or malformed.")

        raw_usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        usage: Dict[str, int] = {}
        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if isinstance(raw_usage.get(k), int):
                usage[k] = raw_usage[k]

        resolved_model = str(data.get("model") or target_model)
        finish_reason = str(choices[0].get("finish_reason") or "stop")

        return AIProviderResponse(
            content=content,
            provider_id=PROVIDER_BYOK,
            model=resolved_model,
            finish_reason=finish_reason,
            usage=usage,
        )
