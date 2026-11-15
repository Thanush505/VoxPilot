"""
VoxPilot - Stage B Native Windows Desktop Shell Tests
Verifies:
- Console-free standard stream safety (sys.stdout / sys.stderr is None)
- Strict loopback host validation
- Windows Named Mutex single-instance guard
- Managed in-process Uvicorn startup and FastAPI lifespan shutdown
- Desktop shell status, focus IPC, and Origin/session token security
- PyWebView DesktopShellController startup, readiness transition, focus, and shutdown
- Headless / server-only compatibility mode flags
"""

from __future__ import annotations

import json
import logging
import socket
import sys
import threading
import time
import types
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.desktop.shell import (
    DesktopShellController,
    ManagedUvicornServer,
    NullTextStream,
    SingleInstanceGuard,
    build_startup_error_html,
    build_startup_loading_html,
    ensure_safe_standard_streams,
    get_shared_desktop_controller,
    set_shared_desktop_controller,
    validate_loopback_host,
)
import launcher


def _get_free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_ensure_safe_standard_streams_when_none(monkeypatch, tmp_path):
    """Console-free (console=False) PyInstaller builds set stdout/stderr to None; ensure safe logging."""
    monkeypatch.setenv("VOXPILOT_USER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VOXPILOT_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    vp_logger = logging.getLogger("voxpilot")
    original_handlers = list(vp_logger.handlers)
    vp_logger.handlers.clear()

    try:
        ensure_safe_standard_streams()
        assert isinstance(sys.stdout, NullTextStream)
        assert isinstance(sys.stderr, NullTextStream)
        assert sys.stdout.isatty() is False

        # Must not raise in console=False mode
        print("VoxPilot console-free stdout check")
        configured_logger = launcher.setup_application_logging()
        configured_logger.info("Stage B safe stream log verification")
        assert (tmp_path / "logs" / "voxpilot.log").is_file()
    finally:
        for h in list(vp_logger.handlers):
            try:
                h.close()
            except Exception:
                pass
        vp_logger.handlers.clear()
        vp_logger.handlers.extend(original_handlers)


@pytest.mark.parametrize("host,expected", [
    ("127.0.0.1", "127.0.0.1"),
    ("localhost", "localhost"),
    ("LOCALHOST", "localhost"),
    ("::1", "::1"),
    ("[::1]", "::1"),
])
def test_validate_loopback_host_accepts_loopback(host: str, expected: str):
    assert validate_loopback_host(host) == expected


@pytest.mark.parametrize("bad_host", [
    "0.0.0.0",
    "192.168.1.10",
    "10.0.0.5",
    "example.com",
    "",
])
def test_validate_loopback_host_rejects_non_loopback(bad_host: str):
    with pytest.raises(ValueError):
        validate_loopback_host(bad_host)


def test_single_instance_mutex_exclusivity_and_release():
    """Windows Named Mutex prevents duplicate instances and releases cleanly."""
    mutex_name = f"Local\\VoxPilot_StageB_Test_{uuid.uuid4().hex}"

    g1 = SingleInstanceGuard(mutex_name=mutex_name)
    assert g1.acquired is True

    g2 = SingleInstanceGuard(mutex_name=mutex_name)
    if sys.platform == "win32":
        assert g2.acquired is False
    g2.release()

    g1.release()
    assert g1.acquired is False

    g3 = SingleInstanceGuard(mutex_name=mutex_name)
    try:
        assert g3.acquired is True
    finally:
        g3.release()


def test_managed_uvicorn_server_lifecycle_and_desktop_security(monkeypatch, tmp_path):
    """ManagedUvicornServer starts backend.main:app on loopback, serves desktop endpoints, and stops cleanly."""
    monkeypatch.setenv("VOXPILOT_USER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VOXPILOT_DB_PATH", str(tmp_path / "data" / "test_stage_b.db"))
    monkeypatch.setenv("VOXPILOT_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("VOXPILOT_ENABLE_HOTKEY", "0")
    monkeypatch.setenv("VOXPILOT_ENABLE_SCHEDULER", "0")

    from backend.main import app

    port = _get_free_loopback_port()
    controller = DesktopShellController(host="127.0.0.1", port=port)
    set_shared_desktop_controller(controller)
    server = ManagedUvicornServer(app=app, host="127.0.0.1", port=port, startup_timeout=20.0)

    try:
        server.start()
        assert server.wait_until_ready(timeout=20.0) is True

        # 1. Health endpoint
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3.0) as resp:
            assert resp.status == 200
            health = json.loads(resp.read().decode("utf-8"))
            assert health["service"] == "VoxPilot"

        # 2. Desktop status endpoint rejects missing or invalid session token in desktop mode
        no_token_req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/system/desktop/status",
            headers={"Origin": f"http://127.0.0.1:{port}", "Connection": "close"},
            method="GET",
        )
        with pytest.raises(urllib.error.HTTPError) as missing_exc:
            urllib.request.urlopen(no_token_req, timeout=3.0)
        assert missing_exc.value.code == 401

        bad_token_req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/system/desktop/status",
            headers={
                "Origin": f"http://127.0.0.1:{port}",
                "X-VoxPilot-Desktop-Token": "invalid-token-value",
                "Connection": "close",
            },
            method="GET",
        )
        with pytest.raises(urllib.error.HTTPError) as wrong_exc:
            urllib.request.urlopen(bad_token_req, timeout=3.0)
        assert wrong_exc.value.code == 401

        # 3. Desktop status endpoint succeeds with valid token + loopback Origin, without exposing token
        status_req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/system/desktop/status",
            headers={
                "Origin": f"http://127.0.0.1:{port}",
                "X-VoxPilot-Desktop-Token": controller.session_token,
                "Connection": "close",
            },
            method="GET",
        )
        with urllib.request.urlopen(status_req, timeout=3.0) as resp:
            assert resp.status == 200
            raw_status = resp.read().decode("utf-8")
            desktop_status = json.loads(raw_status)
            assert desktop_status["mode"] == "desktop_shell"
            assert desktop_status["port"] == port
            assert desktop_status["session_token_required"] is True
            assert controller.session_token not in raw_status

        # 4. Desktop focus endpoint allows tokenless second-instance loopback IPC, rejects invalid tokens
        focus_req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/system/desktop/focus",
            data=b"{}",
            headers={
                "Content-Type": "application/json",
                "Origin": f"http://127.0.0.1:{port}",
                "Connection": "close",
            },
            method="POST",
        )
        with urllib.request.urlopen(focus_req, timeout=3.0) as resp:
            assert resp.status == 200
            focus_payload = json.loads(resp.read().decode("utf-8"))
            assert focus_payload["ok"] is True

        bad_focus_req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/system/desktop/focus",
            data=b"{}",
            headers={
                "Content-Type": "application/json",
                "X-VoxPilot-Desktop-Token": "bogus-token",
                "Connection": "close",
            },
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as bad_focus_exc:
            urllib.request.urlopen(bad_focus_req, timeout=3.0)
        assert bad_focus_exc.value.code == 401

        # 5. Reject untrusted cross-origin requests (even with a valid desktop token!)
        for bad_origin in (
            "https://evil.example.com",
            "http://localhost.evil.com",
            "https://blocked-without-token.invalid",
        ):
            bad_req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/system/desktop/focus",
                data=b"{}",
                headers={
                    "Content-Type": "application/json",
                    "Origin": bad_origin,
                    "X-VoxPilot-Desktop-Token": controller.session_token,
                    "Connection": "close",
                },
                method="POST",
            )
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(bad_req, timeout=3.0)
            assert exc_info.value.code == 403

            bad_status_req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/system/desktop/status",
                headers={
                    "Origin": bad_origin,
                    "X-VoxPilot-Desktop-Token": controller.session_token,
                    "Connection": "close",
                },
                method="GET",
            )
            with pytest.raises(urllib.error.HTTPError) as status_exc_info:
                urllib.request.urlopen(bad_status_req, timeout=3.0)
            assert status_exc_info.value.code == 403

        assert server.stop(timeout=10.0) is True
        assert server._thread is not None and not server._thread.is_alive()
    finally:
        server.stop(timeout=5.0)
        set_shared_desktop_controller(None)


