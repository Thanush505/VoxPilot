"""
VoxPilot - End-to-End Agent Integration Verification Suite
Tests the 13 core end-to-end agent workflows across REST API, LangGraph workflow,
ToolExecutor, memory context manager, confirmation security, and database persistence:

1. General conversational request (no desktop tools, responds politely).
2. Concrete application launch (launches application and records active window).
3. System information query (runs get_system_info and returns hardware metrics).
4. File search discovery (runs search_files and returns matching paths).
5. Ambiguous target detection (multiple search results + pronoun 'open it' -> asks clarification).
6. Multi-turn pronoun resolution ('minimize it' binds active window from previous turn).
7. Conversation isolation (Conversation B does not inherit Conversation A's window context).
8. Unsupported action rejection (plan validation rejects unregistered tool names).
9. Schema parameter validation failure (validation rejects invalid parameter types/fields).
10. Intermediate failure halt (failure in step 1 stops execution of dependent steps).
11. Consequential action pause (delete_file pauses with confirmation_required status).
12. Resuming execution after approval (token approved via API resumes and completes execution).
13. Session persistence across SQLite reopening (records retain state after engine disposal).
"""

import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from backend.main import app
from backend.agent.graph import VoxPilotAgent
from backend.agent.tool_registry import ToolRegistry
from backend.agent.tool_executor import ToolExecutor
from backend.automation.confirmation_manager import ConfirmationManager
from backend.memory.context_manager import ConversationContextManager
from backend.database.repository import DatabaseRepository
from backend.database.connection import close_db, reset_db_connection, init_db_sync
from backend.api.agent import set_agent, clear_executions


@pytest.fixture
def test_setup():
    clear_executions()
    cm = ConfirmationManager()
    reg = ToolRegistry(register_defaults=True)
    executor = ToolExecutor(registry=reg, confirmation_manager=cm)
    ctx_mgr = ConversationContextManager(revalidate_targets=False)

    yield cm, reg, executor, ctx_mgr

    clear_executions()
    set_agent(None)


@pytest.fixture
def client():
    return TestClient(app)


# ==============================================================================
# Workflow 1: General Conversational Query
# ==============================================================================

def test_workflow_01_general_conversation(test_setup):
    """General greeting or conversation responds without executing automation tools."""
    cm, reg, executor, ctx_mgr = test_setup

    mock_intent = MagicMock()
    mock_intent.recognize_intent.return_value = {
        "intent": "general_conversation",
        "confidence": 0.99,
        "reason": "User greeting",
    }
    mock_planner = MagicMock()
    mock_planner.create_plan.return_value = {
        "steps": [
            {
                "action": "respond",
                "description": "Hello! How can I assist you with your computer today?",
                "parameters": {},
                "requires_confirmation": False,
            }
        ],
        "response": "Hello! How can I assist you with your computer today?",
    }

    agent = VoxPilotAgent(
        tool_registry=reg,
        tool_executor=executor,
        confirmation_manager=cm,
        intent_recognizer=mock_intent,
        planner=mock_planner,
        context_manager=ctx_mgr,
    )

    result = agent.process_request("hello VoxPilot", conversation_id="conv_w1")
    assert result["execution_status"] == "completed"
    assert "assist you" in result["response"].lower()
    assert len(result["completed_steps"]) == 1
    assert result["completed_steps"][0]["action"] == "respond"


# ==============================================================================
# Workflow 2: Direct Application Launch
# ==============================================================================

def test_workflow_02_application_launch(test_setup):
    """Direct application launch executes launch_application and populates active_window in context."""
    cm, reg, executor, ctx_mgr = test_setup

    mock_intent = MagicMock()
    mock_intent.recognize_intent.return_value = {
        "intent": "application_control",
        "confidence": 0.98,
        "reason": "Launch notepad",
    }
    mock_planner = MagicMock()
    mock_planner.create_plan.return_value = {
        "steps": [
            {
                "action": "launch_application",
                "description": "Launch Notepad",
                "parameters": {"application_name": "notepad"},
                "requires_confirmation": False,
            }
        ],
        "response": "Launching Notepad.",
    }

    # Mock tool executor execute_step to return successful HWND
    executor.execute_step = MagicMock(return_value={
        "action": "launch_application",
        "step_index": 0,
        "status": "completed",
        "success": True,
        "message": "Notepad launched successfully.",
        "data": {"hwnd": 54321, "title": "Untitled - Notepad", "app_name": "notepad"},
    })

    agent = VoxPilotAgent(
        tool_registry=reg,
        tool_executor=executor,
        confirmation_manager=cm,
        intent_recognizer=mock_intent,
        planner=mock_planner,
        context_manager=ctx_mgr,
    )

    result = agent.process_request("open notepad", conversation_id="conv_w2")
    assert result["execution_status"] == "completed"
    assert len(result["completed_steps"]) == 1

    # Context should have captured Notepad
    ctx = ctx_mgr.get_context("conv_w2")
    assert ctx.active_window_hwnd == 54321
    assert ctx.recent_application == "notepad"


