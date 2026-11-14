"""
VoxPilot - Local AI Memory REST API Endpoints
Exposes paginated listing, full-text search, creation, updating, and deletion
of strictly local, secret-scrubbed long-term AI memories.
"""

from __future__ import annotations

import logging
from typing import Dict, Any, List, Optional

import anyio
from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from backend.api.agent import _verify_trusted_request
from backend.database.models import VALID_MEMORY_TYPES
from backend.memory.long_term_memory import (
    InvalidMemoryTypeError,
    MemoryPrivacyError,
    get_shared_long_term_memory_service,
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

class MemoryCreateRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=1000, description="Memory fact, preference, or instruction.")
    memory_type: str = Field(
        default="general_context",
        description=f"One of: {', '.join(VALID_MEMORY_TYPES)}",
    )
    importance: int = Field(default=3, ge=1, le=5, description="Importance score from 1 (low) to 5 (high).")
    profile_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    source_conversation_id: Optional[str] = Field(default=None, max_length=128)
    metadata: Optional[Dict[str, Any]] = Field(default=None)


class MemoryUpdateRequest(BaseModel):
    content: Optional[str] = Field(default=None, min_length=1, max_length=1000)
    memory_type: Optional[str] = Field(default=None)
    importance: Optional[int] = Field(default=None, ge=1, le=5)
    metadata: Optional[Dict[str, Any]] = Field(default=None)


class MemoryItemResponse(BaseModel):
    id: str
    memory_id: str
    profile_id: str
    memory_type: str
    content: str
    normalized_content: str
    source_conversation_id: Optional[str] = None
    importance: int
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    last_accessed_at: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    deduplicated: Optional[bool] = None


class MemoryListResponse(BaseModel):
    memories: List[MemoryItemResponse] = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0
    local_only: bool = True


class MemorySearchResponse(BaseModel):
    query: str
    memories: List[MemoryItemResponse] = Field(default_factory=list)
    count: int = 0
    local_only: bool = True


class MemoryDeleteResponse(BaseModel):
    status: str = "deleted"
    deleted: bool = True
    memory_id: str


def _build_memory_router(prefix: str) -> APIRouter:
    """Register memory REST endpoints cleanly on a router prefix without duplicate logic."""
    router = APIRouter(prefix=prefix, tags=["memory"])

    @router.get("", response_model=MemoryListResponse)
    async def list_memories(
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        memory_type: Optional[str] = Query(None),
        profile_id: Optional[str] = Query(None, min_length=1, max_length=64),
    ):
        svc = get_shared_long_term_memory_service()
        target_profile = await anyio.to_thread.run_sync(lambda: _resolve_profile_id(profile_id))
        try:
            result = await anyio.to_thread.run_sync(
                lambda: svc.list_memories(
                    limit=limit,
                    offset=offset,
                    memory_type=memory_type,
                    profile_id=target_profile,
                )
            )
            return MemoryListResponse(**result, local_only=True)
        except InvalidMemoryTypeError as err:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err))

    # NOTE: /search must precede /{memory_id}
    @router.get("/search", response_model=MemorySearchResponse)
    async def search_memories(
        q: str = Query("", description="Full-text search query."),
        limit: int = Query(20, ge=1, le=50),
        memory_type: Optional[str] = Query(None),
        profile_id: Optional[str] = Query(None, min_length=1, max_length=64),
    ):
        svc = get_shared_long_term_memory_service()
        target_profile = await anyio.to_thread.run_sync(lambda: _resolve_profile_id(profile_id))
        try:
            matches = await anyio.to_thread.run_sync(
                lambda: svc.search_memories(
                    query=q,
                    limit=limit,
                    memory_type=memory_type,
                    profile_id=target_profile,
                )
            )
            return MemorySearchResponse(
                query=q,
                memories=matches,
                count=len(matches),
                local_only=True,
            )
        except InvalidMemoryTypeError as err:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err))

    @router.get("/{memory_id}", response_model=MemoryItemResponse)
    async def get_memory(
        memory_id: str,
        profile_id: Optional[str] = Query(None, min_length=1, max_length=64),
    ):
        svc = get_shared_long_term_memory_service()
        target_profile = await anyio.to_thread.run_sync(lambda: _resolve_profile_id(profile_id))
        item = await anyio.to_thread.run_sync(
            lambda: svc.get_memory(memory_id, touch=True, profile_id=target_profile)
        )
        if item is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Memory '{memory_id}' not found.",
            )
        return item

    @router.post("", response_model=MemoryItemResponse, status_code=status.HTTP_201_CREATED)
    async def create_memory(payload: MemoryCreateRequest, http_request: Request):
        _verify_trusted_request(http_request)
        svc = get_shared_long_term_memory_service()
        target_profile = await anyio.to_thread.run_sync(lambda: _resolve_profile_id(payload.profile_id))
        try:
            created = await anyio.to_thread.run_sync(
                lambda: svc.create_memory(
                    content=payload.content,
                    memory_type=payload.memory_type,
                    importance=payload.importance,
                    profile_id=target_profile,
                    source_conversation_id=payload.source_conversation_id,
                    metadata=payload.metadata,
                )
            )
            return created
        except (MemoryPrivacyError, InvalidMemoryTypeError, ValueError) as err:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err))

    @router.patch("/{memory_id}", response_model=MemoryItemResponse)
    async def update_memory(
        memory_id: str,
        payload: MemoryUpdateRequest,
        http_request: Request,
        profile_id: Optional[str] = Query(None, min_length=1, max_length=64),
    ):
        _verify_trusted_request(http_request)
        svc = get_shared_long_term_memory_service()
        target_profile = await anyio.to_thread.run_sync(lambda: _resolve_profile_id(profile_id))
        try:
            updated = await anyio.to_thread.run_sync(
                lambda: svc.update_memory(
                    memory_id=memory_id,
                    content=payload.content,
                    memory_type=payload.memory_type,
                    importance=payload.importance,
                    metadata=payload.metadata,
                    profile_id=target_profile,
                )
            )
        except (MemoryPrivacyError, InvalidMemoryTypeError, ValueError) as err:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err))

        if updated is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Memory '{memory_id}' not found.",
            )
        return updated

    @router.delete("/{memory_id}", response_model=MemoryDeleteResponse)
    async def delete_memory(
        memory_id: str,
        http_request: Request,
        profile_id: Optional[str] = Query(None, min_length=1, max_length=64),
    ):
        _verify_trusted_request(http_request)
        svc = get_shared_long_term_memory_service()
        target_profile = await anyio.to_thread.run_sync(lambda: _resolve_profile_id(profile_id))
        deleted = await anyio.to_thread.run_sync(
            lambda: svc.delete_memory(memory_id, profile_id=target_profile)
        )
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Memory '{memory_id}' not found.",
            )
        return MemoryDeleteResponse(status="deleted", deleted=True, memory_id=memory_id)

    return router


api_memory_router = _build_memory_router("/api/memory")
agent_memory_router = _build_memory_router("/agent/memory")
