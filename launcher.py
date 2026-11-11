"""
VoxPilot - Standalone Windows Launcher Entrypoint
Initializes logging, performs pre-flight dependency checks, verifies data persistence paths,
starts the Uvicorn ASGI server with the FastAPI backend, and serves the bundled React frontend.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

# Add project root to sys.path if not present
_current_dir = Path(__file__).resolve().parent
if str(_current_dir) not in sys.path:
    sys.path.insert(0, str(_current_dir))

from backend.paths import (
    is_frozen,
    get_bundle_dir,
    get_executable_dir,
    get_user_data_dir,
    get_database_path,
    get_export_dir,
    get_log_dir,
    get_frontend_dist_dir,
    get_piper_model_path,
    get_app_icon_path,
    ensure_user_directories,
)
from backend.version import get_app_version


def configure_windows_app_branding() -> None:
    """Configure Windows AppUserModelID and console window icon on Windows 11."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        # Set explicit AppUserModelID so Windows 11 taskbar groups VoxPilot under its own icon
        app_id = f"VoxPilotTeam.VoxPilot.DesktopAgent.{get_app_version()}"
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)

        # Set console window title and small/big icons (WM_SETICON) if a console window is attached
        ctypes.windll.kernel32.SetConsoleTitleW("VoxPilot — Personal AI Desktop Control Agent")
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        icon_path = get_app_icon_path()
        if hwnd and icon_path.exists():
            IMAGE_ICON = 1
            LR_LOADFROMFILE = 0x00000010
            LR_DEFAULTSIZE = 0x00000040
            WM_SETICON = 0x0080
            ICON_SMALL = 0
            ICON_BIG = 1

            hicon_small = ctypes.windll.user32.LoadImageW(
                None, str(icon_path), IMAGE_ICON, 16, 16, LR_LOADFROMFILE
            )
            hicon_big = ctypes.windll.user32.LoadImageW(
                None, str(icon_path), IMAGE_ICON, 32, 32, LR_LOADFROMFILE | LR_DEFAULTSIZE
            )
            if hicon_small:
                ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon_small)
            if hicon_big:
                ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon_big)
    except Exception:
        pass


def setup_application_logging() -> logging.Logger:
    """Initialize dual console and file logging in user data directory."""
    ensure_user_directories()
    log_dir = get_log_dir()
    log_file = log_dir / "voxpilot.log"

    logger = logging.getLogger("voxpilot")
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler
    try:
        file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except Exception as err:
        print(f"[VoxPilot] Warning: Could not initialize log file at {log_file}: {err}")

    return logger


def check_ollama_service(logger: logging.Logger) -> bool:
    """Check whether local Ollama service is reachable and whether the target model is present."""
    ollama_url = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    tags_url = f"{ollama_url}/api/tags"
    target_model = os.environ.get("VOXPILOT_OLLAMA_MODEL", "qwen3:4b")
    try:
        req = urllib.request.Request(tags_url, headers={"User-Agent": f"VoxPilot/{get_app_version()}"})
        with urllib.request.urlopen(req, timeout=0.8) as resp:
            if resp.status == 200:
                logger.info(f"[Ollama Check] Connected successfully to local Ollama service at {ollama_url}")
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("name", "") for m in data.get("models", [])]
                has_model = any(target_model in m for m in models)
                if has_model:
                    logger.info(f"[Ollama Check] Required model '{target_model}' found in local library.")
                else:
                    logger.warning(
                        f"[Ollama Check] Notice: Connected to Ollama, but model '{target_model}' was not found.\n"
                        f"                 Run 'ollama pull {target_model}' to download the required planning model."
                    )
                return True
    except Exception:
        pass

    logger.warning(
        f"[Ollama Check] Notice: Local Ollama service is not reachable at {ollama_url}.\n"
        f"                 LLM agent planning requires Ollama running with model '{target_model}'.\n"
        f"                 Please ensure Ollama is installed from https://ollama.com and run 'ollama serve'."
    )
    return False


