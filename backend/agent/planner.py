
from typing import Optional, List, Dict, Any, Tuple
import json
import ollama


class PlannerError(Exception):
    """Raised when agent planning fails."""


MAX_PLAN_STEPS = 10


class AgentPlanner:

    def __init__(self, model="qwen3:4b"):
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
            "general_conversation": ["respond"],
            "open_application": [
                "launch_application",
                "open_application",
                "find_windows",
                "manage_window",
                "type_text",
                "press_key",
                "send_shortcut",
                "respond",
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
            ],
            "unknown": ["ask_clarification", "respond"],
        }

    def create_plan(self, user_input, intent, context=None):

        if not user_input or not user_input.strip():
            raise PlannerError("User input cannot be empty.")

        if intent not in self.intent_actions:
            raise PlannerError(f"Unsupported intent: {intent}")

        allowed_for_intent = self.intent_actions[intent]

        context_prompt = f"\nPrevious conversation context:\n{context}\n" if context else ""

        system_prompt = f"""
You are the planning module of VoxPilot,
a personal AI desktop assistant.

Your task is to convert the user's request into
a structured action plan.
{context_prompt}
The user's classified intent is: {intent}

You may only use these action types:
{allowed_for_intent}

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
2. Do not generate shell commands or executable code.
3. Do not invent file paths, application paths, or URLs.
4. Put the information supplied by the user into parameters.
5. If required information is missing, use ask_clarification.
6. Set requires_confirmation to true for destructive
   or consequential operations.
7. Do not execute any action.
8. Keep the plan simple and concise.
"""

        try:
            result = ollama.chat(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": system_prompt
                    },
                    {
                        "role": "user",
                        "content": user_input.strip()
                    }
                ],
                format="json",
                options={"temperature": 0}
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
                    raise PlannerError(
                        f"Unsupported action: {action}"
                    )

                if action not in allowed_for_intent:
                    raise PlannerError(
                        f"Action {action} is not allowed for intent {intent}."
                    )

                parameters = step.get("parameters", {})

                if not isinstance(parameters, dict):
                    raise PlannerError(
                        "Step parameters must be an object."
                    )

                requires_confirmation = step.get(
                    "requires_confirmation", False
                )

                if not isinstance(requires_confirmation, bool):
                    raise PlannerError(
                        "requires_confirmation must be a boolean."
                    )

                description = step.get("description", "")

                if not isinstance(description, str):
                    raise PlannerError(
                        "Step description must be a string."
                    )

                validated_steps.append({
                    "action": action,
                    "description": description,
                    "parameters": parameters,
                    "requires_confirmation": requires_confirmation
                })

            response = plan_data.get("response", "")

            if not isinstance(response, str):
                raise PlannerError("Plan response must be a string.")

            return {
                "steps": validated_steps,
                "response": response
            }

        except PlannerError:
            raise

        except Exception as error:
            raise PlannerError(
                f"Could not create agent plan: {error}"
            ) from error

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

        # 3. Application launched -> Bind window HWND if subsequent step needs HWND
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

        # 4. Window discovered via find_windows -> Bind HWND into subsequent steps
        if last_action == "find_windows":
            windows = last_data.get("windows") or []
            if windows and isinstance(windows, list):
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