def test_desktop_shell_controller_with_mocked_pywebview(monkeypatch, tmp_path):
    """DesktopShellController shows startup splash, transitions to app URL, focuses, and shuts down Uvicorn."""
    monkeypatch.setenv("VOXPILOT_USER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VOXPILOT_DB_PATH", str(tmp_path / "data" / "test_controller.db"))
    monkeypatch.setenv("VOXPILOT_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("VOXPILOT_ENABLE_HOTKEY", "0")
    monkeypatch.setenv("VOXPILOT_ENABLE_SCHEDULER", "0")

    from backend.main import app

    class _FakeEvent:
        def __init__(self) -> None:
            self.handlers: List[Callable[..., Any]] = []

        def __iadd__(self, fn: Callable[..., Any]) -> "_FakeEvent":
            self.handlers.append(fn)
            return self

        def fire(self) -> None:
            for fn in list(self.handlers):
                fn()

    class _FakeWindow:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs
            self.loaded_urls: List[str] = []
            self.loaded_html: List[str] = []
            self.restored = False
            self.shown = False
            self.destroyed = False
            self.evaluated_scripts: List[str] = []
            self.url_loaded_event = threading.Event()
            self.events = types.SimpleNamespace(
                shown=_FakeEvent(),
                loaded=_FakeEvent(),
                closing=_FakeEvent(),
                closed=_FakeEvent(),
            )

        def evaluate_js(self, script: str) -> None:
            self.evaluated_scripts.append(script)

        def load_url(self, url: str) -> None:
            self.loaded_urls.append(url)
            self.url_loaded_event.set()

        def load_html(self, content: str) -> None:
            self.loaded_html.append(content)

        def restore(self) -> None:
            self.restored = True

        def show(self) -> None:
            self.shown = True

        def destroy(self) -> None:
            self.destroyed = True

    created_windows: List[_FakeWindow] = []
    start_calls: List[Dict[str, Any]] = []

    def _fake_create_window(**kwargs: Any) -> _FakeWindow:
        win = _FakeWindow(**kwargs)
        created_windows.append(win)
        return win

    def _fake_start(**kwargs: Any) -> None:
        start_calls.append(kwargs)
        win = created_windows[0]
        win.events.shown.fire()
        assert win.url_loaded_event.wait(timeout=15.0), "Desktop controller did not load backend URL"
        win.events.loaded.fire()
        assert controller.focus_window() is True
        assert win.restored is True
        assert win.shown is True
        win.events.closing.fire()
        win.events.closed.fire()

    fake_webview = types.ModuleType("webview")
    fake_webview.settings = {}
    fake_webview.create_window = _fake_create_window  # type: ignore[attr-defined]
    fake_webview.start = _fake_start  # type: ignore[attr-defined]

    port = _get_free_loopback_port()
    controller = DesktopShellController(host="127.0.0.1", port=port, startup_timeout=15.0)
    set_shared_desktop_controller(controller)

    try:
        with patch.dict(sys.modules, {"webview": fake_webview}), \
             patch("backend.desktop.shell.focus_window_by_title", return_value=True), \
             patch("backend.desktop.shell.apply_window_icon", return_value=True):
            rc = controller.run(app)

        assert rc == 0
        assert len(created_windows) == 1
        win = created_windows[0]
        assert win.kwargs["title"] == "VoxPilot"
        assert win.kwargs["js_api"] is None
        assert "Initializing VoxPilot" in win.kwargs["html"]
        assert win.loaded_urls == [f"http://127.0.0.1:{port}/"]
        assert any(
            "__VOXPILOT_DESKTOP_TOKEN__" in s and controller.session_token in s
            for s in win.evaluated_scripts
        )
        assert start_calls[0]["gui"] == "edgechromium"
        assert start_calls[0]["debug"] is False
        assert controller.server is not None
        assert controller.server._thread is not None
        assert controller.server._thread.is_alive() is False
    finally:
        controller.stop()
        set_shared_desktop_controller(None)


def test_startup_html_builders_escape_content():
    loading = build_startup_loading_html("http://127.0.0.1:8000", "0.5.2")
    assert "VoxPilot Desktop" in loading
    assert "0.5.2" in loading
    assert "http://127.0.0.1:8000/" in loading

    err_html = build_startup_error_html("Boom <script>alert(1)</script>", r"C:\Logs\voxpilot.log")
    assert "<script>alert(1)</script>" not in err_html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in err_html
    assert r"C:\Logs\voxpilot.log" in err_html


def test_should_use_headless_server_mode(monkeypatch):
    monkeypatch.delenv("VOXPILOT_HEADLESS", raising=False)
    monkeypatch.delenv("VOXPILOT_AUTO_OPEN_BROWSER", raising=False)

    assert launcher.should_use_headless_server_mode([]) is False
    assert launcher.should_use_headless_server_mode(["--server-only"]) is True
    assert launcher.should_use_headless_server_mode(["--headless"]) is True

    monkeypatch.setenv("VOXPILOT_HEADLESS", "1")
    assert launcher.should_use_headless_server_mode([]) is True
    monkeypatch.delenv("VOXPILOT_HEADLESS", raising=False)

    monkeypatch.setenv("VOXPILOT_AUTO_OPEN_BROWSER", "0")
    assert launcher.should_use_headless_server_mode([]) is True


def test_desktop_html_token_bootstrap_and_cross_site_isolation(monkeypatch, tmp_path):
    """SPA HTML injects desktop token meta only for trusted desktop requests and sets anti-framing/cache headers."""
    import backend.main as main_mod
    from backend.database.connection import reset_db_connection

    monkeypatch.setenv("VOXPILOT_USER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VOXPILOT_DB_PATH", str(tmp_path / "data" / "test_html_bootstrap.db"))
    monkeypatch.setenv("VOXPILOT_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("VOXPILOT_ENABLE_HOTKEY", "0")
    monkeypatch.setenv("VOXPILOT_ENABLE_SCHEDULER", "0")

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)
    (dist_dir / "index.html").write_text(
        "<!doctype html><html><head><title>VoxPilot</title></head><body><div id='root'></div></body></html>",
        encoding="utf-8",
    )
    monkeypatch.setattr(main_mod, "_frontend_dist", dist_dir)

    controller = DesktopShellController(host="127.0.0.1", port=8765)
    reset_db_connection()
    set_shared_desktop_controller(controller)

    try:
        with TestClient(main_mod.app) as client:
            # Trusted WebView initial load
            res = client.get("/", headers={"Accept": "text/html", "Sec-Fetch-Site": "none"})
            assert res.status_code == 200
            assert f'<meta name="voxpilot-desktop-token" content="{controller.session_token}" />' in res.text
            assert "no-store" in res.headers.get("cache-control", "")
            assert res.headers.get("referrer-policy") == "no-referrer"
            assert res.headers.get("x-frame-options") == "DENY"
            assert "frame-ancestors 'none'" in res.headers.get("content-security-policy", "")

            # Cross-site fetch must NOT receive desktop token on either / or /index.html
            cross_site_res = client.get(
                "/",
                headers={"Accept": "text/html", "Sec-Fetch-Site": "cross-site"},
            )
            assert cross_site_res.status_code == 200
            assert "voxpilot-desktop-token" not in cross_site_res.text
            assert controller.session_token not in cross_site_res.text

            evil_origin_res = client.get(
                "/index.html",
                headers={"Accept": "text/html", "Origin": "https://evil.example.com"},
            )
            assert evil_origin_res.status_code == 200
            assert "voxpilot-desktop-token" not in evil_origin_res.text
            assert controller.session_token not in evil_origin_res.text

            # Headless / server-only mode serves index without desktop token injection
            set_shared_desktop_controller(None)
            headless_res = client.get("/", headers={"Accept": "text/html"})
            assert headless_res.status_code == 200
            assert "voxpilot-desktop-token" not in headless_res.text
            assert controller.session_token not in headless_res.text
    finally:
        set_shared_desktop_controller(None)
        reset_db_connection()


def test_desktop_token_enforcement_across_agent_and_specialist_endpoints_and_headless_mode(monkeypatch, tmp_path):
    """Agent execution and specialist routes enforce desktop session token in desktop mode and stay open on loopback in headless mode."""
    from backend.api.agent import clear_executions, set_agent
    from backend.database.connection import init_db_sync, reset_db_connection
    from backend.main import app

    monkeypatch.setenv("VOXPILOT_USER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VOXPILOT_DB_PATH", str(tmp_path / "data" / "test_routes.db"))
    monkeypatch.setenv("VOXPILOT_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("VOXPILOT_ENABLE_HOTKEY", "0")
    monkeypatch.setenv("VOXPILOT_ENABLE_SCHEDULER", "0")

    reset_db_connection()
    init_db_sync()
    clear_executions()

    mock_agent = MagicMock()
    mock_agent.profile_service.get_active_profile_id.return_value = "default"
    mock_agent.process_request.return_value = {
        "execution_id": "exec-desktop-token-1",
        "conversation_id": "conv-desktop-1",
        "execution_status": "completed",
        "user_input": "Help",
        "intent": "general_chat",
        "confidence": 0.99,
        "plan": [],
        "execution_results": [],
        "completed_steps": [],
        "failed_steps": [],
        "response": "Desktop session verified.",
        "requires_confirmation": False,
        "pending_confirmation": None,
    }
    set_agent(mock_agent)

    controller = DesktopShellController(host="127.0.0.1", port=8766)
    set_shared_desktop_controller(controller)

    protected_routes = [
        ("POST", "/agent/execute", {"user_input": "Help"}),
        ("GET", "/agent/tools", None),
        ("GET", "/api/profiles", None),
        ("GET", "/api/providers", None),
        ("GET", "/api/memory", None),
        ("GET", "/api/personalization", None),
        ("GET", "/api/automations", None),
        ("GET", "/api/system/notifications", None),
        ("GET", "/api/system/startup", None),
        ("GET", "/api/updates/status", None),
        ("GET", "/api/screen/status", None),
    ]

    try:
        with TestClient(app) as client:
            for method, path, body in protected_routes:
                # 1. Missing token -> 401
                res_missing = client.request(method, path, json=body)
                assert res_missing.status_code == 401, f"{method} {path} did not reject missing token"

                # 2. Invalid token -> 401
                res_wrong = client.request(
                    method,
                    path,
                    json=body,
                    headers={"X-VoxPilot-Desktop-Token": "wrong-token"},
                )
                assert res_wrong.status_code == 401, f"{method} {path} did not reject wrong token"

                # 3. Untrusted Origin even with valid token -> 403
                res_evil = client.request(
                    method,
                    path,
                    json=body,
                    headers={
                        "Origin": "https://evil.example",
                        "X-VoxPilot-Desktop-Token": controller.session_token,
                    },
                )
                assert res_evil.status_code == 403, f"{method} {path} allowed untrusted Origin"

                # 4. Valid desktop token -> 200
                res_ok = client.request(
                    method,
                    path,
                    json=body,
                    headers={
                        "Origin": "http://127.0.0.1:8766",
                        "X-VoxPilot-Desktop-Token": controller.session_token,
                    },
                )
                assert res_ok.status_code == 200, f"{method} {path} failed with valid token: {res_ok.text}"

            # 5. Headless / server-only mode works without token, while still enforcing Origin
            set_shared_desktop_controller(None)
            for method, path, body in protected_routes:
                res_headless_ok = client.request(method, path, json=body)
                assert res_headless_ok.status_code == 200, f"Headless {method} {path} failed: {res_headless_ok.text}"

                res_headless_evil = client.request(
                    method,
                    path,
                    json=body,
                    headers={"Origin": "https://evil.example"},
                )
                assert res_headless_evil.status_code == 403
    finally:
        clear_executions()
        set_agent(None)
        set_shared_desktop_controller(None)
        reset_db_connection()
