"""
VoxPilot - Native Windows Desktop Shell Controller & Lifecycle Management
Hosts the React frontend and local FastAPI/Uvicorn backend inside a native
Windows desktop window via PyWebView (Microsoft Edge WebView2).
"""

from __future__ import annotations

import html
import io
import json
import logging
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from backend.desktop.floating_orb import FloatingOrbController
from backend.paths import get_app_icon_path, get_log_dir, get_user_data_dir, get_webview_storage_dir
from backend.version import get_app_version

logger = logging.getLogger("voxpilot.desktop.shell")

DEFAULT_MUTEX_NAME = r"Local\VoxPilot_Desktop_SingleInstance_Mutex"
CANONICAL_AUMID_PREFIX = "VoxPilotTeam.VoxPilot.DesktopAgent"
ALLOWED_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
ERROR_ALREADY_EXISTS = 183


def get_canonical_app_user_model_id() -> str:
    """Return the canonical Windows AppUserModelID aligned across shell, notifications, and installer."""
    return f"{CANONICAL_AUMID_PREFIX}.{get_app_version()}"


class NullTextStream(io.TextIOBase):
    """Safe writable no-op text stream for console-free (console=False) Windows builds."""

    encoding = "utf-8"
    errors = "replace"

    @property
    def closed(self) -> bool:
        return False

    def write(self, s: str) -> int:
        return len(s) if isinstance(s, str) else 0

    def writelines(self, lines: Iterable[str]) -> None:
        for _ in lines:
            pass

    def flush(self) -> None:
        return None

    def isatty(self) -> bool:
        return False

    def readable(self) -> bool:
        return False

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False


def ensure_safe_standard_streams() -> None:
    """Ensure sys.stdout and sys.stderr are valid writable streams in console=False builds."""
    if sys.stdout is None:
        sys.stdout = NullTextStream()
    if sys.stderr is None:
        sys.stderr = NullTextStream()


def validate_loopback_host(host: str) -> str:
    """Validate that a server bind host is strictly local loopback."""
    if not isinstance(host, str):
        raise ValueError("Host must be a loopback address string.")
    cleaned = host.strip()
    if cleaned.startswith("[") and cleaned.endswith("]"):
        cleaned = cleaned[1:-1].strip()
    normalized = cleaned.lower()
    if normalized not in ALLOWED_LOOPBACK_HOSTS:
        raise ValueError(
            f"Refusing non-loopback bind host '{host}'. VoxPilot desktop backend must bind only to loopback."
        )
    return "localhost" if normalized == "localhost" else normalized


def configure_windows_desktop_process() -> None:
    """Configure Windows 11 AppUserModelID and Per-Monitor DPI awareness for desktop shell."""
    if sys.platform != "win32":
        return

    try:
        import ctypes

        app_id = get_canonical_app_user_model_id()
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        except Exception:
            pass

        try:
            from backend.automation.notifications import ensure_windows_aumid_registered

            ensure_windows_aumid_registered(app_id)
        except Exception:
            pass

        # Enable Per-Monitor v2 DPI awareness before creating WebView2 window
        try:
            # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
            res = ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
            if res:
                return
        except Exception:
            pass

        try:
            # PROCESS_PER_MONITOR_DPI_AWARE = 2
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
            return
        except Exception:
            pass

        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    except Exception:
        pass