def check_piper_voice_model(logger: logging.Logger) -> bool:
    """Verify bundled or configured Piper TTS voice model existence."""
    model_path = get_piper_model_path()
    if model_path.exists():
        logger.info(f"[Piper TTS] Voice model found at: {model_path}")
        return True

    logger.warning(
        f"[Piper TTS] Notice: Voice model not found at: {model_path}.\n"
        f"                 Text-to-speech synthesis will be disabled until model is placed in models/piper/."
    )
    return False


def check_faster_whisper_model(logger: logging.Logger) -> bool:
    """Check whether Faster-Whisper base speech model is cached locally."""
    hf_hub = Path(os.environ.get("USERPROFILE", "")) / ".cache" / "huggingface" / "hub"
    matches = list(hf_hub.glob("models--Systran--faster-whisper-base")) if hf_hub.exists() else []
    if matches:
        logger.info(f"[Faster-Whisper] Base speech model found in local cache: {matches[0].name}")
        return True

    logger.info(
        "[Faster-Whisper] Notice: Faster-Whisper base model (~140 MB) is not yet cached.\n"
        "                It will download automatically from Hugging Face on the first voice command."
    )
    return False


def check_playwright_chromium(logger: logging.Logger) -> bool:
    """Check whether Chromium browser executable is available for Playwright."""
    import glob

    # Ensure PLAYWRIGHT_BROWSERS_PATH defaults to local app data
    default_browsers = os.path.expandvars(r"%LOCALAPPDATA%\ms-playwright")
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", default_browsers)

    candidates = [
        default_browsers,
        str(get_bundle_dir() / "playwright" / "driver" / "package" / ".local-browsers"),
        str(get_executable_dir() / "playwright" / "driver" / "package" / ".local-browsers"),
    ]

    for c in candidates:
        matches = glob.glob(os.path.join(c, "chromium-*", "chrome-win64", "chrome.exe"))
        if matches:
            exe_path = matches[0]
            logger.info(f"[Playwright] Chromium browser located at: {exe_path}")
            return True

    logger.warning(
        f"[Playwright] Notice: Chromium browser executable not found in {default_browsers}.\n"
        f"              Run 'VoxPilot.exe --install-browser' or double-click 'install_browser.bat'\n"
        f"              to install the browser automatically without requiring Python."
    )
    return False


def install_playwright_chromium(logger: logging.Logger) -> int:
    """Run bundled Node driver to install Playwright Chromium without requiring Python."""
    import subprocess

    node_exe = get_bundle_dir() / "playwright" / "driver" / "node.exe"
    cli_js = get_bundle_dir() / "playwright" / "driver" / "package" / "cli.js"
    if not node_exe.exists() or not cli_js.exists():
        logger.error(f"[Playwright] Error: Bundled driver components not found at {node_exe}")
        return 1

    default_browsers = os.path.expandvars(r"%LOCALAPPDATA%\ms-playwright")
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = default_browsers
    print(f"Installing Playwright Chromium browser into: {default_browsers}...")
    logger.info(f"Installing Playwright Chromium into {default_browsers}...")
    try:
        res = subprocess.run([str(node_exe), str(cli_js), "install", "chromium"])
        if res.returncode == 0:
            print("Chromium browser successfully installed!")
            logger.info("Chromium browser successfully installed!")
        else:
            print(f"Chromium installation failed with exit code {res.returncode}")
            logger.error(f"Chromium installation failed with exit code {res.returncode}")
        return res.returncode
    except Exception as err:
        print(f"Error executing browser installer: {err}")
        logger.error(f"Error executing browser installer: {err}")
        return 1


def is_voxpilot_already_running(host: str, port: int) -> bool:
    """Check if another VoxPilot instance is already running on the target port."""
    try:
        req = urllib.request.Request(f"http://{host}:{port}/health")
        with urllib.request.urlopen(req, timeout=0.8) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("service") == "VoxPilot"
    except Exception:
        pass
    return False


