"""
VoxPilot - Agent API Endpoints
Provides RESTful FastAPI endpoints for triggering agent requests, monitoring execution state,
inspecting pending confirmations, approving/rejecting consequential actions, querying conversation history,
and listing registered tools.
"""

from __future__ import annotations

import functools
import logging
import threading
from typing import Dict, Any, Optional, List
import anyio
from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from backend.agent.graph import VoxPilotAgent
from backend.agent.state import VoxPilotState
from backend.database.repository import DatabaseRepository
from backend.automation.confirmation_manager import (
    InvalidConfirmationError,
    ConfirmationExpiredError,
    ReplayedConfirmationError,
    ConfirmationRejectedError,
)
from backend.automation.emergency_stop import (
    get_shared_emergency_stop_manager,
    EmergencyStopActiveError,
    UnsafeResetError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"])

# In-memory execution store and synchronization
_EXECUTIONS: Dict[str, Dict[str, Any]] = {}
_EXECUTIONS_LOCK = threading.Lock()
_SHARED_AGENT: Optional[VoxPilotAgent] = None


def get_agent() -> VoxPilotAgent:
    """Retrieve or initialize the active shared VoxPilotAgent instance."""
    global _SHARED_AGENT
    if _SHARED_AGENT is None:
        _SHARED_AGENT = VoxPilotAgent()
    return _SHARED_AGENT


def set_agent(agent: Optional[VoxPilotAgent]) -> None:
    """Inject or override the shared agent instance (e.g. for testing)."""
    global _SHARED_AGENT
    _SHARED_AGENT = agent


def save_execution(execution_id: str, state: Dict[str, Any]) -> None:
    """Store or update execution state in thread-safe memory."""
    with _EXECUTIONS_LOCK:
        _EXECUTIONS[execution_id] = dict(state)


def get_execution(execution_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve execution state by id in thread-safe manner."""
    with _EXECUTIONS_LOCK:
        res = _EXECUTIONS.get(execution_id)
        return dict(res) if res is not None else None


def clear_executions() -> None:
    """Clear in-memory execution store (for tests)."""
    with _EXECUTIONS_LOCK:
        _EXECUTIONS.clear()


# ==============================================================================
# Pydantic Schemas
# ==============================================================================

class ExecuteRequest(BaseModel):
    user_input: str = Field(..., min_length=1, description="Natural language request or command.")
    execution_id: Optional[str] = Field(None, description="Optional custom identifier for the execution.")
    conversation_id: Optional[str] = Field(None, description="Optional conversation session identifier.")


class ConfirmationApprovalRequest(BaseModel):
    token: str = Field(..., min_length=1, description="Cryptographic confirmation token to approve.")


class ConfirmationRejectionRequest(BaseModel):
    token: str = Field(..., min_length=1, description="Cryptographic confirmation token to reject.")
    reason: Optional[str] = Field(None, description="Optional reason for rejecting the action.")


class EmergencyStopRequest(BaseModel):
    reason: Optional[str] = Field("Emergency stop triggered via API.", description="Reason for triggering emergency stop.")


class EmergencyStopResetRequest(BaseModel):
    force: bool = Field(False, description="Force reset even if operations cannot be verified.")


class EmergencyStopStatusResponse(BaseModel):
    is_active: bool
    trigger_reason: Optional[str] = None
    trigger_source: Optional[str] = None
    triggered_at: Optional[float] = None
    reset_at: Optional[float] = None
    active_operations_count: int = 0
    active_operations: List[Dict[str, Any]] = Field(default_factory=list)


class PendingConfirmationInfo(BaseModel):
    token: str
    action: str
    parameters: Dict[str, Any] = Field(default_factory=dict)
    description: Optional[str] = None
    execution_id: Optional[str] = None
    step_index: Optional[int] = None


class ExecutionStatusResponse(BaseModel):
    execution_id: str
    conversation_id: Optional[str] = None
    execution_status: str
    user_input: str
    intent: Optional[str] = None
    confidence: Optional[float] = None
    plan: List[Dict[str, Any]] = Field(default_factory=list)
    execution_results: List[Dict[str, Any]] = Field(default_factory=list)
    completed_steps: List[Dict[str, Any]] = Field(default_factory=list)
    failed_steps: List[Dict[str, Any]] = Field(default_factory=list)
    response: Optional[str] = None
    error: Optional[str] = None
    requires_confirmation: bool = False
    pending_confirmation: Optional[PendingConfirmationInfo] = None
    replan_count: int = 0
    replan_history: List[Dict[str, Any]] = Field(default_factory=list)


class ConversationInfo(BaseModel):
    conversation_id: str
    title: Optional[str] = None
    summary: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ConversationDetailResponse(BaseModel):
    conversation_id: str
    title: Optional[str] = None
    summary: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    context_summary: Optional[str] = None
    execution_count: int = 0


class ToolInfo(BaseModel):
    name: str
    category: str
    description: str
    is_consequential: bool


# ==============================================================================
# Security Helpers (CSRF & Origin Verification)
# ==============================================================================

def _verify_trusted_request(request: Request) -> None:
    """Verify that incoming requests to sensitive confirmation endpoints do not originate
    from untrusted cross-origin browser sites (CSRF protection for local desktop daemon).
    """
    origin = request.headers.get("origin")
    if origin:
        origin_clean = origin.lower().strip()
        allowed = (
            origin_clean.startswith("http://localhost")
            or origin_clean.startswith("https://localhost")
            or origin_clean.startswith("http://127.0.0.1")
            or origin_clean.startswith("https://127.0.0.1")
        )
        if not allowed:
            logger.warning(f"Blocked cross-origin confirmation attempt from untrusted origin: {origin}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Cross-origin requests from origin '{origin}' are forbidden.",
            )


# ==============================================================================
# API Endpoints
# ==============================================================================

@router.post("/execute", response_model=ExecutionStatusResponse)
async def execute_request(request: ExecuteRequest):
    """Execute a user natural language command through the LangGraph agent workflow."""
    if not request.user_input or not request.user_input.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User input cannot be empty or whitespace.",
        )

    agent = get_agent()
    try:
        final_state = await anyio.to_thread.run_sync(
            functools.partial(
                agent.process_request,
                user_input=request.user_input.strip(),
                execution_id=request.execution_id,
                conversation_id=request.conversation_id,
            )
        )
    except Exception as err:
        logger.error(f"Error processing agent execution: {err}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent execution encountered an unhandled error: {err}",
        )

    exec_id = final_state.get("execution_id", "")
    save_execution(exec_id, final_state)
    return final_state


@router.get("/status/{execution_id}", response_model=ExecutionStatusResponse)
async def get_execution_status(execution_id: str):
    """Fetch the current status and execution history for a specific execution ID."""
    # Check in-memory store first
    state = get_execution(execution_id)
    if state:
        return state

    # Query persistent SQLite database repository
    agent = get_agent()
    try:
        record = await agent.repository.get_execution(execution_id)
        if record:
            steps = await agent.repository.get_execution_steps(execution_id)
            completed_steps = [s.to_dict() for s in steps if s.execution_status == "completed"]
            failed_steps = [s.to_dict() for s in steps if s.execution_status == "failed"]
            return ExecutionStatusResponse(
                execution_id=record.execution_id,
                conversation_id=record.conversation_id,
                execution_status=record.execution_status,
                user_input=record.user_input,
                intent=record.intent,
                plan=record.get_plan(),
                execution_results=[s.to_dict() for s in steps],
                completed_steps=completed_steps,
                failed_steps=failed_steps,
                response=record.response,
                error=record.error,
                requires_confirmation=False,
                pending_confirmation=None,
            )
    except Exception as err:
        logger.warning(f"Error fetching execution {execution_id} from database: {err}")

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Execution session '{execution_id}' not found.",
    )


@router.get("/conversations", response_model=List[ConversationInfo])
async def list_conversations(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List stored conversations ordered by most recent activity."""
    agent = get_agent()
    try:
        convs = await agent.repository.list_conversations(limit=limit, offset=offset)
        return [
            ConversationInfo(
                conversation_id=c.conversation_id,
                title=c.title,
                summary=c.summary,
                created_at=c.created_at.isoformat() if c.created_at else None,
                updated_at=c.updated_at.isoformat() if c.updated_at else None,
            )
            for c in convs
        ]
    except Exception as err:
        logger.error(f"Error listing conversations: {err}", exc_info=True)
        return []


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation_details(conversation_id: str):
    """Retrieve metadata, turn summary, and context for an isolated conversation."""
    agent = get_agent()
    record = await agent.repository.get_conversation(conversation_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation '{conversation_id}' not found.",
        )

    executions = await agent.repository.list_executions_for_conversation(conversation_id)
    ctx = agent.context_manager.get_context(conversation_id)
    return ConversationDetailResponse(
        conversation_id=record.conversation_id,
        title=record.title,
        summary=record.summary,
        created_at=record.created_at.isoformat() if record.created_at else None,
        updated_at=record.updated_at.isoformat() if record.updated_at else None,
        context_summary=ctx.get_summary_text(),
        execution_count=len(executions),
    )


@router.get("/conversations/{conversation_id}/executions", response_model=List[ExecutionStatusResponse])
async def list_conversation_executions(conversation_id: str):
    """Retrieve all execution records belonging to a conversation."""
    agent = get_agent()
    record = await agent.repository.get_conversation(conversation_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation '{conversation_id}' not found.",
        )

    records = await agent.repository.list_executions_for_conversation(conversation_id)
    results = []
    for r in records:
        results.append(
            ExecutionStatusResponse(
                execution_id=r.execution_id,
                conversation_id=r.conversation_id,
                execution_status=r.execution_status,
                user_input=r.user_input,
                intent=r.intent,
                plan=r.get_plan(),
                response=r.response,
                error=r.error,
                requires_confirmation=False,
                pending_confirmation=None,
            )
        )
    return results


@router.get("/confirmations/{execution_id}", response_model=PendingConfirmationInfo)
def get_pending_confirmation(execution_id: str):
    """Retrieve details of the pending confirmation for an execution awaiting approval."""
    state = get_execution(execution_id)
    if not state:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Execution session '{execution_id}' not found.",
        )

    pending = state.get("pending_confirmation")
    if not pending or state.get("execution_status") != "confirmation_required":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No pending confirmation awaiting approval for execution '{execution_id}'.",
        )

    return pending


