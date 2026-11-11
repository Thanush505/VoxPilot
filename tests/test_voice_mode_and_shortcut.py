"""
VoxPilot - Comprehensive Unit & Integration Tests for Voice Mode Integration
and Customizable Global Keyboard Shortcut.
Covers:
- Voice pipeline start/stop, multi-turn loop, cancellation, and microphone privacy
- Microphone failure handling and non-destructive microphone testing
- Speech transcription, agent response, confirmation token scrubbing, and Piper TTS playback
- Emergency stop integration with Voice Mode
- Global shortcut parsing, Win32 RegisterHotKey registration, custom shortcuts, conflict rollback,
  Ctrl+Alt+Escape reservation protection, duplicate prevention, unfocused toggle, and clean shutdown
- Persistent settings in %LOCALAPPDATA%\\VoxPilot\\config\\voice_settings.json
- FastAPI voice & shortcut endpoints and CSRF origin protection
"""

from __future__ import annotations

import ctypes
import json
from pathlib import Path
import threading
import time
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
import numpy as np
import pytest

from backend.automation.emergency_stop import get_shared_emergency_stop_manager
from backend.main import app
from backend.paths import get_config_dir
from backend.voice.audio_recorder import AudioRecorder, AudioRecorderError
from backend.voice.hotkey import (
    DEFAULT_VOICE_SHORTCUT,
    HOTKEY_ID_VOICE_MODE,
    MOD_ALT,
    MOD_CONTROL,
    MOD_SHIFT,
    MOD_WIN,
    ReservedShortcutError,
    ShortcutConflictError,
    ShortcutValidationError,
    VoiceHotkeyListener,
    WM_HOTKEY,
    parse_shortcut,
)
from backend.voice.speech_to_text import SpeechToTextError
from backend.voice.text_to_speech import TextToSpeechError
from backend.voice.voice_service import (
    PHASE_ERROR,
    PHASE_IDLE,
    PHASE_LISTENING,
    VoiceService,
    VoiceSettingsManager,
    list_available_piper_voices,
    resolve_piper_voice_path,
)


# ==============================================================================
# 1. Shortcut Parsing & Emergency Stop Reservation Tests
# ==============================================================================

class TestShortcutParsingAndValidation:
    """Verify shortcut string parsing, canonicalization, and safety invariants."""

    def test_default_shortcut_ctrl_shift_v(self):
        canonical, mods, vk = parse_shortcut("Ctrl+Shift+V")
        assert canonical == "Ctrl+Shift+V"
        assert mods == (MOD_CONTROL | MOD_SHIFT)
        assert vk == ord("V")

    def test_custom_shortcuts_normalized_canonically(self):
        canonical, mods, vk = parse_shortcut("shift + control + m")
        assert canonical == "Ctrl+Shift+M"
        assert mods == (MOD_CONTROL | MOD_SHIFT)
        assert vk == ord("M")

        canonical_f, mods_f, vk_f = parse_shortcut("Alt + F9")
        assert canonical_f == "Alt+F9"
        assert mods_f == MOD_ALT
        assert vk_f == 0x70 + 8

        canonical_fn_only, mods_fn, vk_fn = parse_shortcut("F8")
        assert canonical_fn_only == "F8"
        assert mods_fn == 0
        assert vk_fn == 0x70 + 7

        canonical_win, mods_w, vk_w = parse_shortcut("Ctrl+Win+Space")
        assert canonical_win == "Ctrl+Win+Space"
        assert mods_w == (MOD_CONTROL | MOD_WIN)
        assert vk_w == 0x20

    def test_rejects_emergency_stop_shortcut_ctrl_alt_esc(self):
        """CRITICAL: Never allow assigning Ctrl + Alt + Esc to voice mode."""
        for variant in (
            "Ctrl+Alt+Esc",
            "Ctrl+Alt+Escape",
            "Control + Alt + Escape",
            "alt+ctrl+esc",
        ):
            with pytest.raises(ReservedShortcutError, match="permanently reserved for the Global Emergency Stop"):
                parse_shortcut(variant)

    def test_rejects_invalid_or_unmodified_single_key_shortcuts(self):
        with pytest.raises(ShortcutValidationError):
            parse_shortcut("")

        with pytest.raises(ShortcutValidationError):
            parse_shortcut("   ")

        with pytest.raises(ShortcutValidationError, match="must include a non-modifier key"):
            parse_shortcut("Ctrl+Shift")

        with pytest.raises(ShortcutValidationError, match="must include at least one modifier"):
            parse_shortcut("V")

        with pytest.raises(ShortcutValidationError, match="multiple primary keys"):
            parse_shortcut("Ctrl+A+B")