def print_startup_banner(logger: logging.Logger, host: str, port: int) -> None:
    """Print clean diagnostic startup information."""
    banner = f"""
================================================================================
  VoxPilot - Personal AI Desktop Control Agent
  Version:     {get_app_version()}
  Mode:        {'Frozen (PyInstaller Standalone Distribution)' if is_frozen() else 'Development (.venv)'}
  Server:      http://{host}:{port}
  User Data:   {get_user_data_dir()}
  Database:    {get_database_path()}
  Frontend:    {get_frontend_dist_dir()}
  Hotkeys:     Ctrl + Alt + Escape (Global Emergency Stop)
               Ctrl + Shift + V    (Global Voice Mode Toggle - Customizable)
================================================================================
"""
    print(banner)
    logger.info(f"VoxPilot daemon initializing on http://{host}:{port}")


def open_browser_when_ready(host: str, port: int, delay_s: float = 1.5) -> None:
    """Open default web browser to the VoxPilot dashboard once server starts."""
    def _open():
        time.sleep(delay_s)
        url = f"http://{host}:{port}/"
        try:
            req = urllib.request.Request(f"http://{host}:{port}/health")
            with urllib.request.urlopen(req, timeout=3):
                pass
            webbrowser.open(url)
        except Exception:
            pass

    t = threading.Thread(target=_open, daemon=True)
    t.start()


def main():
    """Main launcher entrypoint."""
    configure_windows_app_branding()
    logger = setup_application_logging()

    # Handle dedicated setup or updater commands
    if "--run-updater" in sys.argv:
        from backend.updater.updater_runner import main as run_updater_main
        idx = sys.argv.index("--run-updater")
        sys.exit(run_updater_main(sys.argv[idx + 1:]))

    if "--install-browser" in sys.argv or "--setup-browser" in sys.argv:
        sys.exit(install_playwright_chromium(logger))

    host = os.environ.get("VOXPILOT_HOST", "127.0.0.1")
    port = int(os.environ.get("VOXPILOT_PORT", "8000"))

    # Single-instance check: If another instance of VoxPilot is already running, open browser and exit cleanly
    if is_voxpilot_already_running(host, port):
        logger.info(f"VoxPilot is already running on http://{host}:{port}/. Focusing existing instance...")
        print(f"VoxPilot is already running on http://{host}:{port}/. Opening dashboard in browser...")
        auto_open = os.environ.get("VOXPILOT_AUTO_OPEN_BROWSER", "1").strip().lower()
        if auto_open not in ("0", "false", "no", "off"):
            webbrowser.open(f"http://{host}:{port}/")
        return

    print_startup_banner(logger, host, port)

    # Dependency pre-flight checks
    check_ollama_service(logger)
    check_piper_voice_model(logger)
    check_faster_whisper_model(logger)
    check_playwright_chromium(logger)

    # Automatically launch dashboard in browser if requested
    auto_open = os.environ.get("VOXPILOT_AUTO_OPEN_BROWSER", "1").strip().lower()
    if auto_open not in ("0", "false", "no", "off"):
        open_browser_when_ready(host, port)

    # Import FastAPI application
    try:
        from backend.main import app
    except Exception as err:
        logger.critical(f"Failed to load VoxPilot FastAPI application: {err}", exc_info=True)
        sys.exit(1)

    import uvicorn

    try:
        logger.info(f"Starting Uvicorn server on {host}:{port}...")
        uvicorn.run(app, host=host, port=port, log_level="info", reload=False)
    except KeyboardInterrupt:
        logger.info("Received KeyboardInterrupt. Shutting down VoxPilot...")
    except Exception as err:
        logger.error(f"Uvicorn runtime error: {err}", exc_info=True)
    finally:
        logger.info("VoxPilot shutdown complete.")


if __name__ == "__main__":
    main()
