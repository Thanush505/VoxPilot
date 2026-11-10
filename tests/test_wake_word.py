"""
Unit tests for Wake Word Detection (openWakeWord) in VoxPilot.
Validates initialization, parameter validation, custom model error handling,
detection scoring, cooldown enforcement, stream handover, and VoicePipeline integration.
"""

from unittest.mock import MagicMock, patch
import queue
import time
import numpy as np
import pytest

from backend.voice.wake_word import (
    WakeWordDetector,
    WakeWordError,
    DEFAULT_WAKE_WORD_ENABLED,
    DEFAULT_WAKE_WORD_MODEL,
    DEFAULT_WAKE_WORD_THRESHOLD,
    DEFAULT_WAKE_WORD_COOLDOWN_SECONDS,
    FRAME_SIZE_SAMPLES,
)
from backend.voice.audio_recorder import AudioRecorder
from backend.voice.voice_pipeline import VoicePipeline


def test_wake_word_default_configuration():
    """Verify default parameters match the approved specifications."""
    detector = WakeWordDetector()

    assert detector.enabled is DEFAULT_WAKE_WORD_ENABLED
    assert detector.model_path == DEFAULT_WAKE_WORD_MODEL
    assert detector.threshold == DEFAULT_WAKE_WORD_THRESHOLD
    assert detector.cooldown_seconds == DEFAULT_WAKE_WORD_COOLDOWN_SECONDS
    assert detector.sample_rate == 16000
    assert detector.model is None
    assert detector.last_detection_time == 0.0


def test_wake_word_parameter_validation():
    """Verify invalid parameters raise WakeWordError."""
    # Threshold outside (0.0, 1.0)
    with pytest.raises(WakeWordError):
        WakeWordDetector(threshold=0.0)
    with pytest.raises(WakeWordError):
        WakeWordDetector(threshold=1.0)
    with pytest.raises(WakeWordError):
        WakeWordDetector(threshold=-0.2)
    with pytest.raises(WakeWordError):
        WakeWordDetector(threshold=1.5)

    # Negative cooldown
    with pytest.raises(WakeWordError):
        WakeWordDetector(cooldown_seconds=-1.0)

    # Non-16kHz sample rate
    with pytest.raises(WakeWordError):
        WakeWordDetector(sample_rate=44100)
    with pytest.raises(WakeWordError):
        WakeWordDetector(sample_rate=8000)

    # Empty model path
    with pytest.raises(WakeWordError):
        WakeWordDetector(model_path="")


def test_missing_custom_model_raises_descriptive_error():
    """Verify missing custom model file (e.g. hey_voxpilot.onnx) raises descriptive WakeWordError."""
    detector = WakeWordDetector(model_path="models/wake_word/nonexistent_model.onnx")

    with pytest.raises(WakeWordError) as exc_info:
        detector.load_model()

    assert "Custom wake word model file not found" in str(exc_info.value)
    assert "Hey VoxPilot" in str(exc_info.value)


def test_detector_loads_model_successfully():
    """Verify load_model successfully initializes openwakeword.model.Model."""
    detector = WakeWordDetector(model_path="hey_jarvis_v0.1")

    mock_oww = MagicMock()
    with patch("backend.voice.wake_word.OWWModel", return_value=mock_oww):
        detector.load_model()

        assert detector.model is mock_oww
        assert detector.active_model_name == "hey_jarvis_v0.1"


def test_predict_frame_converts_float32_to_int16():
    """Verify predict_frame converts float32 NumPy arrays to int16 before model inference."""
    detector = WakeWordDetector()
    mock_model = MagicMock()
    mock_model.predict.return_value = {"hey_jarvis_v0.1": 0.0}
    detector.model = mock_model

    float_audio = np.full(FRAME_SIZE_SAMPLES, 0.5, dtype=np.float32)
    preds = detector.predict_frame(float_audio)

    mock_model.predict.assert_called_once()
    passed_array = mock_model.predict.call_args[0][0]
    assert passed_array.dtype == np.int16
    assert np.isclose(passed_array[0], int(0.5 * 32767), atol=2)
    assert preds == {"hey_jarvis_v0.1": 0.0}


