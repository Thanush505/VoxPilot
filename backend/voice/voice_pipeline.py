
import os
import uuid
from pathlib import Path
import sounddevice as sd
import soundfile as sf
from dotenv import load_dotenv

from backend.voice.audio_recorder import AudioRecorder
from backend.voice.speech_to_text import SpeechToText
from backend.voice.text_to_speech import TextToSpeech
from backend.voice.wake_word import WakeWordDetector, WakeWordError
from backend.agent.graph import VoxPilotAgent
from backend.automation.emergency_stop import get_shared_emergency_stop_manager
from typing import Optional

load_dotenv()


class VoicePipeline:
    def __init__(
        self,
        microphone_device=None,
        whisper_model="base",
        piper_model="models/piper/en_US-lessac-medium.onnx",
        ollama_model="qwen3:4b",
        use_streaming_vad=None,
        enable_wake_word=None,
        wake_word_model=None,
        wake_word_threshold=None,
        agent: Optional[VoxPilotAgent] = None,
    ):
        if microphone_device is None:
            env_dev = os.environ.get("VOXPILOT_MICROPHONE_DEVICE", "").strip()
            microphone_device = env_dev if env_dev else None

        if use_streaming_vad is None:
            env_val = os.environ.get("VOXPILOT_STREAMING_VAD_ENABLED", "").strip().lower()
            self.use_streaming_vad = env_val not in ("false", "0", "no") if env_val else True
        else:
            self.use_streaming_vad = bool(use_streaming_vad)

        # Wake word configuration
        if enable_wake_word is None:
            env_ww = os.environ.get("VOXPILOT_WAKE_WORD_ENABLED", "").strip().lower()
            self.enable_wake_word = env_ww in ("true", "1", "yes") if env_ww else False
        else:
            self.enable_wake_word = bool(enable_wake_word)

        if self.enable_wake_word:
            self.wake_detector = WakeWordDetector(
                enabled=True,
                model_path=wake_word_model,
                threshold=wake_word_threshold,
                device=microphone_device,
            )
        else:
            self.wake_detector = None

        self.recorder = AudioRecorder(
            sample_rate=16000,
            channels=1,
            device=microphone_device,
            streaming_vad_enabled=self.use_streaming_vad,
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

        if agent is not None:
            self.agent = agent
        else:
            self.agent = VoxPilotAgent(
                model=ollama_model
            )

        self.current_conversation_id = f"conv_voice_{uuid.uuid4().hex[:8]}"

        self.last_agent_result = None

        self.output_directory = Path("models/piper/generated")
        self.output_directory.mkdir(parents=True, exist_ok=True)

        self.exit_phrases = [
            "exit voxpilot",
            "stop voxpilot",
            "quit voxpilot",
            "goodbye voxpilot"
        ]

    def start_new_conversation(self) -> str:
        """Start a new independent conversation session with a fresh conversation ID."""
        self.current_conversation_id = f"conv_voice_{uuid.uuid4().hex[:8]}"
        return self.current_conversation_id

    def initialize(self):
        print("\nInitializing VoxPilot Voice Pipeline...")

        dev_info = self.recorder.get_selected_device_info()
        print(f"Active microphone: [{dev_info['index']}] {dev_info['name']} (Host API: {dev_info['host_api']})")
        vad_mode_str = "Enabled (WebRTC dynamic endpointing)" if self.use_streaming_vad else "Disabled (Fixed duration)"
        print(f"Streaming recording VAD: {vad_mode_str}")

        if self.wake_detector is not None:
            self.wake_detector.load_model()
            print(f"Wake word detection: Enabled ('{self.wake_detector.active_model_name}', threshold: {self.wake_detector.threshold})")
        else:
            print("Wake word detection: Disabled")

        self.stt.load_model()
        self.tts.load_model()

        print("Checking Ollama connection...")

        try:
            import ollama
            ollama.list()

            print("Ollama connection successful.")

        except Exception as error:
            raise RuntimeError(
                f"Could not connect to Ollama: {error}"
            ) from error

        print("Voice pipeline initialized successfully.")

    def wait_for_wake_word(self, timeout=None):
        """Listen for the wake word, completely closing the microphone upon detection.

        Returns:
            Optional[dict]: Detection event dictionary or None.
        """
        if self.wake_detector is None:
            return None
        return self.wake_detector.listen_for_wake_word(timeout=timeout)

    def listen(self, duration=5):
        if self.use_streaming_vad:
            audio = self.recorder.record_until_silence()
        else:
            print("\nVoxPilot is listening...")
            audio = self.recorder.record(duration=duration)

        return audio

    def recognize(self, audio):
        if audio is None or len(audio) == 0:
            return ""

        print("\nRecognizing speech...")

        result = self.stt.transcribe_audio(audio)

        recognized_text = result["text"].strip()

        print("Recognized:", recognized_text)

        return recognized_text

    def generate_response(self, user_message, conversation_id=None):
        manager = get_shared_emergency_stop_manager()
        if manager.is_emergency_stop_active():
            response = (
                "Emergency stop is currently active. All automated actions are blocked. "
                "Please reset emergency stop in the VoxPilot control panel."
            )
            print("\nVoxPilot response:", response)
            return response

        print("\nVoxPilot is processing your request...")

        conv_id = conversation_id or self.current_conversation_id
        result = self.agent.process_request(user_message, conversation_id=conv_id)

        self.last_agent_result = result

        if result.get("execution_status") == "cancelled":
            response = result.get("response") or "Execution was cancelled due to emergency stop."
            print("\nVoxPilot response:", response)
            return response

        if result.get("execution_status") == "confirmation_required":
            response = (
                "VoxPilot needs your approval before performing this action. "
                "Please review the pending confirmation in the application."
            )
            print("\nVoxPilot response:", response)
            return response

        if result.get("error") and result.get("execution_status") == "failed":
            print("\nAgent error:", result["error"])

            return result.get("response") or "Sorry, I could not process your request."

        intent = result.get("intent", "unknown")
        confidence = result.get("confidence", 0.0)
        plan = result.get("plan", [])

        print("\nRecognized intent:", intent)
        print("Confidence:", confidence)

        print("\nGenerated plan:")

        for index, step in enumerate(plan, start=1):
            if isinstance(step, dict):
                print(f"Step {index}: {step.get('action', 'unknown')}")
                print("Description:", step.get("description", ""))
            else:
                print(f"Step {index}: {step}")

        response = result.get(
            "response",
            "I have processed your request."
        )

        # Security check: NEVER speak confirmation tokens or raw tokens
        if "token" in response.lower() and ("approve" in response.lower() or "token:" in response.lower()):
            response = (
                "VoxPilot needs your approval before performing this action. "
                "Please review the pending confirmation in the application."
            )

        print("\nVoxPilot response:", response)

        return response

    def speak(self, text):
        if not text or not text.strip():
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

    def is_exit_command(self, text):
        normalized_text = text.lower().strip()

        return any(
            phrase in normalized_text
            for phrase in self.exit_phrases
        )

    def run_once(self, duration=5):
        audio = self.listen(duration)

        recognized_text = self.recognize(audio)

        if not recognized_text:
            print("No speech recognized.")
            return None

        ai_response = self.generate_response(recognized_text, conversation_id=self.current_conversation_id)

        self.speak(ai_response)

        return {
            "conversation_id": self.current_conversation_id,
            "recognized_text": recognized_text,
            "ai_response": ai_response,
            "status": "completed"
        }

    def run_conversation(self, duration=5):
        print("\n====================================")
        print("       VOXPILOT CONVERSATION")
        print("====================================")
        print("VoxPilot is ready.")
        if self.wake_detector is not None:
            print(f"Wake word active: Say '{self.wake_detector.active_model_name}' to activate.")
        print("Say 'Exit VoxPilot' to stop.")
        print("Press Ctrl+C to stop manually.")

        self.start_new_conversation()
        conversation_active = True

        try:
            while conversation_active:
                # If wake word detection is enabled, monitor for the wake phrase first
                if self.wake_detector is not None:
                    print(f"\nWaiting for wake word ('{self.wake_detector.active_model_name}')...")
                    detection = self.wait_for_wake_word()
                    if not detection:
                        continue
                    print(f"\n[Wake Word Detected: {detection['model']} (score: {detection['score']:.2f})]")
                    self.speak("Yes?")

                # AudioRecorder now captures the command with exclusive microphone ownership
                audio = self.listen(duration)

                recognized_text = self.recognize(audio)

                if not recognized_text:
                    print("No speech detected. Listening again...")
                    continue

                if self.is_exit_command(recognized_text):
                    goodbye_message = "Goodbye! See you later."

                    self.speak(goodbye_message)

                    conversation_active = False
                    break

                ai_response = self.generate_response(
                    recognized_text,
                    conversation_id=self.current_conversation_id,
                )

                self.speak(ai_response)

        except KeyboardInterrupt:
            print("\nConversation interrupted by user.")

        finally:
            sd.stop()
            if self.wake_detector is not None:
                self.wake_detector.stop()

            print("\n====================================")
            print("       VOXPILOT STOPPED")
            print("====================================")
