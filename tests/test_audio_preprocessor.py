"""
Unit tests for the VoxPilot Audio Preprocessor.
Verifies controlled gain normalization, clipping prevention, silence handling,
parameter validation, and data format preservation.
"""

import numpy as np
import pytest

from backend.voice.audio_preprocessor import (
    AudioPreprocessor,
    AudioPreprocessorError,
    DEFAULT_TARGET_PEAK_DBFS,
    DEFAULT_MAX_GAIN_DB,
    DEFAULT_MIN_PEAK_DBFS,
    SILENCE_DBFS,
)


def test_linear_and_dbfs_conversions():
    """Verify conversion accuracy between linear amplitude and dBFS."""
    preprocessor = AudioPreprocessor()

    # 1.0 is 0 dBFS
    assert np.isclose(preprocessor.linear_to_dbfs(1.0), 0.0, atol=1e-3)
    assert np.isclose(preprocessor.dbfs_to_linear(0.0), 1.0, atol=1e-3)

    # 0.5 is approx -6.02 dBFS
    assert np.isclose(preprocessor.linear_to_dbfs(0.5), -6.0206, atol=1e-2)
    assert np.isclose(preprocessor.dbfs_to_linear(-6.0206), 0.5, atol=1e-2)

    # -3 dBFS is approx 0.7079 linear
    assert np.isclose(preprocessor.dbfs_to_linear(-3.0), 0.707945, atol=1e-3)
    assert np.isclose(preprocessor.linear_to_dbfs(0.707945), -3.0, atol=1e-2)

    # Zero or near-zero returns SILENCE_DBFS
    assert preprocessor.linear_to_dbfs(0.0) == SILENCE_DBFS
    assert preprocessor.linear_to_dbfs(1e-9) == SILENCE_DBFS


def test_parameter_validation():
    """Verify that invalid parameter combinations raise AudioPreprocessorError."""
    # Target peak > 0 dBFS would cause clipping
    with pytest.raises(AudioPreprocessorError):
        AudioPreprocessor(target_peak_dbfs=1.0)

    # Negative max gain
    with pytest.raises(AudioPreprocessorError):
        AudioPreprocessor(max_gain_db=-5.0)

    # min_peak_dbfs >= target_peak_dbfs
    with pytest.raises(AudioPreprocessorError):
        AudioPreprocessor(min_peak_dbfs=-2.0, target_peak_dbfs=-3.0)


def test_quiet_audio_amplified_to_target():
    """Verify quiet audio (e.g. -17 dBFS like the diagnostic recording) is amplified toward -3 dBFS."""
    preprocessor = AudioPreprocessor(target_peak_dbfs=-3.0, max_gain_db=20.0)

    # Create synthetic quiet sine wave peaking at 0.137146 (~ -17.26 dBFS)
    t = np.linspace(0, 1.0, 16000, endpoint=False)
    quiet_audio = (0.137146 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)

    normalized, meta = preprocessor.process_with_metadata(quiet_audio)

    # Peak should now be close to -3 dBFS (~0.7079 linear)
    expected_peak = preprocessor.dbfs_to_linear(-3.0)
    assert np.isclose(meta["normalized_peak"], expected_peak, atol=1e-2)
    assert np.isclose(meta["normalized_peak_dbfs"], -3.0, atol=0.2)
    assert np.isclose(meta["gain_db"], 14.26, atol=0.2)
    assert not meta["is_silent"]


def test_already_normalized_audio_unmodified():
    """Verify audio already at or near -3 dBFS is not unnecessarily amplified."""
    preprocessor = AudioPreprocessor(target_peak_dbfs=-3.0, headroom_db=0.5)

    # Audio peaking at 0.70 (~ -3.1 dBFS, within 0.5 dB headroom)
    t = np.linspace(0, 1.0, 16000, endpoint=False)
    audio = (0.70 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)

    normalized, meta = preprocessor.process_with_metadata(audio)

    assert meta["gain_db"] == 0.0
    assert np.array_equal(normalized, audio)


def test_silent_audio_remains_silent():
    """Verify completely silent audio (all zeros) remains unchanged with 0 gain."""
    preprocessor = AudioPreprocessor()
    silent_audio = np.zeros(16000, dtype=np.float32)

    normalized, meta = preprocessor.process_with_metadata(silent_audio)

    assert np.array_equal(normalized, silent_audio)
    assert meta["is_silent"] is True
    assert meta["gain_db"] == 0.0
    assert meta["normalized_peak"] == 0.0