@router.post("/confirmations/{execution_id}/approve", response_model=ExecutionStatusResponse)
async def approve_confirmation(
    execution_id: str,
    request: ConfirmationApprovalRequest,
    http_request: Request,
):
    """Approve a pending confirmation token and resume agent execution."""
    _verify_trusted_request(http_request)

    state = get_execution(execution_id)
    if not state:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Execution session '{execution_id}' not found.",
        )

    if state.get("execution_status") != "confirmation_required":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Execution '{execution_id}' is not awaiting confirmation. Current status: '{state.get('execution_status')}'.",
        )

    pending = state.get("pending_confirmation")
    if not pending or pending.get("token") != request.token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Confirmation token does not match the pending action token for this execution.",
        )

    agent = get_agent()
    try:
        agent.confirmation_manager.approve_confirmation(request.token)
    except (InvalidConfirmationError, ConfirmationExpiredError, ReplayedConfirmationError, ConfirmationRejectedError) as conf_err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to approve confirmation token: {conf_err}",
        )

    try:
        new_state = await anyio.to_thread.run_sync(
            functools.partial(agent.resume_execution, state, confirmation_token=request.token)
        )
    except Exception as res_err:
        logger.error(f"Failed resuming execution '{execution_id}': {res_err}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed resuming execution: {res_err}",
        )

    save_execution(execution_id, new_state)
    return new_state


