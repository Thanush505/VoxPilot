"""Tests for Critical Agent Planning Bug Fixes (Bug A & Bug B).

Bug A: Incorrect tool parameter generation (application vs application_name).
Bug B: Keyboard typing incorrectly interpreted as file saving.
"""

import pytest
from unittest.mock import MagicMock, patch

from backend.agent.intent import IntentRecognizer
from backend.agent.planner import AgentPlanner
from backend.agent.tool_registry import (
    ToolRegistry,
    ToolArgumentValidationError,
    UnknownToolError,
)
from backend.agent.tool_executor import ToolExecutor
from backend.tools.keyboard import (
    TypeTextInput,
    PressKeyInput,
    SendShortcutInput,
    _resolve_target_hwnd,
    type_text_tool,
)
from backend.automation.window_manager import WindowInfo
from backend.agent.graph import VoxPilotAgent
from backend.automation.confirmation_manager import ConfirmationManager
from backend.memory.context_manager import ConversationContextManager
from backend.automation.emergency_stop import (
    EmergencyStopManager,
    set_shared_emergency_stop_manager,
    get_shared_emergency_stop_manager,
)


# ==============================================================================
# Bug A: Application Launching & Parameter Canonicalization Tests
# ==============================================================================

class TestBugAParameterGenerationAndNormalization:
    """Tests verifying Bug A root cause and resolution."""

    @pytest.fixture
    def registry(self):
        return ToolRegistry()

    @pytest.fixture
    def planner(self):
        return AgentPlanner()

    @pytest.fixture
    def intent_recognizer(self):
        return IntentRecognizer()

    def test_open_notepad_intent_and_canonical_plan(self, intent_recognizer, planner):
        """'Open Notepad' must be recognized as open_application with application_name parameter."""
        intent_res = intent_recognizer.recognize_intent("Open Notepad")
        assert intent_res["intent"] == "open_application"

        plan = planner.create_plan("Open Notepad", intent=intent_res["intent"])
        assert len(plan["steps"]) >= 1
        launch_step = plan["steps"][0]
        assert launch_step["action"] == "launch_application"
        assert "application_name" in launch_step["parameters"]
        assert launch_step["parameters"]["application_name"].lower() == "notepad"
        assert "application" not in launch_step["parameters"]

    def test_launch_calculator_canonical_plan(self, intent_recognizer, planner):
        """'Launch Calculator' must generate application_name='calculator'."""
        intent_res = intent_recognizer.recognize_intent("Launch Calculator")
        assert intent_res["intent"] == "open_application"

        plan = planner.create_plan("Launch Calculator", intent=intent_res["intent"])
        launch_step = plan["steps"][0]
        assert launch_step["action"] == "launch_application"
        assert launch_step["parameters"]["application_name"].lower() == "calculator"
        assert "application" not in launch_step["parameters"]

    def test_start_vscode_canonical_plan(self, intent_recognizer, planner):
        """'Start VS Code' must generate application_name='vscode'."""
        intent_res = intent_recognizer.recognize_intent("Start VS Code")
        assert intent_res["intent"] == "open_application"

        plan = planner.create_plan("Start VS Code", intent=intent_res["intent"])
        launch_step = plan["steps"][0]
        assert launch_step["action"] == "launch_application"
        assert launch_step["parameters"]["application_name"].lower() == "vscode"

    def test_alias_normalization_maps_to_canonical(self, registry):
        """Legacy aliases like 'application', 'app_name', 'app' normalize to 'application_name'."""
        # 1. launch_application
        norm1 = registry.normalize_parameters("launch_application", {"application": "notepad"})
        assert norm1 == {"application_name": "notepad"}

        norm2 = registry.normalize_parameters("launch_application", {"app_name": "notepad"})
        assert norm2 == {"application_name": "notepad"}

        norm3 = registry.normalize_parameters("launch_application", {"app": "notepad"})
        assert norm3 == {"application_name": "notepad"}

        # 2. find_windows
        norm_fw = registry.normalize_parameters("find_windows", {"application": "notepad", "title": "test"})
        assert norm_fw == {"application_name": "notepad", "title_pattern": "test"}

        # 3. manage_window
        norm_mw = registry.normalize_parameters("manage_window", {"app": "notepad", "action": "activate"})
        assert norm_mw == {"application_name": "notepad", "action": "activate"}

        # 4. type_text
        norm_tt = registry.normalize_parameters("type_text", {"string": "hello", "application": "notepad"})
        assert norm_tt == {"text": "hello", "application_name": "notepad"}

        # 5. search_files
        norm_sf = registry.normalize_parameters("search_files", {"directory": "C:/data", "filename": "test.txt"})
        assert norm_sf == {"directory_path": "C:/data", "query": "test.txt"}

    def test_conflicting_alias_and_canonical_rejected(self, registry):
        """Conflicting parameter definitions must raise ToolArgumentValidationError."""
        with pytest.raises(ToolArgumentValidationError, match="Conflicting/ambiguous parameters"):
            registry.normalize_parameters(
                "launch_application",
                {"application": "notepad", "application_name": "calculator"}
            )

    def test_tool_executor_accepts_and_normalizes_legacy_parameters(self, registry):
        """ToolExecutor executes tools successfully even if legacy aliases are supplied."""
        executor = ToolExecutor(registry=registry)
        with patch("backend.automation.application_launcher.ApplicationLauncher.launch") as mock_launch:
            mock_launch.return_value = {
                "success": True,
                "app_id": "notepad",
                "display_name": "Notepad",
                "pid": 1234,
                "status": "launched",
                "message": "Application launched successfully.",
                "timestamp": "2026-11-11T12:00:00Z",
            }
            result = executor.execute_step(
                step={
                    "action": "launch_application",
                    "parameters": {"application": "notepad"},
                },
                step_index=0,
            )
            assert result["success"] is True
            # Verified that launch was called with canonical application_name
            mock_launch.assert_called_once_with("notepad")

    def test_pseudo_actions_preserve_parameters(self, registry):
        """Pseudo-actions 'respond' and 'ask_clarification' bypass tool normalization cleanly."""
        norm_resp = registry.normalize_parameters("respond", {"message": "Hello"})
        assert norm_resp == {"message": "Hello"}

        norm_ask = registry.normalize_parameters("ask_clarification", {"question": "Which one?"})
        assert norm_ask == {"question": "Which one?"}


