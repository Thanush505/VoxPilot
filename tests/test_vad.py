"""
Unit tests for Faster-Whisper integrated Silero Voice Activity Detection (VAD) in VoxPilot.
Validates parameter passing, enable/disable toggle, boundary validation, and pipeline integration.
"""

from unittest.mock import MagicMock
from pathlib import Path
import numpy as np
import pytest
import soundfile as sf

from backend.voice.speech_to_text import (
    SpeechToText,
    SpeechToTextError,
    DEFAULT_VAD_ENABLED,
    DEFAULT_VAD_THRESHOLD,
    DEFAULT_VAD_MIN_SPEECH_DURATION_MS,
    DEFAULT_VAD_MIN_SILENCE_DURATION_MS,
    DEFAULT_VAD_SPEECH_PAD_MS,
)
from backend.voice.audio_preprocessor import AudioPreprocessor


def test_vad_default_configuration():
    """Verify default VAD parameters match the approved VoxPilot specifications."""
    stt = SpeechToText()

    assert stt.vad_enabled is DEFAULT_VAD_ENABLED
    assert stt.vad_threshold == DEFAULT_VAD_THRESHOLD
    assert stt.vad_min_speech_duration_ms == DEFAULT_VAD_MIN_SPEECH_DURATION_MS
    assert stt.vad_min_silence_duration_ms == DEFAULT_VAD_MIN_SILENCE_DURATION_MS
    assert stt.vad_speech_pad_ms == DEFAULT_VAD_SPEECH_PAD_MS

    params = stt.get_vad_parameters()
    assert params == {
        "threshold": 0.5,
        "min_speech_duration_ms": 250,
        "min_silence_duration_ms": 500,
        "speech_pad_ms": 200,
    }


def test_vad_enabled_passed_to_whisper():
    """Verify vad_filter=True and VAD parameters dict reach WhisperModel.transcribe."""
    stt = SpeechToText(vad_enabled=True)

    mock_model = MagicMock()
    mock_segment = MagicMock()
    mock_segment.text = "Hello world"
    mock_info = MagicMock()
    mock_info.language = "en"
    mock_info.language_probability = 0.99
    mock_model.transcribe.return_value = ([mock_segment], mock_info)

    stt.model = mock_model

    dummy_audio = np.zeros(16000, dtype=np.float32)
    result = stt.transcribe_audio(dummy_audio)

    assert result["text"] == "Hello world"
    mock_model.transcribe.assert_called_once()
    _, kwargs = mock_model.transcribe.call_args

    assert kwargs["vad_filter"] is True
    assert kwargs["vad_parameters"] == {
        "threshold": 0.5,
        "min_speech_duration_ms": 250,
        "min_silence_duration_ms": 500,
        "speech_pad_ms": 200,
    }


def test_vad_disabled_passed_to_whisper():
    """Verify vad_filter=False and vad_parameters=None when VAD is disabled."""
    stt = SpeechToText(vad_enabled=False)

    mock_model = MagicMock()
    mock_segment = MagicMock()
    mock_segment.text = "Testing disabled"
    mock_info = MagicMock()
    mock_info.language = "en"
    mock_info.language_probability = 0.95
    mock_model.transcribe.return_value = ([mock_segment], mock_info)

    stt.model = mock_model

    dummy_audio = np.zeros(16000, dtype=np.float32)
    result = stt.transcribe_audio(dummy_audio)

    assert result["text"] == "Testing disabled"
    mock_model.transcribe.assert_called_once()
    _, kwargs = mock_model.transcribe.call_args

    assert kwargs["vad_filter"] is False
    assert kwargs["vad_parameters"] is None
    assert stt.get_vad_parameters() is None


def test_custom_vad_parameters_reach_whisper():
    """Verify custom VAD threshold, durations, and padding reach transcribe()."""
    stt = SpeechToText(
        vad_enabled=True,
        vad_threshold=0.65,
        vad_min_speech_duration_ms=300,
        vad_min_silence_duration_ms=600,
        vad_speech_pad_ms=250,
    )

    mock_model = MagicMock()
    mock_info = MagicMock()
    mock_info.language = "en"
    mock_info.language_probability = 1.0
    mock_model.transcribe.return_value = ([], mock_info)
    stt.model = mock_model

    dummy_audio = np.zeros(16000, dtype=np.float32)
    _ = stt.transcribe_audio(dummy_audio)

    _, kwargs = mock_model.transcribe.call_args
    assert kwargs["vad_parameters"] == {
        "threshold": 0.65,
        "min_speech_duration_ms": 300,
        "min_silence_duration_ms": 600,
        "speech_pad_ms": 250,
    }


