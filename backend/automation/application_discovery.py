"""
VoxPilot - Universal Windows Application Discovery & Searchable Application Index
Discovers installed applications across 6 authoritative Windows sources:
1. Windows Start Menu shortcuts (Current User & All Users)
2. Windows Registry (64-bit & 32-bit Uninstall keys + App Paths, HKLM & HKCU)
3. Microsoft Store / MSIX / Appx packaged applications (via Get-StartApps & AppModel registry)
4. Known installation directories (Program Files, Program Files (x86), %LOCALAPPDATA%\\Programs, %LOCALAPPDATA%)
5. Desktop shortcuts (Current User Desktop, OneDrive Desktop, & Public Desktop)
6. Windows built-in applications, utilities, Settings URI destinations, and special shell folders

Provides:
- Deduplicated, normalized searchable application index
- Persistent caching in %LOCALAPPDATA%\\VoxPilot\\config\\application_index.json
- Alias, partial, and fuzzy matching with ambiguity detection
- Strict pre-launch target validation (blocking unsafe/temp/download paths and shell injection)
"""

from __future__ import annotations

import difflib
import json
import logging
import os
import re
import struct
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.paths import get_config_dir

try:
    import winreg
except ImportError:
    winreg = None  # type: ignore

logger = logging.getLogger(__name__)

# Characters strictly prohibited in any executable path, shortcut target, or argument
FORBIDDEN_LAUNCH_CHARS = re.compile(r"[;&|><`$\r\n\x00]")
FORBIDDEN_SHELL_CHARS = FORBIDDEN_LAUNCH_CHARS

# Executables that are strictly blocked from being indexed or launched by the AI agent
PROHIBITED_EXECUTABLES: Set[str] = {
    "cmd.exe",
    "powershell.exe",
    "pwsh.exe",
    "powershell_ise.exe",
    "wscript.exe",
    "cscript.exe",
    "mshta.exe",
    "rundll32.exe",
    "regsvr32.exe",
    "regedit.exe",
    "regedt32.exe",
    "reg.exe",
    "certutil.exe",
    "bitsadmin.exe",
    "bash.exe",
    "wsl.exe",
    "wslconfig.exe",
    "ftp.exe",
    "netsh.exe",
    "sc.exe",
    "schtasks.exe",
    "takeown.exe",
    "icacls.exe",
    "format.com",
    "diskpart.exe",
    "bcdedit.exe",
    "vssadmin.exe",
    "wbadmin.exe",
    "cipher.exe",
    "shutdown.exe",
    "logoff.exe",
}

# Keywords in shortcut names that indicate non-application entries (uninstallers, docs, help)
_EXCLUDED_SHORTCUT_KEYWORDS = (
    "uninstall",
    "unins000",
    "remove ",
    "readme",
    "release notes",
    "documentation",
    "manual",
    "help",
    "website",
    "license",
    "changelog",
    "install playwright browser",
)

# Common natural-language aliases for popular desktop and Store applications
KNOWN_APPLICATION_ALIASES: Dict[str, List[str]] = {
    "notepad": ["notepad", "text editor", "notes", "editor"],
    "calculator": ["calculator", "calc"],
    "google chrome": ["chrome", "google chrome", "web browser", "browser"],
    "visual studio code": ["vscode", "vs code", "code", "visual studio code"],
    "microsoft edge": ["edge", "ms edge", "microsoft edge"],
    "mozilla firefox": ["firefox", "mozilla firefox"],
    "brave": ["brave", "brave browser"],
    "whatsapp": ["whatsapp", "whats app", "whatsapp desktop"],
    "spotify": ["spotify", "spotify music"],
    "discord": ["discord", "discord app"],
    "slack": ["slack"],
    "microsoft teams": ["teams", "ms teams", "microsoft teams"],
    "zoom": ["zoom", "zoom workplace", "zoom meetings"],
    "telegram desktop": ["telegram", "telegram desktop"],
    "microsoft word": ["word", "ms word", "microsoft word", "winword"],
    "microsoft excel": ["excel", "ms excel", "microsoft excel"],
    "microsoft powerpoint": ["powerpoint", "ms powerpoint", "microsoft powerpoint", "ppt"],
    "microsoft outlook": ["outlook", "ms outlook", "microsoft outlook"],
    "onenote": ["onenote", "ms onenote", "microsoft onenote"],
    "android studio": ["android studio", "androidstudio"],
    "docker desktop": ["docker", "docker desktop"],
    "mongodb compass": ["mongodb compass", "mongo compass", "compass", "mongodb"],
    "postman": ["postman"],
    "git bash": ["git bash"],
    "github desktop": ["github desktop", "github"],
    "vlc media player": ["vlc", "vlc media player", "vlc player"],
    "obs studio": ["obs", "obs studio"],
    "steam": ["steam"],
    "epic games launcher": ["epic games", "epic games launcher"],
    "paint": ["paint", "mspaint", "microsoft paint"],
    "snipping tool": ["snipping tool", "snip & sketch", "screen snip"],
    "windows terminal": ["terminal", "windows terminal", "wt"],
    "7-zip file manager": ["7-zip", "7zip", "7-zip file manager"],
    "winrar": ["winrar"],
    "adobe acrobat": ["acrobat", "adobe acrobat", "adobe reader", "pdf reader"],
    "figma": ["figma"],
    "notion": ["notion"],
    "obsidian": ["obsidian"],
    "cursor": ["cursor", "cursor ide"],
    "pycharm": ["pycharm", "jetbrains pycharm"],
    "intellij idea": ["intellij", "intellij idea"],
}


# ==============================================================================
# Fixed Immutable Allowlist of Windows Settings URIs, Utilities, & Shell Folders
# ==============================================================================

@dataclass(frozen=True)
class WindowsDestinationSpec:
    """Immutable specification for a built-in Windows Settings page, utility, or folder."""
    app_id: str
    display_name: str
    category: str  # "windows_settings", "system_utility", "special_folder"
    launch_method: str  # "settings_uri", "system_utility", "shell_folder"
    target: str
    arguments: Tuple[str, ...] = ()
    aliases: Tuple[str, ...] = ()
    requires_confirmation: bool = False
    publisher: str = "Microsoft Corporation"