# ==============================================================================
# 2. Global Hotkey Registration, Conflict Rollback & Unfocused Toggle Tests
# ==============================================================================

class TestGlobalVoiceHotkeyListener:
    """Verify real Windows RegisterHotKey behavior, conflict recovery, and clean unregistration."""

    def test_default_shortcut_registers_and_unregisters_cleanly_on_windows(self):
        triggered = []
        # Use a safe test combination (Ctrl+Shift+F11) or default Ctrl+Shift+V
        listener = VoiceHotkeyListener(
            callback=lambda sc: triggered.append(sc),
            shortcut=DEFAULT_VOICE_SHORTCUT,
            enabled=True,
            debounce_seconds=0.1,
        )
        try:
            ok = listener.start()
            assert ok is True
            assert listener.is_registered is True
            assert listener.registration_error is None

            # Duplicate start() is idempotent and prevents duplicate registration
            assert listener.start() is True
            assert listener.is_registered is True

            # Simulate OS posting WM_HOTKEY (as Windows does even when dashboard is unfocused)
            assert listener._thread_id is not None
            ctypes.windll.user32.PostThreadMessageW(
                listener._thread_id,
                WM_HOTKEY,
                HOTKEY_ID_VOICE_MODE,
                0,
            )
            time.sleep(0.15)
            assert len(triggered) == 1
            assert triggered[0] == "Ctrl+Shift+V"
            assert listener.trigger_count == 1
        finally:
            listener.stop()
            assert listener.is_registered is False

    def test_key_repeat_debounce_prevents_duplicate_activations(self):
        triggered = []
        listener = VoiceHotkeyListener(
            callback=lambda sc: triggered.append(sc),
            shortcut="Ctrl+Shift+F10",
            enabled=True,
            debounce_seconds=0.4,
        )
        try:
            assert listener.start() is True
            tid = listener._thread_id
            assert tid is not None

            # Post 4 rapid WM_HOTKEY messages within debounce window
            for _ in range(4):
                ctypes.windll.user32.PostThreadMessageW(tid, WM_HOTKEY, HOTKEY_ID_VOICE_MODE, 0)

            time.sleep(0.15)
            assert len(triggered) == 1
        finally:
            listener.stop()

    def test_custom_shortcut_update_and_conflict_rollback(self):
        """
        Verify changing shortcut dynamically works, and if a new shortcut conflicts with an
        already-registered hotkey on Windows, the listener automatically rolls back and
        keeps the previous valid shortcut active.
        """
        # Occupy 'Ctrl+Shift+F12' with an external Win32 hotkey listener to force a real OS conflict
        blocker = VoiceHotkeyListener(
            callback=lambda _: None,
            shortcut="Ctrl+Shift+F12",
            enabled=True,
        )
        listener = VoiceHotkeyListener(
            callback=lambda _: None,
            shortcut="Ctrl+Shift+V",
            enabled=True,
        )
        try:
            assert blocker.start() is True
            assert listener.start() is True
            assert listener.shortcut == "Ctrl+Shift+V"

            # 1. Update to a valid non-conflicting custom shortcut
            updated = listener.update_shortcut("Ctrl+Shift+F9")
            assert updated == "Ctrl+Shift+F9"
            assert listener.shortcut == "Ctrl+Shift+F9"
            assert listener.is_registered is True

            # 2. Attempt to update to Ctrl+Shift+F12 (currently held by blocker -> WinError 1409 conflict!)
            with pytest.raises(ShortcutConflictError, match="Previous shortcut 'Ctrl\\+Shift\\+F9' remains active"):
                listener.update_shortcut("Ctrl+Shift+F12")

            # Verify rollback preserved Ctrl+Shift+F9 and re-registered it!
            assert listener.shortcut == "Ctrl+Shift+F9"
            assert listener.is_registered is True

            # 3. Attempt to update to reserved Ctrl+Alt+Escape -> rejected without losing Ctrl+Shift+F9
            with pytest.raises(ReservedShortcutError):
                listener.update_shortcut("Ctrl+Alt+Escape")
            assert listener.shortcut == "Ctrl+Shift+F9"
            assert listener.is_registered is True
        finally:
            listener.stop()
            blocker.stop()


