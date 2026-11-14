"""
VoxPilot - Screen Awareness & Local Vision API Endpoints
Provides explicit on-demand screen capture, profile-isolated ephemeral
management, honest vision-model status checks, and local visual analysis.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import anyio
from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from backend.ai.vision import (
    ScreenCaptureAccessDeniedError,
    ScreenCaptureAuthorizationError,
    ScreenCaptureError,
    ScreenCaptureNotFoundError,
    VisionAnalysisResult,
    VisionAvailabilityStatus,
    get_shared_screen_capture_service,
    get_shared_vision_provider,
)
from backend.api.agent import _resolve_api_profile_id, _verify_trusted_request, get_agent
from backend.automation.emergency_stop import EmergencyStopActiveError

logger = logging.getLogger(__name__)


class ScreenCaptureRequest(BaseModel):
    confirmed: bool = Field(
        False,
        description="Must be explicitly True to authorize on-demand primary display capture.",
    )
    profile_id: Optional[str] = Field(
        None,
        description="Target profile owning this ephemeral screenshot.",
    )
    include_preview: bool = Field(
        True,
        description="Whether to return a base64 preview data URL for the local UI.",
    )


class ScreenAnalyzeRequest(BaseModel):
    capture_id: str = Field(..., min_length=1, description="Ephemeral capture identifier.")
    prompt: str = Field(
        "What is on my screen?",
        min_length=1,
        description="User question about the captured screen.",
    )
    profile_id: Optional[str] = Field(
        None,
        description="Profile identifier verifying ownership of the capture.",
    )
    clear_after_use: bool = Field(
        True,
        description="Whether to delete the temporary screenshot after analysis.",
    )


def _resolve_profile(explicit_profile_id: Optional[str]) -> str:
    try:
        return _resolve_api_profile_id(get_agent(), explicit_profile_id)
    except Exception:
        clean = (explicit_profile_id or "").strip()
        return clean or "default"


def _build_screen_router(prefix: str) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["screen"])

    @router.get("/status", response_model=VisionAvailabilityStatus)
    async def get_vision_status():
        """Return honest local vision-model availability and hardware recommendations."""
        provider = get_shared_vision_provider()
        return await anyio.to_thread.run_sync(provider.check_availability)

    @router.post("/capture")
    async def capture_primary_screen(
        payload: ScreenCaptureRequest,
        request: Request,
    ) -> Dict[str, Any]:
        """Capture the primary display ONLY after explicit user confirmation."""
        _verify_trusted_request(request)
        if not payload.confirmed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Explicit user confirmation (confirmed=true) is required before capturing the screen.",
            )

        profile_id = _resolve_profile(payload.profile_id)
        service = get_shared_screen_capture_service()

        try:
            entry = await anyio.to_thread.run_sync(
                lambda: service.capture_primary_display(
                    profile_id=profile_id,
                    explicit_user_action=True,
                )
            )
        except ScreenCaptureAuthorizationError as err:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(err))
        except EmergencyStopActiveError as err:
            raise HTTPException(status_code=status.HTTP_423_LOCKED, detail=str(err))
        except ScreenCaptureError as err:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(err),
            )

        vision_status = await anyio.to_thread.run_sync(
            get_shared_vision_provider().check_availability
        )
        result = entry.to_public_dict(include_preview=payload.include_preview)
        result["vision_status"] = vision_status.model_dump()
        return result

    @router.delete("/capture/{capture_id}")
    async def delete_screen_capture(
        capture_id: str,
        request: Request,
        profile_id: Optional[str] = Query(None),
    ) -> Dict[str, Any]:
        """Explicitly remove an ephemeral screenshot and scrub temporary data."""
        _verify_trusted_request(request)
        resolved_profile = _resolve_profile(profile_id)
        service = get_shared_screen_capture_service()
        try:
            removed = service.delete_capture(capture_id=capture_id, profile_id=resolved_profile)
        except ScreenCaptureAccessDeniedError as err:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(err))
        return {
            "capture_id": capture_id,
            "removed": removed,
            "profile_id": resolved_profile,
        }

    @router.post("/analyze", response_model=VisionAnalysisResult)
    async def analyze_screen_capture(
        payload: ScreenAnalyzeRequest,
        request: Request,
    ) -> VisionAnalysisResult:
        """Analyze a previously captured ephemeral screenshot locally."""
        _verify_trusted_request(request)
        resolved_profile = _resolve_profile(payload.profile_id)
        service = get_shared_screen_capture_service()

        try:
            entry = service.get_capture(
                capture_id=payload.capture_id,
                profile_id=resolved_profile,
            )
        except ScreenCaptureAccessDeniedError as err:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(err))
        except ScreenCaptureNotFoundError as err:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err))

        provider = get_shared_vision_provider()
        try:
            return await anyio.to_thread.run_sync(
                lambda: provider.analyze_image(
                    image_base64=entry.image_base64,
                    user_prompt=payload.prompt,
                )
            )
        finally:
            if payload.clear_after_use:
                try:
                    service.delete_capture(
                        capture_id=payload.capture_id,
                        profile_id=resolved_profile,
                    )
                except Exception:
                    pass

    return router


api_screen_router = _build_screen_router("/api/screen")
agent_screen_router = _build_screen_router("/agent/screen")
