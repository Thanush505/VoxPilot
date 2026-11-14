"""
VoxPilot - Phase 8B: Encrypted DPAPI Credential Storage & OpenAI BYOK Tests.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine

from backend.ai.email_assistant import CredentialStorageError
from backend.ai.providers import (
    AIProviderRegistry,
    AIProviderRequest,
    OpenAIBYOKAdapter,
    PROVIDER_BYOK,
    PROVIDER_LOCAL,
    ProviderAuthenticationError,
    ProviderCancelledError,
    ProviderConfigurationError,
    ProviderNotConfiguredError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    WindowsDPAPIProviderCredentialStore,
    sanitize_secret,
)
from backend.automation.emergency_stop import get_shared_emergency_stop_manager
from backend.database.models import ProfileProviderConfigRecord
from backend.main import app


@pytest.fixture(autouse=True)
def _reset_emergency_stop():
    mgr = get_shared_emergency_stop_manager()
    mgr.reset_emergency_stop()
    yield
    mgr.reset_emergency_stop()


def _make_registry(tmp_path: Path, handler=None) -> AIProviderRegistry:
    db_path = tmp_path / "phase8b_test.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    cred_store = WindowsDPAPIProviderCredentialStore(storage_dir=tmp_path / "creds")
    if handler is not None:
        transport = httpx.MockTransport(handler)
        adapter = OpenAIBYOKAdapter(
            client_factory=lambda timeout: httpx.Client(transport=transport, timeout=timeout)
        )
    else:
        adapter = OpenAIBYOKAdapter()

    return AIProviderRegistry(
        engine=engine,
        credential_store=cred_store,
        openai_adapter=adapter,
    )


def test_dpapi_credential_roundtrip_update_delete_and_profile_isolation(tmp_path: Path):
    store = WindowsDPAPIProviderCredentialStore(storage_dir=tmp_path / "creds")
    secret_a = "sk-test-alpha-1234567890"
    secret_a2 = "sk-test-alpha-updated-999"

    ref_a = store.store_credential("work", "openai", secret_a)
    assert ref_a == "byok:work:openai"
    assert store.has_credential("work", ref_a) is True

    # Ciphertext file must not contain plaintext key
    blob_file = tmp_path / "creds" / "byok_work_openai.dpapi.json"
    assert blob_file.is_file()
    raw_text = blob_file.read_text(encoding="utf-8")
    assert secret_a not in raw_text
    assert "dpapi_ciphertext" in raw_text

    # Resolve succeeds for owning profile
    assert store.resolve_credential("work", ref_a) == secret_a

    # Cross-profile access is blocked
    assert store.has_credential("personal", ref_a) is False
    assert store.delete_credential("personal", ref_a) is False
    with pytest.raises(ProviderConfigurationError, match="Cross-profile"):
        store.resolve_credential("personal", ref_a)

    # Update overwrites encrypted secret
    ref_a2 = store.store_credential("work", "openai", secret_a2)
    assert ref_a2 == ref_a
    assert store.resolve_credential("work", ref_a2) == secret_a2

    # Delete removes blob cleanly
    assert store.delete_credential("work", ref_a) is True
    assert store.has_credential("work", ref_a) is False
    assert store.resolve_credential("work", ref_a) is None


def test_dpapi_failure_never_falls_back_to_plaintext(tmp_path: Path):
    class _BrokenDPAPI:
        @staticmethod
        def protect_secret(_secret: str) -> str:
            raise CredentialStorageError("DPAPI unavailable")

        @staticmethod
        def unprotect_secret(_protected_b64: str) -> str:
            raise CredentialStorageError("DPAPI decrypt failed")

    store = WindowsDPAPIProviderCredentialStore(
        storage_dir=tmp_path / "creds",
        dpapi_backend=_BrokenDPAPI(),
    )

    with pytest.raises(ProviderConfigurationError, match="Windows DPAPI"):
        store.store_credential("default", "openai", "sk-secret-value-123456")

    assert list((tmp_path / "creds").glob("*.json")) == []

    # Corrupt / undecryptable blob raises ProviderConfigurationError
    bad_file = tmp_path / "creds" / "byok_default_openai.dpapi.json"
    bad_file.write_text(
        json.dumps({
            "version": 1,
            "profile_id": "default",
            "vendor": "openai",
            "credential_ref": "byok:default:openai",
            "dpapi_ciphertext": "corrupt-data",
        }),
        encoding="utf-8",
    )
    with pytest.raises(ProviderConfigurationError, match="decrypt"):
        store.resolve_credential("default", "byok:default:openai")


def test_secret_redaction_helper():
    raw = "Error with Bearer sk-proj-abc1234567890xyz and sk-live-9876543210"
    cleaned = sanitize_secret(raw, "sk-proj-abc1234567890xyz")
    assert "sk-proj-abc1234567890xyz" not in cleaned
    assert "sk-live-9876543210" not in cleaned
    assert "[REDACTED]" in cleaned


def test_openai_adapter_validation_and_generation_error_normalization():
    secret = "sk-test-openai-99999999"

    def _ok_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {secret}"
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "gpt-4o-mini"}]})
        if request.url.path.endswith("/chat/completions"):
            body = json.loads(request.content.decode("utf-8"))
            assert body["model"] == "gpt-4o-mini"
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-123",
                    "model": "gpt-4o-mini",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "Hello from OpenAI!"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
                },
            )
        return httpx.Response(404)

    adapter = OpenAIBYOKAdapter(
        client_factory=lambda t: httpx.Client(transport=httpx.MockTransport(_ok_handler), timeout=t)
    )
    health = adapter.validate_credential(secret, model="gpt-4o-mini")
    assert health.available is True
    assert health.status == "ready"
    assert health.active_model == "gpt-4o-mini"

    resp = adapter.generate(
        AIProviderRequest(messages=[{"role": "user", "content": "Hi"}]),
        api_key=secret,
        model="gpt-4o-mini",
    )
    assert resp.content == "Hello from OpenAI!"
    assert resp.provider_id == PROVIDER_BYOK
    assert resp.usage["total_tokens"] == 14

    # 401 -> ProviderAuthenticationError (without leaking secret)
    auth_adapter = OpenAIBYOKAdapter(
        client_factory=lambda t: httpx.Client(
            transport=httpx.MockTransport(
                lambda _req: httpx.Response(
                    401,
                    json={"error": {"message": f"Invalid key {secret}", "code": "invalid_api_key"}},
                )
            ),
            timeout=t,
        )
    )
    with pytest.raises(ProviderAuthenticationError) as exc_info:
        auth_adapter.validate_credential(secret)
    assert secret not in str(exc_info.value)

    # 429 quota -> ProviderUnavailableError
    quota_adapter = OpenAIBYOKAdapter(
        client_factory=lambda t: httpx.Client(
            transport=httpx.MockTransport(
                lambda _req: httpx.Response(
                    429,
                    json={"error": {"message": "You exceeded your current quota", "code": "insufficient_quota"}},
                )
            ),
            timeout=t,
        )
    )
    with pytest.raises(ProviderUnavailableError, match="quota"):
        quota_adapter.generate(
            AIProviderRequest(messages=[{"role": "user", "content": "Hi"}]),
            api_key=secret,
        )

    # Timeout -> ProviderTimeoutError
    def _timeout_handler(_req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    timeout_adapter = OpenAIBYOKAdapter(
        client_factory=lambda t: httpx.Client(transport=httpx.MockTransport(_timeout_handler), timeout=t)
    )
    with pytest.raises(ProviderTimeoutError):
        timeout_adapter.generate(
            AIProviderRequest(messages=[{"role": "user", "content": "Hi"}], timeout_seconds=2.0),
            api_key=secret,
        )

    # Network error -> ProviderUnavailableError without secret leak
    def _net_handler(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"Connection failed for {secret}")

    net_adapter = OpenAIBYOKAdapter(
        client_factory=lambda t: httpx.Client(transport=httpx.MockTransport(_net_handler), timeout=t)
    )
    with pytest.raises(ProviderUnavailableError) as net_exc:
        net_adapter.generate(
            AIProviderRequest(messages=[{"role": "user", "content": "Hi"}]),
            api_key=secret,
        )
    assert secret not in str(net_exc.value)

    # EmergencyStop cancels generation before network call
    get_shared_emergency_stop_manager().trigger_emergency_stop(reason="Unit test stop")
    with pytest.raises(ProviderCancelledError):
        adapter.generate(
            AIProviderRequest(messages=[{"role": "user", "content": "Hi"}]),
            api_key=secret,
        )


def test_registry_configure_does_not_auto_activate_and_routes_only_when_selected(tmp_path: Path):
    secret = "sk-test-registry-88888888"

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "gpt-4o-mini"}]})
        return httpx.Response(
            200,
            json={
                "model": "gpt-4o-mini",
                "choices": [{"message": {"role": "assistant", "content": "BYOK reply"}, "finish_reason": "stop"}],
                "usage": {"total_tokens": 8},
            },
        )

    reg = _make_registry(tmp_path, handler=_handler)

    # Saving & validating OpenAI key must NOT auto-switch active_provider_id from local
    cfg_res = reg.configure_byok_openai(
        profile_id="work",
        api_key=secret,
        model="gpt-4o-mini",
        validate=True,
    )
    assert cfg_res["profile_config"]["active_provider_id"] == PROVIDER_LOCAL
    assert cfg_res["profile_config"]["local_model"] == "qwen3:4b"
    assert cfg_res["profile_config"]["byok_vendor"] == "openai"
    assert cfg_res["profile_config"]["has_byok_credential"] is True
    assert secret not in json.dumps(cfg_res)

    # SQLite record must not contain raw secret
    with Session(reg._get_engine()) as session:
        rec = session.get(ProfileProviderConfigRecord, "work")
        assert rec is not None
        assert rec.byok_credential_ref == "byok:work:openai"
        assert secret not in (rec.config_json or "")

    # Explicitly select BYOK for 'work' while 'personal' stays Local
    sel_res = reg.select_provider(PROVIDER_BYOK, profile_id="work")
    assert sel_res["profile_config"]["active_provider_id"] == PROVIDER_BYOK
    assert reg.get_profile_config("personal")["active_provider_id"] == PROVIDER_LOCAL

    # Generate on 'work' routes to OpenAI BYOK
    out = reg.generate(
        AIProviderRequest(messages=[{"role": "user", "content": "Hello"}]),
        profile_id="work",
    )
    assert out.content == "BYOK reply"
    assert out.provider_id == PROVIDER_BYOK

    # Removing credential deletes DPAPI blob and reverts active provider to Local
    rem = reg.remove_byok_credential(profile_id="work", vendor="openai")
    assert rem["removed"] is True
    assert rem["profile_config"]["active_provider_id"] == PROVIDER_LOCAL
    assert rem["profile_config"]["has_byok_credential"] is False


def test_fastapi_byok_endpoints_and_secret_non_disclosure(tmp_path: Path):
    secret = "sk-test-fastapi-77777777"

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "gpt-4o-mini"}]})
        return httpx.Response(404)

    reg = _make_registry(tmp_path, handler=_handler)
    client = TestClient(app)
    fake_hw = SimpleNamespace(ollama=SimpleNamespace(reachable=True))

    # Untrusted origin blocked
    assert client.post(
        "/api/providers/byok/openai/configure",
        headers={"Origin": "https://evil.example.com"},
        json={"api_key": secret},
    ).status_code == 403

    with patch("backend.api.providers.get_shared_provider_registry", return_value=reg), \
         patch("backend.ai.providers.local_ollama.get_shared_hardware_monitor") as mock_hm, \
         patch("backend.ai.providers.local_ollama.get_shared_model_manager") as mock_mm:
        mock_hm.return_value.get_hardware_snapshot.return_value = fake_hw
        mock_mm.return_value.list_installed_models.return_value = [SimpleNamespace(name="qwen3:4b")]

        # 1. Configure & validate OpenAI key (must keep local active)
        r_cfg = client.post(
            "/api/providers/byok/openai/configure",
            json={"api_key": secret, "model": "gpt-4o-mini", "validate_connection": True},
        )
        assert r_cfg.status_code == 200
        assert r_cfg.json()["profile_config"]["active_provider_id"] == PROVIDER_LOCAL
        assert r_cfg.json()["profile_config"]["has_byok_credential"] is True
        assert secret not in r_cfg.text

        # 2. Validate stored connection
        r_val = client.post("/api/providers/byok/openai/validate", json={})
        assert r_val.status_code == 200
        assert r_val.json()["valid"] is True
        assert secret not in r_val.text

        # 3. Change model
        r_mod = client.post("/api/providers/byok/openai/model", json={"model": "gpt-4o"})
        assert r_mod.status_code == 200
        assert r_mod.json()["profile_config"]["byok_model"] == "gpt-4o"

        # 4. Select BYOK explicitly
        r_sel = client.post("/api/providers/select", json={"provider_id": PROVIDER_BYOK})
        assert r_sel.status_code == 200
        assert r_sel.json()["profile_config"]["active_provider_id"] == PROVIDER_BYOK

        # 5. Active endpoint never exposes secret
        r_active = client.get("/api/providers/active")
        assert r_active.status_code == 200
        assert r_active.json()["profile_config"]["has_byok_credential"] is True
        assert secret not in r_active.text

        # 6. Delete credentials reverts to Local
        r_del = client.delete("/api/providers/byok/openai/credentials")
        assert r_del.status_code == 200
        assert r_del.json()["removed"] is True
        assert r_del.json()["profile_config"]["active_provider_id"] == PROVIDER_LOCAL
        assert r_del.json()["profile_config"]["has_byok_credential"] is False
