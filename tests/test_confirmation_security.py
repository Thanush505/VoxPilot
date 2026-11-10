"""
VoxPilot - Action Confirmation and Trust Boundary Security Test Suite
Verifies:
1. Confirmation actions (approve/reject) are strictly excluded from ToolRegistry (LLM cannot self-approve).
2. LLM-supplied 'confirmed=True' or 'is_consequential=False' cannot bypass confirmation requirements.
3. Cryptographic confirmation token lifecycle: single-use, expiration, parameter hash binding, tampering detection.
4. Cross-execution token mismatches rejected with HTTP 403 Forbidden.
5. CSRF defense: cross-origin browser Origin headers rejected with HTTP 403 Forbidden on approval/rejection endpoints.
6. Tokens are never leaked in assistant responses, voice synthesis output, or persisted SQLite records.
7. Localhost server binding (127.0.0.1) enforcement.
"""

import time
import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from backend.main import app
from backend.agent.tool_registry import ToolRegistry
from backend.agent.tool_executor import ToolExecutor
from backend.agent.graph import VoxPilotAgent
from backend.api.agent import set_agent, clear_executions
from backend.automation.confirmation_manager import (
    ConfirmationManager,
    ConfirmationStatus,
    InvalidConfirmationError,
    MissingUserApprovalError,
    ConfirmationExpiredError,
    ReplayedConfirmationError,
    ActionMismatchConfirmationError,
    TamperedActionConfirmationError,
    ConfirmationRejectedError,
)
from backend.database.models import sanitize_dict_for_storage, serialize_sanitized_json


# ==============================================================================
# Fixtures
# ==============================================================================

@pytest.fixture
def client():
    """FastAPI TestClient fixture."""
    return TestClient(app)


@pytest.fixture(autouse=True)
def cleanup():
    """Ensure clean execution state before each test."""
    clear_executions()
    yield
    clear_executions()
    set_agent(None)


# ==============================================================================
# 1. Tool Registry Trust Boundary Tests
# ==============================================================================

def test_confirmation_operations_not_in_tool_registry():
    """Confirmation approval and rejection operations must NOT exist as tools in ToolRegistry."""
    registry = ToolRegistry(register_defaults=True)

    disallowed_actions = [
        "approve_confirmation",
        "reject_confirmation",
        "approve",
        "reject",
        "bypass_confirmation",
        "confirm_action",
    ]

    for action in disallowed_actions:
        assert not registry.has_tool(action), f"Security violation: '{action}' must not be a registered tool."
        assert action not in registry.list_tools()


def test_llm_cannot_bypass_confirmation_with_confirmed_flag():
    """LLM supplying 'confirmed=True' in parameters cannot bypass confirmation."""
    registry = ToolRegistry(register_defaults=True)
    cm = ConfirmationManager()
    executor = ToolExecutor(registry=registry, confirmation_manager=cm)

    # delete_file is an inherently consequential action
    malicious_params = {
        "file_path": "important_system_file.txt",
        "confirmed": True,
    }

    result = executor.execute_step({"action": "delete_file", "parameters": malicious_params})

    # Must be paused for confirmation, not executed!
    assert result["status"] == "confirmation_required"
    assert result["success"] is False
    assert result["confirmation_token"] is not None


def test_llm_cannot_bypass_confirmation_with_fake_token():
    """Supplying an unapproved or fabricated confirmation token raises an invalid token error."""
    registry = ToolRegistry(register_defaults=True)
    cm = ConfirmationManager()
    executor = ToolExecutor(registry=registry, confirmation_manager=cm)

    fake_params = {
        "file_path": "important_file.txt",
        "confirmation_token": "fabricated_token_abc_123",
    }

    result = executor.execute_step({"action": "delete_file", "parameters": fake_params})
    assert result["status"] == "confirmation_failed"
    assert result["success"] is False
    assert "invalid" in (result.get("error") or "").lower() or "not found" in (result.get("error") or "").lower()


# ==============================================================================
# 2. Cryptographic Token Lifecycle & Tamper Resistance Tests
# ==============================================================================

def test_unapproved_token_cannot_be_consumed():
    """A generated token that is still pending user approval cannot be consumed."""
    cm = ConfirmationManager()
    conf = cm.create_confirmation("delete_file", {"file_path": "doc.txt"})

    with pytest.raises(MissingUserApprovalError):
        cm.validate_and_consume(conf.token, "delete_file", {"file_path": "doc.txt"})


