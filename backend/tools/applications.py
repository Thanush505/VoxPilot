"""
VoxPilot - Application Tool Interface
Provides Pydantic models and tool functions for the LangGraph agent to discover,
search, refresh, and launch Windows applications, Store apps, Settings pages, and utilities.
"""

from __future__ import annotations

import re
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator

from backend.automation.application_discovery import (
    ApplicationDiscoveryService,
    get_shared_discovery_service,
)
from backend.automation.application_launcher import (
    AmbiguousApplicationError,
    ApplicationLauncher,
    ApplicationLauncherError,
    ApplicationNotAllowedError,
    ApplicationNotFoundError,
)

logger = logging.getLogger(__name__)

# Forbidden characters indicating paths, shell chaining, or command injection
FORBIDDEN_CHARS_PATTERN = re.compile(r"[/\\;&|><`$\r\n]")


class LaunchApplicationInput(BaseModel):
    """Pydantic input schema for universal application launching."""
    application_name: str = Field(
        ...,
        description=(
            "The common or natural-language name of the installed application, Microsoft Store app, "
            "Windows Settings page, built-in utility, or system folder to launch "
            "(e.g. 'Notepad', 'WhatsApp', 'Spotify', 'Visual Studio Code', 'Bluetooth Settings', "
            "'Control Panel', 'Task Manager', 'File Explorer', 'Downloads')."
        ),
        min_length=1,
        max_length=100,
    )
    confirmation_token: Optional[str] = Field(
        default=None,
        description="Optional single-use confirmation token when launching sensitive system utilities.",
    )

    @field_validator("application_name")
    @classmethod
    def validate_application_name(cls, value: str) -> str:
        """Validate that the application name is clean, bounded, and contains no injection patterns."""
        if not isinstance(value, str):
            raise ValueError("Application name must be a string.")

        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Application name cannot be empty or whitespace only.")

        if len(cleaned) > 100:
            raise ValueError("Application name exceeds maximum allowed length of 100 characters.")

        # Disallow path separators (prevents directory traversal or arbitrary executable targeting)
        if "/" in cleaned or "\\" in cleaned:
            raise ValueError("Application name cannot contain path separators ('/' or '\\').")

        # Disallow shell metacharacters and chaining operators
        if FORBIDDEN_CHARS_PATTERN.search(cleaned):
            raise ValueError("Application name contains invalid characters or shell operators.")

        # Disallow starting with flag/option markers
        if cleaned.startswith("-") or cleaned.startswith("/"):
            raise ValueError("Application name cannot start with command-line flag characters.")

        return cleaned


class LaunchApplicationResult(BaseModel):
    """Pydantic output schema for application launch outcomes."""
    success: bool
    app_id: str
    display_name: str
    pid: Optional[int] = None
    status: str
    launch_method: Optional[str] = None
    source: Optional[str] = None
    candidates: List[str] = Field(default_factory=list)
    message: str
    timestamp: str


