"""
VoxPilot - Audio Recorder Module
Handles microphone discovery, configurable device selection, and audio recording.
"""

import os
import math
import queue
import logging
import sounddevice as sd
import soundfile as sf
import numpy as np
import scipy.signal

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

logger = logging.getLogger(__name__)


class AudioRecorderError(Exception):
    """Raised when an audio recording or microphone selection operation fails."""
    pass


class AudioRecorder:
    """Manages microphone selection, device capability validation, and audio recording."""

    def __init__(
        self,
        sample_rate=16000,
        channels=1,
        device=None,
        streaming_vad_enabled=None,
        frame_duration_ms=None,
        aggressiveness=None,
        end_silence_ms=None,
        pre_roll_ms=None,
        min_speech_ms=None,
        max_recording_seconds=None,
        start_timeout_seconds=None,
    ):
        """Initialize the AudioRecorder.

        Args:
            sample_rate: Target sampling frequency in Hz (default: 16000 Hz for Whisper).
            channels: Target audio channels (default: 1 for mono).
            device: Microphone identifier. Can be an integer index, device name substring,
                    'default', or None (which resolves to the system default microphone).
            streaming_vad_enabled: Whether streaming VAD mode is active. If None, reads VOXPILOT_STREAMING_VAD_ENABLED.
            frame_duration_ms: Frame length in ms (10, 20, or 30).
            aggressiveness: WebRTC VAD mode (0-3).
            end_silence_ms: Trailing silence in ms to conclude recording.
            pre_roll_ms: Pre-roll buffer in ms prior to speech onset.
            min_speech_ms: Minimum speech duration required to qualify as utterance.
            max_recording_seconds: Hard ceiling on recording time in seconds.
            start_timeout_seconds: Timeout in seconds if no speech is detected.
        """
        self.sample_rate = sample_rate
        self.channels = channels
        self.device = device

        # Streaming VAD settings
        if streaming_vad_enabled is None:
            env_val = os.environ.get("VOXPILOT_STREAMING_VAD_ENABLED", "").strip().lower()
            self.streaming_vad_enabled = env_val not in ("false", "0", "no") if env_val else DEFAULT_STREAMING_VAD_ENABLED
        else:
            self.streaming_vad_enabled = bool(streaming_vad_enabled)

        self.frame_duration_ms = frame_duration_ms
        self.aggressiveness = aggressiveness
        self.end_silence_ms = end_silence_ms
        self.pre_roll_ms = pre_roll_ms
        self.min_speech_ms = min_speech_ms
        self.max_recording_seconds = max_recording_seconds
        self.start_timeout_seconds = start_timeout_seconds

    @staticmethod
    def get_input_devices():
        """Retrieve a list of all available audio input devices.

        Returns:
            list[dict]: Dictionaries containing device index, name, host API,
                        maximum input channels, and default sample rate.
        """
        devices = []
        try:
            raw_devices = sd.query_devices()
            hostapis = sd.query_hostapis()

            for index, dev in enumerate(raw_devices):
                if dev.get("max_input_channels", 0) > 0:
                    api_idx = dev.get("hostapi", 0)
                    host_api_name = hostapis[api_idx]["name"] if api_idx < len(hostapis) else "Unknown"
                    devices.append({
                        "index": index,
                        "name": dev.get("name", "Unknown"),
                        "host_api": host_api_name,
                        "channels": dev.get("max_input_channels", 0),
                        "default_sample_rate": dev.get("default_samplerate", 44100.0),
                    })
        except Exception as error:
            raise AudioRecorderError(f"Failed to query audio devices: {error}") from error

        return devices

    def list_microphones(self):
        """Print all available microphones with their index, name, host API, channels, and sample rate."""
        devices = self.get_input_devices()

        print("\nAvailable microphones:")
        if not devices:
            print("  No audio input devices found.")
            return

        for dev in devices:
            print(
                f"  [{dev['index']:2d}] {dev['name']} "
                f"| Host API: {dev['host_api']} "
                f"| Channels: {dev['channels']} "
                f"| Default SR: {int(dev['default_sample_rate'])} Hz"
            )

    @classmethod
    def resolve_device(cls, device_spec=None):
        """Resolve a device specification (index, name substring, 'default', or None) to an active input device.

        Args:
            device_spec: Device index, name substring, None, or 'default'.

        Returns:
            tuple[int, dict]: The resolved device integer index and its info dictionary.

        Raises:
            AudioRecorderError: If no microphone is found or the device specification is invalid.
        """
        devices = cls.get_input_devices()
        if not devices:
            raise AudioRecorderError("No microphone input devices found on this system.")

        # Case 1: System default microphone
        if device_spec is None or device_spec == "" or str(device_spec).strip().lower() == "default":
            try:
                default_idx = sd.default.device[0]
            except Exception:
                default_idx = None

            if default_idx is not None and default_idx >= 0:
                match = next((d for d in devices if d["index"] == default_idx), None)
                if match:
                    return match["index"], match

            # Fallback to the first available input device
            return devices[0]["index"], devices[0]

        # Case 2: Integer or numeric string index
        try:
            target_idx = int(device_spec)
            match = next((d for d in devices if d["index"] == target_idx), None)
            if match:
                return match["index"], match
            available = "\n".join(f"  [{d['index']}] {d['name']} ({d['host_api']})" for d in devices)
            raise AudioRecorderError(
                f"Device index {target_idx} is not a valid input device.\nAvailable microphones:\n{available}"
            )
        except (ValueError, TypeError):
            pass

        # Case 3: Device name substring match (case-insensitive)
        query = str(device_spec).strip().lower()
        matches = [d for d in devices if query in d["name"].lower()]

        if matches:
            # If multiple host APIs exist for the same hardware, prefer WASAPI, then DirectSound, then MME
            def _api_rank(d):
                api = d["host_api"].lower()
                if "wasapi" in api:
                    return 0
                if "directsound" in api:
                    return 1
                return 2

            matches.sort(key=_api_rank)
            return matches[0]["index"], matches[0]

        available = "\n".join(f"  [{d['index']}] {d['name']} ({d['host_api']})" for d in devices)
        raise AudioRecorderError(
            f"No microphone matching '{device_spec}' found.\nAvailable microphones:\n{available}"
        )

    def get_selected_device_info(self):
        """Get the info dictionary of the currently resolved microphone.

        Returns:
            dict: Info dictionary of the resolved device.
        """
        _, dev_info = self.resolve_device(self.device)
        return dev_info

    def _determine_recording_parameters(self, device_idx):
        """Determine supported recording sample rate and channels for the device.

        Tests whether direct 16 kHz mono is supported. If not, falls back to the device's
        native sample rate and channel count, marking that resampling will be required.

        Args:
            device_idx: Sounddevice input device index.

        Returns:
            tuple[int, int, bool]: (recording_sample_rate, recording_channels, needs_resample)
        """
        # First attempt: direct 16 kHz mono (ideal for Whisper)
        try:
            sd.check_input_settings(device=device_idx, channels=self.channels, samplerate=self.sample_rate)
            return self.sample_rate, self.channels, False
        except Exception:
            pass

        # Second attempt: device native sample rate and channels
        try:
            dev_info = sd.query_devices(device_idx)
            native_sr = int(dev_info.get("default_samplerate", 44100))
            max_channels = int(dev_info.get("max_input_channels", 1))
        except Exception as error:
            raise AudioRecorderError(f"Could not query capabilities for device {device_idx}: {error}") from error

        for ch in [self.channels, min(2, max_channels)]:
            try:
                sd.check_input_settings(device=device_idx, channels=ch, samplerate=native_sr)
                return native_sr, ch, True
            except Exception:
                continue

        raise AudioRecorderError(
            f"Could not find a supported recording configuration for device {device_idx}."
        )

    def record(self, duration=5):
        """Record audio for a fixed duration, automatically resampling to 16 kHz mono if needed.

        Args:
            duration: Recording duration in seconds (must be positive).

        Returns:
            np.ndarray: 1D float32 NumPy array at 16,000 Hz.
        """
        if duration <= 0:
            raise AudioRecorderError("Recording duration must be positive.")

        device_idx, dev_info = self.resolve_device(self.device)
        rec_sr, rec_channels, needs_resample = self._determine_recording_parameters(device_idx)

        try:
            print(f"\nRecording for {duration} seconds using [{device_idx}] {dev_info['name']}...")
            print("Speak now!")

            frames_to_record = int(duration * rec_sr)
            audio = sd.rec(
                frames_to_record,
                samplerate=rec_sr,
                channels=rec_channels,
                dtype="float32",
                device=device_idx,
            )
            sd.wait()

            print("Recording completed.")
            audio = np.squeeze(audio)

            # Convert stereo to mono if recorded with multiple channels
            if audio.ndim > 1:
                audio = np.mean(audio, axis=1)

            # Resample to target sample rate (16000 Hz) if recorded at native hardware rate
            if needs_resample or rec_sr != self.sample_rate:
                gcd = math.gcd(rec_sr, self.sample_rate)
                up = self.sample_rate // gcd
                down = rec_sr // gcd
                audio = scipy.signal.resample_poly(audio, up, down).astype(np.float32)

            return audio

        except Exception as error:
            raise AudioRecorderError(
                f"Microphone recording failed on [{device_idx}] {dev_info.get('name', 'Unknown')}: {error}"
            ) from error

    def save_recording(self, audio, filename="recording.wav"):
        """Save NumPy audio data to a WAV file.

        Args:
            audio: 1D NumPy array.
            filename: Destination WAV file path.
        """
        try:
            sf.write(filename, audio, self.sample_rate)
            print(f"Recording saved: {filename}")
        except Exception as error:
            raise AudioRecorderError(f"Could not save recording: {error}") from error

    def record_to_file(self, duration=5, filename="recording.wav"):
        """Record audio and save it directly to a WAV file.

        Args:
            duration: Recording duration in seconds.
            filename: Destination WAV file path.

        Returns:
            np.ndarray: Recorded audio data at 16,000 Hz mono.
        """
        audio = self.record(duration)
        self.save_recording(audio, filename)
        return audio

    def record_until_silence(self, max_duration=None, start_timeout=None):
        """Record audio dynamically, automatically stopping when the user finishes speaking.

        Uses real-time WebRTC Voice Activity Detection (StreamingVAD) to detect speech onset,
        maintain a pre-roll buffer to preserve the first syllable, bridge inter-word pauses,
        and cleanly terminate recording once sustained silence is detected.

        Args:
            max_duration: Optional override for maximum recording duration in seconds.
            start_timeout: Optional override for initial speech detection timeout in seconds.

        Returns:
            np.ndarray: 1D float32 NumPy array at 16,000 Hz, or an empty array if timeout/no speech.
        """
        if not self.streaming_vad_enabled:
            fallback_duration = max_duration if max_duration is not None else 5
            return self.record(duration=fallback_duration)

        device_idx, dev_info = self.resolve_device(self.device)
        rec_sr, rec_channels, needs_resample = self._determine_recording_parameters(device_idx)

        # WebRTC VAD natively supports 8000, 16000, 32000, 48000 Hz
        if rec_sr in StreamingVAD.VALID_SAMPLE_RATES:
            vad_sr = rec_sr
            downsample_in_stream = False
        else:
            vad_sr = self.sample_rate  # 16000 Hz
            downsample_in_stream = True

        vad = StreamingVAD(
            enabled=True,
            frame_duration_ms=self.frame_duration_ms,
            aggressiveness=self.aggressiveness,
            end_silence_ms=self.end_silence_ms,
            pre_roll_ms=self.pre_roll_ms,
            min_speech_ms=self.min_speech_ms,
            max_recording_seconds=max_duration if max_duration is not None else self.max_recording_seconds,
            start_timeout_seconds=start_timeout if start_timeout is not None else self.start_timeout_seconds,
            sample_rate=vad_sr,
        )

        frame_duration = vad.frame_duration_ms / 1000.0
        block_size = int(rec_sr * frame_duration)

        audio_queue = queue.Queue()

        def stream_callback(indata, frames, time_info, status):
            if status:
                logger.warning(f"Audio stream status: {status}")
            audio_queue.put(indata.copy())

        try:
            print(f"\nListening for speech using [{device_idx}] {dev_info['name']} (Streaming VAD active)...")

            speech_detected_logged = False

            with sd.InputStream(
                samplerate=rec_sr,
                channels=rec_channels,
                dtype="float32",
                blocksize=block_size,
                device=device_idx,
                callback=stream_callback,
            ) as stream:
                while not vad.is_finished():
                    try:
                        chunk = audio_queue.get(timeout=0.5)
                    except queue.Empty:
                        if not stream.active:
                            raise AudioRecorderError("Audio input stream terminated unexpectedly.")
                        continue

                    # Squeeze or downmix to mono
                    chunk = np.squeeze(chunk)
                    if chunk.ndim > 1:
                        chunk = np.mean(chunk, axis=1)

                    # Resample chunk if hardware native sample rate isn't directly supported by WebRTC VAD
                    if downsample_in_stream:
                        gcd = math.gcd(rec_sr, vad_sr)
                        up = vad_sr // gcd
                        down = rec_sr // gcd
                        chunk = scipy.signal.resample_poly(chunk, up, down).astype(np.float32)

                    vad.feed_samples(chunk)

                    if vad.state == StreamingVAD.STATE_RECORDING and not speech_detected_logged:
                        print("Speech detected, recording...")
                        speech_detected_logged = True

            if vad.state == StreamingVAD.STATE_TIMEOUT:
                print("No speech detected (timeout).")
            elif vad.state == StreamingVAD.STATE_COMPLETED:
                print("End of speech detected, recording stopped.")
            elif vad.state == StreamingVAD.STATE_MAX_DURATION:
                print("Maximum recording duration reached.")

            audio = vad.get_audio()

            # Resample recorded audio to target sample rate (16000 Hz) if recorded at a different rate
            if len(audio) > 0 and vad_sr != self.sample_rate:
                gcd = math.gcd(vad_sr, self.sample_rate)
                up = self.sample_rate // gcd
                down = vad_sr // gcd
                audio = scipy.signal.resample_poly(audio, up, down).astype(np.float32)

            return audio

        except Exception as error:
            raise AudioRecorderError(
                f"Dynamic microphone recording failed on [{device_idx}] {dev_info.get('name', 'Unknown')}: {error}"
            ) from error
