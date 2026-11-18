"""
VoxPilot - Intelligent Task Planning & Dynamic Re-Planner Module
Transforms natural-language user intent into structured, Pydantic-validated
execution plans using Ollama (qwen3:4b) and deterministic planning rules.
"""

from __future__ import annotations

import re
import json
import logging
from typing import Optional, List, Dict, Any, Tuple
import ollama

from backend.agent.intent import extract_open_command_target
from backend.agent.tool_registry import ToolRegistry, get_shared_tool_registry
from backend.ai.model_manager import get_shared_model_manager
from backend.automation.application_discovery import get_shared_discovery_service
from backend.automation.application_launcher import PROHIBITED_QUERY_NAMES

logger = logging.getLogger(__name__)


class PlannerError(Exception):
    """Raised when agent planning fails."""
    pass


MAX_PLAN_STEPS = 10

ACTION_SCHEMAS_DOC: Dict[str, str] = {
    "launch_application": (
        "Launch any discovered Windows desktop application, Microsoft Store app, Windows Settings page, "
        "built-in utility, or system folder (e.g. Notepad, Calculator, Chrome, VS Code, WhatsApp, Spotify, "
        "Discord, Docker Desktop, File Explorer, Downloads, Recycle Bin, Control Panel, Task Manager, Device Manager). "
        "Parameters: {'application_name': str} (REQUIRED. Example: {'application_name': 'WhatsApp'}). "
        "CRITICAL: Do NOT use 'application' or 'app_name'; you MUST use 'application_name'."
    ),
    "open_application": (
        "Alias for launch_application. "
        "Parameters: {'application_name': str} (REQUIRED. Example: {'application_name': 'notepad'}). "
        "CRITICAL: Do NOT use 'application' or 'app_name'; you MUST use 'application_name'."
    ),
    "open_system_utility": (
        "Open an allowlisted Windows built-in utility (Control Panel, Task Manager, Device Manager, Services). "
        "Parameters: {'application_name': str} (REQUIRED. Example: {'application_name': 'Control Panel'})."
    ),
    "open_windows_settings": (
        "Open an allowlisted Windows Settings page (Windows Settings, Bluetooth Settings, Wi-Fi Settings, "
        "Display Settings, Sound Settings, Windows Update, Installed Apps). "
        "Parameters: {'setting_name': str} (REQUIRED. Example: {'setting_name': 'Bluetooth Settings'})."
    ),
    "search_applications": (
        "Search or list discovered installed applications on this Windows PC. "
        "Parameters: {'query': Optional[str], 'source': Optional[str], 'limit': Optional[int]}."
    ),
    "refresh_applications": (
        "Refresh the installed application discovery index. "
        "Parameters: {'include_store_apps': Optional[bool]}."
    ),
    "refresh_application_index": (
        "Alias for refresh_applications. "
        "Parameters: {'include_store_apps': Optional[bool]}."
    ),
    "find_windows": (
        "Discover open top-level windows for an approved application. "
        "Parameters: {'application_name': Optional[str], 'title_pattern': Optional[str]}."
    ),
    "manage_window": (
        "Manage an approved window. "
        "Parameters: {'action': str, 'application_name': Optional[str], 'hwnd': Optional[int], 'layout': Optional[str]}. "
        "Action must be one of: 'activate', 'minimize', 'maximize', 'restore', 'close', 'get_active', 'minimize_all', 'arrange'."
    ),
    "type_text": (
        "Type text into target application or active window using SendInput. "
        "Parameters: {'text': str, 'application_name': Optional[str], 'hwnd': Optional[int]}. "
        "CRITICAL: Parameter MUST be named 'text' (e.g. {'text': 'Hello', 'application_name': 'notepad'})."
    ),
    "press_key": (
        "Press an approved keyboard key (enter, tab, space, escape, backspace, etc.). "
        "Parameters: {'key': str, 'application_name': Optional[str], 'hwnd': Optional[int]}."
    ),
    "send_shortcut": (
        "Send an approved keyboard shortcut (ctrl+c, ctrl+v, ctrl+s, etc.). "
        "Parameters: {'shortcut': str, 'application_name': Optional[str], 'hwnd': Optional[int]}."
    ),
    "list_directory": (
        "List files and folders in an approved directory. "
        "Parameters: {'directory_path': str}."
    ),
    "search_files": (
        "Search for files by query pattern in approved directories. "
        "Parameters: {'directory_path': str, 'query': Optional[str]}."
    ),
    "get_file_metadata": (
        "Retrieve file size, timestamps, and metadata. "
        "Parameters: {'file_path': str}."
    ),
    "create_file": (
        "Create a file in an approved directory on disk. "
        "Parameters: {'file_path': str, 'content': Optional[str]}."
    ),
    "create_folder": (
        "Create a folder in an approved directory. "
        "Parameters: {'folder_path': str}."
    ),
    "delete_file": (
        "Delete a file on disk (MANDATORY human confirmation). "
        "Parameters: {'file_path': str}."
    ),
    "delete_folder": (
        "Delete a directory on disk (MANDATORY human confirmation). "
        "Parameters: {'folder_path': str}."
    ),
    "copy_item": (
        "Copy a file or directory. "
        "Parameters: {'source_path': str, 'destination_path': str}."
    ),
    "move_item": (
        "Move a file or directory. "
        "Parameters: {'source_path': str, 'destination_path': str}."
    ),
    "rename_item": (
        "Rename a file or directory. "
        "Parameters: {'source_path': str, 'new_name': str}."
    ),
    "start_browser": (
        "Start an isolated Playwright browser session. "
        "Parameters: {}."
    ),
    "navigate_url": (
        "Navigate browser to an allowlisted HTTP/HTTPS URL. "
        "Parameters: {'url': str}."
    ),
    "click_element": (
        "Click a web element on active page. "
        "Parameters: {'selector': str}."
    ),
    "enter_text": (
        "Enter text into an input element on active web page. "
        "Parameters: {'selector': str, 'text': str}."
    ),
    "get_system_info": (
        "Retrieve system hardware and OS metrics. "
        "Parameters: {}."
    ),
    "capture_desktop": (
        "Capture full desktop screenshot. "
        "Parameters: {}."
    ),
    "capture_window": (
        "Capture screenshot of an approved window. "
        "Parameters: {'application_name': Optional[str], 'hwnd': Optional[int]}."
    ),
    "respond": (
        "Conversational reply to the user. "
        "Parameters: {}."
    ),
    "ask_clarification": (
        "Ask user for genuinely missing information. "
        "Parameters: {}."
    ),
}


