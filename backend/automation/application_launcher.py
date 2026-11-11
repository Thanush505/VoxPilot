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

from backend.automation.application_discovery import (
    ApplicationDiscoveryService,
    DiscoveredApplication,
    FORBIDDEN_SHELL_CHARS,
    PROHIBITED_EXECUTABLES,
    WINDOWS_BUILT_IN_UTILITIES,
    WINDOWS_SETTINGS_DESTINATIONS,
    WINDOWS_SPECIAL_FOLDERS,
    get_shared_discovery_service,
    is_suspicious_or_temporary_path,
    parse_windows_lnk,
    validate_executable_target,
    validate_shortcut_arguments,
)

logger = logging.getLogger(__name__)

# Regex detecting forbidden shell chaining characters in registry values
FORBIDDEN_REG_CHARS = re.compile(r"[;&|><`$\r\n]")

# Prohibited application queries (shells, interpreters, registry editors, malware test tokens)
PROHIBITED_QUERY_NAMES = {
    "cmd",
    "cmd.exe",
    "command prompt",
    "powershell",
    "powershell.exe",
    "pwsh",
    "pwsh.exe",
    "regedit",
    "regedit.exe",
    "registry editor",
    "mshta",
    "mshta.exe",
    "wscript",
    "wscript.exe",
    "cscript",
    "cscript.exe",
    "rundll32",
    "rundll32.exe",
    "regsvr32",
    "regsvr32.exe",
    "certutil",
    "certutil.exe",
    "bitsadmin",
    "bitsadmin.exe",
    "bash",
    "bash.exe",
    "wsl",
    "wsl.exe",
}


class ApplicationLauncherError(Exception):
    """Base exception for application launcher errors."""
    pass


class ApplicationNotAllowedError(ApplicationLauncherError):
    """Raised when a requested application is blocked by security policy or not on the allowlist."""
    pass


class UnsafeExecutablePathError(ApplicationNotAllowedError):
    """Raised when an executable target resides in an unsafe, temporary, or prohibited path."""
    pass


class ApplicationNotFoundError(ApplicationLauncherError):
    """Raised when a requested application cannot be located on the system."""
    pass


