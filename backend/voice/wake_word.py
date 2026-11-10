"""
VoxPilot - Wake Word Detection Module
Monitors microphone audio in idle mode and triggers voice activation on configured wake phrases.
Powered by openWakeWord (ONNX Runtime).
"""

from __future__ import annotations

import os
import time
import queue
import logging
from pathlib import Path
from typing import Optional, Dict, Any, Union
import sounddevice as sd
import numpy as np

try:
    import openwakeword
    from openwakeword.model import Model as OWWModel
    from openwakeword.utils import download_models as oww_download_models
except ImportError:
    openwakeword = None
    OWWModel = None
    oww_download_models = None

from backend.voice.audio_recorder import AudioRecorder, AudioRecorderError

logger = logging.getLogger(__name__)

# Default Wake Word Configuration Constants
DEFAULT_WAKE_WORD_ENABLED = False
DEFAULT_WAKE_WORD_MODEL = "hey_jarvis_v0.1"
DEFAULT_WAKE_WORD_THRESHOLD = 0.5
DEFAULT_WAKE_WORD_COOLDOWN_SECONDS = 2.0
REQUIRED_SAMPLE_RATE = 16000
FRAME_SIZE_SAMPLES = 1280  # 80 ms at 16 kHz


class WakeWordError(Exception):
    """Raised when wake word detection, initialization, or audio monitoring fails."""
    pass