# ==============================================================================
# Workflow 3: System Information Query
# ==============================================================================

def test_workflow_03_system_info_query(test_setup):
    """System information query executes get_system_info tool and returns hardware stats."""
    cm, reg, executor, ctx_mgr = test_setup

    mock_intent = MagicMock()
    mock_intent.recognize_intent.return_value = {
        "intent": "system_information",
        "confidence": 0.97,
        "reason": "Battery query",
    }
    mock_planner = MagicMock()
    mock_planner.create_plan.return_value = {
        "steps": [
            {
                "action": "get_system_info",
                "description": "Inspect system performance and hardware metrics",
                "parameters": {},
                "requires_confirmation": False,
            }
        ],
        "response": "Checking system information.",
    }

    executor.execute_step = MagicMock(return_value={
        "action": "get_system_info",
        "step_index": 0,
        "status": "completed",
        "success": True,
        "message": "Battery status: 88% plugged in.",
        "data": {"battery": {"percent": 88, "power_plugged": True}},
    })

    agent = VoxPilotAgent(
        tool_registry=reg,
        tool_executor=executor,
        confirmation_manager=cm,
        intent_recognizer=mock_intent,
        planner=mock_planner,
        context_manager=ctx_mgr,
    )

    result = agent.process_request("how is my battery", conversation_id="conv_w3")
    assert result["execution_status"] == "completed"
    assert result["completed_steps"][0]["action"] == "get_system_info"


# ==============================================================================
# Workflow 4: File Search Discovery
# ==============================================================================

def test_workflow_04_file_search_discovery(test_setup):
    """File search query executes search_files and stores discovered files in context memory."""
    cm, reg, executor, ctx_mgr = test_setup

    mock_intent = MagicMock()
    mock_intent.recognize_intent.return_value = {
        "intent": "file_management",
        "confidence": 0.95,
        "reason": "File search",
    }
    mock_planner = MagicMock()
    mock_planner.create_plan.return_value = {
        "steps": [
            {
                "action": "search_files",
                "description": "Search for quarterly reports",
                "parameters": {"directory_path": "C:/Reports", "query": "Q1"},
                "requires_confirmation": False,
            }
        ],
        "response": "Searching for files.",
    }

    executor.execute_step = MagicMock(return_value={
        "action": "search_files",
        "step_index": 0,
        "status": "completed",
        "success": True,
        "message": "Found 1 matching file.",
        "data": {"matches": ["C:/Reports/Q1_Financials.pdf"]},
    })

    agent = VoxPilotAgent(
        tool_registry=reg,
        tool_executor=executor,
        confirmation_manager=cm,
        intent_recognizer=mock_intent,
        planner=mock_planner,
        context_manager=ctx_mgr,
    )

    result = agent.process_request("find Q1 reports", conversation_id="conv_w4")
    assert result["execution_status"] == "completed"
    ctx = ctx_mgr.get_context("conv_w4")
    assert len(ctx.recent_files) == 1
    assert ctx.recent_files[0] == "C:/Reports/Q1_Financials.pdf"


# ==============================================================================
# Workflow 5: Ambiguous Target Disambiguation
# ==============================================================================

def test_workflow_05_ambiguous_file_clarification(test_setup):
    """When multiple file candidates exist and user says 'open it', agent flags ambiguity."""
    cm, reg, executor, ctx_mgr = test_setup

    mock_intent = MagicMock()
    mock_intent.recognize_intent.return_value = {
        "intent": "file_management",
        "confidence": 0.96,
        "reason": "Open file",
    }
    mock_planner = MagicMock()

    # Seed context with 2 files from previous turn
    ctx = ctx_mgr.get_context("conv_w5")
    ctx.recent_files = ["C:/docs/resume_v1.docx", "C:/docs/resume_v2.docx"]

    agent = VoxPilotAgent(
        tool_registry=reg,
        tool_executor=executor,
        confirmation_manager=cm,
        intent_recognizer=mock_intent,
        planner=mock_planner,
        context_manager=ctx_mgr,
    )

    result = agent.process_request("open it", conversation_id="conv_w5")
    assert result["execution_status"] == "completed"
    assert len(result["plan"]) == 1
    assert result["plan"][0]["action"] == "ask_clarification"
    assert "multiple files" in result["response"].lower()