# ==============================================================================
# 3. Persistent Configuration Storage Tests
# ==============================================================================

class TestPersistentVoiceConfiguration:
    """Verify voice settings persist in %LOCALAPPDATA%\\VoxPilot\\config\\voice_settings.json."""

    def test_config_dir_resolves_to_localappdata_voxpilot_config(self, monkeypatch, tmp_path):
        monkeypatch.delenv("VOXPILOT_CONFIG_DIR", raising=False)
        monkeypatch.delenv("VOXPILOT_USER_DATA_DIR", raising=False)
        fake_local = tmp_path / "LocalAppData"
        monkeypatch.setenv("LOCALAPPDATA", str(fake_local))

        cfg_dir = get_config_dir()
        assert cfg_dir == (fake_local / "VoxPilot" / "config").resolve()
        assert cfg_dir.exists()

    def test_settings_persistence_across_restarts(self, tmp_path):
        cfg_file = tmp_path / "config" / "voice_settings.json"
        mgr1 = VoiceSettingsManager(config_path=cfg_file)
        assert mgr1.get_settings()["shortcut"] == "Ctrl+Shift+V"
        assert mgr1.get_settings()["tts_enabled"] is True

        mgr1.save({
            "shortcut": "Ctrl+Alt+M",
            "shortcut_enabled": True,
            "microphone_device": 2,
            "tts_enabled": False,
            "streaming_vad_enabled": False,
        })
        assert cfg_file.exists()

        # Simulate application restart with a new manager instance reading the same file
        mgr2 = VoiceSettingsManager(config_path=cfg_file)
        loaded = mgr2.get_settings()
        assert loaded["shortcut"] == "Ctrl+Alt+M"
        assert loaded["microphone_device"] == 2
        assert loaded["tts_enabled"] is False
        assert loaded["streaming_vad_enabled"] is False

    def test_corrupted_or_reserved_shortcut_in_file_falls_back_safely(self, tmp_path):
        cfg_file = tmp_path / "config" / "voice_settings.json"
        cfg_file.parent.mkdir(parents=True, exist_ok=True)
        cfg_file.write_text(json.dumps({"shortcut": "Ctrl+Alt+Escape"}), encoding="utf-8")

        mgr = VoiceSettingsManager(config_path=cfg_file)
        assert mgr.get_settings()["shortcut"] == DEFAULT_VOICE_SHORTCUT


# ==============================================================================
# 4. Voice Mode Lifecycle, Pipeline Integration, Safety & Privacy Tests
# ==============================================================================

