"""
Unit tests for the VoxPilot Voice Subsystem (AudioRecorder, SpeechToText, TextToSpeech, VoicePipeline).
"""

from pathlib import Path
import numpy as np
import pytest

from backend.voice.audio_recorder import AudioRecorder, AudioRecorderError
from backend.voice.speech_to_text import SpeechToText, SpeechToTextError
from backend.voice.text_to_speech import TextToSpeech, TextToSpeechError
from backend.voice.voice_pipeline import VoicePipeline


def test_audio_recorder_initialization():
    """Verify AudioRecorder initialization and parameter validation."""
    recorder = AudioRecorder(sample_rate=16000, channels=1)
    assert recorder.sample_rate == 16000
    assert recorder.channels == 1

    with pytest.raises(AudioRecorderError):
        recorder.record(duration=-1.0)


def test_audio_recorder_save_recording(tmp_path: Path):
    """Verify saving generated audio array to a WAV file."""
    recorder = AudioRecorder(sample_rate=16000, channels=1)
    dummy_audio = np.zeros(16000, dtype=np.float32)
    wav_file = tmp_path / "test_output.wav"

    recorder.save_recording(dummy_audio, str(wav_file))
    assert wav_file.is_file()
    assert wav_file.stat().st_size > 0


def test_speech_to_text_initialization():
    """Verify SpeechToText initialization and configuration defaults."""
    stt = SpeechToText(model_size="base", device="cpu", compute_type="int8", language="en")
    assert stt.model_size == "base"
    assert stt.device == "cpu"
    assert stt.compute_type == "int8"
    assert stt.language == "en"
    assert stt.model is None


def test_speech_to_text_validation():
    """Verify input validation for speech-to-text."""
    stt = SpeechToText(model_size="base", device="cpu", compute_type="int8")

    with pytest.raises(SpeechToTextError):
        stt.transcribe_audio("not_a_numpy_array")

    with pytest.raises(SpeechToTextError):
        stt.transcribe_audio(np.array([], dtype=np.float32))


def test_text_to_speech_initialization():
    """Verify TextToSpeech initialization and model path discovery."""
    tts = TextToSpeech(model_path="models/piper/en_US-lessac-medium.onnx")
    assert tts.model_path == Path("models/piper/en_US-lessac-medium.onnx")
    assert tts.voice is None


def test_text_to_speech_validation():
    """Verify TTS text input validation and unloaded model guards."""
    tts = TextToSpeech(model_path="models/piper/en_US-lessac-medium.onnx")

    with pytest.raises(TextToSpeechError):
        tts.synthesize("")

    with pytest.raises(TextToSpeechError):
        tts.synthesize("Hello world")  # Not loaded yet


def test_voice_pipeline_initialization():
    """Verify VoicePipeline container composition."""
    pipeline = VoicePipeline(
        whisper_model="base",
        piper_model="models/piper/en_US-lessac-medium.onnx"
    )
    assert pipeline.recorder.sample_rate == 16000
    assert pipeline.stt.model_size == "base"
    assert pipeline.tts.model_path == Path("models/piper/en_US-lessac-medium.onnx")
