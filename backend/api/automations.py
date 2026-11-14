"""
VoxPilot - Custom Automation Builder API Endpoints (Phase 6A)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import anyio
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.api.agent import _verify_trusted_request
from backend.automation.custom_automations import (
    AutomationAccessError,
    AutomationNotFoundError,
    AutomationValidationError,
    get_shared_custom_automation_service,
)

api_automations_router = APIRouter(prefix="/api/automations", tags=["automations"])
agent_automations_router = APIRouter(prefix="/agent/automations", tags=["automations"])


class AutomationPreviewRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=1000)


class AutomationStepPayload(BaseModel):
    step_id: Optional[str] = None
    action: str = Field(..., min_length=1, max_length=100)
    parameters: Dict[str, Any] = Field(default_factory=dict)
    description: Optional[str] = None


class AutomationCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    trigger_type: str = Field(default="manual")
    trigger_config: Dict[str, Any] = Field(default_factory=dict)
    steps: List[AutomationStepPayload]
    enabled: bool = True


class AutomationUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=120)
    description: Optional[str] = Field(default=None, max_length=500)
    trigger_type: Optional[str] = None
    trigger_config: Optional[Dict[str, Any]] = None
    steps: Optional[List[AutomationStepPayload]] = None
    enabled: Optional[bool] = None


class AutomationToggleRequest(BaseModel):
    enabled: bool


class AutomationScheduleRequest(BaseModel):
    frequency: str = "daily"
    time_of_day: str = "09:00"
    weekdays: List[int] = Field(default_factory=list)
    paused: bool = False


class AutomationExecuteRequest(BaseModel):
    confirmation_token: Optional[str] = None


class AutomationRetryRequest(BaseModel):
    allow_rerun_with_side_effects: bool = False
    confirmation_token: Optional[str] = None


def _error_response(exc: Exception) -> JSONResponse:
    if isinstance(exc, AutomationValidationError):
        return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content={"detail": str(exc)})
    if isinstance(exc, AutomationNotFoundError):
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": str(exc)})
    if isinstance(exc, AutomationAccessError):
        return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"detail": str(exc)})
    return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"detail": str(exc)})


def _register_routes(r: APIRouter) -> None:
    @r.post("/preview")
    async def preview_automation(request: Request, req: AutomationPreviewRequest):
        _verify_trusted_request(request)
        svc = get_shared_custom_automation_service()
        try:
            return await anyio.to_thread.run_sync(svc.preview_automation, req.prompt)
        except Exception as exc:
            return _error_response(exc)

    @r.get("")
    async def list_automations(request: Request):
        _verify_trusted_request(request)
        svc = get_shared_custom_automation_service()
        return {"automations": svc.list_automations()}

    @r.post("")
    async def create_automation(request: Request, req: AutomationCreateRequest):
        _verify_trusted_request(request)
        svc = get_shared_custom_automation_service()
        try:
            return svc.create_automation(
                name=req.name,
                description=req.description,
                trigger_type=req.trigger_type,
                trigger_config=req.trigger_config,
                steps=[s.model_dump() for s in req.steps],
                enabled=req.enabled,
            )
        except Exception as exc:
            return _error_response(exc)

    @r.get("/history")
    async def list_automation_history(
        request: Request,
        automation_id: Optional[str] = None,
        status: Optional[str] = None,
        trigger_type: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 25,
        offset: int = 0,
    ):
        _verify_trusted_request(request)
        svc = get_shared_custom_automation_service()
        return svc.list_execution_history(
            automation_id=automation_id,
            status_filter=status,
            trigger_type=trigger_type,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            offset=offset,
        )

    @r.delete("/history")
    async def clear_automation_history(request: Request, confirm: bool = False):
        _verify_trusted_request(request)
        svc = get_shared_custom_automation_service()
        try:
            return svc.clear_execution_history(confirm=confirm)
        except Exception as exc:
            return _error_response(exc)

    @r.get("/executions/{execution_id}")
    async def get_automation_execution(request: Request, execution_id: str):
        _verify_trusted_request(request)
        svc = get_shared_custom_automation_service()
        try:
            return svc.get_execution_status(execution_id)
        except Exception as exc:
            return _error_response(exc)

    @r.post("/executions/{execution_id}/cancel")
    async def cancel_automation_execution(request: Request, execution_id: str):
        _verify_trusted_request(request)
        svc = get_shared_custom_automation_service()
        try:
            return svc.cancel_execution(execution_id)
        except Exception as exc:
            return _error_response(exc)

    @r.post("/executions/{execution_id}/retry")
    async def retry_automation_execution(
        request: Request,
        execution_id: str,
        req: Optional[AutomationRetryRequest] = None,
    ):
        _verify_trusted_request(request)
        svc = get_shared_custom_automation_service()
        allow_rerun = req.allow_rerun_with_side_effects if req else False
        token = req.confirmation_token if req else None
        try:
            return await anyio.to_thread.run_sync(
                lambda: svc.retry_execution(
                    execution_id,
                    allow_rerun_with_side_effects=allow_rerun,
                    confirmation_token=token,
                )
            )
        except Exception as exc:
            return _error_response(exc)

    @r.delete("/executions/{execution_id}")
    async def delete_automation_execution(request: Request, execution_id: str):
        _verify_trusted_request(request)
        svc = get_shared_custom_automation_service()
        try:
            return svc.delete_execution_history(execution_id)
        except Exception as exc:
            return _error_response(exc)

    @r.get("/{automation_id}")
    async def get_automation(request: Request, automation_id: str):
        _verify_trusted_request(request)
        svc = get_shared_custom_automation_service()
        try:
            return svc.get_automation(automation_id)
        except Exception as exc:
            return _error_response(exc)

    @r.patch("/{automation_id}")
    async def update_automation(request: Request, automation_id: str, req: AutomationUpdateRequest):
        _verify_trusted_request(request)
        svc = get_shared_custom_automation_service()
        try:
            payload = req.model_dump(exclude_none=True)
            if "steps" in payload:
                payload["steps"] = [s.model_dump() for s in req.steps or []]
            return svc.update_automation(automation_id, payload)
        except Exception as exc:
            return _error_response(exc)

    @r.delete("/{automation_id}")
    async def delete_automation(request: Request, automation_id: str):
        _verify_trusted_request(request)
        svc = get_shared_custom_automation_service()
        try:
            return svc.delete_automation(automation_id)
        except Exception as exc:
            return _error_response(exc)

    @r.post("/{automation_id}/toggle")
    async def toggle_automation(request: Request, automation_id: str, req: AutomationToggleRequest):
        _verify_trusted_request(request)
        svc = get_shared_custom_automation_service()
        try:
            return svc.toggle_automation(automation_id, req.enabled)
        except Exception as exc:
            return _error_response(exc)

    @r.post("/{automation_id}/schedule")
    async def update_automation_schedule(request: Request, automation_id: str, req: AutomationScheduleRequest):
        _verify_trusted_request(request)
        svc = get_shared_custom_automation_service()
        try:
            return svc.update_schedule(automation_id, req.model_dump())
        except Exception as exc:
            return _error_response(exc)

    @r.post("/{automation_id}/execute")
    async def execute_automation(
        request: Request,
        automation_id: str,
        req: Optional[AutomationExecuteRequest] = None,
    ):
        _verify_trusted_request(request)
        svc = get_shared_custom_automation_service()
        token = req.confirmation_token if req else None
        try:
            return await anyio.to_thread.run_sync(
                lambda: svc.execute_automation(
                    automation_id,
                    confirmation_token=token,
                    trigger_source="manual",
                )
            )
        except Exception as exc:
            return _error_response(exc)


_register_routes(api_automations_router)
_register_routes(agent_automations_router)
