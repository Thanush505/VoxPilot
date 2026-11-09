
import soundfile as sf
import numpy as np

from faster_whisper import WhisperModel


class SpeechToTextError(Exception):
    """Raised when speech recognition fails."""


class SpeechToText:
    def __init__(
        self,
        model_size="base",
        device="cpu",
        compute_type="int8",
        language="en"
    ):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.language = language

        self.model = None

    def load_model(self):
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
        if not isinstance(audio, np.ndarray):
            raise SpeechToTextError(
                "Audio must be provided as a NumPy array."
            )

        if audio.size == 0:
            raise SpeechToTextError("Audio is empty.")

        try:
            self.load_model()

            segments, information = self.model.transcribe(
                audio.astype(np.float32),
                beam_size=5,
                language=self.language
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
