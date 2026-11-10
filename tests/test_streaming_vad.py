"""
Unit tests for Streaming Voice Activity Detection (VAD) and Dynamic Endpoint Detection in VoxPilot.
Validates real-time WebRTC VAD state machine, onset/offset detection, pre-roll buffering,
timeout handling, click rejection, output format, and AudioRecorder integration.
"""

from unittest.mock import MagicMock, patch
import queue
import numpy as np
import pytest

from backend.voice.streaming_vad import (
    StreamingVAD,
    StreamingVADError,
    DEFAULT_STREAMING_VAD_ENABLED,
    DEFAULT_FRAME_DURATION_MS,
    DEFAULT_AGGRESSIVENESS,
    DEFAULT_END_SILENCE_MS,
    DEFAULT_PRE_ROLL_MS,
    DEFAULT_MIN_SPEECH_MS,
    DEFAULT_MAX_RECORDING_SECONDS,
    DEFAULT_START_TIMEOUT_SECONDS,
)
from backend.voice.audio_recorder import AudioRecorder, AudioRecorderError
from backend.voice.voice_pipeline import VoicePipeline


def _generate_synthetic_speech(duration_s: float, sample_rate: int = 16000) -> np.ndarray:
    """Generate harmonic sine wave simulating speech formants."""
    t = np.linspace(0, duration_s, int(sample_rate * duration_s), endpoint=False)
    # Combine 200 Hz and 400 Hz to trigger WebRTC VAD human voice filter
    speech = 0.4 * np.sin(2 * np.pi * 200 * t) + 0.3 * np.sin(2 * np.pi * 400 * t)
    return speech.astype(np.float32)


def _generate_synthetic_silence(duration_s: float, sample_rate: int = 16000) -> np.ndarray:
    """Generate pure digital silence."""
    return np.zeros(int(sample_rate * duration_s), dtype=np.float32)


def test_streaming_vad_default_configuration():
    """Verify default parameters match the approved specifications."""
    vad = StreamingVAD()

    assert vad.enabled is DEFAULT_STREAMING_VAD_ENABLED
    assert vad.frame_duration_ms == DEFAULT_FRAME_DURATION_MS
    assert vad.aggressiveness == DEFAULT_AGGRESSIVENESS
    assert vad.end_silence_ms == DEFAULT_END_SILENCE_MS
    assert vad.pre_roll_ms == DEFAULT_PRE_ROLL_MS
    assert vad.min_speech_ms == DEFAULT_MIN_SPEECH_MS
    assert vad.max_recording_seconds == DEFAULT_MAX_RECORDING_SECONDS
    assert vad.start_timeout_seconds == DEFAULT_START_TIMEOUT_SECONDS
    assert vad.sample_rate == 16000
    assert vad.samples_per_frame == 480
    assert vad.state == StreamingVAD.STATE_WAITING


def test_streaming_vad_parameter_validation():
    """Verify parameter boundaries and invalid inputs raise StreamingVADError."""
    # Invalid frame durations (must be 10, 20, or 30 ms)
    with pytest.raises(StreamingVADError):
        StreamingVAD(frame_duration_ms=15)
    with pytest.raises(StreamingVADError):
        StreamingVAD(frame_duration_ms=50)

    # Invalid aggressiveness (must be 0, 1, 2, or 3)
    with pytest.raises(StreamingVADError):
        StreamingVAD(aggressiveness=-1)
    with pytest.raises(StreamingVADError):
        StreamingVAD(aggressiveness=4)

    # Invalid sample rates (must be 8000, 16000, 32000, or 48000)
    with pytest.raises(StreamingVADError):
        StreamingVAD(sample_rate=44100)
    with pytest.raises(StreamingVADError):
        StreamingVAD(sample_rate=22050)

    # Non-positive end silence or negative durations
    with pytest.raises(StreamingVADError):
        StreamingVAD(end_silence_ms=0)
    with pytest.raises(StreamingVADError):
        StreamingVAD(pre_roll_ms=-50)
    with pytest.raises(StreamingVADError):
        StreamingVAD(min_speech_ms=-10)
    with pytest.raises(StreamingVADError):
        StreamingVAD(max_recording_seconds=0.0)
    with pytest.raises(StreamingVADError):
        StreamingVAD(start_timeout_seconds=-1.0)


