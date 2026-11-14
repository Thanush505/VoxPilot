"""
VoxPilot - Local Personalization REST API Endpoints
Exposes listing, retrieval, creation, updating, and deletion of strictly local,
secret-checked explicit user preferences.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import anyio
from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from backend.api.agent import _verify_trusted_request
from backend.database.models import VALID_PREFERENCE_CATEGORIES
from backend.personalization.service import (
    DuplicatePreferenceError,
    InvalidPreferenceCategoryError,
    InvalidPreferenceValueError,
    PreferencePrivacyError,
    get_shared_personalization_service,
)
from backend.profiles.service import get_shared_profile_service

logger = logging.getLogger(__name__)


def _resolve_profile_id(explicit: Optional[str]) -> str:
    if explicit and isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    try:
        return get_shared_profile_service().get_active_profile_id()
    except Exception:
        return "default"


# ==============================================================================
# Pydantic Request & Response Schemas
# ==============================================================================

class PreferenceCreateRequest(BaseModel):
    category: str = Field(
        default="communication",
        description=f"One of: {', '.join(VALID_PREFERENCE_CATEGORIES)}",
    )
    key: str = Field(..., min_length=1, max_length=100, description="Preference key/name.")
    value: str = Field(..., min_length=1, max_length=500, description="Preference value.")
    description: Optional[str] = Field(default=None, max_length=300)
    enabled: bool = Field(default=True)
    priority: int = Field(default=3, ge=1, le=5)
    profile_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    metadata: Optional[Dict[str, Any]] = Field(default=None)
    upsert: bool = Field(default=False)


class PreferenceUpdateRequest(BaseModel):
    category: Optional[str] = Field(default=None)
    key: Optional[str] = Field(default=None, min_length=1, max_length=100)
    value: Optional[str] = Field(default=None, min_length=1, max_length=500)
    description: Optional[str] = Field(default=None, max_length=300)
    enabled: Optional[bool] = Field(default=None)
    priority: Optional[int] = Field(default=None, ge=1, le=5)
    metadata: Optional[Dict[str, Any]] = Field(default=None)


class PreferenceItemResponse(BaseModel):
    id: str
    preference_id: str
    profile_id: str
    category: str
    key: str
    normalized_key: str
    value: str
    description: Optional[str] = None
    enabled: bool
    priority: int
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PreferenceListResponse(BaseModel):
    preferences: List[PreferenceItemResponse]
    total: int
    limit: int
    offset: int
    categories: List[str] = Field(default_factory=lambda: list(VALID_PREFERENCE_CATEGORIES))


class PreferenceDeleteResponse(BaseModel):
    deleted: bool
    preference_id: str


# ==============================================================================
# Router Builder (dual prefix: /api/personalization and /agent/personalization)
# ==============================================================================

def _build_personalization_router(prefix: str) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["personalization"])

    @router.get("", response_model=PreferenceListResponse)
    async def list_preferences(
        profile_id: Optional[str] = Query(default=None, min_length=1, max_length=64),
        category: Optional[str] = Query(default=None),
        enabled_only: bool = Query(default=False),
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ):
        svc = get_shared_personalization_service()
        target_profile = await anyio.to_thread.run_sync(lambda: _resolve_profile_id(profile_id))
        try:
            items = await anyio.to_thread.run_sync(
                lambda: svc.list_preferences(
                    profile_id=target_profile,
                    category=category,
                    enabled_only=enabled_only,
                    limit=limit,
                    offset=offset,
                )
            )
            total = await anyio.to_thread.run_sync(
                lambda: svc.count_preferences(
                    profile_id=target_profile,
                    category=category,
                    enabled_only=enabled_only,
                )
            )
        except InvalidPreferenceCategoryError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

        return PreferenceListResponse(
            preferences=[PreferenceItemResponse(**item) for item in items],
            total=total,
            limit=limit,
            offset=offset,
        )

    @router.get("/{preference_id}", response_model=PreferenceItemResponse)
    async def get_preference(
        preference_id: str,
        profile_id: Optional[str] = Query(default=None, min_length=1, max_length=64),
    ):
        svc = get_shared_personalization_service()
        target_profile = await anyio.to_thread.run_sync(lambda: _resolve_profile_id(profile_id))
        item = await anyio.to_thread.run_sync(
            lambda: svc.get_preference(preference_id, profile_id=target_profile)
        )
        if item is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Preference '{preference_id}' not found.",
            )
        return PreferenceItemResponse(**item)

    @router.post("", response_model=PreferenceItemResponse, status_code=status.HTTP_201_CREATED)
    async def create_preference(
        payload: PreferenceCreateRequest,
        http_request: Request,
    ):
        _verify_trusted_request(http_request)
        svc = get_shared_personalization_service()
        target_profile = await anyio.to_thread.run_sync(lambda: _resolve_profile_id(payload.profile_id))
        try:
            item = await anyio.to_thread.run_sync(
                lambda: svc.create_preference(
                    category=payload.category,
                    key=payload.key,
                    value=payload.value,
                    description=payload.description,
                    enabled=payload.enabled,
                    priority=payload.priority,
                    profile_id=target_profile,
                    metadata=payload.metadata,
                    upsert=payload.upsert,
                )
            )
        except (InvalidPreferenceCategoryError, InvalidPreferenceValueError, PreferencePrivacyError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
        except DuplicatePreferenceError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
        return PreferenceItemResponse(**item)

    @router.patch("/{preference_id}", response_model=PreferenceItemResponse)
    async def update_preference(
        preference_id: str,
        payload: PreferenceUpdateRequest,
        http_request: Request,
        profile_id: Optional[str] = Query(default=None, min_length=1, max_length=64),
    ):
        _verify_trusted_request(http_request)
        svc = get_shared_personalization_service()
        target_profile = await anyio.to_thread.run_sync(lambda: _resolve_profile_id(profile_id))
        kwargs: Dict[str, Any] = {"profile_id": target_profile}
        fields_set = payload.model_fields_set
        if "category" in fields_set:
            kwargs["category"] = payload.category
        if "key" in fields_set:
            kwargs["key"] = payload.key
        if "value" in fields_set:
            kwargs["value"] = payload.value
        if "description" in fields_set:
            kwargs["description"] = payload.description
        if "enabled" in fields_set:
            kwargs["enabled"] = payload.enabled
        if "priority" in fields_set:
            kwargs["priority"] = payload.priority
        if "metadata" in fields_set:
            kwargs["metadata"] = payload.metadata

        try:
            item = await anyio.to_thread.run_sync(
                lambda: svc.update_preference(preference_id, **kwargs)
            )
        except (InvalidPreferenceCategoryError, InvalidPreferenceValueError, PreferencePrivacyError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
        except DuplicatePreferenceError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))

        if item is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Preference '{preference_id}' not found.",
            )
        return PreferenceItemResponse(**item)

    @router.delete("/{preference_id}", response_model=PreferenceDeleteResponse)
    async def delete_preference(
        preference_id: str,
        http_request: Request,
        profile_id: Optional[str] = Query(default=None, min_length=1, max_length=64),
    ):
        _verify_trusted_request(http_request)
        svc = get_shared_personalization_service()
        target_profile = await anyio.to_thread.run_sync(lambda: _resolve_profile_id(profile_id))
        deleted = await anyio.to_thread.run_sync(
            lambda: svc.delete_preference(preference_id, profile_id=target_profile)
        )
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Preference '{preference_id}' not found.",
            )
        return PreferenceDeleteResponse(deleted=True, preference_id=preference_id)

    return router


api_personalization_router = _build_personalization_router("/api/personalization")
agent_personalization_router = _build_personalization_router("/agent/personalization")
