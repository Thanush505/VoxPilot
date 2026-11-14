"""
VoxPilot - Phase 5B: Advanced Voice Experience Tests.

Covers:
- Push-to-talk start & stop lifecycle (idle -> listening -> transcribing -> processing -> speaking -> idle)
- Duplicate recording prevention (already_recording)
- Microphone unavailable / PortAudioError handling and stream cleanup
- Empty and silent audio recording handling (no agent execution, clean idle reset)
- Wake-word enable/disable persistence, cooldown enforcement, duplicate trigger suppression,
  and TTS echo suppression
- Speech-to-text failure and Piper TTS failure graceful recovery
- Speech interruption (interrupt_voice) transitioning through interrupted -> idle and halting TTS
- EmergencyStopManager integration blocking PTT/wake-word and halting active voice mode
- FastAPI /agent/voice/interrupt, /agent/voice/ptt/start, /agent/voice/ptt/stop, and
  /agent/voice/config (wake_word_enabled) endpoints
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.automation.emergency_stop import get_shared_emergency_stop_manager
from backend.main import app
from backend.voice.speech_to_text import SpeechToTextError
from backend.voice.text_to_speech import TextToSpeechError
from backend.voice.voice_service import (
    PHASE_ERROR,
    PHASE_IDLE,
    PHASE_INTERRUPTED,
    PHASE_LISTENING,
    PHASE_SPEAKING,
    VoiceService,
    VoiceSettingsManager,
)


def _make_service(tmp_path: Path) -> VoiceService:
    settings_path = tmp_path / "voice_settings.json"
    manager = VoiceSettingsManager(config_path=settings_path)
    pipeline = MagicMock()
    pipeline.recorder = MagicMock()
    pipeline.recorder.is_recording = False
    pipeline.recorder.device = None
    pipeline.stt = MagicMock()
    pipeline.tts = MagicMock()
    pipeline.wake_word = None
    pipeline.is_listening = False
    pipeline.is_speaking = False
    pipeline._initialized = True
    return VoiceService(pipeline=pipeline, settings_manager=manager)


@pytest.fixture(autouse=True)
def _reset_emergency_stop():
    mgr = get_shared_emergency_stop_manager()
    mgr.reset_emergency_stop()
    yield
    mgr.reset_emergency_stop()


class _FakeInputStream:
    def __init__(self, frames_to_emit=None, fail_on_start: Exception | None = None):
        self.frames_to_emit = list(frames_to_emit or [])
        self.fail_on_start = fail_on_start
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self):
        if self.fail_on_start is not None:
            raise self.fail_on_start
        self.started = True

    def read(self, _n):
        if self.frames_to_emit:
            return self.frames_to_emit.pop(0), False
        time.sleep(0.01)
        return np.zeros((0, 1), dtype=np.float32), False

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


# ------------------------------------------------------------------------------
# 1. Push-to-Talk Lifecycle & Error Handling
# ------------------------------------------------------------------------------

def test_ptt_full_lifecycle_transcribe_agent_tts(tmp_path: Path):
    svc = _make_service(tmp_path)
    speech_chunk = np.full((1024, 1), 0.2, dtype=np.float32)
    fake_stream = _FakeInputStream(frames_to_emit=[speech_chunk, speech_chunk])

    svc.pipeline.recognize.return_value = "check hardware status"
    svc.pipeline.generate_response.return_value = "Your CPU and GPU look healthy."
    svc.pipeline.last_agent_result = {
        "execution_status": "completed",
        "response": "Your CPU and GPU look healthy.",
        "conversation_id": "conv-ptt-1",
    }

    with patch("backend.voice.voice_service.sd.InputStream", return_value=fake_stream):
        start_res = svc.start_push_to_talk(conversation_id="conv-ptt-1")
        assert start_res["success"] is True
        assert start_res["status"] == "listening"
        assert svc.get_status()["is_ptt_active"] is True

        # Wait briefly so capture thread collects frames
        deadline = time.monotonic() + 1.0
        while len(svc._ptt_frames) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)

        stop_res = svc.stop_push_to_talk()

    assert stop_res["success"] is True
    assert stop_res["status"] == "completed"
    assert stop_res["transcript"] == "check hardware status"
    assert stop_res["response"] == "Your CPU and GPU look healthy."
    svc.pipeline.generate_response.assert_called_once_with("check hardware status", conversation_id="conv-ptt-1")
    svc.pipeline.speak.assert_called_once_with("Your CPU and GPU look healthy.", cleanup_temp_file=True)
    assert fake_stream.stopped and fake_stream.closed
    assert svc.get_status()["phase"] == PHASE_IDLE
    assert svc.get_status()["is_ptt_active"] is False
    assert svc._ptt_frames == []


def test_ptt_prevents_duplicate_recording(tmp_path: Path):
    svc = _make_service(tmp_path)
    fake_stream = _FakeInputStream(frames_to_emit=[np.full((512, 1), 0.1, dtype=np.float32)])

    with patch("backend.voice.voice_service.sd.InputStream", return_value=fake_stream):
        first = svc.start_push_to_talk()
        second = svc.start_push_to_talk()
        assert first["success"] is True
        assert second["success"] is False
        assert second["status"] == "already_recording"
        svc.interrupt_voice(reason="cleanup")


def test_ptt_handles_microphone_unavailable_gracefully(tmp_path: Path):
    svc = _make_service(tmp_path)
    failing_stream = _FakeInputStream(fail_on_start=RuntimeError("PortAudioError: No input device"))

    with patch("backend.voice.voice_service.sd.InputStream", return_value=failing_stream):
        svc.start_push_to_talk()
        deadline = time.monotonic() + 1.0
        while svc.get_status()["phase"] != PHASE_ERROR and time.monotonic() < deadline:
            time.sleep(0.01)

    status = svc.get_status()
    assert status["phase"] == PHASE_ERROR
    assert "Microphone error" in (status["last_error"] or "")
    assert svc.pipeline.recorder.is_recording is False


def test_ptt_empty_or_silent_audio_resets_to_idle(tmp_path: Path):
    svc = _make_service(tmp_path)
    silent_chunk = np.zeros((1024, 1), dtype=np.float32)
    fake_stream = _FakeInputStream(frames_to_emit=[silent_chunk, silent_chunk])

    with patch("backend.voice.voice_service.sd.InputStream", return_value=fake_stream):
        svc.start_push_to_talk()
        deadline = time.monotonic() + 1.0
        while len(svc._ptt_frames) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        res = svc.stop_push_to_talk()

    assert res["success"] is True
    assert res["status"] == "empty_audio"
    svc.pipeline.recognize.assert_not_called()
    assert svc.get_status()["phase"] == PHASE_IDLE


def test_ptt_handles_stt_and_tts_failures_gracefully(tmp_path: Path):
    svc = _make_service(tmp_path)
    speech_chunk = np.full((1024, 1), 0.25, dtype=np.float32)

    # 1) STT failure -> PHASE_ERROR
    svc.pipeline.recognize.side_effect = SpeechToTextError("whisper failed")
    with patch("backend.voice.voice_service.sd.InputStream", return_value=_FakeInputStream([speech_chunk, speech_chunk])):
        svc.start_push_to_talk()
        deadline = time.monotonic() + 1.0
        while len(svc._ptt_frames) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        res = svc.stop_push_to_talk()
    assert res["success"] is False
    assert res["status"] == "stt_error"
    assert svc.get_status()["phase"] == PHASE_ERROR

    # 2) TTS failure -> non-fatal, still returns agent response and resets to IDLE
    svc.pipeline.recognize.side_effect = None
    svc.pipeline.recognize.return_value = "hello voxpilot"
    svc.pipeline.generate_response.return_value = "Hello there!"
    svc.pipeline.last_agent_result = {
        "execution_status": "completed",
        "response": "Hello there!",
        "conversation_id": "conv-2",
    }
    svc.pipeline.speak.side_effect = TextToSpeechError("piper crashed")
    with patch("backend.voice.voice_service.sd.InputStream", return_value=_FakeInputStream([speech_chunk, speech_chunk])):
        svc.start_push_to_talk()
        deadline = time.monotonic() + 1.0
        while len(svc._ptt_frames) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        res2 = svc.stop_push_to_talk()
    assert res2["success"] is True
    assert res2["response"] == "Hello there!"
    assert svc.get_status()["phase"] == PHASE_IDLE


# ------------------------------------------------------------------------------
# 2. Wake-Word Persistence, Cooldown, Echo Suppression & Emergency Stop
# ------------------------------------------------------------------------------

def test_wake_word_settings_persistence(tmp_path: Path):
    svc = _make_service(tmp_path)
    assert svc.settings_manager.get_settings()["wake_word_enabled"] is False

    with patch.object(svc, "start_wake_word_listener", return_value=True) as mock_start, \
         patch.object(svc, "stop_wake_word_listener") as mock_stop:
        svc.update_config({"wake_word_enabled": True, "wake_word_cooldown_seconds": 3.5})
        mock_start.assert_called_once()

        # Reload from disk into fresh settings manager
        reloaded = VoiceSettingsManager(config_path=tmp_path / "voice_settings.json").get_settings()
        assert reloaded["wake_word_enabled"] is True
        assert reloaded["wake_word_cooldown_seconds"] == 3.5

        svc.update_config({"wake_word_enabled": False})
        mock_stop.assert_called()


def test_wake_word_cooldown_and_tts_echo_suppression(tmp_path: Path):
    svc = _make_service(tmp_path)
    svc.settings_manager.save({"wake_word_enabled": True, "wake_word_cooldown_seconds": 2.0, "tts_echo_cooldown_seconds": 1.5})

    # Simulate TTS just finished -> echo suppression blocks wake trigger
    svc._last_tts_finished_at = time.monotonic()
    assert svc._should_suppress_wake_trigger() is True

    # Simulate old TTS finish, test duplicate wake-word trigger cooldown
    svc._last_tts_finished_at = time.monotonic() - 10.0
    with patch.object(svc, "start_voice_mode") as mock_start_vm:
        accepted = svc._handle_wake_word_detection({"model": "hey_jarvis_v0.1", "score": 0.91})
        assert accepted is True
        mock_start_vm.assert_called_once_with(source="wake_word")

        # Immediate duplicate trigger within cooldown is suppressed
        duplicate = svc._handle_wake_word_detection({"model": "hey_jarvis_v0.1", "score": 0.95})
        assert duplicate is False


def test_speech_interruption_and_emergency_stop(tmp_path: Path):
    svc = _make_service(tmp_path)
    with svc._lock:
        svc._phase = PHASE_SPEAKING

    status = svc.interrupt_voice(reason="User clicked Stop Speaking")
    svc.pipeline.stop_speaking.assert_called_once()
    svc.pipeline.stop_listening.assert_called_once()
    assert status["phase"] == PHASE_IDLE
    events = status["events"]
    assert any(e["type"] == "voice_interrupted" for e in events)
    assert any(e["type"] == "phase_changed" and e["phase"] == PHASE_INTERRUPTED for e in events)

    # Emergency stop blocks PTT and wake-word
    mgr = get_shared_emergency_stop_manager()
    mgr.trigger_emergency_stop(reason="Test emergency stop")
    blocked = svc.start_push_to_talk()
    assert blocked["success"] is False
    assert blocked["status"] == "cancelled"
    assert svc._should_suppress_wake_trigger() is True


# ------------------------------------------------------------------------------
# 3. FastAPI Endpoints for Phase 5B
# ------------------------------------------------------------------------------

def test_fastapi_phase5b_voice_endpoints(tmp_path: Path):
    svc = _make_service(tmp_path)
    client = TestClient(app)

    with patch("backend.api.agent.get_shared_voice_service", return_value=svc), \
         patch("backend.voice.voice_service.sd.InputStream", return_value=_FakeInputStream([np.full((512, 1), 0.2, dtype=np.float32)])):
        r_cfg = client.post("/agent/voice/config", json={"wake_word_enabled": True, "wake_word_cooldown_seconds": 2.5})
        assert r_cfg.status_code == 200
        assert r_cfg.json()["settings"]["wake_word_enabled"] is True

        r_start = client.post("/agent/voice/ptt/start", json={"conversation_id": "conv-api"})
        assert r_start.status_code == 200
        assert r_start.json()["success"] is True
        assert r_start.json()["voice_status"]["is_ptt_active"] is True
        assert r_start.json()["voice_status"]["phase"] == PHASE_LISTENING

        r_int = client.post("/agent/voice/interrupt", json={"reason": "API interrupt test"})
        assert r_int.status_code == 200
        assert r_int.json()["phase"] == PHASE_IDLE
        assert r_int.json()["is_ptt_active"] is False

        r_stop = client.post("/agent/voice/ptt/stop")
        assert r_stop.status_code == 200
        assert r_stop.json()["voice_status"]["phase"] == PHASE_IDLE