WINDOWS_SETTINGS_DESTINATIONS: Dict[str, WindowsDestinationSpec] = {
    "settings-home": WindowsDestinationSpec(
        app_id="settings-home",
        display_name="Windows Settings",
        category="windows_settings",
        launch_method="settings_uri",
        target="ms-settings:",
        aliases=(
            "windows settings",
            "settings",
            "system settings",
            "pc settings",
            "settings app",
        ),
    ),
    "settings-network": WindowsDestinationSpec(
        app_id="settings-network",
        display_name="Network & Internet Settings",
        category="windows_settings",
        launch_method="settings_uri",
        target="ms-settings:network",
        aliases=(
            "network settings",
            "network and internet",
            "network & internet",
            "network and internet settings",
            "internet settings",
        ),
    ),
    "settings-wifi": WindowsDestinationSpec(
        app_id="settings-wifi",
        display_name="Wi-Fi Settings",
        category="windows_settings",
        launch_method="settings_uri",
        target="ms-settings:network-wifi",
        aliases=(
            "wifi settings",
            "wi-fi settings",
            "wireless settings",
            "wifi",
            "wi-fi",
        ),
    ),
    "settings-bluetooth": WindowsDestinationSpec(
        app_id="settings-bluetooth",
        display_name="Bluetooth & Devices Settings",
        category="windows_settings",
        launch_method="settings_uri",
        target="ms-settings:bluetooth",
        aliases=(
            "bluetooth settings",
            "bluetooth",
            "bluetooth and devices",
            "bluetooth & devices",
            "bluetooth devices",
        ),
    ),
    "settings-display": WindowsDestinationSpec(
        app_id="settings-display",
        display_name="Display Settings",
        category="windows_settings",
        launch_method="settings_uri",
        target="ms-settings:display",
        aliases=(
            "display settings",
            "screen settings",
            "monitor settings",
            "resolution settings",
            "display",
        ),
    ),
    "settings-sound": WindowsDestinationSpec(
        app_id="settings-sound",
        display_name="Sound Settings",
        category="windows_settings",
        launch_method="settings_uri",
        target="ms-settings:sound",
        aliases=(
            "sound settings",
            "audio settings",
            "volume settings",
            "speaker settings",
            "microphone settings",
        ),
    ),
    "settings-personalization": WindowsDestinationSpec(
        app_id="settings-personalization",
        display_name="Personalization Settings",
        category="windows_settings",
        launch_method="settings_uri",
        target="ms-settings:personalization",
        aliases=(
            "personalization settings",
            "personalization",
            "theme settings",
            "wallpaper settings",
            "background settings",
        ),
    ),
    "settings-windowsupdate": WindowsDestinationSpec(
        app_id="settings-windowsupdate",
        display_name="Windows Update Settings",
        category="windows_settings",
        launch_method="settings_uri",
        target="ms-settings:windowsupdate",
        aliases=(
            "windows update",
            "windows update settings",
            "update settings",
            "system updates",
            "check for windows updates",
        ),
    ),
    "settings-apps": WindowsDestinationSpec(
        app_id="settings-apps",
        display_name="Installed Apps Settings",
        category="windows_settings",
        launch_method="settings_uri",
        target="ms-settings:appsfeatures",
        aliases=(
            "installed apps",
            "installed apps settings",
            "apps and features",
            "apps & features",
            "apps settings",
            "add or remove programs",
        ),
    ),
    "settings-privacy": WindowsDestinationSpec(
        app_id="settings-privacy",
        display_name="Privacy & Security Settings",
        category="windows_settings",
        launch_method="settings_uri",
        target="ms-settings:privacy",
        aliases=(
            "privacy settings",
            "privacy and security",
            "privacy & security",
            "privacy and security settings",
            "security settings",
        ),
    ),
    "settings-storage": WindowsDestinationSpec(
        app_id="settings-storage",
        display_name="Storage Settings",
        category="windows_settings",
        launch_method="settings_uri",
        target="ms-settings:storagesense",
        aliases=(
            "storage settings",
            "disk space settings",
        ),
    ),
    "settings-datetime": WindowsDestinationSpec(
        app_id="settings-datetime",
        display_name="Date & Time Settings",
        category="windows_settings",
        launch_method="settings_uri",
        target="ms-settings:dateandtime",
        aliases=(
            "date and time settings",
            "date & time settings",
            "time settings",
        ),
    ),
    "settings-defaultapps": WindowsDestinationSpec(
        app_id="settings-defaultapps",
        display_name="Default Apps Settings",
        category="windows_settings",
        launch_method="settings_uri",
        target="ms-settings:defaultapps",
        aliases=(
            "default apps",
            "default apps settings",
        ),
    ),
}


WINDOWS_BUILT_IN_UTILITIES: Dict[str, WindowsDestinationSpec] = {
    "utility-control-panel": WindowsDestinationSpec(
        app_id="utility-control-panel",
        display_name="Control Panel",
        category="system_utility",
        launch_method="system_utility",
        target=r"C:\Windows\System32\control.exe",
        aliases=("control panel", "windows control panel"),
        requires_confirmation=False,
    ),
    "utility-task-manager": WindowsDestinationSpec(
        app_id="utility-task-manager",
        display_name="Task Manager",
        category="system_utility",
        launch_method="system_utility",
        target=r"C:\Windows\System32\Taskmgr.exe",
        aliases=("task manager", "windows task manager"),
        requires_confirmation=False,
    ),
    "utility-device-manager": WindowsDestinationSpec(
        app_id="utility-device-manager",
        display_name="Device Manager",
        category="system_utility",
        launch_method="system_utility",
        target=r"C:\Windows\System32\control.exe",
        arguments=("hdwwiz.cpl",),
        aliases=("device manager", "windows device manager", "devmgmt"),
        # Device Manager can disable or uninstall hardware drivers; require explicit confirmation
        requires_confirmation=True,
    ),
    "utility-system-configuration": WindowsDestinationSpec(
        app_id="utility-system-configuration",
        display_name="System Configuration (msconfig)",
        category="system_utility",
        launch_method="system_utility",
        target=r"C:\Windows\System32\msconfig.exe",
        aliases=("system configuration", "msconfig"),
        requires_confirmation=True,
    ),
    "utility-services": WindowsDestinationSpec(
        app_id="utility-services",
        display_name="Windows Services",
        category="system_utility",
        launch_method="system_utility",
        target=r"C:\Windows\System32\mmc.exe",
        arguments=(r"C:\Windows\System32\services.msc",),
        aliases=("windows services", "services.msc", "services manager"),
        requires_confirmation=True,
    ),
    "utility-character-map": WindowsDestinationSpec(
        app_id="utility-character-map",
        display_name="Character Map",
        category="system_utility",
        launch_method="system_utility",
        target=r"C:\Windows\System32\charmap.exe",
        aliases=("character map", "charmap"),
        requires_confirmation=False,
    ),
    "utility-snipping-tool": WindowsDestinationSpec(
        app_id="utility-snipping-tool",
        display_name="Snipping Tool",
        category="system_utility",
        launch_method="system_utility",
        target=r"C:\Windows\System32\SnippingTool.exe",
        aliases=("snipping tool", "screen snip"),
        requires_confirmation=False,
    ),
}


