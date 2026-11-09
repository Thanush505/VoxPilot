# VoxPilot — Testing Record

This file documents all verification commands executed, their environments, and their actual outputs.

---

## Test Run: 2026-11-10 — Environment & Dependency Check

- **Scope:** Phase 1 Foundation
- **Target:** Python 3.12 Virtual Environment (`.venv`)

### 1. Dependency Integrity Check
- **Command:**
  ```powershell
  .\.venv\Scripts\python.exe -m pip check
  ```
- **Result:** PASS
- **Output:** `No broken requirements found.`

### 2. Dependency Resolution Check
- **Command:**
  ```powershell
  .\.venv\Scripts\python.exe -m pip install --dry-run -r requirements.txt
  ```
- **Result:** PASS
- **Output:** All requirements satisfied without version conflict.

### 3. Core Package Import Check
- **Command:**
  ```powershell
  .\.venv\Scripts\python.exe -c "import langgraph, langchain, langchain_ollama, faster_whisper, piper, openwakeword, pyautogui, pywinauto, fastapi, sqlmodel, playwright, mcp; print('ALL_IMPORTS_SUCCESSFUL')"
  ```
- **Result:** PASS (Importable)
- **Note:** Confirms package installation only. Runtime tests for hardware devices (mic/GPU/speakers) and models remain pending per Phase 1 & 2 completion criteria.

---

## Test Run: 2026-11-10 — Voice Subsystem Unit Tests

- **Scope:** Phase 2 Voice Foundation (`backend/voice`)
- **Target:** `tests/test_voice.py`
- **Command:**
  ```powershell
  .\.venv\Scripts\pytest.exe tests/test_voice.py -v
  ```
- **Result:** PASS (7 passed in 1.09s)
- **Detailed Results:**
  - `test_audio_recorder_initialization`: PASSED (Parameter validation and error guard)
  - `test_audio_recorder_save_recording`: PASSED (PCM WAV file creation via soundfile)
  - `test_speech_to_text_initialization`: PASSED (Default model size and device configuration)
  - `test_speech_to_text_validation`: PASSED (Input type and empty array error handling)
  - `test_text_to_speech_initialization`: PASSED (Model path discovery and lazy voice check)
  - `test_text_to_speech_validation`: PASSED (Empty text and unloaded model guards)
  - `test_voice_pipeline_initialization`: PASSED (Full VoicePipeline composition)

