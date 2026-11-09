
import sounddevice as sd
import soundfile as sf
import numpy as np


class AudioRecorderError(Exception):
    """Raised when an audio recording operation fails."""


class AudioRecorder:
    def __init__(self, sample_rate=16000, channels=1, device=None):
        self.sample_rate = sample_rate
        self.channels = channels
        self.device = device

    def list_microphones(self):
        devices = sd.query_devices()

        print("\nAvailable microphones:")

        for index, device in enumerate(devices):
            if device["max_input_channels"] > 0:
                print(f"{index}: {device['name']}")

    def record(self, duration=5):
        if duration <= 0:
            raise AudioRecorderError("Recording duration must be positive.")

        try:
            print(f"\nRecording for {duration} seconds...")
            print("Speak now!")

            audio = sd.rec(
                int(duration * self.sample_rate),
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="float32",
                device=self.device
            )

            sd.wait()

            print("Recording completed.")

            return np.squeeze(audio)

        except Exception as error:
            raise AudioRecorderError(
                f"Microphone recording failed: {error}"
            ) from error

    def save_recording(self, audio, filename="recording.wav"):
        try:
            sf.write(filename, audio, self.sample_rate)
            print(f"Recording saved: {filename}")

        except Exception as error:
            raise AudioRecorderError(
                f"Could not save recording: {error}"
            ) from error

    def record_to_file(self, duration=5, filename="recording.wav"):
        audio = self.record(duration)
        self.save_recording(audio, filename)

        return audio
