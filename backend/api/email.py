"""
VoxPilot - AI Email Assistant API Endpoints (Phase 4D)
Provides profile-isolated IMAP/SMTP account configuration (DPAPI-protected),
draft management, and confirmation-gated email sending.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import anyio
from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from backend.ai.email_assistant import (
    CredentialStorageError,
    EmailAccountConfig,
    EmailAssistantError,
    EmailAuthenticationError,
    EmailNotConfiguredError,
    EmailProviderUnavailableError,
    EmailValidationError,
    get_shared_email_assistant,
)
from backend.api.agent import _resolve_api_profile_id, _verify_trusted_request

logger = logging.getLogger(__name__)

api_email_router = APIRouter(prefix="/api/email", tags=["email"])


class EmailAccountConfigureRequest(BaseModel):
    profile_id: Optional[str] = None
    email_address: str = Field(..., min_length=3)
    password: str = Field(..., min_length=1)
    imap_host: str = Field(..., min_length=1)
    imap_port: int = Field(993, ge=1, le=65535)
    smtp_host: str = Field(..., min_length=1)
    smtp_port: int = Field(465, ge=1, le=65535)
    use_starttls: bool = False
    display_name: str = ""
    authorized_by_user: bool = False


class EmailDraftCreateRequest(BaseModel):
    profile_id: Optional[str] = None
    instruction: str = Field(..., min_length=1)
    to: Optional[List[str]] = None
    subject: Optional[str] = None
    reply_to_uid: Optional[str] = None


class EmailDraftUpdateRequest(BaseModel):
    profile_id: Optional[str] = None
    to: Optional[List[str]] = None
    subject: Optional[str] = None
    body: Optional[str] = None


class EmailDraftSendRequest(BaseModel):
    profile_id: Optional[str] = None
    confirmation_token: str = Field(..., min_length=1)
    approved: bool = False


@api_email_router.get("/account")
async def get_email_account_status(
    request: Request,
    profile_id: Optional[str] = Query(default=None),
) -> Dict[str, Any]:
    _verify_trusted_request(request)
    resolved_profile = _resolve_api_profile_id(profile_id, request)
    service = get_shared_email_assistant()
    return service.get_account_status(resolved_profile)


@api_email_router.post("/account")
async def configure_email_account(
    payload: EmailAccountConfigureRequest,
    request: Request,
) -> Dict[str, Any]:
    _verify_trusted_request(request)
    resolved_profile = _resolve_api_profile_id(payload.profile_id, request)
    service = get_shared_email_assistant()
    cfg = EmailAccountConfig(
        profile_id=resolved_profile,
        email_address=payload.email_address.strip(),
        imap_host=payload.imap_host.strip(),
        imap_port=payload.imap_port,
        smtp_host=payload.smtp_host.strip(),
        smtp_port=payload.smtp_port,
        use_starttls=payload.use_starttls,
        display_name=payload.display_name.strip(),
        authorized_by_user=payload.authorized_by_user,
    )
    try:
        return service.configure_account(cfg, payload.password)
    except (EmailValidationError, CredentialStorageError) as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err


@api_email_router.delete("/account")
async def disconnect_email_account(
    request: Request,
    profile_id: Optional[str] = Query(default=None),
) -> Dict[str, Any]:
    _verify_trusted_request(request)
    resolved_profile = _resolve_api_profile_id(profile_id, request)
    service = get_shared_email_assistant()
    removed = service.disconnect_account(resolved_profile)
    return {"success": True, "disconnected": removed, "profile_id": resolved_profile}


@api_email_router.get("/drafts")
async def list_email_drafts(
    request: Request,
    profile_id: Optional[str] = Query(default=None),
) -> Dict[str, Any]:
    _verify_trusted_request(request)
    resolved_profile = _resolve_api_profile_id(profile_id, request)
    service = get_shared_email_assistant()
    return {"drafts": service.list_drafts(resolved_profile)}


@api_email_router.post("/drafts")
async def create_email_draft(
    payload: EmailDraftCreateRequest,
    request: Request,
) -> Dict[str, Any]:
    _verify_trusted_request(request)
    resolved_profile = _resolve_api_profile_id(payload.profile_id, request)
    service = get_shared_email_assistant()
    return await anyio.to_thread.run_sync(
        lambda: service.generate_draft(
            profile_id=resolved_profile,
            instruction=payload.instruction,
            to=payload.to,
            subject=payload.subject,
            reply_to_uid=payload.reply_to_uid,
        )
    )


@api_email_router.patch("/drafts/{draft_id}")
async def update_email_draft(
    draft_id: str,
    payload: EmailDraftUpdateRequest,
    request: Request,
) -> Dict[str, Any]:
    _verify_trusted_request(request)
    resolved_profile = _resolve_api_profile_id(payload.profile_id, request)
    service = get_shared_email_assistant()
    try:
        draft = service.update_draft(
            profile_id=resolved_profile,
            draft_id=draft_id,
            to=payload.to,
            subject=payload.subject,
            body=payload.body,
        )
        return {"success": True, "draft": draft.to_dict()}
    except EmailValidationError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err


@api_email_router.post("/drafts/{draft_id}/request-send")
async def request_email_send_confirmation(
    draft_id: str,
    request: Request,
    profile_id: Optional[str] = Query(default=None),
) -> Dict[str, Any]:
    _verify_trusted_request(request)
    resolved_profile = _resolve_api_profile_id(profile_id, request)
    service = get_shared_email_assistant()
    try:
        return service.request_send_confirmation(resolved_profile, draft_id)
    except EmailValidationError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err


@api_email_router.post("/drafts/{draft_id}/send")
async def confirm_and_send_email(
    draft_id: str,
    payload: EmailDraftSendRequest,
    request: Request,
) -> Dict[str, Any]:
    _verify_trusted_request(request)
    resolved_profile = _resolve_api_profile_id(payload.profile_id, request)
    if not payload.approved:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Explicit user approval (approved=true) is required to send email.",
        )
    service = get_shared_email_assistant()
    try:
        service.confirmation_manager.approve_confirmation(payload.confirmation_token.strip())
    except Exception:
        pass
    try:
        return await anyio.to_thread.run_sync(
            lambda: service.confirm_and_send_draft(
                profile_id=resolved_profile,
                draft_id=draft_id,
                confirmation_token=payload.confirmation_token,
            )
        )
    except (EmailNotConfiguredError, EmailAuthenticationError, EmailProviderUnavailableError, EmailAssistantError) as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err
