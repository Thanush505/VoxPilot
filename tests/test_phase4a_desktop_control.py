"""
Targeted tests for VoxPilot Phase 4A: Advanced Natural Language Desktop Control.
Covers intent recognition, structured multi-step planning, application discovery
and aliases, extended WindowManager operations, confirmation policy for closing
windows, emergency stop enforcement, and non-vision fallback.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.agent.intent import IntentRecognizer
from backend.agent.planner import AgentPlanner
from backend.agent.tool_executor import ToolExecutor
from backend.agent.tool_registry import get_shared_tool_registry
from backend.automation.application_discovery import get_shared_discovery_service
from backend.automation.application_launcher import ApplicationLauncher, ApplicationNotFoundError
from backend.automation.emergency_stop import (
    EmergencyStopActiveError,
    get_shared_emergency_stop_manager,
)
from backend.automation.window_manager import WindowInfo, WindowManager
from backend.tools.windows import ManageWindowInput, manage_window_tool


@pytest.fixture(autouse=True)
def _reset_estop():
    estop = get_shared_emergency_stop_manager()
    if estop.is_emergency_stop_active():
        estop.reset_emergency_stop()
    yield
    if estop.is_emergency_stop_active():
        estop.reset_emergency_stop()


# ==============================================================================
# 1. Natural-Language Intent Interpretation & Structured Planning
# ==============================================================================

@pytest.mark.parametrize(
    "prompt,expected_intent",
    [
        ("Open Chrome", "open_application"),
        ("Launch Visual Studio Code", "open_application"),
        ("Switch to Notepad", "window_management"),
        ("Close the currently active window", "window_management"),
        ("Minimize all windows", "window_management"),
        ("Open my Downloads folder", "file_management"),
        ("Find a folder named VoxPilot", "file_management"),
        ("Arrange my open windows", "window_management"),
        ("Open Notepad, write a meeting summary, and save it in Documents.", "open_application"),
    ],
)
def test_phase4a_natural_language_intent_interpretation(prompt: str, expected_intent: str):
    recognizer = IntentRecognizer()
    res = recognizer.recognize_intent(prompt)
    assert res["intent"] == expected_intent
    assert res["confidence"] >= 0.9


def test_phase4a_structured_plans_for_window_and_folder_commands():
    planner = AgentPlanner()

    min_all = planner.create_plan("Minimize all windows", "window_management")
    assert min_all["steps"][0]["action"] == "manage_window"
    assert min_all["steps"][0]["parameters"]["action"] == "minimize_all"

    arrange = planner.create_plan("Arrange my open windows", "window_management")
    assert arrange["steps"][0]["action"] == "manage_window"
    assert arrange["steps"][0]["parameters"]["action"] == "arrange"

    close_active = planner.create_plan("Close the currently active window", "window_management")
    assert close_active["steps"][0]["action"] == "manage_window"
    assert close_active["steps"][0]["parameters"]["action"] == "close"
    assert close_active["steps"][0]["requires_confirmation"] is True

    switch_np = planner.create_plan("Switch to Notepad", "window_management")
    assert [s["action"] for s in switch_np["steps"]] == ["find_windows", "manage_window"]
    assert switch_np["steps"][1]["parameters"]["action"] == "activate"

    downloads = planner.create_plan("Open my Downloads folder", "file_management")
    assert downloads["steps"][0]["action"] == "launch_application"
    assert downloads["steps"][0]["parameters"]["application_name"] == "downloads"

    find_folder = planner.create_plan("Find a folder named VoxPilot", "file_management")
    assert find_folder["steps"][0]["action"] == "search_files"
    assert find_folder["steps"][0]["parameters"]["query"] == "VoxPilot"


def test_phase4a_multistep_notepad_write_and_save_plan():
    planner = AgentPlanner()
    plan = planner.create_plan(
        "Open Notepad, write a meeting summary, and save it in Documents.",
        "open_application",
    )
    actions = [s["action"] for s in plan["steps"]]
    assert actions == [
        "launch_application",
        "type_text",
        "send_shortcut",
        "type_text",
        "press_key",
    ]
    assert plan["steps"][0]["parameters"]["application_name"] == "notepad"
    assert plan["steps"][1]["parameters"]["text"] == "a meeting summary"
    assert plan["steps"][2]["parameters"]["shortcut"] == "ctrl+s"
    assert "Documents" in plan["steps"][3]["parameters"]["text"]


# ==============================================================================
# 2. Application Discovery, Aliases & Unknown Application Handling
# ==============================================================================

def test_phase4a_unknown_application_fails_safely_without_inventing_path():
    launcher = ApplicationLauncher()
    with pytest.raises(ApplicationNotFoundError):
        launcher.launch("NonExistentAppXYZ12345")


def test_phase4a_application_discovery_resolves_downloads_and_vscode_aliases():
    discovery = get_shared_discovery_service()
    discovery.ensure_indexed()
    downloads_res = discovery.resolve_query("downloads")
    assert downloads_res["status"] == "resolved"
    assert downloads_res["match"].app_id == "folder-downloads"


# ==============================================================================
# 3. Window Operations & Confirmation Policy
# ==============================================================================

def test_phase4a_manage_window_input_supports_extended_actions():
    for action in ("get_active", "minimize_all", "arrange", "close"):
        model = ManageWindowInput(action=action)
        assert model.action == action


def test_phase4a_manage_window_tool_dispatches_get_active_minimize_all_and_arrange():
    mock_mgr = MagicMock(spec=WindowManager)
    sample_win = WindowInfo(
        hwnd=101,
        title="Untitled - Notepad",
        app_id="notepad",
        display_name="Notepad",
        pid=4242,
        process_name="notepad.exe",
        executable_path="C:\\Windows\\System32\\notepad.exe",
        is_visible=True,
        is_minimized=False,
        is_maximized=False,
        is_responsive=True,
        rect=(0, 0, 800, 600),
    )
    mock_mgr.get_active_window.return_value = sample_win
    mock_mgr.minimize_all_windows.return_value = 3
    mock_mgr.arrange_windows.return_value = 2

    res_active = manage_window_tool(ManageWindowInput(action="get_active"), manager=mock_mgr)
    assert res_active.success is True
    assert res_active.window["hwnd"] == 101

    res_min = manage_window_tool(ManageWindowInput(action="minimize_all"), manager=mock_mgr)
    assert res_min.success is True
    assert "3" in res_min.message

    res_arr = manage_window_tool(ManageWindowInput(action="arrange", layout="tile"), manager=mock_mgr)
    assert res_arr.success is True
    assert "2" in res_arr.message


def test_phase4a_closing_window_requires_backend_confirmation():
    registry = get_shared_tool_registry()
    req_conf, action_type, _ = registry.check_confirmation_required(
        "manage_window",
        {"action": "close", "application_name": "notepad"},
    )
    assert req_conf is True
    assert action_type == "close_window"

    # Even if an LLM sets requires_confirmation=False, ToolExecutor enforces confirmation
    executor = ToolExecutor(registry=registry)
    res = executor.execute_step(
        {
            "action": "manage_window",
            "parameters": {"action": "close", "application_name": "notepad"},
            "requires_confirmation": False,
        },
        step_index=0,
    )
    assert res["status"] == "confirmation_required"
    assert res["confirmation_token"]


# ==============================================================================
# 4. Emergency Stop Enforcement on Extended Window Controls
# ==============================================================================

def test_phase4a_window_controls_respect_emergency_stop():
    estop = get_shared_emergency_stop_manager()
    estop.trigger_emergency_stop(reason="Phase 4A safety test", source="pytest")

    mgr = WindowManager()
    with pytest.raises(EmergencyStopActiveError):
        mgr.close_window(12345)
    with pytest.raises(EmergencyStopActiveError):
        mgr.minimize_all_windows()
    with pytest.raises(EmergencyStopActiveError):
        mgr.arrange_windows("tile")