class TestVoiceServiceLifecycleAndPrivacy:
    """Verify VoiceService lifecycle, STT/Agent/TTS flow, emergency stop, and mic privacy."""

    def _make_mock_pipeline(self):
        pipeline = MagicMock()
        pipeline.recorder = MagicMock()
        pipeline.recorder.is_recording = False
        pipeline.recorder.device = None
        pipeline.recorder.aggressiveness = 2
        pipeline.recorder.frame_duration_ms = 30
        pipeline.recorder.end_silence_ms = 900
        pipeline.recorder.resolve_device.return_value = (
            0,
            {"index": 0, "name": "Mock WASAPI Mic", "host_api": "Windows WASAPI", "channels": 1, "default_sample_rate": 16000.0},
        )
        pipeline.stt = MagicMock()
        pipeline.stt.model_size = "base"
        pipeline.stt.device = "cpu"
        pipeline.stt.compute_type = "int8"
        pipeline.stt.model = MagicMock()
        pipeline.tts = MagicMock()
        pipeline.tts.voice = MagicMock()
        pipeline.tts.model_path = resolve_piper_voice_path("en_US-lessac-medium.onnx")
        pipeline.current_conversation_id = "conv_voice_test"
        pipeline.last_agent_result = None
        pipeline.is_exit_command.side_effect = lambda txt: txt.lower().strip() in ("exit voxpilot", "stop listening")
        return pipeline

    def test_microphone_never_activates_on_startup_or_when_off(self, tmp_path):
        mgr = VoiceSettingsManager(config_path=tmp_path / "voice_settings.json")
        pipeline = self._make_mock_pipeline()

        service = VoiceService(settings_manager=mgr, pipeline=pipeline, register_hotkey_on_init=False)
        assert service.is_active is False
        assert service.phase == PHASE_IDLE
        assert pipeline.recorder.is_recording is False
        pipeline.listen.assert_not_called()

    def test_voice_mode_full_turn_transcription_agent_and_piper_tts(self, tmp_path):
        mgr = VoiceSettingsManager(config_path=tmp_path / "voice_settings.json")
        pipeline = self._make_mock_pipeline()

        service: VoiceService
        call_count = 0

        def fake_listen(stop_event=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return np.ones(16000, dtype=np.float32) * 0.1
            # Stop after first turn completes
            time.sleep(0.05)
            service._stop_event.set()
            return np.array([], dtype=np.float32)

        pipeline.listen.side_effect = fake_listen
        pipeline.recognize.return_value = "Open Notepad"

        def fake_generate_response(text, conversation_id=None):
            pipeline.last_agent_result = {
                "execution_id": "exec_voice_01",
                "conversation_id": conversation_id or "conv_voice_test",
                "execution_status": "completed",
                "user_input": text,
                "intent": "application_control",
                "confidence": 0.99,
                "plan": [{"action": "open_application", "parameters": {"application_name": "notepad"}}],
                "execution_results": [{"status": "completed"}],
                "completed_steps": [{"action": "open_application", "status": "completed"}],
                "failed_steps": [],
                "response": "Opened Notepad successfully.",
                "requires_confirmation": False,
                "pending_confirmation": None,
            }
            return "Opened Notepad successfully."

        pipeline.generate_response.side_effect = fake_generate_response

        with patch.object(AudioRecorder, "get_input_devices", return_value=[{"index": 0, "name": "Mock Mic", "host_api": "WASAPI", "channels": 1, "default_sample_rate": 16000.0}]), \
             patch.object(AudioRecorder, "resolve_device", return_value=(0, {"index": 0, "name": "Mock Mic", "host_api": "WASAPI", "channels": 1, "default_sample_rate": 16000.0})):
            service = VoiceService(settings_manager=mgr, pipeline=pipeline, register_hotkey_on_init=False)
            service.start_voice_mode(conversation_id="conv_voice_test")

            # Wait for turn to complete
            deadline = time.time() + 3.0
            while service.last_response != "Opened Notepad successfully." and time.time() < deadline:
                time.sleep(0.05)
                if service._last_response == "Opened Notepad successfully.":
                    break

            service.stop_voice_mode()

        assert service._last_transcript == "Open Notepad"
        assert service._last_response == "Opened Notepad successfully."
        pipeline.recognize.assert_called_once()
        pipeline.generate_response.assert_called_once_with("Open Notepad", conversation_id="conv_voice_test")
        pipeline.speak.assert_called_once_with("Opened Notepad successfully.", cleanup_temp_file=True)

        status = service.get_status()
        assert status["is_active"] is False
        assert status["phase"] == PHASE_IDLE
        assert status["is_recording"] is False
        event_types = [ev["type"] for ev in status["events"]]
        assert "transcription" in event_types
        assert "agent_response" in event_types

    def test_microphone_failure_transitions_to_error_state(self, tmp_path):
        mgr = VoiceSettingsManager(config_path=tmp_path / "voice_settings.json")
        pipeline = self._make_mock_pipeline()
        pipeline.recorder.resolve_device.side_effect = AudioRecorderError("No microphone input devices found.")

        service = VoiceService(settings_manager=mgr, pipeline=pipeline, register_hotkey_on_init=False)
        status = service.start_voice_mode()

        assert status["is_active"] is False
        assert status["phase"] == PHASE_ERROR
        assert "No microphone input devices found" in (status["last_error"] or "")

    def test_emergency_stop_blocks_and_aborts_voice_mode(self, tmp_path):
        mgr = VoiceSettingsManager(config_path=tmp_path / "voice_settings.json")
        pipeline = self._make_mock_pipeline()
        em_manager = get_shared_emergency_stop_manager()

        listen_started = threading.Event()

        def blocking_listen(stop_event=None):
            listen_started.set()
            while stop_event is not None and not stop_event.is_set():
                time.sleep(0.02)
            return np.array([], dtype=np.float32)

        pipeline.listen.side_effect = blocking_listen

        try:
            service = VoiceService(settings_manager=mgr, pipeline=pipeline, register_hotkey_on_init=False)
            service.start_voice_mode()
            assert listen_started.wait(timeout=2.0) is True
            assert service.is_active is True

            # Trigger Emergency Stop while Voice Mode is actively listening
            em_manager.trigger_emergency_stop(reason="Test emergency halt", source="test")
            time.sleep(0.1)

            assert service.is_active is False
            pipeline.stop_listening.assert_called()
            pipeline.stop_speaking.assert_called()

            # Starting Voice Mode while Emergency Stop is latched must be blocked
            blocked_status = service.start_voice_mode()
            assert blocked_status["is_active"] is False
            assert blocked_status["phase"] == PHASE_ERROR
            assert "Emergency Stop is active" in (blocked_status["last_error"] or "")
        finally:
            em_manager.reset_emergency_stop(force=False)

    def test_confirmation_required_handling_during_voice_mode(self, tmp_path):
        """Verify voice mode handles confirmation_required safely and never speaks confirmation tokens."""
        mgr = VoiceSettingsManager(config_path=tmp_path / "voice_settings.json")
        pipeline = self._make_mock_pipeline()

        service: VoiceService
        call_count = 0

        def fake_listen(stop_event=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return np.ones(16000, dtype=np.float32) * 0.1
            time.sleep(0.05)
            service._stop_event.set()
            return np.array([], dtype=np.float32)

        pipeline.listen.side_effect = fake_listen
        pipeline.recognize.return_value = "Delete file report.txt"

        def fake_generate_response(text, conversation_id=None):
            pipeline.last_agent_result = {
                "execution_id": "exec_voice_conf_01",
                "conversation_id": conversation_id or "conv_voice_test",
                "execution_status": "confirmation_required",
                "user_input": text,
                "intent": "file_management",
                "confidence": 0.96,
                "plan": [{"action": "delete_file", "parameters": {"path": "report.txt"}}],
                "execution_results": [],
                "completed_steps": [],
                "failed_steps": [],
                "response": "VoxPilot needs your approval before performing this action. Please review the pending confirmation in the application.",
                "requires_confirmation": True,
                "pending_confirmation": {
                    "token": "secret-cryptographic-token-99999",
                    "action": "delete_file",
                    "parameters": {"path": "report.txt"},
                    "description": "Delete file report.txt",
                    "execution_id": "exec_voice_conf_01",
                    "step_index": 0,
                },
            }
            return pipeline.last_agent_result["response"]

        pipeline.generate_response.side_effect = fake_generate_response

        with patch.object(AudioRecorder, "get_input_devices", return_value=[{"index": 0, "name": "Mock Mic", "host_api": "WASAPI", "channels": 1, "default_sample_rate": 16000.0}]), \
             patch.object(AudioRecorder, "resolve_device", return_value=(0, {"index": 0, "name": "Mock Mic", "host_api": "WASAPI", "channels": 1, "default_sample_rate": 16000.0})):
            service = VoiceService(settings_manager=mgr, pipeline=pipeline, register_hotkey_on_init=False)
            service.start_voice_mode(conversation_id="conv_voice_test")

            deadline = time.time() + 3.0
            while service.last_response is None and time.time() < deadline:
                time.sleep(0.05)

            service.stop_voice_mode()

        assert service.last_response is not None
        assert "secret-cryptographic-token-99999" not in service.last_response
        pipeline.speak.assert_called_once()
        spoken_arg = pipeline.speak.call_args[0][0]
        assert "secret-cryptographic-token-99999" not in spoken_arg

    def test_global_shortcut_callback_toggles_voice_mode_when_unfocused(self, tmp_path):
        mgr = VoiceSettingsManager(config_path=tmp_path / "voice_settings.json")
        pipeline = self._make_mock_pipeline()

        def idle_listen(stop_event=None):
            while stop_event is not None and not stop_event.is_set():
                time.sleep(0.02)
            return np.array([], dtype=np.float32)

        pipeline.listen.side_effect = idle_listen
        service = VoiceService(settings_manager=mgr, pipeline=pipeline, register_hotkey_on_init=False)

        try:
            assert service.is_active is False
            # 1st press of global shortcut (e.g. while user is in another Windows app) -> activates Voice Mode
            service._on_global_hotkey_pressed("Ctrl+Shift+V")
            assert service.is_active is True
            assert service.phase == PHASE_LISTENING

            # 2nd press of global shortcut -> deactivates Voice Mode immediately
            service._on_global_hotkey_pressed("Ctrl+Shift+V")
            assert service.is_active is False
            assert service.phase == PHASE_IDLE
        finally:
            service.shutdown()

    def test_microphone_test_discards_audio_without_saving_to_disk(self, tmp_path):
        mgr = VoiceSettingsManager(config_path=tmp_path / "voice_settings.json")
        pipeline = self._make_mock_pipeline()
        service = VoiceService(settings_manager=mgr, pipeline=pipeline, register_hotkey_on_init=False)

        fake_samples = np.sin(np.linspace(0, 20, 8000)).astype(np.float32) * 0.25
        with patch.object(AudioRecorder, "resolve_device", return_value=(0, {"index": 0, "name": "Test Mic", "host_api": "WASAPI", "channels": 1, "default_sample_rate": 16000.0})), \
             patch.object(AudioRecorder, "_determine_recording_parameters", return_value=(16000, 1, False)), \
             patch("backend.voice.voice_service.sd.rec", return_value=fake_samples), \
             patch("backend.voice.voice_service.sd.wait"):
            res = service.test_microphone(device=0, duration_seconds=0.5)

        assert res["success"] is True
        assert res["signal_detected"] is True
        assert res["peak_level"] > 0.1
        # Ensure no WAV files were written to tmp_path
        assert list(tmp_path.rglob("*.wav")) == []


# ==============================================================================
# 5. FastAPI Voice & Shortcut Endpoints + CSRF Security Tests
# ==============================================================================

class TestVoiceApiEndpointsAndSecurity:
    """Verify FastAPI /agent/voice/* endpoints and CSRF protection."""

    def test_voice_status_and_config_endpoints(self, monkeypatch, tmp_path):
        monkeypatch.setenv("VOXPILOT_CONFIG_DIR", str(tmp_path / "config"))
        monkeypatch.setenv("VOXPILOT_ENABLE_HOTKEY", "0")

        with TestClient(app) as client:
            status_res = client.get("/agent/voice/status")
            assert status_res.status_code == 200
            data = status_res.json()
            assert "is_active" in data
            assert "phase" in data
            assert "settings" in data
            assert "shortcut_status" in data
            assert "components" in data

            cfg_res = client.get("/agent/voice/config")
            assert cfg_res.status_code == 200
            assert "settings" in cfg_res.json()

            voices_res = client.get("/agent/voice/voices")
            assert voices_res.status_code == 200
            assert len(voices_res.json()["voices"]) >= 1

    def test_shortcut_endpoint_rejects_ctrl_alt_esc(self, monkeypatch, tmp_path):
        monkeypatch.setenv("VOXPILOT_CONFIG_DIR", str(tmp_path / "config"))
        monkeypatch.setenv("VOXPILOT_ENABLE_HOTKEY", "0")

        with TestClient(app) as client:
            res = client.post(
                "/agent/voice/shortcut",
                json={"shortcut": "Ctrl+Alt+Escape", "enabled": True},
            )
            assert res.status_code == 400
            assert "reserved for the Global Emergency Stop" in res.json()["detail"]

    def test_csrf_protection_blocks_untrusted_origins_on_voice_endpoints(self):
        untrusted_headers = {"Origin": "https://malicious-site.example.com"}
        with TestClient(app) as client:
            for endpoint, payload in [
                ("/agent/voice/start", {}),
                ("/agent/voice/stop", {}),
                ("/agent/voice/toggle", {}),
                ("/agent/voice/config", {"tts_enabled": False}),
                ("/agent/voice/shortcut", {"shortcut": "Ctrl+Shift+V"}),
                ("/agent/voice/microphones/test", {"duration_seconds": 0.2}),
            ]:
                resp = client.post(endpoint, json=payload, headers=untrusted_headers)
                assert resp.status_code == 403, f"Expected 403 for {endpoint}, got {resp.status_code}"
