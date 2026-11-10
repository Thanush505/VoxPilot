"""
VoxPilot - Secure Tool Execution Engine
Coordinates Pydantic input validation, backend confirmation trust-boundary verification,
safe tool execution, and structured result extraction.
"""

from __future__ import annotations

import logging
import uuid
from typing import Dict, Any, Optional

from backend.agent.state import ExecutionResult
from backend.agent.tool_registry import (
    ToolRegistry,
    get_shared_tool_registry,
    UnknownToolError,
    ToolArgumentValidationError,
)
from backend.automation.confirmation_manager import (
    ConfirmationManager,
    get_shared_confirmation_manager,
    ConfirmationError,
    MissingUserApprovalError,
    ConfirmationRejectedError,
    ConfirmationExpiredError,
    ReplayedConfirmationError,
    ActionMismatchConfirmationError,
    TamperedActionConfirmationError,
)
from backend.automation.emergency_stop import (
    get_shared_emergency_stop_manager,
    EmergencyStopActiveError,
)

logger = logging.getLogger(__name__)


class ToolExecutor:
    """Executes validated tool actions and enforces the confirmation trust boundary."""

    def __init__(
        self,
        registry: Optional[ToolRegistry] = None,
        confirmation_manager: Optional[ConfirmationManager] = None,
    ):
        self.registry = registry or get_shared_tool_registry()
        self.confirmation_manager = confirmation_manager or get_shared_confirmation_manager()

    def execute_step(
        self,
        step: Dict[str, Any],
        step_index: int = 0,
        override_confirmation_token: Optional[str] = None,
    ) -> ExecutionResult:
        """Validate, verify confirmation trust boundary, and execute a single plan step.

        Args:
            step: Plan step dictionary containing 'action' and 'parameters'.
            step_index: 0-based sequence index of this step in the overall plan.
            override_confirmation_token: Optional user-approved token passed upon resumption.

        Returns:
            ExecutionResult: Structured result dictionary.
        """
        action = step.get("action", "")
        raw_params = dict(step.get("parameters", {}))

        # 0. Emergency Stop Check before parameter processing
        manager = get_shared_emergency_stop_manager()
        if manager.is_emergency_stop_active():
            logger.warning(
                f"Execution rejected at step {step_index} ('{action}'): "
                f"emergency stop is active ({manager.trigger_reason})."
            )
            return ExecutionResult(
                step_index=step_index,
                action=action,
                success=False,
                status="cancelled",
                message=f"Execution cancelled: emergency stop is active ({manager.trigger_reason}).",
                error=manager.trigger_reason,
            )

        # If an override token was provided (e.g. on resume), inject it into parameters
        if override_confirmation_token:
            raw_params["confirmation_token"] = override_confirmation_token

        # 1. Action resolution and parameter schema validation
        try:
            validated_input = self.registry.validate_parameters(action, raw_params)
            tool_def = self.registry.get_tool(action)
        except UnknownToolError as err:
            logger.error(f"Execution rejected unknown action '{action}': {err}")
            return ExecutionResult(
                step_index=step_index,
                action=action,
                success=False,
                status="failed",
                message=f"Action '{action}' is not registered or approved.",
                error=str(err),
            )
        except ToolArgumentValidationError as err:
            logger.error(f"Execution rejected invalid arguments for action '{action}': {err}")
            return ExecutionResult(
                step_index=step_index,
                action=action,
                success=False,
                status="failed",
                message=f"Parameter validation failed for action '{action}': {err}",
                error=str(err),
            )

        # 2. Trusted Backend Confirmation Policy Check
        # NEVER trust the LLM: LLM-supplied confirmed=True or is_consequential=False CANNOT bypass this!
        requires_conf, action_type, conf_params = self.registry.check_confirmation_required(
            action, raw_params
        )

        provided_token = raw_params.get("confirmation_token")

        if requires_conf:
            # If no approved token is present, halt and generate a pending confirmation request
            if not provided_token:
                pending = self.confirmation_manager.create_confirmation(
                    action_type=action_type,
                    action_params=conf_params,
                    details={
                        "step_index": step_index,
                        "action": action,
                        "description": step.get("description", ""),
                        **conf_params,
                    },
                )
                logger.info(
                    f"Execution paused at step {step_index} ('{action}'): "
                    f"human confirmation required. Token: {pending.token}"
                )
                return ExecutionResult(
                    step_index=step_index,
                    action=action,
                    success=False,
                    status="confirmation_required",
                    message=(
                        f"Action '{action}' requires explicit human approval before execution. "
                        f"Approval token generated: {pending.token}"
                    ),
                    confirmation_token=pending.token,
                    data={
                        "token": pending.token,
                        "action_type": action_type,
                        "parameters": conf_params,
                        "expires_at": pending.expires_at,
                    },
                )

            # Token is present: validate and consume via ConfirmationManager
            try:
                self.confirmation_manager.validate_and_consume(
                    token=provided_token,
                    action_type=action_type,
                    action_params=conf_params,
                )
                logger.info(
                    f"Confirmation token '{provided_token}' approved and consumed for '{action}'."
                )
            except ConfirmationError as conf_err:
                logger.warning(f"Confirmation validation failed for token '{provided_token}': {conf_err}")
                return ExecutionResult(
                    step_index=step_index,
                    action=action,
                    success=False,
                    status="confirmation_failed",
                    message=f"Confirmation verification failed: {conf_err}",
                    error=str(conf_err),
                    confirmation_token=provided_token,
                )

        # 3. Safe Tool Execution with Active Operation Tracking
        op_id = f"op_{step_index}_{uuid.uuid4().hex[:8]}"
        try:
            manager.register_active_operation(
                operation_id=op_id,
                execution_id=step.get("execution_id"),
                category=tool_def.category,
                description=action,
            )
        except EmergencyStopActiveError as estop_err:
            return ExecutionResult(
                step_index=step_index,
                action=action,
                success=False,
                status="cancelled",
                message=f"Action '{action}' cancelled before dispatch: {estop_err}",
                error=str(estop_err),
            )

        try:
            # Immediate pre-execution check
            manager.check_emergency_stop()

            logger.info(f"Executing step {step_index}: '{action}'")
            tool_result = tool_def.func(validated_input)

            # Extract structured result fields
            if isinstance(tool_result, dict):
                success = tool_result.get("success", False)
                status = tool_result.get("status", "completed" if success else "failed")
                message = tool_result.get("message", str(tool_result))
                requires_conf_result = tool_result.get("requires_confirmation", False)
                token_result = tool_result.get("confirmation_token", None) or provided_token
                data_payload = tool_result.get("data", tool_result)
            else:
                success = getattr(tool_result, "success", False)
                status = getattr(tool_result, "status", "completed" if success else "failed")
                message = getattr(tool_result, "message", str(tool_result))
                requires_conf_result = getattr(tool_result, "requires_confirmation", False)
                token_result = getattr(tool_result, "confirmation_token", None) or provided_token

                data_payload = None
                if hasattr(tool_result, "model_dump"):
                    data_payload = tool_result.model_dump()
                elif hasattr(tool_result, "to_dict"):
                    data_payload = tool_result.to_dict()
                elif hasattr(tool_result, "data"):
                    data_payload = tool_result.data

            if requires_conf_result or status == "confirmation_required":
                return ExecutionResult(
                    step_index=step_index,
                    action=action,
                    success=False,
                    status="confirmation_required",
                    message=message,
                    confirmation_token=token_result,
                    data=data_payload,
                )

            if not success:
                return ExecutionResult(
                    step_index=step_index,
                    action=action,
                    success=False,
                    status="failed",
                    message=message,
                    error=message,
                    data=data_payload,
                )

            return ExecutionResult(
                step_index=step_index,
                action=action,
                success=True,
                status=status,
                message=message,
                data=data_payload,
                confirmation_token=token_result,
            )

        except EmergencyStopActiveError as estop_err:
            logger.warning(f"Action '{action}' aborted by emergency stop: {estop_err}")
            return ExecutionResult(
                step_index=step_index,
                action=action,
                success=False,
                status="cancelled",
                message=f"Action '{action}' cancelled by emergency stop: {estop_err}",
                error=str(estop_err),
            )
        except Exception as err:
            logger.error(f"Unexpected exception executing action '{action}': {err}", exc_info=True)
            return ExecutionResult(
                step_index=step_index,
                action=action,
                success=False,
                status="failed",
                message=f"Action '{action}' failed with unexpected error: {err}",
                error=str(err),
            )
        finally:
            manager.unregister_active_operation(op_id)