def test_speech_onset_and_endpoint_detection():
    """Verify speech onset transitions state to recording and trailing silence terminates."""
    sr = 16000
    vad = StreamingVAD(sample_rate=sr, pre_roll_ms=300, end_silence_ms=600, min_speech_ms=300)

    lead_silence = _generate_synthetic_silence(0.5, sr)
    speech = _generate_synthetic_speech(1.0, sr)
    trail_silence = _generate_synthetic_silence(0.8, sr)

    audio = np.concatenate([lead_silence, speech, trail_silence])
    keep_going = vad.feed_samples(audio)

    assert keep_going is False
    assert vad.is_finished() is True
    assert vad.state == StreamingVAD.STATE_COMPLETED

    recorded = vad.get_audio()
    assert len(recorded) > 0
    # Recorded audio must contain pre-roll (300ms) + speech (1.0s) + end silence (~600ms)
    expected_min_samples = int(sr * (0.3 + 1.0 + 0.5))
    assert len(recorded) >= expected_min_samples


def test_leading_silence_and_pre_roll_preservation():
    """Verify pre-roll buffer preserves the audio immediately preceding speech onset."""
    sr = 16000
    pre_roll_ms = 300
    vad = StreamingVAD(sample_rate=sr, pre_roll_ms=pre_roll_ms, end_silence_ms=500)

    # 300ms pre-speech window with low non-speech ambient offset (0.005)
    pre_speech_window = np.full(int(sr * 0.3), 0.005, dtype=np.float32)
    speech = _generate_synthetic_speech(0.6, sr)
    trail = _generate_synthetic_silence(0.6, sr)

    audio = np.concatenate([pre_speech_window, speech, trail])
    vad.feed_samples(audio)

    recorded = vad.get_audio()
    assert len(recorded) > 0
    # First samples in recorded audio should come from pre_speech_window (0.005 amplitude)
    assert np.isclose(recorded[0], 0.005, atol=1e-4)


def test_trailing_silence_preservation():
    """Verify trailing silence up to end_silence_ms is retained in the recorded audio."""
    sr = 16000
    end_silence_ms = 600
    vad = StreamingVAD(sample_rate=sr, end_silence_ms=end_silence_ms)

    speech = _generate_synthetic_speech(0.8, sr)
    trail = _generate_synthetic_silence(1.0, sr)

    vad.feed_samples(np.concatenate([speech, trail]))
    recorded = vad.get_audio()

    assert vad.state == StreamingVAD.STATE_COMPLETED
    # The end of recorded audio should be silence
    trailing_samples = int(sr * (end_silence_ms / 1000))
    tail = recorded[-trailing_samples:]
    assert np.all(tail == 0.0)


def test_brief_inter_word_pauses_do_not_terminate():
    """Verify brief silence pauses between words do not prematurely conclude recording."""
    sr = 16000
    # Set end silence threshold to 800ms
    vad = StreamingVAD(sample_rate=sr, end_silence_ms=800)

    word1 = _generate_synthetic_speech(0.6, sr)
    inter_word_pause = _generate_synthetic_silence(0.3, sr)  # 300ms pause < 800ms threshold
    word2 = _generate_synthetic_speech(0.6, sr)
    final_silence = _generate_synthetic_silence(1.0, sr)    # 1000ms >= 800ms threshold

    # Feed up to the pause
    cont1 = vad.feed_samples(np.concatenate([word1, inter_word_pause]))
    assert cont1 is True
    assert vad.state == StreamingVAD.STATE_RECORDING

    # Feed second word and final silence
    cont2 = vad.feed_samples(np.concatenate([word2, final_silence]))
    assert cont2 is False
    assert vad.state == StreamingVAD.STATE_COMPLETED

    recorded = vad.get_audio()
    # Entire utterance including both words and pause must be captured
    total_expected_s = 0.6 + 0.3 + 0.6 + 0.8  # ~2.3s
    assert len(recorded) >= int(sr * 2.0)


def test_no_speech_start_timeout():
    """Verify silence exceeding start_timeout_seconds terminates with empty audio."""
    sr = 16000
    timeout_s = 2.0  # Test with short 2s timeout
    vad = StreamingVAD(sample_rate=sr, start_timeout_seconds=timeout_s)

    silence = _generate_synthetic_silence(2.5, sr)
    keep_going = vad.feed_samples(silence)

    assert keep_going is False
    assert vad.is_finished() is True
    assert vad.state == StreamingVAD.STATE_TIMEOUT
    assert len(vad.get_audio()) == 0


