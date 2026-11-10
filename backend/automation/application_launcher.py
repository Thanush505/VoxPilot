"""
VoxPilot - Windows Application Launcher Module
Provides secure, allowlisted application discovery and launching for Windows 11.
Enforces strict allowlist resolution, executable validation, and shell=False execution.
"""

from __future__ import annotations

import os
import re
import sys
import logging
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, List, Any

try:
    import winreg
except ImportError:
    winreg = None  # type: ignore

try:
    import psutil
except ImportError:
    psutil = None  # type: ignore

logger = logging.getLogger(__name__)

# Regex detecting forbidden shell chaining characters in registry values
FORBIDDEN_REG_CHARS = re.compile(r"[;&|><`$\r\n]")


class ApplicationLauncherError(Exception):
    """Base exception for application launcher errors."""
    pass


class ApplicationNotAllowedError(ApplicationLauncherError):
    """Raised when a requested application is not present on the approved allowlist."""
    pass


class ApplicationNotFoundError(ApplicationLauncherError):
    """Raised when an approved application's executable cannot be located on the system."""
    pass


@dataclass(frozen=True)
class ApplicationConfig:
    """Configuration specification for an approved application."""
    app_id: str
    display_name: str
    aliases: List[str] = field(default_factory=list)
    executable_candidates: List[Path] = field(default_factory=list)
    registry_app_path: Optional[str] = None
    approved_directories: List[Path] = field(default_factory=list)
    allow_args: bool = False
    requires_confirmation: bool = False


