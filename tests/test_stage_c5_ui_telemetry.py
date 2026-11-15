"""
Stage C.5 UI & Live Voice Telemetry Tests.

Verifies:
1. AudioRecorder.compute_audio_telemetry() produces zeroed bands for silence and
   deterministic normalized RMS/Peak/16-band FFT magnitudes for real audio signals.
2. update_live_telemetry(), get_live_telemetry(), and reset_live_telemetry() work cleanly
   on both float32 and int16 audio chunks without allocating extra hardware streams.
3. VoiceService.get_status() includes structured `audio_telemetry` across idle,
   active microphone recording, TTS speaking, and mock-recorder fallback states.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np

from backend.voice.audio_recorder import AudioRecorder
from backend.voice.voice_service import (
    PHASE_IDLE,
    PHASE_LISTENING,
    PHASE_SPEAKING,
    VoiceService,
    VoiceSettingsManager,
    resolve_piper_voice_path,
)


class TestAudioTelemetryComputation:
    def test_silence_returns_zero_telemetry(self):
        silence = np.zeros(1024, dtype=np.float32)
        rms, peak, bands = AudioRecorder.compute_audio_telemetry(silence, band_count=16)

        assert rms == 0.0
        assert peak == 0.0
        assert len(bands) == 16
        assert all(val == 0.0 for val in bands)

    def test_sine_wave_produces_normalized_fft_bands_and_levels(self):
        sr = 16000
        t = np.arange(512, dtype=np.float32) / sr
        # Low (220Hz) + mid/high (1800Hz) test tone
        tone = 0.25 * np.sin(2.0 * np.pi * 220.0 * t) + 0.35 * np.sin(2.0 * np.pi * 1800.0 * t)

        rms, peak, bands = AudioRecorder.compute_audio_telemetry(tone, band_count=16)

        assert 0.1 < rms <= 1.0
        assert 0.3 <= peak <= 1.0
        assert len(bands) == 16
        assert all(0.0 <= val <= 1.0 for val in bands)
        assert max(bands) > 0.1

    def test_live_telemetry_update_and_reset_supports_int16_chunks(self):
        recorder = AudioRecorder()
        t = np.arange(480, dtype=np.float32) / 16000.0
        pcm_int16 = (np.sin(2.0 * np.pi * 600.0 * t) * 16000.0).astype(np.int16)

        recorder.update_live_telemetry(pcm_int16, is_speech=True)
        snap = recorder.get_live_telemetry()
        assert snap["is_speech"] is True
        assert snap["rms_level"] > 0.0
        assert snap["peak_level"] > 0.0
        assert len(snap["bands"]) == 16
        assert max(snap["bands"]) > 0.0

        recorder.reset_live_telemetry()
        cleared = recorder.get_live_telemetry()
        assert cleared["rms_level"] == 0.0
        assert cleared["peak_level"] == 0.0
        assert cleared["is_speech"] is False
        assert cleared["bands"] == [0.0] * 16


class TestVoiceServiceAudioTelemetryExposure:
    @staticmethod
    def _make_mock_pipeline():
        pipeline = MagicMock()
        pipeline.recorder = AudioRecorder()
        pipeline.stt = MagicMock()
        pipeline.stt.model_size = "base"
        pipeline.stt.device = "cpu"
        pipeline.stt.compute_type = "int8"
        pipeline.stt.model = MagicMock()
        pipeline.tts = MagicMock()
        pipeline.tts.voice = MagicMock()
        pipeline.tts.model_path = resolve_piper_voice_path("en_US-lessac-medium.onnx")
        pipeline.current_conversation_id = None
        pipeline.last_agent_result = None
        return pipeline

    def test_idle_status_exposes_calm_audio_telemetry(self, tmp_path):
        mgr = VoiceSettingsManager(config_path=tmp_path / "voice_settings.json")
        pipeline = self._make_mock_pipeline()
        service = VoiceService(settings_manager=mgr, pipeline=pipeline, register_hotkey_on_init=False)

        status = service.get_status()
        telemetry = status["audio_telemetry"]
        assert telemetry["active"] is False
        assert telemetry["mode"] == "idle"
        assert telemetry["rms_level"] == 0.0
        assert telemetry["peak_level"] == 0.0
        assert telemetry["is_speech"] is False
        assert telemetry["bands"] == [0.0] * 16

    def test_active_recording_exposes_live_microphone_telemetry(self, tmp_path):
        mgr = VoiceSettingsManager(config_path=tmp_path / "voice_settings.json")
        pipeline = self._make_mock_pipeline()
        service = VoiceService(settings_manager=mgr, pipeline=pipeline, register_hotkey_on_init=False)

        t = np.arange(480, dtype=np.float32) / 16000.0
        pipeline.recorder.update_live_telemetry(0.4 * np.sin(2.0 * np.pi * 440.0 * t), is_speech=True)
        pipeline.recorder.is_recording = True
        service._is_active = True
        service._phase = PHASE_LISTENING

        status = service.get_status()
        telemetry = status["audio_telemetry"]
        assert telemetry["active"] is True
        assert telemetry["mode"] == "microphone"
        assert telemetry["is_speech"] is True
        assert telemetry["rms_level"] > 0.0
        assert max(telemetry["bands"]) > 0.0

    def test_speaking_phase_exposes_tts_telemetry_mode(self, tmp_path):
        mgr = VoiceSettingsManager(config_path=tmp_path / "voice_settings.json")
        pipeline = self._make_mock_pipeline()
        service = VoiceService(settings_manager=mgr, pipeline=pipeline, register_hotkey_on_init=False)

        service._is_active = True
        service._phase = PHASE_SPEAKING

        status = service.get_status()
        telemetry = status["audio_telemetry"]
        assert telemetry["active"] is True
        assert telemetry["mode"] == "tts"
        assert telemetry["rms_level"] == 0.0
        assert telemetry["bands"] == [0.0] * 16

    def test_handles_mock_recorder_without_get_live_telemetry(self, tmp_path):
        mgr = VoiceSettingsManager(config_path=tmp_path / "voice_settings.json")
        pipeline = MagicMock()
        pipeline.recorder = MagicMock(spec=["is_recording", "device", "aggressiveness", "frame_duration_ms", "end_silence_ms"])
        pipeline.recorder.is_recording = False
        pipeline.recorder.device = None
        pipeline.recorder.aggressiveness = 2
        pipeline.recorder.frame_duration_ms = 30
        pipeline.recorder.end_silence_ms = 900
        pipeline.stt = MagicMock(model_size="base", device="cpu", compute_type="int8", model=None)
        pipeline.tts = MagicMock(voice=None, model_path=resolve_piper_voice_path("en_US-lessac-medium.onnx"))

        service = VoiceService(settings_manager=mgr, pipeline=pipeline, register_hotkey_on_init=False)
        status = service.get_status()
        assert status["phase"] == PHASE_IDLE
        assert status["audio_telemetry"]["mode"] == "idle"
        assert len(status["audio_telemetry"]["bands"]) == 16