# ==============================================================================
# Workflow 6: Multi-Turn Window Reference Resolution
# ==============================================================================

def test_workflow_06_multiturn_window_reference_resolution(test_setup):
    """Pronoun 'minimize it' in Turn 2 resolves to the active window launched in Turn 1."""
    cm, reg, executor, ctx_mgr = test_setup

    # Turn 1: Launch application
    ctx_mgr.record_turn(
        conversation_id="conv_w6",
        user_input="launch notepad",
        intent="application_control",
        plan=[{"action": "launch_application", "parameters": {"application_name": "notepad"}}],
        execution_results=[{
            "action": "launch_application",
            "success": True,
            "data": {"hwnd": 77777, "title": "Notepad", "app_name": "notepad"},
        }],
        completed_steps=[{"action": "launch_application"}],
        response="Notepad launched.",
    )

    # Turn 2: 'minimize it'
    mock_intent = MagicMock()
    mock_intent.recognize_intent.return_value = {"intent": "window_management", "confidence": 0.98, "reason": "Minimize"}
    mock_planner = MagicMock()
    mock_planner.create_plan.return_value = {
        "steps": [
            {
                "action": "manage_window",
                "description": "Minimize window",
                "parameters": {"action": "minimize", "hwnd": 0},
                "requires_confirmation": False,
            }
        ],
        "response": "Minimizing window.",
    }

    agent = VoxPilotAgent(
        tool_registry=reg,
        tool_executor=executor,
        confirmation_manager=cm,
        intent_recognizer=mock_intent,
        planner=mock_planner,
        context_manager=ctx_mgr,
    )

    # In create_plan, resolved_window (hwnd 77777) must be bound into step parameters
    plan_result = agent.create_plan({
        "user_input": "minimize it",
        "intent": "window_management",
        "conversation_id": "conv_w6",
        "execution_status": "planned",
    })

    assert plan_result["plan"][0]["parameters"]["hwnd"] == 77777


# ==============================================================================
# Workflow 7: Conversation Isolation
# ==============================================================================

def test_workflow_07_conversation_isolation(test_setup):
    """Conversation B does not inherit Conversation A's window context and asks for clarification."""
    cm, reg, executor, ctx_mgr = test_setup
    revalidating_ctx_mgr = ConversationContextManager(revalidate_targets=True)

    # Conv A launches Calculator
    revalidating_ctx_mgr.record_turn(
        conversation_id="conv_A",
        user_input="open calculator",
        intent="application_control",
        plan=[{"action": "launch_application", "parameters": {"application_name": "calculator"}}],
        execution_results=[{
            "action": "launch_application",
            "success": True,
            "data": {"hwnd": 88888, "title": "Calculator"},
        }],
        completed_steps=[{"action": "launch_application"}],
        response="Calculator opened.",
    )

    # Conv B asks to "minimize it"
    mock_intent_b = MagicMock()
    mock_intent_b.recognize_intent.return_value = {
        "intent": "window_management",
        "confidence": 0.98,
        "reason": "Minimize",
    }
    mock_planner_b = MagicMock()

    agent_b = VoxPilotAgent(
        tool_registry=reg,
        tool_executor=executor,
        confirmation_manager=cm,
        intent_recognizer=mock_intent_b,
        planner=mock_planner_b,
        context_manager=revalidating_ctx_mgr,
    )

    result_b = agent_b.process_request("minimize it", conversation_id="conv_B")
    assert result_b["execution_status"] == "completed"
    assert result_b["plan"][0]["action"] == "ask_clarification"
    assert "no active window" in result_b["response"].lower() or "which window" in result_b["response"].lower()


# ==============================================================================
# Workflow 8: Unsupported Action Rejection
# ==============================================================================

