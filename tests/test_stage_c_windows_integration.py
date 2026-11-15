"""
VoxPilot - Stage C Native Windows Integration Tests

Covers:
1. Canonical AppUserModelID alignment across desktop shell, notifications, and Inno Setup installer.
2. Idempotent per-user Windows AUMID registry registration & WinRT toast payload branding/fallback.
3. Zero-polling window focus/blur/minimize/restore tracking & notification UI suppression.
4. Desktop token lifecycle hardening (_on_window_loaded dispatching 'voxpilot:token-ready').
5. Global hotkey registration idempotency and VoiceService duplicate-worker prevention.
6. Windows Startup Manager launching the native desktop app rather than an external browser.
"""

from __future__ import annotations

import base64
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from backend.automation.emergency_stop import GlobalHotkeyListener
from backend.automation.notifications import (
    WINDOWS_POWERSHELL_AUMID,
    NotificationService,
    ensure_windows_aumid_registered,
)
from backend.automation.startup_manager import WindowsStartupManager
from backend.desktop.shell import (
    DesktopShellController,
    get_canonical_app_user_model_id,
    get_shared_desktop_controller,
    is_native_window_foreground,
    set_shared_desktop_controller,
)
from backend.main import app
from backend.version import get_app_version
from backend.voice.hotkey import VoiceHotkeyListener
from backend.voice.voice_service import VoiceService, VoiceSettingsManager


def test_canonical_aumid_alignment_across_shell_notifications_and_installer():
    expected_aumid = f"VoxPilotTeam.VoxPilot.DesktopAgent.{get_app_version()}"
    assert get_canonical_app_user_model_id() == expected_aumid

    svc = NotificationService()
    assert svc._get_app_user_model_id() == expected_aumid
    status = svc.get_status()
    assert status["app_user_model_id"] == expected_aumid

    iss_path = Path(__file__).resolve().parent.parent / "installer" / "VoxPilot.iss"
    iss_content = iss_path.read_text(encoding="utf-8")
    assert f"AppId={expected_aumid}" in iss_content or "VoxPilotTeam.VoxPilot.DesktopAgent.{#MyAppVersion}" in iss_content
    assert "AppUserModelID" in iss_content


def test_windows_aumid_registry_registration_and_toast_script(tmp_path: Path):
    svc = NotificationService(config_dir=tmp_path, platform_override="win32")

    written_values: dict[str, str] = {}

    class _FakeKey:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False

    def _fake_set_value(_key, name, _reserved, _typ, val):
        written_values[name] = val

    with patch("winreg.CreateKeyEx", return_value=_FakeKey()) as create_mock, patch(
        "winreg.SetValueEx", side_effect=_fake_set_value
    ), patch("sys.platform", "win32"):
        assert ensure_windows_aumid_registered() is True

    create_mock.assert_called_once()
    assert written_values["DisplayName"] == "VoxPilot"
    assert written_values["IconBackgroundColor"] == "FF0B0F17"

    captured_cmd: list[list[str]] = []

    def _fake_subproc_run(cmd, **_kwargs):
        captured_cmd.append(cmd)
        proc = MagicMock()
        proc.returncode = 0
        proc.stderr = ""
        proc.stdout = ""
        return proc

    with patch("backend.automation.notifications.ensure_windows_aumid_registered", return_value=True), patch(
        "subprocess.run", side_effect=_fake_subproc_run
    ):
        svc._send_windows_toast("VoxPilot Test", "Hello from Stage C", get_canonical_app_user_model_id())

    assert len(captured_cmd) == 1
    encoded = captured_cmd[0][-1]
    script_text = base64.b64decode(encoded).decode("utf-16le")
    assert get_canonical_app_user_model_id() in script_text
    assert WINDOWS_POWERSHELL_AUMID in script_text
    assert "try {" in script_text and "catch {" in script_text


def test_desktop_focus_minimize_restore_and_notification_suppression(tmp_path: Path):
    previous_controller = get_shared_desktop_controller()
    controller = DesktopShellController(host="127.0.0.1", port=8765, require_session_token=True)
    mock_window = MagicMock()
    controller._window = mock_window
    sent: list[tuple[str, str, str]] = []
    svc = NotificationService(
        config_dir=tmp_path,
        platform_override="win32",
        sender_fn=lambda t, m, a: sent.append((t, m, a)),
    )

    try:
        set_shared_desktop_controller(controller)
        with patch("backend.automation.notifications.get_shared_notification_service", return_value=svc), patch(
            "backend.desktop.shell.is_native_window_foreground", return_value=None
        ), patch("backend.automation.notifications.is_native_window_foreground", return_value=None):
            # Initial shown -> active & focused -> suppresses non-forced toast
            controller._on_window_shown()
            assert controller.is_window_active is True
            assert controller.is_window_focused is True
            assert svc.is_ui_recently_active() is True

            suppressed = svc.notify("task_completed", "Done", "Background work complete")
            assert suppressed.delivered is False
            assert suppressed.suppressed is True
            assert suppressed.reason == "suppressed_while_active"

            # Minimized -> unfocused & unsuppressed immediately
            controller._on_window_minimized()
            assert controller.is_window_focused is False
            assert controller.get_status_dict()["window_minimized"] is True
            assert svc.is_ui_recently_active() is False

            delivered = svc.notify("task_completed", "Done", "Background work complete")
            assert delivered.delivered is True
            assert len(sent) == 1

            # Restored -> focused & suppressed again
            controller._on_window_restored()
            assert controller.is_window_focused is True
            assert controller.get_status_dict()["window_minimized"] is False
            assert svc.is_ui_recently_active() is True

            # On-demand Win32 foreground check detects focus loss even before blur event arrives
            with patch("backend.desktop.shell.is_native_window_foreground", return_value=False), patch(
                "backend.automation.notifications.is_native_window_foreground", return_value=False
            ):
                assert controller.is_window_focused is False
                assert svc.is_ui_recently_active() is False

            # Loaded event reinforces token and dispatches cleanup event
            controller._on_window_loaded()
            mock_window.evaluate_js.assert_called_once()
            js_call = mock_window.evaluate_js.call_args[0][0]
            assert "window.__VOXPILOT_DESKTOP_TOKEN__" in js_call
            assert "voxpilot:token-ready" in js_call
    finally:
        set_shared_desktop_controller(previous_controller)