WINDOWS_SPECIAL_FOLDERS: Dict[str, WindowsDestinationSpec] = {
    "folder-file-explorer": WindowsDestinationSpec(
        app_id="folder-file-explorer",
        display_name="File Explorer",
        category="special_folder",
        launch_method="shell_folder",
        target=r"C:\Windows\explorer.exe",
        arguments=(),
        aliases=(
            "file explorer",
            "windows explorer",
            "explorer",
            "this pc",
            "my computer",
        ),
    ),
    "folder-downloads": WindowsDestinationSpec(
        app_id="folder-downloads",
        display_name="Downloads",
        category="special_folder",
        launch_method="shell_folder",
        target=r"C:\Windows\explorer.exe",
        arguments=("__USER_DOWNLOADS__",),
        aliases=(
            "downloads",
            "downloads folder",
            "my downloads",
        ),
    ),
    "folder-documents": WindowsDestinationSpec(
        app_id="folder-documents",
        display_name="Documents",
        category="special_folder",
        launch_method="shell_folder",
        target=r"C:\Windows\explorer.exe",
        arguments=("__USER_DOCUMENTS__",),
        aliases=(
            "documents folder",
            "my documents",
        ),
    ),
    "folder-desktop": WindowsDestinationSpec(
        app_id="folder-desktop",
        display_name="Desktop Folder",
        category="special_folder",
        launch_method="shell_folder",
        target=r"C:\Windows\explorer.exe",
        arguments=("__USER_DESKTOP__",),
        aliases=(
            "desktop folder",
            "my desktop folder",
        ),
    ),
    "folder-pictures": WindowsDestinationSpec(
        app_id="folder-pictures",
        display_name="Pictures",
        category="special_folder",
        launch_method="shell_folder",
        target=r"C:\Windows\explorer.exe",
        arguments=("__USER_PICTURES__",),
        aliases=(
            "pictures folder",
            "my pictures",
        ),
    ),
    "folder-recycle-bin": WindowsDestinationSpec(
        app_id="folder-recycle-bin",
        display_name="Recycle Bin",
        category="special_folder",
        launch_method="shell_folder",
        target=r"C:\Windows\explorer.exe",
        arguments=("shell:RecycleBinFolder",),
        aliases=(
            "recycle bin",
            "trash",
            "recycle bin folder",
        ),
    ),
}


# ==============================================================================
# Data Models
# ==============================================================================

@dataclass
class DiscoveredApplication:
    """Metadata record for an indexed application, utility, Settings page, or folder."""
    app_id: str
    name: str
    display_name: str
    executable_path: str
    source: str
    launch_method: str  # "executable", "shortcut", "store_app", "settings_uri", "system_utility", "shell_folder"
    publisher: Optional[str] = None
    install_location: Optional[str] = None
    package_id: Optional[str] = None
    shortcut_path: Optional[str] = None
    arguments: List[str] = field(default_factory=list)
    aliases: List[str] = field(default_factory=list)
    requires_confirmation: bool = False
    discovered_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DiscoveredApplication":
        return cls(
            app_id=str(data.get("app_id", "")),
            name=str(data.get("name", "")),
            display_name=str(data.get("display_name", "")),
            executable_path=str(data.get("executable_path", "")),
            source=str(data.get("source", "unknown")),
            launch_method=str(data.get("launch_method", "executable")),
            publisher=data.get("publisher"),
            install_location=data.get("install_location"),
            package_id=data.get("package_id"),
            shortcut_path=data.get("shortcut_path"),
            arguments=list(data.get("arguments") or []),
            aliases=list(data.get("aliases") or []),
            requires_confirmation=bool(data.get("requires_confirmation", False)),
            discovered_at=str(
                data.get("discovered_at") or datetime.now(timezone.utc).isoformat()
            ),
        )


# ==============================================================================
# Normalization & Security Validation Helpers
# ==============================================================================

