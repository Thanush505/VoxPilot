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

from backend.agent.tool_registry import ToolRegistry, get_shared_tool_registry

logger = logging.getLogger(__name__)


class PlannerError(Exception):
    """Raised when agent planning fails."""
    pass


MAX_PLAN_STEPS = 10

ACTION_SCHEMAS_DOC: Dict[str, str] = {
    "launch_application": (
        "Launch an approved application (notepad, calculator, chrome, vscode). "
        "Parameters: {'application_name': str} (REQUIRED. Example: {'application_name': 'notepad'}). "
        "CRITICAL: Do NOT use 'application' or 'app_name'; you MUST use 'application_name'."
    ),
    "open_application": (
        "Alias for launch_application. "
        "Parameters: {'application_name': str} (REQUIRED. Example: {'application_name': 'notepad'}). "
        "CRITICAL: Do NOT use 'application' or 'app_name'; you MUST use 'application_name'."
    ),
    "find_windows": (
        "Discover open top-level windows for an approved application. "
        "Parameters: {'application_name': Optional[str], 'title_pattern': Optional[str]}."
    ),
    "manage_window": (
        "Manage an approved window. "
        "Parameters: {'action': str, 'application_name': Optional[str], 'hwnd': Optional[int]}. "
        "Action must be one of: 'activate', 'minimize', 'maximize', 'restore'."
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
            "unknown": ["ask_clarification", "respond"],
        }

    def _deterministic_plan(self, user_input: str, intent: str) -> Optional[Dict[str, Any]]:
        """Construct deterministic plans for standard natural-language desktop requests."""
        clean = user_input.strip()
        lower = clean.lower()

        # 1. Direct application launching ("Open Notepad", "Launch Calculator", "Start VS Code")
        launch_match = re.search(
            r"^(?:open|launch|start|run)\s+(notepad|calculator|chrome|vs\s*code|vscode)\.?$",
            lower,
        )
        if launch_match and not ("and type" in lower or "and write" in lower):
            raw_app = launch_match.group(1).replace(" ", "")
            app_name = "vscode" if raw_app in ("vscode", "vs code") else raw_app
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

        # 7. Create text file ("Create a text file named notes.txt")
        create_file_match = re.search(
            r"^(?:create|make)\s+(?:a\s+)?(?:new\s+)?(?:text\s+)?file\s+(?:named\s+|called\s+)?([a-zA-Z0-9_\-\.]+\.[a-zA-Z0-9]+)\.?$",
            lower,
        )
        if create_file_match:
            filename = create_file_match.group(1)
            return {
                "steps": [
                    {
                        "action": "create_file",
                        "description": f"Create text file '{filename}'",
                        "parameters": {"file_path": filename, "content": ""},
                        "requires_confirmation": False,
                    }
                ],
                "response": f"Creating text file '{filename}'.",
            }

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

        return None

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
            result = ollama.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_input.strip()},
                ],
                format="json",
                options={"temperature": 0},
            )

            response_text = result.message.content
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
