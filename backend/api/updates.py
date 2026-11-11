"""
VoxPilot - Software Update & Version API Endpoints
Exposes REST endpoints for querying the installed version, checking GitHub Releases
for updates, managing update preferences, downloading & verifying the official
Windows installer, and triggering the detached external updater process.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import anyio
from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from backend.api.agent import _verify_trusted_request
from backend.updater.update_service import (
    ChecksumVerificationError,
    InvalidInstallerAssetError,
    UntrustedDownloadUrlError,
    UpdateError,
    get_shared_update_service,
)

logger = logging.getLogger(__name__)

api_updates_router = APIRouter(prefix="/api", tags=["updates"])
agent_updates_router = APIRouter(prefix="/agent", tags=["updates"])


class UpdateCheckRequest(BaseModel):
    force: bool = Field(default=False, description="Bypass check cache and query GitHub immediately")


class UpdateDownloadRequest(BaseModel):
    background: bool = Field(
        default=True,
        description="Run download asynchronously in background thread while polling status",
    )


class UpdateInstallRequest(BaseModel):
    confirm: bool = Field(
        ...,
        description="Explicit user confirmation required before launching the external installer",
    )
    relaunch: bool = Field(
        default=True,
        description="Whether the external updater should relaunch VoxPilot after installation",
    )
    shutdown_app: bool = Field(
        default=True,
        description="Whether to initiate graceful VoxPilot shutdown after launching updater",
    )


class UpdateDismissRequest(BaseModel):
    version: Optional[str] = Field(
        default=None,
        description="Version string to dismiss notifications for ('Remind Me Later')",
    )


class UpdateSettingsRequest(BaseModel):
    auto_check_enabled: Optional[bool] = Field(
        default=None,
        description="Automatically check for updates in the background on startup",
    )
    channel: Optional[str] = Field(
        default=None,
        description="Update release channel: 'stable' or 'beta'",
    )
    release_repo: Optional[str] = Field(
        default=None,
        description="GitHub repository slug ('owner/repo') for official releases",
    )


async def _handle_get_version() -> Dict[str, Any]:
    svc = get_shared_update_service()
    return svc.get_version_info()


async def _handle_get_status() -> Dict[str, Any]:
    svc = get_shared_update_service()
    return svc.get_status()


async def _handle_check_updates(force: bool) -> Dict[str, Any]:
    svc = get_shared_update_service()
    return await anyio.to_thread.run_sync(lambda: svc.check_for_updates(force=force))


async def _handle_start_download(request: Request, req: UpdateDownloadRequest) -> Dict[str, Any]:
    _verify_trusted_request(request)
    svc = get_shared_update_service()
    try:
        return await anyio.to_thread.run_sync(
            lambda: svc.start_download(background=req.background)
        )
    except (UntrustedDownloadUrlError, InvalidInstallerAssetError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except UpdateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


async def _handle_cancel_download(request: Request) -> Dict[str, Any]:
    _verify_trusted_request(request)
    svc = get_shared_update_service()
    return await anyio.to_thread.run_sync(svc.cancel_download)


async def _handle_dismiss_update(request: Request, req: UpdateDismissRequest) -> Dict[str, Any]:
    _verify_trusted_request(request)
    svc = get_shared_update_service()
    return svc.dismiss_notification(version=req.version)


async def _handle_get_settings() -> Dict[str, Any]:
    svc = get_shared_update_service()
    return svc.get_settings()


async def _handle_update_settings(request: Request, req: UpdateSettingsRequest) -> Dict[str, Any]:
    _verify_trusted_request(request)
    svc = get_shared_update_service()
    updates_dict: Dict[str, Any] = {}
    if req.auto_check_enabled is not None:
        updates_dict["auto_check_enabled"] = req.auto_check_enabled
    if req.channel is not None:
        updates_dict["channel"] = req.channel
    if req.release_repo is not None:
        updates_dict["release_repo"] = req.release_repo

    try:
        saved = svc.update_settings(updates_dict)
        return {
            "status": "updated",
            "settings": saved,
        }
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


async def _handle_trigger_install(request: Request, req: UpdateInstallRequest) -> Dict[str, Any]:
    _verify_trusted_request(request)
    if not req.confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Explicit user confirmation (confirm=true) is required to install an update.",
        )
    svc = get_shared_update_service()
    try:
        return await anyio.to_thread.run_sync(
            lambda: svc.trigger_install(
                confirm=req.confirm,
                relaunch=req.relaunch,
                shutdown_app=req.shutdown_app,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ChecksumVerificationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except UpdateError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


def _register_routes(r: APIRouter) -> None:
    @r.get("/version")
    async def get_version() -> Dict[str, Any]:
        """Return installed VoxPilot version and channel metadata."""
        return await _handle_get_version()

    @r.get("/updates/status")
    async def get_update_status() -> Dict[str, Any]:
        """Return current update check, download, verification, and installation state."""
        return await _handle_get_status()

    @r.get("/updates/check")
    async def check_updates_get(
        force: bool = Query(default=False, description="Bypass cache and force a fresh check"),
    ) -> Dict[str, Any]:
        """Check GitHub Releases for newer official VoxPilot versions."""
        return await _handle_check_updates(force=force)

    @r.post("/updates/check")
    async def check_updates_post(
        request: Request,
        req: Optional[UpdateCheckRequest] = None,
    ) -> Dict[str, Any]:
        """Trigger a manual or forced GitHub release check."""
        _verify_trusted_request(request)
        force_flag = req.force if req is not None else True
        return await _handle_check_updates(force=force_flag)

    @r.post("/updates/download")
    async def download_update(
        request: Request,
        req: Optional[UpdateDownloadRequest] = None,
    ) -> Dict[str, Any]:
        """Download and verify the official VoxPilot-Setup.exe installer."""
        body = req or UpdateDownloadRequest()
        return await _handle_start_download(request, body)

    @r.post("/updates/cancel")
    async def cancel_update_download(request: Request) -> Dict[str, Any]:
        """Cancel an in-progress update download and remove incomplete files."""
        return await _handle_cancel_download(request)

    @r.post("/updates/dismiss")
    async def dismiss_update_banner(
        request: Request,
        req: Optional[UpdateDismissRequest] = None,
    ) -> Dict[str, Any]:
        """Dismiss the update notification banner ('Remind Me Later') for the current version."""
        body = req or UpdateDismissRequest()
        return await _handle_dismiss_update(request, body)

    @r.get("/updates/settings")
    async def get_update_settings() -> Dict[str, Any]:
        """Retrieve persistent update settings."""
        return await _handle_get_settings()

    @r.post("/updates/settings")
    async def update_update_settings(
        request: Request,
        req: UpdateSettingsRequest,
    ) -> Dict[str, Any]:
        """Save persistent update settings (auto-check toggle, channel, repository)."""
        return await _handle_update_settings(request, req)

    @r.post("/updates/install")
    async def install_update(
        request: Request,
        req: UpdateInstallRequest,
    ) -> Dict[str, Any]:
        """Launch the separate external Windows updater after explicit user confirmation."""
        return await _handle_trigger_install(request, req)


_register_routes(api_updates_router)
_register_routes(agent_updates_router)

