"""
VoxPilot - Runtime Path Resolution Subsystem
Manages unified path resolution across development and PyInstaller frozen modes.
Distinguishes between bundled read-only assets (sys._MEIPASS / bundle dir)
and writable user data (%LOCALAPPDATA%\\VoxPilot).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional, List


def is_frozen() -> bool:
    """Check whether the application is running inside a PyInstaller frozen bundle."""
    return getattr(sys, "frozen", False)


def get_project_root() -> Path:
    """Return the project source root in development mode."""
    return Path(__file__).resolve().parent.parent


def get_bundle_dir() -> Path:
    """Return the read-only bundle directory containing packaged assets.

    In PyInstaller frozen mode, returns sys._MEIPASS (one-file extraction or internal bundle).
    In development mode, returns the project repository root.
    """
    if is_frozen() and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS).resolve()
    elif is_frozen():
        return Path(sys.executable).parent.resolve()
    return get_project_root()


def get_executable_dir() -> Path:
    """Return the directory containing the running executable or main script."""
    if is_frozen():
        return Path(sys.executable).parent.resolve()
    return get_project_root()


def get_user_data_dir() -> Path:
    """Return the root writable directory for persistent user data.

    Precedence:
    1. VOXPILOT_USER_DATA_DIR environment variable
    2. Frozen mode: %LOCALAPPDATA%\\VoxPilot (Windows) or ~/.voxpilot
    3. Dev mode: <project_root>/data (or %LOCALAPPDATA%\\VoxPilot if VOXPILOT_FORCE_USER_DATA is set)
    """
    custom = os.environ.get("VOXPILOT_USER_DATA_DIR", "").strip()
    if custom:
        p = Path(custom).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    force_user = os.environ.get("VOXPILOT_FORCE_USER_DATA", "").strip().lower() in ("1", "true", "yes")

    if is_frozen() or force_user:
        base = os.environ.get("LOCALAPPDATA")
        if base:
            user_dir = Path(base) / "VoxPilot"
        else:
            user_dir = Path.home() / ".voxpilot"
        user_dir.mkdir(parents=True, exist_ok=True)
        return user_dir.resolve()

    # Development mode: default to repository root data directory
    dev_dir = get_project_root() / "data"
    dev_dir.mkdir(parents=True, exist_ok=True)
    return dev_dir.resolve()


def get_database_path() -> Path:
    """Return the canonical SQLite database file path.

    Precedence:
    1. VOXPILOT_DB_PATH environment variable
    2. Frozen mode: %LOCALAPPDATA%\\VoxPilot\\data\\voxpilot.db
    3. Dev mode: <project_root>/data/voxpilot.db
    """
    custom = os.environ.get("VOXPILOT_DB_PATH", "").strip()
    if custom:
        p = Path(custom).resolve()
        if not str(p).startswith(":memory:"):
            p.parent.mkdir(parents=True, exist_ok=True)
        return p

    if is_frozen() or os.environ.get("VOXPILOT_FORCE_USER_DATA", "").strip().lower() in ("1", "true", "yes"):
        db_dir = get_user_data_dir() / "data"
        db_dir.mkdir(parents=True, exist_ok=True)
        return (db_dir / "voxpilot.db").resolve()

    dev_db = get_project_root() / "data" / "voxpilot.db"
    dev_db.parent.mkdir(parents=True, exist_ok=True)
    return dev_db.resolve()


def get_export_dir() -> Path:
    """Return the approved audit export directory."""
    custom = os.environ.get("VOXPILOT_EXPORT_DIR", "").strip()
    if custom:
        p = Path(custom).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    if is_frozen() or os.environ.get("VOXPILOT_FORCE_USER_DATA", "").strip().lower() in ("1", "true", "yes"):
        export_dir = get_user_data_dir() / "exports"
    else:
        export_dir = get_project_root() / "data" / "exports"

    export_dir.mkdir(parents=True, exist_ok=True)
    return export_dir.resolve()


def get_log_dir() -> Path:
    """Return the application log directory."""
    custom = os.environ.get("VOXPILOT_LOG_DIR", "").strip()
    if custom:
        p = Path(custom).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    if is_frozen() or os.environ.get("VOXPILOT_FORCE_USER_DATA", "").strip().lower() in ("1", "true", "yes"):
        log_dir = get_user_data_dir() / "logs"
    else:
        log_dir = get_project_root() / "logs"

    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir.resolve()


def get_config_dir() -> Path:
    """
    Return persistent user configuration directory.
    Always stores user settings under %LOCALAPPDATA%\\VoxPilot\\config
    (unless overridden via VOXPILOT_CONFIG_DIR or VOXPILOT_USER_DATA_DIR).
    """
    custom = os.environ.get("VOXPILOT_CONFIG_DIR", "").strip()
    if custom:
        p = Path(custom).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    custom_user_data = os.environ.get("VOXPILOT_USER_DATA_DIR", "").strip()
    if custom_user_data:
        cfg_dir = Path(custom_user_data).resolve() / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        return cfg_dir

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        cfg_dir = Path(local_app_data) / "VoxPilot" / "config"
    else:
        cfg_dir = Path.home() / "AppData" / "Local" / "VoxPilot" / "config"

    cfg_dir.mkdir(parents=True, exist_ok=True)
    return cfg_dir.resolve()


def get_updates_dir() -> Path:
    """
    Return persistent directory for downloaded VoxPilot installer updates and update state.
    Always stores update artifacts under %LOCALAPPDATA%\\VoxPilot\\updates
    (unless overridden via VOXPILOT_UPDATES_DIR or VOXPILOT_USER_DATA_DIR).
    """
    custom = os.environ.get("VOXPILOT_UPDATES_DIR", "").strip()
    if custom:
        p = Path(custom).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    custom_user_data = os.environ.get("VOXPILOT_USER_DATA_DIR", "").strip()
    if custom_user_data:
        upd_dir = Path(custom_user_data).resolve() / "updates"
        upd_dir.mkdir(parents=True, exist_ok=True)
        return upd_dir

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        upd_dir = Path(local_app_data) / "VoxPilot" / "updates"
    else:
        upd_dir = Path.home() / "AppData" / "Local" / "VoxPilot" / "updates"

    upd_dir.mkdir(parents=True, exist_ok=True)
    return upd_dir.resolve()



def get_voice_temp_dir() -> Path:
    """Return directory for temporary generated TTS audio files."""
    if is_frozen() or os.environ.get("VOXPILOT_FORCE_USER_DATA", "").strip().lower() in ("1", "true", "yes"):
        temp_dir = get_user_data_dir() / "temp" / "voice"
    else:
        temp_dir = get_project_root() / "models" / "piper" / "generated"

    temp_dir.mkdir(parents=True, exist_ok=True)
    return temp_dir.resolve()


def get_frontend_dist_dir() -> Path:
    """Return the frontend production build directory (read-only bundled asset)."""
    custom = os.environ.get("VOXPILOT_FRONTEND_DIST", "").strip()
    if custom:
        return Path(custom).resolve()

    # Search candidates in bundle dir, executable dir, or project root
    candidates: List[Path] = []
    if is_frozen():
        candidates.append(get_bundle_dir() / "frontend" / "dist")
        candidates.append(get_executable_dir() / "frontend" / "dist")
        candidates.append(get_bundle_dir() / "dist")
        candidates.append(get_executable_dir() / "dist")

    candidates.append(get_project_root() / "frontend" / "dist")

    for c in candidates:
        if c.exists() and (c / "index.html").exists():
            return c.resolve()

    # Return primary candidate even if missing
    return candidates[0].resolve()


def get_piper_model_path() -> Path:
    """Return path to the bundled or configured Piper ONNX voice model."""
    custom = os.environ.get("VOXPILOT_PIPER_MODEL", "").strip()
    if custom:
        return Path(custom).resolve()

    candidates: List[Path] = []
    if is_frozen():
        candidates.append(get_bundle_dir() / "models" / "piper" / "en_US-lessac-medium.onnx")
        candidates.append(get_executable_dir() / "models" / "piper" / "en_US-lessac-medium.onnx")

    candidates.append(get_project_root() / "models" / "piper" / "en_US-lessac-medium.onnx")

    for c in candidates:
        if c.exists():
            return c.resolve()

    return candidates[0].resolve()


def get_app_icon_path() -> Path:
    """Return path to the bundled or repository VoxPilot application icon (.ico)."""
    candidates: List[Path] = []
    if is_frozen():
        candidates.append(get_bundle_dir() / "assets" / "icons" / "voxpilot.ico")
        candidates.append(get_executable_dir() / "assets" / "icons" / "voxpilot.ico")
        candidates.append(get_bundle_dir() / "frontend" / "dist" / "favicon.ico")
    candidates.append(get_project_root() / "assets" / "icons" / "voxpilot.ico")

    for c in candidates:
        if c.exists():
            return c.resolve()

    return candidates[0].resolve()


def ensure_user_directories() -> None:
    """Create all standard writable user data directories if they do not exist."""
    get_user_data_dir()
    (get_user_data_dir() / "data").mkdir(parents=True, exist_ok=True)
    (get_user_data_dir() / "logs").mkdir(parents=True, exist_ok=True)
    (get_user_data_dir() / "exports").mkdir(parents=True, exist_ok=True)
    (get_user_data_dir() / "screenshots").mkdir(parents=True, exist_ok=True)
    (get_user_data_dir() / "downloads").mkdir(parents=True, exist_ok=True)
    (get_user_data_dir() / "config").mkdir(parents=True, exist_ok=True)
    (get_user_data_dir() / "updates").mkdir(parents=True, exist_ok=True)
    (get_user_data_dir() / "temp" / "voice").mkdir(parents=True, exist_ok=True)
    get_updates_dir()