# Approved application specifications for VoxPilot Phase 3
DEFAULT_APPROVED_APPLICATIONS: Dict[str, ApplicationConfig] = {
    "notepad": ApplicationConfig(
        app_id="notepad",
        display_name="Notepad",
        aliases=["notepad", "text editor", "notes", "editor"],
        executable_candidates=[
            Path(r"C:\Windows\System32\notepad.exe"),
            Path(os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps\notepad.exe")),
        ],
        registry_app_path="notepad.exe",
        approved_directories=[
            Path(r"C:\Windows\System32"),
            Path(os.path.expandvars(r"%SystemRoot%\System32")),
            Path(os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps")),
            Path(os.path.expandvars(r"%ProgramFiles%\WindowsApps")),
        ],
        allow_args=False,
        requires_confirmation=False,
    ),
    "calculator": ApplicationConfig(
        app_id="calculator",
        display_name="Calculator",
        aliases=["calculator", "calc"],
        executable_candidates=[
            Path(r"C:\Windows\System32\calc.exe"),
            Path(os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps\calc.exe")),
        ],
        registry_app_path="calc.exe",
        approved_directories=[
            Path(r"C:\Windows\System32"),
            Path(os.path.expandvars(r"%SystemRoot%\System32")),
            Path(os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps")),
            Path(os.path.expandvars(r"%ProgramFiles%\WindowsApps")),
        ],
        allow_args=False,
        requires_confirmation=False,
    ),
    "chrome": ApplicationConfig(
        app_id="chrome",
        display_name="Google Chrome",
        aliases=["chrome", "google chrome", "browser", "web browser"],
        executable_candidates=[
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
            Path(os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")),
        ],
        registry_app_path="chrome.exe",
        approved_directories=[
            Path(r"C:\Program Files\Google\Chrome\Application"),
            Path(r"C:\Program Files (x86)\Google\Chrome\Application"),
            Path(os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application")),
            Path(os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application")),
            Path(os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application")),
        ],
        allow_args=False,
        requires_confirmation=False,
    ),
    "vscode": ApplicationConfig(
        app_id="vscode",
        display_name="Visual Studio Code",
        aliases=["vscode", "vs code", "code", "visual studio code"],
        executable_candidates=[
            Path(os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe")),
            Path(r"C:\Program Files\Microsoft VS Code\Code.exe"),
            Path(r"C:\Program Files (x86)\Microsoft VS Code\Code.exe"),
        ],
        registry_app_path="Code.exe",
        approved_directories=[
            Path(os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code")),
            Path(r"C:\Program Files\Microsoft VS Code"),
            Path(r"C:\Program Files (x86)\Microsoft VS Code"),
            Path(os.path.expandvars(r"%ProgramFiles%\Microsoft VS Code")),
            Path(os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft VS Code")),
        ],
        allow_args=False,
        requires_confirmation=False,
    ),
}


def is_path_in_approved_directories(resolved_path: Path, approved_dirs: List[Path]) -> bool:
    """Check if resolved_path is located inside any of the approved_dirs, case-insensitively.

    Args:
        resolved_path: Fully resolved canonical Path of the target executable.
        approved_dirs: List of approved directory Paths.

    Returns:
        bool: True if resolved_path is contained within at least one approved directory.
    """
    resolved_norm = resolved_path.resolve()
    for app_dir in approved_dirs:
        try:
            expanded_dir = Path(os.path.expandvars(str(app_dir))).resolve()
            if resolved_norm.is_relative_to(expanded_dir):
                return True
        except (ValueError, AttributeError):
            continue
    return False


def validate_registry_value(
    raw_value: Any,
    expected_filename: str,
    approved_dirs: List[Path],
) -> Optional[Path]:
    """Validate a raw registry value string to ensure it points to an approved executable.

    Enforces:
    - Must be a non-empty string.
    - Zero shell metacharacters (;, &, |, >, <, `, $, newlines).
    - Zero command-line arguments (quoted string must end with quote, unquoted cannot have flags).
    - No unresolved environment variables (%VAR%).
    - Must exist on disk and be an authentic file.
    - Filename must match expected_filename case-insensitively.
    - Resolved canonical path must reside within an approved installation directory.

    Args:
        raw_value: Value retrieved from Windows Registry App Paths.
        expected_filename: Expected binary filename (e.g. "chrome.exe", "Code.exe").
        approved_dirs: List of allowed directory roots for this application.

    Returns:
        Optional[Path]: Resolved, validated Path, or None if validation fails.
    """
    if not raw_value or not isinstance(raw_value, str):
        return None

    raw_str = raw_value.strip()
    if not raw_str:
        return None

    # Reject shell chaining operators
    if FORBIDDEN_REG_CHARS.search(raw_str):
        logger.warning(f"Rejected registry value containing shell operators: {raw_str}")
        return None

    # Parse and reject command-line arguments
    if raw_str.startswith('"'):
        # Quoted path: must close at the very end of the string (no trailing arguments)
        second_quote = raw_str.find('"', 1)
        if second_quote == -1 or second_quote != len(raw_str) - 1:
            logger.warning(f"Rejected registry value with unclosed quote or trailing arguments: {raw_str}")
            return None
        unquoted = raw_str[1:-1].strip()
        if '"' in unquoted:
            return None
    else:
        # Unquoted path: reject if internal quotes or argument patterns exist (e.g. space followed by flag)
        if '"' in raw_str:
            return None
        if re.search(r"\s+[-/]", raw_str):
            logger.warning(f"Rejected registry value containing command-line arguments: {raw_str}")
            return None
        unquoted = raw_str

    # Check for unresolved environment variables
    if "%" in unquoted:
        expanded = os.path.expandvars(unquoted)
        if re.search(r"%[^%]+%", expanded):
            logger.warning(f"Rejected registry value with unresolved environment variables: {unquoted}")
            return None
    else:
        expanded = unquoted

    candidate = Path(expanded)
    try:
        if not candidate.is_file():
            return None

        # Check filename matches expected binary name
        if candidate.name.lower() != expected_filename.lower():
            logger.warning(
                f"Rejected registry value filename mismatch: expected '{expected_filename}', got '{candidate.name}'"
            )
            return None

        # Resolve symlinks and reparse points
        resolved_path = candidate.resolve()
        if not resolved_path.is_file():
            return None

        if resolved_path.name.lower() != expected_filename.lower():
            logger.warning(
                f"Rejected resolved symlink filename mismatch: expected '{expected_filename}', got '{resolved_path.name}'"
            )
            return None

        # Enforce directory containment
        if approved_dirs and not is_path_in_approved_directories(resolved_path, approved_dirs):
            logger.warning(
                f"Rejected registry executable outside approved directories: {resolved_path}"
            )
            return None

        return resolved_path

    except (OSError, PermissionError, ValueError) as err:
        logger.warning(f"Error validating registry candidate '{expanded}': {err}")
        return None


class ApplicationRegistry:
    """Maintains the approved application allowlist and resolves natural language queries."""

    def __init__(self, applications: Optional[Dict[str, ApplicationConfig]] = None):
        self._applications: Dict[str, ApplicationConfig] = {}
        self._alias_map: Dict[str, str] = {}

        apps_to_load = applications if applications is not None else DEFAULT_APPROVED_APPLICATIONS
        for config in apps_to_load.values():
            self.register(config)

    def register(self, config: ApplicationConfig) -> None:
        """Register an application configuration into the registry and update alias mapping."""
        self._applications[config.app_id] = config

        # Register canonical ID and display name
        self._alias_map[config.app_id.lower()] = config.app_id
        self._alias_map[config.display_name.lower()] = config.app_id

        # Register each alias
        for alias in config.aliases:
            norm_alias = alias.strip().lower()
            if norm_alias:
                self._alias_map[norm_alias] = config.app_id

    @staticmethod
    def normalize_query(query: str) -> str:
        """Normalize a query string by trimming, lowercasing, and stripping file extensions."""
        if not isinstance(query, str):
            raise ApplicationNotAllowedError("Application query must be a string.")

        clean_query = query.strip().lower()
        if not clean_query:
            raise ApplicationNotAllowedError("Application query cannot be empty.")

        # Remove surrounding quotes if present
        clean_query = clean_query.strip("'\"")

        # Strip .exe extension if present at the end
        if clean_query.endswith(".exe"):
            clean_query = clean_query[:-4].strip()

        return clean_query

    def resolve(self, query: str) -> ApplicationConfig:
        """Resolve a natural-language application query to its canonical ApplicationConfig.

        Raises:
            ApplicationNotAllowedError: If the query does not map to an approved application.
        """
        normalized = self.normalize_query(query)

        # Direct alias lookup
        app_id = self._alias_map.get(normalized)

        if not app_id:
            # Check for close substring match in approved display names or canonical IDs
            for alias, target_id in self._alias_map.items():
                if normalized == alias:
                    app_id = target_id
                    break

        if not app_id or app_id not in self._applications:
            raise ApplicationNotAllowedError(
                f"Application '{query}' is not on the approved application allowlist."
            )

        return self._applications[app_id]

    def list_approved_applications(self) -> List[Dict[str, Any]]:
        """Return a list of metadata dictionaries for all approved applications."""
        return [
            {
                "app_id": config.app_id,
                "display_name": config.display_name,
                "aliases": list(config.aliases),
                "requires_confirmation": config.requires_confirmation,
            }
            for config in self._applications.values()
        ]


class ApplicationLauncher:
    """Discovers, validates, and launches approved Windows applications safely."""

    def __init__(self, registry: Optional[ApplicationRegistry] = None):
        self.registry = registry if registry is not None else ApplicationRegistry()

    def resolve_application(self, query: str) -> ApplicationConfig:
        """Resolve a user-provided application query using the registry."""
        return self.registry.resolve(query)

    def _query_registry_app_path(
        self,
        reg_key_name: str,
        approved_dirs: Optional[List[Path]] = None,
    ) -> Optional[Path]:
        """Query Windows Registry App Paths (read-only) for an executable path.

        SEC-002: Enforces HKEY_LOCAL_MACHINE before HKEY_CURRENT_USER.
        SEC-003: Strictly validates value, filename, arguments, and approved directory containment.
        """
        if winreg is None:
            return None

        # Check HKLM first (requires administrative privileges to modify), then HKCU
        root_keys = [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]
        sub_key = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{reg_key_name}"
        effective_dirs = approved_dirs or []

        for root in root_keys:
            try:
                with winreg.OpenKey(root, sub_key, 0, winreg.KEY_READ) as key:
                    value, _ = winreg.QueryValueEx(key, "")
                    validated_path = validate_registry_value(
                        raw_value=value,
                        expected_filename=reg_key_name,
                        approved_dirs=effective_dirs,
                    )
                    if validated_path is not None:
                        logger.debug(f"Resolved valid executable via registry App Paths: {validated_path}")
                        return validated_path
            except (OSError, FileNotFoundError, PermissionError):
                continue

        return None

    def find_executable(self, config: ApplicationConfig) -> Path:
        """Locate the validated executable path on disk for an approved application.

        Resolution Order:
        1. Explicit executable candidate paths (validated against expected binary name and approved dirs).
        2. Windows Registry App Paths (read-only, HKLM first, strictly validated).

        SEC-001: Ambient PATH lookup (shutil.which) is completely removed to prevent binary planting.

        Raises:
            ApplicationNotFoundError: If the executable cannot be located on disk.
        """
        expected_binary = config.registry_app_path or f"{config.app_id}.exe"

        # 1. Check explicit executable candidate paths
        for candidate in config.executable_candidates:
            expanded_path = Path(os.path.expandvars(str(candidate)))
            if expanded_path.is_file():
                resolved_cand = expanded_path.resolve()
                if resolved_cand.is_file() and resolved_cand.name.lower() == expected_binary.lower():
                    if not config.approved_directories or is_path_in_approved_directories(resolved_cand, config.approved_directories):
                        logger.debug(f"Resolved '{config.display_name}' via candidate path: {resolved_cand}")
                        return resolved_cand

        # 2. Check Windows Registry App Paths (HKLM first, then HKCU, with strict validation)
        if config.registry_app_path:
            reg_path = self._query_registry_app_path(
                config.registry_app_path,
                approved_dirs=config.approved_directories,
            )
            if reg_path is not None and reg_path.is_file():
                logger.debug(f"Resolved '{config.display_name}' via registry: {reg_path}")
                return reg_path

        # SEC-001: System PATH fallback has been completely removed.
        raise ApplicationNotFoundError(
            f"Executable for '{config.display_name}' could not be located on this system. "
            f"Checked approved candidate paths and Windows App Paths for '{expected_binary}'."
        )

    def launch(self, query: str) -> Dict[str, Any]:
        """Safely launch an approved application.

        Enforces:
        - Strict allowlist resolution.
        - Verified executable path on disk.
        - shell=False argument list execution.
        - Zero arbitrary arguments.

        Returns:
            Dict[str, Any]: Structured launch result dictionary.

        Raises:
            ApplicationNotAllowedError: If the application is not approved.
            ApplicationNotFoundError: If the executable is missing.
            ApplicationLauncherError: If process creation fails.
        """
        config = self.resolve_application(query)
        executable_path = self.find_executable(config)

        # Enforce strict argument list with shell=False
        cmd_args = [str(executable_path)]

        try:
            logger.info(f"Launching approved application: {config.display_name} -> {executable_path}")
            process = subprocess.Popen(
                cmd_args,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            pid = process.pid
            logger.info(f"Successfully spawned {config.display_name} with PID: {pid}")

            return {
                "success": True,
                "app_id": config.app_id,
                "display_name": config.display_name,
                "pid": pid,
                "status": "launched",
                "message": f"Successfully launched {config.display_name} (PID: {pid}).",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        except (OSError, PermissionError) as err:
            logger.error(f"Failed to spawn process for '{config.display_name}': {err}")
            raise ApplicationLauncherError(
                f"Failed to launch '{config.display_name}' at '{executable_path}': {err}"
            ) from err

    def is_running(self, query: str) -> bool:
        """Check whether an approved application currently has an active running process.

        Args:
            query: Natural language application name or alias.

        Returns:
            bool: True if at least one matching process is found, False otherwise.
        """
        config = self.resolve_application(query)
        if psutil is None:
            return False

        expected_names = {
            f"{config.app_id}.exe".lower(),
            (config.registry_app_path or "").lower(),
        }
        for cand in config.executable_candidates:
            expected_names.add(cand.name.lower())
        expected_names.discard("")

        try:
            for proc in psutil.process_iter(["name", "exe"]):
                try:
                    p_name = (proc.info.get("name") or "").lower()
                    if p_name in expected_names:
                        return True
                    p_exe = (proc.info.get("exe") or "").lower()
                    if any(p_exe.endswith(name) for name in expected_names):
                        return True
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
        except Exception as err:
            logger.warning(f"Error checking running processes for '{config.display_name}': {err}")

        return False
