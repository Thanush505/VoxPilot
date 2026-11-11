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
from pathlib import Path
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
from backend.database.audit_export import get_shared_audit_exporter
from backend.voice.audio_recorder import AudioRecorderError
from backend.voice.hotkey import (
    ReservedShortcutError,
    ShortcutConflictError,
    ShortcutValidationError,
)
from backend.voice.voice_service import (
    get_shared_voice_service,
    list_available_piper_voices,
)
from backend.automation.application_discovery import get_shared_discovery_service
from backend.automation.application_launcher import (
    AmbiguousApplicationError,
    ApplicationLauncher,
    ApplicationLauncherError,
    ApplicationNotAllowedError,
    ApplicationNotFoundError,
)
from backend.agent.tool_registry import get_shared_tool_registry
from backend.automation.confirmation_manager import (
    ConfirmationError,
    get_shared_confirmation_manager,
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
    force: bool = Field(False, description="Deprecated force reset flag; disallowed in favor of operator recovery.")


class EmergencyStopRecoveryRequest(BaseModel):
    operator_justification: str = Field(..., min_length=10, description="Justification explaining verified physical/system state.")
    confirmed_terminated_operations: List[str] = Field(default_factory=list, description="List of operation IDs verified terminated.")
    verification_evidence: Optional[Dict[str, Any]] = Field(default=None, description="Optional evidence such as checked PIDs or logs.")


class AuditExportRequest(BaseModel):
    format: str = Field("json", description="Export format: 'json' or 'csv'.")
    filename: Optional[str] = Field(None, description="Optional custom destination filename (name only, no paths).")
    execution_id: Optional[str] = Field(None, description="Optional execution ID filter.")
    conversation_id: Optional[str] = Field(None, description="Optional conversation ID filter.")
    status_filter: Optional[str] = Field(None, description="Optional status filter ('completed', 'failed', 'cancelled', 'rate_limited').")
    start_date: Optional[str] = Field(None, description="Optional start datetime filter (ISO format or YYYY-MM-DD).")
    end_date: Optional[str] = Field(None, description="Optional end datetime filter (ISO format or YYYY-MM-DD).")
    confirm_overwrite: bool = Field(False, description="Whether to overwrite existing export file if it exists.")


class AuditExportResponse(BaseModel):
    export_id: str
    filename: str
    file_path: str
    format: str
    record_count: int
    exported_at: str
    status: str


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

    if final_state.get("execution_status") == "rate_limited":
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=final_state.get("error") or "Rate limit or execution budget exceeded.",
        )

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


@router.post("/emergency-stop/recover")
async def recover_emergency_stop_endpoint(
    http_request: Request,
    req: EmergencyStopRecoveryRequest,
):
    """Authorized operator endpoint to recover from emergency stop with verified evidence."""
    _verify_trusted_request(http_request)
    manager = get_shared_emergency_stop_manager()
    try:
        status_info = manager.operator_emergency_recovery(
            operator_justification=req.operator_justification,
            confirmed_terminated_operations=req.confirmed_terminated_operations,
            verification_evidence=req.verification_evidence,
        )
        return {
            "success": True,
            "message": "Emergency stop recovery completed successfully.",
            **status_info,
        }
    except ValueError as val_err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(val_err),
        )
    except UnsafeResetError as err:
        logger.warning(f"Emergency stop recovery rejected: {err}")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(err),
        )


# ==============================================================================
# Audit Export Endpoints (Localhost Only, CSRF Protected)
# ==============================================================================

@router.post("/audit/export", response_model=AuditExportResponse)
async def export_audit_trail_endpoint(
    http_request: Request,
    req: AuditExportRequest,
):
    """Securely export scrubbed audit records to JSON or CSV in the approved export directory."""
    _verify_trusted_request(http_request)
    exporter = get_shared_audit_exporter()

    filters: Dict[str, Any] = {}
    if req.execution_id:
        filters["execution_id"] = req.execution_id
    if req.conversation_id:
        filters["conversation_id"] = req.conversation_id
    if req.status_filter:
        filters["status"] = req.status_filter
    if req.start_date:
        filters["start_date"] = req.start_date
    if req.end_date:
        filters["end_date"] = req.end_date

    export_format = (req.format or "json").lower().strip()
    try:
        if export_format == "json":
            result = exporter.export_json(
                filename=req.filename,
                filters=filters,
                confirm_overwrite=req.confirm_overwrite,
            )
        elif export_format == "csv":
            result = exporter.export_csv(
                filename=req.filename,
                filters=filters,
                confirm_overwrite=req.confirm_overwrite,
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported export format '{req.format}'. Supported formats: 'json', 'csv'.",
            )
        return AuditExportResponse(**result)
    except FileExistsError as err:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(err),
        )
    except ValueError as val_err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(val_err),
        )