def test_workflow_08_unsupported_action_rejected(test_setup):
    """Plan specifying an unregistered/unapproved tool action is rejected at plan validation."""
    cm, reg, executor, ctx_mgr = test_setup

    mock_intent = MagicMock()
    mock_intent.recognize_intent.return_value = {"intent": "system_information", "confidence": 0.99, "reason": "test"}
    mock_planner = MagicMock()
    # Malicious or hallucinated tool action
    mock_planner.create_plan.return_value = {
        "steps": [
            {
                "action": "execute_arbitrary_shell_payload",
                "description": "Run shell",
                "parameters": {"cmd": "whoami"},
                "requires_confirmation": False,
            }
        ],
        "response": "Executing payload.",
    }

    agent = VoxPilotAgent(
        tool_registry=reg,
        tool_executor=executor,
        confirmation_manager=cm,
        intent_recognizer=mock_intent,
        planner=mock_planner,
        context_manager=ctx_mgr,
    )

    result = agent.process_request("run whoami", conversation_id="conv_w8")
    assert result["execution_status"] == "validation_failed"
    assert "not permitted or recognized" in result["response"]
    assert len(result["completed_steps"]) == 0


# ==============================================================================
# Workflow 9: Schema Parameter Validation Failure
# ==============================================================================

def test_workflow_09_invalid_parameters_halt_execution(test_setup):
    """Plan step failing Pydantic argument validation is rejected at validation node."""
    cm, reg, executor, ctx_mgr = test_setup

    mock_intent = MagicMock()
    mock_intent.recognize_intent.return_value = {"intent": "application_control", "confidence": 0.99, "reason": "test"}
    mock_planner = MagicMock()
    # launch_application requires string application_name
    mock_planner.create_plan.return_value = {
        "steps": [
            {
                "action": "launch_application",
                "description": "Invalid launch",
                "parameters": {"unsupported_field": 12345},  # missing required application_name
                "requires_confirmation": False,
            }
        ],
        "response": "Launching.",
    }

    agent = VoxPilotAgent(
        tool_registry=reg,
        tool_executor=executor,
        confirmation_manager=cm,
        intent_recognizer=mock_intent,
        planner=mock_planner,
        context_manager=ctx_mgr,
    )

    result = agent.process_request("open app", conversation_id="conv_w9")
    assert result["execution_status"] == "validation_failed"
    assert "failed validation" in result["response"].lower() or "missing" in result["response"].lower()


# ==============================================================================
# Workflow 10: Intermediate Step Failure Halts Execution
# ==============================================================================

def test_workflow_10_intermediate_step_failure_halts_execution(test_setup):
    """When step 1 fails, step 2 is not executed and overall status is failed."""
    cm, reg, executor, ctx_mgr = test_setup

    mock_intent = MagicMock()
    mock_intent.recognize_intent.return_value = {"intent": "application_control", "confidence": 0.99, "reason": "test"}
    mock_planner = MagicMock()
    mock_planner.create_plan.return_value = {
        "steps": [
            {"action": "launch_application", "description": "Step 1", "parameters": {"application_name": "notepad"}},
            {"action": "get_system_info", "description": "Step 2", "parameters": {}},
        ],
        "response": "Running two steps.",
    }

    # Step 1 fails
    executor.execute_step = MagicMock(return_value={
        "action": "launch_application",
        "step_index": 0,
        "status": "failed",
        "success": False,
        "error": "Binary not found on system.",
    })

    agent = VoxPilotAgent(
        tool_registry=reg,
        tool_executor=executor,
        confirmation_manager=cm,
        intent_recognizer=mock_intent,
        planner=mock_planner,
        context_manager=ctx_mgr,
    )

    result = agent.process_request("open notepad and check info", conversation_id="conv_w10")
    assert result["execution_status"] == "failed"
    assert len(result["completed_steps"]) == 0
    assert len(result["failed_steps"]) == 1
    assert "binary not found" in result["error"].lower()


# ==============================================================================
# Workflow 11: Consequential Action Pauses for Confirmation
# ==============================================================================