def test_wake_word_detection_event_above_threshold():
    """Verify scores above threshold generate a structured detection event."""
    detector = WakeWordDetector(threshold=0.5)

    mock_model = MagicMock()
    # First frame 0.2 (no wake), second frame 0.85 (wake triggered!)
    mock_model.predict.side_effect = [
        {"hey_jarvis_v0.1": 0.2},
        {"hey_jarvis_v0.1": 0.85},
    ]
    detector.model = mock_model

    class MockStream:
        def __init__(self, *args, **kwargs):
            self.callback = kwargs.get("callback")
            self.active = True

        def __enter__(self):
            # Feed two audio blocks to callback
            self.callback(np.zeros((FRAME_SIZE_SAMPLES, 1), dtype=np.float32), FRAME_SIZE_SAMPLES, None, None)
            self.callback(np.zeros((FRAME_SIZE_SAMPLES, 1), dtype=np.float32), FRAME_SIZE_SAMPLES, None, None)
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            self.active = False

    with patch("sounddevice.InputStream", side_effect=MockStream), \
         patch("backend.voice.audio_recorder.AudioRecorder.resolve_device", return_value=(0, {"index": 0, "name": "Mock Mic", "host_api": "WASAPI"})):

        event = detector.listen_for_wake_word(timeout=2.0)

        assert event is not None
        assert event["detected"] is True
        assert event["model"] == "hey_jarvis_v0.1"
        assert event["score"] == 0.85
        assert event["timestamp"] > 0


def test_non_wake_audio_below_threshold_does_not_activate():
    """Verify audio below threshold times out without activation."""
    detector = WakeWordDetector(threshold=0.5)

    mock_model = MagicMock()
    mock_model.predict.return_value = {"hey_jarvis_v0.1": 0.3}  # Below 0.5
    detector.model = mock_model

    class MockStream:
        def __init__(self, *args, **kwargs):
            self.callback = kwargs.get("callback")
            self.active = True

        def __enter__(self):
            # Feed 3 quiet frames
            for _ in range(3):
                self.callback(np.zeros((FRAME_SIZE_SAMPLES, 1), dtype=np.float32), FRAME_SIZE_SAMPLES, None, None)
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            self.active = False

    with patch("sounddevice.InputStream", side_effect=MockStream), \
         patch("backend.voice.audio_recorder.AudioRecorder.resolve_device", return_value=(0, {"index": 0, "name": "Mock Mic", "host_api": "WASAPI"})):

        event = detector.listen_for_wake_word(timeout=0.1)

        assert event is None


def test_detection_threshold_boundary():
    """Verify boundary conditions: score == 0.49 fails, score == 0.50 activates."""
    detector = WakeWordDetector(threshold=0.5)
    mock_model = MagicMock()
    detector.model = mock_model

    class MockStreamFactory:
        def __init__(self, score_to_return):
            self.score = score_to_return

        def __call__(self, *args, **kwargs):
            callback = kwargs.get("callback")
            mock_model.predict.return_value = {"hey_jarvis_v0.1": self.score}

            class StreamInstance:
                active = True
                def __enter__(self):
                    callback(np.zeros((FRAME_SIZE_SAMPLES, 1), dtype=np.float32), FRAME_SIZE_SAMPLES, None, None)
                    return self
                def __exit__(self, *args):
                    self.active = False

            return StreamInstance()

    with patch("backend.voice.audio_recorder.AudioRecorder.resolve_device", return_value=(0, {"index": 0, "name": "Mock Mic", "host_api": "WASAPI"})):
        # 0.49 score
        with patch("sounddevice.InputStream", side_effect=MockStreamFactory(0.49)):
            res_fail = detector.listen_for_wake_word(timeout=0.05)
            assert res_fail is None

        # 0.50 score
        with patch("sounddevice.InputStream", side_effect=MockStreamFactory(0.50)):
            res_pass = detector.listen_for_wake_word(timeout=0.05)
            assert res_pass is not None
            assert res_pass["detected"] is True


def test_cooldown_prevents_immediate_duplicate_activation():
    """Verify cooldown period ignores detections within cooldown_seconds."""
    detector = WakeWordDetector(threshold=0.5, cooldown_seconds=2.0)
    detector.last_detection_time = time.time()  # Triggered right now!

    mock_model = MagicMock()
    mock_model.predict.return_value = {"hey_jarvis_v0.1": 0.9}  # High score
    detector.model = mock_model

    class MockStream:
        def __init__(self, *args, **kwargs):
            self.callback = kwargs.get("callback")
            self.active = True

        def __enter__(self):
            self.callback(np.zeros((FRAME_SIZE_SAMPLES, 1), dtype=np.float32), FRAME_SIZE_SAMPLES, None, None)
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            self.active = False

    with patch("sounddevice.InputStream", side_effect=MockStream), \
         patch("backend.voice.audio_recorder.AudioRecorder.resolve_device", return_value=(0, {"index": 0, "name": "Mock Mic", "host_api": "WASAPI"})):

        event = detector.listen_for_wake_word(timeout=0.1)

        # In cooldown: must NOT trigger!
        assert event is None


def test_detector_starts_and_stops_cleanly():
    """Verify stop() terminates listen_for_wake_word immediately."""
    detector = WakeWordDetector()
    detector.model = MagicMock()

    class MockLoopStream:
        def __init__(self, *args, **kwargs):
            self.callback = kwargs.get("callback")
            self.active = True

        def __enter__(self):
            # Request stop right as stream enters
            detector.stop()
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            self.active = False

    with patch("sounddevice.InputStream", side_effect=MockLoopStream), \
         patch("backend.voice.audio_recorder.AudioRecorder.resolve_device", return_value=(0, {"index": 0, "name": "Mock Mic", "host_api": "WASAPI"})):

        res = detector.listen_for_wake_word()
        assert res is None
        assert detector._is_listening is False


