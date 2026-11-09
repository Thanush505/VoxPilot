
import wave
from pathlib import Path

from piper import PiperVoice


class TextToSpeechError(Exception):
    """Raised when text-to-speech operations fail."""


class TextToSpeech:
    def __init__(self, model_path=None):
        self.model_path = Path(model_path) if model_path else None
        self.voice = None

    def load_model(self, model_path=None):
        if model_path is not None:
            self.model_path = Path(model_path)

        if self.model_path is None:
            raise TextToSpeechError(
                "Piper voice model path was not provided."
            )

        if not self.model_path.exists():
            raise TextToSpeechError(
                f"Piper model not found: {self.model_path}"
            )

        try:
            print("Loading Piper voice model...")

            self.voice = PiperVoice.load(str(self.model_path))

            print("Piper voice model loaded successfully.")

        except Exception as error:
            raise TextToSpeechError(
                f"Could not load Piper model: {error}"
            ) from error

    def synthesize(self, text):
        if not text or not text.strip():
            raise TextToSpeechError(
                "Text cannot be empty."
            )

        if self.voice is None:
            raise TextToSpeechError(
                "Piper model is not loaded. Call load_model() first."
            )

        try:
            audio_chunks = list(self.voice.synthesize(text))

            if not audio_chunks:
                raise TextToSpeechError(
                    "Piper did not generate any audio."
                )

            return audio_chunks

        except TextToSpeechError:
            raise

        except Exception as error:
            raise TextToSpeechError(
                f"Speech synthesis failed: {error}"
            ) from error

    def save_audio(self, audio_chunks, output_path="output.wav"):
        if not audio_chunks:
            raise TextToSpeechError(
                "No audio data was provided."
            )

        try:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with wave.open(str(output_path), "wb") as wav_file:
                first_chunk = audio_chunks[0]

                wav_file.setnchannels(
                    first_chunk.sample_channels
                )
                wav_file.setsampwidth(
                    first_chunk.sample_width
                )
                wav_file.setframerate(
                    first_chunk.sample_rate
                )

                for chunk in audio_chunks:
                    wav_file.writeframes(chunk.audio_int16_bytes)

            print(f"Speech saved successfully: {output_path}")

        except Exception as error:
            raise TextToSpeechError(
                f"Could not save generated audio: {error}"
            ) from error

    def text_to_speech(self, text, output_path="output.wav"):
        audio_chunks = self.synthesize(text)
        self.save_audio(audio_chunks, output_path)

        return str(output_path)
