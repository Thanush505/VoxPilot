# VoxPilot — Project Status

**Current Development Phase:** Phase 1 — Environment and Project Foundation  
**Last Updated:** 2026-11-10  

---

## 1. Phase Progress Overview

| Phase | Description | Status |
|---|---|---|
| **Phase 1** | **Environment and Project Foundation** | **In Progress** |
| **Phase 2** | **Voice Interface (Whisper STT + Piper TTS)** | **Completed** |
| Phase 3 | Desktop Automation (Windows UI, Apps, Files) | Not Started |
| Phase 4 | AI Agent Integration (LangGraph + Ollama) | Not Started |
| Phase 5 | Agent Intelligence and Safety (Guardrails, Approvals) | Not Started |
| Phase 6 | Frontend Dashboard (React + Vite) | Not Started |
| Phase 7 | Testing and Packaging (End-to-End, PyInstaller) | Not Started |

---

## 2. Completed Features
- [x] Project master rules and charter established ([`PROJECT_MASTER_PROMPT.md`](./PROJECT_MASTER_PROMPT.md)).
- [x] Python 3.12 virtual environment (`.venv`) initialized and populated.
- [x] Dependencies organized into modular specification files in [`requirements/`](./requirements/):
  - `base.txt` (FastAPI, SQLModel, Pydantic, uvicorn, etc.)
  - `ai.txt` (LangGraph, LangChain, Ollama, MCP)
  - `voice.txt` (Faster-Whisper, Piper-TTS, openWakeWord, sounddevice)
  - `automation.txt` (PyAutoGUI, pywinauto, pywin32, Playwright)
  - `development.txt` (pytest, pytest-asyncio, pyinstaller)
- [x] `.gitignore` established for Python, virtualenv, databases, models, and cache files.
- [x] `.env.example` created with template configuration variables.
- [x] Directory scaffolding created for `backend/`, `frontend/`, `models/`, `tests/`, and `docs/`.
- [x] Core Voice Subsystem implemented in [`backend/voice/`](./backend/voice/):
  - `audio_recorder.py`: Microphone capture, device enumeration, streaming recording, and energy VAD.
  - `speech_to_text.py`: Faster-Whisper wrapper with auto hardware selection, INT8 CPU fallback, and audio preprocessing.
  - `text_to_speech.py`: Piper TTS wrapper with auto voice model acquisition, sounddevice audio output, and emergency stop.
  - Unit test suite [`tests/test_voice.py`](./tests/test_voice.py) created and verified (6/6 tests passing).

---

## 3. Features In Progress (Phase 1)
- [ ] Verify local Ollama runtime installation and model availability (target: Qwen 3B/4B class quantized).
- [ ] Implement backend configuration loading (`backend/config/` with `pydantic-settings`).
- [ ] Create minimal FastAPI entrypoint (`backend/main.py`) and health-check endpoint.
- [ ] Run automated tests for FastAPI startup and Ollama local model connectivity.

---

## 4. Features Not Started
- Phase 2 through Phase 7 features (Voice pipeline, Desktop automation, LangGraph agent workflows, React frontend, Packaging).

---

## 5. Known Issues & Hardware Considerations
- System hardware: 16GB DDR5 RAM, Intel Core 5 205H, NVIDIA RTX 3050 (6GB VRAM).
- GPU VRAM constraint: 6GB VRAM requires lightweight quantized LLMs (e.g., Qwen 2.5 3B/Qwen 1.5/2.5 4B) and careful memory budgeting. Faster-Whisper can fall back to CPU with INT8 compute if VRAM is congested.

---

## 6. Latest Successful Tests
- `python -m pip check`: Passed (no broken requirements).
- `pip install --dry-run -r requirements.txt`: Passed (all dependencies resolved cleanly without conflicts).
- Core package import check: Passed (LangGraph, LangChain, langchain-ollama, Faster-Whisper, Piper TTS, openWakeWord, PyAutoGUI, pywinauto, FastAPI, SQLModel, Playwright, MCP).

---

## 7. Next Immediate Task
Verify Ollama installation and availability on Windows, then test a local lightweight model (e.g. Qwen 2.5 3B/7B quantized or test prompt) per Phase 1 completion criteria.