class SingleInstanceGuard:
    """Per-user Windows Named Mutex single-instance lock for VoxPilot desktop shell."""

    def __init__(
        self,
        mutex_name: str = DEFAULT_MUTEX_NAME,
        auto_acquire: bool = True,
    ) -> None:
        self.mutex_name = mutex_name
        self.acquired: bool = False
        self._handle: Optional[int] = None
        self._attempted: bool = False
        self._kernel32: Any = None
        if auto_acquire:
            self.acquire()

    def acquire(self) -> bool:
        """Attempt to acquire the single-instance mutex."""
        if self.acquired:
            return True

        self._attempted = True
        if sys.platform != "win32":
            self.acquired = True
            return True

        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.argtypes = [
                wintypes.LPVOID,
                wintypes.BOOL,
                wintypes.LPCWSTR,
            ]
            kernel32.CreateMutexW.restype = wintypes.HANDLE
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            self._kernel32 = kernel32

            ctypes.set_last_error(0)
            handle = kernel32.CreateMutexW(None, False, self.mutex_name)
            last_error = ctypes.get_last_error()

            if not handle:
                self.acquired = False
                self._handle = None
                return False

            if last_error == ERROR_ALREADY_EXISTS:
                kernel32.CloseHandle(handle)
                self.acquired = False
                self._handle = None
                return False

            self._handle = int(handle)
            self.acquired = True
            return True
        except Exception as exc:
            logger.warning("Failed to acquire single-instance mutex '%s': %s", self.mutex_name, exc)
            self.acquired = False
            self._handle = None
            return False

    def release(self) -> None:
        """Release the acquired Windows mutex handle."""
        if self._handle is not None and sys.platform == "win32":
            try:
                if self._kernel32 is not None:
                    self._kernel32.CloseHandle(self._handle)
                else:
                    import ctypes
                    ctypes.windll.kernel32.CloseHandle(self._handle)
            except Exception:
                pass
        self._handle = None
        self.acquired = False

    def __enter__(self) -> "SingleInstanceGuard":
        if not self._attempted:
            self.acquire()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.release()


def _find_windows_by_title(window_title: str) -> List[int]:
    """Find top-level non-console HWNDs matching the VoxPilot desktop window title."""
    if sys.platform != "win32":
        return []

    matches: List[int] = []
    exact_matches: List[int] = []
    prefix_dash_em = f"{window_title} —"
    prefix_dash_ascii = f"{window_title} -"

    try:
        import win32gui  # type: ignore

        def _enum_cb(hwnd: int, _: Any) -> bool:
            try:
                if not win32gui.IsWindow(hwnd):
                    return True
                cls_name = win32gui.GetClassName(hwnd) or ""
                if cls_name == "ConsoleWindowClass":
                    return True
                title = win32gui.GetWindowText(hwnd) or ""
                if title == window_title:
                    exact_matches.append(hwnd)
                elif title.startswith(prefix_dash_em) or title.startswith(prefix_dash_ascii):
                    matches.append(hwnd)
            except Exception:
                pass
            return True

        win32gui.EnumWindows(_enum_cb, 0)
        return exact_matches + matches
    except Exception:
        pass

    try:
        import ctypes

        hwnd = ctypes.windll.user32.FindWindowW(None, window_title)
        if hwnd:
            return [int(hwnd)]
    except Exception:
        pass

    return []


def focus_window_by_title(window_title: str = "VoxPilot") -> bool:
    """Restore (if minimized) and bring an existing Windows top-level window to foreground."""
    if sys.platform != "win32":
        return False

    hwnds = _find_windows_by_title(window_title)
    if not hwnds:
        return False

    try:
        import ctypes

        user32 = ctypes.windll.user32
        SW_SHOW = 5
        SW_RESTORE = 9
        VK_MENU = 0x12
        KEYEVENTF_KEYUP = 0x0002

        hwnd = hwnds[0]
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        else:
            user32.ShowWindow(hwnd, SW_SHOW)

        # Standard Windows foreground activation unlock sequence + AttachThreadInput
        try:
            user32.keybd_event(VK_MENU, 0, 0, 0)
            user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        except Exception:
            pass

        fg_hwnd = int(user32.GetForegroundWindow() or 0)
        fg_tid = int(user32.GetWindowThreadProcessId(fg_hwnd, None) or 0) if fg_hwnd else 0
        target_tid = int(user32.GetWindowThreadProcessId(hwnd, None) or 0)
        attached = False
        try:
            if fg_tid and target_tid and fg_tid != target_tid:
                attached = bool(user32.AttachThreadInput(fg_tid, target_tid, True))
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                try:
                    user32.AttachThreadInput(fg_tid, target_tid, False)
                except Exception:
                    pass
        return True
    except Exception as exc:
        logger.debug("Win32 foreground activation failed for '%s': %s", window_title, exc)
        return False


