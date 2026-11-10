"""
Tests for Configurable Microphone Selection in VoxPilot.
Covers device discovery, default selection, name/index resolution, error handling,
and resampling validation.
"""

import math
import numpy as np
import pytest
import sounddevice as sd
import scipy.signal

from backend.voice.audio_recorder import AudioRecorder, AudioRecorderError
from backend.voice.voice_pipeline import VoicePipeline


def test_get_input_devices_structure():
    """Verify get_input_devices returns formatted list with required metadata."""
    devices = AudioRecorder.get_input_devices()
    assert isinstance(devices, list)

    if devices:
        dev = devices[0]
        assert "index" in dev
        assert "name" in dev
        assert "host_api" in dev
        assert "channels" in dev
        assert "default_sample_rate" in dev
        assert dev["channels"] > 0
        assert dev["default_sample_rate"] > 0


def test_list_microphones_output(capsys):
    """Verify list_microphones prints device information without error."""
    recorder = AudioRecorder()
    recorder.list_microphones()

    captured = capsys.readouterr()
    assert "Available microphones:" in captured.out


def test_resolve_default_microphone():
    """Verify resolving None, empty string, or 'default' points to a valid input device."""
    devices = AudioRecorder.get_input_devices()
    if not devices:
        pytest.skip("No audio input devices present in environment.")

    # None
    idx_none, dev_none = AudioRecorder.resolve_device(None)
    assert isinstance(idx_none, int)
    assert dev_none["channels"] > 0

    # Empty string
    idx_empty, dev_empty = AudioRecorder.resolve_device("")
    assert idx_empty == idx_none

    # 'default'
    idx_def, dev_def = AudioRecorder.resolve_device("default")
    assert idx_def == idx_none


def test_resolve_microphone_by_index():
    """Verify resolving device by numeric index and numeric string."""
    devices = AudioRecorder.get_input_devices()
    if not devices:
        pytest.skip("No audio input devices present in environment.")

    target = devices[0]
    target_idx = target["index"]

    # As int
    idx, dev = AudioRecorder.resolve_device(target_idx)
    assert idx == target_idx
    assert dev["name"] == target["name"]

    # As string representation of int
    idx_str, dev_str = AudioRecorder.resolve_device(str(target_idx))
    assert idx_str == target_idx
    assert dev_str["name"] == target["name"]


def test_resolve_microphone_by_name_substring():
    """Verify resolving device by case-insensitive name substring."""
    devices = AudioRecorder.get_input_devices()
    if not devices:
        pytest.skip("No audio input devices present in environment.")

    target = devices[0]
    # Pick a unique part of the device name
    name_part = target["name"].split()[0].lower()

    idx, dev = AudioRecorder.resolve_device(name_part)
    assert isinstance(idx, int)
    assert name_part in dev["name"].lower()


def test_resolve_invalid_microphone_raises_error():
    """Verify attempting to resolve a non-existent device raises AudioRecorderError."""
    # Invalid index
    with pytest.raises(AudioRecorderError) as exc_info:
        AudioRecorder.resolve_device(99999)
    assert "99999 is not a valid input device" in str(exc_info.value)

    # Invalid name
    with pytest.raises(AudioRecorderError) as exc_info:
        AudioRecorder.resolve_device("NonExistentMicrophone_XYZ_12345")
    assert "No microphone matching" in str(exc_info.value)


def test_resampling_conversion_logic():
    """Verify mathematical resampling from native hardware rates (e.g. 48kHz, 44.1kHz) to 16kHz."""
    target_sr = 16000

    for native_sr in [48000, 44100]:
        duration = 1.0  # 1 second
        # Generate 1 second of stereo audio at native sample rate
        t = np.linspace(0, duration, int(duration * native_sr), endpoint=False)
        left_ch = np.sin(2 * np.pi * 440 * t).astype(np.float32)
        right_ch = np.sin(2 * np.pi * 440 * t).astype(np.float32)
        stereo_audio = np.stack([left_ch, right_ch], axis=1)

        # Convert stereo to mono
        mono_audio = np.mean(stereo_audio, axis=1)
        assert mono_audio.ndim == 1

        # Resample to 16000 Hz using polyphase filter
        gcd = math.gcd(native_sr, target_sr)
        up = target_sr // gcd
        down = native_sr // gcd
        resampled = scipy.signal.resample_poly(mono_audio, up, down).astype(np.float32)

        # Verify output duration and format
        expected_samples = int(duration * target_sr)
        assert len(resampled) == expected_samples
        assert resampled.dtype == np.float32


def test_voice_pipeline_respects_env_device(monkeypatch):
    """Verify VoicePipeline picks up VOXPILOT_MICROPHONE_DEVICE from environment."""
    devices = AudioRecorder.get_input_devices()
    if not devices:
        pytest.skip("No audio input devices present in environment.")

    target = devices[0]
    monkeypatch.setenv("VOXPILOT_MICROPHONE_DEVICE", str(target["index"]))

    pipeline = VoicePipeline()
    assert pipeline.recorder.device == str(target["index"])
    info = pipeline.recorder.get_selected_device_info()
    assert info["index"] == target["index"]


def test_voice_pipeline_explicit_device_overrides_env(monkeypatch):
    """Verify an explicit parameter to VoicePipeline overrides the environment variable."""
    devices = AudioRecorder.get_input_devices()
    if len(devices) < 2:
        pytest.skip("Need at least 2 input devices to test override.")

    dev_env = devices[0]
    dev_explicit = devices[1]

    monkeypatch.setenv("VOXPILOT_MICROPHONE_DEVICE", str(dev_env["index"]))

    pipeline = VoicePipeline(microphone_device=dev_explicit["index"])
    assert pipeline.recorder.device == dev_explicit["index"]
    info = pipeline.recorder.get_selected_device_info()
    assert info["index"] == dev_explicit["index"]

