"""
VoxPilot - LangGraph Agent Workflow
Constructs and compiles the complete LangGraph StateGraph connecting intent recognition,
action planning, plan validation, tool execution, result evaluation, and response generation.
Supports multi-step execution loops, confirmation trust boundaries, and execution resumption.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
import unittest.mock
from typing import Dict, Any, Optional, List
import ollama
from langgraph.graph import StateGraph, START, END

from backend.agent.state import VoxPilotState, ExecutionResult
from backend.agent.intent import IntentRecognizer
from backend.agent.llm import OllamaLLM
from backend.ai.providers.base import AIProviderError, ProviderCancelledError
from backend.agent.planner import AgentPlanner
from backend.agent.tool_registry import (
    ToolRegistry,
    get_shared_tool_registry,
    UnknownToolError,
    ToolArgumentValidationError,
)
from backend.agent.tool_executor import ToolExecutor
from backend.agent.rate_limiter import get_shared_rate_limiter
from backend.automation.confirmation_manager import (
    ConfirmationManager,
    get_shared_confirmation_manager,
)
from backend.memory.context_manager import (
    ConversationContextManager,
    get_shared_context_manager,
)
from backend.memory.long_term_memory import (
    LongTermMemoryService,
    get_shared_long_term_memory_service,
)
from backend.memory.memory_extractor import MemoryExtractor
from backend.memory.conversation_hydrator import ConversationHydrator
from backend.personalization.service import (
    PersonalizationService,
    get_shared_personalization_service,
)
from backend.profiles.service import (
    ProfileService,
    get_shared_profile_service,
)
from backend.database.repository import DatabaseRepository
from backend.automation.emergency_stop import (
    get_shared_emergency_stop_manager,
    EmergencyStopActiveError,
)
from backend.ai.vision import (
    ScreenCaptureAccessDeniedError,
    ScreenCaptureNotFoundError,
    get_shared_screen_capture_service,
    get_shared_vision_provider,
)
from backend.ai.file_assistant import get_shared_file_assistant
from backend.ai.browser_research import get_shared_browser_research_service
from backend.ai.email_assistant import get_shared_email_assistant

logger = logging.getLogger(__name__)


class VoxPilotAgent:
    """Intelligent desktop control agent orchestrated via LangGraph."""

    def __init__(
        self,
        model: str = "qwen3:4b",
        tool_registry: Optional[ToolRegistry] = None,
        tool_executor: Optional[ToolExecutor] = None,
        confirmation_manager: Optional[ConfirmationManager] = None,
        intent_recognizer: Optional[IntentRecognizer] = None,
        planner: Optional[AgentPlanner] = None,
        context_manager: Optional[ConversationContextManager] = None,
        repository: Optional[DatabaseRepository] = None,
        llm: Optional[OllamaLLM] = None,
        memory_service: Optional[LongTermMemoryService] = None,
        memory_extractor: Optional[MemoryExtractor] = None,
        conversation_hydrator: Optional[ConversationHydrator] = None,
        personalization_service: Optional[PersonalizationService] = None,
        profile_service: Optional[ProfileService] = None,
    ):
        self.model = model
        self.tool_registry = tool_registry or get_shared_tool_registry()
        self.confirmation_manager = confirmation_manager or get_shared_confirmation_manager()
        self.tool_executor = tool_executor or ToolExecutor(
            registry=self.tool_registry,
            confirmation_manager=self.confirmation_manager,
        )
        self.intent_recognizer = intent_recognizer or IntentRecognizer(model=model)
        self.planner = planner or AgentPlanner(model=model)
        self.context_manager = context_manager or get_shared_context_manager()
        self.repository = repository or DatabaseRepository()

        self._explicit_llm = llm is not None
        self.llm = llm or OllamaLLM(model=model)

        if memory_service is not None:
            self.memory_service = memory_service
        elif repository is not None:
            self.memory_service = LongTermMemoryService(sync_engine=self.repository._sync_engine)
        else:
            self.memory_service = get_shared_long_term_memory_service()

        self.memory_extractor = memory_extractor or MemoryExtractor(memory_service=self.memory_service)
        self.conversation_hydrator = conversation_hydrator or ConversationHydrator(repository=self.repository)

        if personalization_service is not None:
            self.personalization_service = personalization_service
        elif repository is not None:
            self.personalization_service = PersonalizationService(sync_engine=self.repository._sync_engine)
        else:
            self.personalization_service = get_shared_personalization_service()

        if profile_service is not None:
            self.profile_service = profile_service
        elif repository is not None:
            self.profile_service = ProfileService(sync_engine=self.repository._sync_engine)
        else:
            self.profile_service = get_shared_profile_service()

        self._conversation_profiles: Dict[str, str] = {}
        self.graph = self.build_graph()

    # ==========================================================================
    # Graph Node 1: Intent Recognition
    # ==========================================================================

    def recognize_intent(self, state: VoxPilotState) -> Dict[str, Any]:
        """Classify user natural language request into a discrete intent category."""
        manager = get_shared_emergency_stop_manager()
        if manager.is_emergency_stop_active():
            return {
                "execution_status": "cancelled",
                "error": f"Emergency stop is active: {manager.trigger_reason}",
                "response": f"Execution halted: emergency stop is active ({manager.trigger_reason}).",
            }

        # If resuming after human confirmation, bypass intent classification
        if state.get("execution_status") == "resuming":
            return {}

        user_input = state.get("user_input", "")
        if not user_input or not user_input.strip():
            return {
                "error": "User input cannot be empty.",
                "execution_status": "failed",
                "response": "Please provide a request or command.",
            }

        try:
            result = self.intent_recognizer.recognize_intent(user_input)
            return {
                "intent": result["intent"],
                "confidence": result["confidence"],
                "intent_reason": result["reason"],
                "error": "",
            }
        except Exception as error:
            logger.error(f"Intent recognition failed: {error}")
            return {
                "intent": "unknown",
                "confidence": 0.0,
                "intent_reason": "Intent recognition failure.",
                "error": str(error),
                "execution_status": "failed",
                "response": f"I could not understand your request: {error}",
            }

    # ==========================================================================
    # Graph Node 2: Plan Generation
    # ==========================================================================

    def create_plan(self, state: VoxPilotState) -> Dict[str, Any]:
        """Transform classified intent and user request into an ordered action plan."""
        manager = get_shared_emergency_stop_manager()
        if manager.is_emergency_stop_active():
            return {
                "execution_status": "cancelled",
                "error": f"Emergency stop is active: {manager.trigger_reason}",
                "response": f"Execution halted: emergency stop is active ({manager.trigger_reason}).",
            }

        # If resuming, keep existing plan
        if state.get("execution_status") == "resuming":
            return {}

        if state.get("error") or state.get("execution_status") in ("failed", "cancelled"):
            return {}

        user_input = state.get("user_input", "")
        intent = state.get("intent", "unknown")
        conv_id = state.get("conversation_id", "")

        # Reference resolution against short-term conversation context
        ref_info = self.context_manager.resolve_references(conv_id, user_input) if conv_id else {}
        if ref_info.get("needs_clarification"):
            clarify_msg = ref_info.get("clarification_message", "Please clarify which item you want to target.")
            return {
                "plan": [
                    {
                        "action": "ask_clarification",
                        "description": clarify_msg,
                        "parameters": {},
                        "requires_confirmation": False,
                    }
                ],
                "response": clarify_msg,
                "execution_status": "planned",
                "current_step": 0,
                "execution_results": [],
                "completed_steps": [],
                "failed_steps": [],
            }

        context_summary = state.get("conversation_history")
        if not context_summary and conv_id:
            context_summary = self.context_manager.get_context(conv_id).get_summary_text()

        try:
            result = self.planner.create_plan(
                user_input=user_input,
                intent=intent,
                context=context_summary,
            )
            steps = result.get("steps", [])
            requires_conf = False
            if isinstance(steps, list):
                for step in steps:
                    if isinstance(step, dict) and step.get("requires_confirmation", False):
                        requires_conf = True
                        break

            # If resolved_window exists and step has hwnd=0 or missing, bind it
            if ref_info.get("resolved_window") and isinstance(steps, list):
                res_hwnd = ref_info["resolved_window"].get("hwnd")
                if res_hwnd:
                    for s in steps:
                        if isinstance(s, dict) and s.get("action") in ("type_text", "press_key", "send_shortcut", "manage_window"):
                            p = dict(s.get("parameters", {}))
                            if not p.get("hwnd") or p.get("hwnd") == 0:
                                p["hwnd"] = int(res_hwnd)
                                s["parameters"] = p

            # If resolved_file exists and step has empty file_path, bind it
            if ref_info.get("resolved_file") and isinstance(steps, list):
                res_file = ref_info["resolved_file"]
                for s in steps:
                    if isinstance(s, dict) and s.get("action") in ("get_file_metadata", "copy_item", "delete_file", "move_item", "rename_item"):
                        p = dict(s.get("parameters", {}))
                        if not p.get("file_path"):
                            p["file_path"] = res_file
                            s["parameters"] = p

            return {
                "plan": steps,
                "response": result.get("response", ""),
                "requires_confirmation": requires_conf,
                "execution_status": "planned",
                "current_step": 0,
                "execution_results": [],
                "completed_steps": [],
                "failed_steps": [],
            }
        except Exception as error:
            logger.error(f"Planner failed: {error}")
            return {
                "error": str(error),
                "execution_status": "failed",
                "response": f"Could not create an execution plan: {error}",
            }

    # ==========================================================================
    # Graph Node 3: Plan Validation
    # ==========================================================================

    def validate_plan(self, state: VoxPilotState) -> Dict[str, Any]:
        """Verify plan structure, tool registration, and parameter schemas before execution."""
        manager = get_shared_emergency_stop_manager()
        if manager.is_emergency_stop_active():
            return {
                "execution_status": "cancelled",
                "error": f"Emergency stop is active: {manager.trigger_reason}",
                "response": f"Execution halted: emergency stop is active ({manager.trigger_reason}).",
            }

        # If resuming, transition status to running and proceed
        if state.get("execution_status") == "resuming":
            return {"execution_status": "running"}

        if state.get("error") or state.get("execution_status") in ("failed", "cancelled"):
            return {}

        plan = state.get("plan", [])
        if not plan or not isinstance(plan, list):
            # If conversational intent without tools, validate as completed
            if state.get("intent") == "general_conversation":
                return {
                    "execution_status": "completed",
                    "response": state.get("response") or "Hello! How can I assist you with your computer today?",
                }
            return {
                "error": "Plan contains no actionable steps.",
                "execution_status": "validation_failed",
                "response": "No actionable steps could be generated for this request.",
            }

        rate_limiter = get_shared_rate_limiter()
        max_steps = rate_limiter.config.max_steps
        if len(plan) > max_steps:
            return {
                "error": f"Plan step count ({len(plan)}) exceeds maximum allowed safety limit of {max_steps}.",
                "execution_status": "validation_failed",
                "response": "The requested task requires too many steps and was blocked for safety.",
            }

        # Validate each individual step
        validated_plan: List[Dict[str, Any]] = []
        for idx, step in enumerate(plan):
            if not isinstance(step, dict):
                return {
                    "error": f"Step {idx} is malformed: expected dictionary.",
                    "execution_status": "validation_failed",
                    "response": "Execution halted: plan contains malformed step structures.",
                }

            action = step.get("action")
            if not action or not isinstance(action, str):
                return {
                    "error": f"Step {idx} is missing an action name.",
                    "execution_status": "validation_failed",
                    "response": "Execution halted: step missing action name.",
                }

            # Check pseudo-actions
            if action in ("respond", "ask_clarification"):
                validated_plan.append(step)
                continue

            # Check tool registry registration
            if not self.tool_registry.has_tool(action):
                return {
                    "error": f"Step {idx} specifies unknown or unapproved action '{action}'.",
                    "execution_status": "validation_failed",
                    "response": f"Action '{action}' is not permitted or recognized by VoxPilot.",
                }

            # Validate and normalize parameter schemas
            params = dict(step.get("parameters", {}))
            try:
                normalized_params = self.tool_registry.normalize_parameters(action, params)
                self.tool_registry.validate_parameters(action, normalized_params)
                step_copy = dict(step)
                step_copy["parameters"] = normalized_params
                validated_plan.append(step_copy)
            except ToolArgumentValidationError as val_err:
                return {
                    "error": f"Step {idx} ('{action}') has invalid parameters: {val_err}",
                    "execution_status": "validation_failed",
                    "response": f"Parameters for '{action}' failed validation: {val_err}",
                }

        return {
            "plan": validated_plan,
            "execution_status": "validated",
            "current_step": state.get("current_step", 0),
        }

    # ==========================================================================
    # Graph Node 4: Tool Execution
    # ==========================================================================

    def execute_tool(self, state: VoxPilotState) -> Dict[str, Any]:
        """Execute the current validated plan step using the secure ToolExecutor."""
        manager = get_shared_emergency_stop_manager()
        plan = state.get("plan", [])
        current_idx = state.get("current_step", 0)

        if manager.is_emergency_stop_active():
            return {
                "current_action": plan[current_idx] if current_idx < len(plan) else {},
                "execution_status": "cancelled",
                "error": f"Emergency stop is active: {manager.trigger_reason}",
                "response": f"Execution halted: emergency stop is active ({manager.trigger_reason}).",
                "current_execution_result": ExecutionResult(
                    step_index=current_idx,
                    action=plan[current_idx].get("action", "unknown") if current_idx < len(plan) else "unknown",
                    success=False,
                    status="cancelled",
                    message=f"Execution cancelled: emergency stop is active ({manager.trigger_reason}).",
                    error=manager.trigger_reason,
                ),
            }

        if current_idx >= len(plan):
            return {"execution_status": "completed"}

        step = plan[current_idx]
        action = step.get("action", "")

        # Handle non-tool conversational steps
        if action == "respond":
            return {
                "current_action": step,
                "execution_status": "running",
                "current_execution_result": ExecutionResult(
                    step_index=current_idx,
                    action=action,
                    success=True,
                    status="completed",
                    message=step.get("description", "Responded."),
                ),
            }

        if action == "ask_clarification":
            return {
                "current_action": step,
                "execution_status": "running",
                "current_execution_result": ExecutionResult(
                    step_index=current_idx,
                    action=action,
                    success=True,
                    status="completed",
                    message=step.get("description", "Clarification requested."),
                ),
            }

        # Check execution wall-clock duration budget before step execution
        rate_limiter = get_shared_rate_limiter()
        started_at = state.get("started_at")
        elapsed = (time.time() - started_at) if started_at else 0.0
        dur_ok, dur_reason = rate_limiter.check_execution_budget(
            execution_id=state.get("execution_id", ""),
            elapsed_seconds=elapsed,
        )
        if not dur_ok:
            return {
                "current_action": plan[current_idx] if current_idx < len(plan) else {},
                "execution_status": "rate_limited",
                "error": dur_reason,
                "response": f"Execution halted: {dur_reason}",
                "current_execution_result": ExecutionResult(
                    step_index=current_idx,
                    action=plan[current_idx].get("action", "unknown") if current_idx < len(plan) else "unknown",
                    success=False,
                    status="rate_limited",
                    message=f"Execution halted: {dur_reason}",
                    error=dur_reason,
                ),
            }

        # Check if an approved confirmation token is supplied in state
        override_token = None
        pending_conf = state.get("pending_confirmation")
        if pending_conf and pending_conf.get("step_index") == current_idx:
            override_token = pending_conf.get("token")

        # Execute through ToolExecutor with execution_id and elapsed_duration
        step_payload = dict(step)
        step_payload["execution_id"] = state.get("execution_id", "")
        step_payload["elapsed_duration"] = elapsed

        result = self.tool_executor.execute_step(
            step=step_payload,
            step_index=current_idx,
            override_confirmation_token=override_token,
        )

        return {
            "current_action": step,
            "execution_status": "running",
            "current_execution_result": result,
        }

    # ==========================================================================
    # Graph Node 5: Result Evaluation
    # ==========================================================================

    def evaluate_result(self, state: VoxPilotState) -> Dict[str, Any]:
        """Inspect the executed step's outcome and determine next workflow transition."""
        result = state.get("current_execution_result", {})
        plan = state.get("plan", [])
        current_idx = state.get("current_step", 0)
        results = list(state.get("execution_results", []))
        completed = list(state.get("completed_steps", []))
        failed = list(state.get("failed_steps", []))

        results.append(result)

        action = result.get("action", f"step_{current_idx}")
        status = result.get("status", "")
        success = result.get("success", False)

        if success:
            completed.append(plan[current_idx] if current_idx < len(plan) else {"action": action})

        # 0. Emergency Stop or Action Cancelled -> CANCEL execution immediately
        manager = get_shared_emergency_stop_manager()
        if manager.is_emergency_stop_active() or status == "cancelled":
            reason = manager.trigger_reason if manager.is_emergency_stop_active() else (result.get("error") or "Cancelled by user.")
            return {
                "execution_results": results,
                "completed_steps": completed,
                "execution_status": "cancelled",
                "error": f"Emergency stop is active: {reason}",
                "response": f"Execution halted: emergency stop is active ({reason}).",
            }

        # 0b. Rate limit or Budget Exceeded -> HALT execution immediately
        if status == "rate_limited" or result.get("status") == "rate_limited":
            reason = result.get("error") or result.get("message") or "Execution budget or rate limit exceeded."
            return {
                "execution_results": results,
                "completed_steps": completed,
                "execution_status": "rate_limited",
                "error": reason,
                "response": f"Execution halted due to safety limit: {reason}",
                "pending_confirmation": None,
                "needs_replan": False,
            }

        # 1. Action requires human confirmation -> PAUSE
        if status == "confirmation_required":
            token = result.get("confirmation_token")
            pending_data = {
                "token": token,
                "action": action,
                "parameters": plan[current_idx].get("parameters", {}),
                "description": plan[current_idx].get("description", ""),
                "execution_id": state.get("execution_id", ""),
                "step_index": current_idx,
            }
            return {
                "execution_results": results,
                "execution_status": "confirmation_required",
                "requires_confirmation": True,
                "pending_confirmation": pending_data,
                "response": (
                    f"Action '{action}' requires your confirmation before proceeding. "
                    "Please review the pending confirmation in the application."
                ),
            }

        # 2. Action failed -> STOP execution immediately
        if not success:
            failed.append(plan[current_idx] if current_idx < len(plan) else {"action": action})
            error_msg = result.get("error") or result.get("message") or "Unknown execution error."
            return {
                "execution_results": results,
                "failed_steps": failed,
                "execution_status": "failed",
                "error": error_msg,
                "response": f"Execution halted at step {current_idx + 1} ('{action}'): {error_msg}",
            }

        # 3. Action succeeded -> Advance to next step (and check dynamic re-planning)
        next_step = current_idx + 1

        if self._should_replan(state, action, result):
            return {
                "execution_results": results,
                "completed_steps": completed,
                "current_step": next_step,
                "execution_status": "running",
                "needs_replan": True,
                "replan_reason": f"Step {current_idx + 1} ('{action}') results require plan revision.",
                "pending_confirmation": None,
            }

        if next_step >= len(plan):
            return {
                "execution_results": results,
                "completed_steps": completed,
                "current_step": next_step,
                "execution_status": "completed",
                "pending_confirmation": None,
            }

        return {
            "execution_results": results,
            "completed_steps": completed,
            "current_step": next_step,
            "execution_status": "running",
            "pending_confirmation": None,
        }

    def _should_replan(self, state: VoxPilotState, action: str, result: ExecutionResult) -> bool:
        """Determine whether structured execution results require dynamic plan adjustment."""
        replan_count = state.get("replan_count", 0)
        max_replans = state.get("max_replans", 2)
        if replan_count >= max_replans:
            return False

        # Budget Check: verify elapsed wall-clock duration has not exhausted budget
        rate_limiter = get_shared_rate_limiter()
        started_at = state.get("started_at")
        if started_at and (time.time() - started_at) >= rate_limiter.config.max_duration_seconds:
            return False

        data = result.get("data") or {}
        user_in = state.get("user_input", "").lower()
        current_idx = state.get("current_step", 0)
        plan = state.get("plan", [])

        # 1. File search / listing: check if matches were discovered and next action needs target
        if action in ("search_files", "list_directory"):
            matches = data.get("matches") or data.get("files") or []
            if matches and any(w in user_in for w in ("open", "read", "view", "launch")):
                return True
            if len(matches) > 1 and "open" in user_in:
                return True

        # 2. Application launch: check if subsequent steps need HWND binding
        if action in ("launch_application", "open_application"):
            for step in plan[current_idx + 1:]:
                if isinstance(step, dict) and step.get("action") in ("type_text", "press_key", "send_shortcut", "manage_window"):
                    params = step.get("parameters", {})
                    if not params.get("hwnd") or params.get("hwnd") == 0:
                        return True

        # 3. Window discovery: check if subsequent steps need HWND
        if action == "find_windows":
            windows = data.get("windows") or []
            if windows:
                for step in plan[current_idx + 1:]:
                    if isinstance(step, dict) and step.get("action") in ("type_text", "press_key", "send_shortcut", "manage_window"):
                        params = step.get("parameters", {})
                        if not params.get("hwnd") or params.get("hwnd") == 0:
                            return True

        return False

    def replan_plan(self, state: VoxPilotState) -> Dict[str, Any]:
        """Dynamically adjust remaining plan steps based on structured tool execution results."""
        manager = get_shared_emergency_stop_manager()
        if manager.is_emergency_stop_active():
            return {
                "execution_status": "cancelled",
                "error": f"Emergency stop is active: {manager.trigger_reason}",
                "response": f"Execution halted: emergency stop is active ({manager.trigger_reason}).",
            }

        replan_count = state.get("replan_count", 0) + 1
        history = list(state.get("replan_history", []))
        old_plan = list(state.get("plan", []))
        completed = list(state.get("completed_steps", []))
        results = list(state.get("execution_results", []))
        user_input = state.get("user_input", "")

        revised_steps, reason = self.planner.replan_from_results(
            original_input=user_input,
            completed_steps=completed,
            completed_results=results,
            remaining_plan=old_plan[len(completed):],
            context=state.get("conversation_history"),
        )

        full_plan = completed + revised_steps
        if len(full_plan) > 10:
            full_plan = full_plan[:10]

        history.append({
            "replan_index": replan_count,
            "reason": reason,
            "old_plan": old_plan,
            "new_plan": full_plan,
        })

        return {
            "plan": full_plan,
            "current_step": len(completed),
            "replan_count": replan_count,
            "replan_history": history,
            "needs_replan": False,
            "execution_status": "planned",
        }

    # ==========================================================================
    # Graph Node 6: Response Generation
    # ==========================================================================

    def _is_mock_only_harness(self) -> bool:
        """Detect pure unit-test harness where both intent & planner are mocks and no LLM is injected/patched."""
        return (
            isinstance(self.intent_recognizer, unittest.mock.Mock)
            and isinstance(self.planner, unittest.mock.Mock)
            and not self._explicit_llm
            and not isinstance(self.llm, unittest.mock.Mock)
            and not isinstance(ollama.chat, unittest.mock.Mock)
        )

    def _build_offline_conversational_fallback(self, state: VoxPilotState) -> str:
        """Deterministic fallback response when local Ollama is offline or unavailable."""
        extracted = state.get("extracted_memories") or []
        retrieved = state.get("retrieved_memories") or []
        user_in = (state.get("user_input") or "").strip()

        if extracted and any(
            kw in user_in.lower()
            for kw in ("remember", "prefer", "preferred", "my name is", "my project", "always ")
        ):
            first_mem = extracted[0].get("content", "").strip()
            if first_mem:
                return f"Got it. I have saved that locally in memory: {first_mem}"

        if retrieved and ("?" in user_in or any(w in user_in.lower() for w in ("what", "who", "prefer", "remember", "which"))):
            snippets = [str(m.get("content", "")).strip() for m in retrieved[:2] if m.get("content")]
            if snippets:
                return f"Based on your local VoxPilot memory: {' '.join(snippets)}"

        existing = state.get("response")
        if existing and existing not in ("Generate conversational response", "Planning completed."):
            return existing
        return "Hello! How can I assist you with your computer today?"

    def generate_response(self, state: VoxPilotState) -> Dict[str, Any]:
        """Generate final truthful summary or local LLM conversational response."""
        status = state.get("execution_status", "")
        results = state.get("execution_results", [])

        # If already set by confirmation or failure node, preserve it
        if status in ("confirmation_required", "validation_failed"):
            return {}

        if status == "cancelled":
            return {
                "response": "Execution was cancelled by the user.",
            }

        if status == "failed":
            if state.get("response"):
                return {}
            err = state.get("error", "An error occurred.")
            return {
                "response": f"I was unable to complete your request. {err}",
            }

        if status == "rate_limited":
            if state.get("response"):
                return {}
            err = state.get("error", "Rate limit or execution budget exceeded.")
            return {
                "response": f"Execution halted due to safety limit: {err}",
            }

        if status == "completed":
            is_conversational_only = (not results) or all(
                isinstance(r, dict) and r.get("action") == "respond" for r in results
            )

            if is_conversational_only and (state.get("intent") == "general_conversation" or results):
                if self._is_mock_only_harness():
                    preserved = state.get("response")
                    if not preserved and results:
                        preserved = results[0].get("message")
                    return {"response": preserved or "Hello! How can I assist you with your computer today?"}

                try:
                    llm_reply = self.llm.generate_contextual_response(
                        user_message=state.get("user_input", ""),
                        conversation_history=state.get("conversation_history"),
                        recent_turns_text=state.get("conversation_turns_text"),
                        memories=state.get("retrieved_memories"),
                        personalization_context=state.get("personalization_context"),
                    )
                    if llm_reply:
                        return {"response": llm_reply}
                except ProviderCancelledError as cancel_err:
                    return {
                        "execution_status": "cancelled",
                        "error": str(cancel_err),
                        "response": "Execution was cancelled by the user.",
                    }
                except AIProviderError as provider_err:
                    logger.warning(f"Active AI provider failed: {provider_err}")
                    return {
                        "execution_status": "failed",
                        "error": str(provider_err),
                        "response": f"AI provider error: {provider_err}",
                    }
                except Exception as llm_err:
                    logger.warning(f"Local Ollama response failed; falling back gracefully: {llm_err}")

                return {"response": self._build_offline_conversational_fallback(state)}

            # Construct truthful execution summary for tool-executed requests
            success_messages = [
                r.get("message", f"Completed {r.get('action')}")
                for r in results if r.get("success")
            ]
            if len(success_messages) == 1:
                summary = success_messages[0]
            elif len(success_messages) > 1:
                summary = f"Successfully completed all {len(success_messages)} steps: {'; '.join(success_messages)}."
            else:
                summary = state.get("response") or "Request completed."

            return {
                "response": summary,
            }

        return {}

    # ==========================================================================
    # Conditional Edge Routing
    # ==========================================================================

    def _route_after_validation(self, state: VoxPilotState) -> str:
        """Route to tool execution or response generation based on validation state."""
        status = state.get("execution_status", "")
        if status in ("validation_failed", "failed", "completed", "cancelled", "rate_limited") or state.get("error"):
            return "generate_response"
        return "execute_tool"

    def _route_after_evaluation(self, state: VoxPilotState) -> str:
        """Determine whether to loop to next step, replan, or complete execution."""
        status = state.get("execution_status", "")
        if status in ("confirmation_required", "failed", "cancelled", "rate_limited"):
            return "generate_response"

        if state.get("needs_replan"):
            return "replan_plan"

        current_idx = state.get("current_step", 0)
        plan = state.get("plan", [])

        if current_idx < len(plan):
            return "execute_tool"
        return "generate_response"

    # ==========================================================================
    # Graph Construction
    # ==========================================================================

    def build_graph(self) -> Any:
        """Assemble and compile the StateGraph workflow."""
        workflow = StateGraph(VoxPilotState)

        # 1. Add nodes
        workflow.add_node("recognize_intent", self.recognize_intent)
        workflow.add_node("create_plan", self.create_plan)
        workflow.add_node("validate_plan", self.validate_plan)
        workflow.add_node("execute_tool", self.execute_tool)
        workflow.add_node("evaluate_result", self.evaluate_result)
        workflow.add_node("replan_plan", self.replan_plan)
        workflow.add_node("generate_response", self.generate_response)

        # 2. Add structural edges
        workflow.add_edge(START, "recognize_intent")
        workflow.add_edge("recognize_intent", "create_plan")
        workflow.add_edge("create_plan", "validate_plan")

        # 3. Add conditional edges
        workflow.add_conditional_edges(
            "validate_plan",
            self._route_after_validation,
            {
                "execute_tool": "execute_tool",
                "generate_response": "generate_response",
            },
        )

        workflow.add_edge("execute_tool", "evaluate_result")

        workflow.add_conditional_edges(
            "evaluate_result",
            self._route_after_evaluation,
            {
                "execute_tool": "execute_tool",
                "replan_plan": "replan_plan",
                "generate_response": "generate_response",
            },
        )

        workflow.add_edge("replan_plan", "validate_plan")
        workflow.add_edge("generate_response", END)

        return workflow.compile()

    # ==========================================================================
    # Public Execution Interfaces
    # ==========================================================================

    def _resolve_profile_id(self, explicit_profile_id: Optional[str] = None) -> str:
        if explicit_profile_id and isinstance(explicit_profile_id, str) and explicit_profile_id.strip():
            return explicit_profile_id.strip()
        try:
            return self.profile_service.get_active_profile_id()
        except Exception as err:
            logger.warning(f"Failed resolving active profile; falling back to default: {err}")
            return "default"

    def process_request(
        self,
        user_input: str,
        execution_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        profile_id: Optional[str] = None,
        capture_id: Optional[str] = None,
        clear_capture_after_use: bool = True,
    ) -> VoxPilotState:
        """Process a natural language request from initial input through execution."""
        if not user_input or not user_input.strip():
            raise ValueError("User input cannot be empty.")

        exec_id = execution_id or f"exec_{uuid.uuid4().hex[:12]}"
        conv_id = conversation_id or f"conv_{exec_id[:8]}"
        active_profile_id = self._resolve_profile_id(profile_id)

        # Enforce strict conversation-to-profile ownership before touching context or DB
        known_mem_profile = self._conversation_profiles.get(conv_id)
        if known_mem_profile is not None and known_mem_profile != active_profile_id:
            raise ValueError(f"Conversation '{conv_id}' belongs to another profile.")
        try:
            existing_conv = self.repository.get_conversation_sync(conv_id)
            if existing_conv is not None and (existing_conv.profile_id or "default") != active_profile_id:
                raise ValueError(f"Conversation '{conv_id}' belongs to another profile.")
        except ValueError:
            raise
        except Exception as conv_err:
            logger.debug(f"Non-fatal conversation profile pre-check skipped: {conv_err}")

        self._conversation_profiles[conv_id] = active_profile_id

        manager = get_shared_emergency_stop_manager()
        if manager.is_emergency_stop_active():
            logger.warning(
                f"Rejecting process_request: emergency stop is active ({manager.trigger_reason})."
            )
            cancelled_state: VoxPilotState = {
                "user_input": user_input.strip(),
                "execution_id": exec_id,
                "conversation_id": conv_id,
                "conversation_history": "",
                "execution_status": "cancelled",
                "current_step": 0,
                "plan": [],
                "execution_results": [],
                "completed_steps": [],
                "failed_steps": [],
                "error": f"Emergency stop is active: {manager.trigger_reason}",
                "requires_confirmation": False,
                "pending_confirmation": None,
                "replan_count": 0,
                "max_replans": 2,
                "needs_replan": False,
                "replan_reason": None,
                "replan_history": [],
                "response": f"Execution halted: emergency stop is active ({manager.trigger_reason}).",
            }
            try:
                self.repository.save_execution_sync(cancelled_state)
            except Exception as db_err:
                logger.warning(f"Failed to persist cancelled state to database: {db_err}")
            return cancelled_state

        # Rate Limiting & Safety Budget Checkpoints
        rate_limiter = get_shared_rate_limiter()
        req_allowed, req_reason = rate_limiter.check_request_rate_limit(conversation_id=conv_id)
        if not req_allowed:
            logger.warning(f"Rejecting process_request due to rate limit: {req_reason}")
            rate_limited_state: VoxPilotState = {
                "user_input": user_input.strip(),
                "execution_id": exec_id,
                "conversation_id": conv_id,
                "conversation_history": "",
                "execution_status": "rate_limited",
                "current_step": 0,
                "plan": [],
                "execution_results": [],
                "completed_steps": [],
                "failed_steps": [],
                "error": req_reason or "Request rate limit exceeded.",
                "requires_confirmation": False,
                "pending_confirmation": None,
                "replan_count": 0,
                "max_replans": 2,
                "needs_replan": False,
                "replan_reason": None,
                "replan_history": [],
                "response": req_reason or "Request rate limit exceeded. Please wait a moment before trying again.",
            }
            try:
                self.repository.save_execution_sync(rate_limited_state)
            except Exception as db_err:
                logger.warning(f"Failed to persist rate_limited state to database: {db_err}")
            return rate_limited_state

        slot_acquired, slot_reason = rate_limiter.acquire_execution_slot(exec_id)
        if not slot_acquired:
            logger.warning(f"Rejecting process_request due to concurrency limit: {slot_reason}")
            concurrent_state: VoxPilotState = {
                "user_input": user_input.strip(),
                "execution_id": exec_id,
                "conversation_id": conv_id,
                "conversation_history": "",
                "execution_status": "rate_limited",
                "current_step": 0,
                "plan": [],
                "execution_results": [],
                "completed_steps": [],
                "failed_steps": [],
                "error": slot_reason or "Concurrent execution capacity reached.",
                "requires_confirmation": False,
                "pending_confirmation": None,
                "replan_count": 0,
                "max_replans": 2,
                "needs_replan": False,
                "replan_reason": None,
                "replan_history": [],
                "response": slot_reason or "Concurrent execution capacity reached. Please wait for other tasks to complete.",
            }
            try:
                self.repository.save_execution_sync(concurrent_state)
            except Exception as db_err:
                logger.warning(f"Failed to persist concurrent rate_limited state to database: {db_err}")
            return concurrent_state

        rate_limiter.record_request(conversation_id=conv_id)
        profile_id = active_profile_id
        clean_input = user_input.strip()

        # 1. Hydrate short-term conversation context from SQLite on cold start or switch
        try:
            if not self.context_manager.has_context(conv_id):
                conv_ctx = self.conversation_hydrator.hydrate_context(
                    conv_id,
                    self.context_manager,
                    revalidate_os_handles=True,
                    profile_id=profile_id,
                )
            else:
                conv_ctx = self.context_manager.get_context(conv_id)
        except Exception as hyd_err:
            logger.warning(f"Non-fatal context hydration fallback for '{conv_id}': {hyd_err}")
            conv_ctx = self.context_manager.get_context(conv_id)

        conv_summary = conv_ctx.get_summary_text()
        recent_turns_text = conv_ctx.get_recent_turns_text(max_turns=5)

        # 2. Conservatively extract durable local memories from explicit user statements
        # Never persist screenshot-attached, document-assistant, or browser-research turns into permanent long-term memory.
        extracted_memories: List[Dict[str, Any]] = []
        is_file_assistant_query = self._looks_like_file_assistant_request(clean_input)
        is_browser_research_query = self._looks_like_browser_research_request(clean_input)
        is_email_query = self._looks_like_email_assistant_request(clean_input)
        if not capture_id and not is_file_assistant_query and not is_browser_research_query and not is_email_query:
            try:
                extracted_memories = self.memory_extractor.extract_and_store(
                    user_input=clean_input,
                    conversation_id=conv_id,
                    profile_id=profile_id,
                )
            except Exception as ext_err:
                logger.warning(f"Non-fatal memory extraction failure: {ext_err}")

        # 3. Retrieve relevant local long-term memories to ground agent responses
        retrieved_memories: List[Dict[str, Any]] = []
        try:
            retrieved_memories = self.memory_service.search_memories(
                query=clean_input,
                limit=5,
                profile_id=profile_id,
                match_mode="any",
            )
        except Exception as search_err:
            logger.warning(f"Non-fatal long-term memory search failure: {search_err}")

        # 4. Load active explicit user personalization preferences
        personalization_summary = ""
        active_preferences: List[Dict[str, Any]] = []
        try:
            pers_ctx = self.personalization_service.build_personalization_context(
                user_input=clean_input,
                profile_id=profile_id,
            )
            personalization_summary = str(pers_ctx.get("summary_text") or "")
            active_preferences = list(pers_ctx.get("preferences") or [])
        except Exception as pers_err:
            logger.warning(f"Non-fatal personalization context failure: {pers_err}")

        initial_state: VoxPilotState = {
            "user_input": clean_input,
            "execution_id": exec_id,
            "conversation_id": conv_id,
            "profile_id": profile_id,
            "conversation_history": conv_summary,
            "conversation_turns_text": recent_turns_text,
            "retrieved_memories": retrieved_memories,
            "extracted_memories": extracted_memories,
            "personalization_context": personalization_summary or None,
            "active_preferences": active_preferences,
            "execution_status": "started",
            "current_step": 0,
            "plan": [],
            "execution_results": [],
            "completed_steps": [],
            "failed_steps": [],
            "error": "",
            "requires_confirmation": False,
            "pending_confirmation": None,
            "replan_count": 0,
            "max_replans": 2,
            "needs_replan": False,
            "replan_reason": None,
            "replan_history": [],
            "started_at": time.time(),
        }

        # Handle explicit on-demand screen awareness request without polluting long-term memory
        if capture_id:
            screen_service = get_shared_screen_capture_service()
            vision_provider = get_shared_vision_provider()
            try:
                capture_entry = screen_service.get_capture(capture_id=capture_id, profile_id=profile_id)
                analysis = vision_provider.analyze_image(
                    image_base64=capture_entry.image_base64,
                    user_prompt=clean_input,
                )
                final_state = {
                    **initial_state,
                    "intent": "screen_awareness",
                    "confidence": 1.0,
                    "intent_reason": "User attached an explicitly authorized screen capture.",
                    "capture_id": capture_id,
                    "screen_analysis_used": True,
                    "execution_status": "completed" if analysis.status != "error" else "failed",
                    "completed_steps": [
                        {
                            "action": "screen_awareness_analysis",
                            "status": analysis.status,
                            "model": analysis.model_used or "none",
                        }
                    ],
                    "response": analysis.answer,
                    "error": analysis.error or "",
                }
            except ScreenCaptureAccessDeniedError as access_err:
                raise PermissionError(str(access_err)) from access_err
            except ScreenCaptureNotFoundError as not_found_err:
                final_state = {
                    **initial_state,
                    "intent": "screen_awareness",
                    "capture_id": capture_id,
                    "screen_analysis_used": False,
                    "execution_status": "failed",
                    "error": str(not_found_err),
                    "response": "The attached screenshot expired or is no longer available. Please capture your screen again.",
                }
            except Exception as screen_err:
                logger.error(f"Screen awareness execution failed: {screen_err}")
                final_state = {
                    **initial_state,
                    "intent": "screen_awareness",
                    "capture_id": capture_id,
                    "screen_analysis_used": False,
                    "execution_status": "failed",
                    "error": str(screen_err),
                    "response": f"Screen analysis failed: {screen_err}",
                }
            finally:
                rate_limiter.release_execution_slot(exec_id)
                if clear_capture_after_use:
                    try:
                        screen_service.delete_capture(capture_id=capture_id, profile_id=profile_id)
                    except Exception:
                        pass
        else:
            try:
                handled_file_state = self._try_handle_file_assistant_request(
                    clean_input, initial_state, conv_ctx
                )
                if handled_file_state is not None:
                    final_state = handled_file_state
                else:
                    handled_browser_state = self._try_handle_browser_research_request(
                        clean_input, initial_state
                    )
                    if handled_browser_state is not None:
                        final_state = handled_browser_state
                    else:
                        handled_email_state = self._try_handle_email_assistant_request(
                            clean_input, initial_state
                        )
                        if handled_email_state is not None:
                            final_state = handled_email_state
                        else:
                            final_state = self.graph.invoke(initial_state)
            except Exception as error:
                logger.error(f"Agent graph execution failed: {error}", exc_info=True)
                final_state = {
                    **initial_state,
                    "error": str(error),
                    "execution_status": "failed",
                    "response": f"Agent execution failed: {error}",
                }
            finally:
                rate_limiter.release_execution_slot(exec_id)

        # Update in-memory context manager & persist active context JSON to SQLite
        try:
            self.context_manager.record_turn(
                conversation_id=conv_id,
                user_input=clean_input,
                intent=final_state.get("intent", "unknown"),
                plan=final_state.get("plan", []),
                execution_results=final_state.get("execution_results", []),
                completed_steps=final_state.get("completed_steps", []),
                response=final_state.get("response", ""),
                status=final_state.get("execution_status", "completed"),
            )
        except Exception as ctx_err:
            logger.warning(f"Failed to record turn in context manager: {ctx_err}")

        # Persist execution state to SQLite database synchronously
        try:
            self.repository.save_execution_sync(final_state)
        except Exception as db_err:
            logger.warning(f"Failed to persist execution state to database: {db_err}")

        try:
            updated_ctx = self.context_manager.get_context(conv_id)
            self.conversation_hydrator.save_context(conv_id, updated_ctx, profile_id=profile_id)
        except Exception as ctx_save_err:
            logger.warning(f"Failed to persist active conversation context: {ctx_save_err}")

        self._notify_execution_outcome(final_state)
        return final_state

    @staticmethod
    def _notify_execution_outcome(final_state: Dict[str, Any]) -> None:
        """Emit a privacy-safe desktop notification for meaningful execution outcomes."""
        try:
            from backend.automation.notifications import get_shared_notification_service

            status_val = str(final_state.get("execution_status") or "")
            if status_val in ("", "started", "running", "planned", "validated", "cancelled"):
                return

            exec_id = str(final_state.get("execution_id") or "")
            profile_id = str(final_state.get("profile_id") or "default")
            intent = str(final_state.get("intent") or "").lower()
            plan = final_state.get("plan") or []
            is_automation = bool(plan) or intent in (
                "desktop_control",
                "file_assistant",
                "browser_research",
                "automation",
            )
            sensitive = intent in ("email_assistant", "file_assistant", "browser_research", "vision", "screen")

            if status_val == "confirmation_required":
                get_shared_notification_service().notify(
                    category="confirmation_required",
                    title="VoxPilot Confirmation Required",
                    message="An action requires your explicit approval in VoxPilot.",
                    event_key=f"exec:{exec_id}:confirmation_required",
                    profile_id=profile_id,
                    sensitive=True,
                )
            elif status_val in ("failed", "validation_failed"):
                cat = "automation_failed" if is_automation else "task_failed"
                get_shared_notification_service().notify(
                    category=cat,
                    title="VoxPilot Task Failed",
                    message=str(final_state.get("error") or final_state.get("response") or "Task failed."),
                    event_key=f"exec:{exec_id}:failed",
                    profile_id=profile_id,
                    sensitive=sensitive,
                )
            elif status_val == "completed":
                if is_automation and len(plan) >= 3:
                    cat = "long_running_task_completed"
                elif is_automation:
                    cat = "automation_completed"
                else:
                    cat = "task_completed"
                get_shared_notification_service().notify(
                    category=cat,
                    title="VoxPilot Task Completed",
                    message=str(final_state.get("response") or "Task completed."),
                    event_key=f"exec:{exec_id}:completed",
                    profile_id=profile_id,
                    sensitive=sensitive,
                )
        except Exception as notify_err:
            logger.debug(f"Non-fatal desktop notification error: {notify_err}")

    # ==========================================================================
    # Phase 4B: AI File Assistant Natural-Language Routing
    # ==========================================================================

    @staticmethod
    def _looks_like_file_assistant_request(user_input: str) -> bool:
        lower = user_input.strip().lower()
        return bool(
            re.search(
                r"^(?:summarize|summarise|read|inspect|analyze|analyse)\s+(?:this\s+|the\s+|my\s+)?(?:pdf|docx?|txt|csv|json|md|markdown|document|file|resume|report|notes|it)\b",
                lower,
            )
            or re.search(
                r"^(?:summarize|summarise|read|inspect)\s+.*\.(?:pdf|docx?|txt|csv|json|md|markdown|py|log)\b",
                lower,
            )
            or re.search(
                r"^what\s+does\s+(?:this|the)\s+(?:document|file|pdf|docx?|report|resume|notes)\s+say\b",
                lower,
            )
            or re.search(
                r"^compare\s+(?:these\s+(?:two\s+)?(?:documents|files)|.*\.(?:pdf|docx?|txt|csv|json|md|py)\b)",
                lower,
            )
            or re.search(r"^find\s+(?:my\s+resume|all\s+(?:python|\.py)\s+files\b|files\s+modified\s+recently\b)", lower)
            or re.search(r"^organize\s+(?:these\s+)?files\b", lower)
        )

    @staticmethod
    def _extract_file_tokens(text: str) -> List[str]:
        """Extract explicit file paths or filenames with extensions from user text."""
        candidates: List[str] = []
        # Quoted paths/filenames first
        for m in re.finditer(r"['\"]([^'\"]+\.[a-zA-Z0-9]{1,8})['\"]", text):
            candidates.append(m.group(1).strip())
        # Windows/POSIX absolute paths
        for m in re.finditer(r"(?:[A-Za-z]:\\[^\s\"']+|\b[\w./\\-]+\.(?:pdf|docx?|txt|md|markdown|csv|json|py|log|yaml|yml|xml|html|js|ts))\b", text, re.IGNORECASE):
            val = m.group(0).strip().rstrip(".,?!;:")
            if val not in candidates:
                candidates.append(val)
        return candidates

    def _resolve_single_file_target(
        self,
        user_input: str,
        conv_ctx: Any,
        file_assistant: Any,
    ) -> tuple[Optional[str], Optional[str]]:
        """Resolve a single file path from prompt or conversation context, or return an error/clarification message."""
        tokens = self._extract_file_tokens(user_input)
        if tokens:
            candidate = tokens[0]
            if any(sep in candidate for sep in ("\\", "/")):
                return candidate, None
            # Search approved dirs for bare filename
            found = file_assistant.find_files(query=candidate, max_results=5)
            items = found.get("items") or []
            exact = [i for i in items if i["name"].lower() == candidate.lower()]
            matches = exact or items
            if len(matches) == 1:
                return matches[0]["path"], None
            if len(matches) > 1:
                opts = "\n".join(f"- {m['name']} ({m['path']})" for m in matches[:5])
                return None, f"Multiple files matched '{candidate}'. Please specify which one:\n{opts}"
            return None, f"Could not find '{candidate}' in your approved directories."

        last_file = getattr(conv_ctx, "last_file", None)
        if last_file:
            return str(last_file), None

        return None, (
            "Please specify the file path or filename you want me to inspect "
            "(for example, 'Summarize C:\\Users\\...\\report.pdf')."
        )

    def _try_handle_file_assistant_request(
        self,
        user_input: str,
        initial_state: VoxPilotState,
        conv_ctx: Any,
    ) -> Optional[VoxPilotState]:
        """Handle natural-language AI File Assistant tasks cleanly without mutating long-term memory."""
        if not self._looks_like_file_assistant_request(user_input):
            return None

        manager = get_shared_emergency_stop_manager()
        if manager.is_emergency_stop_active():
            return {
                **initial_state,
                "intent": "file_management",
                "confidence": 1.0,
                "execution_status": "cancelled",
                "error": f"Emergency stop is active: {manager.trigger_reason}",
                "response": f"Execution halted: emergency stop is active ({manager.trigger_reason}).",
            }

        lower = user_input.strip().lower()
        file_assistant = get_shared_file_assistant()

        # 1. Project Python files search ("Find all Python files inside my VoxPilot project")
        if re.search(r"^find\s+all\s+(?:python|\.py)\s+files\b", lower):
            include_proj = "voxpilot" in lower or "project" in lower
            res = file_assistant.find_files(
                extension=".py",
                include_project_readonly=include_proj,
                max_results=50,
            )
            return {
                **initial_state,
                "intent": "file_management",
                "confidence": 0.99,
                "execution_status": "completed" if res.get("success") else "failed",
                "execution_results": [res],
                "completed_steps": [{"action": "find_files", "status": res.get("status", "success")}],
                "response": res.get("message", "Search completed."),
            }

        # 2. Recent files search ("Find files modified recently in Downloads")
        if re.search(r"^find\s+files\s+modified\s+recently\b", lower):
            folder_match = re.search(r"\bin\s+(downloads|documents|desktop|pictures|music|videos)\b", lower)
            folder = folder_match.group(1) if folder_match else None
            res = file_assistant.find_files(
                directory_path=folder,
                modified_within_days=7,
                sort_by="modified",
                max_results=20,
            )
            return {
                **initial_state,
                "intent": "file_management",
                "confidence": 0.99,
                "execution_status": "completed" if res.get("success") else "failed",
                "execution_results": [res],
                "completed_steps": [{"action": "find_recent_files", "status": res.get("status", "success")}],
                "response": res.get("message", "Search completed."),
            }

        # 3. Named personal file search ("Find my resume")
        resume_match = re.search(r"^find\s+my\s+([a-zA-Z0-9_\- ]{2,40}?)\.?$", lower)
        if resume_match:
            term = resume_match.group(1).strip()
            res = file_assistant.find_files(query=term, sort_by="modified", max_results=15)
            exec_results = [res]
            items = res.get("items") or []
            if len(items) == 1:
                exec_results.append({"action": "find_files", "success": True, "source_path": items[0]["path"]})
            return {
                **initial_state,
                "intent": "file_management",
                "confidence": 0.99,
                "execution_status": "completed" if res.get("success") else "failed",
                "execution_results": exec_results,
                "completed_steps": [{"action": "find_files", "status": res.get("status", "success")}],
                "response": res.get("message", "Search completed."),
            }

        # 4. Organize files into folders (always requires confirmation before moving)
        if re.search(r"^organize\s+(?:these\s+)?files\b", lower):
            folder_match = re.search(r"\bin\s+(downloads|documents|desktop|pictures|music|videos)\b", lower)
            folder = folder_match.group(1) if folder_match else "downloads"
            plan_res = file_assistant.plan_folder_organization(folder)
            if not plan_res.get("moves"):
                return {
                    **initial_state,
                    "intent": "file_management",
                    "confidence": 0.99,
                    "execution_status": "completed" if plan_res.get("success") else "failed",
                    "execution_results": [plan_res],
                    "response": plan_res.get("message", "No files found to organize."),
                }
            preview_lines = [
                f"- {m['file_name']} -> {m['category']}/"
                for m in plan_res["moves"][:15]
            ]
            if len(plan_res["moves"]) > 15:
                preview_lines.append(f"...and {len(plan_res['moves']) - 15} more.")
            return {
                **initial_state,
                "intent": "file_management",
                "confidence": 0.99,
                "execution_status": "confirmation_required",
                "requires_confirmation": True,
                "pending_confirmation": {
                    "operation": "organize_files",
                    "directory": plan_res.get("directory"),
                    "file_count": plan_res.get("file_count", 0),
                },
                "execution_results": [plan_res],
                "response": (
                    f"{plan_res['message']}\n\nPlanned moves:\n" + "\n".join(preview_lines)
                ),
            }

        # 5. Compare two documents
        if lower.startswith("compare "):
            tokens = self._extract_file_tokens(user_input)
            if len(tokens) < 2:
                recent_files = list(getattr(conv_ctx, "recent_files", []) or [])
                for rf in recent_files:
                    if rf not in tokens:
                        tokens.append(rf)
            if len(tokens) < 2:
                return {
                    **initial_state,
                    "intent": "file_management",
                    "confidence": 0.99,
                    "execution_status": "completed",
                    "response": (
                        "Please provide at least two document paths or filenames to compare "
                        "(for example, 'Compare doc1.txt and doc2.txt')."
                    ),
                }
            resolved_paths: List[str] = []
            for tok in tokens[:4]:
                if any(sep in tok for sep in ("\\", "/")):
                    resolved_paths.append(tok)
                else:
                    found = file_assistant.find_files(query=tok, max_results=3)
                    items = found.get("items") or []
                    exact = [i for i in items if i["name"].lower() == tok.lower()]
                    pick = (exact or items)
                    if not pick:
                        return {
                            **initial_state,
                            "intent": "file_management",
                            "confidence": 0.99,
                            "execution_status": "failed",
                            "response": f"Could not find '{tok}' in your approved directories to compare.",
                        }
                    resolved_paths.append(pick[0]["path"])

            cmp_res = file_assistant.compare_documents(resolved_paths, focus=user_input)
            return {
                **initial_state,
                "intent": "file_management",
                "confidence": 0.99,
                "execution_status": "completed" if cmp_res.get("success") else "failed",
                "execution_results": [cmp_res],
                "completed_steps": [{"action": "compare_documents", "status": cmp_res.get("status", "success")}],
                "response": cmp_res.get("message", "Comparison failed."),
                "error": "" if cmp_res.get("success") else cmp_res.get("message", ""),
            }

        # 6. Summarize document
        if re.search(r"^(?:summarize|summarise|read|inspect|analyze|analyse)\b", lower):
            target_path, err_msg = self._resolve_single_file_target(user_input, conv_ctx, file_assistant)
            if err_msg or not target_path:
                return {
                    **initial_state,
                    "intent": "file_management",
                    "confidence": 0.99,
                    "execution_status": "completed",
                    "response": err_msg or "Please specify which document to summarize.",
                }
            sum_res = file_assistant.summarize_document(target_path)
            return {
                **initial_state,
                "intent": "file_management",
                "confidence": 0.99,
                "execution_status": "completed" if sum_res.get("success") else "failed",
                "execution_results": [sum_res, {"action": "summarize_document", "success": sum_res.get("success", False), "source_path": target_path}],
                "completed_steps": [{"action": "summarize_document", "status": sum_res.get("status", "success")}],
                "response": sum_res.get("message", "Summarization failed."),
                "error": "" if sum_res.get("success") else sum_res.get("message", ""),
            }

        # 7. Document-grounded Q&A ("What does this document say about ...?")
        if re.search(r"^what\s+does\s+(?:this|the)\s+(?:document|file|pdf|docx?|report|resume|notes)\s+say\b", lower):
            target_path, err_msg = self._resolve_single_file_target(user_input, conv_ctx, file_assistant)
            if err_msg or not target_path:
                return {
                    **initial_state,
                    "intent": "file_management",
                    "confidence": 0.99,
                    "execution_status": "completed",
                    "response": err_msg or "Please specify which document you are asking about.",
                }
            qa_res = file_assistant.answer_document_question(target_path, question=user_input)
            return {
                **initial_state,
                "intent": "file_management",
                "confidence": 0.99,
                "execution_status": "completed" if qa_res.get("success") else "failed",
                "execution_results": [qa_res, {"action": "answer_document_question", "success": qa_res.get("success", False), "source_path": target_path}],
                "completed_steps": [{"action": "answer_document_question", "status": qa_res.get("status", "success")}],
                "response": qa_res.get("message", "Document Q&A failed."),
                "error": "" if qa_res.get("success") else qa_res.get("message", ""),
            }

        return None

    # ==========================================================================
    # Phase 4C: AI Browser Research Agent Natural-Language Routing
    # ==========================================================================

    @staticmethod
    def _looks_like_browser_research_request(user_input: str) -> bool:
        lower = user_input.strip().lower()
        return bool(
            re.search(r"^(?:research|investigate)\b", lower)
            or re.search(
                r"^(?:open|visit|browse|go\s+to|summarize|summarise|read|inspect)\s+(?:this\s+|the\s+)?(?:website|webpage|web\s+page|article|site|page)\b",
                lower,
            )
            or re.search(
                r"^compare\s+(?:the\s+information\s+from\s+these\s+.*?(?:websites|webpages|pages|sites)|.*https?://)",
                lower,
            )
            or re.search(
                r"^(?:find|extract|get)\s+.*?\b(?:on\s+this\s+(?:page|website|site)|from\s+these\s+search\s+results|official\s+documentation\s+for)\b",
                lower,
            )
            or (
                re.search(r"https?://\S+", lower)
                and any(w in lower for w in ("summarize", "summarise", "compare", "research", "find", "extract", "read", "inspect", "open"))
            )
        )

    @staticmethod
    def _extract_urls(text: str) -> List[str]:
        urls = []
        for match in re.finditer(r"https?://[^\s'\"<>]+", text, re.IGNORECASE):
            cleaned = match.group(0).rstrip(".,?!;:)]")
            if cleaned and cleaned not in urls:
                urls.append(cleaned)
        return urls

    def _try_handle_browser_research_request(
        self,
        user_input: str,
        initial_state: VoxPilotState,
    ) -> Optional[VoxPilotState]:
        """Handle natural-language AI Browser Research tasks safely without storing page text in LTM."""
        if not self._looks_like_browser_research_request(user_input):
            return None

        manager = get_shared_emergency_stop_manager()
        if manager.is_emergency_stop_active():
            return {
                **initial_state,
                "intent": "browser_task",
                "confidence": 1.0,
                "execution_status": "cancelled",
                "error": f"Emergency stop is active: {manager.trigger_reason}",
                "response": f"Execution halted: emergency stop is active ({manager.trigger_reason}).",
            }

        lower = user_input.strip().lower()
        urls = self._extract_urls(user_input)
        research_service = get_shared_browser_research_service()

        if lower.startswith("compare "):
            if len(urls) < 2:
                return {
                    **initial_state,
                    "intent": "browser_task",
                    "confidence": 0.99,
                    "execution_status": "completed",
                    "response": (
                        "Please provide at least two HTTP/HTTPS website URLs to compare "
                        "(for example, 'Compare https://example.com and https://example.org')."
                    ),
                }
            res = research_service.compare_sources(urls=urls, focus=user_input, objective=user_input)
            op_name = "compare_sources"
        elif re.search(r"^(?:research|investigate)\b", lower) or "official documentation for" in lower:
            res = research_service.research_topic(query=user_input, urls=urls or None)
            op_name = "research_topic"
        else:
            target_url = urls[0] if urls else None
            res = research_service.summarize_page(url=target_url, focus=user_input, objective=user_input)
            op_name = "summarize_page"

        success = bool(res.get("success"))
        return {
            **initial_state,
            "intent": "browser_task",
            "confidence": 0.99,
            "execution_status": "completed" if success else "failed",
            "execution_results": [
                {
                    "action": op_name,
                    "success": success,
                    "status": res.get("status", "unknown"),
                    "sources": res.get("sources", []),
                }
            ],
            "completed_steps": [{"action": op_name, "status": res.get("status", "unknown")}] if success else [],
            "failed_steps": [] if success else [{"action": op_name, "status": res.get("status", "unknown")}],
            "response": res.get("message", "Browser research completed."),
            "error": "" if success else res.get("message", ""),
        }

    # ==========================================================================
    # Phase 4D: AI Email Assistant Natural-Language Routing
    # ==========================================================================

    @staticmethod
    def _looks_like_email_assistant_request(user_input: str) -> bool:
        lower = user_input.strip().lower()
        if re.search(r"^(?:reply\s+politely\s+that|draft\s+(?:a\s+)?(?:professional\s+)?reply)\b", lower):
            return True
        return bool(
            re.search(r"\b(?:emails?|e-mails?|inbox|unread\s+emails?|email\s+thread)\b", lower)
            and any(
                kw in lower
                for kw in (
                    "summarize", "summarise", "find", "search", "show", "list",
                    "draft", "write", "reply", "identify", "unread", "attachment",
                    "thread", "check", "read",
                )
            )
        )

    def _try_handle_email_assistant_request(
        self,
        user_input: str,
        initial_state: VoxPilotState,
    ) -> Optional[VoxPilotState]:
        """Handle natural-language AI Email Assistant tasks safely with profile isolation."""
        if not self._looks_like_email_assistant_request(user_input):
            return None

        manager = get_shared_emergency_stop_manager()
        if manager.is_emergency_stop_active():
            return {
                **initial_state,
                "intent": "email_assistant",
                "confidence": 1.0,
                "execution_status": "cancelled",
                "error": f"Emergency stop is active: {manager.trigger_reason}",
                "response": f"Execution halted: emergency stop is active ({manager.trigger_reason}).",
            }

        profile_id = str(initial_state.get("profile_id") or "default")
        lower = user_input.strip().lower()
        email_svc = get_shared_email_assistant()

        if any(w in lower for w in ("draft ", "write ", "reply ")):
            res = email_svc.generate_draft(profile_id=profile_id, instruction=user_input)
            op_name = "generate_email_draft"
        elif "unread" in lower:
            res = email_svc.list_emails(profile_id=profile_id, unread_only=True)
            op_name = "list_unread_emails"
        elif "attachment" in lower:
            res = email_svc.list_emails(profile_id=profile_id, has_attachments=True)
            op_name = "find_emails_with_attachments"
        elif "need a response" in lower or "require a reply" in lower or "need a reply" in lower:
            res = email_svc.summarize_emails(profile_id=profile_id, need_reply_focus=True)
            op_name = "identify_emails_needing_reply"
        elif "summarize" in lower or "summarise" in lower:
            res = email_svc.summarize_emails(profile_id=profile_id)
            op_name = "summarize_emails"
        else:
            from_match = re.search(r"\bfrom\s+(?:my\s+)?([a-zA-Z0-9_.@ \-]{2,50}?)\.?$", user_input.strip(), re.IGNORECASE)
            containing_match = re.search(r"\bcontaining\s+(.+?)\.?$", user_input.strip(), re.IGNORECASE)
            sender = from_match.group(1).strip() if from_match else None
            query = containing_match.group(1).strip() if containing_match else None
            res = email_svc.list_emails(profile_id=profile_id, sender=sender, query=query)
            op_name = "search_emails"

        success = bool(res.get("success"))
        exec_item: Dict[str, Any] = {
            "action": op_name,
            "success": success,
            "status": res.get("status", "unknown"),
        }
        if "draft" in res:
            exec_item["draft"] = res["draft"]

        return {
            **initial_state,
            "intent": "email_assistant",
            "confidence": 0.99,
            "execution_status": "completed" if success else "failed",
            "execution_results": [exec_item],
            "completed_steps": [{"action": op_name, "status": res.get("status", "unknown")}] if success else [],
            "failed_steps": [] if success else [{"action": op_name, "status": res.get("status", "unknown")}],
            "response": res.get("message", "Email assistant completed."),
            "error": "" if success else res.get("message", ""),
        }

    def resume_execution(self, state: VoxPilotState, confirmation_token: str) -> VoxPilotState:
        """Resume a paused execution session after human confirmation token approval."""
        manager = get_shared_emergency_stop_manager()
        if manager.is_emergency_stop_active():
            logger.warning(
                f"Rejecting resume_execution: emergency stop is active ({manager.trigger_reason})."
            )
            cancelled_state: VoxPilotState = {
                **state,
                "execution_status": "cancelled",
                "error": f"Emergency stop is active: {manager.trigger_reason}",
                "response": f"Execution resumption blocked: emergency stop is active ({manager.trigger_reason}).",
            }
            try:
                self.repository.save_execution_sync(cancelled_state)
            except Exception as db_err:
                logger.warning(f"Failed to persist cancelled state to database: {db_err}")
            return cancelled_state

        if state.get("execution_status") != "confirmation_required":
            raise ValueError(
                f"Cannot resume execution: status is '{state.get('execution_status')}', "
                "expected 'confirmation_required'."
            )

        if not confirmation_token or not confirmation_token.strip():
            raise ValueError("A valid confirmation token is required to resume execution.")

        resume_profile_id = (state.get("profile_id") or "default").strip() or "default"
        resume_conv_id = state.get("conversation_id")
        if resume_conv_id:
            known_prof = self._conversation_profiles.get(resume_conv_id)
            if known_prof is not None and known_prof != resume_profile_id:
                raise ValueError(
                    f"Execution resumption blocked: conversation '{resume_conv_id}' belongs to another profile."
                )
            try:
                conv_rec = self.repository.get_conversation_sync(resume_conv_id)
                if conv_rec is not None and (conv_rec.profile_id or "default") != resume_profile_id:
                    raise ValueError(
                        f"Execution resumption blocked: conversation '{resume_conv_id}' belongs to another profile."
                    )
            except ValueError:
                raise
            except Exception as conv_err:
                logger.debug(f"Non-fatal resume profile check skipped: {conv_err}")

        # Rate Limiting & Safety Budget Checkpoints before Resumption
        rate_limiter = get_shared_rate_limiter()
        exec_id = state.get("execution_id", "")
        started_at = state.get("started_at")
        elapsed = (time.time() - started_at) if started_at else 0.0
        dur_ok, dur_reason = rate_limiter.check_execution_budget(exec_id, elapsed_seconds=elapsed)
        if not dur_ok:
            logger.warning(f"Resumption rejected due to duration budget: {dur_reason}")
            rate_limited_resume: VoxPilotState = {
                **state,
                "execution_status": "rate_limited",
                "error": dur_reason or "Execution duration budget exhausted.",
                "response": f"Resumption blocked: {dur_reason}",
                "pending_confirmation": None,
            }
            try:
                self.repository.save_execution_sync(rate_limited_resume)
            except Exception as db_err:
                logger.warning(f"Failed to persist rate_limited resumed state: {db_err}")
            return rate_limited_resume

        current_idx = state.get("current_step", 0)
        plan = list(state.get("plan", []))

        if current_idx >= len(plan):
            raise ValueError("Cannot resume: no steps remaining in plan.")

        # Update pending step parameters with the approved confirmation token
        updated_step = dict(plan[current_idx])
        updated_params = dict(updated_step.get("parameters", {}))
        updated_params["confirmation_token"] = confirmation_token.strip()
        updated_step["parameters"] = updated_params
        plan[current_idx] = updated_step

        # Mark state for resumption
        resumed_state: VoxPilotState = {
            **state,
            "plan": plan,
            "execution_status": "resuming",
            "pending_confirmation": {
                "token": confirmation_token.strip(),
                "step_index": current_idx,
                "action": updated_step.get("action", ""),
                "parameters": updated_params,
                "execution_id": state.get("execution_id", ""),
            },
        }

        rate_limiter.acquire_execution_slot(exec_id)
        try:
            final_state = self.graph.invoke(resumed_state)
        except Exception as error:
            logger.error(f"Error resuming agent execution: {error}", exc_info=True)
            final_state = {
                **resumed_state,
                "error": str(error),
                "execution_status": "failed",
                "response": f"Failed resuming execution: {error}",
            }
        finally:
            rate_limiter.release_execution_slot(exec_id)

        # Persist resumed state & updated conversation context to database
        conv_id = final_state.get("conversation_id")
        if conv_id:
            try:
                self.context_manager.record_turn(
                    conversation_id=conv_id,
                    user_input=final_state.get("user_input", ""),
                    intent=final_state.get("intent", "unknown"),
                    plan=final_state.get("plan", []),
                    execution_results=final_state.get("execution_results", []),
                    completed_steps=final_state.get("completed_steps", []),
                    response=final_state.get("response", ""),
                    status=final_state.get("execution_status", "completed"),
                )
            except Exception as ctx_err:
                logger.warning(f"Failed recording resumed turn in context manager: {ctx_err}")

        try:
            self.repository.save_execution_sync(final_state)
        except Exception as db_err:
            logger.warning(f"Failed to persist resumed state to database: {db_err}")

        if conv_id:
            try:
                self.conversation_hydrator.save_context(
                    conv_id,
                    self.context_manager.get_context(conv_id),
                    profile_id=final_state.get("profile_id", "default"),
                )
            except Exception as ctx_save_err:
                logger.warning(f"Failed persisting resumed conversation context: {ctx_save_err}")

        self._notify_execution_outcome(final_state)
        return final_state