def is_native_window_minimized(
    window_title: str = "VoxPilot",
    hwnd_hint: Optional[int] = None,
) -> Optional[bool]:
    """Query Win32 iconic/hidden state on demand without polling or timers.

    Returns ``None`` when running off Windows or when no native window handle exists.
    """
    if sys.platform != "win32":
        return None

    try:
        import ctypes

        user32 = ctypes.windll.user32
        target_hwnd = 0
        if hwnd_hint and user32.IsWindow(hwnd_hint):
            target_hwnd = int(hwnd_hint)
        else:
            hwnds = _find_windows_by_title(window_title)
            if hwnds:
                target_hwnd = int(hwnds[0])
            else:
                found = user32.FindWindowW(None, window_title)
                if found:
                    target_hwnd = int(found)

        if not target_hwnd:
            return None

        return bool(user32.IsIconic(target_hwnd)) or (not bool(user32.IsWindowVisible(target_hwnd)))
    except Exception:
        return None


def is_native_window_foreground(
    window_title: str = "VoxPilot",
    hwnd_hint: Optional[int] = None,
) -> Optional[bool]:
    """Query Win32 foreground/iconic status on demand without polling or timers.

    Returns ``None`` when running off Windows or when no native window handle exists.
    """
    if sys.platform != "win32":
        return None

    try:
        import ctypes

        user32 = ctypes.windll.user32
        target_hwnd = 0
        if hwnd_hint and user32.IsWindow(hwnd_hint):
            target_hwnd = int(hwnd_hint)
        else:
            hwnds = _find_windows_by_title(window_title)
            if hwnds:
                target_hwnd = int(hwnds[0])
            else:
                found = user32.FindWindowW(None, window_title)
                if found:
                    target_hwnd = int(found)

        if not target_hwnd:
            return None

        if user32.IsIconic(target_hwnd) or not user32.IsWindowVisible(target_hwnd):
            return False

        fg_hwnd = int(user32.GetForegroundWindow() or 0)
        if not fg_hwnd:
            return None

        if fg_hwnd == target_hwnd:
            return True

        GA_ROOT = 2
        if int(user32.GetAncestor(fg_hwnd, GA_ROOT) or 0) == target_hwnd:
            return True

        return bool(user32.IsChild(target_hwnd, fg_hwnd))
    except Exception:
        return None


def apply_window_icon(window_title: str = "VoxPilot", icon_path: Optional[Path] = None) -> bool:
    """Apply the VoxPilot .ico icon onto the native top-level desktop window via WM_SETICON."""
    if sys.platform != "win32":
        return False

    target_icon = icon_path or get_app_icon_path()
    if not target_icon or not target_icon.is_file():
        return False

    hwnds = _find_windows_by_title(window_title)
    if not hwnds:
        return False

    try:
        import ctypes

        user32 = ctypes.windll.user32
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x00000010
        LR_DEFAULTSIZE = 0x00000040
        WM_SETICON = 0x0080
        ICON_SMALL = 0
        ICON_BIG = 1

        hicon_small = user32.LoadImageW(
            None, str(target_icon), IMAGE_ICON, 16, 16, LR_LOADFROMFILE
        )
        hicon_big = user32.LoadImageW(
            None, str(target_icon), IMAGE_ICON, 32, 32, LR_LOADFROMFILE | LR_DEFAULTSIZE
        )

        applied = False
        for hwnd in hwnds:
            if hicon_small:
                user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon_small)
                applied = True
            if hicon_big:
                user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon_big)
                applied = True
        return applied
    except Exception as exc:
        logger.debug("Failed to apply WM_SETICON to desktop window: %s", exc)
        return False


