"""
VoxPilot - Speech to Text (STT) Module
Powered by Faster-Whisper (CTranslate2) with integrated Silero VAD and Audio Normalization.
"""

import os
import logging
import soundfile as sf
import numpy as np
from dotenv import load_dotenv

from faster_whisper import WhisperModel
from backend.voice.audio_preprocessor import AudioPreprocessor

load_dotenv()
logger = logging.getLogger(__name__)

# Default VAD configuration constants
DEFAULT_VAD_ENABLED = True
DEFAULT_VAD_THRESHOLD = 0.5
DEFAULT_VAD_MIN_SPEECH_DURATION_MS = 250
DEFAULT_VAD_MIN_SILENCE_DURATION_MS = 500
DEFAULT_VAD_SPEECH_PAD_MS = 200


class SpeechToTextError(Exception):
    """Raised when speech recognition or VAD processing fails."""
    pass


class SpeechToText:
    """Offline speech recognition using Faster-Whisper, with integrated Silero VAD and gain normalization."""

    def __init__(
        self,
        model_size="base",
        device="cpu",
        compute_type="int8",
        language="en",
        preprocessor=None,
        vad_enabled=None,
        vad_threshold=None,
        vad_min_speech_duration_ms=None,
        vad_min_silence_duration_ms=None,
        vad_speech_pad_ms=None,
    ):
        """Initialize the SpeechToText engine.

        Args:
            model_size: Whisper model variant (e.g. 'tiny', 'base', 'small').
            device: 'cpu' or 'cuda'.
            compute_type: Computation precision (e.g. 'int8', 'float16').
            language: Language code ('en' or None for auto-detection).
            preprocessor: AudioPreprocessor instance for software gain normalization.
            vad_enabled: Whether Silero VAD filtering is active. If None, reads VOXPILOT_VAD_ENABLED (default: True).
            vad_threshold: Speech probability threshold (0.0 to 1.0, default: 0.5).
            vad_min_speech_duration_ms: Minimum speech segment duration in ms (default: 250).
            vad_min_silence_duration_ms: Silence duration in ms to separate segments (default: 500).
            vad_speech_pad_ms: Padding added to each side of speech chunks in ms (default: 200).
        """
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.preprocessor = preprocessor if preprocessor is not None else AudioPreprocessor()

        # VAD Configuration
        if vad_enabled is None:
            env_val = os.environ.get("VOXPILOT_VAD_ENABLED", "").strip().lower()
            self.vad_enabled = env_val not in ("false", "0", "no") if env_val else DEFAULT_VAD_ENABLED
        else:
            self.vad_enabled = bool(vad_enabled)

        if vad_threshold is None:
            try:
                self.vad_threshold = float(os.environ.get("VOXPILOT_VAD_THRESHOLD", DEFAULT_VAD_THRESHOLD))
            except ValueError:
                self.vad_threshold = DEFAULT_VAD_THRESHOLD
        else:
            self.vad_threshold = float(vad_threshold)

        if vad_min_speech_duration_ms is None:
            try:
                self.vad_min_speech_duration_ms = int(
                    os.environ.get("VOXPILOT_VAD_MIN_SPEECH_DURATION_MS", DEFAULT_VAD_MIN_SPEECH_DURATION_MS)
                )
            except ValueError:
                self.vad_min_speech_duration_ms = DEFAULT_VAD_MIN_SPEECH_DURATION_MS
        else:
            self.vad_min_speech_duration_ms = int(vad_min_speech_duration_ms)

        if vad_min_silence_duration_ms is None:
            try:
                self.vad_min_silence_duration_ms = int(
                    os.environ.get("VOXPILOT_VAD_MIN_SILENCE_DURATION_MS", DEFAULT_VAD_MIN_SILENCE_DURATION_MS)
                )
            except ValueError:
                self.vad_min_silence_duration_ms = DEFAULT_VAD_MIN_SILENCE_DURATION_MS
        else:
            self.vad_min_silence_duration_ms = int(vad_min_silence_duration_ms)

        if vad_speech_pad_ms is None:
            try:
                self.vad_speech_pad_ms = int(os.environ.get("VOXPILOT_VAD_SPEECH_PAD_MS", DEFAULT_VAD_SPEECH_PAD_MS))
            except ValueError:
                self.vad_speech_pad_ms = DEFAULT_VAD_SPEECH_PAD_MS
        else:
            self.vad_speech_pad_ms = int(vad_speech_pad_ms)

        self._validate_vad_parameters()
        self.model = None

    def _validate_vad_parameters(self):
        """Validate VAD parameter boundaries to avoid invalid configurations."""
        if not (0.0 < self.vad_threshold < 1.0):
            raise SpeechToTextError(
                f"vad_threshold must be strictly between 0.0 and 1.0, got {self.vad_threshold}"
            )
        if self.vad_min_speech_duration_ms < 0:
            raise SpeechToTextError(
                f"vad_min_speech_duration_ms must be non-negative, got {self.vad_min_speech_duration_ms}"
            )
        if self.vad_min_silence_duration_ms < 0:
            raise SpeechToTextError(
                f"vad_min_silence_duration_ms must be non-negative, got {self.vad_min_silence_duration_ms}"
            )
        if self.vad_speech_pad_ms < 0:
            raise SpeechToTextError(
                f"vad_speech_pad_ms must be non-negative, got {self.vad_speech_pad_ms}"
            )

    def get_vad_parameters(self):
        """Return the dictionary of VAD parameters if VAD is enabled, or None if disabled."""
        if not self.vad_enabled:
            return None
        return {
            "threshold": self.vad_threshold,
            "min_speech_duration_ms": self.vad_min_speech_duration_ms,
            "min_silence_duration_ms": self.vad_min_silence_duration_ms,
            "speech_pad_ms": self.vad_speech_pad_ms,
        }

    def load_model(self):
        """Load the Faster-Whisper model into memory if not already loaded."""
        if self.model is not None:
            return

        try:
            print(f"Loading Faster-Whisper model: {self.model_size}")

            self.model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type
            )

            print("Faster-Whisper model loaded successfully.")

        except Exception as error:
            raise SpeechToTextError(
                f"Could not load Whisper model: {error}"
            ) from error

    def transcribe_audio(self, audio):
        """Preprocess audio and transcribe it using Faster-Whisper with Silero VAD filtering.

        Args:
            audio: NumPy array containing 16 kHz audio samples.

        Returns:
            dict: {"text": str, "language": str, "language_probability": float}
        """
        if not isinstance(audio, np.ndarray):
            raise SpeechToTextError(
                "Audio must be provided as a NumPy array."
            )

        if audio.size == 0:
            raise SpeechToTextError("Audio is empty.")

        try:
            self.load_model()

            # Preprocess and normalize audio before passing to Faster-Whisper
            processed_audio = self.preprocessor.process(audio)

            vad_params = self.get_vad_parameters()

            segments, information = self.model.transcribe(
                processed_audio.astype(np.float32),
                beam_size=5,
                language=self.language,
                vad_filter=self.vad_enabled,
                vad_parameters=vad_params,
            )

            transcription = " ".join(
                segment.text.strip()
                for segment in segments
            ).strip()

            return {
                "text": transcription,
                "language": information.language,
                "language_probability": information.language_probability
            }

        except Exception as error:
            raise SpeechToTextError(
                f"Speech transcription failed: {error}"
            ) from error

    def transcribe_file(self, filename):
        """Transcribe an audio file from disk.

        Args:
            filename: Path to WAV audio file.

        Returns:
            dict: {"text": str, "language": str, "language_probability": float}
        """
        try:
            audio, sample_rate = sf.read(
                filename,
                dtype="float32"
            )

            if sample_rate != 16000:
                raise SpeechToTextError(
                    "Audio must have a sample rate of 16000 Hz."
                )

            if audio.ndim > 1:
                audio = np.mean(audio, axis=1)

            return self.transcribe_audio(audio)

        except SpeechToTextError:
            raise

        except Exception as error:
            raise SpeechToTextError(
                f"Could not read audio file: {error}"
            ) from error
