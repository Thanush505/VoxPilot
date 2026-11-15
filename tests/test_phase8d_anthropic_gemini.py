"""
VoxPilot - Phase 8D: Anthropic Claude & Google Gemini BYOK Integration Tests.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine

from backend.ai.providers import (
    AIProviderRegistry,
    AIProviderRequest,
    AnthropicBYOKAdapter,
    GeminiBYOKAdapter,
    OpenAIBYOKAdapter,
    PROVIDER_BYOK,
    PROVIDER_LOCAL,
    ProviderAuthenticationError,
    ProviderCancelledError,
    ProviderConfigurationError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    WindowsDPAPIProviderCredentialStore,
    run_provider_chat,
    sanitize_secret,
)
from backend.automation.emergency_stop import get_shared_emergency_stop_manager
from backend.main import app


@pytest.fixture(autouse=True)
def _reset_emergency_stop():
    mgr = get_shared_emergency_stop_manager()
    mgr.reset_emergency_stop()
    yield
    mgr.reset_emergency_stop()


def _make_registry(
    tmp_path: Path,
    *,
    openai_handler=None,
    anthropic_handler=None,
    gemini_handler=None,
) -> AIProviderRegistry:
    db_path = tmp_path / "phase8d_test.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    cred_store = WindowsDPAPIProviderCredentialStore(storage_dir=tmp_path / "creds")
    openai_adapter = (
        OpenAIBYOKAdapter(
            client_factory=lambda t: httpx.Client(transport=httpx.MockTransport(openai_handler), timeout=t)
        )
        if openai_handler
        else OpenAIBYOKAdapter()
    )
    anthropic_adapter = (
        AnthropicBYOKAdapter(
            client_factory=lambda t: httpx.Client(transport=httpx.MockTransport(anthropic_handler), timeout=t)
        )
        if anthropic_handler
        else AnthropicBYOKAdapter()
    )
    gemini_adapter = (
        GeminiBYOKAdapter(
            client_factory=lambda t: httpx.Client(transport=httpx.MockTransport(gemini_handler), timeout=t)
        )
        if gemini_handler
        else GeminiBYOKAdapter()
    )
    return AIProviderRegistry(
        engine=engine,
        credential_store=cred_store,
        openai_adapter=openai_adapter,
        anthropic_adapter=anthropic_adapter,
        gemini_adapter=gemini_adapter,
    )


def test_anthropic_adapter_auth_models_generation_and_errors():
    secret = "sk-ant-api03-testkey12345678"

    def _ok_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == secret
        assert request.headers["anthropic-version"] == "2023-06-01"
        if request.url.path.endswith("/models"):
            return httpx.Response(
                200,
                json={"data": [{"id": "claude-sonnet-4-20250514", "display_name": "Claude Sonnet 4"}]},
            )
        if request.url.path.endswith("/messages"):
            body = json.loads(request.content.decode("utf-8"))
            assert body["system"] == "Be concise."
            assert all(m["role"] != "system" for m in body["messages"])
            assert body["max_tokens"] >= 1
            return httpx.Response(
                200,
                json={
                    "id": "msg_123",
                    "model": body["model"],
                    "content": [{"type": "text", "text": "Hello from Claude!"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 12, "output_tokens": 5},
                },
            )
        return httpx.Response(404)

    adapter = AnthropicBYOKAdapter(
        client_factory=lambda t: httpx.Client(transport=httpx.MockTransport(_ok_handler), timeout=t)
    )
    health = adapter.validate_credential(secret, model="claude-sonnet-4-20250514")
    assert health.available is True
    assert health.status == "ready"

    discovered = adapter.discover_models(secret)
    assert discovered[0].id == "claude-sonnet-4-20250514"

    resp = adapter.generate(
        AIProviderRequest(
            messages=[
                {"role": "system", "content": "Be concise."},
                {"role": "user", "content": "Hi"},
            ]
        ),
        api_key=secret,
    )
    assert resp.content == "Hello from Claude!"
    assert resp.usage["total_tokens"] == 17

    # Structured JSON mode is honestly unsupported on Anthropic
    with pytest.raises(ProviderConfigurationError, match="structured JSON"):
        adapter.generate(
            AIProviderRequest(
                messages=[{"role": "user", "content": "Return JSON"}],
                structured_schema={"type": "json_object"},
            ),
            api_key=secret,
        )

    # 401 -> ProviderAuthenticationError without leaking key
    auth_err_adapter = AnthropicBYOKAdapter(
        client_factory=lambda t: httpx.Client(
            transport=httpx.MockTransport(
                lambda _r: httpx.Response(
                    401,
                    json={"error": {"type": "authentication_error", "message": f"Invalid x-api-key {secret}"}},
                )
            ),
            timeout=t,
        )
    )
    with pytest.raises(ProviderAuthenticationError) as exc_info:
        auth_err_adapter.validate_credential(secret)
    assert secret not in str(exc_info.value)

    # 429 rate limit -> ProviderUnavailableError
    rate_adapter = AnthropicBYOKAdapter(
        client_factory=lambda t: httpx.Client(
            transport=httpx.MockTransport(
                lambda _r: httpx.Response(
                    429,
                    json={"error": {"type": "rate_limit_error", "message": "Rate limit exceeded"}},
                )
            ),
            timeout=t,
        )
    )
    with pytest.raises(ProviderUnavailableError, match="rate limit"):
        rate_adapter.generate(AIProviderRequest(messages=[{"role": "user", "content": "Hi"}]), api_key=secret)

    # Timeout -> ProviderTimeoutError
    def _timeout(_r):
        raise httpx.ReadTimeout("timeout")

    timeout_adapter = AnthropicBYOKAdapter(
        client_factory=lambda t: httpx.Client(transport=httpx.MockTransport(_timeout), timeout=t)
    )
    with pytest.raises(ProviderTimeoutError):
        timeout_adapter.generate(AIProviderRequest(messages=[{"role": "user", "content": "Hi"}]), api_key=secret)

    # EmergencyStop -> ProviderCancelledError
    get_shared_emergency_stop_manager().trigger_emergency_stop(reason="Test halt")
    with pytest.raises(ProviderCancelledError):
        adapter.generate(AIProviderRequest(messages=[{"role": "user", "content": "Hi"}]), api_key=secret)


def test_gemini_adapter_auth_models_generation_structured_and_errors():
    secret = "AIzaSyTestGeminiKey1234567890"

    def _ok_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-goog-api-key"] == secret
        assert "key=" not in str(request.url)
        if request.url.path.endswith("/models"):
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "models/gemini-2.5-flash",
                            "displayName": "Gemini 2.5 Flash",
                            "supportedGenerationMethods": ["generateContent"],
                            "inputTokenLimit": 1048576,
                        },
                        {
                            "name": "models/embedding-001",
                            "displayName": "Embedding",
                            "supportedGenerationMethods": ["embedContent"],
                        },
                    ]
                },
            )
        if request.url.path.endswith(":generateContent"):
            body = json.loads(request.content.decode("utf-8"))
            assert body["systemInstruction"]["parts"][0]["text"] == "Be helpful."
            assert body["contents"][0]["role"] == "user"
            assert body["contents"][1]["role"] == "model"
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "content": {"parts": [{"text": "Hello from Gemini!"}]},
                            "finishReason": "STOP",
                        }
                    ],
                    "usageMetadata": {
                        "promptTokenCount": 9,
                        "candidatesTokenCount": 4,
                        "totalTokenCount": 13,
                    },
                },
            )
        return httpx.Response(404)

    adapter = GeminiBYOKAdapter(
        client_factory=lambda t: httpx.Client(transport=httpx.MockTransport(_ok_handler), timeout=t)
    )
    health = adapter.validate_credential(secret, model="gemini-2.5-flash")
    assert health.available is True
    assert health.status == "ready"

    discovered = adapter.discover_models(secret)
    assert [m.id for m in discovered] == ["gemini-2.5-flash"]

    resp = adapter.generate(
        AIProviderRequest(
            messages=[
                {"role": "system", "content": "Be helpful."},
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "Hello!"},
                {"role": "user", "content": "How are you?"},
            ],
            structured_schema={"type": "json_object"},
        ),
        api_key=secret,
    )
    assert resp.content == "Hello from Gemini!"
    assert resp.usage["total_tokens"] == 13

    # 403 / API_KEY_INVALID -> ProviderAuthenticationError
    auth_err_adapter = GeminiBYOKAdapter(
        client_factory=lambda t: httpx.Client(
            transport=httpx.MockTransport(
                lambda _r: httpx.Response(
                    400,
                    json={"error": {"status": "INVALID_ARGUMENT", "message": f"API key not valid: {secret}"}},
                )
            ),
            timeout=t,
        )
    )
    with pytest.raises(ProviderAuthenticationError) as exc_info:
        auth_err_adapter.validate_credential(secret)
    assert secret not in str(exc_info.value)

    # 429 quota -> ProviderUnavailableError
    quota_adapter = GeminiBYOKAdapter(
        client_factory=lambda t: httpx.Client(
            transport=httpx.MockTransport(
                lambda _r: httpx.Response(
                    429,
                    json={"error": {"status": "RESOURCE_EXHAUSTED", "message": "Quota exceeded"}},
                )
            ),
            timeout=t,
        )
    )
    with pytest.raises(ProviderUnavailableError, match="quota"):
        quota_adapter.generate(AIProviderRequest(messages=[{"role": "user", "content": "Hi"}]), api_key=secret)

    # Network error -> ProviderUnavailableError without secret leak
    def _net(_r):
        raise httpx.ConnectError(f"Failed with {secret}")

    net_adapter = GeminiBYOKAdapter(
        client_factory=lambda t: httpx.Client(transport=httpx.MockTransport(_net), timeout=t)
    )
    with pytest.raises(ProviderUnavailableError) as net_exc:
        net_adapter.generate(AIProviderRequest(messages=[{"role": "user", "content": "Hi"}]), api_key=secret)
    assert secret not in str(net_exc.value)
    assert "[REDACTED]" in sanitize_secret(f"Failed with {secret}")


def test_multi_vendor_registry_switching_isolation_and_no_auto_activate(tmp_path: Path):
    def _openai(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "gpt-4o-mini"}]})
        return httpx.Response(
            200,
            json={"model": "gpt-4o-mini", "choices": [{"message": {"content": "OpenAI OK"}, "finish_reason": "stop"}]},
        )

    def _anthropic(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "claude-sonnet-4-20250514"}]})
        return httpx.Response(
            200,
            json={"model": "claude-sonnet-4-20250514", "content": [{"type": "text", "text": "Claude OK"}], "stop_reason": "end_turn"},
        )

    def _gemini(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"models": [{"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]}]})
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": "Gemini OK"}]}, "finishReason": "STOP"}]},
        )

    reg = _make_registry(
        tmp_path,
        openai_handler=_openai,
        anthropic_handler=_anthropic,
        gemini_handler=_gemini,
    )

    # Saving all three keys never auto-activates BYOK
    reg.configure_byok_vendor("openai", profile_id="work", api_key="sk-openai-12345678", validate=True)
    reg.configure_byok_vendor("anthropic", profile_id="work", api_key="sk-ant-12345678", validate=True)
    reg.configure_byok_vendor("google", profile_id="work", api_key="AIzaSyGemini12345678", validate=True)
    assert reg.get_profile_config("work")["active_provider_id"] == PROVIDER_LOCAL
    assert reg.get_profile_config("personal")["active_provider_id"] == PROVIDER_LOCAL

    with patch("backend.ai.providers.runtime.get_shared_provider_registry", return_value=reg):
        # Switch to OpenAI
        reg.select_provider(PROVIDER_BYOK, profile_id="work", config={"byok_vendor": "openai"})
        assert run_provider_chat([{"role": "user", "content": "hi"}], profile_id="work", json_mode=True) == "OpenAI OK"

        # Switch to Anthropic
        reg.select_provider(PROVIDER_BYOK, profile_id="work", config={"byok_vendor": "anthropic"})
        assert run_provider_chat([{"role": "user", "content": "hi"}], profile_id="work") == "Claude OK"
        with pytest.raises(ProviderConfigurationError, match="structured JSON"):
            run_provider_chat([{"role": "user", "content": "hi"}], profile_id="work", json_mode=True)

        # Switch to Gemini
        reg.select_provider(PROVIDER_BYOK, profile_id="work", config={"byok_vendor": "google"})
        assert run_provider_chat([{"role": "user", "content": "hi"}], profile_id="work", json_mode=True) == "Gemini OK"

        # Personal profile remains untouched on Local AI
        assert reg.get_profile_config("personal")["active_provider_id"] == PROVIDER_LOCAL

    # Deleting active Gemini key reverts 'work' to Local AI while preserving OpenAI & Anthropic keys
    rem = reg.remove_byok_credential(profile_id="work", vendor="google")
    assert rem["removed"] is True
    assert rem["profile_config"]["active_provider_id"] == PROVIDER_LOCAL
    vendors = {v["vendor"]: v for v in reg.list_byok_vendors("work")}
    assert vendors["openai"]["configured"] is True
    assert vendors["anthropic"]["configured"] is True
    assert vendors["google"]["configured"] is False


def test_fastapi_multi_vendor_endpoints_and_non_disclosure(tmp_path: Path):
    ant_key = "sk-ant-fastapi-12345678"
    gem_key = "AIzaSyFastApiGemini123456"

    reg = _make_registry(
        tmp_path,
        anthropic_handler=lambda _r: httpx.Response(200, json={"data": [{"id": "claude-sonnet-4-20250514"}]}),
        gemini_handler=lambda _r: httpx.Response(200, json={"models": [{"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]}]}),
    )
    client = TestClient(app)
    fake_hw = SimpleNamespace(ollama=SimpleNamespace(reachable=True))

    with patch("backend.api.providers.get_shared_provider_registry", return_value=reg), \
         patch("backend.ai.providers.local_ollama.get_shared_hardware_monitor") as mock_hm, \
         patch("backend.ai.providers.local_ollama.get_shared_model_manager") as mock_mm:
        mock_hm.return_value.get_hardware_snapshot.return_value = fake_hw
        mock_mm.return_value.list_installed_models.return_value = [SimpleNamespace(name="qwen3:4b")]

        r_ant = client.post(
            "/api/providers/byok/anthropic/configure",
            json={"api_key": ant_key, "model": "claude-sonnet-4-20250514", "validate_connection": True},
        )
        assert r_ant.status_code == 200
        assert r_ant.json()["profile_config"]["active_provider_id"] == PROVIDER_LOCAL
        assert ant_key not in r_ant.text

        r_gem = client.post(
            "/api/providers/byok/google/configure",
            json={"api_key": gem_key, "model": "gemini-2.5-flash", "validate_connection": True},
        )
        assert r_gem.status_code == 200
        assert gem_key not in r_gem.text

        r_vendors = client.get("/api/providers/byok/vendors")
        assert r_vendors.status_code == 200
        by_v = {v["vendor"]: v for v in r_vendors.json()["vendors"]}
        assert by_v["anthropic"]["configured"] is True
        assert by_v["google"]["configured"] is True
        assert ant_key not in r_vendors.text
        assert gem_key not in r_vendors.text

        r_models = client.get("/api/providers/byok/google/models?refresh=true")
        assert r_models.status_code == 200
        assert r_models.json()["models"][0]["id"] == "gemini-2.5-flash"

        r_sel = client.post(
            "/api/providers/select",
            json={"provider_id": PROVIDER_BYOK, "config": {"byok_vendor": "anthropic"}},
        )
        assert r_sel.status_code == 200
        assert r_sel.json()["profile_config"]["byok_vendor"] == "anthropic"

        r_active = client.get("/api/providers/active")
        assert r_active.status_code == 200
        assert r_active.json()["capabilities"]["structured_output"] is False

        r_del = client.delete("/api/providers/byok/anthropic/credentials")
        assert r_del.status_code == 200
        assert r_del.json()["profile_config"]["active_provider_id"] == PROVIDER_LOCAL