def focus_existing_voxpilot_window(
    host: str = "127.0.0.1",
    port: int = 8000,
    window_title: str = "VoxPilot",
) -> bool:
    """Focus an already-running VoxPilot desktop instance via API IPC and Win32 activation."""
    safe_host = validate_loopback_host(host)
    formatted_host = f"[{safe_host}]" if ":" in safe_host else safe_host
    base_url = f"http://{formatted_host}:{port}"
    api_focused = False

    try:
        req = urllib.request.Request(
            f"{base_url}/api/system/desktop/focus",
            data=b"{}",
            headers={
                "Content-Type": "application/json",
                "Origin": base_url,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=1.2) as resp:
            if resp.status == 200:
                payload = json.loads(resp.read().decode("utf-8"))
                api_focused = bool(payload.get("focused", True))
    except Exception as exc:
        logger.debug("Desktop focus IPC request failed (%s); falling back to Win32 title search.", exc)

    win_focused = focus_window_by_title(window_title)
    return api_focused or win_focused


class ManagedUvicornServer:
    """Runs FastAPI via uvicorn.Server in a controlled background thread with clean shutdown."""

    def __init__(
        self,
        app: Any,
        host: str = "127.0.0.1",
        port: int = 8000,
        startup_timeout: float = 30.0,
    ) -> None:
        self.app = app
        self.host = validate_loopback_host(host)
        self.port = int(port)
        self.startup_timeout = float(startup_timeout)
        self.server: Any = None
        self._thread: Optional[threading.Thread] = None
        self._stop_lock = threading.Lock()
        self._stopped = False
        self.startup_error: Optional[BaseException] = None

    @property
    def base_url(self) -> str:
        formatted_host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{formatted_host}:{self.port}"

    def _run(self) -> None:
        try:
            if self.server is not None:
                self.server.run()
        except BaseException as exc:
            self.startup_error = exc
            logger.error("Managed Uvicorn server exited with error: %s", exc, exc_info=True)

    def start(self) -> None:
        """Start the in-process Uvicorn server thread on loopback."""
        if self._thread is not None and self._thread.is_alive():
            return

        ensure_safe_standard_streams()
        import uvicorn

        config = uvicorn.Config(
            app=self.app,
            host=self.host,
            port=self.port,
            log_level="info",
            log_config=None,
            access_log=False,
        )
        server = uvicorn.Server(config)
        # Signal handlers can only be registered on the main thread
        server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
        self.server = server
        self._stopped = False
        self.startup_error = None

        self._thread = threading.Thread(
            target=self._run,
            name="VoxPilotUvicornThread",
            daemon=False,
        )
        self._thread.start()

    def wait_until_ready(self, timeout: Optional[float] = None) -> bool:
        """Poll local /health until FastAPI reports healthy or startup fails."""
        max_wait = self.startup_timeout if timeout is None else float(timeout)
        deadline = time.monotonic() + max_wait
        health_url = f"{self.base_url}/health"

        while time.monotonic() < deadline:
            if self.startup_error is not None:
                return False
            if self._thread is not None and not self._thread.is_alive() and self.server is not None:
                if not getattr(self.server, "started", False):
                    return False

            try:
                req = urllib.request.Request(health_url, method="GET")
                with urllib.request.urlopen(req, timeout=0.8) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode("utf-8"))
                        if data.get("service") == "VoxPilot":
                            return True
            except Exception:
                pass

            time.sleep(0.15)

        return False

    def stop(self, timeout: float = 10.0) -> bool:
        """Trigger FastAPI lifespan shutdown and join the Uvicorn thread."""
        with self._stop_lock:
            if self.server is not None:
                self.server.should_exit = True
                self.server.force_exit = False

            thread = self._thread
            if thread is not None and thread.is_alive():
                thread.join(timeout=timeout)
                clean_exit = not thread.is_alive()
            else:
                clean_exit = True

            self._stopped = True
            return clean_exit


