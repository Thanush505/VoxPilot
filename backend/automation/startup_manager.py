"""
VoxPilot - Windows Wake-on-Startup Manager (Phase 5A)

Manages optional, per-user Windows sign-in launch via the standard Windows
Registry Run key (`HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run`).

Design & Security Guarantees:
- Strictly per-user (`HKEY_CURRENT_USER`); never requires administrator privileges
  and never modifies system-wide (`HKLM`) startup configuration.
- Idempotent single-value registration (`"VoxPilot"`) preventing duplicate entries.
- Validates executable / launcher paths and working directory before enabling.
- Supports both PyInstaller packaged mode (`VoxPilot.exe`) and development mode
  (`python.exe launcher.py`).
- Never enables startup silently without explicit user action.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from backend.paths import get_project_root, is_frozen

if sys.platform == "win32":
    import winreg
else:  # pragma: no cover - non-Windows fallback
    winreg = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_VALUE_NAME = "VoxPilot"


class StartupManagerError(Exception):
    """Base error for Wake-on-Startup operations."""


class StartupUnsupportedError(StartupManagerError):
    """Raised when Windows startup registration is not supported on this platform."""


class StartupConfigurationError(StartupManagerError):
    """Raised when the target executable or launcher path is invalid or missing."""


class StartupPermissionError(StartupManagerError):
    """Raised when Windows denies access to the per-user Run key."""


@dataclass
class StartupStatus:
    """Public status of VoxPilot Wake-on-Startup configuration."""
    enabled: bool
    supported: bool
    mode: str  # "packaged" or "development"
    mechanism: str = "windows_hkcu_run"
    target_valid: bool = True
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "supported": self.supported,
            "mode": self.mode,
            "mechanism": self.mechanism,
            "target_valid": self.target_valid,
            "reason": self.reason,
        }


class WindowsStartupManager:
    """Per-user Windows sign-in startup manager using HKCU Run registry key."""

    def __init__(
        self,
        executable_path: Optional[Path] = None,
        launcher_path: Optional[Path] = None,
        frozen_override: Optional[bool] = None,
        registry_module: Optional[Any] = None,
    ):
        self._executable_override = executable_path
        self._launcher_override = launcher_path
        self._frozen_override = frozen_override
        self._winreg = registry_module if registry_module is not None else winreg

    def _is_frozen_mode(self) -> bool:
        if self._frozen_override is not None:
            return self._frozen_override
        return is_frozen()

    def _get_mode_label(self) -> str:
        return "packaged" if self._is_frozen_mode() else "development"

    def build_launch_command(self) -> str:
        """Validate and construct the exact startup command for packaged or dev mode."""
        exe_path = Path(self._executable_override or sys.executable)
        if not exe_path.parent.exists() or not exe_path.parent.is_dir():
            raise StartupConfigurationError("Application executable directory does not exist.")
        if not exe_path.exists() or not exe_path.is_file():
            raise StartupConfigurationError("Application executable could not be found.")

        if self._is_frozen_mode():
            if exe_path.suffix.lower() != ".exe":
                raise StartupConfigurationError("Packaged startup requires a valid .exe executable.")
            return f'"{exe_path.resolve()}"'

        launcher = Path(self._launcher_override or (get_project_root() / "launcher.py"))
        if not launcher.parent.exists() or not launcher.parent.is_dir():
            raise StartupConfigurationError("Application project directory does not exist.")
        if not launcher.exists() or not launcher.is_file():
            raise StartupConfigurationError("Application launcher script (launcher.py) could not be found.")
        return f'"{exe_path.resolve()}" "{launcher.resolve()}"'

    def _is_supported(self) -> bool:
        return self._winreg is not None and (sys.platform == "win32" or self._winreg is not winreg)

    def get_status(self) -> StartupStatus:
        """Check whether Wake-on-Startup is currently enabled for the current Windows user."""
        mode = self._get_mode_label()
        if not self._is_supported():
            return StartupStatus(
                enabled=False,
                supported=False,
                mode=mode,
                target_valid=False,
                reason="Wake-on-Startup is only supported on Windows.",
            )

        target_valid = True
        reason: Optional[str] = None
        try:
            self.build_launch_command()
        except StartupConfigurationError as err:
            target_valid = False
            reason = str(err)

        try:
            with self._winreg.OpenKey(
                self._winreg.HKEY_CURRENT_USER,
                RUN_KEY_PATH,
                0,
                self._winreg.KEY_QUERY_VALUE,
            ) as key:
                val, _ = self._winreg.QueryValueEx(key, STARTUP_VALUE_NAME)
                enabled = bool(isinstance(val, str) and val.strip())
                return StartupStatus(
                    enabled=enabled,
                    supported=True,
                    mode=mode,
                    target_valid=target_valid,
                    reason=reason,
                )
        except FileNotFoundError:
            return StartupStatus(
                enabled=False,
                supported=True,
                mode=mode,
                target_valid=target_valid,
                reason=reason,
            )
        except PermissionError as err:
            logger.warning("Permission denied reading Windows startup key: %s", err)
            return StartupStatus(
                enabled=False,
                supported=True,
                mode=mode,
                target_valid=target_valid,
                reason="Permission denied reading Windows per-user startup settings.",
            )
        except OSError as err:
            logger.warning("Failed to query Windows startup key: %s", err)
            return StartupStatus(
                enabled=False,
                supported=True,
                mode=mode,
                target_valid=target_valid,
                reason=f"Could not read Windows startup settings: {err}",
            )

    def enable(self) -> StartupStatus:
        """Enable Wake-on-Startup for the current Windows user without duplicates."""
        if not self._is_supported():
            raise StartupUnsupportedError("Wake-on-Startup is only supported on Windows.")

        command = self.build_launch_command()

        try:
            with self._winreg.CreateKeyEx(
                self._winreg.HKEY_CURRENT_USER,
                RUN_KEY_PATH,
                0,
                self._winreg.KEY_SET_VALUE | self._winreg.KEY_QUERY_VALUE,
            ) as key:
                self._remove_case_duplicates(key)
                self._winreg.SetValueEx(
                    key,
                    STARTUP_VALUE_NAME,
                    0,
                    self._winreg.REG_SZ,
                    command,
                )
            logger.info("Enabled per-user Windows Wake-on-Startup (%s mode).", self._get_mode_label())
            return StartupStatus(
                enabled=True,
                supported=True,
                mode=self._get_mode_label(),
                target_valid=True,
            )
        except PermissionError as err:
            raise StartupPermissionError(
                "Permission denied updating Windows per-user startup settings."
            ) from err
        except OSError as err:
            raise StartupManagerError(f"Failed to enable Windows startup: {err}") from err

    def disable(self) -> StartupStatus:
        """Disable Wake-on-Startup for the current Windows user idempotently."""
        if not self._is_supported():
            raise StartupUnsupportedError("Wake-on-Startup is only supported on Windows.")

        try:
            with self._winreg.OpenKey(
                self._winreg.HKEY_CURRENT_USER,
                RUN_KEY_PATH,
                0,
                self._winreg.KEY_SET_VALUE | self._winreg.KEY_QUERY_VALUE,
            ) as key:
                self._remove_case_duplicates(key, include_canonical=True)
            logger.info("Disabled per-user Windows Wake-on-Startup.")
        except FileNotFoundError:
            pass
        except PermissionError as err:
            raise StartupPermissionError(
                "Permission denied removing Windows per-user startup entry."
            ) from err
        except OSError as err:
            raise StartupManagerError(f"Failed to disable Windows startup: {err}") from err

        return self.get_status()

    def _remove_case_duplicates(self, key: Any, include_canonical: bool = False) -> None:
        """Remove any duplicate case-variant VoxPilot entries in the Run key."""
        to_delete = []
        idx = 0
        while True:
            try:
                name, _, _ = self._winreg.EnumValue(key, idx)
                idx += 1
                if name.lower() == STARTUP_VALUE_NAME.lower():
                    if include_canonical or name != STARTUP_VALUE_NAME:
                        to_delete.append(name)
            except OSError:
                break

        for name in to_delete:
            try:
                self._winreg.DeleteValue(key, name)
            except FileNotFoundError:
                pass


_shared_startup_manager: Optional[WindowsStartupManager] = None


def get_shared_startup_manager() -> WindowsStartupManager:
    """Return a singleton WindowsStartupManager instance."""
    global _shared_startup_manager
    if _shared_startup_manager is None:
        _shared_startup_manager = WindowsStartupManager()
    return _shared_startup_manager