def test_max_recording_duration_enforced():
    """Verify recording is capped when max_recording_seconds is reached."""
    sr = 16000
    max_s = 3.0
    vad = StreamingVAD(sample_rate=sr, max_recording_seconds=max_s, end_silence_ms=2000)

    # Continuous speech for 4.0s (exceeds 3.0s ceiling)
    long_speech = _generate_synthetic_speech(4.0, sr)
    keep_going = vad.feed_samples(long_speech)

    assert keep_going is False
    assert vad.is_finished() is True
    assert vad.state == StreamingVAD.STATE_MAX_DURATION

    recorded = vad.get_audio()
    # Max duration enforced (~3.0s of frames)
    assert abs(len(recorded) / sr - max_s) < 0.1


def test_minimum_speech_duration_filters_clicks():
    """Verify brief accidental clicks/pops below min_speech_ms are discarded as noise."""
    sr = 16000
    vad = StreamingVAD(sample_rate=sr, min_speech_ms=300, end_silence_ms=500)

    # 60ms click (only 2 frames, below 300ms min_speech) followed by 800ms silence
    click = np.full(int(sr * 0.06), 0.8, dtype=np.float32)
    silence = _generate_synthetic_silence(0.8, sr)

    audio = np.concatenate([click, silence])
    vad.feed_samples(audio)

    # The click should have been rejected, reverting state back to WAITING
    assert vad.state == StreamingVAD.STATE_WAITING
    assert len(vad.get_audio()) == 0


def test_empty_and_varying_chunk_sizes_handled_safely():
    """Verify internal buffering handles arbitrary chunk sizes without frame misalignment."""
    sr = 16000
    vad = StreamingVAD(sample_rate=sr, end_silence_ms=500)

    # Feed empty arrays safely
    assert vad.feed_samples(np.array([], dtype=np.float32)) is True

    # Feed audio in irregular chunk sizes: 100, 250, 512, 1024
    speech = _generate_synthetic_speech(1.0, sr)
    trail = _generate_synthetic_silence(0.7, sr)
    full = np.concatenate([speech, trail])

    idx = 0
    chunk_sizes = [100, 250, 512, 1024, 777]
    c_idx = 0
    while idx < len(full) and not vad.is_finished():
        size = chunk_sizes[c_idx % len(chunk_sizes)]
        chunk = full[idx : idx + size]
        vad.feed_samples(chunk)
        idx += size
        c_idx += 1

    assert vad.state == StreamingVAD.STATE_COMPLETED
    assert len(vad.get_audio()) > 0


def test_output_format_is_16khz_mono_float32():
    """Verify get_audio() outputs 1D float32 numpy array."""
    sr = 16000
    vad = StreamingVAD(sample_rate=sr, end_silence_ms=400)
    speech = _generate_synthetic_speech(0.5, sr)
    trail = _generate_synthetic_silence(0.5, sr)

    vad.feed_samples(np.concatenate([speech, trail]))
    audio = vad.get_audio()

    assert isinstance(audio, np.ndarray)
    assert audio.dtype == np.float32
    assert audio.ndim == 1


def test_audio_recorder_fixed_duration_still_works():
    """Verify backward compatibility: existing fixed-duration record() remains functional."""
    recorder = AudioRecorder(sample_rate=16000, channels=1)

    mock_rec_data = np.zeros((16000, 1), dtype=np.float32)

    with patch("sounddevice.rec", return_value=mock_rec_data) as mock_rec, \
         patch("sounddevice.wait") as mock_wait, \
         patch.object(recorder, "resolve_device", return_value=(0, {"index": 0, "name": "Mock Mic", "host_api": "WASAPI"})):

        audio = recorder.record(duration=1)

        mock_rec.assert_called_once()
        mock_wait.assert_called_once()
        assert isinstance(audio, np.ndarray)
        assert audio.ndim == 1


def test_audio_recorder_record_until_silence_streaming_disabled():
    """Verify record_until_silence() delegates to record() when streaming VAD is disabled."""
    recorder = AudioRecorder(streaming_vad_enabled=False)

    with patch.object(recorder, "record", return_value=np.zeros(16000, dtype=np.float32)) as mock_fixed_record:
        result = recorder.record_until_silence(max_duration=4)

        mock_fixed_record.assert_called_once_with(duration=4)
        assert len(result) == 16000


