"""
VoxPilot - Hardware Telemetry & Local AI Model Lifecycle API Endpoints
Exposes read-only host CPU/RAM/GPU/Ollama telemetry and safe local model
loading/unloading controls without external network egress or unsafe shell calls.
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional

import anyio
from fastapi import APIRouter, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.ai.hardware_monitor import (
    HardwareSnapshot,
    OllamaInstalledModel,
    OllamaRunningModel,
    get_shared_hardware_monitor,
)
from backend.ai.model_manager import (
    DEFAULT_PRIMARY_MODEL,
    ActiveInferenceConflictError,
    ConcurrentModelOperationError,
    InsufficientResourcesError,
    InvalidModelNameError,
    ModelLoadTimeoutError,
    ModelManagerError,
    ModelNotFoundError,
    ModelOperationResult,
    OllamaConnectionError,
    get_shared_model_manager,
)
from backend.api.agent import _verify_trusted_request
from backend.automation.notifications import get_shared_notification_service
from backend.database.backup import BackupError, DatabaseBackupManager
from backend.desktop.shell import (
    DEFAULT_MUTEX_NAME,
    focus_window_by_title,
    get_canonical_app_user_model_id,
    get_shared_desktop_controller,
)
from backend.paths import get_database_path, get_runtime_paths_summary
from backend.automation.startup_manager import (
    StartupConfigurationError,
    StartupManagerError,
    StartupPermissionError,
    StartupUnsupportedError,
    get_shared_startup_manager,
)

logger = logging.getLogger(__name__)

api_system_router = APIRouter(prefix="/api/system", tags=["system"])
agent_system_router = APIRouter(prefix="/agent/system", tags=["system"])


# ==============================================================================
# Pydantic Request & Response Schemas
# ==============================================================================

class ModelLoadRequest(BaseModel):
    model_name: str = Field(..., min_length=1, max_length=128, description="Installed Ollama model name to load.")
    keep_alive: Optional[str] = Field(
        default=None,
        max_length=32,
        description="Ollama keep_alive retention duration (e.g. '15m', '60s', '-1').",
    )


class ModelUnloadRequest(BaseModel):
    model_name: str = Field(..., min_length=1, max_length=128, description="Running Ollama model name to unload.")


class StartupConfigRequest(BaseModel):
    enabled: bool = Field(..., description="Whether VoxPilot should launch automatically at Windows sign-in.")


class StartupStatusResponse(BaseModel):
    enabled: bool
    supported: bool
    mode: str
    mechanism: str
    target_valid: bool
    reason: Optional[str] = None


class NotificationPreferencesUpdateRequest(BaseModel):
    enabled: Optional[bool] = None
    suppress_when_active: Optional[bool] = None
    categories: Optional[dict[str, bool]] = None


class NotificationActivityRequest(BaseModel):
    active: bool = True


class BackupCreateRequest(BaseModel):
    label: Optional[str] = Field(default=None, max_length=64, description="Optional label for backup folder.")
    include_config: bool = True
    include_credentials: bool = True


class SystemModelsResponse(BaseModel):
    reachable: bool
    base_url: str
    default_model: str = DEFAULT_PRIMARY_MODEL
    active_model: str = DEFAULT_PRIMARY_MODEL
    installed_models: List[OllamaInstalledModel] = Field(default_factory=list)
    running_models: List[OllamaRunningModel] = Field(default_factory=list)
    installed_count: int = 0
    running_count: int = 0
    error: Optional[str] = None


class RunningModelsResponse(BaseModel):
    reachable: bool
    base_url: str
    default_model: str = DEFAULT_PRIMARY_MODEL
    active_model: str = DEFAULT_PRIMARY_MODEL
    running_models: List[OllamaRunningModel] = Field(default_factory=list)
    running_count: int = 0
    error: Optional[str] = None


# ==============================================================================
# Error Mapping & Handlers
# ==============================================================================

def _map_model_error_response(exc: Exception) -> JSONResponse:
    """Convert domain model-lifecycle errors into structured JSON HTTP responses."""
    if isinstance(exc, InvalidModelNameError):
        code = status.HTTP_400_BAD_REQUEST
        error_code = "invalid_model_name"
    elif isinstance(exc, ModelNotFoundError):
        code = status.HTTP_404_NOT_FOUND
        error_code = "model_not_found"
    elif isinstance(exc, ConcurrentModelOperationError):
        code = status.HTTP_409_CONFLICT
        error_code = "concurrent_model_operation"
    elif isinstance(exc, ActiveInferenceConflictError):
        code = status.HTTP_409_CONFLICT
        error_code = "active_inference_conflict"
    elif isinstance(exc, InsufficientResourcesError):
        code = status.HTTP_409_CONFLICT
        error_code = "insufficient_resources"
    elif isinstance(exc, ModelLoadTimeoutError):
        code = status.HTTP_504_GATEWAY_TIMEOUT
        error_code = "model_load_timeout"
    elif isinstance(exc, OllamaConnectionError):
        code = status.HTTP_503_SERVICE_UNAVAILABLE
        error_code = "ollama_unavailable"
    else:
        code = status.HTTP_500_INTERNAL_SERVER_ERROR
        error_code = "model_manager_error"

    return JSONResponse(
        status_code=code,
        content={
            "detail": str(exc),
            "error_code": error_code,
            "message": str(exc),
        },
    )


async def _handle_get_hardware(request: Request) -> HardwareSnapshot:
    _verify_trusted_request(request)
    monitor = get_shared_hardware_monitor()
    return await anyio.to_thread.run_sync(monitor.get_hardware_snapshot)


async def _handle_get_models(request: Request) -> SystemModelsResponse:
    _verify_trusted_request(request)
    monitor = get_shared_hardware_monitor()
    manager = get_shared_model_manager()
    telemetry = await anyio.to_thread.run_sync(monitor.collect_ollama_telemetry)
    return SystemModelsResponse(
        reachable=telemetry.reachable,
        base_url=telemetry.base_url,
        default_model=manager.default_model,
        active_model=manager.get_active_model(),
        installed_models=telemetry.installed_models,
        running_models=telemetry.running_models,
        installed_count=telemetry.installed_count,
        running_count=telemetry.running_count,
        error=telemetry.error,
    )


async def _handle_get_running_models(request: Request) -> RunningModelsResponse:
    _verify_trusted_request(request)
    monitor = get_shared_hardware_monitor()
    manager = get_shared_model_manager()
    telemetry = await anyio.to_thread.run_sync(monitor.collect_ollama_telemetry)
    return RunningModelsResponse(
        reachable=telemetry.reachable,
        base_url=telemetry.base_url,
        default_model=manager.default_model,
        active_model=manager.get_active_model(),
        running_models=telemetry.running_models,
        running_count=telemetry.running_count,
        error=telemetry.error,
    )


async def _handle_get_model_metadata(request: Request, model_name: str):
    _verify_trusted_request(request)
    manager = get_shared_model_manager()
    try:
        return await anyio.to_thread.run_sync(lambda: manager.get_model_metadata(model_name))
    except ModelManagerError as exc:
        return _map_model_error_response(exc)


async def _handle_load_model(request: Request, req: ModelLoadRequest):
    _verify_trusted_request(request)
    manager = get_shared_model_manager()
    try:
        return await anyio.to_thread.run_sync(
            lambda: manager.load_model(req.model_name, keep_alive=req.keep_alive)
        )
    except ModelManagerError as exc:
        return _map_model_error_response(exc)


async def _handle_unload_model(request: Request, req: ModelUnloadRequest):
    _verify_trusted_request(request)
    manager = get_shared_model_manager()
    try:
        return await anyio.to_thread.run_sync(lambda: manager.unload_model(req.model_name))
    except ModelManagerError as exc:
        return _map_model_error_response(exc)


# ==============================================================================
# Route Registration (/api/system/* and /agent/system/*)
# ==============================================================================

def _register_routes(r: APIRouter) -> None:
    @r.get("/hardware", response_model=HardwareSnapshot)
    async def get_system_hardware(request: Request):
        """Return real-time read-only host CPU, RAM, GPU, and Ollama telemetry."""
        return await _handle_get_hardware(request)

    @r.get("/models", response_model=SystemModelsResponse)
    async def get_system_models(request: Request):
        """Return installed and running local Ollama models along with active selection."""
        return await _handle_get_models(request)

    @r.get("/models/running", response_model=RunningModelsResponse)
    async def get_running_models(request: Request):
        """Return currently loaded Ollama models from /api/ps."""
        return await _handle_get_running_models(request)

    @r.get("/models/metadata")
    async def get_model_metadata(
        request: Request,
        model_name: str = Query(..., min_length=1, max_length=128, description="Installed Ollama model name."),
    ):
        """Return metadata for a specific installed local Ollama model."""
        return await _handle_get_model_metadata(request, model_name)

    @r.post("/models/load", response_model=ModelOperationResult)
    async def load_system_model(request: Request, req: ModelLoadRequest):
        """Load an installed local Ollama model into memory after resource checks."""
        return await _handle_load_model(request, req)

    @r.post("/models/unload", response_model=ModelOperationResult)
    async def unload_system_model(request: Request, req: ModelUnloadRequest):
        """Safely unload a running local Ollama model if no active inference is occurring."""
        return await _handle_unload_model(request, req)

    @r.get("/startup", response_model=StartupStatusResponse)
    async def get_startup_status(request: Request):
        """Return per-user Windows Wake-on-Startup registration status."""
        _verify_trusted_request(request)
        mgr = get_shared_startup_manager()
        return mgr.get_status().to_dict()

    @r.post("/startup", response_model=StartupStatusResponse)
    async def update_startup_status(request: Request, req: StartupConfigRequest):
        """Enable or disable per-user Windows Wake-on-Startup registration."""
        _verify_trusted_request(request)
        mgr = get_shared_startup_manager()
        try:
            res = mgr.enable() if req.enabled else mgr.disable()
            return res.to_dict()
        except StartupUnsupportedError as exc:
            return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"detail": str(exc)})
        except StartupConfigurationError as exc:
            return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content={"detail": str(exc)})
        except StartupPermissionError as exc:
            return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"detail": str(exc)})
        except StartupManagerError as exc:
            return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"detail": str(exc)})

    @r.get("/notifications")
    async def get_notifications_status(request: Request):
        """Return desktop notification capability and active-profile preferences."""
        _verify_trusted_request(request)
        return get_shared_notification_service().get_status()

    @r.post("/notifications")
    async def update_notifications_preferences(request: Request, req: NotificationPreferencesUpdateRequest):
        """Update desktop notification preferences for the active profile."""
        _verify_trusted_request(request)
        svc = get_shared_notification_service()
        svc.update_preferences(req.model_dump(exclude_none=True))
        return svc.get_status()

    @r.post("/notifications/reset")
    async def reset_notifications_preferences(request: Request):
        """Restore default desktop notification preferences for the active profile."""
        _verify_trusted_request(request)
        svc = get_shared_notification_service()
        svc.reset_preferences()
        return svc.get_status()

    @r.post("/notifications/test")
    async def send_test_notification(request: Request):
        """Send a safe preview Windows toast notification."""
        _verify_trusted_request(request)
        svc = get_shared_notification_service()
        result = await anyio.to_thread.run_sync(svc.send_test_notification)
        return {**result.to_dict(), "status": svc.get_status()}

    @r.post("/notifications/activity")
    async def record_notification_activity(request: Request, req: NotificationActivityRequest):
        """Record whether the VoxPilot window is currently active for suppression."""
        _verify_trusted_request(request)
        controller = get_shared_desktop_controller()
        if controller is not None:
            controller.set_focus_state(bool(req.active))
        else:
            get_shared_notification_service().record_ui_activity(req.active)
        return {"ok": True}

    @r.get("/desktop/status")
    async def get_desktop_status(request: Request):
        """Return runtime status for the VoxPilot native desktop shell or headless server."""
        _verify_trusted_request(request)
        controller = get_shared_desktop_controller()
        if controller is not None:
            payload = controller.get_status_dict()
            payload["single_instance_mutex"] = DEFAULT_MUTEX_NAME
            return payload

        try:
            port_val = int(os.environ.get("VOXPILOT_PORT", "8000"))
        except ValueError:
            port_val = 8000
        return {
            "mode": "server_only",
            "window_title": "VoxPilot",
            "window_active": False,
            "window_focused": False,
            "window_minimized": False,
            "app_user_model_id": get_canonical_app_user_model_id(),
            "host": os.environ.get("VOXPILOT_HOST", "127.0.0.1"),
            "port": port_val,
            "base_url": None,
            "pid": os.getpid(),
            "single_instance_mutex": DEFAULT_MUTEX_NAME,
        }

    @r.post("/desktop/focus")
    async def focus_desktop_window(request: Request):
        """Restore and bring an active VoxPilot desktop window to the foreground."""
        _verify_trusted_request(request, require_desktop_token=False)
        controller = get_shared_desktop_controller()
        focused = False
        if controller is not None:
            focused = bool(controller.focus_window())
        win_focused = focus_window_by_title("VoxPilot")
        return {"ok": True, "focused": bool(focused or win_focused)}

    @r.get("/storage")
    async def get_storage_status(request: Request):
        """Return runtime application data paths and backup inventory (no secret contents)."""
        _verify_trusted_request(request)
        db_file = get_database_path()
        backup_mgr = DatabaseBackupManager()
        backups = await anyio.to_thread.run_sync(backup_mgr.list_backups)
        return {
            "paths": get_runtime_paths_summary(),
            "database": {
                "path": str(db_file),
                "exists": db_file.exists(),
                "size_bytes": db_file.stat().st_size if db_file.is_file() else 0,
            },
            "backups": backups,
        }

    @r.post("/backups")
    async def create_system_backup(request: Request, req: Optional[BackupCreateRequest] = None):
        """Create a SQLite-consistent online backup snapshot without overwriting existing backups."""
        _verify_trusted_request(request)
        payload = req or BackupCreateRequest()
        backup_mgr = DatabaseBackupManager()
        try:
            manifest = await anyio.to_thread.run_sync(
                lambda: backup_mgr.create_backup(
                    label=payload.label,
                    include_config=payload.include_config,
                    include_credentials=payload.include_credentials,
                )
            )
            return manifest
        except BackupError as exc:
            return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"detail": str(exc)})


_register_routes(api_system_router)
_register_routes(agent_system_router)
