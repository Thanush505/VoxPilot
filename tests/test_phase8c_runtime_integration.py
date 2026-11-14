"""
VoxPilot - Phase 8C: Unified Runtime AI Provider Integration Tests.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest
from sqlmodel import SQLModel, create_engine

from backend.agent.graph import VoxPilotAgent
from backend.agent.intent import IntentRecognizer
from backend.agent.llm import OllamaLLM
from backend.agent.planner import AgentPlanner
from backend.ai.email_assistant import DPAPICredentialStore, EmailAssistantService
from backend.ai.providers import (
    AIProviderRegistry,
    OpenAIBYOKAdapter,
    PROVIDER_BYOK,
    PROVIDER_LOCAL,
    ProviderAuthenticationError,
    ProviderCancelledError,
    ProviderConfigurationError,
    ProviderUnavailableError,
    WindowsDPAPIProviderCredentialStore,
    run_provider_chat,
)
from backend.ai.vision import OllamaVisionProvider
from backend.automation.emergency_stop import get_shared_emergency_stop_manager


@pytest.fixture(autouse=True)
def _reset_emergency_stop():
    mgr = get_shared_emergency_stop_manager()
    mgr.reset_emergency_stop()
    yield
    mgr.reset_emergency_stop()


def _make_registry_with_openai_mock(tmp_path: Path, handler) -> AIProviderRegistry:
    db_path = tmp_path / "phase8c_test.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    cred_store = WindowsDPAPIProviderCredentialStore(storage_dir=tmp_path / "creds")
    transport = httpx.MockTransport(handler)
    adapter = OpenAIBYOKAdapter(
        client_factory=lambda timeout: httpx.Client(transport=transport, timeout=timeout)
    )
    return AIProviderRegistry(
        engine=engine,
        credential_store=cred_store,
        openai_adapter=adapter,
    )


def test_runtime_switching_local_to_byok_and_back_with_memory_preservation(tmp_path: Path):
    openai_calls = []

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "gpt-4o-mini"}]})
        payload = json.loads(request.content.decode("utf-8"))
        openai_calls.append(payload)
        return httpx.Response(
            200,
            json={
                "model": payload["model"],
                "choices": [{"message": {"role": "assistant", "content": "OpenAI runtime reply"}, "finish_reason": "stop"}],
                "usage": {"total_tokens": 20},
            },
        )

    reg = _make_registry_with_openai_mock(tmp_path, _handler)
    secret = "sk-phase8c-test-12345678"
    reg.configure_byok_openai(profile_id="default", api_key=secret, model="gpt-4o-mini", validate=True)

    with patch("backend.ai.providers.runtime.get_shared_provider_registry", return_value=reg), \
         patch("backend.agent.llm.ollama.chat") as mock_ollama_chat:
        mock_ollama_chat.return_value = SimpleNamespace(message=SimpleNamespace(content="Local qwen3 reply"))
        llm = OllamaLLM(model="qwen3:4b")

        # 1. Default is Local AI -> calls ollama.chat, NOT OpenAI
        r1 = llm.generate_contextual_response(
            "Hello",
            memories=[{"memory_type": "user_preference", "content": "Prefer concise answers"}],
            recent_turns_text="User: Hi",
        )
        assert r1 == "Local qwen3 reply"
        assert mock_ollama_chat.call_count == 1
        assert len(openai_calls) == 0

        # 2. Explicitly switch to OpenAI BYOK -> calls OpenAI with memories & turns preserved
        reg.select_provider(PROVIDER_BYOK, profile_id="default")
        r2 = llm.generate_contextual_response(
            "What do I prefer?",
            memories=[{"memory_type": "user_preference", "content": "Prefer concise answers"}],
            recent_turns_text="User: Hi\nAssistant: Hello",
        )
        assert r2 == "OpenAI runtime reply"
        assert len(openai_calls) == 1
        sys_msg = openai_calls[0]["messages"][0]["content"]
        assert "Prefer concise answers" in sys_msg
        assert "User: Hi" in sys_msg
        assert mock_ollama_chat.call_count == 1  # untouched

        # 3. Explicitly switch back to Local AI -> uses qwen3:4b again
        reg.select_provider(PROVIDER_LOCAL, profile_id="default")
        r3 = llm.generate_contextual_response("Back to local")
        assert r3 == "Local qwen3 reply"
        assert mock_ollama_chat.call_count == 2
        assert len(openai_calls) == 1


def test_agent_graph_surfaces_byok_errors_without_silent_fallback(tmp_path: Path):
    def _failing_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "gpt-4o-mini"}]})
        return httpx.Response(
            401,
            json={"error": {"message": "Incorrect API key", "code": "invalid_api_key"}},
        )

    reg = _make_registry_with_openai_mock(tmp_path, _failing_handler)
    reg.configure_byok_openai(profile_id="default", api_key="sk-bad-key-999999", model="gpt-4o-mini", validate=True)
    reg.select_provider(PROVIDER_BYOK, profile_id="default")

    graph = VoxPilotAgent(
        intent_recognizer=MagicMock(),
        planner=MagicMock(),
        tool_executor=MagicMock(),
    )

    with patch("backend.ai.providers.runtime.get_shared_provider_registry", return_value=reg), \
         patch("backend.agent.llm.ollama.chat") as mock_ollama_chat:
        out = graph.generate_response({
            "user_input": "Hello there",
            "intent": "general_conversation",
            "execution_status": "completed",
            "execution_results": [],
        })
        assert out["execution_status"] == "failed"
        assert "authentication failed" in out["error"].lower()
        assert "AI provider error" in out["response"]
        # Never silently fell back to Local Ollama!
        mock_ollama_chat.assert_not_called()


def test_intent_and_planner_json_mode_with_openai_byok(tmp_path: Path):
    seen_formats = []

    def _json_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "gpt-4o-mini"}]})
        body = json.loads(request.content.decode("utf-8"))
        seen_formats.append(body.get("response_format"))
        sys_txt = body["messages"][0]["content"]
        if "steps" in sys_txt:
            content = json.dumps({
                "steps": [
                    {
                        "action": "open_application",
                        "parameters": {"application_name": "notepad"},
                        "description": "Open Notepad",
                        "requires_confirmation": False,
                    }
                ]
            })
        else:
            content = json.dumps({
                "intent": "open_application",
                "confidence": 0.92,
                "reason": "User wants to open an app",
            })
        return httpx.Response(
            200,
            json={
                "model": "gpt-4o-mini",
                "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            },
        )

    reg = _make_registry_with_openai_mock(tmp_path, _json_handler)
    reg.configure_byok_openai(profile_id="default", api_key="sk-json-test-123456", model="gpt-4o-mini", validate=True)
    reg.select_provider(PROVIDER_BYOK, profile_id="default")

    with patch("backend.ai.providers.runtime.get_shared_provider_registry", return_value=reg):
        recognizer = IntentRecognizer()
        intent_res = recognizer.recognize_intent("Could you please do the thing with that one program?")
        assert intent_res["intent"] == "open_application"

        planner = AgentPlanner()
        plan = planner.create_plan(
            "Coordinate launching my custom workspace setup",
            "open_application",
        )
        assert len(plan["steps"]) == 1
        assert plan["steps"][0]["action"] == "open_application"

    assert all(fmt == {"type": "json_object"} for fmt in seen_formats)


def test_unsupported_capabilities_and_vision_refusal_on_byok(tmp_path: Path):
    def _ok_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "gpt-4o-mini"}]})
        return httpx.Response(404)

    reg = _make_registry_with_openai_mock(tmp_path, _ok_handler)
    reg.configure_byok_openai(profile_id="default", api_key="sk-cap-test-123456", model="gpt-4o-mini", validate=True)
    reg.select_provider(PROVIDER_BYOK, profile_id="default")

    with patch("backend.ai.providers.runtime.get_shared_provider_registry", return_value=reg), \
         patch("backend.ai.vision.ollama.chat") as mock_vision_ollama:
        # Native tool_calling unsupported on Phase 8C OpenAI adapter
        with pytest.raises(ProviderConfigurationError, match="tool calling"):
            run_provider_chat(
                [{"role": "user", "content": "hi"}],
                require_tool_calling=True,
            )

        # Vision unsupported on OpenAI BYOK -> clear error, no silent Ollama fallback
        vs = OllamaVisionProvider()
        res = vs.analyze_image("ZmFrZS1pbWFnZQ==", "What is on my screen?")
        assert res.available is False
        assert res.status == "unsupported_capability"
        assert "does not support vision" in res.answer.lower()
        mock_vision_ollama.assert_not_called()


def test_specialist_email_and_emergency_stop_on_byok(tmp_path: Path):
    def _email_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "gpt-4o-mini"}]})
        return httpx.Response(
            200,
            json={
                "model": "gpt-4o-mini",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps({"subject": "Project Sync", "body": "Hi team, let's sync."}),
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    reg = _make_registry_with_openai_mock(tmp_path, _email_handler)
    reg.configure_byok_openai(profile_id="work", api_key="sk-email-test-123456", model="gpt-4o-mini", validate=True)
    reg.select_provider(PROVIDER_BYOK, profile_id="work")

    with patch("backend.ai.providers.runtime.get_shared_provider_registry", return_value=reg):
        svc = EmailAssistantService(
            credential_store=DPAPICredentialStore(storage_dir=tmp_path / "email_creds")
        )
        draft_res = svc.generate_draft(
            instruction="Draft an email requesting a project sync",
            to=["guide@example.com"],
            profile_id="work",
        )
        assert draft_res["success"] is True
        assert draft_res["draft"]["subject"] == "Project Sync"
        assert "let's sync" in draft_res["draft"]["body"]

        # Profile isolation: 'personal' is still Local AI
        assert reg.get_profile_config("personal")["active_provider_id"] == PROVIDER_LOCAL

        # Emergency stop halts BYOK runtime calls immediately
        get_shared_emergency_stop_manager().trigger_emergency_stop(reason="Test halt")
        with pytest.raises(ProviderCancelledError):
            run_provider_chat([{"role": "user", "content": "Hello"}], profile_id="work")