def test_workflow_11_consequential_action_pauses_for_confirmation(test_setup):
    """Consequential action (delete_file) halts in confirmation_required status with token."""
    cm, reg, executor, ctx_mgr = test_setup

    mock_intent = MagicMock()
    mock_intent.recognize_intent.return_value = {"intent": "file_management", "confidence": 0.99, "reason": "delete"}
    mock_planner = MagicMock()
    mock_planner.create_plan.return_value = {
        "steps": [
            {
                "action": "delete_file",
                "description": "Delete old project logs",
                "parameters": {"file_path": "C:/logs/old.log"},
                "requires_confirmation": True,
            }
        ],
        "response": "Deleting log file.",
    }

    agent = VoxPilotAgent(
        tool_registry=reg,
        tool_executor=executor,
        confirmation_manager=cm,
        intent_recognizer=mock_intent,
        planner=mock_planner,
        context_manager=ctx_mgr,
    )

    result = agent.process_request("delete C:/logs/old.log", conversation_id="conv_w11")
    assert result["execution_status"] == "confirmation_required"
    assert result["requires_confirmation"] is True
    assert result["pending_confirmation"] is not None
    assert result["pending_confirmation"]["action"] == "delete_file"
    assert result["pending_confirmation"]["token"] is not None


# ==============================================================================
# Workflow 12: Resuming Execution After Human Approval
# ==============================================================================

def test_workflow_12_resume_execution_after_approval(test_setup, client: TestClient):
    """Approving pending confirmation via REST API resumes execution and completes the step."""
    cm, reg, executor, ctx_mgr = test_setup

    mock_intent = MagicMock()
    mock_intent.recognize_intent.return_value = {"intent": "file_management", "confidence": 0.99, "reason": "delete"}
    mock_planner = MagicMock()
    mock_planner.create_plan.return_value = {
        "steps": [
            {
                "action": "delete_file",
                "description": "Delete test file",
                "parameters": {"file_path": "C:/temp/test.txt"},
                "requires_confirmation": True,
            }
        ],
        "response": "Deleting file.",
    }

    agent = VoxPilotAgent(
        tool_registry=reg,
        tool_executor=executor,
        confirmation_manager=cm,
        intent_recognizer=mock_intent,
        planner=mock_planner,
        context_manager=ctx_mgr,
    )
    set_agent(agent)

    # 1. Trigger initial execution -> pauses for confirmation
    exec_res = client.post("/agent/execute", json={"user_input": "delete C:/temp/test.txt", "execution_id": "exec_w12"})
    assert exec_res.status_code == 200
    token = exec_res.json()["pending_confirmation"]["token"]

    # Mock tool executor to simulate successful deletion once approved
    original_execute_step = executor.execute_step
    def mock_step(step, step_index=0, override_confirmation_token=None):
        if override_confirmation_token == token:
            return {
                "action": "delete_file",
                "step_index": step_index,
                "status": "completed",
                "success": True,
                "message": "File deleted safely.",
            }
        return original_execute_step(step, step_index=step_index, override_confirmation_token=override_confirmation_token)

    executor.execute_step = mock_step

    # 2. Approve via API
    approve_res = client.post(
        "/agent/confirmations/exec_w12/approve",
        json={"token": token},
    )
    assert approve_res.status_code == 200
    res_data = approve_res.json()
    assert res_data["execution_status"] == "completed"
    assert res_data["pending_confirmation"] is None


# ==============================================================================
# Workflow 13: SQLite Persistence Across Session Reopening
# ==============================================================================

@pytest.mark.asyncio
async def test_workflow_13_session_persistence_across_reopening(tmp_path, monkeypatch):
    """Multi-turn conversation records and execution logs persist across engine disposal."""
    db_file = tmp_path / "integration_persist.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    sync_url = f"sqlite:///{db_file}"

    monkeypatch.setenv("VOXPILOT_DB_URL", db_url)
    monkeypatch.setenv("VOXPILOT_DB_PATH", str(db_file))

    reset_db_connection()
    init_db_sync(sync_url)

    # Initial session
    repo1 = DatabaseRepository()
    await repo1.create_or_get_conversation("conv_persist_13", title="Session 13")
    await repo1.save_execution({
        "execution_id": "exec_13_turn_1",
        "conversation_id": "conv_persist_13",
        "user_input": "search papers",
        "intent": "file_management",
        "execution_status": "completed",
        "plan": [{"action": "search_files", "description": "Search"}],
        "response": "Found 3 papers.",
    })

    # Close and reset connection
    await close_db()
    reset_db_connection()

    # Reopen connection in fresh repository instance
    repo2 = DatabaseRepository()
    conv = await repo2.get_conversation("conv_persist_13")
    assert conv is not None
    assert conv.title == "Session 13"

    exec_record = await repo2.get_execution("exec_13_turn_1")
    assert exec_record is not None
    assert exec_record.user_input == "search papers"
    assert exec_record.response == "Found 3 papers."
    assert exec_record.execution_status == "completed"

    await close_db()
    reset_db_connection()
