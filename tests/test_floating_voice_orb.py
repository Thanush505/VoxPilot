"""Tests for the Global Floating Voice Orb, shortcut sync, states, and multi-monitor placement."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.automation.emergency_stop import get_shared_emergency_stop_manager
from backend.desktop.floating_orb import (
    FloatingOrbController,
    MonitorWorkArea,
    ORB_COLLAPSED_SIZE,
    ORB_EXPANDED_SIZE,
    apply_no_activate_topmost_style,
    compute_orb_position,
    show_window_no_activate,
)
from backend.desktop.shell import DesktopShellController
from backend.voice.audio_recorder import AudioRecorderError
from backend.voice.voice_service import (
    PHASE_ERROR,
    PHASE_EXECUTING,
    PHASE_IDLE,
    PHASE_LISTENING,
    PHASE_PLANNING,
    PHASE_PROCESSING,
    PHASE_SPEAKING,
    PHASE_STARTING,
    VoiceService,
    VoiceSettingsManager,
)


def _make_voice_service(tmp_path: Path, pipeline_mock: MagicMock) -> VoiceService:
    settings_mgr = VoiceSettingsManager(config_path=tmp_path / "voice_settings.json")
    return VoiceService(
        settings_manager=settings_mgr,
        pipeline=pipeline_mock,
        register_hotkey_on_init=False,
    )


class TestMultiMonitorAndPositioning:
    """Verify screen-corner and custom multi-monitor orb coordinate clamping."""

    def test_single_monitor_corners(self):
        mons = [MonitorWorkArea(index=0, left=0, top=0, right=1920, bottom=1040, is_primary=True)]
        w, h = ORB_COLLAPSED_SIZE
        assert compute_orb_position("top_left", w, h, monitors=mons) == (20, 20)
        assert compute_orb_position("top_right", w, h, monitors=mons) == (1920 - w - 20, 20)
        assert compute_orb_position("bottom_left", w, h, monitors=mons) == (20, 1040 - h - 20)
        assert compute_orb_position("bottom_right", w, h, monitors=mons) == (1920 - w - 20, 1040 - h - 20)

    def test_multi_monitor_negative_left_and_clamping(self):
        mons = [
            MonitorWorkArea(index=0, left=0, top=0, right=1920, bottom=1040, is_primary=True),
            MonitorWorkArea(index=1, left=-1920, top=0, right=0, bottom=1080, is_primary=False),
        ]
        w, h = ORB_COLLAPSED_SIZE
        x, y = compute_orb_position("custom", w, h, custom_x=-500, custom_y=200, monitors=mons)
        assert -1920 <= x <= -w
        assert y == 200

        # Out-of-bounds custom coordinate clamps cleanly inside nearest monitor
        cx, cy = compute_orb_position("custom", w, h, custom_x=9999, custom_y=9999, monitors=mons)
        assert cx == 1920 - w
        assert cy == 1040 - h


class TestFloatingOrbWindowController:
    """Verify always-on-top, no-focus, single-instance, and hide-without-stopping behavior."""

    def test_no_duplicate_orb_windows_and_frameless_topmost_flags(self):
        controller = FloatingOrbController()
        mock_webview = MagicMock()
        mock_win = MagicMock()
        mock_webview.create_window.return_value = mock_win

        win1 = controller.create_orb_window(mock_webview, "<html></html>", {"floating_orb_corner": "bottom_right"})
        win2 = controller.create_orb_window(mock_webview, "<html></html>", {"floating_orb_corner": "top_left"})

        assert win1 is mock_win
        assert win2 is mock_win
        assert mock_webview.create_window.call_count == 1

        kwargs = mock_webview.create_window.call_args.kwargs
        assert kwargs["frameless"] is True
        assert kwargs["on_top"] is True
        assert kwargs["transparent"] is True
        assert kwargs["focus"] is False
        assert kwargs["hidden"] is True

        # Attaching another window is rejected while active
        assert controller.attach_window(MagicMock()) is False

    def test_win32_no_activate_style_and_show(self):
        if not hasattr(apply_no_activate_topmost_style, "__call__"):
            pytest.skip("Win32 helper unavailable")
        fake_user32 = MagicMock()
        fake_user32.GetWindowLongW.return_value = 0
        with patch("ctypes.windll", create=True) as mock_windll:
            mock_windll.user32 = fake_user32
            assert apply_no_activate_topmost_style(12345) is True
            assert show_window_no_activate(12345) is True
            assert fake_user32.SetWindowLongW.called
            assert fake_user32.ShowWindow.call_args[0] == (12345, 4)  # SW_SHOWNOACTIVATE

    def test_orb_hidden_while_voice_mode_remains_active(self, tmp_path):
        pipeline = MagicMock()
        pipeline.recorder = MagicMock()
        pipeline.tts = MagicMock()
        pipeline.tts.model_path = Path("en_US-lessac-medium.onnx")
        pipeline.current_conversation_id = "conv_orb"
        gate = threading.Event()

        def _listen(stop_event=None, on_capture_active=None):
            if on_capture_active:
                on_capture_active()
            gate.wait(timeout=2.0)
            return []

        pipeline.listen.side_effect = _listen

        svc = _make_voice_service(tmp_path, pipeline)
        orb = FloatingOrbController()
        mock_win = MagicMock()
        orb.attach_window(mock_win)
        svc.register_orb_listener(orb.sync_state)

        try:
            svc.start_voice_mode(source="hotkey:Ctrl+Shift+V")
            time.sleep(0.15)
            assert svc.is_active is True
            assert orb.is_visible is True

            # Hiding the orb must NOT stop voice mode
            svc.update_config({"floating_orb_visible": False})
            assert orb.is_visible is False
            assert svc.is_active is True
            assert mock_win.hide.called

            # Restoring orb shows it again
            svc.update_config({"floating_orb_visible": True})
            assert orb.is_visible is True
            assert svc.is_active is True
        finally:
            gate.set()
            svc.stop_voice_mode(reason="test cleanup")

    def test_panel_expand_resizes_window(self):
        orb = FloatingOrbController()
        win = MagicMock()
        orb.attach_window(win)
        orb.sync_state("listening", True, {"floating_orb_enabled": True, "floating_orb_visible": True})
        orb.set_panel_expanded(True, {"floating_orb_corner": "bottom_right"})
        win.resize.assert_called_with(*ORB_EXPANDED_SIZE)
        orb.set_panel_expanded(False, {"floating_orb_corner": "bottom_right"})
        win.resize.assert_called_with(*ORB_COLLAPSED_SIZE)


class TestVoiceShortcutAndStateSync:
    """Verify hotkey toggle, starting->listening stream confirmation, errors, and shutdown."""

    def test_first_and_second_shortcut_press_and_starting_to_listening(self, tmp_path):
        pipeline = MagicMock()
        pipeline.recorder = MagicMock()
        pipeline.tts = MagicMock()
        pipeline.tts.model_path = Path("en_US-lessac-medium.onnx")
        pipeline.current_conversation_id = "conv_1"

        capture_gate = threading.Event()
        release_listen = threading.Event()
        observed_phases: list[tuple[str, bool]] = []

        def _listen(stop_event=None, on_capture_active=None):
            # Stay in 'starting' until capture_gate is released
            capture_gate.wait(timeout=2.0)
            if on_capture_active:
                on_capture_active()
            release_listen.wait(timeout=2.0)
            return []

        pipeline.listen.side_effect = _listen

        svc = _make_voice_service(tmp_path, pipeline)
        orb = FloatingOrbController()
        orb.attach_window(MagicMock())

        def _record(phase, is_active, settings):
            observed_phases.append((phase, is_active))
            orb.sync_state(phase, is_active, settings)

        svc.register_orb_listener(_record)

        try:
            # First press -> Starting immediately, NOT Listening until mic stream opens
            svc._on_global_hotkey_pressed("Ctrl+Shift+V")
            assert svc.is_active is True
            assert svc.phase == PHASE_STARTING
            assert orb.is_visible is True

            # Confirm mic capture active -> transitions to Listening
            capture_gate.set()
            deadline = time.time() + 2.0
            while svc.phase != PHASE_LISTENING and time.time() < deadline:
                time.sleep(0.02)
            assert svc.phase == PHASE_LISTENING

            # Second press -> toggles voice mode off and hides orb
            release_listen.set()
            svc._on_global_hotkey_pressed("Ctrl+Shift+V")
            assert svc.is_active is False
            assert svc.phase == PHASE_IDLE
            assert orb.is_visible is False
            assert (PHASE_STARTING, True) in observed_phases
            assert (PHASE_LISTENING, True) in observed_phases
            assert (PHASE_IDLE, False) in observed_phases
        finally:
            capture_gate.set()
            release_listen.set()
            svc.stop_voice_mode(reason="cleanup")

    def test_microphone_initialization_failure_sets_error_not_listening(self, tmp_path):
        pipeline = MagicMock()
        pipeline.recorder = MagicMock()
        pipeline.recorder.resolve_device.side_effect = AudioRecorderError("No microphone found")
        pipeline.tts = MagicMock()
        pipeline.tts.model_path = Path("en_US-lessac-medium.onnx")

        svc = _make_voice_service(tmp_path, pipeline)
        orb = FloatingOrbController()
        orb.attach_window(MagicMock())
        svc.register_orb_listener(orb.sync_state)

        status = svc.start_voice_mode(source="hotkey:Ctrl+Shift+V")
        assert status["is_active"] is False
        assert status["phase"] == PHASE_ERROR
        assert orb.is_visible is True  # Error state surfaces clearly to user

    def test_duplicate_worker_prevention(self, tmp_path):
        pipeline = MagicMock()
        pipeline.recorder = MagicMock()
        pipeline.tts = MagicMock()
        pipeline.tts.model_path = Path("en_US-lessac-medium.onnx")
        gate = threading.Event()
        pipeline.listen.side_effect = lambda stop_event=None, on_capture_active=None: (
            on_capture_active() if on_capture_active else None,
            gate.wait(timeout=2.0),
            [],
        )[-1]

        svc = _make_voice_service(tmp_path, pipeline)
        try:
            svc.start_voice_mode(source="hotkey:Ctrl+Shift+V")
            worker_1 = svc._worker_thread
            svc.start_voice_mode(source="hotkey:Ctrl+Shift+V")
            worker_2 = svc._worker_thread
            assert worker_1 is worker_2
        finally:
            gate.set()
            svc.stop_voice_mode(reason="cleanup")

    def test_emergency_stop_halts_voice_and_updates_orb(self, tmp_path):
        pipeline = MagicMock()
        pipeline.recorder = MagicMock()
        pipeline.tts = MagicMock()
        pipeline.tts.model_path = Path("en_US-lessac-medium.onnx")
        gate = threading.Event()
        pipeline.listen.side_effect = lambda stop_event=None, on_capture_active=None: (
            on_capture_active() if on_capture_active else None,
            gate.wait(timeout=2.0),
            [],
        )[-1]

        em = get_shared_emergency_stop_manager()
        if em.is_emergency_stop_active():
            em.reset_emergency_stop()

        svc = _make_voice_service(tmp_path, pipeline)
        orb = FloatingOrbController()
        orb.attach_window(MagicMock())
        svc.register_orb_listener(orb.sync_state)

        try:
            svc.start_voice_mode(source="hotkey:Ctrl+Shift+V")
            time.sleep(0.1)
            assert svc.is_active is True

            em.trigger_emergency_stop(reason="Test Ctrl+Alt+Escape", source="hotkey")
            gate.set()
            time.sleep(0.1)
            assert svc.is_active is False
            assert orb.is_visible is False
        finally:
            gate.set()
            if em.is_emergency_stop_active():
                em.reset_emergency_stop()
            svc.stop_voice_mode(reason="cleanup")

    def test_all_orb_state_transitions_and_shutdown_cleanup(self, tmp_path):
        pipeline = MagicMock()
        pipeline.recorder = MagicMock()
        pipeline.tts = MagicMock()
        pipeline.tts.model_path = Path("en_US-lessac-medium.onnx")

        svc = _make_voice_service(tmp_path, pipeline)
        svc._is_active = True
        for phase in (PHASE_STARTING, PHASE_LISTENING, PHASE_PROCESSING, PHASE_PLANNING, PHASE_EXECUTING, PHASE_SPEAKING, PHASE_ERROR):
            svc.set_phase(phase)
            assert svc.phase == phase

        # DesktopShellController.stop() destroys orb & VoiceService.shutdown() stops hotkey listener
        desktop = DesktopShellController(port=8765)
        mock_orb_win = MagicMock()
        desktop.orb_controller.attach_window(mock_orb_win)
        desktop.stop()
        assert mock_orb_win.destroy.called
        assert not desktop.orb_controller.has_window

        with patch.object(svc.hotkey_listener, "stop") as mock_stop_hk:
            svc.shutdown()
            assert mock_stop_hk.called