def test_is_native_window_foreground_detects_child_hwnd_and_minimized():
    fake_user32 = MagicMock()
    fake_user32.IsWindow.return_value = 1
    fake_user32.IsIconic.return_value = 0
    fake_user32.IsWindowVisible.return_value = 1
    # Child WebView2 HWND inside top-level window 1001
    fake_user32.GetForegroundWindow.return_value = 2002
    fake_user32.GetAncestor.return_value = 1001
    fake_user32.IsChild.return_value = 1

    with patch("sys.platform", "win32"), patch("ctypes.windll", create=True) as mock_windll:
        mock_windll.user32 = fake_user32
        assert is_native_window_foreground("VoxPilot", hwnd_hint=1001) is True

        # Minimized -> False
        fake_user32.IsIconic.return_value = 1
        assert is_native_window_foreground("VoxPilot", hwnd_hint=1001) is False

        # Foreground owned by different top-level app -> False
        fake_user32.IsIconic.return_value = 0
        fake_user32.GetForegroundWindow.return_value = 9999
        fake_user32.GetAncestor.return_value = 9999
        fake_user32.IsChild.return_value = 0
        assert is_native_window_foreground("VoxPilot", hwnd_hint=1001) is False


def test_system_activity_and_desktop_status_stage_c_fields():
    previous_controller = get_shared_desktop_controller()
    controller = DesktopShellController(host="127.0.0.1", port=8765, require_session_token=False)
    controller._window_active = True
    try:
        set_shared_desktop_controller(controller)
        client = TestClient(app)

        with patch("backend.desktop.shell.is_native_window_foreground", return_value=None):
            res_blur = client.post("/api/system/notifications/activity", json={"active": False})
            assert res_blur.status_code == 200
            status_blur = client.get("/api/system/desktop/status").json()
            assert status_blur["window_focused"] is False
            assert status_blur["app_user_model_id"] == get_canonical_app_user_model_id()

            res_focus = client.post("/api/system/notifications/activity", json={"active": True})
            assert res_focus.status_code == 200
            status_focus = client.get("/api/system/desktop/status").json()
            assert status_focus["window_focused"] is True
            assert status_focus["window_minimized"] is False
    finally:
        set_shared_desktop_controller(previous_controller)


def test_voice_service_prevents_duplicate_workers_and_cleans_up_stale_threads(tmp_path: Path):
    settings_mgr = VoiceSettingsManager(config_path=tmp_path / "voice_settings.json")
    mock_pipeline = MagicMock()
    mock_pipeline.recorder = MagicMock()
    mock_pipeline.recorder.device = None
    mock_pipeline.recorder.resolve_device.return_value = None
    mock_pipeline.recorder.is_recording = False
    mock_pipeline.current_conversation_id = "conv-test"
    mock_pipeline.listen.return_value = None

    service = VoiceService(settings_manager=settings_mgr, pipeline=mock_pipeline, register_hotkey_on_init=False)
    started_threads: list[str] = []

    def _fake_loop():
        started_threads.append(threading.current_thread().name)
        while not service._stop_event.wait(timeout=0.05):
            pass

    service._voice_interaction_loop = _fake_loop  # type: ignore[method-assign]
    try:
        s1 = service.start_voice_mode(source="test")
        assert s1["is_active"] is True
        first_worker = service._worker_thread
        assert first_worker is not None and first_worker.is_alive()

        # Starting again while active must reuse the existing worker without spawning a second
        s2 = service.start_voice_mode(source="duplicate_hotkey")
        assert s2["is_active"] is True
        assert service._worker_thread is first_worker

        service.stop_voice_mode(reason="test_stop")
        time.sleep(0.05)
        assert not first_worker.is_alive()
    finally:
        service.shutdown()


def test_hotkey_listeners_guard_against_duplicate_registrations():
    mock_thread = MagicMock()
    mock_thread.is_alive.return_value = True

    voice_listener = VoiceHotkeyListener(callback=lambda _: None)
    voice_listener._thread = mock_thread
    voice_listener._is_registered = True
    assert voice_listener.start() is True

    em_listener = GlobalHotkeyListener()
    em_listener._thread = mock_thread
    em_listener._running = True
    em_listener._is_registered = True
    assert em_listener.start() is True


def test_windows_startup_manager_launches_desktop_app_not_browser(tmp_path: Path):
    mgr = WindowsStartupManager(frozen_override=False)
    cmd = mgr.build_launch_command()
    assert "launcher.py" in cmd
    assert "http://" not in cmd and "https://" not in cmd
    assert "chrome" not in cmd.lower() and "msedge" not in cmd.lower()

    fake_exe = tmp_path / "VoxPilot.exe"
    fake_exe.write_bytes(b"MZ")
    mgr_frozen = WindowsStartupManager(executable_path=fake_exe, frozen_override=True)
    cmd_frozen = mgr_frozen.build_launch_command()
    assert str(fake_exe.resolve()) in cmd_frozen
