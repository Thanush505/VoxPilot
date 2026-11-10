"""
VoxPilot - Application Tool Interface
Provides Pydantic models and tool functions for the LangGraph agent to launch approved applications.
"""

from __future__ import annotations

import re
import logging
from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field, field_validator

from backend.automation.application_launcher import (
    ApplicationLauncher,
    ApplicationLauncherError,
    ApplicationNotAllowedError,
    ApplicationNotFoundError,
)

logger = logging.getLogger(__name__)

# Forbidden characters indicating paths, shell chaining, or command injection
FORBIDDEN_CHARS_PATTERN = re.compile(r"[/\\;&|><`$\r\n]")


class LaunchApplicationInput(BaseModel):
    """Pydantic input schema for application launching."""
    application_name: str = Field(
        ...,
        description="The common or natural-language name of the approved application to launch (e.g. 'Notepad', 'Calculator', 'Chrome', 'VS Code').",
        min_length=1,
        max_length=100,
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
    message: str
    timestamp: str


def launch_application_tool(
    data: LaunchApplicationInput,
    launcher: Optional[ApplicationLauncher] = None,
) -> LaunchApplicationResult:
    """Agent tool wrapper for launching an approved Windows application safely.

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
            pid=raw_result["pid"],
            status=raw_result["status"],
            message=raw_result["message"],
            timestamp=raw_result["timestamp"],
        )

    except ApplicationNotAllowedError as err:
        logger.warning(f"Application launch rejected by allowlist: {err}")
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
        logger.warning(f"Approved application executable not found on disk: {err}")
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