class OpenWindowsSettingsInput(BaseModel):
    """Pydantic input schema for opening an allowlisted Windows Settings page."""
    setting_name: str = Field(
        default="Windows Settings",
        description=(
            "Name of the Windows Settings page to open "
            "(e.g. 'Windows Settings', 'Bluetooth Settings', 'Wi-Fi Settings', 'Display Settings', "
            "'Sound Settings', 'Windows Update', 'Installed Apps')."
        ),
        min_length=1,
        max_length=120,
    )
    confirmation_token: Optional[str] = Field(
        default=None,
        description="Optional single-use confirmation token.",
    )

    @field_validator("setting_name")
    @classmethod
    def validate_setting_name(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            return "Windows Settings"
        if "/" in cleaned or "\\" in cleaned or FORBIDDEN_CHARS_PATTERN.search(cleaned):
            raise ValueError("Setting name contains invalid path or shell characters.")
        return cleaned


class SearchApplicationsInput(BaseModel):
    """Pydantic input schema for searching or listing installed applications."""
    query: str = Field(
        default="",
        description="Optional search filter (leave empty to list discovered applications).",
        max_length=120,
    )
    source: Optional[str] = Field(
        default=None,
        description="Optional discovery source filter (start_menu, registry, store_app, program_files, desktop_shortcut, settings_uri, system_utility, shell_folder).",
    )
    limit: int = Field(
        default=50,
        ge=1,
        le=200,
        description="Maximum number of applications to return.",
    )


class SearchApplicationsResult(BaseModel):
    """Pydantic output schema for application search/listing."""
    success: bool
    status: str
    query: str
    total_discovered: int
    returned_count: int
    applications: List[Dict[str, Any]] = Field(default_factory=list)
    message: str
    timestamp: str


class RefreshApplicationIndexInput(BaseModel):
    """Pydantic input schema for refreshing the application discovery index."""
    include_store_apps: bool = Field(
        default=True,
        description="Whether to include Microsoft Store / Appx applications during the refresh.",
    )


class RefreshApplicationIndexResult(BaseModel):
    """Pydantic output schema for refreshing the application index."""
    success: bool
    status: str
    total_applications: int
    sources_breakdown: Dict[str, int] = Field(default_factory=dict)
    last_refreshed: Optional[str] = None
    message: str
    timestamp: str


def launch_application_tool(
    data: LaunchApplicationInput,
    launcher: Optional[ApplicationLauncher] = None,
) -> LaunchApplicationResult:
    """Agent tool wrapper for launching a discovered Windows application safely.

    Args:
        data: Validated LaunchApplicationInput model containing the application name.
        launcher: Optional ApplicationLauncher instance (defaults to standard instance).

    Returns:
        LaunchApplicationResult: Structured outcome model.
    """
    active_launcher = launcher if launcher is not None else ApplicationLauncher()
    app_query = data.application_name

    try:
        raw_result = active_launcher.launch(app_query)
        return LaunchApplicationResult(
            success=True,
            app_id=raw_result["app_id"],
            display_name=raw_result["display_name"],
            pid=raw_result.get("pid"),
            status=raw_result["status"],
            launch_method=raw_result.get("launch_method"),
            source=raw_result.get("source"),
            candidates=[],
            message=raw_result["message"],
            timestamp=raw_result["timestamp"],
        )

    except AmbiguousApplicationError as err:
        logger.info(f"Ambiguous application query '{app_query}': {err.candidates}")
        return LaunchApplicationResult(
            success=False,
            app_id="ambiguous",
            display_name=app_query,
            pid=None,
            status="ambiguous",
            candidates=list(err.candidates),
            message=str(err),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    except ApplicationNotAllowedError as err:
        logger.warning(f"Application launch rejected by security policy: {err}")
        return LaunchApplicationResult(
            success=False,
            app_id="unknown",
            display_name=app_query,
            pid=None,
            status="rejected",
            message=str(err),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    except ApplicationNotFoundError as err:
        logger.warning(f"Requested application not found on disk: {err}")
        return LaunchApplicationResult(
            success=False,
            app_id="not_found",
            display_name=app_query,
            pid=None,
            status="not_found",
            message=str(err),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    except ApplicationLauncherError as err:
        logger.error(f"Operating system failed to launch application: {err}")
        return LaunchApplicationResult(
            success=False,
            app_id="error",
            display_name=app_query,
            pid=None,
            status="failed",
            message=str(err),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    except Exception as err:
        logger.exception(f"Unexpected error in launch_application_tool: {err}")
        return LaunchApplicationResult(
            success=False,
            app_id="error",
            display_name=app_query,
            pid=None,
            status="failed",
            message="An unexpected system error occurred while attempting to launch the application.",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )


def open_windows_settings_tool(
    data: OpenWindowsSettingsInput,
    launcher: Optional[ApplicationLauncher] = None,
) -> LaunchApplicationResult:
    """Agent tool wrapper for opening an allowlisted Windows Settings page."""
    query = data.setting_name.strip()
    if "setting" not in query.lower() and "update" not in query.lower():
        query = f"{query} settings"
    return launch_application_tool(
        LaunchApplicationInput(application_name=query),
        launcher=launcher,
    )


def search_applications_tool(
    data: SearchApplicationsInput,
    discovery_service: Optional[ApplicationDiscoveryService] = None,
) -> SearchApplicationsResult:
    """Agent tool wrapper for searching or listing discovered Windows applications."""
    service = discovery_service or get_shared_discovery_service()
    all_apps = service.list_applications(source_filter=data.source)
    query_str = (data.query or "").strip()

    if query_str:
        matched = service.search(query_str, limit=data.limit)
        if data.source:
            matched = [a for a in matched if a.source == data.source]
    else:
        matched = all_apps[: data.limit]

    serialized = [
        {
            "app_id": app.app_id,
            "display_name": app.display_name,
            "publisher": app.publisher,
            "source": app.source,
            "launch_method": app.launch_method,
            "requires_confirmation": app.requires_confirmation,
        }
        for app in matched
    ]

    sample_names = ", ".join(a.display_name for a in matched[:15])
    if query_str:
        msg = (
            f"Found {len(serialized)} installed application(s) matching '{query_str}': {sample_names}."
            if serialized
            else f"No installed applications matched '{query_str}'."
        )
    else:
        msg = (
            f"Found {len(all_apps)} discovered applications and utilities "
            f"(showing {len(serialized)}): {sample_names}."
        )

    return SearchApplicationsResult(
        success=True,
        status="completed",
        query=query_str,
        total_discovered=len(all_apps),
        returned_count=len(serialized),
        applications=serialized,
        message=msg,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


def refresh_applications_tool(
    data: RefreshApplicationIndexInput,
    discovery_service: Optional[ApplicationDiscoveryService] = None,
) -> RefreshApplicationIndexResult:
    """Agent tool wrapper for rescanning and refreshing the installed application index."""
    service = discovery_service or get_shared_discovery_service()
    apps = service.refresh_index(include_store_apps=data.include_store_apps)
    summary = service.get_index_summary()

    return RefreshApplicationIndexResult(
        success=True,
        status="completed",
        total_applications=len(apps),
        sources_breakdown=summary.get("sources_breakdown", {}),
        last_refreshed=summary.get("last_refreshed"),
        message=f"Refreshed application discovery index ({len(apps)} applications and system destinations indexed).",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