class AgentPlanner:

    def __init__(self, model: str = "qwen3:4b"):
        self.model = model

        self.allowed_actions = {
            # Application and window automation
            "launch_application",
            "open_application",
            "open_system_utility",
            "open_windows_settings",
            "open_settings",
            "search_applications",
            "list_applications",
            "refresh_applications",
            "refresh_application_index",
            "find_windows",
            "manage_window",
            # Keyboard and mouse interaction
            "type_text",
            "press_key",
            "send_shortcut",
            "move_mouse",
            "click_mouse",
            "scroll_mouse",
            # File and directory management
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
            # Screenshots
            "capture_desktop",
            "capture_primary_monitor",
            "capture_monitor",
            "capture_window",
            "list_monitors",
            # Browser automation
            "start_browser",
            "navigate_url",
            "search_web",
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
            # System metrics
            "get_system_info",
            # Conversational and fallback
            "respond",
            "ask_clarification",
        }

        self.intent_actions = {
            "general_conversation": ["respond", "ask_clarification"],
            "open_application": [
                "launch_application",
                "open_application",
                "open_system_utility",
                "open_windows_settings",
                "search_applications",
                "refresh_applications",
                "refresh_application_index",
                "find_windows",
                "manage_window",
                "type_text",
                "press_key",
                "send_shortcut",
                "respond",
                "ask_clarification",
            ],
            "keyboard_automation": [
                "launch_application",
                "open_application",
                "find_windows",
                "manage_window",
                "type_text",
                "press_key",
                "send_shortcut",
                "respond",
                "ask_clarification",
            ],
            "window_management": [
                "find_windows",
                "manage_window",
                "launch_application",
                "open_application",
                "respond",
                "ask_clarification",
            ],
            "file_management": [
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
                "respond",
                "ask_clarification",
            ],
            "system_information": [
                "get_system_info",
                "list_monitors",
                "capture_desktop",
                "capture_primary_monitor",
                "capture_monitor",
                "capture_window",
                "find_windows",
                "search_applications",
                "respond",
                "ask_clarification",
            ],
            "browser_task": [
                "start_browser",
                "navigate_url",
                "search_web",
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
                "respond",
                "ask_clarification",
            ],
            "email_assistant": ["respond", "ask_clarification"],
            "unknown": ["ask_clarification", "respond"],
        }

    def _deterministic_plan(self, user_input: str, intent: str) -> Optional[Dict[str, Any]]:
        """Construct deterministic plans for standard natural-language desktop requests."""
        clean = user_input.strip()
        lower = clean.lower()

        if intent == "general_conversation":
            return {
                "steps": [
                    {
                        "action": "respond",
                        "description": "Generate conversational response",
                        "parameters": {},
                        "requires_confirmation": False,
                    }
                ],
                "response": "",
            }

        # 0a. Application index refresh ("Refresh application index", "Rescan installed applications")
        if re.search(
            r"^(?:refresh|rescan|update|rebuild)\s+(?:the\s+)?(?:installed\s+)?(?:application|app)\s+(?:index|list|cache|catalog)\.?$",
            lower,
        ):
            return {
                "steps": [
                    {
                        "action": "refresh_applications",
                        "description": "Refresh the Windows application discovery index",
                        "parameters": {"include_store_apps": True},
                        "requires_confirmation": False,
                    }
                ],
                "response": "Refreshing the installed application discovery index.",
            }

        # 0b. Application search / listing ("Show me all installed applications", "List installed apps")
        search_apps_match = re.search(
            r"^(?:show|list|search|find|get)\s+(?:me\s+)?(?:all\s+)?(?:installed\s+)?(?:applications|apps|programs)(?:\s+(?:matching|for|named|called)\s+(.+?))?\.?$",
            clean,
            re.IGNORECASE,
        )
        if search_apps_match:
            app_filter = (search_apps_match.group(1) or "").strip().strip("'\"")
            return {
                "steps": [
                    {
                        "action": "search_applications",
                        "description": (
                            f"Search installed applications for '{app_filter}'"
                            if app_filter
                            else "List discovered installed applications"
                        ),
                        "parameters": {"query": app_filter},
                        "requires_confirmation": False,
                    }
                ],
                "response": (
                    f"Searching installed applications for '{app_filter}'."
                    if app_filter
                    else "Listing discovered installed applications."
                ),
            }

        # Resolve natural-language folder listing requests against actual existing directories.
        list_plan = self._parse_list_directory_request(clean, lower)
        if list_plan is not None:
            return list_plan

        # 1. Direct core application launching ("Open Notepad", "Launch Calculator", "Start VS Code")
        launch_match = re.search(
            r"^(?:open|launch|start|run)\s+(notepad|calculator|chrome|google\s+chrome|vs\s*code|vscode|visual\s+studio\s+code)\.?$",
            lower,
        )
        if launch_match and not ("and type" in lower or "and write" in lower):
            raw_app = launch_match.group(1).strip()
            if raw_app in ("vscode", "vs code", "visual studio code"):
                app_name = "vscode"
            elif raw_app in ("chrome", "google chrome"):
                app_name = "chrome"
            else:
                app_name = raw_app.replace(" ", "")
            return {
                "steps": [
                    {
                        "action": "launch_application",
                        "description": f"Launch approved application: {app_name}",
                        "parameters": {"application_name": app_name},
                        "requires_confirmation": False,
                    }
                ],
                "response": f"Launching {app_name.capitalize()}.",
            }

        # 2. Typing text into an application ("Type Hello from VoxPilot! into Notepad.")
        type_app_match = re.search(
            r"^(?:type|write|input)\s+[\"']?(.*?)[\"']?\s+(?:into|in|to)\s+(notepad|calculator|chrome|vs\s*code|vscode)\.?$",
            clean,
            re.IGNORECASE,
        )
        if type_app_match:
            text_to_type = type_app_match.group(1).strip()
            raw_app = type_app_match.group(2).lower().replace(" ", "")
            app_name = "vscode" if raw_app in ("vscode", "vs code") else raw_app
            return {
                "steps": [
                    {
                        "action": "find_windows",
                        "description": f"Verify whether {app_name} is running",
                        "parameters": {"application_name": app_name},
                        "requires_confirmation": False,
                    },
                    {
                        "action": "manage_window",
                        "description": f"Bring {app_name} window to foreground",
                        "parameters": {"action": "activate", "application_name": app_name},
                        "requires_confirmation": False,
                    },
                    {
                        "action": "type_text",
                        "description": f"Type text into {app_name}",
                        "parameters": {"text": text_to_type, "application_name": app_name},
                        "requires_confirmation": False,
                    },
                ],
                "response": f"Typing text into {app_name.capitalize()}.",
            }

        # 3. Typing text into active window ("Type this message into the active window.")
        type_active_match = re.search(
            r"^(?:type|write|input)\s+[\"']?(.*?)[\"']?\s+(?:into|in|to)\s+(?:the\s+)?active\s+window\.?$",
            clean,
            re.IGNORECASE,
        )
        if type_active_match:
            text_to_type = type_active_match.group(1).strip()
            return {
                "steps": [
                    {
                        "action": "type_text",
                        "description": "Type text into active foreground window",
                        "parameters": {"text": text_to_type},
                        "requires_confirmation": False,
                    }
                ],
                "response": "Typing text into the active foreground window.",
            }

        # 4a. Multi-step: Open Notepad, write text, and save in Documents
        # e.g. "Open Notepad, write a meeting summary, and save it in Documents."
        notepad_save_match = re.search(
            r"^(?:open|launch|start)\s+notepad[,;\s]+(?:and\s+)?(?:write|type)\s+[\"']?(.*?)[\"']?[,;\s]+and\s+save\s+(?:it\s+)?(?:in|to)\s+(documents|desktop|downloads)(?:\s+as\s+([a-zA-Z0-9_\-\.]+))?\.?$",
            clean,
            re.IGNORECASE,
        )
        if notepad_save_match:
            import os
            content_desc = notepad_save_match.group(1).strip()
            folder_key = notepad_save_match.group(2).strip().capitalize()
            explicit_name = notepad_save_match.group(3)
            filename = explicit_name.strip() if explicit_name else "meeting_summary.txt"
            if "." not in filename:
                filename = f"{filename}.txt"
            target_path = os.path.join(os.path.expanduser("~"), folder_key, filename)
            return {
                "steps": [
                    {
                        "action": "launch_application",
                        "description": "Launch Notepad",
                        "parameters": {"application_name": "notepad"},
                        "requires_confirmation": False,
                    },
                    {
                        "action": "type_text",
                        "description": "Enter supplied text into Notepad",
                        "parameters": {"text": content_desc, "application_name": "notepad"},
                        "requires_confirmation": False,
                    },
                    {
                        "action": "send_shortcut",
                        "description": "Open Save As dialog in Notepad",
                        "parameters": {"shortcut": "ctrl+s", "application_name": "notepad"},
                        "requires_confirmation": False,
                    },
                    {
                        "action": "type_text",
                        "description": f"Enter destination path in {folder_key}: {filename}",
                        "parameters": {"text": target_path},
                        "requires_confirmation": False,
                    },
                    {
                        "action": "press_key",
                        "description": "Confirm Save dialog",
                        "parameters": {"key": "enter"},
                        "requires_confirmation": False,
                    },
                ],
                "response": f"Opening Notepad, entering text, and saving to {folder_key} as {filename}.",
            }

        # 4. Composite open and type ("Open Notepad and type Hello")
        composite_match = re.search(
            r"^(?:open|launch|start)\s+(notepad|calculator|chrome|vs\s*code|vscode)\s+and\s+(?:type|write)\s+[\"']?(.*?)[\"']?\.?$",
            clean,
            re.IGNORECASE,
        )
        if composite_match:
            raw_app = composite_match.group(1).lower().replace(" ", "")
            app_name = "vscode" if raw_app in ("vscode", "vs code") else raw_app
            text_to_type = composite_match.group(2).strip()
            return {
                "steps": [
                    {
                        "action": "launch_application",
                        "description": f"Launch approved application: {app_name}",
                        "parameters": {"application_name": app_name},
                        "requires_confirmation": False,
                    },
                    {
                        "action": "find_windows",
                        "description": f"Find {app_name} window",
                        "parameters": {"application_name": app_name},
                        "requires_confirmation": False,
                    },
                    {
                        "action": "manage_window",
                        "description": f"Bring {app_name} window to foreground",
                        "parameters": {"action": "activate", "application_name": app_name},
                        "requires_confirmation": False,
                    },
                    {
                        "action": "type_text",
                        "description": f"Type text into {app_name}",
                        "parameters": {"text": text_to_type, "application_name": app_name},
                        "requires_confirmation": False,
                    },
                ],
                "response": f"Opening {app_name.capitalize()} and typing text.",
            }

        # 5a. Desktop-wide window operations ("Minimize all windows", "Arrange my open windows", "Close active window")
        if re.search(r"^minimize\s+(?:all|every)\s+windows?\.?$", lower):
            return {
                "steps": [
                    {
                        "action": "manage_window",
                        "description": "Minimize all open approved windows",
                        "parameters": {"action": "minimize_all"},
                        "requires_confirmation": False,
                    }
                ],
                "response": "Minimizing all open windows.",
            }

        arrange_match = re.search(
            r"^(?:arrange|tile|cascade)\s+(?:my\s+)?(?:open\s+)?windows?\.?$",
            lower,
        )
        if arrange_match:
            layout = "cascade" if "cascade" in lower else "tile"
            return {
                "steps": [
                    {
                        "action": "manage_window",
                        "description": f"Arrange open windows ({layout})",
                        "parameters": {"action": "arrange", "layout": layout},
                        "requires_confirmation": False,
                    }
                ],
                "response": f"Arranging open windows in {layout} layout.",
            }

        if re.search(
            r"^close\s+(?:the\s+)?(?:currently\s+)?(?:active|current|this)\s+window\.?$",
            lower,
        ):
            return {
                "steps": [
                    {
                        "action": "manage_window",
                        "description": "Close the currently active window (requires confirmation)",
                        "parameters": {"action": "close"},
                        "requires_confirmation": True,
                    }
                ],
                "response": "Requesting confirmation to close the currently active window.",
            }

        if re.search(
            r"^(?:what|which|identify|show|get)\s+(?:is\s+)?(?:the\s+)?(?:currently\s+)?active\s+window\??$",
            lower,
        ):
            return {
                "steps": [
                    {
                        "action": "manage_window",
                        "description": "Identify the currently active window",
                        "parameters": {"action": "get_active"},
                        "requires_confirmation": False,
                    }
                ],
                "response": "Checking the currently active window.",
            }

        if re.search(
            r"^(?:list|show)\s+(?:all\s+)?(?:running|open)\s+(?:applications|apps|windows)\.?$",
            lower,
        ):
            return {
                "steps": [
                    {
                        "action": "find_windows",
                        "description": "List running applications and open windows",
                        "parameters": {},
                        "requires_confirmation": False,
                    }
                ],
                "response": "Listing currently open windows.",
            }

        # 5b. Switch to / focus / minimize / maximize / restore a named window
        switch_match = re.search(
            r"^(?:switch\s+to|focus|bring\s+to\s+(?:the\s+)?front)\s+(.+?)\.?$",
            clean,
            re.IGNORECASE,
        )
        if switch_match:
            target_app = switch_match.group(1).strip().strip("'\"")
            return {
                "steps": [
                    {
                        "action": "find_windows",
                        "description": f"Find {target_app} window",
                        "parameters": {"application_name": target_app},
                        "requires_confirmation": False,
                    },
                    {
                        "action": "manage_window",
                        "description": f"Activate {target_app} window",
                        "parameters": {"action": "activate", "application_name": target_app},
                        "requires_confirmation": False,
                    },
                ],
                "response": f"Switching to {target_app}.",
            }

        # 5. Window management ("Minimize Notepad", "Maximize Calculator")
        win_manage_match = re.search(
            r"^(minimize|maximize|restore)\s+(notepad|calculator|chrome|vs\s*code|vscode)\.?$",
            lower,
        )
        if win_manage_match:
            action = win_manage_match.group(1)
            raw_app = win_manage_match.group(2).replace(" ", "")
            app_name = "vscode" if raw_app in ("vscode", "vs code") else raw_app
            return {
                "steps": [
                    {
                        "action": "find_windows",
                        "description": f"Find {app_name} window",
                        "parameters": {"application_name": app_name},
                        "requires_confirmation": False,
                    },
                    {
                        "action": "manage_window",
                        "description": f"{action.capitalize()} {app_name} window",
                        "parameters": {"action": action, "application_name": app_name},
                        "requires_confirmation": False,
                    },
                ],
                "response": f"{action.capitalize()}ing {app_name.capitalize()} window.",
            }

        # 6. Window close ("Close Notepad")
        close_match = re.search(
            r"^close\s+(notepad|calculator|chrome|vs\s*code|vscode)\.?$",
            lower,
        )
        if close_match:
            raw_app = close_match.group(1).replace(" ", "")
            app_name = "vscode" if raw_app in ("vscode", "vs code") else raw_app
            return {
                "steps": [
                    {
                        "action": "ask_clarification",
                        "description": f"Closing {app_name} may cause unsaved changes to be lost. Please confirm if you wish to close {app_name}.",
                        "parameters": {},
                        "requires_confirmation": False,
                    }
                ],
                "response": f"Please confirm if you wish to close {app_name.capitalize()}.",
            }

        # 7. Create file ("Create a Notepad file in desktop.",
        #    "Create a text file on my desktop.",
        #    "Create a Python file in downloads.",
        #    "Create a text file named notes.txt",
        #    "Create a text file containing these notes: ...")
        create_plan = self._parse_create_file_request(clean, lower)
        if create_plan is not None:
            return create_plan

        # 8. Save document ("Save the document", "Save the current document")
        if re.search(r"^save\s+(?:the\s+)?(?:current\s+)?(?:document|file)\.?$", lower):
            return {
                "steps": [
                    {
                        "action": "send_shortcut",
                        "description": "Send Ctrl+S to save current document",
                        "parameters": {"shortcut": "ctrl+s"},
                        "requires_confirmation": False,
                    }
                ],
                "response": "Sending Ctrl+S shortcut to save the document.",
            }

        # 9a. Open special user folders ("Open my Downloads folder", "Open Documents folder")
        open_folder_match = re.search(
            r"^open\s+(?:my\s+)?(?:the\s+)?(downloads|documents|desktop)(?:\s+folder)?\.?$",
            lower,
        )
        if open_folder_match:
            folder_name = open_folder_match.group(1).strip()
            return {
                "steps": [
                    {
                        "action": "launch_application",
                        "description": f"Open {folder_name.capitalize()} folder",
                        "parameters": {"application_name": folder_name},
                        "requires_confirmation": False,
                    }
                ],
                "response": f"Opening your {folder_name.capitalize()} folder.",
            }

        # 9b. Search for folder ("Find a folder named VoxPilot")
        folder_search_match = re.search(
            r"^(?:search|find|look|locate)\s+(?:for\s+)?(?:a\s+)?(?:folder|directory)\s+(?:named\s+|called\s+)?([a-zA-Z0-9_\-\.\s]{1,80}?)\.?$",
            clean,
            re.IGNORECASE,
        )
        if folder_search_match:
            import os
            folder_query = folder_search_match.group(1).strip().strip("'\"")
            home_dir = os.path.expanduser("~")
            return {
                "steps": [
                    {
                        "action": "search_files",
                        "description": f"Search for folder named '{folder_query}'",
                        "parameters": {"query": folder_query, "directory_path": home_dir},
                        "requires_confirmation": False,
                    }
                ],
                "response": f"Searching for folder '{folder_query}'.",
            }

        # 9. Search for file ("Search for notes.txt", "Search for a document")
        search_match = re.search(
            r"^(?:search|find|look)\s+for\s+(?:a\s+)?(?:file|document\s+named\s+|document\s+)?([a-zA-Z0-9_\-\.\*]+)\.?$",
            lower,
        )
        if search_match:
            query = search_match.group(1).strip()
            if query in ("document", "file"):
                query = "*"
            import os
            default_dir = os.path.join(os.path.expanduser("~"), "Documents")
            return {
                "steps": [
                    {
                        "action": "search_files",
                        "description": f"Search for file: {query}",
                        "parameters": {"query": query, "directory_path": default_dir},
                        "requires_confirmation": False,
                    }
                ],
                "response": f"Searching for '{query}'.",
            }

        # 10. Universal Application, Windows Settings, System Utility, or Special Folder launching
        # Resolves fuzzy/phonetic/alias queries (e.g. "Open, watch up." -> "WhatsApp") against the
        # discovered application index and asks for clarification when ambiguous or low-confidence.
        if intent == "open_application" and not ("and type" in lower or "and write" in lower):
            raw_target = extract_open_command_target(clean)
            if raw_target:
                lower_target = raw_target.lower()
                is_url = bool(re.search(r"^(?:https?://|www\.)|\.(?:com|org|net|io|gov|edu)\b", lower_target))
                is_doc_file = bool(
                    re.search(
                        r"\.(?:txt|md|pdf|docx?|xlsx?|pptx?|csv|json|xml|yaml|yml|py|js|ts|html|css|log)$",
                        lower_target,
                    )
                )
                is_my_folder = bool(
                    re.search(
                        r"^(?:my\s+(?:files|directory|folder|downloads|documents|desktop)|(?:the\s+)?(?:downloads|documents|desktop)\s+folder)\b",
                        lower_target,
                    )
                )
                if not is_url and not is_doc_file and not is_my_folder and "/" not in raw_target and "\\" not in raw_target:
                    registry = get_shared_tool_registry()
                    if lower_target in PROHIBITED_QUERY_NAMES:
                        return {
                            "steps": [
                                {
                                    "action": "launch_application",
                                    "description": f"Attempt to launch restricted destination: {raw_target}",
                                    "parameters": {"application_name": raw_target},
                                    "requires_confirmation": True,
                                }
                            ],
                            "response": f"Checking security policy for {raw_target}.",
                        }

                    discovery = get_shared_discovery_service()
                    resolution = discovery.resolve_query(raw_target)
                    status = resolution.get("status")
                    matched = resolution.get("match")
                    confidence = float(resolution.get("confidence", 0.0) or 0.0)

                    if status == "resolved" and matched is not None and confidence >= 0.72:
                        # Use canonical display_name unless it contains characters restricted by LaunchApplicationInput
                        # (e.g. '&' in "Bluetooth & Devices Settings"); in that case keep raw_target or app_id.
                        candidate_name = matched.display_name
                        if re.search(r"[/\\;&|><`$\r\n]", candidate_name):
                            candidate_name = raw_target
                        needs_conf = (
                            registry._is_sensitive_application_target(candidate_name)
                            or registry._is_sensitive_application_target(raw_target)
                        )
                        return {
                            "steps": [
                                {
                                    "action": "launch_application",
                                    "description": f"Launch application or destination: {candidate_name}",
                                    "parameters": {"application_name": candidate_name},
                                    "requires_confirmation": needs_conf,
                                }
                            ],
                            "response": f"Opening {candidate_name}.",
                        }

                    if status == "ambiguous":
                        clarification_msg = resolution.get("message") or (
                            f"Multiple installed applications match '{raw_target}'. Which one would you like to open?"
                        )
                        return {
                            "steps": [
                                {
                                    "action": "ask_clarification",
                                    "description": f"Clarify ambiguous application query: {raw_target}",
                                    "parameters": {"question": clarification_msg},
                                    "requires_confirmation": False,
                                }
                            ],
                            "response": clarification_msg,
                        }

                    needs_conf = registry._is_sensitive_application_target(raw_target)
                    return {
                        "steps": [
                            {
                                "action": "launch_application",
                                "description": f"Launch application or destination: {raw_target}",
                                "parameters": {"application_name": raw_target},
                                "requires_confirmation": needs_conf,
                            }
                        ],
                        "response": f"Opening {raw_target}.",
                    }

        return None

    _FILE_TYPE_DEFAULT_NAMES: Dict[str, str] = {
        "notepad": "New Text Document.txt",
        "text": "New Text Document.txt",
        "txt": "New Text Document.txt",
        "note": "New Text Document.txt",
        "notes": "New Text Document.txt",
        "python": "new_file.py",
        "py": "new_file.py",
        "html": "index.html",
        "htm": "index.html",
        "markdown": "notes.md",
        "md": "notes.md",
        "json": "data.json",
        "csv": "data.csv",
        "yaml": "config.yaml",
        "yml": "config.yaml",
        "xml": "data.xml",
        "javascript": "script.js",
        "js": "script.js",
        "typescript": "script.ts",
        "ts": "script.ts",
        "css": "styles.css",
        "sql": "query.sql",
        "log": "app.log",
        "batch": "script.bat",
        "bat": "script.bat",
        "powershell": "script.ps1",
        "ps1": "script.ps1",
    }

    def _parse_create_file_request(self, clean: str, lower: str) -> Optional[Dict[str, Any]]:
        """Deterministically plan natural-language file creation with known-folder and default-filename inference."""
        import os
        from pathlib import Path
        from backend.automation.file_manager import (
            generate_unique_filename,
            resolve_known_folder,
        )

        # Split optional trailing content clause ("containing ...", "with content ...")
        note_content = ""
        header = clean
        content_match = re.search(
            r"\s+(?:containing|with)\s+(?:these\s+notes\s*:?\s*|content\s*:?\s*)?(.+)$",
            clean,
            re.IGNORECASE | re.DOTALL,
        )
        if content_match:
            header = clean[: content_match.start()].strip()
            note_content = content_match.group(1).strip().strip("'\"")

        header_match = re.match(
            r"^(?:create|make)\s+(?:an?\s+)?(?:new\s+)?(?:empty\s+)?(?:(?P<ftype>notepad|text|txt|python|py|html|htm|markdown|md|json|csv|yaml|yml|xml|javascript|js|typescript|ts|css|sql|log|batch|bat|powershell|ps1|notes?)\s+)?(?:file|document)(?P<rest>.*)$",
            header.strip().rstrip(".!?"),
            re.IGNORECASE,
        )
        if not header_match:
            return None

        ftype = (header_match.group("ftype") or "").lower() or None
        rest = (header_match.group("rest") or "").strip()

        # Extract destination clause ("in desktop", "on my desktop", "in C:\...", etc.)
        dest_text: Optional[str] = None
        dest_match = re.search(
            r"\b(?:in|on|to|into|at|inside)\s+(?P<dest>.+?)\s*$",
            rest,
            re.IGNORECASE,
        )
        if dest_match:
            dest_text = dest_match.group("dest").strip().strip("'\"")
            rest = rest[: dest_match.start()].strip()

        # Extract explicit filename if provided ("named foo.txt", "called bar", or standalone "foo.txt")
        explicit_filename: Optional[str] = None
        name_match = re.match(
            r"^(?:named|called)\s+['\"]?([a-zA-Z0-9_\-\. ]+?)['\"]?$",
            rest,
            re.IGNORECASE,
        )
        if name_match:
            explicit_filename = name_match.group(1).strip()
        elif rest and re.match(r"^[a-zA-Z0-9_\-\.]+\.[a-zA-Z0-9]+$", rest):
            explicit_filename = rest

        # Append inferred extension if user gave a bare name alongside a file type (e.g. "python file named app")
        if explicit_filename and "." not in explicit_filename and ftype in self._FILE_TYPE_DEFAULT_NAMES:
            default_ext = Path(self._FILE_TYPE_DEFAULT_NAMES[ftype]).suffix
            explicit_filename = f"{explicit_filename}{default_ext}"

        filename_is_default = explicit_filename is None
        default_name = (
            self._FILE_TYPE_DEFAULT_NAMES.get(ftype, "New Text Document.txt")
            if ftype
            else ("notes.txt" if note_content else "New Text Document.txt")
        )
        filename = explicit_filename or default_name

        # Resolve destination directory
        dest_dir: Optional[Path] = None
        if dest_text:
            dest_dir = resolve_known_folder(dest_text)
            if dest_dir is None:
                if os.path.isabs(dest_text):
                    dest_dir = Path(dest_text)
                else:
                    clarify_msg = (
                        f"I couldn't recognize the destination folder '{dest_text}'. "
                        "Please specify a known folder (such as Desktop, Downloads, Documents, or Pictures) "
                        "or provide a full directory path."
                    )
                    return {
                        "steps": [
                            {
                                "action": "ask_clarification",
                                "description": clarify_msg,
                                "parameters": {"question": clarify_msg},
                                "requires_confirmation": False,
                            }
                        ],
                        "response": clarify_msg,
                    }
        elif note_content:
            dest_dir = resolve_known_folder("documents") or (Path.home() / "Documents")
        elif explicit_filename is None:
            clarify_msg = (
                "Where should I create the file? Please specify a destination such as "
                "Desktop, Downloads, Documents, Pictures, or a full path."
            )
            return {
                "steps": [
                    {
                        "action": "ask_clarification",
                        "description": clarify_msg,
                        "parameters": {"question": clarify_msg},
                        "requires_confirmation": False,
                    }
                ],
                "response": clarify_msg,
            }

        if dest_dir is not None:
            if filename_is_default and dest_dir.is_dir():
                target_path = generate_unique_filename(dest_dir, filename)
            else:
                target_path = dest_dir / filename
            target_str = str(target_path)
        else:
            target_str = filename

        params: Dict[str, Any] = {
            "file_path": target_str,
            "content": note_content,
        }
        if filename_is_default:
            params["auto_rename_on_conflict"] = True

        return {
            "steps": [
                {
                    "action": "create_file",
                    "description": f"Create file '{Path(target_str).name}' at '{target_str}'",
                    "parameters": params,
                    "requires_confirmation": False,
                }
            ],
            "response": (
                f"Creating '{target_str}' with your notes."
                if note_content
                else f"Creating file at '{target_str}'."
            ),
        }

    def _parse_list_directory_request(self, clean: str, lower: str) -> Optional[Dict[str, Any]]:
        """Deterministically resolve natural-language directory listing requests against existing folders."""
        from pathlib import Path
        from backend.automation.file_manager import (
            AmbiguousDirectoryError,
            FileManager,
            FileManagerError,
            PathNotAllowedError,
            PathNotFoundError,
        )

        # Pattern A: "List files in my Major Project folder", "Show contents of Documents"
        match = re.match(
            r"^(?:list|show)\s+(?:all\s+|the\s+|my\s+)?(?:files|folders|items|contents|everything|directory|folder)\s+(?:in|inside|of|from|under|within)\s+(?P<target>.+?)\.?$",
            clean.strip(),
            re.IGNORECASE,
        )
        if not match:
            # Pattern B: "List my downloads", "Show desktop folder"
            match = re.match(
                r"^(?:list|show)\s+(?P<target>(?:my\s+|the\s+)?(?:downloads|documents|desktop|pictures|music|videos)(?:\s+folder)?)\.?$",
                clean.strip(),
                re.IGNORECASE,
            )
        if not match:
            return None

        raw_target = match.group("target").strip().strip("'\"")
        if not raw_target:
            return None

        is_explicit = Path(raw_target).is_absolute() or any(c in raw_target for c in ("/", "\\", ":"))
        try:
            mgr = FileManager()
            resolved = mgr.resolve_directory(raw_target)
            if not resolved.is_dir():
                clarify_msg = f"'{resolved}' is not a directory. Please provide a valid folder path."
                return {
                    "steps": [
                        {
                            "action": "ask_clarification",
                            "description": clarify_msg,
                            "parameters": {"question": clarify_msg},
                            "requires_confirmation": False,
                        }
                    ],
                    "response": clarify_msg,
                }
            resolved_str = str(resolved)
            return {
                "steps": [
                    {
                        "action": "list_directory",
                        "description": f"List files in '{resolved_str}'",
                        "parameters": {"directory_path": resolved_str},
                        "requires_confirmation": False,
                    }
                ],
                "response": f"Listing files in '{resolved_str}'.",
            }
        except AmbiguousDirectoryError as err:
            candidates_str = "\n".join(f"- {p}" for p in err.candidates)
            clarify_msg = (
                f"Multiple folders match '{raw_target}'. Which one do you mean?\n{candidates_str}"
            )
            return {
                "steps": [
                    {
                        "action": "ask_clarification",
                        "description": clarify_msg,
                        "parameters": {"question": clarify_msg, "candidates": [str(p) for p in err.candidates]},
                        "requires_confirmation": False,
                    }
                ],
                "response": clarify_msg,
            }
        except (PathNotFoundError, PathNotAllowedError, FileManagerError) as err:
            if is_explicit:
                clarify_msg = (
                    f"The specified directory '{raw_target}' could not be accessed ({err}). "
                    "Please check that the path exists and is inside an approved directory."
                )
            else:
                clarify_msg = (
                    f"I couldn't find a folder matching '{raw_target}' in your known folders "
                    "(Desktop, Documents, Downloads, Pictures). Where is that folder located?"
                )
            return {
                "steps": [
                    {
                        "action": "ask_clarification",
                        "description": clarify_msg,
                        "parameters": {"question": clarify_msg},
                        "requires_confirmation": False,
                    }
                ],
                "response": clarify_msg,
            }

    def create_plan(
        self,
        user_input: str,
        intent: str,
        context: Optional[str] = None,
    ) -> Dict[str, Any]:

        if not user_input or not user_input.strip():
            raise PlannerError("User input cannot be empty.")

        if intent not in self.intent_actions:
            raise PlannerError(f"Unsupported intent: {intent}")

        allowed_for_intent = self.intent_actions[intent]
        registry = get_shared_tool_registry()

        # Check deterministic high-precision plan generation first
        deterministic_plan = self._deterministic_plan(user_input, intent)
        if deterministic_plan is not None:
            # Validate and normalize steps
            validated_steps = []
            for step in deterministic_plan["steps"]:
                action = step.get("action", "")
                params = dict(step.get("parameters", {}))
                normalized_params = registry.normalize_parameters(action, params)
                step_copy = dict(step)
                step_copy["parameters"] = normalized_params
                validated_steps.append(step_copy)
            return {
                "steps": validated_steps,
                "response": deterministic_plan["response"],
            }

        # Format parameter schemas documentation for the allowed actions
        schema_doc_lines = []
        for action in allowed_for_intent:
            if action in ACTION_SCHEMAS_DOC:
                schema_doc_lines.append(f"- {action}: {ACTION_SCHEMAS_DOC[action]}")
        formatted_schemas = "\n".join(schema_doc_lines)

        context_prompt = f"\nPrevious conversation context:\n{context}\n" if context else ""
        if intent == "open_application":
            try:
                disc = get_shared_discovery_service()
                if disc._loaded:
                    hints = disc.get_planner_application_hints(max_items=40)
                    context_prompt += f"\nDiscovered installed applications sample: {hints}\n"
            except Exception:
                pass

        system_prompt = f"""
You are the planning module of VoxPilot, a personal AI desktop assistant.

Your task is to convert the user's request into a structured action plan.
{context_prompt}
The user's classified intent is: {intent}

You may ONLY use these action types:
{allowed_for_intent}

Action Parameter Schemas (YOU MUST USE THESE EXACT PARAMETER NAMES):
{formatted_schemas}

Return only valid JSON in this format:

{{
    "steps": [
        {{
            "action": "action_name",
            "description": "What this step should accomplish",
            "parameters": {{}},
            "requires_confirmation": false
        }}
    ],
    "response": "Short explanation of the plan"
}}

Rules:
1. Only use the action types provided above.
2. You MUST use exact canonical parameter names as specified in the schemas:
   - For open_application/launch_application: use "application_name" (NOT "application", NOT "app_name").
   - For type_text: use "text" (NOT "string", NOT "message").
   - For find_windows: use "application_name".
   - For manage_window: use "action" ("activate", "minimize", "maximize", "restore") and "application_name".
3. Do not generate shell commands or executable code.
4. Do not invent file paths, application paths, or URLs.
5. If required information is missing, use ask_clarification.
6. Set requires_confirmation to true for destructive operations (delete_file, delete_folder).
7. For typing text into an application (e.g. "Type X into Notepad"):
   Step 1: find_windows with {{"application_name": "notepad"}}
   Step 2: manage_window with {{"action": "activate", "application_name": "notepad"}}
   Step 3: type_text with {{"text": "X", "application_name": "notepad"}}
8. Keep the plan simple and concise.
"""

        try:
            from backend.ai.providers.runtime import run_provider_chat

            def _local_plan_call(local_model: str, msgs, temp: float) -> str:
                model_manager = get_shared_model_manager()
                target_model = self.model or local_model
                with model_manager.track_inference(target_model):
                    result = ollama.chat(
                        model=target_model,
                        messages=msgs,
                        format="json",
                        options={"temperature": temp},
                    )
                return result.message.content

            response_text = run_provider_chat(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_input.strip()},
                ],
                temperature=0,
                json_mode=True,
                local_executor=_local_plan_call,
            )
            if not response_text:
                raise PlannerError("The model returned an empty plan.")

            plan_data = json.loads(response_text)
            steps = plan_data.get("steps")

            if not isinstance(steps, list) or not steps:
                raise PlannerError("The plan must contain at least one step.")

            if len(steps) > MAX_PLAN_STEPS:
                raise PlannerError(f"Plan step count ({len(steps)}) exceeds maximum allowed ({MAX_PLAN_STEPS}).")

            validated_steps = []

            for step in steps:
                if not isinstance(step, dict):
                    raise PlannerError("Each plan step must be an object.")

                action = step.get("action")
                if action not in self.allowed_actions:
                    raise PlannerError(f"Unsupported action: {action}")

                if action not in allowed_for_intent:
                    raise PlannerError(f"Action {action} is not allowed for intent {intent}.")

                parameters = step.get("parameters", {})
                if not isinstance(parameters, dict):
                    raise PlannerError("Step parameters must be an object.")

                # Normalize known aliases immediately
                normalized_params = registry.normalize_parameters(action, parameters)

                requires_confirmation = step.get("requires_confirmation", False)
                if not isinstance(requires_confirmation, bool):
                    raise PlannerError("requires_confirmation must be a boolean.")

                description = step.get("description", "")
                if not isinstance(description, str):
                    raise PlannerError("Step description must be a string.")

                validated_steps.append({
                    "action": action,
                    "description": description,
                    "parameters": normalized_params,
                    "requires_confirmation": requires_confirmation,
                })

            response = plan_data.get("response", "")
            if not isinstance(response, str):
                raise PlannerError("Plan response must be a string.")

            return {
                "steps": validated_steps,
                "response": response,
            }

        except PlannerError:
            raise

        except Exception as error:
            raise PlannerError(f"Could not create agent plan: {error}") from error

    def replan_from_results(
        self,
        original_input: str,
        completed_steps: List[Dict[str, Any]],
        completed_results: List[Dict[str, Any]],
        remaining_plan: List[Dict[str, Any]],
        context: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], str]:
        """Dynamically revise remaining plan steps based on structured tool outcomes.

        Returns:
            Tuple of (revised_remaining_steps, reason_string).
        """
        if not completed_steps or not completed_results:
            return remaining_plan, "No completed steps to evaluate."

        last_step = completed_steps[-1]
        last_result = completed_results[-1]
        last_action = last_step.get("action", "")
        last_success = last_result.get("success", False)
        last_data = last_result.get("data") or {}

        # 1. Intermediate Failure: abort dependent steps safely
        if not last_success:
            return [], f"Aborted dependent steps following failure in '{last_action}'."

        # 2. File discovery -> Bind target file or clarify
        if last_action in ("search_files", "list_directory"):
            matches = last_data.get("matches") or last_data.get("files") or []
            paths = [
                str(m.get("path") if isinstance(m, dict) else m)
                for m in matches
            ]
            if not paths:
                return [
                    {
                        "action": "respond",
                        "description": "Inform user that no matching files were found.",
                        "parameters": {},
                        "requires_confirmation": False,
                    }
                ], "No matching files found; updated plan to notify user."

            if len(paths) == 1:
                target_file = paths[0]
                lower_in = original_input.lower()
                if remaining_plan:
                    revised = []
                    for step in remaining_plan:
                        step_copy = dict(step)
                        params = dict(step_copy.get("parameters", {}))
                        for key in ("file_path", "source_path", "path", "target"):
                            if key in params or key in ("file_path", "source_path"):
                                params[key] = target_file
                                break
                        step_copy["parameters"] = params
                        revised.append(step_copy)
                    return revised, f"Discovered single matching file: {target_file}"
                elif any(w in lower_in for w in ("open", "read", "view", "launch")):
                    revised = [
                        {
                            "action": "get_file_metadata",
                            "description": f"Inspect discovered file: {target_file}",
                            "parameters": {"file_path": target_file},
                            "requires_confirmation": False,
                        }
                    ]
                    return revised, f"Unambiguously resolved file path to: {target_file}"
                return remaining_plan, "File search completed with 1 match."

            if len(paths) > 1:
                return [
                    {
                        "action": "ask_clarification",
                        "description": f"Found {len(paths)} matching files. Clarify which one user wants.",
                        "parameters": {},
                        "requires_confirmation": False,
                    }
                ], f"Multiple matching files found ({len(paths)}); clarification requested."

        # 3. Application launched -> Bind window HWND or insert find_windows
        if last_action in ("launch_application", "open_application"):
            hwnd = last_data.get("hwnd")
            needs_hwnd = False
            for step in remaining_plan:
                if step.get("action") in ("type_text", "press_key", "send_shortcut", "manage_window"):
                    params = step.get("parameters", {})
                    if not params.get("hwnd") or params.get("hwnd") == 0:
                        needs_hwnd = True
                        break

            if needs_hwnd:
                if hwnd:
                    revised = []
                    for step in remaining_plan:
                        step_copy = dict(step)
                        params = dict(step_copy.get("parameters", {}))
                        if step_copy.get("action") in ("type_text", "press_key", "send_shortcut", "manage_window"):
                            if not params.get("hwnd") or params.get("hwnd") == 0:
                                params["hwnd"] = int(hwnd)
                                step_copy["parameters"] = params
                        revised.append(step_copy)
                    return revised, f"Bound active window HWND {hwnd} to subsequent interaction steps."
                else:
                    app_name = last_step.get("parameters", {}).get("application_name", "")
                    revised = [
                        {
                            "action": "find_windows",
                            "description": f"Find top-level window for {app_name}",
                            "parameters": {"application_name": app_name},
                            "requires_confirmation": False,
                        }
                    ]
                    revised.extend(remaining_plan)
                    return revised, f"Added find_windows to bind window HWND for {app_name}."

        # 4. Window discovered via find_windows -> Bind HWND or launch if not running
        if last_action == "find_windows":
            windows = last_data.get("windows") or []
            if not windows:
                # Target window not found; if an application was targeted and subsequent steps interact with it, launch it first!
                target_app = last_step.get("parameters", {}).get("application_name")
                if not target_app:
                    for s in remaining_plan:
                        target_app = s.get("parameters", {}).get("application_name")
                        if target_app:
                            break
                if target_app:
                    needs_window = any(
                        step.get("action") in ("type_text", "press_key", "send_shortcut", "manage_window")
                        for step in remaining_plan
                    )
                    if needs_window:
                        revised = [
                            {
                                "action": "launch_application",
                                "description": f"Launch {target_app} since no active window was found",
                                "parameters": {"application_name": target_app},
                                "requires_confirmation": False,
                            }
                        ]
                        revised.extend(remaining_plan)
                        return revised, f"No open window for '{target_app}'; prepended launch_application step."
            elif windows and isinstance(windows, list):
                top_hwnd = windows[0].get("hwnd") if isinstance(windows[0], dict) else None
                if top_hwnd:
                    revised = []
                    for step in remaining_plan:
                        step_copy = dict(step)
                        params = dict(step_copy.get("parameters", {}))
                        if step_copy.get("action") in ("type_text", "press_key", "send_shortcut", "manage_window"):
                            if not params.get("hwnd") or params.get("hwnd") == 0:
                                params["hwnd"] = int(top_hwnd)
                                step_copy["parameters"] = params
                        revised.append(step_copy)
                    return revised, f"Bound HWND {top_hwnd} to subsequent window interaction steps."

        return remaining_plan, "No dynamic re-planning required."