def normalize_app_name(raw_name: str) -> str:
    """Normalize an application name for indexing and matching."""
    if not isinstance(raw_name, str):
        return ""
    cleaned = raw_name.strip().lower()
    cleaned = cleaned.strip("'\"")
    if cleaned.endswith(".exe") or cleaned.endswith(".lnk"):
        cleaned = cleaned[:-4].strip()
    # Strip trailing version numbers like " (64-bit)", " x64", " v1.2.3"
    cleaned = re.sub(r"\s*\((?:64-bit|32-bit|x64|x86)\)\s*$", "", cleaned)
    cleaned = re.sub(r"\s+v?\d+(?:\.\d+){1,3}\s*$", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def make_app_slug(raw_name: str) -> str:
    """Create a URL/ID-safe slug from an application name."""
    norm = normalize_app_name(raw_name)
    slug = re.sub(r"[^a-z0-9]+", "-", norm).strip("-")
    return slug or "app"


def get_trusted_install_roots() -> List[Path]:
    """Return canonical list of trusted Windows application installation roots."""
    roots: List[Path] = []
    env_vars = [
        "ProgramFiles",
        "ProgramFiles(x86)",
        "ProgramW6432",
        "LOCALAPPDATA",
        "APPDATA",
        "SystemRoot",
        "windir",
    ]
    for var in env_vars:
        val = os.environ.get(var, "").strip()
        if val:
            try:
                roots.append(Path(val).resolve())
            except Exception:
                continue

    for fallback in (
        r"C:\Program Files",
        r"C:\Program Files (x86)",
        r"C:\Windows",
    ):
        try:
            p = Path(fallback)
            if p.exists():
                roots.append(p.resolve())
        except Exception:
            pass

    extra = os.environ.get("VOXPILOT_EXTRA_APP_DIRS", "").strip()
    if extra:
        for token in extra.split(os.pathsep):
            if token.strip():
                try:
                    roots.append(Path(token.strip()).resolve())
                except Exception:
                    continue

    return roots


def is_suspicious_or_temporary_path(target_path: Path) -> bool:
    """Return True if target_path resides inside a temporary, downloads, or recycle bin location."""
    try:
        resolved = target_path.resolve()
    except Exception:
        return True

    path_str = str(resolved).lower()
    if path_str.startswith("\\\\"):
        # Block UNC network shares
        return True

    suspicious_segments = {
        "temp",
        "tmp",
        "downloads",
        "inetcache",
        "temporary internet files",
        "$recycle.bin",
    }

    # If target is inside an explicitly configured VOXPILOT_EXTRA_APP_DIRS root,
    # inspect only the relative path segments inside that root so pytest tmp_path
    # roots (e.g. AppData\Local\Temp\pytest-of-...\ProgramFiles) work while
    # still blocking <root>/Temp or <root>/Downloads.
    extra = os.environ.get("VOXPILOT_EXTRA_APP_DIRS", "").strip()
    if extra:
        for token in extra.split(os.pathsep):
            if token.strip():
                try:
                    extra_root = Path(token.strip()).resolve()
                    if resolved.is_relative_to(extra_root):
                        rel_parts = [p.lower() for p in resolved.relative_to(extra_root).parts]
                        return any(seg in suspicious_segments for seg in rel_parts)
                except Exception:
                    continue

    parts_lower = [p.lower() for p in resolved.parts]
    for seg in parts_lower:
        if seg in suspicious_segments:
            return True

    # Also check environment TEMP/TMP explicitly
    for env_key in ("TEMP", "TMP"):
        env_val = os.environ.get(env_key, "").strip()
        if env_val:
            try:
                temp_root = Path(env_val).resolve()
                if resolved.is_relative_to(temp_root):
                    return True
            except Exception:
                pass

    return False


def validate_executable_target(
    exe_path: Path | str,
    allow_custom_roots: Optional[List[Path]] = None,
) -> Tuple[bool, Optional[Path], str]:
    """Validate that an executable path is safe, exists on disk, is not a prohibited shell,
    and does not reside in a temporary or downloads folder.
    """
    if not exe_path:
        return False, None, "Executable path is empty."

    raw_str = str(exe_path).strip().strip('"')
    if not raw_str or FORBIDDEN_LAUNCH_CHARS.search(raw_str):
        return False, None, "Executable path contains forbidden characters."

    expanded = os.path.expandvars(raw_str)
    if "%" in expanded:
        return False, None, "Executable path contains unresolved environment variables."

    candidate = Path(expanded)
    try:
        if not candidate.is_file():
            return False, None, f"Executable does not exist on disk: {candidate}"
        resolved = candidate.resolve()
        if not resolved.is_file():
            return False, None, f"Resolved executable does not exist: {resolved}"
    except Exception as exc:
        return False, None, f"Failed to resolve executable path: {exc}"

    if resolved.suffix.lower() != ".exe":
        return False, None, f"Target '{resolved.name}' is not a .exe binary."

    if resolved.name.lower() in PROHIBITED_EXECUTABLES:
        return False, None, f"Executable '{resolved.name}' is blocked by security policy."

    if is_suspicious_or_temporary_path(resolved):
        return (
            False,
            None,
            f"Executable '{resolved}' resides in an untrusted temporary or downloads location.",
        )

    trusted_roots = list(allow_custom_roots or []) + get_trusted_install_roots()
    in_trusted = False
    for root in trusted_roots:
        try:
            if resolved.is_relative_to(root.resolve()):
                in_trusted = True
                break
        except Exception:
            continue

    if not in_trusted:
        return (
            False,
            None,
            f"Executable '{resolved}' is outside trusted Windows installation directories.",
        )

    return True, resolved, "OK"


def validate_shortcut_arguments(raw_args: str) -> Tuple[bool, List[str]]:
    """Validate shortcut arguments (e.g. Electron Squirrel '--processStart Discord.exe')."""
    if not raw_args or not raw_args.strip():
        return True, []

    cleaned = raw_args.strip()
    if FORBIDDEN_LAUNCH_CHARS.search(cleaned):
        return False, []

    # Reject dangerous script/command flags
    lower = cleaned.lower()
    for bad_flag in (
        "-enc",
        "-encodedcommand",
        "-command",
        "/c ",
        "/k ",
        "http://",
        "https://",
        "javascript:",
        "file://",
        "..",
    ):
        if bad_flag in lower:
            return False, []

    # Split safely into argument tokens without shell evaluation
    import shlex
    try:
        tokens = shlex.split(cleaned, posix=False)
        cleaned_tokens = [t.strip('"') for t in tokens if t.strip('"')]
        return True, cleaned_tokens
    except Exception:
        return False, []


# ==============================================================================
# Fast Pure-Python Windows Shortcut (.lnk) Binary Parser + COM Fallback
# ==============================================================================

def parse_windows_lnk(lnk_path: Path) -> Optional[Tuple[str, str, str]]:
    """Extract (target_path, arguments, working_dir) from a Windows .lnk shortcut file.

    Uses fast binary MS-SHLLINK parsing first, falling back to WScript.Shell COM if needed.
    """
    try:
        data = lnk_path.read_bytes()
    except Exception:
        return None

    if len(data) < 76:
        return None

    # HeaderSize must be 0x0000004C (76 bytes)
    header_size, link_flags = struct.unpack_from("<II", data, 0)
    if header_size != 0x4C:
        return None

    has_link_target_id_list = bool(link_flags & 0x01)
    has_link_info = bool(link_flags & 0x02)
    has_name = bool(link_flags & 0x04)
    has_relative_path = bool(link_flags & 0x08)
    has_working_dir = bool(link_flags & 0x10)
    has_arguments = bool(link_flags & 0x20)
    is_unicode = bool(link_flags & 0x80)

    offset = 76
    # 1. Skip LinkTargetIDList if present
    if has_link_target_id_list:
        if offset + 2 > len(data):
            return None
        id_list_size = struct.unpack_from("<H", data, offset)[0]
        offset += 2 + id_list_size

    target_path = ""
    # 2. Parse LinkInfo if present
    if has_link_info and offset + 28 <= len(data):
        link_info_size, link_info_header_size, link_info_flags = struct.unpack_from(
            "<III", data, offset
        )
        if link_info_flags & 0x01:  # VolumeIDAndLocalBasePath
            local_base_path_offset = struct.unpack_from("<I", data, offset + 16)[0]
            if link_info_header_size >= 0x24 and offset + 32 <= len(data):
                uni_offset = struct.unpack_from("<I", data, offset + 28)[0]
                if uni_offset > 0 and offset + uni_offset < len(data):
                    end = data.find(b"\x00\x00", offset + uni_offset)
                    if end != -1:
                        if (end - (offset + uni_offset)) % 2 != 0:
                            end += 1
                        target_path = data[offset + uni_offset : end].decode(
                            "utf-16-le", errors="ignore"
                        )
            if not target_path and local_base_path_offset > 0:
                start = offset + local_base_path_offset
                if start < len(data):
                    end = data.find(b"\x00", start)
                    if end != -1:
                        target_path = data[start:end].decode("cp1252", errors="ignore")
        offset += link_info_size

    # 3. Parse StringData sections (Name, RelativePath, WorkingDir, Arguments)
    def _read_string_data(cur: int) -> Tuple[str, int]:
        if cur + 2 > len(data):
            return "", cur
        count = struct.unpack_from("<H", data, cur)[0]
        cur += 2
        if is_unicode:
            byte_len = count * 2
            val = data[cur : cur + byte_len].decode("utf-16-le", errors="ignore")
            return val, cur + byte_len
        else:
            val = data[cur : cur + count].decode("cp1252", errors="ignore")
            return val, cur + count

    if has_name:
        _, offset = _read_string_data(offset)
    rel_path = ""
    if has_relative_path:
        rel_path, offset = _read_string_data(offset)
    working_dir = ""
    if has_working_dir:
        working_dir, offset = _read_string_data(offset)
    arguments = ""
    if has_arguments:
        arguments, offset = _read_string_data(offset)
    if bool(link_flags & 0x40):  # HasIconLocation
        _, offset = _read_string_data(offset)

    if not target_path and rel_path and working_dir:
        try:
            target_path = str((Path(working_dir) / rel_path).resolve())
        except Exception:
            pass

    # 4. Parse ExtraData blocks for EnvironmentVariableDataBlock (0xA0000001) if target_path is still empty
    while not target_path and offset + 8 <= len(data):
        block_size, block_sig = struct.unpack_from("<II", data, offset)
        if block_size < 4 or offset + block_size > len(data):
            break
        if block_sig == 0xA0000001 and block_size >= 788:
            # EnvironmentVariableDataBlock: TargetUnicode starts at offset + 268 (520 bytes)
            uni_raw = data[offset + 268 : offset + 788]
            end = uni_raw.find(b"\x00\x00")
            if end != -1:
                if end % 2 != 0:
                    end += 1
                target_path = uni_raw[:end].decode("utf-16-le", errors="ignore").strip()
            if not target_path:
                ansi_raw = data[offset + 8 : offset + 268]
                end_a = ansi_raw.find(b"\x00")
                if end_a != -1:
                    target_path = ansi_raw[:end_a].decode("cp1252", errors="ignore").strip()
            break
        offset += block_size

    if target_path:
        return target_path.strip(), arguments.strip(), working_dir.strip()

    return None


# ==============================================================================
# Application Discovery Service
# ==============================================================================

class ApplicationDiscoveryService:
    """Discovers, deduplicates, caches, and searches installed Windows applications."""

    def __init__(
        self,
        index_file: Optional[Path] = None,
        cache_ttl_seconds: float = 1800.0,
        auto_discover: bool = True,
    ) -> None:
        self._lock = threading.RLock()
        self._index_file = index_file
        self._cache_ttl_seconds = cache_ttl_seconds
        self._auto_discover = auto_discover

        self._entries: Dict[str, DiscoveredApplication] = {}
        self._alias_to_ids: Dict[str, Set[str]] = {}
        self._last_refreshed_monotonic: Optional[float] = None
        self._last_refreshed_iso: Optional[str] = None
        self._discovery_errors: List[str] = []

        # Always register built-in Windows Settings, Utilities, and Special Folders immediately
        self._register_builtin_destinations()

        # Load persisted cache if present on disk
        self._load_persisted_index()

    @property
    def index_file_path(self) -> Path:
        if self._index_file is not None:
            self._index_file.parent.mkdir(parents=True, exist_ok=True)
            return self._index_file
        return get_config_dir() / "application_index.json"

    def _register_builtin_destinations(self) -> None:
        """Populate fixed allowlisted Windows Settings pages, utilities, and folders."""
        now_iso = datetime.now(timezone.utc).isoformat()

        for spec_dict in (
            WINDOWS_SETTINGS_DESTINATIONS,
            WINDOWS_BUILT_IN_UTILITIES,
            WINDOWS_SPECIAL_FOLDERS,
        ):
            for spec in spec_dict.values():
                entry = DiscoveredApplication(
                    app_id=spec.app_id,
                    name=normalize_app_name(spec.display_name),
                    display_name=spec.display_name,
                    executable_path=spec.target,
                    source=spec.category,
                    launch_method=spec.launch_method,
                    publisher=spec.publisher,
                    install_location=None,
                    package_id=None,
                    arguments=list(spec.arguments),
                    aliases=list(spec.aliases),
                    requires_confirmation=spec.requires_confirmation,
                    discovered_at=now_iso,
                )
                self._add_or_merge_entry(entry)

        # Also register core built-in desktop apps (Notepad & Calculator) as baseline entries
        for core_id, disp_name, exe_path, aliases in [
            (
                "notepad",
                "Notepad",
                r"C:\Windows\System32\notepad.exe",
                ["notepad", "text editor", "notes", "editor"],
            ),
            (
                "calculator",
                "Calculator",
                r"C:\Windows\System32\calc.exe",
                ["calculator", "calc"],
            ),
        ]:
            self._add_or_merge_entry(
                DiscoveredApplication(
                    app_id=core_id,
                    name=normalize_app_name(disp_name),
                    display_name=disp_name,
                    executable_path=exe_path,
                    source="builtin",
                    launch_method="executable",
                    publisher="Microsoft Corporation",
                    install_location=r"C:\Windows\System32",
                    aliases=aliases,
                    requires_confirmation=False,
                    discovered_at=now_iso,
                )
            )

    def _add_or_merge_entry(self, entry: DiscoveredApplication) -> None:
        """Insert or merge a discovered application into the index, avoiding duplicates."""
        norm_name = normalize_app_name(entry.display_name or entry.name)
        if not norm_name:
            return
        entry.name = norm_name

        # Enrich with well-known aliases if matching
        known_aliases = KNOWN_APPLICATION_ALIASES.get(norm_name, [])
        merged_aliases: List[str] = []
        seen_aliases: Set[str] = set()
        for a in [entry.app_id, norm_name, entry.display_name.lower()] + list(entry.aliases) + known_aliases:
            a_clean = a.strip().lower()
            if a_clean and a_clean not in seen_aliases:
                seen_aliases.add(a_clean)
                merged_aliases.append(a_clean)
        entry.aliases = merged_aliases

        existing = self._entries.get(entry.app_id)
        if existing is not None:
            # Merge metadata: prefer non-empty publisher and install_location
            if not existing.publisher and entry.publisher:
                existing.publisher = entry.publisher
            if not existing.install_location and entry.install_location:
                existing.install_location = entry.install_location
            for a in entry.aliases:
                if a not in existing.aliases:
                    existing.aliases.append(a)
                self._alias_to_ids.setdefault(a, set()).add(existing.app_id)
            return

        self._entries[entry.app_id] = entry
        for a in entry.aliases:
            self._alias_to_ids.setdefault(a, set()).add(entry.app_id)

    def _load_persisted_index(self) -> bool:
        """Load cached application index from disk if valid."""
        path = self.index_file_path
        if not path.is_file():
            return False
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return False
            items = raw.get("applications", [])
            if not isinstance(items, list):
                return False
            with self._lock:
                for item in items:
                    if isinstance(item, dict):
                        entry = DiscoveredApplication.from_dict(item)
                        self._add_or_merge_entry(entry)
                self._last_refreshed_iso = raw.get("refreshed_at")
            return True
        except Exception as exc:
            logger.debug("Could not load persisted application index: %s", exc)
            return False

    def _save_persisted_index(self) -> None:
        """Persist current application index to %LOCALAPPDATA%\\VoxPilot\\config\\application_index.json."""
        path = self.index_file_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                payload = {
                    "refreshed_at": self._last_refreshed_iso,
                    "count": len(self._entries),
                    "applications": [
                        e.to_dict()
                        for e in sorted(self._entries.values(), key=lambda x: x.display_name.lower())
                    ],
                }
            tmp_path = path.with_suffix(".json.tmp")
            tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp_path.replace(path)
        except Exception as exc:
            logger.warning("Failed to save application index cache: %s", exc)

    @property
    def _loaded(self) -> bool:
        return self._last_refreshed_monotonic is not None

    def ensure_indexed(self) -> None:
        """Ensure discovery has run at least once or when TTL expires."""
        with self._lock:
            now_mono = time.monotonic()
            if (
                self._last_refreshed_monotonic is not None
                and (now_mono - self._last_refreshed_monotonic) < self._cache_ttl_seconds
            ):
                return
            if not self._auto_discover:
                return
        self.refresh_index()

    def refresh_index(self, include_store_apps: bool = True) -> List[DiscoveredApplication]:
        """Run multi-source Windows application discovery and rebuild the searchable index."""
        errors: List[str] = []
        discovered: List[DiscoveredApplication] = []

        # 1. Start Menu shortcuts (Current User & All Users)
        try:
            discovered.extend(self._discover_start_menu_shortcuts())
        except Exception as exc:
            errors.append(f"start_menu: {exc}")

        # 2. Desktop shortcuts (Current User, OneDrive Desktop, & Public Desktop)
        try:
            discovered.extend(self._discover_desktop_shortcuts())
        except Exception as exc:
            errors.append(f"desktop_shortcuts: {exc}")

        # 3. Windows Registry (App Paths + 64-bit & 32-bit Uninstall keys)
        try:
            discovered.extend(self._discover_registry_applications())
        except Exception as exc:
            errors.append(f"registry: {exc}")

        # 4. Known installation directories (Program Files, Program Files (x86), LocalAppData)
        try:
            discovered.extend(self._discover_known_directories())
        except Exception as exc:
            errors.append(f"known_directories: {exc}")

        # 5. Microsoft Store / MSIX / Appx applications
        if include_store_apps:
            try:
                discovered.extend(self._discover_microsoft_store_apps())
            except Exception as exc:
                errors.append(f"microsoft_store: {exc}")

        now_iso = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._entries.clear()
            self._alias_to_ids.clear()
            self._register_builtin_destinations()
            for entry in discovered:
                self._add_or_merge_entry(entry)
            self._last_refreshed_monotonic = time.monotonic()
            self._last_refreshed_iso = now_iso
            self._discovery_errors = errors
            final_list = sorted(self._entries.values(), key=lambda e: e.display_name.lower())

        self._save_persisted_index()
        return final_list

    # --------------------------------------------------------------------------
    # Discovery Source 1 & 5: Start Menu & Desktop Shortcuts
    # --------------------------------------------------------------------------

    def _get_start_menu_dirs(self) -> List[Path]:
        dirs: List[Path] = []
        appdata = os.environ.get("APPDATA", "").strip()
        if appdata:
            p = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            if p.is_dir():
                dirs.append(p)
        progdata = os.environ.get("ProgramData", r"C:\ProgramData").strip()
        if progdata:
            p = Path(progdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            if p.is_dir():
                dirs.append(p)
        return dirs

    def _get_desktop_dirs(self) -> List[Path]:
        dirs: List[Path] = []
        home = Path.home()
        for candidate in [
            home / "Desktop",
            home / "OneDrive" / "Desktop",
            Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "Desktop",
        ]:
            try:
                if candidate.is_dir() and candidate not in dirs:
                    dirs.append(candidate)
            except Exception:
                continue
        return dirs

    def _scan_shortcut_directory(
        self,
        root_dir: Path,
        source_name: str,
        max_depth: int = 3,
    ) -> List[DiscoveredApplication]:
        results: List[DiscoveredApplication] = []
        if not root_dir.is_dir():
            return results

        now_iso = datetime.now(timezone.utc).isoformat()

        def _walk(current: Path, depth: int) -> None:
            if depth > max_depth:
                return
            try:
                entries = list(current.iterdir())
            except Exception:
                return

            for item in entries:
                try:
                    if item.is_dir():
                        _walk(item, depth + 1)
                    elif item.is_file() and item.suffix.lower() == ".lnk":
                        stem = item.stem.strip()
                        stem_lower = stem.lower()
                        if any(excl in stem_lower for excl in _EXCLUDED_SHORTCUT_KEYWORDS):
                            continue

                        parsed = parse_windows_lnk(item)
                        if not parsed:
                            continue
                        target_str, args_str, work_dir = parsed
                        ok_exe, resolved_exe, _ = validate_executable_target(target_str)
                        if not ok_exe or resolved_exe is None:
                            continue

                        ok_args, parsed_args = validate_shortcut_arguments(args_str)
                        if not ok_args:
                            continue

                        launch_method = "shortcut"
                        app_slug = make_app_slug(stem)
                        results.append(
                            DiscoveredApplication(
                                app_id=app_slug,
                                name=normalize_app_name(stem),
                                display_name=stem,
                                executable_path=str(resolved_exe),
                                shortcut_path=str(item.resolve()),
                                source=source_name,
                                launch_method=launch_method,
                                publisher=None,
                                install_location=str(resolved_exe.parent),
                                arguments=parsed_args,
                                aliases=[normalize_app_name(stem), resolved_exe.stem.lower()],
                                requires_confirmation=False,
                                discovered_at=now_iso,
                            )
                        )
                except Exception:
                    continue

        _walk(root_dir, 0)
        return results

    def _discover_start_menu_shortcuts(self) -> List[DiscoveredApplication]:
        out: List[DiscoveredApplication] = []
        for sm_dir in self._get_start_menu_dirs():
            out.extend(self._scan_shortcut_directory(sm_dir, source_name="start_menu", max_depth=3))
        return out

    def _discover_desktop_shortcuts(self) -> List[DiscoveredApplication]:
        out: List[DiscoveredApplication] = []
        for dt_dir in self._get_desktop_dirs():
            out.extend(
                self._scan_shortcut_directory(dt_dir, source_name="desktop_shortcut", max_depth=1)
            )
        return out

    # --------------------------------------------------------------------------
    # Discovery Source 2: Windows Registry (Uninstall & App Paths)
    # --------------------------------------------------------------------------

    def _discover_registry_applications(self) -> List[DiscoveredApplication]:
        results: List[DiscoveredApplication] = []
        if winreg is None:
            return results

        now_iso = datetime.now(timezone.utc).isoformat()

        # 2A. App Paths (HKLM & HKCU)
        app_paths_subkey = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(hive, app_paths_subkey, 0, winreg.KEY_READ) as parent_key:
                    num_subkeys, _, _ = winreg.QueryInfoKey(parent_key)
                    for idx in range(min(num_subkeys, 500)):
                        try:
                            sub_name = winreg.EnumKey(parent_key, idx)
                            if not sub_name.lower().endswith(".exe"):
                                continue
                            if sub_name.lower() in PROHIBITED_EXECUTABLES:
                                continue
                            with winreg.OpenKey(parent_key, sub_name, 0, winreg.KEY_READ) as sk:
                                val, _ = winreg.QueryValueEx(sk, "")
                                if not isinstance(val, str) or not val.strip():
                                    continue
                                ok, resolved_exe, _ = validate_executable_target(val)
                                if not ok or resolved_exe is None:
                                    continue
                                stem = resolved_exe.stem
                                slug = make_app_slug(stem)
                                results.append(
                                    DiscoveredApplication(
                                        app_id=slug,
                                        name=normalize_app_name(stem),
                                        display_name=stem,
                                        executable_path=str(resolved_exe),
                                        source="registry",
                                        launch_method="executable",
                                        install_location=str(resolved_exe.parent),
                                        aliases=[normalize_app_name(stem), sub_name[:-4].lower()],
                                        discovered_at=now_iso,
                                    )
                                )
                        except OSError:
                            continue
            except OSError:
                continue

        # 2B. Uninstall Keys (64-bit HKLM, 32-bit WOW6432Node HKLM, and HKCU)
        uninstall_locations = [
            (
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
            ),
            (
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
            ),
            (
                winreg.HKEY_CURRENT_USER,
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
            ),
        ]

        for hive, subkey_path in uninstall_locations:
            try:
                with winreg.OpenKey(hive, subkey_path, 0, winreg.KEY_READ) as parent_key:
                    num_subkeys, _, _ = winreg.QueryInfoKey(parent_key)
                    for idx in range(min(num_subkeys, 800)):
                        try:
                            sub_name = winreg.EnumKey(parent_key, idx)
                            with winreg.OpenKey(parent_key, sub_name, 0, winreg.KEY_READ) as sk:
                                display_name = self._query_reg_str(sk, "DisplayName")
                                if not display_name:
                                    continue
                                sys_comp = self._query_reg_int(sk, "SystemComponent")
                                if sys_comp == 1:
                                    continue

                                publisher = self._query_reg_str(sk, "Publisher")
                                install_loc = self._query_reg_str(sk, "InstallLocation")
                                display_icon = self._query_reg_str(sk, "DisplayIcon")

                                candidate_exe = self._resolve_exe_from_uninstall_entry(
                                    display_name=display_name,
                                    display_icon=display_icon,
                                    install_location=install_loc,
                                )
                                if candidate_exe is None:
                                    continue

                                slug = make_app_slug(display_name)
                                results.append(
                                    DiscoveredApplication(
                                        app_id=slug,
                                        name=normalize_app_name(display_name),
                                        display_name=display_name.strip(),
                                        executable_path=str(candidate_exe),
                                        source="registry",
                                        launch_method="executable",
                                        publisher=publisher,
                                        install_location=install_loc or str(candidate_exe.parent),
                                        aliases=[
                                            normalize_app_name(display_name),
                                            candidate_exe.stem.lower(),
                                        ],
                                        discovered_at=now_iso,
                                    )
                                )
                        except OSError:
                            continue
            except OSError:
                continue

        return results

    @staticmethod
    def _query_reg_str(key: Any, value_name: str) -> Optional[str]:
        if winreg is None:
            return None
        try:
            val, _ = winreg.QueryValueEx(key, value_name)
            if isinstance(val, str) and val.strip():
                return val.strip()
        except OSError:
            pass
        return None

    @staticmethod
    def _query_reg_int(key: Any, value_name: str) -> Optional[int]:
        if winreg is None:
            return None
        try:
            val, _ = winreg.QueryValueEx(key, value_name)
            if isinstance(val, int):
                return val
        except OSError:
            pass
        return None

    def _resolve_exe_from_uninstall_entry(
        self,
        display_name: str,
        display_icon: Optional[str],
        install_location: Optional[str],
    ) -> Optional[Path]:
        """Attempt to resolve a valid main executable from DisplayIcon or InstallLocation."""
        if display_icon:
            icon_clean = display_icon.strip()
            # Strip trailing icon index like ",0" or ",-101"
            icon_clean = re.sub(r",\s*-?\d+\s*$", "", icon_clean).strip().strip('"')
            if icon_clean.lower().endswith(".exe"):
                lower_name = Path(icon_clean).name.lower()
                if not any(
                    bad in lower_name for bad in ("unins", "setup", "update", "installer")
                ):
                    ok, resolved, _ = validate_executable_target(icon_clean)
                    if ok and resolved is not None:
                        return resolved

        if install_location:
            loc_clean = os.path.expandvars(install_location.strip().strip('"'))
            loc_path = Path(loc_clean)
            try:
                if loc_path.is_dir():
                    norm_disp = normalize_app_name(display_name)
                    for exe_file in loc_path.glob("*.exe"):
                        fname = exe_file.name.lower()
                        if any(
                            bad in fname
                            for bad in ("unins", "setup", "crash", "updater", "helper")
                        ):
                            continue
                        if exe_file.stem.lower() in norm_disp or norm_disp in exe_file.stem.lower():
                            ok, resolved, _ = validate_executable_target(exe_file)
                            if ok and resolved is not None:
                                return resolved
            except Exception:
                pass

        return None

    # --------------------------------------------------------------------------
    # Discovery Source 3: Microsoft Store / UWP / Packaged Applications
    # --------------------------------------------------------------------------

    def _discover_microsoft_store_apps(self) -> List[DiscoveredApplication]:
        """Discover registered Microsoft Store & UWP apps via Get-StartApps."""
        results: List[DiscoveredApplication] = []
        if sys.platform != "win32":
            return results

        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            completed = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress",
                ],
                capture_output=True,
                text=True,
                timeout=6.0,
                check=False,
            )
            if completed.returncode == 0 and completed.stdout.strip():
                raw_data = json.loads(completed.stdout.strip())
                items = raw_data if isinstance(raw_data, list) else [raw_data]
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("Name") or "").strip()
                    app_id_raw = str(item.get("AppID") or "").strip()
                    if not name or not app_id_raw:
                        continue
                    if FORBIDDEN_LAUNCH_CHARS.search(app_id_raw):
                        continue

                    # Packaged Microsoft Store / UWP apps have an '!' in their AppUserModelId
                    if "!" in app_id_raw and not app_id_raw.lower().endswith(".exe"):
                        slug = make_app_slug(name)
                        pkg_family = app_id_raw.split("!", 1)[0]
                        results.append(
                            DiscoveredApplication(
                                app_id=slug,
                                name=normalize_app_name(name),
                                display_name=name,
                                executable_path=f"shell:AppsFolder\\{app_id_raw}",
                                source="microsoft_store",
                                launch_method="store_app",
                                publisher="Microsoft Store / Packaged App",
                                package_id=pkg_family,
                                aliases=[normalize_app_name(name)],
                                requires_confirmation=False,
                                discovered_at=now_iso,
                            )
                        )
        except Exception as exc:
            logger.debug("Microsoft Store Get-StartApps discovery skipped/failed: %s", exc)

        return results

    # --------------------------------------------------------------------------
    # Discovery Source 4: Known Installation Directories (Bounded)
    # --------------------------------------------------------------------------

    def _discover_known_directories(self) -> List[DiscoveredApplication]:
        """Scan bounded top-level directories in Program Files and %LOCALAPPDATA%\\Programs."""
        results: List[DiscoveredApplication] = []
        now_iso = datetime.now(timezone.utc).isoformat()

        scan_roots: List[Path] = []
        for env_var in ("ProgramFiles", "ProgramFiles(x86)"):
            val = os.environ.get(env_var, "").strip()
            if val:
                p = Path(val)
                if p.is_dir():
                    scan_roots.append(p)

        local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
        if local_appdata:
            programs_dir = Path(local_appdata) / "Programs"
            if programs_dir.is_dir():
                scan_roots.append(programs_dir)

        extra = os.environ.get("VOXPILOT_EXTRA_APP_DIRS", "").strip()
        if extra:
            for token in extra.split(os.pathsep):
                if token.strip():
                    p = Path(token.strip())
                    if p.is_dir():
                        scan_roots.append(p)

        for root in scan_roots:
            try:
                subdirs = list(root.iterdir())
            except Exception:
                continue

            for subdir in subdirs[:150]:
                try:
                    if not subdir.is_dir():
                        continue
                    candidate_dirs = [subdir, subdir / "bin", subdir / "Application"]
                    for cdir in candidate_dirs:
                        if not cdir.is_dir():
                            continue
                        for exe_file in cdir.glob("*.exe"):
                            fname = exe_file.name.lower()
                            if any(
                                skip in fname
                                for skip in (
                                    "unins",
                                    "setup",
                                    "update",
                                    "crash",
                                    "helper",
                                    "daemon",
                                    "service",
                                    "elevate",
                                    "eula",
                                )
                            ):
                                continue
                            ok, resolved, _ = validate_executable_target(
                                exe_file, allow_custom_roots=[root]
                            )
                            if not ok or resolved is None:
                                continue
                            disp = subdir.name if cdir != subdir else exe_file.stem
                            slug = make_app_slug(disp)
                            results.append(
                                DiscoveredApplication(
                                    app_id=slug,
                                    name=normalize_app_name(disp),
                                    display_name=disp,
                                    executable_path=str(resolved),
                                    source="known_directory",
                                    launch_method="executable",
                                    install_location=str(subdir.resolve()),
                                    aliases=[
                                        normalize_app_name(disp),
                                        exe_file.stem.lower(),
                                    ],
                                    discovered_at=now_iso,
                                )
                            )
                except Exception:
                    continue

        return results

    # --------------------------------------------------------------------------
    # Querying, Fuzzy Matching, & Ambiguity Detection
    # --------------------------------------------------------------------------

    def list_applications(
        self,
        search_query: Optional[str] = None,
        source_filter: Optional[str] = None,
        limit: int = 500,
    ) -> List[DiscoveredApplication]:
        """Return discovered applications sorted alphabetically, with optional search/source filter."""
        self.ensure_indexed()
        with self._lock:
            items = list(self._entries.values())

        if source_filter and source_filter.strip().lower() != "all":
            sf = source_filter.strip().lower()
            items = [e for e in items if e.source.lower() == sf]

        if search_query and search_query.strip():
            q = normalize_app_name(search_query)
            scored: List[Tuple[float, DiscoveredApplication]] = []
            for entry in items:
                score = self._score_entry(q, entry)
                if score >= 0.45:
                    scored.append((score, entry))
            scored.sort(key=lambda pair: (-pair[0], pair[1].display_name.lower()))
            items = [pair[1] for pair in scored]
        else:
            items.sort(key=lambda e: e.display_name.lower())

        return items[: max(1, limit)]

    def search(self, query: str, limit: int = 50) -> List[DiscoveredApplication]:
        """Search indexed applications by natural-language query."""
        return self.list_applications(search_query=query, limit=limit)

    def get_index_summary(self) -> Dict[str, Any]:
        """Return summary statistics of the application index."""
        with self._lock:
            by_source: Dict[str, int] = {}
            for entry in self._entries.values():
                by_source[entry.source] = by_source.get(entry.source, 0) + 1
            return {
                "total_count": len(self._entries),
                "total_applications": len(self._entries),
                "by_source": by_source,
                "sources_breakdown": by_source,
                "last_refreshed_at": self._last_refreshed_iso,
                "last_refreshed": self._last_refreshed_iso,
                "errors": list(self._discovery_errors),
            }

    @staticmethod
    def _score_entry(query_norm: str, entry: DiscoveredApplication) -> float:
        """Compute match confidence [0.0, 1.0] between a normalized query and an indexed entry."""
        if not query_norm:
            return 0.0

        # 1. Exact match on app_id, name, display_name, or any alias
        if (
            query_norm == entry.app_id
            or query_norm == entry.name
            or query_norm == entry.display_name.lower()
            or query_norm in entry.aliases
        ):
            return 1.0

        # 2. Word-boundary / prefix match on display_name or alias
        for candidate in [entry.name] + entry.aliases:
            if candidate.startswith(query_norm + " ") or candidate.endswith(" " + query_norm):
                return 0.88
            if query_norm in candidate.split():
                return 0.85
            if query_norm in candidate:
                return 0.75

        # 3. Fuzzy similarity ratio (handles minor typos like "spotfy" -> "spotify")
        best_ratio = 0.0
        for candidate in [entry.name] + entry.aliases:
            ratio = difflib.SequenceMatcher(None, query_norm, candidate).ratio()
            if ratio > best_ratio:
                best_ratio = ratio

        return best_ratio if best_ratio >= 0.72 else 0.0

    def resolve_query(self, query: str) -> Dict[str, Any]:
        """Resolve a natural-language application, Settings, utility, or folder query.

        Returns a dict with:
        - status: "resolved" | "ambiguous" | "not_found"
        - match: Optional[DiscoveredApplication]
        - candidates: List[DiscoveredApplication]
        - message: str
        """
        self.ensure_indexed()
        query_norm = normalize_app_name(query)
        if not query_norm:
            return {
                "status": "not_found",
                "match": None,
                "candidates": [],
                "message": f"Application '{query}' could not be found on this Windows system.",
            }

        # Strip common trailing filler words like "app" or "application" unless it's "installed apps"
        if query_norm not in ("installed apps", "default apps", "apps and features", "apps & features"):
            query_stripped = re.sub(r"\s+(?:app|application|program)$", "", query_norm).strip()
        else:
            query_stripped = query_norm

        with self._lock:
            # Check exact alias map first
            for q_cand in (query_norm, query_stripped):
                exact_ids = self._alias_to_ids.get(q_cand)
                if exact_ids:
                    matched_entries = [self._entries[aid] for aid in exact_ids if aid in self._entries]
                    if len(matched_entries) == 1:
                        return {
                            "status": "resolved",
                            "match": matched_entries[0],
                            "candidates": matched_entries,
                            "message": f"Resolved '{query}' to {matched_entries[0].display_name}.",
                        }
                    elif len(matched_entries) > 1:
                        cand_names = ", ".join(c.display_name for c in matched_entries)
                        return {
                            "status": "ambiguous",
                            "match": None,
                            "candidates": matched_entries,
                            "message": (
                                f"Multiple installed applications match '{query}': {cand_names}. "
                                "Please specify which application you want to open."
                            ),
                        }

            # Score all entries
            scored: List[Tuple[float, DiscoveredApplication]] = []
            for entry in self._entries.values():
                s1 = self._score_entry(query_norm, entry)
                s2 = self._score_entry(query_stripped, entry) if query_stripped != query_norm else s1
                score = max(s1, s2)
                if score >= 0.72:
                    scored.append((score, entry))

        if not scored:
            return {
                "status": "not_found",
                "match": None,
                "candidates": [],
                "message": (
                    f"Application '{query}' is not installed or could not be found on this system. "
                    "Verify that the application is installed or refresh the application index."
                ),
            }

        scored.sort(key=lambda pair: (-pair[0], pair[1].display_name.lower()))
        top_score, top_entry = scored[0]

        if len(scored) == 1:
            return {
                "status": "resolved",
                "match": top_entry,
                "candidates": [top_entry],
                "message": f"Resolved '{query}' to {top_entry.display_name}.",
            }

        # Check if multiple entries share the same top tier (within 0.04 of top_score)
        close_matches = [
            entry for score, entry in scored if (top_score - score) <= 0.04
        ]
        if len(close_matches) > 1:
            cand_names = ", ".join(c.display_name for c in close_matches[:6])
            return {
                "status": "ambiguous",
                "match": None,
                "candidates": close_matches[:6],
                "message": (
                    f"Multiple installed applications match '{query}': {cand_names}. "
                    "Please specify which application you want to open."
                ),
            }

        return {
            "status": "resolved",
            "match": top_entry,
            "candidates": [top_entry],
            "message": f"Resolved '{query}' to {top_entry.display_name}.",
        }

    def get_planner_application_hints(self, max_items: int = 40) -> str:
        """Return a concise comma-separated string of discovered application display names for LLM context."""
        self.ensure_indexed()
        with self._lock:
            names = sorted({e.display_name for e in self._entries.values()})
        return ", ".join(names[:max_items])


_SHARED_DISCOVERY_SERVICE: Optional[ApplicationDiscoveryService] = None
_SHARED_DISCOVERY_LOCK = threading.Lock()


def get_shared_discovery_service() -> ApplicationDiscoveryService:
    """Return the singleton ApplicationDiscoveryService instance."""
    global _SHARED_DISCOVERY_SERVICE
    with _SHARED_DISCOVERY_LOCK:
        if _SHARED_DISCOVERY_SERVICE is None:
            _SHARED_DISCOVERY_SERVICE = ApplicationDiscoveryService()
        return _SHARED_DISCOVERY_SERVICE


def reset_shared_discovery_service() -> None:
    """Reset the singleton ApplicationDiscoveryService (used in tests)."""
    global _SHARED_DISCOVERY_SERVICE
    with _SHARED_DISCOVERY_LOCK:
        _SHARED_DISCOVERY_SERVICE = None
