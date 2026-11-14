"""
VoxPilot - Phase 8A: Three-Mode AI Provider Architecture Foundation Tests.

Covers:
- Provider interface contract & capabilities
- Provider registry, registration, unknown provider handling
- Unavailable provider rejection (`byok`, `voxpilot_cloud`)
- Default Local Ollama provider selection & profile-specific configuration
- Provider health reporting (ready vs unreachable)
- Local Ollama adapter compatibility (`qwen3:4b`, EmergencyStop cancellation)
- Plaintext secret rejection & credential non-disclosure
- FastAPI provider endpoints, profile isolation, and trusted-origin checks
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine

from backend.ai.providers import (
    AIProvider,
    AIProviderRegistry,
    AIProviderRequest,
    BYOKProvider,
    LocalOllamaProvider,
    PROVIDER_BYOK,
    PROVIDER_LOCAL,
    PROVIDER_VOXPILOT_CLOUD,
    ProviderCancelledError,
    ProviderCapabilities,
    ProviderConfigurationError,
    ProviderHealth,
    ProviderNotConfiguredError,
    ProviderUnavailableError,
    VoxPilotCloudProvider,
)
from backend.automation.emergency_stop import get_shared_emergency_stop_manager
from backend.main import app


@pytest.fixture(autouse=True)
def _reset_emergency_stop():
    mgr = get_shared_emergency_stop_manager()
    mgr.reset_emergency_stop()
    yield
    mgr.reset_emergency_stop()


def _make_registry(tmp_path: Path) -> AIProviderRegistry:
    db_path = tmp_path / "phase8a_test.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return AIProviderRegistry(engine=engine)


def test_default_local_provider_and_registry(tmp_path: Path):
    reg = _make_registry(tmp_path)
    cfg = reg.get_profile_config("default")
    assert cfg["active_provider_id"] == PROVIDER_LOCAL
    assert cfg["local_model"] == "qwen3:4b"
    assert reg.get_active_provider("default").provider_id == PROVIDER_LOCAL

    with pytest.raises(ProviderConfigurationError):
        reg.get_provider("unknown_provider")


def test_unavailable_providers_cannot_be_selected_or_called(tmp_path: Path):
    reg = _make_registry(tmp_path)

    for pid in (PROVIDER_BYOK, PROVIDER_VOXPILOT_CLOUD):
        provider = reg.get_provider(pid)
        health = provider.health_check()
        assert health.available is False
        with pytest.raises(ProviderNotConfiguredError):
            provider.generate(AIProviderRequest(messages=[{"role": "user", "content": "hi"}]))
        with pytest.raises(ProviderUnavailableError):
            reg.select_provider(pid, profile_id="default")

    # Active provider remains unchanged
    assert reg.get_profile_config("default")["active_provider_id"] == PROVIDER_LOCAL


def test_plaintext_secrets_rejected_everywhere(tmp_path: Path):
    reg = _make_registry(tmp_path)
    for bad_cfg in (
        {"api_key": "12345"},
        {"secret": "abc"},
        {"byok_model": "sk-proj-1234567890abcdef"},
    ):
        with pytest.raises(ProviderConfigurationError):
            reg.validate_provider_config(PROVIDER_BYOK, bad_cfg)


def test_custom_provider_registration_and_profile_isolation(tmp_path: Path):
    reg = _make_registry(tmp_path)

    class _MockReadyProvider(AIProvider):
        provider_id = "mock_custom"
        display_name = "Mock Custom"
        capabilities = ProviderCapabilities(streaming=False)

        def health_check(self, config=None):
            return ProviderHealth(provider_id=self.provider_id, available=True, configured=True, status="ready")

        def validate_config(self, config=None):
            return dict(config or {})

        def list_models(self, config=None):
            return []

        def generate(self, request, config=None):
            raise NotImplementedError

    reg.register(_MockReadyProvider())
    reg.select_provider("mock_custom", profile_id="work")

    assert reg.get_profile_config("work")["active_provider_id"] == "mock_custom"
    assert reg.get_profile_config("personal")["active_provider_id"] == PROVIDER_LOCAL


def test_local_ollama_provider_health_and_compatibility():
    provider = LocalOllamaProvider()

    # 1. Reachable hardware snapshot -> ready
    fake_hw = SimpleNamespace(ollama=SimpleNamespace(reachable=True))
    with patch("backend.ai.providers.local_ollama.get_shared_hardware_monitor") as mock_hm:
        mock_hm.return_value.get_hardware_snapshot.return_value = fake_hw
        h_ok = provider.health_check({"local_model": "qwen3:4b"})
        assert h_ok.available is True
        assert h_ok.status == "ready"
        assert h_ok.active_model == "qwen3:4b"

    # 2. Unreachable Ollama -> unavailable (no crash)
    fake_hw_down = SimpleNamespace(ollama=SimpleNamespace(reachable=False))
    with patch("backend.ai.providers.local_ollama.get_shared_hardware_monitor") as mock_hm:
        mock_hm.return_value.get_hardware_snapshot.return_value = fake_hw_down
        h_down = provider.health_check()
        assert h_down.available is False
        assert h_down.status == "unavailable"

    # 3. Generate delegates to OllamaLLM._call_ollama_chat
    with patch("backend.ai.providers.local_ollama.OllamaLLM") as mock_llm_cls:
        mock_llm_cls.return_value._call_ollama_chat.return_value = "Local response"
        resp = provider.generate(AIProviderRequest(messages=[{"role": "user", "content": "hello"}]))
        assert resp.content == "Local response"
        assert resp.provider_id == PROVIDER_LOCAL
        assert resp.model == "qwen3:4b"

    # 4. Emergency stop cancels local generation
    mgr = get_shared_emergency_stop_manager()
    mgr.trigger_emergency_stop(reason="Test halt")
    with pytest.raises(ProviderCancelledError):
        provider.generate(AIProviderRequest(messages=[{"role": "user", "content": "hello"}]))


def test_fastapi_provider_endpoints_and_non_disclosure(tmp_path: Path):
    reg = _make_registry(tmp_path)
    client = TestClient(app)

    # Untrusted Origin rejected
    r_untrusted = client.get("/api/providers", headers={"Origin": "https://evil.example.com"})
    assert r_untrusted.status_code == 403

    fake_hw = SimpleNamespace(ollama=SimpleNamespace(reachable=True))
    with patch("backend.api.providers.get_shared_provider_registry", return_value=reg), \
         patch("backend.ai.providers.local_ollama.get_shared_hardware_monitor") as mock_hm, \
         patch("backend.ai.providers.local_ollama.get_shared_model_manager") as mock_mm:
        mock_hm.return_value.get_hardware_snapshot.return_value = fake_hw
        mock_mm.return_value.list_installed_models.return_value = [SimpleNamespace(name="qwen3:4b")]

        r_list = client.get("/api/providers")
        assert r_list.status_code == 200
        ids = [p["provider_id"] for p in r_list.json()["providers"]]
        assert ids == [PROVIDER_LOCAL, PROVIDER_BYOK, PROVIDER_VOXPILOT_CLOUD]

        r_active = client.get("/api/providers/active")
        assert r_active.status_code == 200
        assert r_active.json()["profile_config"]["active_provider_id"] == PROVIDER_LOCAL
        assert "api_key" not in r_active.text

        r_bad_secret = client.post(
            "/api/providers/validate",
            json={"provider_id": PROVIDER_BYOK, "config": {"api_key": "sk-test123"}},
        )
        assert r_bad_secret.status_code == 422

        r_select_unavail = client.post(
            "/api/providers/select",
            json={"provider_id": PROVIDER_VOXPILOT_CLOUD},
        )
        assert r_select_unavail.status_code == 400

        r_select_local = client.post(
            "/api/providers/select",
            json={"provider_id": PROVIDER_LOCAL, "config": {"local_model": "qwen3:4b"}},
        )
        assert r_select_local.status_code == 200
        assert r_select_local.json()["profile_config"]["active_provider_id"] == PROVIDER_LOCAL
