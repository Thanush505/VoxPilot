"""Anthropic Claude BYOK Provider Adapter using httpx REST API (Phase 8D)."""

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

DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
DEFAULT_ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-20250514"
DEFAULT_ANTHROPIC_MAX_TOKENS = 2048
DEFAULT_ANTHROPIC_TIMEOUT_SECONDS = 30.0

CURATED_ANTHROPIC_MODELS: List[ProviderModelInfo] = [
    ProviderModelInfo(
        id="claude-sonnet-4-20250514",
        name="Claude Sonnet 4",
        context_length=200000,
    ),
    ProviderModelInfo(
        id="claude-3-7-sonnet-latest",
        name="Claude 3.7 Sonnet",
        context_length=200000,
    ),
    ProviderModelInfo(
        id="claude-3-5-haiku-latest",
        name="Claude 3.5 Haiku",
        context_length=200000,
    ),
]


class AnthropicBYOKAdapter:
    """Direct REST adapter for Anthropic Messages API using user-supplied BYOK API keys."""

    vendor = "anthropic"
    display_name = "Anthropic Claude (BYOK)"
    capabilities = ProviderCapabilities(
        streaming=False,
        structured_output=False,
        tool_calling=False,
        vision=False,
        local_only=False,
    )

    def __init__(
        self,
        base_url: str = DEFAULT_ANTHROPIC_BASE_URL,
        anthropic_version: str = DEFAULT_ANTHROPIC_VERSION,
        default_timeout_seconds: float = DEFAULT_ANTHROPIC_TIMEOUT_SECONDS,
        client_factory: Optional[Callable[[float], httpx.Client]] = None,
    ) -> None:
        self.base_url = (base_url or DEFAULT_ANTHROPIC_BASE_URL).rstrip("/")
        self.anthropic_version = anthropic_version or DEFAULT_ANTHROPIC_VERSION
        self.default_timeout_seconds = max(1.0, float(default_timeout_seconds))
        self._client_factory = client_factory

    def _create_client(self, timeout_seconds: float) -> httpx.Client:
        timeout = max(1.0, float(timeout_seconds))
        if self._client_factory is not None:
            return self._client_factory(timeout)
        return httpx.Client(timeout=timeout)

    def _headers(self, api_key: str) -> Dict[str, str]:
        return {
            "x-api-key": api_key,
            "anthropic-version": self.anthropic_version,
            "content-type": "application/json",
        }

    @staticmethod
    def _extract_error_details(response: httpx.Response, api_key: str) -> tuple[str, str]:
        err_type = ""
        message = ""
        try:
            payload = response.json()
            err = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(err, dict):
                err_type = str(err.get("type") or "").strip()
                message = str(err.get("message") or "").strip()
        except Exception:
            message = (response.text or "").strip()
        return err_type, sanitize_secret(message, api_key)[:300]

    def _raise_for_http_error(self, response: httpx.Response, api_key: str) -> None:
        status = response.status_code
        err_type, detail = self._extract_error_details(response, api_key)

        if status in (401, 403) or err_type == "authentication_error":
            raise ProviderAuthenticationError(
                "Anthropic authentication failed. Check that your API key is valid and active."
            )
        if status == 429 or err_type == "rate_limit_error":
            if "credit" in detail.lower() or "quota" in detail.lower() or "billing" in detail.lower():
                raise ProviderUnavailableError(
                    "Anthropic API quota or credit balance is exhausted for this key."
                )
            raise ProviderUnavailableError(
                "Anthropic rate limit reached. Please wait a moment and try again."
            )
        if status in (400, 404, 422):
            msg = detail or f"Anthropic rejected the request (HTTP {status})."
            raise ProviderConfigurationError(msg)
        if status in (500, 502, 503, 529) or err_type == "overloaded_error":
            raise ProviderUnavailableError(
                f"Anthropic service is temporarily unavailable (HTTP {status})."
            )
        raise AIProviderError(
            f"Anthropic request failed (HTTP {status}): {detail or 'Unexpected response.'}"
        )

    def list_models(self) -> List[ProviderModelInfo]:
        """Return curated Anthropic Claude models supported for BYOK."""
        return list(CURATED_ANTHROPIC_MODELS)

    def discover_models(
        self,
        api_key: str,
        timeout_seconds: float = 15.0,
    ) -> List[ProviderModelInfo]:
        """Query GET /v1/models to discover Anthropic models available to the API key."""
        cleaned_key = (api_key or "").strip()
        if not cleaned_key:
            raise ProviderNotConfiguredError("No Anthropic API key is configured.")

        try:
            with self._create_client(timeout_seconds) as client:
                response = client.get(
                    f"{self.base_url}/models",
                    headers=self._headers(cleaned_key),
                )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"Anthropic model discovery timed out after {timeout_seconds:.0f}s."
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                f"Unable to reach Anthropic API: {sanitize_secret(str(exc), cleaned_key)}"
            ) from exc

        if response.status_code >= 400:
            self._raise_for_http_error(response, cleaned_key)

        try:
            payload = response.json()
        except Exception:
            return self.list_models()

        raw_models = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(raw_models, list) or not raw_models:
            return self.list_models()

        discovered: List[ProviderModelInfo] = []
        for item in raw_models:
            if not isinstance(item, dict):
                continue
            mid = str(item.get("id") or "").strip()
            if not mid:
                continue
            name = str(item.get("display_name") or mid).strip()
            discovered.append(
                ProviderModelInfo(
                    id=mid,
                    name=name,
                    context_length=200000,
                )
            )
        return discovered or self.list_models()

    def validate_credential(
        self,
        api_key: str,
        model: Optional[str] = None,
        timeout_seconds: float = 15.0,
    ) -> ProviderHealth:
        """Explicitly validate an Anthropic API key against GET /v1/models."""
        cleaned_key = (api_key or "").strip()
        if not cleaned_key:
            raise ProviderNotConfiguredError("No Anthropic API key is configured.")

        target_model = (model or DEFAULT_ANTHROPIC_MODEL).strip() or DEFAULT_ANTHROPIC_MODEL
        t0 = time.monotonic()
        try:
            with self._create_client(timeout_seconds) as client:
                response = client.get(
                    f"{self.base_url}/models",
                    headers=self._headers(cleaned_key),
                )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"Anthropic connection validation timed out after {timeout_seconds:.0f}s."
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                f"Unable to reach Anthropic API: {sanitize_secret(str(exc), cleaned_key)}"
            ) from exc

        latency_ms = round((time.monotonic() - t0) * 1000.0, 2)
        if response.status_code >= 400:
            self._raise_for_http_error(response, cleaned_key)

        return ProviderHealth(
            provider_id=PROVIDER_BYOK,
            available=True,
            configured=True,
            status="ready",
            reason="Anthropic API key validated successfully.",
            active_model=target_model,
            latency_ms=latency_ms,
        )

    def generate(
        self,
        request: AIProviderRequest,
        api_key: str,
        model: Optional[str] = None,
    ) -> AIProviderResponse:
        """Execute a single-turn or multi-turn Anthropic Messages API request."""
        stop_mgr = get_shared_emergency_stop_manager()
        if stop_mgr.is_emergency_stop_active():
            raise ProviderCancelledError("Emergency stop is active; Anthropic request cancelled.")

        cleaned_key = (api_key or "").strip()
        if not cleaned_key:
            raise ProviderNotConfiguredError("No Anthropic API key is configured for this profile.")

        if not request.messages:
            raise ProviderConfigurationError("AIProviderRequest.messages cannot be empty.")

        if request.structured_schema is not None:
            raise ProviderConfigurationError(
                "Anthropic BYOK adapter does not support native structured JSON output mode."
            )

        target_model = (request.model or model or DEFAULT_ANTHROPIC_MODEL).strip() or DEFAULT_ANTHROPIC_MODEL
        timeout_seconds = max(
            1.0, float(request.timeout_seconds or self.default_timeout_seconds)
        )

        # Anthropic requires system prompts as a top-level `system` string,
        # not as `role: "system"` inside `messages`.
        system_parts: List[str] = []
        chat_messages: List[Dict[str, str]] = []
        for msg in request.messages:
            role = str(msg.get("role", "user")).strip().lower()
            content = str(msg.get("content", ""))
            if role == "system":
                if content.strip():
                    system_parts.append(content.strip())
            else:
                mapped_role = "assistant" if role == "assistant" else "user"
                chat_messages.append({"role": mapped_role, "content": content})

        if not chat_messages:
            raise ProviderConfigurationError("Anthropic request must include at least one user message.")

        body: Dict[str, Any] = {
            "model": target_model,
            "max_tokens": int(request.max_tokens or DEFAULT_ANTHROPIC_MAX_TOKENS),
            "messages": chat_messages,
        }
        if system_parts:
            body["system"] = "\n\n".join(system_parts)
        if request.temperature is not None:
            body["temperature"] = float(request.temperature)

        try:
            with self._create_client(timeout_seconds) as client:
                response = client.post(
                    f"{self.base_url}/messages",
                    headers=self._headers(cleaned_key),
                    json=body,
                )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"Anthropic request timed out after {timeout_seconds:.0f}s."
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                f"Anthropic network error: {sanitize_secret(str(exc), cleaned_key)}"
            ) from exc

        if stop_mgr.is_emergency_stop_active():
            raise ProviderCancelledError("Emergency stop triggered during Anthropic request.")

        if response.status_code >= 400:
            self._raise_for_http_error(response, cleaned_key)

        try:
            data = response.json()
        except Exception as exc:
            raise AIProviderError("Anthropic returned an invalid JSON response.") from exc

        blocks = data.get("content") if isinstance(data, dict) else None
        if not isinstance(blocks, list) or not blocks:
            raise AIProviderError("Anthropic response contained no content blocks.")

        text_parts: List[str] = []
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                txt = block.get("text")
                if isinstance(txt, str):
                    text_parts.append(txt)

        content_text = "".join(text_parts)
        if not content_text:
            raise AIProviderError("Anthropic response text content was empty or malformed.")

        raw_usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        usage: Dict[str, int] = {}
        in_tok = raw_usage.get("input_tokens")
        out_tok = raw_usage.get("output_tokens")
        if isinstance(in_tok, int):
            usage["prompt_tokens"] = in_tok
        if isinstance(out_tok, int):
            usage["completion_tokens"] = out_tok
        if isinstance(in_tok, int) and isinstance(out_tok, int):
            usage["total_tokens"] = in_tok + out_tok

        resolved_model = str(data.get("model") or target_model)
        finish_reason = str(data.get("stop_reason") or "stop")

        return AIProviderResponse(
            content=content_text,
            provider_id=PROVIDER_BYOK,
            model=resolved_model,
            finish_reason=finish_reason,
            usage=usage,
        )
