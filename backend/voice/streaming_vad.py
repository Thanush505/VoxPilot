"""
VoxPilot - Streaming Voice Activity Detection (VAD) Module
Powered by WebRTC VAD for real-time speech onset, continuation, and dynamic endpoint detection.
"""

from __future__ import annotations

import os
import logging
import collections
from typing import Optional, List, Deque
import numpy as np

try:
    import webrtcvad
except ImportError:
    webrtcvad = None

logger = logging.getLogger(__name__)

# Default Streaming VAD Configuration Constants
DEFAULT_STREAMING_VAD_ENABLED = True
DEFAULT_FRAME_DURATION_MS = 30
DEFAULT_AGGRESSIVENESS = 2
DEFAULT_END_SILENCE_MS = 800
DEFAULT_PRE_ROLL_MS = 300
DEFAULT_MIN_SPEECH_MS = 300
DEFAULT_MAX_RECORDING_SECONDS = 30.0
DEFAULT_START_TIMEOUT_SECONDS = 10.0


class StreamingVADError(Exception):
    """Raised when streaming VAD configuration or audio frame processing fails."""
    pass


class StreamingVAD:
    """Real-time streaming speech detector and dynamic endpointing state machine.

    Uses WebRTC VAD to evaluate short audio frames (10, 20, or 30 ms), tracks voice
    onset, maintains a rolling pre-roll buffer to prevent cutting off initial consonants,
    bridges inter-word pauses, and stops recording when sustained silence is detected.
    """

    STATE_WAITING = "waiting"
    STATE_RECORDING = "recording"
    STATE_COMPLETED = "completed"
    STATE_TIMEOUT = "timeout"
    STATE_MAX_DURATION = "max_duration"

    VALID_FRAME_DURATIONS = (10, 20, 30)
    VALID_AGGRESSIVENESS = (0, 1, 2, 3)
    VALID_SAMPLE_RATES = (8000, 16000, 32000, 48000)

    def __init__(
        self,
        enabled: Optional[bool] = None,
        frame_duration_ms: Optional[int] = None,
        aggressiveness: Optional[int] = None,
        end_silence_ms: Optional[int] = None,
        pre_roll_ms: Optional[int] = None,
        min_speech_ms: Optional[int] = None,
        max_recording_seconds: Optional[float] = None,
        start_timeout_seconds: Optional[float] = None,
        sample_rate: int = 16000,
    ) -> None:
        """Initialize StreamingVAD with configurable or environment-backed parameters.

        Args:
            enabled: Whether streaming VAD is active. Default: True.
            frame_duration_ms: Frame length in ms (10, 20, or 30). Default: 30.
            aggressiveness: WebRTC VAD mode (0=least aggressive, 3=most aggressive). Default: 2.
            end_silence_ms: Silence duration in ms to trigger end of speech. Default: 800.
            pre_roll_ms: Audio kept prior to speech onset in ms. Default: 300.
            min_speech_ms: Minimum speech duration required to qualify as utterance. Default: 300.
            max_recording_seconds: Hard ceiling on recording time in seconds. Default: 30.0.
            start_timeout_seconds: Max seconds to wait for initial speech. Default: 10.0.
            sample_rate: Audio sampling frequency in Hz (8000, 16000, 32000, or 48000). Default: 16000.
        """
        # Load from environment or use defaults
        if enabled is None:
            env_val = os.environ.get("VOXPILOT_STREAMING_VAD_ENABLED", "").strip().lower()
            self.enabled = env_val not in ("false", "0", "no") if env_val else DEFAULT_STREAMING_VAD_ENABLED
        else:
            self.enabled = bool(enabled)

        if frame_duration_ms is None:
            raw = os.environ.get("VOXPILOT_STREAMING_VAD_FRAME_DURATION_MS") or os.environ.get("VOXPILOT_VAD_FRAME_DURATION_MS")
            try:
                self.frame_duration_ms = int(raw) if raw is not None else DEFAULT_FRAME_DURATION_MS
            except ValueError:
                self.frame_duration_ms = DEFAULT_FRAME_DURATION_MS
        else:
            self.frame_duration_ms = int(frame_duration_ms)

        if aggressiveness is None:
            raw = os.environ.get("VOXPILOT_STREAMING_VAD_AGGRESSIVENESS") or os.environ.get("VOXPILOT_VAD_AGGRESSIVENESS")
            try:
                self.aggressiveness = int(raw) if raw is not None else DEFAULT_AGGRESSIVENESS
            except ValueError:
                self.aggressiveness = DEFAULT_AGGRESSIVENESS
        else:
            self.aggressiveness = int(aggressiveness)

        if end_silence_ms is None:
            raw = os.environ.get("VOXPILOT_STREAMING_VAD_END_SILENCE_MS") or os.environ.get("VOXPILOT_VAD_END_SILENCE_MS")
            try:
                self.end_silence_ms = int(raw) if raw is not None else DEFAULT_END_SILENCE_MS
            except ValueError:
                self.end_silence_ms = DEFAULT_END_SILENCE_MS
        else:
            self.end_silence_ms = int(end_silence_ms)

        if pre_roll_ms is None:
            raw = os.environ.get("VOXPILOT_STREAMING_VAD_PRE_ROLL_MS") or os.environ.get("VOXPILOT_VAD_PRE_ROLL_MS")
            try:
                self.pre_roll_ms = int(raw) if raw is not None else DEFAULT_PRE_ROLL_MS
            except ValueError:
                self.pre_roll_ms = DEFAULT_PRE_ROLL_MS
        else:
            self.pre_roll_ms = int(pre_roll_ms)

        if min_speech_ms is None:
            raw = os.environ.get("VOXPILOT_STREAMING_VAD_MIN_SPEECH_MS") or os.environ.get("VOXPILOT_VAD_MIN_SPEECH_MS")
            try:
                self.min_speech_ms = int(raw) if raw is not None else DEFAULT_MIN_SPEECH_MS
            except ValueError:
                self.min_speech_ms = DEFAULT_MIN_SPEECH_MS
        else:
            self.min_speech_ms = int(min_speech_ms)

        if max_recording_seconds is None:
            raw = os.environ.get("VOXPILOT_STREAMING_VAD_MAX_RECORDING_SECONDS") or os.environ.get("VOXPILOT_VAD_MAX_RECORDING_SECONDS")
            try:
                self.max_recording_seconds = float(raw) if raw is not None else DEFAULT_MAX_RECORDING_SECONDS
            except ValueError:
                self.max_recording_seconds = DEFAULT_MAX_RECORDING_SECONDS
        else:
            self.max_recording_seconds = float(max_recording_seconds)

        if start_timeout_seconds is None:
            raw = os.environ.get("VOXPILOT_STREAMING_VAD_START_TIMEOUT_SECONDS") or os.environ.get("VOXPILOT_VAD_START_TIMEOUT_SECONDS")
            try:
                self.start_timeout_seconds = float(raw) if raw is not None else DEFAULT_START_TIMEOUT_SECONDS
            except ValueError:
                self.start_timeout_seconds = DEFAULT_START_TIMEOUT_SECONDS
        else:
            self.start_timeout_seconds = float(start_timeout_seconds)

        self.sample_rate = int(sample_rate)

        self._validate_parameters()

        if webrtcvad is None:
            raise StreamingVADError(
                "webrtcvad is not installed. Please install 'webrtcvad-wheels' to enable streaming VAD."
            )

        try:
            self._vad = webrtcvad.Vad(self.aggressiveness)
        except Exception as error:
            raise StreamingVADError(f"Failed to initialize webrtcvad: {error}") from error

        self.samples_per_frame = int(self.sample_rate * self.frame_duration_ms / 1000)
        self.pre_roll_max_frames = max(1, int(self.pre_roll_ms / self.frame_duration_ms))
        self.end_silence_frames = max(1, int(self.end_silence_ms / self.frame_duration_ms))
        self.min_speech_frames = max(1, int(self.min_speech_ms / self.frame_duration_ms))
        self.timeout_frames = int(self.start_timeout_seconds * 1000 / self.frame_duration_ms)
        self.max_duration_frames = int(self.max_recording_seconds * 1000 / self.frame_duration_ms)

        # Runtime state
        self.state: str = self.STATE_WAITING
        self.pre_roll: Deque[np.ndarray] = collections.deque(maxlen=self.pre_roll_max_frames)
        self.recorded_frames: List[np.ndarray] = []
        self.consecutive_silence: int = 0
        self.speech_streak: int = 0
        self.onset_counter: int = 0
        self.onset_frames: int = 2
        self.total_speech_frames: int = 0
        self.total_processed_frames: int = 0
        self._sample_buffer: np.ndarray = np.array([], dtype=np.float32)

    def _validate_parameters(self) -> None:
        """Validate parameter values to ensure stability and compatibility."""
        if self.frame_duration_ms not in self.VALID_FRAME_DURATIONS:
            raise StreamingVADError(
                f"frame_duration_ms must be 10, 20, or 30 ms, got {self.frame_duration_ms}"
            )
        if self.aggressiveness not in self.VALID_AGGRESSIVENESS:
            raise StreamingVADError(
                f"aggressiveness must be an integer between 0 and 3, got {self.aggressiveness}"
            )
        if self.sample_rate not in self.VALID_SAMPLE_RATES:
            raise StreamingVADError(
                f"sample_rate must be 8000, 16000, 32000, or 48000 Hz, got {self.sample_rate}"
            )
        if self.end_silence_ms <= 0:
            raise StreamingVADError(
                f"end_silence_ms must be strictly positive, got {self.end_silence_ms}"
            )
        if self.pre_roll_ms < 0:
            raise StreamingVADError(
                f"pre_roll_ms must be non-negative, got {self.pre_roll_ms}"
            )
        if self.min_speech_ms < 0:
            raise StreamingVADError(
                f"min_speech_ms must be non-negative, got {self.min_speech_ms}"
            )
        if self.max_recording_seconds <= 0.0:
            raise StreamingVADError(
                f"max_recording_seconds must be positive, got {self.max_recording_seconds}"
            )
        if self.start_timeout_seconds <= 0.0:
            raise StreamingVADError(
                f"start_timeout_seconds must be positive, got {self.start_timeout_seconds}"
            )

    def reset(self) -> None:
        """Reset state machine for a new recording cycle."""
        self.state = self.STATE_WAITING
        self.pre_roll.clear()
        self.recorded_frames.clear()
        self.consecutive_silence = 0
        self.speech_streak = 0
        self.onset_counter = 0
        self.total_speech_frames = 0
        self.total_processed_frames = 0
        self._sample_buffer = np.array([], dtype=np.float32)

    def is_finished(self) -> bool:
        """Check whether the recording cycle has reached a terminal state."""
        return self.state in (self.STATE_COMPLETED, self.STATE_TIMEOUT, self.STATE_MAX_DURATION)

    def is_speech_frame(self, frame: np.ndarray) -> bool:
        """Classify a single audio frame as speech or non-speech.

        Args:
            frame: 1D NumPy array with length equal to samples_per_frame.

        Returns:
            bool: True if speech is detected, False otherwise.
        """
        if len(frame) != self.samples_per_frame:
            raise StreamingVADError(
                f"Frame size mismatch: expected {self.samples_per_frame} samples, got {len(frame)}"
            )

        pcm16 = (np.clip(frame, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
        return self._vad.is_speech(pcm16, self.sample_rate)

    def feed_samples(self, samples: np.ndarray) -> bool:
        """Buffer and process arbitrary-length incoming audio samples.

        Args:
            samples: 1D NumPy array of audio samples.

        Returns:
            bool: True if the detector is still active and expecting more audio,
                  False if recording has completed, timed out, or reached maximum duration.
        """
        if self.is_finished():
            return False

        if not isinstance(samples, np.ndarray) or samples.size == 0:
            return not self.is_finished()

        # Flatten or squeeze to 1D
        audio_1d = np.squeeze(samples).astype(np.float32)
        if audio_1d.ndim > 1:
            audio_1d = np.mean(audio_1d, axis=1)

        if self._sample_buffer.size == 0:
            self._sample_buffer = audio_1d
        else:
            self._sample_buffer = np.concatenate([self._sample_buffer, audio_1d])

        while len(self._sample_buffer) >= self.samples_per_frame:
            frame = self._sample_buffer[:self.samples_per_frame]
            self._sample_buffer = self._sample_buffer[self.samples_per_frame:]

            should_continue = self._process_frame(frame)
            if not should_continue:
                return False

        return not self.is_finished()

    def _process_frame(self, frame: np.ndarray) -> bool:
        """Process a single calibrated audio frame through the endpoint state machine."""
        is_speech = self.is_speech_frame(frame)
        self.total_processed_frames += 1

        if self.state == self.STATE_WAITING:
            if is_speech:
                self.onset_counter += 1
                self.pre_roll.append(frame)
                if self.onset_counter >= self.onset_frames:
                    # Sustained speech onset confirmed: transition to RECORDING and flush pre-roll
                    self.state = self.STATE_RECORDING
                    self.recorded_frames.extend(list(self.pre_roll))
                    self.total_speech_frames = self.onset_counter
                    self.speech_streak = self.onset_counter
                    self.consecutive_silence = 0
            else:
                self.onset_counter = 0
                self.pre_roll.append(frame)
                if self.total_processed_frames >= self.timeout_frames:
                    self.state = self.STATE_TIMEOUT
                    return False

        elif self.state == self.STATE_RECORDING:
            self.recorded_frames.append(frame)
            if is_speech:
                self.total_speech_frames += 1
                self.speech_streak += 1
                if self.speech_streak >= 2:
                    self.consecutive_silence = 0
            else:
                self.speech_streak = 0
                self.consecutive_silence += 1

            # Check maximum duration ceiling
            if len(self.recorded_frames) >= self.max_duration_frames:
                self.state = self.STATE_MAX_DURATION
                return False

            # Check sustained trailing silence
            if self.consecutive_silence >= self.end_silence_frames:
                if self.total_speech_frames >= self.min_speech_frames:
                    # Sustained speech successfully captured
                    self.state = self.STATE_COMPLETED
                    return False
                else:
                    # Noise burst or momentary click below min speech threshold: reset to waiting
                    self.state = self.STATE_WAITING
                    self.recorded_frames.clear()
                    self.total_speech_frames = 0
                    self.speech_streak = 0
                    self.onset_counter = 0
                    self.consecutive_silence = 0
                    self.pre_roll.clear()

        return True

    def get_audio(self) -> np.ndarray:
        """Return the captured speech audio as a 1D float32 NumPy array.

        Returns:
            np.ndarray: Audio samples captured between pre-roll onset and end-of-speech silence,
                        or an empty float32 array if no valid speech was recorded or on timeout.
        """
        if self.state in (self.STATE_COMPLETED, self.STATE_MAX_DURATION) and self.recorded_frames:
            return np.concatenate(self.recorded_frames).astype(np.float32)
        return np.zeros(0, dtype=np.float32)
