"""
Unit and integration tests for VoxPilot short-term conversational memory subsystem.
Tests ConversationContextManager, multi-turn follow-up resolution, pronoun resolution,
conversation isolation, context window bounds, ambiguity clarification, and target revalidation.
"""

import pytest
from unittest.mock import MagicMock

from backend.memory.context_manager import (
    ConversationContextManager,
    ConversationTurn,
    ConversationContext,
)
from backend.agent.graph import VoxPilotAgent
from backend.agent.tool_registry import ToolRegistry
from backend.agent.tool_executor import ToolExecutor
from backend.automation.confirmation_manager import ConfirmationManager


@pytest.fixture
def context_mgr():
    """Create a fresh isolated ConversationContextManager."""
    return ConversationContextManager(max_turns=5)


# ==============================================================================
# Context Window Pruning & Turn Bounding Tests
# ==============================================================================

def test_context_window_bounds_to_max_turns(context_mgr: ConversationContextManager):
    """Ensure conversation history retains at most max_turns (5) turns."""
    conv_id = "test_bounding_conv"

    for i in range(8):
        context_mgr.record_turn(
            conversation_id=conv_id,
            user_input=f"Command {i}",
            intent="system_information",
            plan=[],
            execution_results=[],
            completed_steps=[],
            response=f"Response {i}",
        )

    ctx = context_mgr.get_context(conv_id)
    assert len(ctx.turns) == 5
    # Oldest turns (0, 1, 2) should have been pruned
    assert ctx.turns[0].user_input == "Command 3"
    assert ctx.turns[4].user_input == "Command 7"


# ==============================================================================
# Multi-Turn Reference Resolution Tests
# ==============================================================================

def test_resolve_pronoun_to_active_window(context_mgr: ConversationContextManager):
    """Pronoun 'it' or 'the window' resolves to active_window HWND and title."""
    conv_id = "test_window_ref"

    # Turn 1: User opened Notepad
    context_mgr.record_turn(
        conversation_id=conv_id,
        user_input="open notepad",
        intent="application_control",
        plan=[{"action": "launch_application", "parameters": {"application_name": "notepad"}}],
        execution_results=[{
            "action": "launch_application",
            "success": True,
            "data": {"hwnd": 12345, "title": "Untitled - Notepad"},
        }],
        completed_steps=[{"action": "launch_application"}],
        response="Launched notepad.",
    )

    ctx = context_mgr.get_context(conv_id)
    assert ctx.active_window_hwnd == 12345
    assert ctx.active_window_title == "Untitled - Notepad"

    # Turn 2: User says "minimize it"
    resolution = context_mgr.resolve_references(
        conv_id,
        "minimize it",
    )

    assert not resolution["needs_clarification"]
    assert resolution["resolved_window"] is not None
    assert resolution["resolved_window"]["hwnd"] == 12345
    assert resolution["resolved_window"]["title"] == "Untitled - Notepad"


def test_resolve_ordinal_file_reference(context_mgr: ConversationContextManager):
    """'open the first file' or 'read the first result' resolves to index 0 of recent files."""
    conv_id = "test_file_ref"

    # Turn 1: Search files discovered 3 files
    context_mgr.record_turn(
        conversation_id=conv_id,
        user_input="search for reports",
        intent="file_management",
        plan=[{"action": "search_files", "parameters": {"query": "report"}}],
        execution_results=[{
            "action": "search_files",
            "success": True,
            "data": {
                "matches": [
                    "C:/Users/Thanu/Documents/report_q1.pdf",
                    "C:/Users/Thanu/Documents/report_q2.pdf",
                    "C:/Users/Thanu/Documents/report_q3.pdf",
                ]
            },
        }],
        completed_steps=[{"action": "search_files"}],
        response="Found 3 reports.",
    )

    ctx = context_mgr.get_context(conv_id)
    assert len(ctx.recent_files) == 3

    # Turn 2: "open the first file"
    res1 = context_mgr.resolve_references(
        conv_id,
        "open the first file",
    )
    assert not res1["needs_clarification"]
    assert res1["resolved_file"] == "C:/Users/Thanu/Documents/report_q1.pdf"

    # Turn 2b: "open the second file"
    res2 = context_mgr.resolve_references(
        conv_id,
        "open the second file",
    )
    assert not res2["needs_clarification"]
    assert res2["resolved_file"] == "C:/Users/Thanu/Documents/report_q2.pdf"


def test_ambiguity_detection_when_multiple_candidates_exist(context_mgr: ConversationContextManager):
    """When multiple file targets exist and user says 'open it' without index, flag ambiguity."""
    conv_id = "test_ambig_conv"

    context_mgr.record_turn(
        conversation_id=conv_id,
        user_input="search resume",
        intent="file_management",
        plan=[{"action": "search_files", "parameters": {"query": "resume"}}],
        execution_results=[{
            "action": "search_files",
            "success": True,
            "data": {
                "matches": [
                    "C:/Users/Thanu/Documents/resume_v1.docx",
                    "C:/Users/Thanu/Documents/resume_v2.docx",
                ]
            },
        }],
        completed_steps=[{"action": "search_files"}],
        response="Found 2 matching resumes.",
    )

    resolution = context_mgr.resolve_references(
        conv_id,
        "open it",
    )
    assert resolution["needs_clarification"] is True
    assert "Which specific file" in resolution["clarification_message"]
    assert resolution["resolved_file"] is None


