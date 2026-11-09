# VoxPilot — Changelog

All notable changes to the VoxPilot project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased] - 2026-11-10

### Added
- Integrated [`backend/voice/voice_pipeline.py`](./backend/voice/voice_pipeline.py) coordinating end-to-end microphone listening, Faster-Whisper recognition, and Piper TTS playback.
- Acquired Piper neural voice model weights: `models/piper/en_US-lessac-medium.onnx` and JSON configuration.
- Comprehensive update to [`README.md`](./README.md) covering full architecture, hardware specifications, current roadmap status, voice subsystem pipeline, and modular setup instructions.
- Implemented `backend/voice` subsystem:
  - [`backend/voice/audio_recorder.py`](./backend/voice/audio_recorder.py): Microphone capture, device discovery, fixed/streaming recording, and silence/energy VAD.
  - [`backend/voice/speech_to_text.py`](./backend/voice/speech_to_text.py): Faster-Whisper integration with audio preprocessing, INT8 CPU / FP16 CUDA support, and detailed segment transcription.
  - [`backend/voice/text_to_speech.py`](./backend/voice/text_to_speech.py): Piper TTS integration with ONNX runtime, auto-download for standard `en_US-lessac-medium` voice, sounddevice playback, and emergency stop.
  - [`backend/voice/__init__.py`](./backend/voice/__init__.py): Clean exports for `AudioRecorder`, `SpeechToText`, and `TextToSpeech`.
- Added [`pytest.ini`](./pytest.ini) configuring test paths, pythonpath, and asyncio fixture scope.
- Added [`tests/test_voice.py`](./tests/test_voice.py) covering device discovery, initialization, preprocessing, and WAV persistence (6/6 tests passing).
- Created [`PROJECT_MASTER_PROMPT.md`](./PROJECT_MASTER_PROMPT.md) as the primary project charter and source of truth.
- Created project tracking and governance documentation:
  - [`PROJECT_STATUS.md`](./PROJECT_STATUS.md)
  - [`TECHNICAL_DECISIONS.md`](./TECHNICAL_DECISIONS.md)
  - [`CHANGELOG.md`](./CHANGELOG.md)
  - [`TESTING.md`](./TESTING.md)
- Created modular requirements structure in `requirements/`:
  - `requirements/base.txt`
  - `requirements/ai.txt`
  - `requirements/voice.txt`
  - `requirements/automation.txt`
  - `requirements/development.txt`
  - Root `requirements.txt` linking all modules.
- Created baseline configuration template [`.env.example`](./.env.example).
- Created [`.gitignore`](./.gitignore) covering virtual environments, models, caches, and databases.
- Initial directory scaffolding for `backend/`, `frontend/`, `models/`, `tests/`, and `docs/`.

