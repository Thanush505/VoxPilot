"""
VoxPilot - Voice Subsystem
Provides audio recording, speech recognition (STT), voice synthesis (TTS),
and the integrated voice pipeline.
"""

from backend.voice.audio_recorder import AudioRecorder, AudioRecorderError
from backend.voice.audio_preprocessor import AudioPreprocessor, AudioPreprocessorError
from backend.voice.streaming_vad import StreamingVAD, StreamingVADError
from backend.voice.wake_word import WakeWordDetector, WakeWordError
from backend.voice.speech_to_text import SpeechToText, SpeechToTextError
from backend.voice.text_to_speech import TextToSpeech, TextToSpeechError
from backend.voice.voice_pipeline import VoicePipeline

__all__ = [
    "AudioRecorder",
    "AudioRecorderError",
    "AudioPreprocessor",
    "AudioPreprocessorError",
    "StreamingVAD",
    "StreamingVADError",
    "WakeWordDetector",
    "WakeWordError",
    "SpeechToText",
    "SpeechToTextError",
    "TextToSpeech",
    "TextToSpeechError",
    "VoicePipeline",
]