# ==============================================================================
# Bug B: Keyboard Automation vs File Saving Tests
# ==============================================================================

class TestBugBKeyboardVsFileSaving:
    """Tests verifying Bug B root cause and resolution."""

    @pytest.fixture
    def intent_recognizer(self):
        return IntentRecognizer()

    @pytest.fixture
    def planner(self):
        return AgentPlanner()

    @pytest.mark.parametrize("query,expected_intents", [
        ("Open Notepad", ["open_application"]),
        ("Type Hello from VoxPilot! into Notepad.", ["keyboard_automation"]),
        ("Write Testing 123 in Notepad.", ["keyboard_automation"]),
        ("Type this message into the active window.", ["keyboard_automation"]),
        ("Open Notepad and type Hello.", ["open_application", "keyboard_automation"]),
        ("Save the document", ["file_management"]),
        ("Create a text file named notes.txt", ["file_management"]),
        ("Search for notes.txt", ["file_management"]),
        ("Open my Downloads folder", ["file_management"]),
        ("Open Calculator", ["open_application"]),
        ("Minimize Notepad", ["window_management"]),
        ("Close Notepad", ["window_management"]),
    ])
    def test_intent_classification_precision(self, intent_recognizer, query, expected_intents):
        """Verify strict classification disambiguation between typing and file management."""
        res = intent_recognizer.recognize_intent(query)
        assert res["intent"] in expected_intents, f"Query '{query}' was classified as '{res['intent']}' instead of '{expected_intents}'"

    def test_type_hello_into_notepad_plan_structure(self, planner):
        """'Type Hello from VoxPilot! into Notepad.' must produce window discovery, activation, and typing."""
        plan = planner.create_plan(
            "Type Hello from VoxPilot! into Notepad.",
            intent="keyboard_automation"
        )
        steps = plan["steps"]
        assert len(steps) >= 3

        # Step 1: find_windows
        assert steps[0]["action"] == "find_windows"
        assert steps[0]["parameters"]["application_name"] == "notepad"

        # Step 2: manage_window activate
        assert steps[1]["action"] == "manage_window"
        assert steps[1]["parameters"]["action"] == "activate"
        assert steps[1]["parameters"]["application_name"] == "notepad"

        # Step 3: type_text
        assert steps[2]["action"] == "type_text"
        assert steps[2]["parameters"]["text"] == "Hello from VoxPilot!"
        assert steps[2]["parameters"]["application_name"] == "notepad"

        # Critical Bug B check: Ensure it did NOT ask user to save a file or ask for file path
        for step in steps:
            assert step["action"] not in ("create_file", "delete_file")
            assert "ask user for the file path" not in step.get("description", "").lower()

    def test_open_notepad_and_type_compound_plan(self, planner):
        """'Open Notepad and type Hello' must include launch_application followed by typing."""
        plan = planner.create_plan(
            "Open Notepad and type Hello",
            intent="keyboard_automation"
        )
        steps = plan["steps"]
        actions = [s["action"] for s in steps]
        assert "launch_application" in actions
        assert "type_text" in actions

    def test_type_into_active_window_plan(self, planner):
        """'Type Hello into the active window' targets active window directly."""
        plan = planner.create_plan(
            "Type Hello into the active window",
            intent="keyboard_automation"
        )
        steps = plan["steps"]
        assert len(steps) == 1
        assert steps[0]["action"] == "type_text"
        assert steps[0]["parameters"]["text"] == "Hello"


