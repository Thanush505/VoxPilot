"""
VoxPilot - Window Management Tool Interface
Provides Pydantic models and tool functions for discovering, inspecting, and managing approved application windows.
"""

from __future__ import annotations

import re
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, field_validator, model_validator

from backend.automation.application_launcher import ApplicationNotAllowedError
from backend.automation.window_manager import (
    WindowManager,
    WindowManagerError,
    WindowNotFoundError,
    WindowNotAllowedError,
    WindowOperationError,
    WindowAmbiguityError,
)

logger = logging.getLogger(__name__)

# Forbidden characters indicating path traversal, shell chaining, or command injection
FORBIDDEN_CHARS_PATTERN = re.compile(r"[/\\;&|><`$\r\n]")


class FindWindowsInput(BaseModel):
    """Pydantic input schema for finding application windows."""
    application_name: Optional[str] = Field(
        None,
        description="Optional name or alias of the approved application to filter by (e.g. 'Notepad', 'Calculator', 'Chrome', 'VS Code').",
        max_length=100,
    )
    title_pattern: Optional[str] = Field(
        None,
        description="Optional regex or substring pattern to match against window titles.",
        max_length=200,
    )
    visible_only: bool = Field(
        True,
        description="Whether to include only visible windows (filters out background/system windows).",
    )

    @field_validator("application_name")
    @classmethod
    def validate_application_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        if len(cleaned) > 100:
            raise ValueError("Application name exceeds maximum allowed length of 100 characters.")
        if "/" in cleaned or "\\" in cleaned:
            raise ValueError("Application name cannot contain path separators ('/' or '\\').")
        if FORBIDDEN_CHARS_PATTERN.search(cleaned):
            raise ValueError("Application name contains invalid characters or shell operators.")
        if cleaned.startswith("-") or cleaned.startswith("/"):
            raise ValueError("Application name cannot start with command-line flag characters.")
        return cleaned

    @field_validator("title_pattern")
    @classmethod
    def validate_title_pattern(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        if len(cleaned) > 200:
            raise ValueError("Title pattern exceeds maximum allowed length of 200 characters.")
        if FORBIDDEN_CHARS_PATTERN.search(cleaned):
            raise ValueError("Title pattern contains invalid characters or shell operators.")
        return cleaned


class FindWindowsResult(BaseModel):
    """Pydantic output schema for window discovery."""
    success: bool
    windows: List[Dict[str, Any]] = Field(default_factory=list)
    count: int = 0
    status: str
    message: str
    timestamp: str


class ManageWindowInput(BaseModel):
    """Pydantic input schema for window management operations."""
    action: str = Field(
        ...,
        description="Management action to execute: 'activate', 'minimize', 'maximize', 'restore'.",
    )
    hwnd: Optional[int] = Field(
        None,
        description="Specific window handle (HWND) to target. Required if multiple windows exist for the application.",
    )
    application_name: Optional[str] = Field(
        None,
        description="Optional application name to target if hwnd is not provided. Fails with ambiguity if multiple windows exist.",
        max_length=100,
    )

    @field_validator("action")
    @classmethod
    def validate_action(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if cleaned not in {"activate", "minimize", "maximize", "restore"}:
            raise ValueError(f"Action '{value}' is invalid. Allowed actions: 'activate', 'minimize', 'maximize', 'restore'.")
        return cleaned

    @field_validator("hwnd")
    @classmethod
    def validate_hwnd(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and value <= 0:
            raise ValueError("Window handle (hwnd) must be a positive integer.")
        return value

    @field_validator("application_name")
    @classmethod
    def validate_application_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        if len(cleaned) > 100:
            raise ValueError("Application name exceeds maximum allowed length of 100 characters.")
        if "/" in cleaned or "\\" in cleaned:
            raise ValueError("Application name cannot contain path separators ('/' or '\\').")
        if FORBIDDEN_CHARS_PATTERN.search(cleaned):
            raise ValueError("Application name contains invalid characters or shell operators.")
        return cleaned

    @model_validator(mode="after")
    def validate_target(self) -> "ManageWindowInput":
        if self.hwnd is None and not self.application_name:
            raise ValueError("Either 'hwnd' or 'application_name' must be provided to manage a window.")
        return self


class ManageWindowResult(BaseModel):
    """Pydantic output schema for window management operations."""
    success: bool
    action: str
    window: Optional[Dict[str, Any]] = None
    matching_windows: Optional[List[Dict[str, Any]]] = None
    status: str
    message: str
    timestamp: str


def find_windows_tool(
    data: FindWindowsInput,
    manager: Optional[WindowManager] = None,
) -> FindWindowsResult:
    """Agent tool wrapper for discovering windows belonging to approved applications.

    Args:
        data: Validated FindWindowsInput model.
        manager: Optional WindowManager instance.

    Returns:
        FindWindowsResult: Structured discovery result with list of matching windows.
    """
    active_mgr = manager if manager is not None else WindowManager()

    try:
        windows = active_mgr.find_windows(
            app_name=data.application_name,
            title_pattern=data.title_pattern,
            visible_only=data.visible_only,
        )
        serialized = [w.to_dict() for w in windows]
        return FindWindowsResult(
            success=True,
            windows=serialized,
            count=len(serialized),
            status="success",
            message=f"Found {len(serialized)} matching window(s).",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    except ApplicationNotAllowedError as err:
        logger.warning(f"Window search rejected by allowlist: {err}")
        return FindWindowsResult(
            success=False,
            windows=[],
            count=0,
            status="not_allowed",
            message=str(err),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    except WindowManagerError as err:
        logger.error(f"Window discovery error: {err}")
        return FindWindowsResult(
            success=False,
            windows=[],
            count=0,
            status="error",
            message=str(err),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    except Exception as err:
        logger.exception(f"Unexpected error in find_windows_tool: {err}")
        return FindWindowsResult(
            success=False,
            windows=[],
            count=0,
            status="error",
            message="An unexpected system error occurred while searching for windows.",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )


def manage_window_tool(
    data: ManageWindowInput,
    manager: Optional[WindowManager] = None,
) -> ManageWindowResult:
    """Agent tool wrapper for managing an approved application window.

    Args:
        data: Validated ManageWindowInput model.
        manager: Optional WindowManager instance.

    Returns:
        ManageWindowResult: Structured outcome model.
    """
    active_mgr = manager if manager is not None else WindowManager()

    try:
        updated_info = active_mgr.manage_window(
            action=data.action,
            hwnd=data.hwnd,
            app_name=data.application_name,
        )
        return ManageWindowResult(
            success=True,
            action=data.action,
            window=updated_info.to_dict(),
            matching_windows=None,
            status="success",
            message=f"Window {updated_info.hwnd} ('{updated_info.title}') successfully {data.action}d.",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    except WindowAmbiguityError as err:
        logger.warning(f"Ambiguous window management request: {err}")
        return ManageWindowResult(
            success=False,
            action=data.action,
            window=None,
            matching_windows=[w.to_dict() for w in err.matching_windows],
            status="ambiguous",
            message=str(err),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    except WindowNotFoundError as err:
        logger.warning(f"Target window not found: {err}")
        return ManageWindowResult(
            success=False,
            action=data.action,
            window=None,
            matching_windows=None,
            status="not_found",
            message=str(err),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    except WindowNotAllowedError as err:
        logger.warning(f"Target window rejected by allowlist: {err}")
        return ManageWindowResult(
            success=False,
            action=data.action,
            window=None,
            matching_windows=None,
            status="rejected",
            message=str(err),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    except WindowOperationError as err:
        logger.error(f"Window operation failed: {err}")
        err_msg = str(err)
        status = "activation_restricted" if (data.action == "activate" or "foreground activation" in err_msg.lower()) else "failed"
        return ManageWindowResult(
            success=False,
            action=data.action,
            window=None,
            matching_windows=None,
            status=status,
            message=err_msg,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    except ApplicationNotAllowedError as err:
        logger.warning(f"Application name not allowed: {err}")
        return ManageWindowResult(
            success=False,
            action=data.action,
            window=None,
            matching_windows=None,
            status="rejected",
            message=str(err),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    except Exception as err:
        logger.exception(f"Unexpected error in manage_window_tool: {err}")
        return ManageWindowResult(
            success=False,
            action=data.action,
            window=None,
            matching_windows=None,
            status="error",
            message="An unexpected system error occurred during window management.",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