def test_parameter_tampering_detected_and_rejected():
    """Modifying approved parameters (e.g. changing file path) invalidates approval hash."""
    cm = ConfirmationManager()
    conf = cm.create_confirmation("delete_file", {"file_path": "safe_draft.txt"})
    cm.approve_confirmation(conf.token)

    # Attacker tries to consume token for a different target file
    tampered_params = {"file_path": "critical_database.sqlite"}

    with pytest.raises(TamperedActionConfirmationError):
        cm.validate_and_consume(conf.token, "delete_file", tampered_params)


def test_action_type_mismatch_detected_and_rejected():
    """Presenting a token approved for action A against action B is strictly rejected."""
    cm = ConfirmationManager()
    conf = cm.create_confirmation("delete_file", {"file_path": "file.txt"})
    cm.approve_confirmation(conf.token)

    with pytest.raises(ActionMismatchConfirmationError):
        cm.validate_and_consume(conf.token, "move_item", {"file_path": "file.txt"})


def test_single_use_token_replay_rejected():
    """An approved token consumed once cannot be replayed."""
    cm = ConfirmationManager()
    conf = cm.create_confirmation("delete_file", {"file_path": "doc.txt"})
    cm.approve_confirmation(conf.token)

    # First consumption succeeds
    consumed = cm.validate_and_consume(conf.token, "delete_file", {"file_path": "doc.txt"})
    assert consumed.status == ConfirmationStatus.CONSUMED

    # Replay attempt fails
    with pytest.raises(ReplayedConfirmationError):
        cm.validate_and_consume(conf.token, "delete_file", {"file_path": "doc.txt"})


def test_expired_token_rejected():
    """A token that exceeded its lifetime cannot be approved or consumed."""
    cm = ConfirmationManager(default_timeout_seconds=0)  # expires immediately
    conf = cm.create_confirmation("delete_file", {"file_path": "doc.txt"})

    # Allow expiration window
    time.sleep(0.01)

    with pytest.raises(ConfirmationExpiredError):
        cm.approve_confirmation(conf.token)


def test_rejected_token_cannot_be_approved_or_consumed():
    """A token explicitly rejected by the human user cannot be subsequently approved or consumed."""
    cm = ConfirmationManager()
    conf = cm.create_confirmation("delete_file", {"file_path": "doc.txt"})

    cm.reject_confirmation(conf.token)

    with pytest.raises(ConfirmationRejectedError):
        cm.approve_confirmation(conf.token)

    with pytest.raises(ConfirmationRejectedError):
        cm.validate_and_consume(conf.token, "delete_file", {"file_path": "doc.txt"})


# ==============================================================================
# 3. REST API Confirmation & CSRF Protection Tests
# ==============================================================================

def test_cross_execution_token_mismatch_rejected_http_403(client: TestClient):
    """Presenting an approval token from execution A to execution B returns HTTP 403 Forbidden."""
    cm = ConfirmationManager()
    reg = ToolRegistry(register_defaults=True)
    executor = ToolExecutor(registry=reg, confirmation_manager=cm)

    # Mock planner returning consequential step
    mock_intent_rec = MagicMock()
    mock_intent_rec.recognize_intent.return_value = {"intent": "file_management", "confidence": 0.99, "reason": "test"}
    mock_planner = MagicMock()
    mock_planner.create_plan.return_value = {
        "steps": [{"action": "delete_file", "description": "Delete file", "parameters": {"file_path": "test.txt"}, "requires_confirmation": True}],
        "response": "Deleting file.",
    }

    agent = VoxPilotAgent(
        tool_registry=reg,
        tool_executor=executor,
        confirmation_manager=cm,
        intent_recognizer=mock_intent_rec,
        planner=mock_planner,
    )
    set_agent(agent)

    # Execution 1
    res1 = client.post("/agent/execute", json={"user_input": "delete test.txt", "execution_id": "exec_1"})
    assert res1.status_code == 200
    token1 = res1.json()["pending_confirmation"]["token"]

    # Execution 2
    res2 = client.post("/agent/execute", json={"user_input": "delete test.txt", "execution_id": "exec_2"})
    assert res2.status_code == 200
    token2 = res2.json()["pending_confirmation"]["token"]
    assert token1 != token2

    # Attempt to approve exec_2 using token1 from exec_1
    cross_res = client.post(
        "/agent/confirmations/exec_2/approve",
        json={"token": token1},
    )
    assert cross_res.status_code == 403
    assert "token does not match" in cross_res.json()["detail"].lower()