# ==============================================================================
# Keyboard Tools Target Resolution & Input Validation Tests
# ==============================================================================

class TestKeyboardToolsTargetResolution:
    """Tests for hwnd and application_name handling in keyboard tools."""

    def test_type_text_input_schema_accepts_optional_hwnd_and_app_name(self):
        """TypeTextInput accepts hwnd=None and valid application_name."""
        inp = TypeTextInput(text="Hello", application_name="notepad")
        assert inp.text == "Hello"
        assert inp.hwnd is None
        assert inp.application_name == "notepad"

    def test_type_text_input_rejects_negative_hwnd(self):
        """TypeTextInput strictly rejects negative HWND values."""
        with pytest.raises(ValueError):
            TypeTextInput(hwnd=-1, text="Hello")

    def test_press_key_and_send_shortcut_schemas(self):
        """PressKeyInput and SendShortcutInput accept optional hwnd and application_name."""
        pk = PressKeyInput(key="enter", application_name="calculator")
        assert pk.key == "enter"
        assert pk.application_name == "calculator"
        assert pk.hwnd is None

        ss = SendShortcutInput(shortcut="ctrl+s", hwnd=12345)
        assert ss.shortcut == "ctrl+s"
        assert ss.hwnd == 12345
        assert ss.application_name is None

    def test_resolve_target_hwnd_unapproved_app_rejected(self):
        """Unapproved application name must return failure with error message."""
        target_hwnd, err = _resolve_target_hwnd(hwnd=None, application_name="malicious_app.exe")
        assert target_hwnd == 0
        assert err is not None
        assert "malicious_app.exe" in err

        # Also verify through type_text_tool
        result = type_text_tool(TypeTextInput(text="test", application_name="malicious_app.exe"))
        assert result.success is False
        assert result.status == "target_not_found"
        assert "malicious_app.exe" in result.message

    def test_resolve_target_hwnd_fallback_to_active_window(self):
        """When neither hwnd nor app_name is provided, resolves to active foreground window."""
        with patch("backend.tools.keyboard.win32gui") as mock_win32gui:
            mock_win32gui.GetForegroundWindow.return_value = 54321
            target_hwnd, err = _resolve_target_hwnd(hwnd=None, application_name=None)
            assert target_hwnd == 54321
            assert err is None

    def test_resolve_target_hwnd_app_name_discovery(self):
        """When application_name is provided, resolves from matching window list."""
        with patch("backend.tools.keyboard.WindowManager") as mock_wm_cls:
            mock_wm = mock_wm_cls.return_value
            mock_wm.find_windows.return_value = [
                WindowInfo(
                    hwnd=98765,
                    title="Untitled - Notepad",
                    app_id="notepad",
                    display_name="Notepad",
                    pid=4321,
                    process_name="notepad.exe",
                    executable_path="C:\\Windows\\notepad.exe",
                    is_visible=True,
                    is_minimized=False,
                    is_maximized=False,
                    is_responsive=True,
                    rect=(0, 0, 800, 600),
                )
            ]
            target_hwnd, err = _resolve_target_hwnd(hwnd=None, application_name="notepad")
            assert target_hwnd == 98765
            assert err is None


# ==============================================================================
# Dynamic Replanning & Integration Verification
# ==============================================================================

class TestDynamicReplanningForTyping:
    """Tests for dynamic replanning when application windows need to be launched."""

    def test_replan_prepends_launch_when_no_windows_found(self):
        """If find_windows finds 0 windows for notepad, dynamic replanning prepends launch_application."""
        planner = AgentPlanner()
        completed_steps = [
            {"action": "find_windows", "parameters": {"application_name": "notepad"}}
        ]
        completed_results = [
            {
                "action": "find_windows",
                "success": True,
                "data": {"count": 0, "windows": []},
            }
        ]
        remaining_plan = [
            {"action": "type_text", "parameters": {"text": "Hello", "application_name": "notepad"}}
        ]
        replanned_steps, reason = planner.replan_from_results(
            original_input="Type Hello into Notepad",
            completed_steps=completed_steps,
            completed_results=completed_results,
            remaining_plan=remaining_plan,
        )
        assert len(replanned_steps) > 0
        actions = [s["action"] for s in replanned_steps]
        assert actions[0] == "launch_application"
        assert replanned_steps[0]["parameters"]["application_name"] == "notepad"