@router.get("/audit/exports", response_model=List[AuditExportResponse])
async def list_audit_exports_endpoint(
    http_request: Request,
):
    """Retrieve metadata for all previously generated audit exports in the approved directory."""
    _verify_trusted_request(http_request)
    exporter = get_shared_audit_exporter()
    exports = exporter.list_exports()
    return [AuditExportResponse(**exp) for exp in exports]


@router.get("/audit/exports/{export_id}/status", response_model=AuditExportResponse)
async def get_audit_export_status_endpoint(
    http_request: Request,
    export_id: str,
):
    """Retrieve metadata and status for a previously generated audit export."""
    _verify_trusted_request(http_request)
    exporter = get_shared_audit_exporter()
    info = exporter.get_export_status(export_id)
    if not info:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Audit export '{export_id}' not found.",
        )
    return AuditExportResponse(**info)


@router.get("/audit/exports/{export_id}/download")
async def download_audit_export_endpoint(
    http_request: Request,
    export_id: str,
):
    """Securely stream a generated audit export file to the user.

    Validates export ID, enforces destination containment within approved export dir,
    prevents path traversal, sets safe Content-Disposition headers, and never executes files.
    """
    _verify_trusted_request(http_request)

    if not export_id or "/" in export_id or "\\" in export_id or ".." in export_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid export identifier format.",
        )

    exporter = get_shared_audit_exporter()
    info = exporter.get_export_status(export_id)
    if not info:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Audit export '{export_id}' not found.",
        )

    file_path = Path(info.get("file_path", "")).resolve()
    try:
        if not file_path.is_relative_to(exporter.export_dir) or not file_path.is_file():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Export file not found or unauthorized path.",
            )
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid export file path resolution.",
        )

    export_format = str(info.get("format", "json")).lower()
    media_type = "application/json" if export_format == "json" else "text/csv"
    filename = str(info.get("filename", f"{export_id}.{export_format}"))

    from fastapi.responses import FileResponse
    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type=media_type,
    )


# ==============================================================================
# Voice Mode & Customizable Global Shortcut Endpoints
# ==============================================================================

class VoiceStartRequest(BaseModel):
    conversation_id: Optional[str] = Field(None, description="Optional conversation session identifier.")


class VoiceStopRequest(BaseModel):
    reason: Optional[str] = Field("Stopped by user", description="Optional reason for stopping voice mode.")


class VoiceConfigUpdateRequest(BaseModel):
    shortcut_enabled: Optional[bool] = Field(None, description="Enable or disable global keyboard shortcut.")
    shortcut: Optional[str] = Field(None, description="Global keyboard shortcut string (e.g. 'Ctrl+Shift+V').")
    microphone_device: Optional[Any] = Field(None, description="Microphone device index, name substring, or None for default.")
    tts_enabled: Optional[bool] = Field(None, description="Enable or disable Piper text-to-speech playback.")
    piper_voice: Optional[str] = Field(None, description="Installed Piper ONNX voice model filename.")
    streaming_vad_enabled: Optional[bool] = Field(None, description="Enable or disable dynamic WebRTC streaming VAD.")


class VoiceShortcutUpdateRequest(BaseModel):
    shortcut: str = Field(..., min_length=1, description="New global keyboard shortcut combination (e.g. 'Ctrl+Shift+V').")
    enabled: Optional[bool] = Field(None, description="Optional flag to enable or disable the shortcut simultaneously.")


class MicrophoneTestRequest(BaseModel):
    device: Optional[Any] = Field(None, description="Microphone device index, name, or None for currently selected device.")
    duration_seconds: float = Field(0.5, ge=0.1, le=2.0, description="Test sample duration in seconds.")


@router.get("/voice/status")
async def get_voice_status_endpoint(
    since_event_id: int = Query(0, ge=0, description="Return voice events with event_id greater than this value."),
):
    """Retrieve live voice mode state, component diagnostics, shortcut status, and recent voice events."""
    voice_service = get_shared_voice_service()
    return await anyio.to_thread.run_sync(
        functools.partial(voice_service.get_status, since_event_id=since_event_id)
    )