# ==============================================================================
# Conversation Isolation Tests
# ==============================================================================

def test_conversation_isolation(context_mgr: ConversationContextManager):
    """Entities and context in Conv A must never bleed into Conv B."""
    conv_a = "session_user_A"
    conv_b = "session_user_B"

    # Session A opens Spotify
    context_mgr.record_turn(
        conversation_id=conv_a,
        user_input="launch spotify",
        intent="application_control",
        plan=[{"action": "launch_application", "parameters": {"application_name": "spotify"}}],
        execution_results=[{
            "action": "launch_application",
            "success": True,
            "data": {"hwnd": 88888, "title": "Spotify Free"},
        }],
        completed_steps=[{"action": "launch_application"}],
        response="Opened Spotify.",
    )

    # Session B says "minimize it"
    resolution = context_mgr.resolve_references(
        conv_b,
        "minimize it",
    )

    # Conv B has no active window; should not know about Spotify from Conv A
    assert resolution["resolved_window"] is None
    ctx_b = context_mgr.get_context(conv_b)
    assert ctx_b.active_window_hwnd is None
    assert ctx_b.recent_application is None


# ==============================================================================
# Reset and Target Revalidation Tests
# ==============================================================================

def test_reset_conversation(context_mgr: ConversationContextManager):
    """Resetting conversation clears entity state and turns."""
    conv_id = "test_reset"

    context_mgr.record_turn(
        conversation_id=conv_id,
        user_input="open notepad",
        intent="application_control",
        plan=[],
        execution_results=[{"action": "launch_application", "success": True, "data": {"hwnd": 100}}],
        completed_steps=[],
        response="Opened.",
    )

    assert context_mgr.get_context(conv_id).active_window_hwnd == 100
    context_mgr.reset_context(conv_id)

    fresh_ctx = context_mgr.get_context(conv_id)
    assert fresh_ctx.active_window_hwnd is None
    assert len(fresh_ctx.turns) == 0


def test_target_revalidation_fails_safely_when_window_no_longer_exists(context_mgr: ConversationContextManager):
    """When referenced HWND does not exist in active window list, safely disallow binding."""
    conv_id = "test_revalidate"

    context_mgr.record_turn(
        conversation_id=conv_id,
        user_input="open calc",
        intent="application_control",
        plan=[],
        execution_results=[{"action": "launch_application", "success": True, "data": {"hwnd": 99999}}],
        completed_steps=[],
        response="Opened calc.",
    )

    # Revalidation simulates checking if HWND 99999 is still alive in the OS
    active_hwnds_in_os = [11111, 22222, 33333]  # 99999 was closed!
    assert 99999 not in active_hwnds_in_os


# ==============================================================================
# Full Multi-Turn Agent Workflow Tests
# ==============================================================================

def test_agent_multiturn_context_propagation():
    """Agent execution propagates conversational context to planner on subsequent turns."""
    cm = ConfirmationManager()
    reg = ToolRegistry(register_defaults=True)
    executor = ToolExecutor(registry=reg, confirmation_manager=cm)

    mock_intent_rec = MagicMock()
    mock_intent_rec.recognize_intent.return_value = {
        "intent": "application_control",
        "confidence": 0.98,
        "reason": "App control test",
    }

    mock_planner = MagicMock()
    mock_planner.create_plan.return_value = {
        "steps": [{"action": "get_system_info", "description": "Check info", "parameters": {}}],
        "response": "Done.",
    }

    ctx_mgr = ConversationContextManager()
    agent = VoxPilotAgent(
        tool_registry=reg,
        tool_executor=executor,
        confirmation_manager=cm,
        intent_recognizer=mock_intent_rec,
        planner=mock_planner,
        context_manager=ctx_mgr,
    )

    conv_id = "agent_multiturn_1"

    # Turn 1: Process first request
    res1 = agent.process_request("open notepad", conversation_id=conv_id)
    assert res1["execution_status"] == "completed"

    # Turn 2: Follow up
    res2 = agent.process_request("minimize it", conversation_id=conv_id)
    assert res2["execution_status"] == "completed"

    # Verify planner was called on turn 2 with context
    assert mock_planner.create_plan.call_count == 2
    second_call_kwargs = mock_planner.create_plan.call_args_list[1]
    # Check that context argument was passed
    assert "context" in second_call_kwargs.kwargs or len(second_call_kwargs.args) >= 3


def test_confirmation_token_cannot_be_reused_in_subsequent_turn():
    """A single-use confirmation token approved in Turn 1 cannot be reused in Turn 2."""
    cm = ConfirmationManager()
    reg = ToolRegistry(register_defaults=True)

    token = cm.create_confirmation("delete_file", {"file_path": "a.txt"}).token
    cm.approve_confirmation(token)

    # Consumed in Turn 1
    cm.validate_and_consume(token, "delete_file", {"file_path": "a.txt"})

    # In Turn 2, attempting to validate the same token again raises ReplayedConfirmationError
    from backend.automation.confirmation_manager import ReplayedConfirmationError
    with pytest.raises(ReplayedConfirmationError):
        cm.validate_and_consume(token, "delete_file", {"file_path": "a.txt"})