def test_csrf_cross_origin_approval_blocked_http_403(client: TestClient):
    """Approval request originating from untrusted web browser origin (CSRF) is blocked with 403."""
    cm = ConfirmationManager()
    reg = ToolRegistry(register_defaults=True)
    executor = ToolExecutor(registry=reg, confirmation_manager=cm)

    mock_intent_rec = MagicMock()
    mock_intent_rec.recognize_intent.return_value = {"intent": "file_management", "confidence": 0.99, "reason": "test"}
    mock_planner = MagicMock()
    mock_planner.create_plan.return_value = {
        "steps": [{"action": "delete_file", "description": "Delete", "parameters": {"file_path": "test.txt"}, "requires_confirmation": True}],
        "response": "Deleting.",
    }

    agent = VoxPilotAgent(
        tool_registry=reg,
        tool_executor=executor,
        confirmation_manager=cm,
        intent_recognizer=mock_intent_rec,
        planner=mock_planner,
    )
    set_agent(agent)

    res = client.post("/agent/execute", json={"user_input": "delete test.txt", "execution_id": "exec_csrf"})
    token = res.json()["pending_confirmation"]["token"]

    # Request with malicious cross-origin header
    malicious_origins = [
        "http://evil.com",
        "https://attacker.site:8080",
        "http://phishing.local",
    ]
    for origin in malicious_origins:
        attack_res = client.post(
            "/agent/confirmations/exec_csrf/approve",
            json={"token": token},
            headers={"Origin": origin},
        )
        assert attack_res.status_code == 403
        assert "forbidden" in attack_res.json()["detail"].lower()

    # Trusted localhost origin passes origin check
    trusted_res = client.post(
        "/agent/confirmations/exec_csrf/approve",
        json={"token": token},
        headers={"Origin": "http://localhost:3000"},
    )
    assert trusted_res.status_code == 200


# ==============================================================================
# 4. Data Exposure & Sanitization Tests
# ==============================================================================

def test_confirmation_tokens_stripped_from_storage_and_models():
    """Sensitive keys (token, confirmation_token, passwords) are scrubbed before SQLite persistence."""
    data = {
        "user_input": "delete sensitive.docx",
        "token": "secret_token_12345",
        "confirmation_token": "conf_token_67890",
        "parameters": {
            "file_path": "sensitive.docx",
            "confirmation_token": "conf_token_67890",
            "safe_flag": True,
        },
    }

    sanitized = sanitize_dict_for_storage(data)
    assert "token" not in sanitized
    assert "confirmation_token" not in sanitized
    assert "confirmation_token" not in sanitized["parameters"]
    assert sanitized["parameters"]["safe_flag"] is True

    serialized = serialize_sanitized_json(data)
    assert "secret_token_12345" not in serialized
    assert "conf_token_67890" not in serialized


def test_voice_pipeline_never_speaks_confirmation_tokens():
    """Voice pipeline response generation never speaks or includes raw confirmation tokens."""
    from backend.voice.voice_pipeline import VoicePipeline

    mock_agent = MagicMock()

    # Agent returns confirmation_required state
    mock_agent.process_request.return_value = {
        "execution_status": "confirmation_required",
        "requires_confirmation": True,
        "pending_confirmation": {
            "token": "SUPER_SECRET_TOKEN_XYZ_999",
            "action": "delete_file",
        },
        "response": "Action 'delete_file' requires your confirmation before proceeding.",
    }

    pipeline = VoicePipeline(
        enable_wake_word=False,
        agent=mock_agent,
    )
    pipeline.stt = MagicMock()
    pipeline.tts = MagicMock()

    spoken_text = pipeline.generate_response("delete my file")

    assert "SUPER_SECRET_TOKEN_XYZ_999" not in spoken_text
    assert "token" not in spoken_text.lower() or "approval" in spoken_text.lower()
    assert "approval before performing this action" in spoken_text


def test_localhost_server_binding_enforced():
    """Verify that backend/main.py enforces localhost-only (127.0.0.1) server binding."""
    with open("backend/main.py", "r", encoding="utf-8") as f:
        content = f.read()

    assert 'host="127.0.0.1"' in content or "host='127.0.0.1'" in content
    assert '0.0.0.0' not in content