@router.post("/voice/start")
async def start_voice_mode_endpoint(
    http_request: Request,
    req: Optional[VoiceStartRequest] = None,
):
    """Activate Voice Mode and start listening for spoken commands."""
    _verify_trusted_request(http_request)
    voice_service = get_shared_voice_service()
    conv_id = req.conversation_id if req else None
    return await anyio.to_thread.run_sync(
        functools.partial(voice_service.start_voice_mode, conversation_id=conv_id, source="ui")
    )


@router.post("/voice/stop")
async def stop_voice_mode_endpoint(
    http_request: Request,
    req: Optional[VoiceStopRequest] = None,
):
    """Immediately stop active Voice Mode, cancel recording/playback, and release microphone."""
    _verify_trusted_request(http_request)
    voice_service = get_shared_voice_service()
    reason = req.reason if req and req.reason else "Stopped by user"
    return await anyio.to_thread.run_sync(
        functools.partial(voice_service.stop_voice_mode, reason=reason)
    )


@router.post("/voice/toggle")
async def toggle_voice_mode_endpoint(
    http_request: Request,
    req: Optional[VoiceStartRequest] = None,
):
    """Toggle Voice Mode on or off."""
    _verify_trusted_request(http_request)
    voice_service = get_shared_voice_service()
    conv_id = req.conversation_id if req else None
    return await anyio.to_thread.run_sync(
        functools.partial(voice_service.toggle_voice_mode, conversation_id=conv_id, source="ui")
    )


@router.get("/voice/config")
async def get_voice_config_endpoint():
    """Retrieve current persistent voice settings and shortcut registration status."""
    voice_service = get_shared_voice_service()
    return {
        "settings": voice_service.settings_manager.get_settings(),
        "shortcut_status": voice_service.hotkey_listener.get_status(),
    }


@router.post("/voice/config")
async def update_voice_config_endpoint(
    http_request: Request,
    req: VoiceConfigUpdateRequest,
):
    """Validate, apply, and persist updated voice settings."""
    _verify_trusted_request(http_request)
    voice_service = get_shared_voice_service()
    updates = req.model_dump(exclude_unset=True)
    try:
        return await anyio.to_thread.run_sync(
            functools.partial(voice_service.update_config, updates)
        )
    except ReservedShortcutError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        )
    except (ShortcutValidationError, AudioRecorderError, ValueError) as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        )
    except ShortcutConflictError as err:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(err),
        )


@router.post("/voice/shortcut")
async def update_voice_shortcut_endpoint(
    http_request: Request,
    req: VoiceShortcutUpdateRequest,
):
    """Validate, register on Windows, and persist a custom global Voice Mode keyboard shortcut."""
    _verify_trusted_request(http_request)
    voice_service = get_shared_voice_service()
    try:
        return await anyio.to_thread.run_sync(
            functools.partial(voice_service.update_shortcut, req.shortcut, enabled=req.enabled)
        )
    except ReservedShortcutError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        )
    except ShortcutValidationError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        )
    except ShortcutConflictError as err:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(err),
        )


@router.get("/voice/microphones")
async def list_microphones_endpoint():
    """List available microphone input devices and currently selected microphone."""
    voice_service = get_shared_voice_service()
    return await anyio.to_thread.run_sync(voice_service.list_microphones)


@router.post("/voice/microphones/test")
async def test_microphone_endpoint(
    http_request: Request,
    req: Optional[MicrophoneTestRequest] = None,
):
    """Test microphone input capture and signal level without saving audio to disk."""
    _verify_trusted_request(http_request)
    voice_service = get_shared_voice_service()
    device = req.device if req else None
    duration = req.duration_seconds if req else 0.5
    return await anyio.to_thread.run_sync(
        functools.partial(voice_service.test_microphone, device=device, duration_seconds=duration)
    )


@router.get("/voice/voices")
async def list_piper_voices_endpoint():
    """List all installed Piper ONNX voice models."""
    return {
        "voices": list_available_piper_voices(),
    }


# ==============================================================================
# Application Discovery, Indexing, and Direct Launching Endpoints
# ==============================================================================


class RefreshApplicationsRequest(BaseModel):
    """Request schema for refreshing the application discovery index."""
    include_store_apps: bool = Field(default=True)


class DirectLaunchApplicationRequest(BaseModel):
    """Request schema for launching a discovered application from the dashboard."""
    query: str = Field(..., min_length=1, max_length=120)
    confirmation_token: Optional[str] = Field(default=None)


