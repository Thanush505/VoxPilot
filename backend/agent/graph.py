"""
VoxPilot - LangGraph Agent Workflow
Constructs and compiles the complete LangGraph StateGraph connecting intent recognition,
action planning, plan validation, tool execution, result evaluation, and response generation.
Supports multi-step execution loops, confirmation trust boundaries, and execution resumption.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Dict, Any, Optional, List
from langgraph.graph import StateGraph, START, END

from backend.agent.state import VoxPilotState, ExecutionResult
from backend.agent.intent import IntentRecognizer
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
from backend.database.repository import DatabaseRepository
from backend.automation.emergency_stop import (
    get_shared_emergency_stop_manager,
    EmergencyStopActiveError,
)

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

    def generate_response(self, state: VoxPilotState) -> Dict[str, Any]:
        """Generate final truthful summary response based on actual execution results."""
        status = state.get("execution_status", "")
        results = state.get("execution_results", [])
        completed = state.get("completed_steps", [])
        failed = state.get("failed_steps", [])
        plan = state.get("plan", [])

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
            if not results and state.get("response"):
                # Conversational response
                return {}

            # Construct truthful execution summary
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

    def process_request(
        self,
        user_input: str,
        execution_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> VoxPilotState:
        """Process a natural language request from initial input through execution."""
        if not user_input or not user_input.strip():
            raise ValueError("User input cannot be empty.")

        exec_id = execution_id or f"exec_{uuid.uuid4().hex[:12]}"
        conv_id = conversation_id or f"conv_{exec_id[:8]}"

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

        # Retrieve conversation context
        conv_ctx = self.context_manager.get_context(conv_id)
        conv_summary = conv_ctx.get_summary_text()

        initial_state: VoxPilotState = {
            "user_input": user_input.strip(),
            "execution_id": exec_id,
            "conversation_id": conv_id,
            "conversation_history": conv_summary,
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

        try:
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

        # Update context manager
        try:
            self.context_manager.record_turn(
                conversation_id=conv_id,
                user_input=user_input.strip(),
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

        return final_state

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

        # Persist resumed state to database
        try:
            self.repository.save_execution_sync(final_state)
        except Exception as db_err:
            logger.warning(f"Failed to persist resumed state to database: {db_err}")

        return final_state
