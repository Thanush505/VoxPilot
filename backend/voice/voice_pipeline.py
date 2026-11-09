
import sounddevice as sd
import soundfile as sf

from pathlib import Path

from backend.voice.audio_recorder import AudioRecorder
from backend.voice.speech_to_text import SpeechToText
from backend.voice.text_to_speech import TextToSpeech


class VoicePipeline:
    def __init__(
        self,
        microphone_device=1,
        whisper_model="base",
        piper_model="models/piper/en_US-lessac-medium.onnx"
    ):
        self.recorder = AudioRecorder(
            sample_rate=16000,
            channels=1,
            device=microphone_device
        )

        self.stt = SpeechToText(
            model_size=whisper_model,
            device="cpu",
            compute_type="int8",
            language="en"
        )

        self.tts = TextToSpeech(
            model_path=piper_model
        )

        self.output_directory = Path("models/piper/generated")
        self.output_directory.mkdir(parents=True, exist_ok=True)

    def initialize(self):
        print("\nInitializing VoxPilot Voice Pipeline...")

        self.stt.load_model()
        self.tts.load_model()

        print("Voice pipeline initialized successfully.")

    def listen(self, duration=5):
        print("\nVoxPilot is listening...")

        audio = self.recorder.record(duration=duration)

        return audio

    def recognize(self, audio):
        print("\nRecognizing speech...")

        result = self.stt.transcribe_audio(audio)

        recognized_text = result["text"]

        print("Recognized:", recognized_text)

        return recognized_text

    def speak(self, text):
        if not text.strip():
            return

        output_path = self.output_directory / "response.wav"

        print("\nVoxPilot is speaking...")

        self.tts.text_to_speech(
            text,
            str(output_path)
        )

        audio, sample_rate = sf.read(
            str(output_path),
            dtype="float32"
        )

        sd.play(audio, sample_rate)
        sd.wait()

        print("Speech playback completed.")

    def run_once(self, duration=5):
        audio = self.listen(duration)

        recognized_text = self.recognize(audio)

        if not recognized_text:
            print("No speech recognized.")
            return None

        self.speak(recognized_text)

        return {
            "recognized_text": recognized_text,
            "status": "completed"
        }