# ==============================================================================
# Full Agent Pipeline End-to-End Integration
# ==============================================================================

class TestFullAgentPipelineIntegration:
    """Tests executing the entire agent pipeline from user request to response."""

    @pytest.fixture
    def agent(self):
        reg = ToolRegistry()
        executor = ToolExecutor(registry=reg)
        cm = ConfirmationManager()
        ir = IntentRecognizer()
        planner = AgentPlanner()
        ctx_mgr = ConversationContextManager()
        # Clean emergency stop manager
        manager = EmergencyStopManager()
        set_shared_emergency_stop_manager(manager)
        if manager.is_emergency_stop_active():
            manager.reset_emergency_stop()
        return VoxPilotAgent(
            tool_registry=reg,
            tool_executor=executor,
            confirmation_manager=cm,
            intent_recognizer=ir,
            planner=planner,
            context_manager=ctx_mgr,
        )

    def test_pipeline_open_notepad_success(self, agent):
        """Pipeline executes 'Open Notepad' through intent, plan, validation, tool execution, and response."""
        with patch("backend.automation.application_launcher.ApplicationLauncher.launch") as mock_launch:
            mock_launch.return_value = {
                "success": True,
                "app_id": "notepad",
                "display_name": "Notepad",
                "pid": 5566,
                "status": "launched",
                "message": "Notepad launched successfully.",
                "timestamp": "2026-11-11T12:00:00Z",
            }
            state = agent.process_request("Open Notepad", conversation_id="conv_pipe_1")
            assert state["execution_status"] == "completed"
            assert len(state["completed_steps"]) == 1
            step = state["completed_steps"][0]
            assert step["action"] == "launch_application"
            assert step["parameters"]["application_name"] == "notepad"
            assert state["response"] != ""

    def test_pipeline_type_into_notepad_success(self, agent):
        """Pipeline executes 'Type Hello from VoxPilot! into Notepad.' through intent, plan, validation, and typing."""
        fake_window = WindowInfo(
            hwnd=7788,
            title="Untitled - Notepad",
            app_id="notepad",
            display_name="Notepad",
            pid=5566,
            process_name="notepad.exe",
            executable_path="C:\\Windows\\notepad.exe",
            is_visible=True,
            is_minimized=False,
            is_maximized=False,
            is_responsive=True,
            rect=(0, 0, 800, 600),
        )
        with patch("backend.automation.window_manager.WindowManager.find_windows", return_value=[fake_window]), \
             patch("backend.automation.window_manager.WindowManager.activate_window", return_value=True), \
             patch("backend.automation.window_manager.WindowManager.get_window_info", return_value=fake_window), \
             patch("backend.automation.keyboard_controller.KeyboardController.type_text", return_value={"status": "success", "characters_typed": 24, "target_hwnd": 7788}), \
             patch("backend.automation.keyboard_controller.KeyboardController._prepare_target_window", return_value=None):

            state = agent.process_request(
                "Type Hello from VoxPilot! into Notepad.",
                conversation_id="conv_pipe_2"
            )
            assert state["execution_status"] == "completed"
            assert len(state["completed_steps"]) == 3
            actions = [s["action"] for s in state["completed_steps"]]
            assert actions == ["find_windows", "manage_window", "type_text"]
            type_step = state["completed_steps"][2]
            assert type_step["parameters"]["text"] == "Hello from VoxPilot!"
            assert type_step["parameters"]["application_name"] == "notepad"
            assert state["response"] != ""

    def test_pipeline_emergency_stop_halts_execution(self, agent):
        """Pipeline immediately cancels execution when emergency stop is active."""
        em_stop = get_shared_emergency_stop_manager()
        em_stop.trigger_emergency_stop(reason="Test emergency stop", source="api")
        try:
            state = agent.process_request("Open Notepad", conversation_id="conv_pipe_3")
            assert state["execution_status"] == "cancelled"
            assert "emergency stop is active" in state["error"].lower()
        finally:
            if em_stop.is_emergency_stop_active():
                em_stop.reset_emergency_stop()