def test_invalid_vad_threshold_raises_error():
    """Verify threshold outside (0.0, 1.0) raises SpeechToTextError."""
    with pytest.raises(SpeechToTextError):
        SpeechToText(vad_threshold=0.0)

    with pytest.raises(SpeechToTextError):
        SpeechToText(vad_threshold=1.0)

    with pytest.raises(SpeechToTextError):
        SpeechToText(vad_threshold=1.5)

    with pytest.raises(SpeechToTextError):
        SpeechToText(vad_threshold=-0.2)


def test_invalid_vad_durations_raise_error():
    """Verify negative durations or padding raise SpeechToTextError."""
    with pytest.raises(SpeechToTextError):
        SpeechToText(vad_min_speech_duration_ms=-10)

    with pytest.raises(SpeechToTextError):
        SpeechToText(vad_min_silence_duration_ms=-1)

    with pytest.raises(SpeechToTextError):
        SpeechToText(vad_speech_pad_ms=-50)


def test_audio_normalization_precedes_vad():
    """Verify audio preprocessing and normalization happens before VAD transcription."""
    mock_preprocessor = MagicMock(spec=AudioPreprocessor)
    normalized_mock = np.ones(16000, dtype=np.float32) * 0.707
    mock_preprocessor.process.return_value = normalized_mock

    stt = SpeechToText(preprocessor=mock_preprocessor, vad_enabled=True)

    mock_model = MagicMock()
    mock_info = MagicMock()
    mock_info.language = "en"
    mock_info.language_probability = 0.99
    mock_model.transcribe.return_value = ([], mock_info)
    stt.model = mock_model

    input_audio = np.ones(16000, dtype=np.float32) * 0.1
    _ = stt.transcribe_audio(input_audio)

    # Preprocessor must be invoked with input_audio
    mock_preprocessor.process.assert_called_once_with(input_audio)

    # Model transcribe must receive normalized_mock
    args, _ = mock_model.transcribe.call_args
    assert np.array_equal(args[0], normalized_mock)


def test_vad_applied_exactly_once():
    """Verify VAD and transcribe are invoked exactly once per call."""
    stt = SpeechToText(vad_enabled=True)
    mock_model = MagicMock()
    mock_info = MagicMock()
    mock_info.language = "en"
    mock_info.language_probability = 1.0
    mock_model.transcribe.return_value = ([], mock_info)
    stt.model = mock_model

    dummy_audio = np.zeros(16000, dtype=np.float32)
    _ = stt.transcribe_audio(dummy_audio)

    assert mock_model.transcribe.call_count == 1


def test_file_transcription_uses_configured_vad(tmp_path: Path):
    """Verify transcribe_file passes configured VAD settings to the model."""
    wav_path = tmp_path / "test_vad.wav"
    dummy_audio = np.zeros(16000, dtype=np.float32)
    sf.write(str(wav_path), dummy_audio, 16000)

    stt = SpeechToText(vad_enabled=True, vad_threshold=0.55)
    mock_model = MagicMock()
    mock_info = MagicMock()
    mock_info.language = "en"
    mock_info.language_probability = 0.9
    mock_model.transcribe.return_value = ([], mock_info)
    stt.model = mock_model

    result = stt.transcribe_file(str(wav_path))

    assert "text" in result
    assert "language" in result
    mock_model.transcribe.assert_called_once()
    _, kwargs = mock_model.transcribe.call_args
    assert kwargs["vad_filter"] is True
    assert kwargs["vad_parameters"]["threshold"] == 0.55


def test_env_variables_configure_vad(monkeypatch):
    """Verify environment variables properly configure VAD parameters."""
    monkeypatch.setenv("VOXPILOT_VAD_ENABLED", "false")
    monkeypatch.setenv("VOXPILOT_VAD_THRESHOLD", "0.6")
    monkeypatch.setenv("VOXPILOT_VAD_MIN_SPEECH_DURATION_MS", "350")
    monkeypatch.setenv("VOXPILOT_VAD_MIN_SILENCE_DURATION_MS", "700")
    monkeypatch.setenv("VOXPILOT_VAD_SPEECH_PAD_MS", "150")

    stt = SpeechToText()

    assert stt.vad_enabled is False
    assert stt.vad_threshold == 0.6
    assert stt.vad_min_speech_duration_ms == 350
    assert stt.vad_min_silence_duration_ms == 700
    assert stt.vad_speech_pad_ms == 150

