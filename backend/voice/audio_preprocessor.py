"""
VoxPilot - Audio Preprocessor Module
Provides controlled software gain normalization, clipping prevention, and signal diagnostics.
"""

from __future__ import annotations

import os
import logging
from typing import Optional, Tuple, Dict, Any
import numpy as np

logger = logging.getLogger(__name__)

# Constants and Defaults
DEFAULT_ENABLED = True
DEFAULT_TARGET_PEAK_DBFS = -3.0
DEFAULT_MAX_GAIN_DB = 20.0
DEFAULT_MIN_PEAK_DBFS = -45.0
DEFAULT_HEADROOM_DB = 0.5
MIN_LINEAR_FLOOR = 1e-7
SILENCE_DBFS = -120.0


class AudioPreprocessorError(Exception):
    """Raised when an audio preprocessing or normalization operation fails."""
    pass


class AudioPreprocessor:
    """Performs controlled software gain normalization on audio signals before ASR processing."""

    def __init__(
        self,
        enabled: Optional[bool] = None,
        target_peak_dbfs: Optional[float] = None,
        max_gain_db: Optional[float] = None,
        min_peak_dbfs: Optional[float] = None,
        headroom_db: float = DEFAULT_HEADROOM_DB,
    ) -> None:
        """Initialize the AudioPreprocessor with configurable normalization parameters.

        Args:
            enabled: Whether normalization is active. If None, reads VOXPILOT_AUDIO_NORMALIZATION_ENABLED (default: True).
            target_peak_dbfs: Desired peak level in dBFS (default: -3.0 dBFS).
            max_gain_db: Maximum allowable amplification in dB (default: 20.0 dB).
            min_peak_dbfs: Silence threshold in dBFS below which audio is not amplified (default: -45.0 dBFS).
            headroom_db: Tolerance in dB around target where normalization is bypassed (default: 0.5 dB).
        """
        # Load from environment or use defaults
        if enabled is None:
            env_val = os.environ.get("VOXPILOT_AUDIO_NORMALIZATION_ENABLED", "").strip().lower()
            self.enabled = env_val not in ("false", "0", "no") if env_val else DEFAULT_ENABLED
        else:
            self.enabled = bool(enabled)

        if target_peak_dbfs is None:
            try:
                self.target_peak_dbfs = float(os.environ.get("VOXPILOT_AUDIO_TARGET_PEAK_DBFS", DEFAULT_TARGET_PEAK_DBFS))
            except ValueError:
                self.target_peak_dbfs = DEFAULT_TARGET_PEAK_DBFS
        else:
            self.target_peak_dbfs = float(target_peak_dbfs)

        if max_gain_db is None:
            try:
                self.max_gain_db = float(os.environ.get("VOXPILOT_AUDIO_MAX_GAIN_DB", DEFAULT_MAX_GAIN_DB))
            except ValueError:
                self.max_gain_db = DEFAULT_MAX_GAIN_DB
        else:
            self.max_gain_db = float(max_gain_db)

        if min_peak_dbfs is None:
            try:
                self.min_peak_dbfs = float(os.environ.get("VOXPILOT_AUDIO_MIN_PEAK_DBFS", DEFAULT_MIN_PEAK_DBFS))
            except ValueError:
                self.min_peak_dbfs = DEFAULT_MIN_PEAK_DBFS
        else:
            self.min_peak_dbfs = float(min_peak_dbfs)

        self.headroom_db = float(headroom_db)
        self._validate_parameters()

    def _validate_parameters(self) -> None:
        """Validate parameter ranges to avoid invalid audio transformations."""
        if self.target_peak_dbfs > 0.0:
            raise AudioPreprocessorError(
                f"target_peak_dbfs must be <= 0.0 dBFS to prevent digital clipping, got {self.target_peak_dbfs}"
            )
        if self.max_gain_db < 0.0:
            raise AudioPreprocessorError(
                f"max_gain_db must be non-negative, got {self.max_gain_db}"
            )
        if self.min_peak_dbfs >= self.target_peak_dbfs:
            raise AudioPreprocessorError(
                f"min_peak_dbfs ({self.min_peak_dbfs}) must be lower than target_peak_dbfs ({self.target_peak_dbfs})"
            )

    @staticmethod
    def linear_to_dbfs(value: float) -> float:
        """Convert a linear amplitude value into decibels relative to full scale (dBFS).

        Args:
            value: Peak or RMS linear amplitude (typically 0.0 to 1.0).

        Returns:
            float: Level in dBFS (e.g. 1.0 -> 0.0 dBFS, 0.5 -> -6.02 dBFS, 0 -> -120.0 dBFS).
        """
        if value <= MIN_LINEAR_FLOOR:
            return SILENCE_DBFS
        return float(20.0 * np.log10(value))

    @staticmethod
    def dbfs_to_linear(dbfs: float) -> float:
        """Convert a level in dBFS into linear amplitude.

        Args:
            dbfs: Level in decibels relative to full scale.

        Returns:
            float: Linear amplitude ratio (e.g. 0.0 dBFS -> 1.0, -3.0 dBFS -> ~0.7079).
        """
        return float(10.0 ** (dbfs / 20.0))

    @staticmethod
    def get_peak_amplitude(audio: np.ndarray) -> float:
        """Measure peak absolute amplitude of an audio array safely.

        Args:
            audio: NumPy array of audio samples.

        Returns:
            float: Maximum absolute sample value, or 0.0 if empty.
        """
        if not isinstance(audio, np.ndarray) or audio.size == 0:
            return 0.0
        # Filter non-finite values if present
        clean = np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0)
        return float(np.max(np.abs(clean)))

    @staticmethod
    def get_rms(audio: np.ndarray) -> float:
        """Measure Root Mean Square (RMS) energy of an audio array safely.

        Args:
            audio: NumPy array of audio samples.

        Returns:
            float: RMS value, or 0.0 if empty.
        """
        if not isinstance(audio, np.ndarray) or audio.size == 0:
            return 0.0
        clean = np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0)
        return float(np.sqrt(np.mean(clean ** 2)))

    def calculate_gain_db(self, audio: np.ndarray) -> float:
        """Calculate the controlled gain in decibels that would be applied to the audio.

        Args:
            audio: NumPy array of audio samples.

        Returns:
            float: Gain in dB (0.0 if disabled, silent, or within headroom).
        """
        if not self.enabled:
            return 0.0

        peak = self.get_peak_amplitude(audio)
        if peak <= MIN_LINEAR_FLOOR:
            return 0.0

        current_dbfs = self.linear_to_dbfs(peak)

        # Silence / ambient noise floor protection
        if current_dbfs < self.min_peak_dbfs:
            logger.debug(
                "Audio peak %.2f dBFS is below silence threshold %.2f dBFS; no gain applied.",
                current_dbfs,
                self.min_peak_dbfs,
            )
            return 0.0

        # Already normalized within headroom
        if abs(current_dbfs - self.target_peak_dbfs) <= self.headroom_db:
            return 0.0

        # Calculate needed gain
        needed_gain_db = self.target_peak_dbfs - current_dbfs

        # Limit upward amplification to max_gain_db
        if needed_gain_db > self.max_gain_db:
            return self.max_gain_db

        return needed_gain_db

    def process_with_metadata(self, audio: np.ndarray) -> Tuple[np.ndarray, Dict[str, Any]]:
        """Normalize audio and return both the processed array and a diagnostic dictionary.

        Args:
            audio: Input NumPy array (float32, 1D mono or 2D stereo).

        Returns:
            tuple[np.ndarray, dict]: (processed_audio, metadata_dictionary)
        """
        if not isinstance(audio, np.ndarray):
            raise AudioPreprocessorError("Audio input must be a NumPy array.")

        if audio.size == 0:
            return np.empty(0, dtype=np.float32), {
                "original_peak_dbfs": SILENCE_DBFS,
                "normalized_peak_dbfs": SILENCE_DBFS,
                "gain_db": 0.0,
                "gain_linear": 1.0,
                "is_silent": True,
                "clipping_prevented": False,
            }

        # Ensure float32 copy without mutating caller's original buffer
        clean_audio = np.nan_to_num(audio, copy=True, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)

        # Downmix stereo to mono if needed to preserve 1D mono contract
        if clean_audio.ndim > 1:
            clean_audio = np.mean(clean_audio, axis=1)

        original_peak = self.get_peak_amplitude(clean_audio)
        original_rms = self.get_rms(clean_audio)
        original_peak_dbfs = self.linear_to_dbfs(original_peak)
        original_rms_dbfs = self.linear_to_dbfs(original_rms)

        is_silent = (original_peak <= MIN_LINEAR_FLOOR) or (original_peak_dbfs < self.min_peak_dbfs)

        if not self.enabled or is_silent:
            return clean_audio, {
                "original_peak": original_peak,
                "original_peak_dbfs": original_peak_dbfs,
                "original_rms": original_rms,
                "original_rms_dbfs": original_rms_dbfs,
                "normalized_peak": original_peak,
                "normalized_peak_dbfs": original_peak_dbfs,
                "normalized_rms": original_rms,
                "normalized_rms_dbfs": original_rms_dbfs,
                "gain_db": 0.0,
                "gain_linear": 1.0,
                "is_silent": is_silent,
                "clipping_prevented": False,
            }

        gain_db = self.calculate_gain_db(clean_audio)
        gain_linear = self.dbfs_to_linear(gain_db)

        if abs(gain_db) < 1e-4 or abs(gain_linear - 1.0) < 1e-4:
            return clean_audio, {
                "original_peak": original_peak,
                "original_peak_dbfs": original_peak_dbfs,
                "original_rms": original_rms,
                "original_rms_dbfs": original_rms_dbfs,
                "normalized_peak": original_peak,
                "normalized_peak_dbfs": original_peak_dbfs,
                "normalized_rms": original_rms,
                "normalized_rms_dbfs": original_rms_dbfs,
                "gain_db": 0.0,
                "gain_linear": 1.0,
                "is_silent": False,
                "clipping_prevented": False,
            }

        # Apply gain
        amplified = clean_audio * np.float32(gain_linear)

        # Clipping protection
        pre_clip_peak = self.get_peak_amplitude(amplified)
        clipping_prevented = pre_clip_peak > 1.0
        normalized_audio = np.clip(amplified, -1.0, 1.0).astype(np.float32)

        norm_peak = self.get_peak_amplitude(normalized_audio)
        norm_rms = self.get_rms(normalized_audio)

        return normalized_audio, {
            "original_peak": original_peak,
            "original_peak_dbfs": original_peak_dbfs,
            "original_rms": original_rms,
            "original_rms_dbfs": original_rms_dbfs,
            "normalized_peak": norm_peak,
            "normalized_peak_dbfs": self.linear_to_dbfs(norm_peak),
            "normalized_rms": norm_rms,
            "normalized_rms_dbfs": self.linear_to_dbfs(norm_rms),
            "gain_db": gain_db,
            "gain_linear": gain_linear,
            "is_silent": False,
            "clipping_prevented": clipping_prevented,
        }

    def process(self, audio: np.ndarray) -> np.ndarray:
        """Normalize audio and return the 1D float32 normalized array.

        Args:
            audio: Input NumPy array.

        Returns:
            np.ndarray: Normalized 1D float32 array.
        """
        processed_audio, _ = self.process_with_metadata(audio)
        return processed_audio

