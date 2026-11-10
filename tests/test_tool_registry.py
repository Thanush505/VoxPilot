"""
Unit tests for the VoxPilot Tool Registry and parameter schema validation.
"""

import pytest
from pydantic import BaseModel, Field

from backend.agent.tool_registry import (
    ToolRegistry,
    ToolDefinition,
    UnknownToolError,
    ToolArgumentValidationError,
    get_shared_tool_registry,
)


@pytest.fixture
def registry() -> ToolRegistry:
    """Fixture providing a fresh ToolRegistry instance."""
    return ToolRegistry(register_defaults=True)


def test_tool_registry_has_built_in_tools(registry: ToolRegistry):
    """Verify standard automation tools across categories are registered."""
    expected_tools = [
        "launch_application",
        "find_windows",
        "manage_window",
        "type_text",
        "press_key",
        "send_shortcut",
        "move_mouse",
        "click_mouse",
        "scroll_mouse",
        "list_directory",
        "search_files",
        "get_file_metadata",
        "create_folder",
        "create_file",
        "rename_item",
        "copy_item",
        "move_item",
        "delete_file",
        "delete_folder",
        "capture_desktop",
        "capture_primary_monitor",
        "capture_monitor",
        "capture_window",
        "list_monitors",
        "start_browser",
        "navigate_url",
        "get_page_info",
        "get_page_text",
        "click_element",
        "enter_text",
        "select_dropdown",
        "scroll_page",
        "navigate_history",
        "capture_browser_screenshot",
        "download_file",
        "close_browser",
        "get_system_info",
    ]
    for tool_name in expected_tools:
        assert registry.has_tool(tool_name), f"Expected tool '{tool_name}' to be registered."


def test_tool_aliases(registry: ToolRegistry):
    """Verify tool aliases resolve to their primary implementations."""
    aliases = {
        "open_application": "launch_application",
        "activate_window": "manage_window",
        "press_keys": "press_key",
        "type_string": "type_text",
        "click": "click_mouse",
        "move": "move_mouse",
        "capture_screenshot": "capture_desktop",
        "take_screenshot": "capture_desktop",
        "open_browser": "start_browser",
        "search_web": "navigate_url",
        "browse": "navigate_url",
    }
    for alias, canonical in aliases.items():
        assert registry.has_tool(alias), f"Alias '{alias}' should be recognized."
        tool = registry.get_tool(alias)
        assert tool.name == canonical, f"Alias '{alias}' should resolve to '{canonical}', got '{tool.name}'."


def test_unknown_tool_rejection(registry: ToolRegistry):
    """Verify unknown tools are rejected with UnknownToolError."""
    assert not registry.has_tool("malicious_rm_rf")
    assert not registry.has_tool("random_fake_action")

    with pytest.raises(UnknownToolError) as exc_info:
        registry.get_tool("malicious_rm_rf")
    assert "malicious_rm_rf" in str(exc_info.value)

    with pytest.raises(UnknownToolError):
        registry.validate_parameters("malicious_rm_rf", {})


def test_parameter_validation_success(registry: ToolRegistry):
    """Verify valid arguments pass schema validation."""
    validated = registry.validate_parameters("launch_application", {"application_name": "notepad"})
    assert validated.application_name == "notepad"

    validated_key = registry.validate_parameters("press_key", {"hwnd": 12345, "key": "enter"})
    assert validated_key.key == "enter"
    assert validated_key.hwnd == 12345

    validated_sys = registry.validate_parameters("get_system_info", {})
    assert validated_sys is not None


def test_parameter_validation_missing_required(registry: ToolRegistry):
    """Verify missing required parameters raise ToolArgumentValidationError."""
    with pytest.raises(ToolArgumentValidationError) as exc_info:
        registry.validate_parameters("launch_application", {})
    assert "application_name" in str(exc_info.value)


def test_parameter_validation_rejects_unknown_fields(registry: ToolRegistry):
    """Verify unknown parameters are strictly rejected."""
    with pytest.raises(ToolArgumentValidationError) as exc_info:
        registry.validate_parameters(
            "launch_application",
            {"application_name": "notepad", "unsupported_extra_arg": "injected_value"},
        )
    assert "unsupported_extra_arg" in str(exc_info.value)


def test_parameter_validation_type_mismatch(registry: ToolRegistry):
    """Verify invalid parameter data types raise ToolArgumentValidationError."""
    with pytest.raises(ToolArgumentValidationError):
        registry.validate_parameters("click_mouse", {"x": "not_an_int", "y": 100})


def test_confirmation_requirement_detection(registry: ToolRegistry):
    """Verify destructive and flagged operations trigger confirmation requirement."""
    # 1. Delete file is inherently consequential
    req, act_type, _ = registry.check_confirmation_required("delete_file", {"file_path": "test.txt"})
    assert req is True
    assert act_type == "delete_file"

    # 2. Delete folder is inherently consequential
    req, act_type, _ = registry.check_confirmation_required("delete_folder", {"folder_path": "test_dir"})
    assert req is True
    assert act_type == "delete_folder"

    # 3. Create file with confirm_overwrite=True requires confirmation
    req, act_type, _ = registry.check_confirmation_required(
        "create_file", {"file_path": "test.txt", "content": "hi", "confirm_overwrite": True}
    )
    assert req is True
    assert "overwrite" in act_type

    # 4. Create file with confirm_overwrite=False does NOT require confirmation
    req, _, _ = registry.check_confirmation_required(
        "create_file", {"file_path": "test.txt", "content": "hi", "confirm_overwrite": False}
    )
    assert req is False

    # 5. Browser click with is_consequential=True requires confirmation
    req, act_type, _ = registry.check_confirmation_required(
        "click_element", {"selector": "#buy-now", "is_consequential": True}
    )
    assert req is True
    assert "consequential" in act_type

    # 6. Browser click with is_consequential=False does NOT require confirmation
    req, _, _ = registry.check_confirmation_required(
        "click_element", {"selector": "#menu", "is_consequential": False}
    )
    assert req is False

    # 7. Safe read-only operations do NOT require confirmation
    req, _, _ = registry.check_confirmation_required("get_system_info", {})
    assert req is False


def test_custom_tool_registration(registry: ToolRegistry):
    """Verify registering a custom tool and enforcing uniqueness."""
    class CustomParam(BaseModel):
        msg: str = Field(...)

    custom_tool = ToolDefinition(
        name="custom_echo",
        category="custom",
        description="Echo custom message",
        input_schema=CustomParam,
        func=lambda data: f"Echo: {data.msg}",
    )

    registry.register(custom_tool)
    assert registry.has_tool("custom_echo")

    # Duplicate registration without overwrite raises ValueError
    with pytest.raises(ValueError):
        registry.register(custom_tool, overwrite=False)

    # With overwrite succeeds
    registry.register(custom_tool, overwrite=True)
    assert registry.has_tool("custom_echo")


def test_shared_registry_singleton():
    """Verify shared singleton factory returns stable registry instance."""
    reg1 = get_shared_tool_registry()
    reg2 = get_shared_tool_registry()
    assert reg1 is reg2
    assert reg1.has_tool("launch_application")