def test_audio_recorder_record_until_silence_with_stream():
    """Verify record_until_silence() captures and returns audio via sounddevice.InputStream."""
    recorder = AudioRecorder(streaming_vad_enabled=True, sample_rate=16000)

    sr = 16000
    speech_frames = _generate_synthetic_speech(0.6, sr)
    trail_silence = _generate_synthetic_silence(0.9, sr)
    test_audio = np.concatenate([speech_frames, trail_silence])

    # Mock sounddevice InputStream to put frames into the callback queue
    class MockInputStream:
        def __init__(self, *args, **kwargs):
            self.callback = kwargs.get("callback")
            self.blocksize = kwargs.get("blocksize", 480)

        def __enter__(self):
            # Simulate streaming blocks to callback
            idx = 0
            while idx < len(test_audio):
                chunk = test_audio[idx : idx + self.blocksize]
                if len(chunk) < self.blocksize:
                    chunk = np.pad(chunk, (0, self.blocksize - len(chunk)))
                self.callback(chunk.reshape(-1, 1), self.blocksize, None, None)
                idx += self.blocksize
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    with patch("sounddevice.InputStream", side_effect=MockInputStream), \
         patch.object(recorder, "resolve_device", return_value=(0, {"index": 0, "name": "Mock Mic", "host_api": "WASAPI"})), \
         patch.object(recorder, "_determine_recording_parameters", return_value=(16000, 1, False)):

        result = recorder.record_until_silence()

        assert isinstance(result, np.ndarray)
        assert result.ndim == 1
        assert len(result) > 0


def test_voice_pipeline_streaming_vad_integration():
    """Verify VoicePipeline respects use_streaming_vad flag and delegates listen() appropriately."""
    # When streaming VAD is active
    pipeline_streaming = VoicePipeline(use_streaming_vad=True)
    assert pipeline_streaming.use_streaming_vad is True
    assert pipeline_streaming.recorder.streaming_vad_enabled is True

    with patch.object(pipeline_streaming.recorder, "record_until_silence", return_value=np.ones(16000, dtype=np.float32)) as mock_dyn:
        audio = pipeline_streaming.listen()
        mock_dyn.assert_called_once()
        assert len(audio) == 16000

    # When streaming VAD is disabled
    pipeline_fixed = VoicePipeline(use_streaming_vad=False)
    assert pipeline_fixed.use_streaming_vad is False
    assert pipeline_fixed.recorder.streaming_vad_enabled is False

    with patch.object(pipeline_fixed.recorder, "record", return_value=np.ones(16000, dtype=np.float32)) as mock_fixed:
        audio = pipeline_fixed.listen(duration=3)
        mock_fixed.assert_called_once_with(duration=3)
        assert len(audio) == 16000


def test_audio_recorder_stream_disconnect_raises_error():
    """Verify AudioRecorderError is raised if input stream becomes inactive unexpectedly."""
    recorder = AudioRecorder(streaming_vad_enabled=True, sample_rate=16000)

    class MockDeadStream:
        def __init__(self, *args, **kwargs):
            self.active = False  # Stream aborted / disconnected immediately

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    with patch("sounddevice.InputStream", side_effect=MockDeadStream), \
         patch.object(recorder, "resolve_device", return_value=(0, {"index": 0, "name": "Mock Mic", "host_api": "WASAPI"})), \
         patch.object(recorder, "_determine_recording_parameters", return_value=(16000, 1, False)):

        with pytest.raises(AudioRecorderError):
            recorder.record_until_silence()


def test_env_variables_configure_streaming_vad(monkeypatch):
    """Verify VOXPILOT_STREAMING_VAD_* environment variables configure the detector."""
    monkeypatch.setenv("VOXPILOT_STREAMING_VAD_ENABLED", "true")
    monkeypatch.setenv("VOXPILOT_STREAMING_VAD_FRAME_DURATION_MS", "20")
    monkeypatch.setenv("VOXPILOT_STREAMING_VAD_AGGRESSIVENESS", "3")
    monkeypatch.setenv("VOXPILOT_STREAMING_VAD_END_SILENCE_MS", "700")
    monkeypatch.setenv("VOXPILOT_STREAMING_VAD_PRE_ROLL_MS", "200")
    monkeypatch.setenv("VOXPILOT_STREAMING_VAD_MIN_SPEECH_MS", "250")
    monkeypatch.setenv("VOXPILOT_STREAMING_VAD_MAX_RECORDING_SECONDS", "25")
    monkeypatch.setenv("VOXPILOT_STREAMING_VAD_START_TIMEOUT_SECONDS", "8")

    vad = StreamingVAD()

    assert vad.enabled is True
    assert vad.frame_duration_ms == 20
    assert vad.aggressiveness == 3
    assert vad.end_silence_ms == 700
    assert vad.pre_roll_ms == 200
    assert vad.min_speech_ms == 250
    assert vad.max_recording_seconds == 25.0
    assert vad.start_timeout_seconds == 8.0
    assert vad.samples_per_frame == int(16000 * 20 / 1000)
