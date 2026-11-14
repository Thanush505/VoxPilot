"""
VoxPilot - Local Multi-Profile REST API Endpoints
Exposes listing, retrieval, creation, updating, activation, and deletion
of strictly local VoxPilot application profiles.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import anyio
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from backend.api.agent import _verify_trusted_request
from backend.database.models import (
    VALID_ACCENT_COLORS,
    VALID_PROFILE_AVATARS,
    VALID_THEME_MODES,
)
from backend.profiles.service import (
    DefaultProfileDeletionError,
    DuplicateProfileError,
    InvalidProfileError,
    ProfilePrivacyError,
    get_shared_profile_service,
)

logger = logging.getLogger(__name__)


# ==============================================================================
# Pydantic Request & Response Schemas
# ==============================================================================

class ProfileCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, description="Profile display name.")
    description: Optional[str] = Field(default=None, max_length=250)
    avatar: str = Field(
        default="user",
        description=f"One of: {', '.join(VALID_PROFILE_AVATARS)}",
    )
    theme_mode: str = Field(
        default="dark",
        description=f"One of: {', '.join(VALID_THEME_MODES)}",
    )
    accent_color: str = Field(
        default="blue",
        description=f"One of: {', '.join(VALID_ACCENT_COLORS)}",
    )
    activate: bool = Field(default=False)


class ProfileUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=64)
    description: Optional[str] = Field(default=None, max_length=250)
    avatar: Optional[str] = Field(default=None)
    theme_mode: Optional[str] = Field(default=None)
    accent_color: Optional[str] = Field(default=None)


class ProfileItemResponse(BaseModel):
    id: str
    profile_id: str
    name: str
    normalized_name: str
    description: Optional[str] = None
    avatar: str
    theme_mode: str = "dark"
    accent_color: str = "blue"
    is_active: bool
    is_default: bool
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ProfileListResponse(BaseModel):
    profiles: List[ProfileItemResponse]
    active_profile: ProfileItemResponse
    total: int
    avatars: List[str] = Field(default_factory=lambda: list(VALID_PROFILE_AVATARS))
    theme_modes: List[str] = Field(default_factory=lambda: list(VALID_THEME_MODES))
    accent_colors: List[str] = Field(default_factory=lambda: list(VALID_ACCENT_COLORS))


class ProfileDeleteResponse(BaseModel):
    deleted: bool
    profile_id: str
    active_profile_id: str


# ==============================================================================
# Router Builder (dual prefix: /api/profiles and /agent/profiles)
# ==============================================================================

def _build_profiles_router(prefix: str) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["profiles"])

    @router.get("", response_model=ProfileListResponse)
    async def list_profiles():
        svc = get_shared_profile_service()
        profiles = await anyio.to_thread.run_sync(svc.list_profiles)
        active = await anyio.to_thread.run_sync(svc.get_active_profile)
        return ProfileListResponse(
            profiles=[ProfileItemResponse(**p) for p in profiles],
            active_profile=ProfileItemResponse(**active),
            total=len(profiles),
        )

    @router.get("/active", response_model=ProfileItemResponse)
    async def get_active_profile():
        svc = get_shared_profile_service()
        active = await anyio.to_thread.run_sync(svc.get_active_profile)
        return ProfileItemResponse(**active)

    @router.post("", response_model=ProfileItemResponse, status_code=status.HTTP_201_CREATED)
    async def create_profile(
        payload: ProfileCreateRequest,
        http_request: Request,
    ):
        _verify_trusted_request(http_request)
        svc = get_shared_profile_service()
        try:
            created = await anyio.to_thread.run_sync(
                lambda: svc.create_profile(
                    name=payload.name,
                    description=payload.description,
                    avatar=payload.avatar,
                    theme_mode=payload.theme_mode,
                    accent_color=payload.accent_color,
                    activate=payload.activate,
                )
            )
        except (InvalidProfileError, ProfilePrivacyError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
        except DuplicateProfileError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
        return ProfileItemResponse(**created)

    @router.patch("/{profile_id}", response_model=ProfileItemResponse)
    async def update_profile(
        profile_id: str,
        payload: ProfileUpdateRequest,
        http_request: Request,
    ):
        _verify_trusted_request(http_request)
        svc = get_shared_profile_service()
        kwargs: Dict[str, Any] = {}
        fields_set = payload.model_fields_set
        if "name" in fields_set:
            kwargs["name"] = payload.name
        if "description" in fields_set:
            kwargs["description"] = payload.description
        if "avatar" in fields_set:
            kwargs["avatar"] = payload.avatar
        if "theme_mode" in fields_set:
            kwargs["theme_mode"] = payload.theme_mode
        if "accent_color" in fields_set:
            kwargs["accent_color"] = payload.accent_color

        try:
            updated = await anyio.to_thread.run_sync(
                lambda: svc.update_profile(profile_id, **kwargs)
            )
        except (InvalidProfileError, ProfilePrivacyError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
        except DuplicateProfileError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))

        if updated is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Profile '{profile_id}' not found.",
            )
        return ProfileItemResponse(**updated)

    @router.post("/{profile_id}/activate", response_model=ProfileItemResponse)
    async def activate_profile(
        profile_id: str,
        http_request: Request,
    ):
        _verify_trusted_request(http_request)
        svc = get_shared_profile_service()
        activated = await anyio.to_thread.run_sync(lambda: svc.activate_profile(profile_id))
        if activated is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Profile '{profile_id}' not found.",
            )
        return ProfileItemResponse(**activated)

    @router.delete("/{profile_id}", response_model=ProfileDeleteResponse)
    async def delete_profile(
        profile_id: str,
        http_request: Request,
    ):
        _verify_trusted_request(http_request)
        svc = get_shared_profile_service()
        try:
            deleted = await anyio.to_thread.run_sync(lambda: svc.delete_profile(profile_id))
        except DefaultProfileDeletionError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Profile '{profile_id}' not found.",
            )
        active = await anyio.to_thread.run_sync(svc.get_active_profile)
        return ProfileDeleteResponse(
            deleted=True,
            profile_id=profile_id,
            active_profile_id=active["id"],
        )

    return router


api_profiles_router = _build_profiles_router("/api/profiles")
agent_profiles_router = _build_profiles_router("/agent/profiles")