def test_microphone_handover_releases_stream_for_audiorecorder():
    """Verify wake-word detector releases sounddevice stream so AudioRecorder can record without conflict."""
    stream_active_log = []

    class TrackedStream:
        def __init__(self, *args, **kwargs):
            self.callback = kwargs.get("callback")
            self.active = False

        def __enter__(self):
            self.active = True
            stream_active_log.append("OPENED")
            # If callback is present (wake word stream), feed frame that triggers wake
            if self.callback:
                self.callback(np.zeros((FRAME_SIZE_SAMPLES, 1), dtype=np.float32), FRAME_SIZE_SAMPLES, None, None)
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            self.active = False
            stream_active_log.append("CLOSED")

    detector = WakeWordDetector(threshold=0.5)
    mock_model = MagicMock()
    mock_model.predict.return_value = {"hey_jarvis_v0.1": 0.8}
    detector.model = mock_model

    recorder = AudioRecorder(streaming_vad_enabled=False)

    with patch("sounddevice.InputStream", side_effect=TrackedStream), \
         patch("sounddevice.rec", return_value=np.zeros((16000, 1), dtype=np.float32)), \
         patch("sounddevice.wait"), \
         patch("backend.voice.audio_recorder.AudioRecorder.resolve_device", return_value=(0, {"index": 0, "name": "Mock Mic", "host_api": "WASAPI"})):

        # Step 1: Wake word monitors and triggers
        event = detector.listen_for_wake_word(timeout=1.0)
        assert event is not None
        assert event["detected"] is True
        # Verify stream was closed upon wake detection
        assert stream_active_log == ["OPENED", "CLOSED"]

        # Step 2: AudioRecorder acquires microphone immediately without collision
        audio = recorder.record(duration=1)
        assert len(audio) == 16000


def test_voice_pipeline_disabled_wake_word_mode():
    """Verify VoicePipeline defaults to wake word disabled and preserves normal conversation mode."""
    pipeline = VoicePipeline(enable_wake_word=False)

    assert pipeline.enable_wake_word is False
    assert pipeline.wake_detector is None

    # Calling wait_for_wake_word returns None safely
    assert pipeline.wait_for_wake_word() is None


def test_voice_pipeline_enabled_wake_word_mode():
    """Verify VoicePipeline correctly wires WakeWordDetector when enabled."""
    pipeline = VoicePipeline(enable_wake_word=True, wake_word_threshold=0.6)

    assert pipeline.enable_wake_word is True
    assert pipeline.wake_detector is not None
    assert pipeline.wake_detector.threshold == 0.6

    with patch.object(pipeline.wake_detector, "listen_for_wake_word", return_value={"detected": True, "model": "test", "score": 0.8}):
        event = pipeline.wait_for_wake_word(timeout=1.0)
        assert event["detected"] is True


def test_microphone_error_raises_wake_word_error():
    """Verify SoundDevice stream initialization errors raise WakeWordError."""
    detector = WakeWordDetector()
    detector.model = MagicMock()

    with patch("sounddevice.InputStream", side_effect=RuntimeError("Device busy")), \
         patch("backend.voice.audio_recorder.AudioRecorder.resolve_device", return_value=(0, {"index": 0, "name": "Mock Mic", "host_api": "WASAPI"})):

        with pytest.raises(WakeWordError) as exc_info:
            detector.listen_for_wake_word()

        assert "Microphone monitoring failed" in str(exc_info.value)


def test_env_variables_configure_wake_word(monkeypatch):
    """Verify environment variables configure wake word settings."""
    monkeypatch.setenv("VOXPILOT_WAKE_WORD_ENABLED", "true")
    monkeypatch.setenv("VOXPILOT_WAKE_WORD_MODEL", "alexa_v0.1")
    monkeypatch.setenv("VOXPILOT_WAKE_WORD_THRESHOLD", "0.65")
    monkeypatch.setenv("VOXPILOT_WAKE_WORD_COOLDOWN_SECONDS", "3.5")

    detector = WakeWordDetector()

    assert detector.enabled is True
    assert detector.model_path == "alexa_v0.1"
    assert detector.threshold == 0.65
    assert detector.cooldown_seconds == 3.5


def test_detector_reset_clears_state():
    """Verify reset clears listening flags and calls underlying model reset."""
    detector = WakeWordDetector()
    mock_model = MagicMock()
    detector.model = mock_model
    detector._stop_requested = True
    detector._is_listening = True

    detector.reset()

    assert detector._stop_requested is False
    assert detector._is_listening is False
    mock_model.reset.assert_called_once()