def test_empty_audio_handled_safely():
    """Verify empty array returns an empty float32 array safely."""
    preprocessor = AudioPreprocessor()
    empty_audio = np.empty(0, dtype=np.float32)

    normalized = preprocessor.process(empty_audio)

    assert isinstance(normalized, np.ndarray)
    assert normalized.size == 0
    assert normalized.dtype == np.float32


def test_very_quiet_audio_below_threshold_not_amplified():
    """Verify audio below min_peak_dbfs (ambient room noise) is not amplified into false speech."""
    preprocessor = AudioPreprocessor(min_peak_dbfs=-45.0)

    # Ambient noise peaking at -55 dBFS (~0.00177)
    t = np.linspace(0, 1.0, 16000, endpoint=False)
    ambient_noise = (0.00177 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)

    normalized, meta = preprocessor.process_with_metadata(ambient_noise)

    assert meta["is_silent"] is True
    assert meta["gain_db"] == 0.0
    assert np.array_equal(normalized, ambient_noise)


def test_max_gain_limit_respected():
    """Verify amplification never exceeds max_gain_db even if target peak is not yet reached."""
    # Quiet audio at -35 dBFS needing +32 dB to reach -3 dBFS, but max_gain is capped at 12 dB
    preprocessor = AudioPreprocessor(target_peak_dbfs=-3.0, max_gain_db=12.0, min_peak_dbfs=-40.0)

    t = np.linspace(0, 1.0, 16000, endpoint=False)
    current_linear = preprocessor.dbfs_to_linear(-35.0)
    audio = (current_linear * np.sin(2 * np.pi * 440 * t)).astype(np.float32)

    normalized, meta = preprocessor.process_with_metadata(audio)

    # Gain applied must be capped exactly at 12.0 dB
    assert np.isclose(meta["gain_db"], 12.0, atol=1e-3)
    # Output peak should be -35 + 12 = -23 dBFS
    assert np.isclose(meta["normalized_peak_dbfs"], -23.0, atol=0.2)


def test_clipping_protection():
    """Verify output never exceeds [-1.0, 1.0] even under extreme signals."""
    preprocessor = AudioPreprocessor(target_peak_dbfs=-3.0)

    # Signal with a spike exceeding 1.0
    t = np.linspace(0, 1.0, 16000, endpoint=False)
    audio = np.sin(2 * np.pi * 440 * t).astype(np.float32)
    audio[100] = 5.0
    audio[200] = -5.0

    normalized = preprocessor.process(audio)

    assert np.max(normalized) <= 1.0
    assert np.min(normalized) >= -1.0


def test_nan_and_infinity_handled_safely():
    """Verify non-finite numbers (NaN, Inf) are safely sanitized to finite values."""
    preprocessor = AudioPreprocessor()
    audio = np.array([0.1, np.nan, 0.2, np.inf, -np.inf, 0.1], dtype=np.float32)

    normalized = preprocessor.process(audio)

    assert np.all(np.isfinite(normalized))
    assert normalized[1] == 0.0
    assert normalized[3] == 0.0
    assert normalized[4] == 0.0


def test_output_format_and_duration_preservation():
    """Verify output data type is float32 and sample count matches input."""
    preprocessor = AudioPreprocessor()
    num_samples = 32000
    t = np.linspace(0, 2.0, num_samples, endpoint=False)
    audio = (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)

    normalized = preprocessor.process(audio)

    assert normalized.dtype == np.float32
    assert len(normalized) == num_samples
    assert normalized.ndim == 1


def test_stereo_downmix_to_mono():
    """Verify 2D stereo array is downmixed to 1D mono float32."""
    preprocessor = AudioPreprocessor()
    t = np.linspace(0, 1.0, 16000, endpoint=False)
    left = (0.1 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    right = (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    stereo = np.stack([left, right], axis=1)

    normalized = preprocessor.process(stereo)

    assert normalized.ndim == 1
    assert len(normalized) == 16000
    assert normalized.dtype == np.float32


def test_disabled_normalization():
    """Verify normalization can be disabled via configuration."""
    preprocessor = AudioPreprocessor(enabled=False)
    t = np.linspace(0, 1.0, 16000, endpoint=False)
    audio = (0.1 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)

    normalized, meta = preprocessor.process_with_metadata(audio)

    assert meta["gain_db"] == 0.0
    assert np.array_equal(normalized, audio)


def test_does_not_mutate_input_buffer():
    """Verify original input array is not modified in-place."""
    preprocessor = AudioPreprocessor(target_peak_dbfs=-3.0)
    t = np.linspace(0, 1.0, 16000, endpoint=False)
    audio = (0.1 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    audio_copy = audio.copy()

    _ = preprocessor.process(audio)

    assert np.array_equal(audio, audio_copy)