@router.get("/applications")
async def list_discovered_applications_endpoint(
    query: Optional[str] = Query(default=None, max_length=120),
    source: Optional[str] = Query(default=None, max_length=60),
    limit: int = Query(default=250, ge=1, le=500),
):
    """List or search discovered Windows applications, Store apps, Settings pages, and utilities."""
    discovery_service = get_shared_discovery_service()
    items = await anyio.to_thread.run_sync(
        functools.partial(
            discovery_service.list_applications,
            search_query=query,
            source_filter=source,
            limit=limit,
        )
    )
    summary = discovery_service.get_index_summary()
    return {
        "applications": [item.to_dict() for item in items],
        "returned_count": len(items),
        "total_count": summary.get("total_count", len(items)),
        "by_source": summary.get("by_source", {}),
        "last_refreshed_at": summary.get("last_refreshed_at"),
        "errors": summary.get("errors", []),
    }


@router.post("/applications/refresh")
async def refresh_discovered_applications_endpoint(
    http_request: Request,
    req: Optional[RefreshApplicationsRequest] = None,
):
    """Rescan the Windows system and refresh the application discovery index."""
    _verify_trusted_request(http_request)
    include_store = req.include_store_apps if req is not None else True
    discovery_service = get_shared_discovery_service()
    items = await anyio.to_thread.run_sync(
        functools.partial(discovery_service.refresh_index, include_store_apps=include_store)
    )
    summary = discovery_service.get_index_summary()
    return {
        "status": "refreshed",
        "applications": [item.to_dict() for item in items[:250]],
        "returned_count": min(len(items), 250),
        "total_count": summary.get("total_count", len(items)),
        "by_source": summary.get("by_source", {}),
        "last_refreshed_at": summary.get("last_refreshed_at"),
        "errors": summary.get("errors", []),
        "message": f"Application index refreshed ({summary.get('total_count', len(items))} applications indexed).",
    }


@router.post("/applications/launch")
async def launch_discovered_application_endpoint(
    http_request: Request,
    req: DirectLaunchApplicationRequest,
):
    """Safely launch a discovered application, Store app, Settings page, or system utility."""
    _verify_trusted_request(http_request)

    estop = get_shared_emergency_stop_manager()
    if estop.is_emergency_stop_active():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot launch application while Emergency Stop is active ({estop.trigger_reason}).",
        )

    query = req.query.strip()
    registry = get_shared_tool_registry()
    conf_mgr = get_shared_confirmation_manager()
    params = {"application_name": query}

    requires_conf, action_type, conf_params = registry.check_confirmation_required(
        "launch_application", params
    )
    if requires_conf:
        if not req.confirmation_token:
            pending = conf_mgr.create_confirmation(
                action_type=action_type,
                action_params=conf_params,
                details={
                    "action": "launch_application",
                    "description": f"Launch sensitive system utility: {query}",
                    **conf_params,
                },
            )
            return {
                "success": False,
                "status": "confirmation_required",
                "requires_confirmation": True,
                "confirmation_token": pending.token,
                "display_name": query,
                "message": (
                    f"Launching '{query}' requires explicit confirmation. "
                    "Please approve the confirmation request to proceed."
                ),
            }
        try:
            conf_mgr.validate_and_consume(
                token=req.confirmation_token,
                action_type=action_type,
                action_params=conf_params,
            )
        except ConfirmationError as conf_err:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Confirmation verification failed: {conf_err}",
            )

    launcher = ApplicationLauncher()
    repo = DatabaseRepository()

    def _do_launch() -> Dict[str, Any]:
        return launcher.launch(query)

    try:
        result = await anyio.to_thread.run_sync(_do_launch)
        try:
            repo.log_action(
                execution_id=f"app_launch_{result.get('app_id', 'app')}",
                step_index=0,
                action_type="launch_application",
                parameters={
                    "application_name": query,
                    "resolved_app_id": result.get("app_id"),
                    "launch_method": result.get("launch_method"),
                    "source": result.get("source"),
                },
                status="completed",
                result_summary=result.get("message", f"Launched {result.get('display_name')}"),
            )
        except Exception as log_err:
            logger.debug("Audit log write warning during application launch: %s", log_err)
        return result

    except AmbiguousApplicationError as err:
        return {
            "success": False,
            "status": "ambiguous",
            "display_name": query,
            "candidates": list(err.candidates),
            "message": str(err),
        }
    except ApplicationNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(err),
        )
    except ApplicationNotAllowedError as err:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(err),
        )
    except ApplicationLauncherError as err:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(err),
        )