@router.post("/confirmations/{execution_id}/reject", response_model=ExecutionStatusResponse)
async def reject_confirmation(
    execution_id: str,
    request: ConfirmationRejectionRequest,
    http_request: Request,
):
    """Reject a pending confirmation token and cancel remaining execution."""
    _verify_trusted_request(http_request)

    state = get_execution(execution_id)
    if not state:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Execution session '{execution_id}' not found.",
        )

    if state.get("execution_status") != "confirmation_required":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Execution '{execution_id}' is not awaiting confirmation. Current status: '{state.get('execution_status')}'.",
        )

    pending = state.get("pending_confirmation")
    if not pending or pending.get("token") != request.token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Confirmation token does not match the pending action token for this execution.",
        )

    agent = get_agent()
    try:
        agent.confirmation_manager.reject_confirmation(request.token)
    except Exception as conf_err:
        logger.warning(f"Error marking token rejected in ConfirmationManager: {conf_err}")

    reason_str = request.reason or "Action was rejected by user."
    state["execution_status"] = "cancelled"
    state["pending_confirmation"] = None
    state["response"] = f"Execution cancelled by user: {reason_str}"
    save_execution(execution_id, state)
    try:
        agent.repository.save_execution_sync(state)
    except Exception as db_err:
        logger.warning(f"Error persisting cancelled execution to db: {db_err}")
    return state


@router.get("/tools", response_model=List[ToolInfo])
def list_available_tools():
    """List all registered tools available to the VoxPilot agent."""
    agent = get_agent()
    tools = agent.tool_registry.list_tool_definitions()
    return [
        ToolInfo(
            name=t.name,
            category=t.category,
            description=t.description,
            is_consequential=t.inherent_confirmation,
        )
        for t in tools
    ]


# ==============================================================================
# Emergency Stop Endpoints
# ==============================================================================

@router.post("/emergency-stop")
async def trigger_emergency_stop_endpoint(
    http_request: Request,
    req: Optional[EmergencyStopRequest] = None,
):
    """Trigger the global emergency stop immediately, halting all active operations."""
    _verify_trusted_request(http_request)
    reason = req.reason if req and req.reason else "Emergency stop triggered via API."
    manager = get_shared_emergency_stop_manager()
    status_info = manager.trigger_emergency_stop(reason=reason, source="api")
    return {
        "success": True,
        "message": "Global emergency stop triggered successfully.",
        **status_info,
    }


@router.get("/emergency-stop/status", response_model=EmergencyStopStatusResponse)
async def get_emergency_stop_status_endpoint():
    """Query current global emergency stop latch status and active operations."""
    manager = get_shared_emergency_stop_manager()
    return manager.get_emergency_stop_status()


@router.post("/emergency-stop/reset")
async def reset_emergency_stop_endpoint(
    http_request: Request,
    req: Optional[EmergencyStopResetRequest] = None,
):
    """Explicitly reset the global emergency stop after verifying safe state."""
    _verify_trusted_request(http_request)
    force = req.force if req else False
    manager = get_shared_emergency_stop_manager()
    try:
        status_info = manager.reset_emergency_stop(force=force)
        return {
            "success": True,
            "message": "Emergency stop successfully reset.",
            **status_info,
        }
    except UnsafeResetError as err:
        logger.warning(f"Unsafe emergency stop reset rejected: {err}")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(err),
        )