def build_startup_loading_html(target_url: str, version: str) -> str:
    """Build the instant inline splash HTML displayed while the FastAPI backend boots."""
    safe_version = html.escape(version)
    safe_target_url = target_url.rstrip("/")
    health_endpoint = html.escape(f"{safe_target_url}/health", quote=True)
    app_url_js = json.dumps(f"{safe_target_url}/")
    health_url_js = json.dumps(f"{safe_target_url}/health")

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>VoxPilot — Starting</title>
  <style>
    :root {{
      color-scheme: dark;
    }}
    * {{
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }}
    body {{
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      background: radial-gradient(circle at top, #172033 0%, #0b0f17 65%);
      color: #f1f5f9;
      font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
      user-select: none;
    }}
    .splash {{
      width: 100%;
      max-width: 440px;
      padding: 36px 32px;
      border-radius: 18px;
      background: rgba(17, 24, 39, 0.85);
      border: 1px solid rgba(148, 163, 184, 0.16);
      box-shadow: 0 24px 60px rgba(0, 0, 0, 0.45);
      text-align: center;
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 6px 14px;
      border-radius: 999px;
      background: rgba(59, 130, 246, 0.14);
      border: 1px solid rgba(59, 130, 246, 0.35);
      color: #93c5fd;
      font-size: 12px;
      font-weight: 600;
      letter-spacing: 0.04em;
      margin-bottom: 18px;
    }}
    h1 {{
      font-size: 26px;
      font-weight: 700;
      letter-spacing: -0.02em;
      margin-bottom: 8px;
    }}
    .subtitle {{
      color: #94a3b8;
      font-size: 14px;
      line-height: 1.5;
      margin-bottom: 26px;
    }}
    .progress-track {{
      width: 100%;
      height: 5px;
      border-radius: 999px;
      background: rgba(148, 163, 184, 0.15);
      overflow: hidden;
      margin-bottom: 14px;
    }}
    .progress-bar {{
      width: 42%;
      height: 100%;
      border-radius: 999px;
      background: linear-gradient(90deg, #3b82f6, #60a5fa);
      animation: slide 1.3s ease-in-out infinite;
    }}
    @keyframes slide {{
      0% {{ transform: translateX(-110%); }}
      100% {{ transform: translateX(260%); }}
    }}
    .status {{
      color: #cbd5e1;
      font-size: 13px;
    }}
  </style>
</head>
<body>
  <div class="splash" data-health-url="{health_endpoint}">
    <div class="badge">VoxPilot Desktop &middot; v{safe_version}</div>
    <h1>Initializing VoxPilot</h1>
    <p class="subtitle">
      Starting your local AI desktop assistant, database, and voice services...
    </p>
    <div class="progress-track">
      <div class="progress-bar"></div>
    </div>
    <p id="startup-status" class="status">Connecting to local backend...</p>
  </div>
  <script>
    (function () {{
      var appUrl = {app_url_js};
      var healthUrl = {health_url_js};
      var redirected = false;
      function poll() {{
        if (redirected) return;
        fetch(healthUrl, {{ cache: "no-store" }})
          .then(function (resp) {{
            if (resp.ok) {{
              redirected = true;
              window.location.replace(appUrl);
            }} else {{
              setTimeout(poll, 250);
            }}
          }})
          .catch(function () {{
            setTimeout(poll, 250);
          }});
      }}
      setTimeout(poll, 250);
    }})();
  </script>
</body>
</html>
"""


def build_startup_error_html(error_message: str, log_path: str) -> str:
    """Build a clear dark-themed startup diagnostic page if the local backend fails to start."""
    safe_error = html.escape(error_message)
    safe_log = html.escape(log_path)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>VoxPilot — Startup Error</title>
  <style>
    :root {{ color-scheme: dark; }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 24px;
      background: #0b0f17;
      color: #f1f5f9;
      font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
    }}
    .card {{
      width: 100%;
      max-width: 560px;
      padding: 32px;
      border-radius: 16px;
      background: #111827;
      border: 1px solid rgba(239, 68, 68, 0.35);
      box-shadow: 0 24px 60px rgba(0, 0, 0, 0.45);
    }}
    .tag {{
      display: inline-block;
      padding: 4px 10px;
      border-radius: 6px;
      background: rgba(239, 68, 68, 0.16);
      color: #fca5a5;
      font-size: 12px;
      font-weight: 600;
      margin-bottom: 14px;
    }}
    h1 {{ font-size: 22px; margin-bottom: 10px; }}
    p {{ color: #cbd5e1; font-size: 14px; line-height: 1.55; margin-bottom: 14px; }}
    pre {{
      background: #090d14;
      border: 1px solid rgba(148, 163, 184, 0.18);
      border-radius: 10px;
      padding: 12px 14px;
      color: #fda4af;
      font-size: 12px;
      white-space: pre-wrap;
      word-break: break-word;
      margin-bottom: 16px;
    }}
    .path {{
      background: rgba(148, 163, 184, 0.1);
      padding: 10px 12px;
      border-radius: 8px;
      font-family: Consolas, monospace;
      font-size: 12px;
      color: #93c5fd;
      word-break: break-all;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="tag">Startup Diagnostic</div>
    <h1>VoxPilot Could Not Start Local Backend</h1>
    <p>
      The local VoxPilot backend server failed to finish initializing inside the desktop shell.
      Close this window and check your log file for details.
    </p>
    <pre>{safe_error}</pre>
    <p>Diagnostic Log File:</p>
    <div class="path">{safe_log}</div>
  </div>
</body>
</html>
"""


class DesktopShellController:
    """Coordinates PyWebView desktop window, local Uvicorn backend, and graceful shutdown."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8000,
        title: str = "VoxPilot",
        width: int = 1280,
        height: int = 820,
        min_size: Tuple[int, int] = (1024, 680),
        startup_timeout: float = 30.0,
        require_session_token: bool = True,
    ) -> None:
        self.host = validate_loopback_host(host)
        self.port = int(port)
        self.title = title
        self.width = int(width)
        self.height = int(height)
        self.min_size = min_size
        self.startup_timeout = float(startup_timeout)
        self.require_session_token = bool(require_session_token)
        self.session_token: str = secrets.token_urlsafe(32)

        self.server: Optional[ManagedUvicornServer] = None
        self._window: Any = None
        self._closing_event = threading.Event()
        self._stopped_event = threading.Event()
        self._window_active = False
        self._window_focused = False
        self._window_minimized = False
        self._hwnd: Optional[int] = None
        self._ready_thread: Optional[threading.Thread] = None
        self._orb_controller = FloatingOrbController()

    @property
    def orb_controller(self) -> FloatingOrbController:
        return self._orb_controller

    @property
    def base_url(self) -> str:
        formatted_host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{formatted_host}:{self.port}"

    @property
    def is_window_active(self) -> bool:
        return self._window_active and not self._closing_event.is_set()

    @property
    def is_window_minimized(self) -> bool:
        """Return True when the native VoxPilot window is minimized/hidden."""
        if self._closing_event.is_set() or not self._window_active:
            return False
        native_min = is_native_window_minimized(self.title, hwnd_hint=self._hwnd)
        if native_min is not None:
            self._window_minimized = native_min
            if native_min and self._window_focused:
                self._window_focused = False
                self._set_ui_activity(False)
            return native_min
        return bool(self._window_minimized)

    @property
    def is_window_focused(self) -> bool:
        """Return True when the native VoxPilot window is active, not minimized, and foreground."""
        if self._closing_event.is_set() or not self._window_active or self.is_window_minimized:
            return False
        if not self._window_focused:
            return False
        native_fg = is_native_window_foreground(self.title, hwnd_hint=self._hwnd)
        if native_fg is False:
            return False
        return True

    def set_focus_state(self, focused: bool, minimized: Optional[bool] = None) -> None:
        """Update window focus/minimized state and synchronize notification UI suppression."""
        if minimized is not None:
            self._window_minimized = bool(minimized)
        if self._closing_event.is_set() or self._window_minimized:
            self._window_focused = False
        else:
            self._window_focused = bool(focused)
        self._set_ui_activity(self._window_focused)

    def _set_ui_activity(self, active: bool) -> None:
        try:
            from backend.automation.notifications import get_shared_notification_service
            get_shared_notification_service().record_ui_activity(active)
        except Exception:
            pass

    def _on_window_shown(self, *args: Any, **kwargs: Any) -> None:
        self._window_active = True
        hwnds = _find_windows_by_title(self.title)
        if hwnds:
            self._hwnd = hwnds[0]
        self.set_focus_state(True, minimized=False)
        try:
            apply_window_icon(self.title, get_app_icon_path())
        except Exception:
            pass

    def _on_window_minimized(self, *args: Any, **kwargs: Any) -> None:
        self.set_focus_state(False, minimized=True)

    def _on_window_restored(self, *args: Any, **kwargs: Any) -> None:
        self.set_focus_state(True, minimized=False)

    def _on_window_loaded(self, *args: Any, **kwargs: Any) -> None:
        """Reinforce the desktop session token in memory after DOM load and trigger client scrub."""
        if self._window is None or not self.require_session_token:
            return
        try:
            import json

            script = (
                "try { "
                f"window.__VOXPILOT_DESKTOP_TOKEN__ = {json.dumps(self.session_token)}; "
                "window.dispatchEvent(new Event('voxpilot:token-ready')); "
                "} catch (e) {}"
            )
            self._window.evaluate_js(script)
        except Exception:
            pass

    def _on_orb_window_loaded(self, *args: Any, **kwargs: Any) -> None:
        """Inject the desktop session token into the floating orb WebView2 window."""
        if not self.require_session_token or not self._orb_controller.has_window:
            return
        try:
            script = (
                "try { "
                f"window.__VOXPILOT_DESKTOP_TOKEN__ = {json.dumps(self.session_token)}; "
                "window.dispatchEvent(new Event('voxpilot:token-ready')); "
                "} catch (e) {}"
            )
            win = getattr(self._orb_controller, "_window", None)
            if win is not None and hasattr(win, "evaluate_js"):
                win.evaluate_js(script)
        except Exception:
            pass

    def _on_window_closed(self, *args: Any, **kwargs: Any) -> None:
        self._closing_event.set()
        self._window_active = False
        self._window_focused = False
        self._window_minimized = False
        self._set_ui_activity(False)
        if self.server is not None:
            self.server.stop(timeout=10.0)
        self._stopped_event.set()

    def _transition_to_app_when_ready(self) -> None:
        if self.server is None or self._window is None:
            return

        ready = self.server.wait_until_ready(timeout=self.startup_timeout)
        if self._closing_event.is_set():
            return

        if ready:
            try:
                apply_window_icon(self.title, get_app_icon_path())
            except Exception:
                pass
            self._set_ui_activity(True)
            try:
                self._window.load_url(f"{self.base_url}/")
            except Exception as exc:
                logger.warning("Failed to navigate desktop window to %s: %s", self.base_url, exc)
            try:
                from backend.voice.voice_service import get_shared_voice_service

                vs = get_shared_voice_service()
                vs.register_orb_listener(self._orb_controller.sync_state)
                self._orb_controller.load_url(f"{self.base_url}/?orb=1")
                self._orb_controller.sync_state(vs.phase, vs.is_active, vs.settings_manager.get_settings())
            except Exception as orb_exc:
                logger.debug("Floating orb initialization warning: %s", orb_exc)
        else:
            err_detail = (
                str(self.server.startup_error)
                if self.server.startup_error is not None
                else f"Timed out after {self.startup_timeout:.0f}s waiting for {self.base_url}/health."
            )
            log_path = str(get_log_dir() / "voxpilot.log")
            logger.error("Desktop backend readiness failed: %s", err_detail)
            try:
                self._window.load_html(build_startup_error_html(err_detail, log_path))
            except Exception as exc:
                logger.warning("Failed to display startup error HTML in desktop window: %s", exc)

    def focus_window(self) -> bool:
        """Restore and bring the active VoxPilot desktop window to the foreground."""
        if self._closing_event.is_set():
            return False

        focused = False
        if self._window is not None:
            for action in ("restore", "show"):
                fn = getattr(self._window, action, None)
                if callable(fn):
                    try:
                        fn()
                        focused = True
                    except Exception:
                        pass

        win32_focused = focus_window_by_title(self.title)
        if focused or win32_focused:
            self.set_focus_state(True, minimized=False)
            return True
        return False

    def request_shutdown(self) -> None:
        """Initiate graceful close of the desktop window and in-process backend."""
        self._closing_event.set()
        self._window_active = False
        self._window_focused = False
        self._window_minimized = False
        self._set_ui_activity(False)
        if self._window is not None:
            destroy_fn = getattr(self._window, "destroy", None)
            if callable(destroy_fn):
                try:
                    destroy_fn()
                except Exception:
                    pass
        self._orb_controller.destroy()
        if self.server is not None:
            self.server.stop(timeout=10.0)
        self._stopped_event.set()

    def stop(self) -> None:
        """Ensure Uvicorn and desktop resources are stopped cleanly."""
        self._closing_event.set()
        self._window_active = False
        self._window_focused = False
        self._window_minimized = False
        self._set_ui_activity(False)
        self._orb_controller.destroy()
        if self.server is not None:
            self.server.stop(timeout=10.0)
        self._stopped_event.set()

    def get_status_dict(self) -> Dict[str, Any]:
        """Return diagnostic desktop shell runtime metadata."""
        import os

        return {
            "mode": "desktop_shell",
            "window_title": self.title,
            "window_active": self.is_window_active,
            "window_focused": self.is_window_focused,
            "window_minimized": self.is_window_minimized,
            "app_user_model_id": get_canonical_app_user_model_id(),
            "host": self.host,
            "port": self.port,
            "base_url": self.base_url,
            "pid": os.getpid(),
            "session_token_required": bool(self.require_session_token),
        }

    def run(self, app: Any) -> int:
        """Launch the Uvicorn backend and run the PyWebView EdgeChromium desktop loop."""
        ensure_safe_standard_streams()
        configure_windows_desktop_process()

        import webview

        self._closing_event.clear()
        self._stopped_event.clear()

        self.server = ManagedUvicornServer(
            app=app,
            host=self.host,
            port=self.port,
            startup_timeout=self.startup_timeout,
        )
        self.server.start()

        try:
            if hasattr(webview, "settings") and isinstance(webview.settings, dict):
                webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        except Exception:
            pass

        startup_html = build_startup_loading_html(self.base_url, get_app_version())
        self._window = webview.create_window(
            title=self.title,
            html=startup_html,
            js_api=None,
            width=self.width,
            height=self.height,
            min_size=self.min_size,
            resizable=True,
            background_color="#0B0F17",
            text_select=True,
        )
        self._window_active = True

        events = getattr(self._window, "events", None)
        if events is not None:
            for evt_name, handler in (
                ("shown", self._on_window_shown),
                ("loaded", self._on_window_loaded),
                ("minimized", self._on_window_minimized),
                ("restored", self._on_window_restored),
                ("maximized", self._on_window_restored),
                ("closing", self._on_window_closed),
                ("closed", self._on_window_closed),
            ):
                evt = getattr(events, evt_name, None)
                if evt is not None:
                    try:
                        evt += handler
                    except Exception:
                        pass

        self._ready_thread = threading.Thread(
            target=self._transition_to_app_when_ready,
            name="VoxPilotDesktopReadyThread",
            daemon=True,
        )
        self._ready_thread.start()

        try:
            from backend.voice.voice_service import get_shared_voice_service
            orb_settings = get_shared_voice_service().settings_manager.get_settings()
        except Exception:
            orb_settings = {}
        orb_win = self._orb_controller.create_orb_window(
            webview,
            "<!doctype html><html><body style='margin:0;background:transparent;overflow:hidden;'></body></html>",
            settings=orb_settings,
        )
        orb_events = getattr(orb_win, "events", None)
        if orb_events is not None and getattr(orb_events, "loaded", None) is not None:
            try:
                orb_events.loaded += self._on_orb_window_loaded
            except Exception:
                pass

        webview_storage_dir = get_webview_storage_dir()
        icon_path = get_app_icon_path()

        start_kwargs: Dict[str, Any] = {
            "gui": "edgechromium",
            "debug": False,
            "private_mode": False,
            "storage_path": str(webview_storage_dir),
        }
        if icon_path and icon_path.is_file():
            start_kwargs["icon"] = str(icon_path)

        try:
            webview.start(**start_kwargs)
        finally:
            self.stop()

        return 0


_shared_desktop_lock = threading.Lock()
_shared_desktop_controller: Optional[DesktopShellController] = None


def get_shared_desktop_controller() -> Optional[DesktopShellController]:
    """Return the active DesktopShellController if VoxPilot is running in desktop shell mode."""
    with _shared_desktop_lock:
        return _shared_desktop_controller


def set_shared_desktop_controller(controller: Optional[DesktopShellController]) -> None:
    """Register or clear the active DesktopShellController singleton."""
    global _shared_desktop_controller
    with _shared_desktop_lock:
        _shared_desktop_controller = controller