class WakeWordDetector:
    """Manages offline wake-word detection, microphone stream lifecycle, and handover."""

    def __init__(
        self,
        enabled: Optional[bool] = None,
        model_path: Optional[str] = None,
        threshold: Optional[float] = None,
        cooldown_seconds: Optional[float] = None,
        sample_rate: int = REQUIRED_SAMPLE_RATE,
        device: Optional[Union[int, str]] = None,
    ) -> None:
        """Initialize the WakeWordDetector.

        Args:
            enabled: Whether wake-word monitoring is active. If None, reads VOXPILOT_WAKE_WORD_ENABLED.
            model_path: Path to custom .onnx model or name of pretrained model (e.g. 'hey_jarvis_v0.1').
            threshold: Activation confidence threshold between 0.0 and 1.0 (default: 0.5).
            cooldown_seconds: Minimum seconds before a subsequent wake word can trigger (default: 2.0).
            sample_rate: Audio sampling frequency in Hz (must be 16,000 for openWakeWord).
            device: Microphone identifier (index, name substring, 'default', or None).
        """
        if enabled is None:
            env_val = os.environ.get("VOXPILOT_WAKE_WORD_ENABLED", "").strip().lower()
            self.enabled = env_val in ("true", "1", "yes") if env_val else DEFAULT_WAKE_WORD_ENABLED
        else:
            self.enabled = bool(enabled)

        if model_path is None:
            env_model = os.environ.get("VOXPILOT_WAKE_WORD_MODEL", "").strip()
            self.model_path = env_model if env_model else DEFAULT_WAKE_WORD_MODEL
        else:
            self.model_path = str(model_path).strip()

        if threshold is None:
            try:
                self.threshold = float(os.environ.get("VOXPILOT_WAKE_WORD_THRESHOLD", DEFAULT_WAKE_WORD_THRESHOLD))
            except ValueError:
                self.threshold = DEFAULT_WAKE_WORD_THRESHOLD
        else:
            self.threshold = float(threshold)

        if cooldown_seconds is None:
            try:
                self.cooldown_seconds = float(
                    os.environ.get("VOXPILOT_WAKE_WORD_COOLDOWN_SECONDS", DEFAULT_WAKE_WORD_COOLDOWN_SECONDS)
                )
            except ValueError:
                self.cooldown_seconds = DEFAULT_WAKE_WORD_COOLDOWN_SECONDS
        else:
            self.cooldown_seconds = float(cooldown_seconds)

        self.sample_rate = int(sample_rate)
        self.device = device

        self._validate_parameters()

        self.model: Optional[OWWModel] = None
        self.active_model_name: Optional[str] = None
        self.last_detection_time: float = 0.0
        self._is_listening: bool = False
        self._stop_requested: bool = False

    def _validate_parameters(self) -> None:
        """Validate parameter ranges and configuration integrity."""
        if not (0.0 < self.threshold < 1.0):
            raise WakeWordError(
                f"threshold must be strictly between 0.0 and 1.0, got {self.threshold}"
            )
        if self.cooldown_seconds < 0.0:
            raise WakeWordError(
                f"cooldown_seconds must be non-negative, got {self.cooldown_seconds}"
            )
        if self.sample_rate != REQUIRED_SAMPLE_RATE:
            raise WakeWordError(
                f"openWakeWord strictly requires {REQUIRED_SAMPLE_RATE} Hz audio, got {self.sample_rate}"
            )
        if not self.model_path:
            raise WakeWordError("model_path cannot be empty.")

    def load_model(self) -> None:
        """Load the wake word model into memory via openWakeWord ONNX framework."""
        if self.model is not None:
            return

        if openwakeword is None:
            raise WakeWordError(
                "openWakeWord is not installed. Please install 'openwakeword' to enable wake word detection."
            )

        model_spec = self.model_path
        target_path = Path(model_spec)

        # Case 1: Explicit file path on disk (e.g. custom models/wake_word/hey_voxpilot.onnx)
        if target_path.suffix.lower() in (".onnx", ".tflite") or target_path.is_absolute() or "/" in model_spec or "\\" in model_spec:
            if not target_path.exists():
                raise WakeWordError(
                    f"Custom wake word model file not found at: {target_path.resolve()}\n"
                    "Note: A pretrained 'Hey VoxPilot' model does not exist out of the box.\n"
                    "Please train a custom model and place it at that location, or configure a "
                    "supported pretrained model (e.g. 'hey_jarvis_v0.1', 'alexa_v0.1')."
                )
            resolved_model = str(target_path.resolve())
            self.active_model_name = target_path.stem

        # Case 2: Named pretrained model in openWakeWord catalog
        else:
            resolved_model = model_spec
            self.active_model_name = model_spec

            # Ensure pretrained weights exist or download them if needed
            known_models = openwakeword.MODELS
            matching_key = next((k for k in known_models if k == model_spec or k in model_spec), None)
            if matching_key:
                model_meta = known_models[matching_key]
                expected_onnx = Path(model_meta["model_path"]).with_suffix(".onnx")
                if not expected_onnx.exists() and oww_download_models:
                    logger.info(f"Downloading pretrained wake word model: {model_spec}...")
                    try:
                        oww_download_models([model_spec])
                    except Exception as err:
                        raise WakeWordError(f"Failed to download pretrained wake word model '{model_spec}': {err}") from err

        try:
            logger.info(f"Loading openWakeWord model: {resolved_model}")
            self.model = OWWModel(
                wakeword_models=[resolved_model],
                inference_framework="onnx",
            )
            logger.info("Wake word model loaded successfully.")

        except Exception as error:
            raise WakeWordError(
                f"Failed to initialize openWakeWord model '{resolved_model}': {error}"
            ) from error

    def predict_frame(self, audio_chunk: np.ndarray) -> Dict[str, float]:
        """Evaluate a single 80 ms audio frame for wake-word activation.

        Args:
            audio_chunk: 1D NumPy array with length equal to 1280 samples at 16 kHz.

        Returns:
            Dict[str, float]: Predictions mapping model name to confidence score [0.0, 1.0].
        """
        if self.model is None:
            self.load_model()

        if not isinstance(audio_chunk, np.ndarray):
            raise WakeWordError("Audio chunk must be a NumPy array.")

        if audio_chunk.size == 0:
            return {}

        # Convert float32 in [-1.0, 1.0] to int16 PCM (openWakeWord requires int16)
        if audio_chunk.dtype != np.int16:
            audio_int16 = (np.clip(audio_chunk, -1.0, 1.0) * 32767.0).astype(np.int16)
        else:
            audio_int16 = audio_chunk

        try:
            return self.model.predict(audio_int16)
        except Exception as error:
            raise WakeWordError(f"Wake word prediction failed: {error}") from error

    def listen_for_wake_word(self, timeout: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """Monitor the microphone stream until the wake phrase is detected or timeout occurs.

        Crucial Concurrency Contract:
        When a wake word is detected or timeout expires, this method COMPLETELY CLOSES and
        RELEASES the microphone input stream before returning, allowing AudioRecorder to acquire
        exclusive microphone access immediately.

        Args:
            timeout: Maximum seconds to listen before returning None. If None, listens indefinitely.

        Returns:
            Optional[Dict[str, Any]]: Detection event with model name, score, and timestamp,
                                      or None if timeout or stopped.
        """
        if self.model is None:
            self.load_model()

        self._stop_requested = False
        self._is_listening = True

        device_idx, dev_info = AudioRecorder.resolve_device(self.device)

        audio_queue: queue.Queue = queue.Queue()

        def stream_callback(indata, frames, time_info, status):
            if status:
                logger.warning(f"Wake word stream status: {status}")
            audio_queue.put(indata.copy())

        start_time = time.time()
        detected_event = None

        try:
            logger.info(f"Wake word monitoring active on [{device_idx}] {dev_info['name']}...")

            # Open sounddevice stream exclusively for wake-word listening
            with sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=FRAME_SIZE_SAMPLES,
                device=device_idx,
                callback=stream_callback,
            ) as stream:
                while not self._stop_requested:
                    # Check timeout
                    if timeout is not None and (time.time() - start_time) >= timeout:
                        break

                    try:
                        chunk = audio_queue.get(timeout=0.5)
                    except queue.Empty:
                        if not stream.active:
                            raise WakeWordError("Audio stream terminated unexpectedly during wake word monitoring.")
                        continue

                    # Squeeze to 1D
                    chunk_1d = np.squeeze(chunk).astype(np.float32)

                    # Enforce cooldown to prevent immediate double activations
                    current_time = time.time()
                    if (current_time - self.last_detection_time) < self.cooldown_seconds:
                        continue

                    # Evaluate audio frame with openWakeWord
                    predictions = self.predict_frame(chunk_1d)

                    for model_name, score in predictions.items():
                        if score >= self.threshold:
                            self.last_detection_time = current_time
                            detected_event = {
                                "detected": True,
                                "model": model_name,
                                "score": float(score),
                                "timestamp": current_time,
                            }
                            break

                    if detected_event is not None:
                        # Break loop immediately to exit the 'with' block and release the mic stream
                        break

        except Exception as error:
            if not self._stop_requested:
                raise WakeWordError(
                    f"Microphone monitoring failed on [{device_idx}] {dev_info.get('name', 'Unknown')}: {error}"
                ) from error
        finally:
            self._is_listening = False

        # Stream is now completely closed and released
        return detected_event

    def stop(self) -> None:
        """Signal the running listening loop to stop and release resources."""
        self._stop_requested = True

    def reset(self) -> None:
        """Reset internal detection state and model prediction buffers."""
        self._stop_requested = False
        self._is_listening = False
        if self.model is not None:
            self.model.reset()