class AmbiguousApplicationError(ApplicationLauncherError):
    """Raised when multiple installed applications closely match a user query."""

    def __init__(
        self,
        message: str,
        candidates: Optional[List[str]] = None,
        entries: Optional[List[DiscoveredApplication]] = None,
    ):
        super().__init__(message)
        self.candidates: List[str] = candidates or []
        self.entries: List[DiscoveredApplication] = entries or []


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
    """Discovers, validates, and launches Windows applications safely."""

    def __init__(
        self,
        registry: Optional[ApplicationRegistry] = None,
        discovery_service: Optional[ApplicationDiscoveryService] = None,
    ):
        self._has_custom_registry = registry is not None
        self.registry = registry if registry is not None else ApplicationRegistry()
        self._discovery_service = discovery_service

    @property
    def discovery_service(self) -> ApplicationDiscoveryService:
        """Lazy accessor for the ApplicationDiscoveryService."""
        if self._discovery_service is None:
            self._discovery_service = get_shared_discovery_service()
        return self._discovery_service

    def resolve_application(self, query: str) -> ApplicationConfig:
        """Resolve a user-provided application query using the static registry."""
        return self.registry.resolve(query)

    @staticmethod
    def _validate_query_security(query: str) -> str:
        """Ensure the query does not contain path traversal, shell operators, or prohibited shells."""
        normalized = ApplicationRegistry.normalize_query(query)
        raw_clean = query.strip().strip("'\"")

        if "/" in raw_clean or "\\" in raw_clean or ":" in raw_clean:
            raise ApplicationNotAllowedError(
                f"Application query '{query}' contains path separators or URI schemes and is not on the approved application allowlist."
            )

        if FORBIDDEN_REG_CHARS.search(raw_clean):
            raise ApplicationNotAllowedError(
                f"Application query '{query}' contains forbidden shell metacharacters and is not on the approved application allowlist."
            )

        if (
            normalized in PROHIBITED_QUERY_NAMES
            or f"{normalized}.exe" in PROHIBITED_EXECUTABLES
            or "malware" in normalized
        ):
            raise ApplicationNotAllowedError(
                f"Application '{query}' is not on the approved application allowlist."
            )

        return normalized

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

    @staticmethod
    def _resolve_special_folder_arguments(args: List[str]) -> List[str]:
        """Resolve canonical folder placeholders for WINDOWS_SPECIAL_FOLDERS."""
        home = Path.home()
        mapping = {
            "__USER_DOWNLOADS__": str((home / "Downloads").resolve()),
            "__USER_DOCUMENTS__": str((home / "Documents").resolve()),
            "__USER_DESKTOP__": str((home / "Desktop").resolve()),
            "__USER_PICTURES__": str((home / "Pictures").resolve()),
        }
        resolved: List[str] = []
        for arg in args:
            if arg in mapping:
                resolved.append(mapping[arg])
            else:
                resolved.append(arg)
        return resolved

    def _build_validated_command_for_entry(self, entry: DiscoveredApplication) -> List[str]:
        """Revalidate a DiscoveredApplication on disk and construct a safe shell=False argument array."""
        method = entry.launch_method

        # 1. Standard desktop executable
        if method == "executable":
            raw_target = entry.executable_path or ""
            if not raw_target:
                raise ApplicationNotFoundError(
                    f"Application '{entry.display_name}' has no executable path configured."
                )
            expanded_target = Path(os.path.expandvars(raw_target))
            if expanded_target.name.lower() in PROHIBITED_EXECUTABLES:
                raise UnsafeExecutablePathError(
                    f"Executable '{raw_target}' for '{entry.display_name}' is blocked by security policy."
                )
            if not expanded_target.is_file():
                raise ApplicationNotFoundError(
                    f"Executable for '{entry.display_name}' was not found on disk at '{raw_target}'. "
                    "The application may have been uninstalled or moved; try refreshing the application index."
                )
            if is_suspicious_or_temporary_path(expanded_target):
                raise UnsafeExecutablePathError(
                    f"Executable path '{raw_target}' for '{entry.display_name}' is blocked by security policy."
                )
            ok_exe, validated_exe, reason = validate_executable_target(raw_target)
            if not ok_exe or validated_exe is None:
                raise UnsafeExecutablePathError(
                    f"Executable '{raw_target}' for '{entry.display_name}' failed security validation: {reason}"
                )
            raw_args = " ".join(entry.arguments) if entry.arguments else ""
            ok_args, safe_args = validate_shortcut_arguments(raw_args)
            if not ok_args:
                raise UnsafeExecutablePathError(
                    f"Arguments for '{entry.display_name}' contain forbidden shell operators."
                )
            return [str(validated_exe), *safe_args]

        # 2. Windows Start Menu / Desktop Shortcut (.lnk)
        if method == "shortcut":
            raw_lnk = entry.shortcut_path or ""
            if raw_lnk:
                lnk_path = Path(os.path.expandvars(raw_lnk))
                if not lnk_path.is_file() or lnk_path.suffix.lower() != ".lnk":
                    raise ApplicationNotFoundError(
                        f"Shortcut for '{entry.display_name}' is missing or invalid at '{raw_lnk}'."
                    )
                if is_suspicious_or_temporary_path(lnk_path):
                    raise UnsafeExecutablePathError(
                        f"Shortcut '{raw_lnk}' resides in an untrusted or temporary directory."
                    )

            target_exe = entry.executable_path
            parsed_args_str = " ".join(entry.arguments) if entry.arguments else ""
            if not target_exe and raw_lnk:
                parsed = parse_windows_lnk(Path(os.path.expandvars(raw_lnk)))
                if parsed:
                    target_exe, parsed_args_str, _ = parsed

            if not target_exe:
                raise ApplicationNotFoundError(
                    f"Shortcut target executable for '{entry.display_name}' could not be resolved."
                )

            expanded_target = Path(os.path.expandvars(target_exe))
            if expanded_target.name.lower() in PROHIBITED_EXECUTABLES:
                raise UnsafeExecutablePathError(
                    f"Shortcut target '{target_exe}' for '{entry.display_name}' is blocked by security policy."
                )
            if not expanded_target.is_file():
                raise ApplicationNotFoundError(
                    f"Shortcut target executable for '{entry.display_name}' does not exist at '{target_exe}'."
                )
            if is_suspicious_or_temporary_path(expanded_target):
                raise UnsafeExecutablePathError(
                    f"Shortcut target '{target_exe}' for '{entry.display_name}' is blocked by security policy."
                )
            ok_exe, validated_exe, reason = validate_executable_target(target_exe)
            if not ok_exe or validated_exe is None:
                raise UnsafeExecutablePathError(
                    f"Shortcut target '{target_exe}' for '{entry.display_name}' failed security validation: {reason}"
                )
            ok_args, safe_args = validate_shortcut_arguments(parsed_args_str)
            if not ok_args:
                raise UnsafeExecutablePathError(
                    f"Shortcut arguments for '{entry.display_name}' contain forbidden shell operators."
                )
            return [str(validated_exe), *safe_args]

        # 3. Microsoft Store / MSIX / Appx packaged app
        if method == "store_app":
            shell_target = (entry.executable_path or "").strip()
            if not shell_target.startswith("shell:AppsFolder\\") or FORBIDDEN_SHELL_CHARS.search(shell_target):
                raise UnsafeExecutablePathError(
                    f"Invalid or unsafe Microsoft Store AppID target for '{entry.display_name}'."
                )
            aumid = shell_target[len("shell:AppsFolder\\"):]
            if not aumid or "!" not in aumid or ".." in aumid:
                raise UnsafeExecutablePathError(
                    f"Malformed Microsoft Store AUMID '{aumid}' for '{entry.display_name}'."
                )
            explorer_exe = Path(r"C:\Windows\explorer.exe")
            return [str(explorer_exe), shell_target]

        # 4. Windows Settings URI (strictly validated against fixed allowlist)
        if method == "settings_uri":
            spec = WINDOWS_SETTINGS_DESTINATIONS.get(entry.app_id)
            if spec is None or entry.executable_path != spec.target or not spec.target.startswith("ms-settings:"):
                raise ApplicationNotAllowedError(
                    f"Windows Settings URI for '{entry.display_name}' is not on the approved Settings allowlist."
                )
            if FORBIDDEN_SHELL_CHARS.search(spec.target):
                raise ApplicationNotAllowedError("Invalid characters in Windows Settings URI.")
            explorer_exe = Path(r"C:\Windows\explorer.exe")
            return [str(explorer_exe), spec.target]

        # 5. Windows Built-In Utility (strictly validated against fixed allowlist)
        if method == "system_utility":
            spec = WINDOWS_BUILT_IN_UTILITIES.get(entry.app_id)
            if spec is None:
                raise ApplicationNotAllowedError(
                    f"System utility '{entry.display_name}' is not on the approved utilities allowlist."
                )
            target_path = Path(os.path.expandvars(spec.target))
            if target_path.name.lower() in PROHIBITED_EXECUTABLES:
                raise ApplicationNotAllowedError(
                    f"System utility '{entry.display_name}' targets a prohibited executable."
                )
            if not target_path.is_file():
                raise ApplicationNotFoundError(
                    f"System utility executable for '{entry.display_name}' was not found at '{spec.target}'."
                )
            return [str(target_path.resolve()), *spec.arguments]

        # 6. Windows Special Folder (File Explorer, Downloads, Recycle Bin, etc.)
        if method == "shell_folder":
            spec = WINDOWS_SPECIAL_FOLDERS.get(entry.app_id)
            if spec is None:
                raise ApplicationNotAllowedError(
                    f"System folder '{entry.display_name}' is not on the approved folders allowlist."
                )
            explorer_exe = Path(r"C:\Windows\explorer.exe")
            resolved_args = self._resolve_special_folder_arguments(spec.arguments)
            return [str(explorer_exe), *resolved_args]

        raise ApplicationNotAllowedError(
            f"Unsupported launch method '{method}' for '{entry.display_name}'."
        )

    def launch_discovered_application(self, entry: DiscoveredApplication) -> Dict[str, Any]:
        """Validate on disk and launch a DiscoveredApplication entry with shell=False."""
        cmd_args = self._build_validated_command_for_entry(entry)

        try:
            logger.info(
                f"Launching discovered application: {entry.display_name} "
                f"(id={entry.app_id}, method={entry.launch_method}, source={entry.source}) -> {cmd_args}"
            )
            process = subprocess.Popen(
                cmd_args,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            pid = process.pid
            logger.info(f"Successfully spawned '{entry.display_name}' with PID: {pid}")

            return {
                "success": True,
                "app_id": entry.app_id,
                "display_name": entry.display_name,
                "pid": pid,
                "status": "launched",
                "launch_method": entry.launch_method,
                "source": entry.source,
                "requires_confirmation": entry.requires_confirmation,
                "message": f"Successfully launched {entry.display_name} (PID: {pid}).",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except (OSError, PermissionError) as err:
            logger.error(f"Failed to spawn process for '{entry.display_name}': {err}")
            raise ApplicationLauncherError(
                f"Failed to launch '{entry.display_name}': {err}"
            ) from err

    def _launch_via_static_config(self, config: ApplicationConfig) -> Dict[str, Any]:
        """Launch an application using a static ApplicationConfig specification."""
        executable_path = self.find_executable(config)
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
                "launch_method": "executable",
                "source": "static_registry",
                "requires_confirmation": config.requires_confirmation,
                "message": f"Successfully launched {config.display_name} (PID: {pid}).",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except (OSError, PermissionError) as err:
            logger.error(f"Failed to spawn process for '{config.display_name}': {err}")
            raise ApplicationLauncherError(
                f"Failed to launch '{config.display_name}' at '{executable_path}': {err}"
            ) from err

    def launch(self, query: str) -> Dict[str, Any]:
        """Safely resolve, validate, and launch a requested Windows application or destination.

        Enforces:
        - Prohibited binary and shell injection rejection.
        - Verified executable, shortcut, Store AppID, or allowlisted Settings/Utility target.
        - shell=False argument list execution.

        Returns:
            Dict[str, Any]: Structured launch result dictionary.

        Raises:
            ApplicationNotAllowedError: If the application or path is blocked by security policy.
            AmbiguousApplicationError: If multiple installed applications closely match the query.
            ApplicationNotFoundError: If the application is not installed or its executable is missing.
            ApplicationLauncherError: If process creation fails.
        """
        self._validate_query_security(query)

        # If a custom ApplicationRegistry was explicitly injected (e.g. unit tests), use it directly
        if self._has_custom_registry:
            config = self.resolve_application(query)
            return self._launch_via_static_config(config)

        # Universal dynamic discovery resolution
        resolution = self.discovery_service.resolve_query(query)
        status = resolution.get("status")

        if status == "ambiguous":
            entries: List[DiscoveredApplication] = resolution.get("candidates", [])
            names = [c.display_name for c in entries]
            raise AmbiguousApplicationError(
                resolution.get("message") or f"Multiple applications match '{query}': {', '.join(names)}.",
                candidates=names,
                entries=entries,
            )

        if status == "resolved":
            entry: DiscoveredApplication = resolution["match"]
            return self.launch_discovered_application(entry)

        # Fallback: check core static registry if discovery index did not find it
        try:
            config = self.registry.resolve(query)
            return self._launch_via_static_config(config)
        except ApplicationNotAllowedError:
            pass

        raise ApplicationNotFoundError(
            resolution.get("message")
            or f"Application '{query}' could not be found on this Windows system. Verify that it is installed or refresh the application index."
        )

    def is_running(self, query: str) -> bool:
        """Check whether an application currently has an active running process."""
        if psutil is None:
            return False

        expected_names = set()
        try:
            config = self.resolve_application(query)
            expected_names.add(f"{config.app_id}.exe".lower())
            if config.registry_app_path:
                expected_names.add(config.registry_app_path.lower())
            for cand in config.executable_candidates:
                expected_names.add(cand.name.lower())
        except ApplicationNotAllowedError:
            if not self._has_custom_registry:
                resolution = self.discovery_service.resolve_query(query)
                if resolution.get("status") == "resolved":
                    entry: DiscoveredApplication = resolution["match"]
                    if entry.executable_path and not entry.executable_path.startswith(("ms-settings:", "shell:")):
                        expected_names.add(Path(entry.executable_path).name.lower())
                    else:
                        expected_names.add(f"{entry.app_id}.exe".lower())
                else:
                    raise
            else:
                raise

        expected_names.discard("")
        if not expected_names:
            return False

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
            logger.warning(f"Error checking running processes for '{query}': {err}")

        return False
