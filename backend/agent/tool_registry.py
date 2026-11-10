"""
VoxPilot - Secure Tool Registry and Dispatcher
Provides strict mapping of allowed action names to approved Pydantic schemas
and executable tool functions, with schema validation, parameter checking,
and confirmation requirement determination.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Dict, Any, List, Optional, Type, Set, Tuple
from pydantic import BaseModel, ValidationError

# Import approved executable tool functions and their Pydantic schemas
from backend.tools.applications import (
    LaunchApplicationInput,
    launch_application_tool,
)
from backend.tools.windows import (
    FindWindowsInput,
    ManageWindowInput,
    find_windows_tool,
    manage_window_tool,
)
from backend.tools.keyboard import (
    TypeTextInput,
    PressKeyInput,
    SendShortcutInput,
    type_text_tool,
    press_key_tool,
    send_shortcut_tool,
)
from backend.tools.mouse import (
    MoveMouseInput,
    ClickMouseInput,
    ScrollMouseInput,
    move_mouse_tool,
    click_mouse_tool,
    scroll_mouse_tool,
)
from backend.tools.files import (
    ListDirectoryInput,
    SearchFilesInput,
    GetFileMetadataInput,
    CreateFolderInput,
    CreateFileInput,
    RenameItemInput,
    CopyItemInput,
    MoveItemInput,
    DeleteFileInput,
    DeleteFolderInput,
    list_directory_tool,
    search_files_tool,
    get_file_metadata_tool,
    create_folder_tool,
    create_file_tool,
    rename_item_tool,
    copy_item_tool,
    move_item_tool,
    delete_file_tool,
    delete_folder_tool,
)
from backend.tools.screenshots import (
    CaptureDesktopInput,
    CaptureMonitorInput,
    CaptureWindowInput,
    capture_desktop_tool,
    capture_primary_monitor_tool,
    capture_monitor_tool,
    capture_window_tool,
    list_monitors_tool,
)
from backend.tools.browser import (
    StartBrowserInput,
    NavigateUrlInput,
    GetPageInfoInput,
    GetPageTextInput,
    ClickElementInput,
    EnterTextInput,
    SelectDropdownInput,
    ScrollPageInput,
    NavigateHistoryInput,
    CaptureBrowserScreenshotInput,
    CloseBrowserInput,
    DownloadFileInput,
    start_browser_tool,
    navigate_url_tool,
    get_page_info_tool,
    get_page_text_tool,
    click_element_tool,
    enter_text_tool,
    select_dropdown_tool,
    scroll_page_tool,
    navigate_history_tool,
    capture_browser_screenshot_tool,
    close_browser_tool,
    download_file_tool,
)
from backend.tools.system import (
    EmptyInput,
    get_system_info_tool,
)

logger = logging.getLogger(__name__)


# ==============================================================================
# Exceptions
# ==============================================================================

class ToolRegistryError(Exception):
    """Base exception for all tool registry errors."""
    pass


class UnknownToolError(ToolRegistryError):
    """Raised when an unknown or unapproved action name is requested."""
    pass


class ToolArgumentValidationError(ToolRegistryError):
    """Raised when action arguments fail Pydantic schema validation or contain unsupported fields."""
    pass


# ==============================================================================
# Data Model
# ==============================================================================

@dataclass(frozen=True)
class ToolDefinition:
    """Metadata describing a registered, approved automation tool."""
    name: str
    category: str
    description: str
    func: Callable[..., Any]
    input_schema: Type[BaseModel]
    inherent_confirmation: bool = False
    confirmation_action_type: Optional[str] = None


# ==============================================================================
# Tool Registry Implementation
# ==============================================================================

class ToolRegistry:
    """Central registry of approved desktop, browser, filesystem, and system tools."""

    def __init__(self, register_defaults: bool = True):
        self._tools: Dict[str, ToolDefinition] = {}
        self._aliases: Dict[str, str] = {}
        if register_defaults:
            self._register_default_tools()

    def register(self, definition: ToolDefinition, overwrite: bool = True) -> None:
        """Register an approved tool definition."""
        if not overwrite and definition.name in self._tools:
            raise ValueError(f"Tool '{definition.name}' is already registered and overwrite is False.")
        self._tools[definition.name] = definition
        logger.debug(f"Registered tool '{definition.name}' in category '{definition.category}'")

    def register_alias(self, alias: str, canonical_name: str) -> None:
        """Register an action alias mapping to a canonical tool name."""
        if canonical_name not in self._tools:
            raise UnknownToolError(
                f"Cannot create alias '{alias}': target tool '{canonical_name}' is not registered."
            )
        self._aliases[alias] = canonical_name

    def resolve_name(self, name: str) -> str:
        """Resolve an action name or alias to its canonical tool name."""
        if not name or not isinstance(name, str):
            raise UnknownToolError("Action name must be a non-empty string.")
        clean = name.strip()
        if clean in self._tools:
            return clean
        if clean in self._aliases:
            return self._aliases[clean]
        raise UnknownToolError(f"Action '{name}' is not a registered or approved VoxPilot tool.")

    def has_tool(self, name: str) -> bool:
        """Check whether an action name or alias is registered."""
        try:
            self.resolve_name(name)
            return True
        except UnknownToolError:
            return False

    def get_tool(self, name: str) -> ToolDefinition:
        """Retrieve tool definition for an action name or alias."""
        canonical = self.resolve_name(name)
        return self._tools[canonical]

    def validate_parameters(self, name: str, parameters: Optional[Dict[str, Any]] = None) -> BaseModel:
        """Validate argument dictionary against tool's Pydantic schema.

        Rejects unsupported extra parameters and malformed types.
        """
        tool_def = self.get_tool(name)
        params = parameters if parameters is not None else {}

        if not isinstance(params, dict):
            raise ToolArgumentValidationError(
                f"Parameters for action '{name}' must be a dictionary, received {type(params).__name__}."
            )

        # Enforce that no unknown or unsupported parameters were injected
        schema_fields: Set[str] = set(tool_def.input_schema.model_fields.keys())
        unknown_fields = set(params.keys()) - schema_fields
        if unknown_fields:
            raise ToolArgumentValidationError(
                f"Action '{name}' received unknown/unsupported parameter(s): {sorted(list(unknown_fields))}. "
                f"Allowed parameters: {sorted(list(schema_fields))}."
            )

        try:
            return tool_def.input_schema(**params)
        except ValidationError as err:
            err_msgs = [f"{e['loc']}: {e['msg']}" for e in err.errors()]
            raise ToolArgumentValidationError(
                f"Parameter validation failed for action '{name}': {'; '.join(err_msgs)}"
            ) from err

    def check_confirmation_required(
        self,
        name: str,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """Determine whether an action requires human confirmation based on trusted backend policy.

        Returns:
            Tuple of (requires_confirmation, action_type_string, parameters_for_hash).
        """
        canonical = self.resolve_name(name)
        tool_def = self._tools[canonical]
        raw_params = parameters or {}
        # Clean parameters for hash to exclude confirmation token metadata
        params = {k: v for k, v in raw_params.items() if k not in ("confirmation_token", "confirmed")}

        # 1. Inherently destructive actions (never trust LLM flags)
        if tool_def.inherent_confirmation:
            action_type = tool_def.confirmation_action_type or canonical
            return True, action_type, params

        # 2. File overwrite actions
        if canonical in ("create_file", "copy_file", "move_file", "rename_item"):
            if raw_params.get("confirm_overwrite") is True:
                return True, f"{canonical}_overwrite", params

        # 3. Consequential browser actions
        if canonical == "click_element" and raw_params.get("is_consequential"):
            return True, "browser_consequential_click", {"selector": raw_params.get("selector")}

        # 4. Browser download overwrite
        if canonical == "download_file" and raw_params.get("confirm_overwrite"):
            return True, "browser_download_overwrite", params

        return False, "", {}

    def list_tools(self, category: Optional[str] = None) -> List[str]:
        """List registered canonical tool names, optionally filtered by category."""
        if category is None:
            return sorted(list(self._tools.keys()))
        return sorted([k for k, v in self._tools.items() if v.category == category])

    def list_tool_definitions(self) -> List[ToolDefinition]:
        """Return list of all registered ToolDefinition instances."""
        return list(self._tools.values())

    # --------------------------------------------------------------------------
    # Default Tool Registration
    # --------------------------------------------------------------------------

    def _register_default_tools(self) -> None:
        """Register all pre-approved VoxPilot automation tools across domains."""

        # 1. Applications & Windows
        self.register(ToolDefinition(
            name="launch_application",
            category="applications",
            description="Launch an approved Windows application (notepad, calculator, chrome, vscode).",
            func=launch_application_tool,
            input_schema=LaunchApplicationInput,
        ))
        self.register_alias("open_application", "launch_application")

        self.register(ToolDefinition(
            name="find_windows",
            category="windows",
            description="Find top-level visible windows belonging to approved applications.",
            func=find_windows_tool,
            input_schema=FindWindowsInput,
        ))

        self.register(ToolDefinition(
            name="manage_window",
            category="windows",
            description="Manage an approved window (activate, minimize, maximize, restore).",
            func=manage_window_tool,
            input_schema=ManageWindowInput,
        ))
        self.register_alias("activate_window", "manage_window")

        # 2. Keyboard & Mouse
        self.register(ToolDefinition(
            name="type_text",
            category="keyboard",
            description="Type Unicode text into the active foreground window using Win32 SendInput.",
            func=type_text_tool,
            input_schema=TypeTextInput,
        ))
        self.register_alias("type_string", "type_text")

        self.register(ToolDefinition(
            name="press_key",
            category="keyboard",
            description="Press an approved keyboard key (enter, tab, space, escape, arrows, etc.).",
            func=press_key_tool,
            input_schema=PressKeyInput,
        ))
        self.register_alias("press_keys", "press_key")

        self.register(ToolDefinition(
            name="send_shortcut",
            category="keyboard",
            description="Send an approved keyboard shortcut combo (ctrl+c, ctrl+v, ctrl+s, etc.).",
            func=send_shortcut_tool,
            input_schema=SendShortcutInput,
        ))

        self.register(ToolDefinition(
            name="move_mouse",
            category="mouse",
            description="Move mouse cursor to absolute virtual desktop or window-relative coordinates.",
            func=move_mouse_tool,
            input_schema=MoveMouseInput,
        ))
        self.register_alias("move", "move_mouse")

        self.register(ToolDefinition(
            name="click_mouse",
            category="mouse",
            description="Click mouse button (left, right, middle) with single or double click.",
            func=click_mouse_tool,
            input_schema=ClickMouseInput,
        ))
        self.register_alias("click", "click_mouse")

        self.register(ToolDefinition(
            name="scroll_mouse",
            category="mouse",
            description="Scroll mouse wheel vertically by bounded amount.",
            func=scroll_mouse_tool,
            input_schema=ScrollMouseInput,
        ))

        # 3. Filesystem
        self.register(ToolDefinition(
            name="list_directory",
            category="filesystem",
            description="List files and folders in an approved user directory.",
            func=list_directory_tool,
            input_schema=ListDirectoryInput,
        ))

        self.register(ToolDefinition(
            name="search_files",
            category="filesystem",
            description="Search for files by name pattern or extension within approved directories.",
            func=search_files_tool,
            input_schema=SearchFilesInput,
        ))

        self.register(ToolDefinition(
            name="get_file_metadata",
            category="filesystem",
            description="Retrieve detailed file metadata (size, timestamps, type) for an approved file.",
            func=get_file_metadata_tool,
            input_schema=GetFileMetadataInput,
        ))

        self.register(ToolDefinition(
            name="create_folder",
            category="filesystem",
            description="Create a new directory in an approved directory path.",
            func=create_folder_tool,
            input_schema=CreateFolderInput,
        ))

        self.register(ToolDefinition(
            name="create_file",
            category="filesystem",
            description="Create a new text or binary file in an approved directory.",
            func=create_file_tool,
            input_schema=CreateFileInput,
        ))

        self.register(ToolDefinition(
            name="rename_item",
            category="filesystem",
            description="Rename an existing file or folder in an approved directory.",
            func=rename_item_tool,
            input_schema=RenameItemInput,
        ))

        self.register(ToolDefinition(
            name="copy_item",
            category="filesystem",
            description="Copy a file or folder within approved directories.",
            func=copy_item_tool,
            input_schema=CopyItemInput,
        ))

        self.register(ToolDefinition(
            name="move_item",
            category="filesystem",
            description="Move a file or folder within approved directories.",
            func=move_item_tool,
            input_schema=MoveItemInput,
        ))

        self.register(ToolDefinition(
            name="delete_file",
            category="filesystem",
            description="Delete a file in an approved directory (MANDATORY human confirmation).",
            func=delete_file_tool,
            input_schema=DeleteFileInput,
            inherent_confirmation=True,
            confirmation_action_type="delete_file",
        ))

        self.register(ToolDefinition(
            name="delete_folder",
            category="filesystem",
            description="Delete a directory in an approved directory (MANDATORY human confirmation).",
            func=delete_folder_tool,
            input_schema=DeleteFolderInput,
            inherent_confirmation=True,
            confirmation_action_type="delete_folder",
        ))

        # 4. Screenshots
        self.register(ToolDefinition(
            name="capture_desktop",
            category="screenshots",
            description="Capture a screenshot across the entire virtual desktop.",
            func=capture_desktop_tool,
            input_schema=CaptureDesktopInput,
        ))
        self.register_alias("capture_screenshot", "capture_desktop")
        self.register_alias("take_screenshot", "capture_desktop")

        self.register(ToolDefinition(
            name="capture_primary_monitor",
            category="screenshots",
            description="Capture a screenshot of the primary display monitor.",
            func=capture_primary_monitor_tool,
            input_schema=CaptureDesktopInput,
        ))

        self.register(ToolDefinition(
            name="capture_monitor",
            category="screenshots",
            description="Capture a screenshot of a specific 1-based display monitor.",
            func=capture_monitor_tool,
            input_schema=CaptureMonitorInput,
        ))

        self.register(ToolDefinition(
            name="capture_window",
            category="screenshots",
            description="Capture a pixel-accurate screenshot of an approved application window.",
            func=capture_window_tool,
            input_schema=CaptureWindowInput,
        ))

        self.register(ToolDefinition(
            name="list_monitors",
            category="screenshots",
            description="List connected physical display monitors and their geometry.",
            func=lambda data=None: list_monitors_tool(),
            input_schema=EmptyInput,
        ))

        # 5. Browser Automation
        self.register(ToolDefinition(
            name="start_browser",
            category="browser",
            description="Start an isolated, ephemeral browser session with Playwright.",
            func=start_browser_tool,
            input_schema=StartBrowserInput,
        ))
        self.register_alias("open_browser", "start_browser")

        self.register(ToolDefinition(
            name="navigate_url",
            category="browser",
            description="Navigate to an allowlisted HTTP/HTTPS URL with SSRF protection.",
            func=navigate_url_tool,
            input_schema=NavigateUrlInput,
        ))
        self.register_alias("search_web", "navigate_url")
        self.register_alias("browse", "navigate_url")

        self.register(ToolDefinition(
            name="get_page_info",
            category="browser",
            description="Retrieve metadata (URL, title, status code) for active browser page.",
            func=get_page_info_tool,
            input_schema=GetPageInfoInput,
        ))

        self.register(ToolDefinition(
            name="get_page_text",
            category="browser",
            description="Extract bounded visible text from active browser page.",
            func=get_page_text_tool,
            input_schema=GetPageTextInput,
        ))

        self.register(ToolDefinition(
            name="click_element",
            category="browser",
            description="Click a DOM element on active browser page with consequential action safeguards.",
            func=click_element_tool,
            input_schema=ClickElementInput,
        ))

        self.register(ToolDefinition(
            name="enter_text",
            category="browser",
            description="Type text into a form input element on active browser page.",
            func=enter_text_tool,
            input_schema=EnterTextInput,
        ))

        self.register(ToolDefinition(
            name="select_dropdown",
            category="browser",
            description="Select an option from a dropdown element on active browser page.",
            func=select_dropdown_tool,
            input_schema=SelectDropdownInput,
        ))

        self.register(ToolDefinition(
            name="scroll_page",
            category="browser",
            description="Scroll active browser page up, down, top, or bottom.",
            func=scroll_page_tool,
            input_schema=ScrollPageInput,
        ))

        self.register(ToolDefinition(
            name="navigate_history",
            category="browser",
            description="Navigate browser history (back, forward, reload).",
            func=navigate_history_tool,
            input_schema=NavigateHistoryInput,
        ))

        self.register(ToolDefinition(
            name="capture_browser_screenshot",
            category="browser",
            description="Capture a screenshot of active browser page.",
            func=capture_browser_screenshot_tool,
            input_schema=CaptureBrowserScreenshotInput,
        ))

        self.register(ToolDefinition(
            name="download_file",
            category="browser",
            description="Download an allowlisted file via browser to approved download directory.",
            func=download_file_tool,
            input_schema=DownloadFileInput,
        ))

        self.register(ToolDefinition(
            name="close_browser",
            category="browser",
            description="Close active browser session and clean up processes.",
            func=close_browser_tool,
            input_schema=CloseBrowserInput,
        ))

        # 6. System Metrics
        self.register(ToolDefinition(
            name="get_system_info",
            category="system",
            description="Retrieve safe, read-only system metrics (OS, CPU, RAM) without shell access.",
            func=get_system_info_tool,
            input_schema=EmptyInput,
        ))


# ==============================================================================
# Global Shared ToolRegistry Singleton
# ==============================================================================

_SHARED_TOOL_REGISTRY: Optional[ToolRegistry] = None


def get_shared_tool_registry() -> ToolRegistry:
    """Retrieve or initialize the active shared ToolRegistry singleton."""
    global _SHARED_TOOL_REGISTRY
    if _SHARED_TOOL_REGISTRY is None:
        _SHARED_TOOL_REGISTRY = ToolRegistry(register_defaults=True)
    return _SHARED_TOOL_REGISTRY


def reset_shared_tool_registry() -> None:
    """Reset the shared ToolRegistry singleton."""
    global _SHARED_TOOL_REGISTRY
    _SHARED_TOOL_REGISTRY = None

