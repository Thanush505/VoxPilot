"""Google Gemini BYOK Provider Adapter using httpx REST API (Phase 8D)."""

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

DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_GEMINI_TIMEOUT_SECONDS = 30.0

CURATED_GEMINI_MODELS: List[ProviderModelInfo] = [
    ProviderModelInfo(
        id="gemini-2.5-flash",
        name="Gemini 2.5 Flash",
        context_length=1048576,
    ),
    ProviderModelInfo(
        id="gemini-2.5-pro",
        name="Gemini 2.5 Pro",
        context_length=1048576,
    ),
    ProviderModelInfo(
        id="gemini-2.0-flash",
        name="Gemini 2.0 Flash",
        context_length=1048576,
    ),
]


class GeminiBYOKAdapter:
    """Direct REST adapter for Google Gemini API using user-supplied BYOK API keys."""

    vendor = "google"
    display_name = "Google Gemini (BYOK)"
    capabilities = ProviderCapabilities(
        streaming=False,
        structured_output=True,
        tool_calling=False,
        vision=False,
        local_only=False,
    )

    def __init__(
        self,
        base_url: str = DEFAULT_GEMINI_BASE_URL,
        default_timeout_seconds: float = DEFAULT_GEMINI_TIMEOUT_SECONDS,
        client_factory: Optional[Callable[[float], httpx.Client]] = None,
    ) -> None:
        self.base_url = (base_url or DEFAULT_GEMINI_BASE_URL).rstrip("/")
        self.default_timeout_seconds = max(1.0, float(default_timeout_seconds))
        self._client_factory = client_factory

    def _create_client(self, timeout_seconds: float) -> httpx.Client:
        timeout = max(1.0, float(timeout_seconds))
        if self._client_factory is not None:
            return self._client_factory(timeout)
        return httpx.Client(timeout=timeout)

    @staticmethod
    def _headers(api_key: str) -> Dict[str, str]:
        return {
            "x-goog-api-key": api_key,
            "Content-Type": "application/json",
        }

    @staticmethod
    def _normalize_model_id(model: Optional[str]) -> str:
        raw = (model or DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL
        if raw.startswith("models/"):
            raw = raw[len("models/"):]
        return raw

    @staticmethod
    def _extract_error_details(response: httpx.Response, api_key: str) -> tuple[str, str]:
        status_str = ""
        message = ""
        try:
            payload = response.json()
            err = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(err, dict):
                status_str = str(err.get("status") or "").strip()
                message = str(err.get("message") or "").strip()
        except Exception:
            message = (response.text or "").strip()
        return status_str, sanitize_secret(message, api_key)[:300]

    def _raise_for_http_error(self, response: httpx.Response, api_key: str) -> None:
        status = response.status_code
        err_status, detail = self._extract_error_details(response, api_key)

        if (
            status in (401, 403)
            or err_status in ("UNAUTHENTICATED", "PERMISSION_DENIED")
            or "api key not valid" in detail.lower()
        ):
            raise ProviderAuthenticationError(
                "Google Gemini authentication failed. Check that your API key is valid and enabled."
            )
        if status == 429 or err_status == "RESOURCE_EXHAUSTED":
            if "quota" in detail.lower() or "billing" in detail.lower():
                raise ProviderUnavailableError(
                    "Google Gemini API quota exceeded or billing is not active for this key."
                )
            raise ProviderUnavailableError(
                "Google Gemini rate limit reached. Please wait a moment and try again."
            )
        if status in (400, 404) or err_status in ("INVALID_ARGUMENT", "NOT_FOUND"):
            msg = detail or f"Google Gemini rejected the request (HTTP {status})."
            raise ProviderConfigurationError(msg)
        if status >= 500 or err_status in ("INTERNAL", "UNAVAILABLE"):
            raise ProviderUnavailableError(
                f"Google Gemini service is temporarily unavailable (HTTP {status})."
            )
        raise AIProviderError(
            f"Google Gemini request failed (HTTP {status}): {detail or 'Unexpected response.'}"
        )

    def list_models(self) -> List[ProviderModelInfo]:
        """Return curated Google Gemini models supported for BYOK."""
        return list(CURATED_GEMINI_MODELS)

    def discover_models(
        self,
        api_key: str,
        timeout_seconds: float = 15.0,
    ) -> List[ProviderModelInfo]:
        """Query GET /models to discover Gemini models supporting generateContent."""
        cleaned_key = (api_key or "").strip()
        if not cleaned_key:
            raise ProviderNotConfiguredError("No Google Gemini API key is configured.")

        try:
            with self._create_client(timeout_seconds) as client:
                response = client.get(
                    f"{self.base_url}/models",
                    headers=self._headers(cleaned_key),
                )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"Google Gemini model discovery timed out after {timeout_seconds:.0f}s."
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                f"Unable to reach Google Gemini API: {sanitize_secret(str(exc), cleaned_key)}"
            ) from exc

        if response.status_code >= 400:
            self._raise_for_http_error(response, cleaned_key)

        try:
            payload = response.json()
        except Exception:
            return self.list_models()

        raw_models = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(raw_models, list) or not raw_models:
            return self.list_models()

        discovered: List[ProviderModelInfo] = []
        for item in raw_models:
            if not isinstance(item, dict):
                continue
            methods = item.get("supportedGenerationMethods") or []
            if isinstance(methods, list) and "generateContent" not in methods:
                continue
            mid = self._normalize_model_id(item.get("name"))
            if not mid:
                continue
            name = str(item.get("displayName") or mid).strip()
            ctx = item.get("inputTokenLimit")
            discovered.append(
                ProviderModelInfo(
                    id=mid,
                    name=name,
                    context_length=ctx if isinstance(ctx, int) else None,
                )
            )
        return discovered or self.list_models()

    def validate_credential(
        self,
        api_key: str,
        model: Optional[str] = None,
        timeout_seconds: float = 15.0,
    ) -> ProviderHealth:
        """Explicitly validate a Google Gemini API key against GET /models."""
        cleaned_key = (api_key or "").strip()
        if not cleaned_key:
            raise ProviderNotConfiguredError("No Google Gemini API key is configured.")

        target_model = self._normalize_model_id(model)
        t0 = time.monotonic()
        try:
            with self._create_client(timeout_seconds) as client:
                response = client.get(
                    f"{self.base_url}/models",
                    headers=self._headers(cleaned_key),
                )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"Google Gemini connection validation timed out after {timeout_seconds:.0f}s."
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                f"Unable to reach Google Gemini API: {sanitize_secret(str(exc), cleaned_key)}"
            ) from exc

        latency_ms = round((time.monotonic() - t0) * 1000.0, 2)
        if response.status_code >= 400:
            self._raise_for_http_error(response, cleaned_key)

        return ProviderHealth(
            provider_id=PROVIDER_BYOK,
            available=True,
            configured=True,
            status="ready",
            reason="Google Gemini API key validated successfully.",
            active_model=target_model,
            latency_ms=latency_ms,
        )

    def generate(
        self,
        request: AIProviderRequest,
        api_key: str,
        model: Optional[str] = None,
    ) -> AIProviderResponse:
        """Execute a generateContent request against Google Gemini."""
        stop_mgr = get_shared_emergency_stop_manager()
        if stop_mgr.is_emergency_stop_active():
            raise ProviderCancelledError("Emergency stop is active; Google Gemini request cancelled.")

        cleaned_key = (api_key or "").strip()
        if not cleaned_key:
            raise ProviderNotConfiguredError("No Google Gemini API key is configured for this profile.")

        if not request.messages:
            raise ProviderConfigurationError("AIProviderRequest.messages cannot be empty.")

        target_model = self._normalize_model_id(request.model or model)
        timeout_seconds = max(
            1.0, float(request.timeout_seconds or self.default_timeout_seconds)
        )

        # Gemini uses top-level `systemInstruction` and `contents` with roles "user" / "model".
        system_parts: List[str] = []
        contents: List[Dict[str, Any]] = []
        for msg in request.messages:
            role = str(msg.get("role", "user")).strip().lower()
            content = str(msg.get("content", ""))
            if role == "system":
                if content.strip():
                    system_parts.append(content.strip())
            else:
                mapped_role = "model" if role in ("assistant", "model") else "user"
                contents.append({
                    "role": mapped_role,
                    "parts": [{"text": content}],
                })

        if not contents:
            raise ProviderConfigurationError("Google Gemini request must include at least one user message.")

        generation_config: Dict[str, Any] = {}
        if request.temperature is not None:
            generation_config["temperature"] = float(request.temperature)
        if request.max_tokens is not None:
            generation_config["maxOutputTokens"] = int(request.max_tokens)
        if request.structured_schema is not None:
            generation_config["responseMimeType"] = "application/json"

        body: Dict[str, Any] = {"contents": contents}
        if system_parts:
            body["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
        if generation_config:
            body["generationConfig"] = generation_config

        try:
            with self._create_client(timeout_seconds) as client:
                response = client.post(
                    f"{self.base_url}/models/{target_model}:generateContent",
                    headers=self._headers(cleaned_key),
                    json=body,
                )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"Google Gemini request timed out after {timeout_seconds:.0f}s."
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                f"Google Gemini network error: {sanitize_secret(str(exc), cleaned_key)}"
            ) from exc

        if stop_mgr.is_emergency_stop_active():
            raise ProviderCancelledError("Emergency stop triggered during Google Gemini request.")

        if response.status_code >= 400:
            self._raise_for_http_error(response, cleaned_key)

        try:
            data = response.json()
        except Exception as exc:
            raise AIProviderError("Google Gemini returned an invalid JSON response.") from exc

        candidates = data.get("candidates") if isinstance(data, dict) else None
        if not isinstance(candidates, list) or not candidates:
            feedback = data.get("promptFeedback") if isinstance(data, dict) else None
            block_reason = feedback.get("blockReason") if isinstance(feedback, dict) else None
            if block_reason:
                raise AIProviderError(f"Google Gemini blocked the prompt ({block_reason}).")
            raise AIProviderError("Google Gemini response contained no candidates.")

        first = candidates[0] if isinstance(candidates[0], dict) else {}
        content_obj = first.get("content") if isinstance(first.get("content"), dict) else {}
        parts = content_obj.get("parts") if isinstance(content_obj.get("parts"), list) else []

        text_parts: List[str] = []
        for part in parts:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                text_parts.append(part["text"])

        content_text = "".join(text_parts)
        if not content_text:
            raise AIProviderError("Google Gemini response text content was empty or malformed.")

        raw_usage = data.get("usageMetadata") if isinstance(data.get("usageMetadata"), dict) else {}
        usage: Dict[str, int] = {}
        if isinstance(raw_usage.get("promptTokenCount"), int):
            usage["prompt_tokens"] = raw_usage["promptTokenCount"]
        if isinstance(raw_usage.get("candidatesTokenCount"), int):
            usage["completion_tokens"] = raw_usage["candidatesTokenCount"]
        if isinstance(raw_usage.get("totalTokenCount"), int):
            usage["total_tokens"] = raw_usage["totalTokenCount"]

        finish_reason = str(first.get("finishReason") or "STOP").lower()

        return AIProviderResponse(
            content=content_text,
            provider_id=PROVIDER_BYOK,
            model=target_model,
            finish_reason=finish_reason,
            usage=usage,
        )
