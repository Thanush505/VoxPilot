# VoxPilot 🎙️🤖
**Your Personal AI Desktop Control Agent**

VoxPilot is an intelligent, privacy-first personal desktop control assistant for Windows 11. Powered by local LLMs via Ollama, LangGraph agent workflows, local speech processing (Faster-Whisper & Piper-TTS), and native OS/browser automation (pywinauto, PyAutoGUI, Playwright), VoxPilot executes permitted operations on your computer using natural language voice and text commands.

---

## 🧭 Project Vision & Core Principles

- **Local-First & Private:** Speech-to-text, LLM planning, and voice synthesis run locally without external cloud dependencies or paid APIs.
- **Computer Operator, Not Just a Chatbot:** Understands multi-step user intent and directly performs permitted system actions.
- **Security & Safety Guardrails:** Strict tool allowlisting, Pydantic input validation, and mandatory confirmation before any destructive or irreversible operation.
- **Hardware-Optimized:** Designed and budgeted specifically for 16GB RAM and 6GB GPU VRAM (ASUS TUF Gaming F16 / RTX 3050).

---

## 🛠️ Technology Stack

| Component | Technology | Role / Purpose |
|---|---|---|
| **Operating System** | Windows 11 | Target host platform |
| **Backend Framework** | FastAPI + Uvicorn + WebSockets | API endpoints, task dispatch, real-time messaging |
| **Agent Orchestration** | LangGraph | State management, tool calling, execution planning, human approval |
| **Local LLM Runtime** | Ollama (`qwen2.5:3b`) | Intent understanding and structured task planning within 6GB VRAM |
| **Speech-to-Text (STT)** | Faster-Whisper (CTranslate2) | High-speed offline transcription (CPU INT8 / CUDA FP16) |
| **Text-to-Speech (TTS)** | Piper TTS (`en_US-lessac-medium`) | Fast, local neural voice synthesis with ONNX runtime |
| **Desktop Automation** | pywinauto & PyAutoGUI | Windows UI Automation, window management, keyboard/mouse actions |
| **Browser Automation** | Playwright Python | Headless and headed web browsing and data extraction |
| **Database & ORM** | SQLite + SQLModel + aiosqlite | Task history, execution logs, and application settings |
| **Frontend Dashboard** | React + Vite | Web/desktop interface for voice, text, status, and monitoring |
| **Testing** | pytest & pytest-asyncio | Unit, integration, and end-to-end verification |

---

## 📊 Current Implementation Status

| Milestone | Scope | Status | Notes |
|---|---|---|---|
| **Phase 1: Foundation** | Environment, dependencies, config, Ollama | **Completed** | Python 3.12 `.venv` active, modular requirements, Ollama verified |
| **Phase 2: Voice Interface** | Microphone capture, STT, TTS, Pipeline | **Completed** | Full voice round-trip working (`VoicePipeline`, 73 tests) |
| **Phase 3: Desktop & Browser Automation** | App launch, windows, keyboard/mouse, files, screenshots, browser, confirmation manager | **Completed & Approved** | Full desktop & browser automation engine complete (380 unit tests, 3 live tests) |
| **Phase 4: Agent Integration** | LangGraph StateGraph, tool registry (37 tools, 11 aliases), tool executor, SQLite persistence, conversational memory, dynamic replanning, FastAPI API | **Completed & Approved** | 6-node LangGraph execution loop, dynamic replanner, 483 total tests passing (0 failed) |
| **Phase 5: Agent Safety** | Global emergency stop, hotkey listener, input release, safe cancellation, safety guardrails | **In Progress (Step 5.1 Complete)** | Global hotkey `Ctrl+Alt+Escape`, fail-closed operation tracking, safe reset validation, 522 tests passing |
| **Phase 6: Frontend** | React + Vite UI dashboard | *Pending* | Scaffolding in `frontend/` |
| **Phase 7: Packaging** | PyInstaller Windows executable | *Pending* | Test suite active in `tests/` |

---

## 📂 Project Structure

```
VoxPilot/
│
├── .venv/                         # Python 3.12 virtual environment
│
├── requirements/                  # Modular dependency manifests
│   ├── base.txt                   # FastAPI, SQLModel, Pydantic, uvicorn
│   ├── ai.txt                     # LangGraph, LangChain, Ollama, MCP
│   ├── voice.txt                  # Faster-Whisper, Piper-TTS, sounddevice, soundfile
│   ├── automation.txt             # PyAutoGUI, pywinauto, pywin32, Playwright
│   └── development.txt            # pytest, pytest-asyncio, pyinstaller
│
├── backend/                       # Application backend
│   ├── __init__.py
│   ├── main.py                    # FastAPI entrypoint (health & WebSocket routes)
│   ├── voice/                     # Voice subsystem (Phase 2 completed)
│   │   ├── __init__.py
│   │   ├── wake_word.py           # Offline wake-word detection (openWakeWord ONNX)
│   │   ├── audio_recorder.py      # Microphone capture & device enumeration
│   │   ├── streaming_vad.py       # Real-time WebRTC streaming speech detector
│   │   ├── audio_preprocessor.py  # Software peak gain normalizer (-3 dBFS)
│   │   ├── speech_to_text.py      # Faster-Whisper transcription engine + Silero VAD
│   │   ├── text_to_speech.py      # Piper TTS ONNX synthesis engine
│   │   └── voice_pipeline.py      # Unified Listen -> Transcribe -> Speak pipeline
│   ├── agent/                     # LangGraph workflows and planner
│   │   ├── graph.py
│   │   ├── planner.py
│   │   └── state.py
│   ├── tools/                     # Allowlisted automation tools
│   │   ├── __init__.py
│   │   ├── applications.py        # Safe application launch tool wrapper (Pydantic)
│   │   ├── windows.py             # Window discovery & management tool wrapper (Pydantic)
│   │   ├── keyboard.py            # Keyboard action tool wrappers (type, press, shortcut)
│   │   ├── mouse.py               # Mouse action tool wrappers (move, click, scroll)
│   │   ├── files.py               # Safe file & directory operations (list, search, move, delete)
│   │   ├── screenshots.py         # Safe screenshot capture tools (desktop, monitor, window)
│   │   ├── browser.py             # Playwright browser interactions
│   │   └── system.py              # System stats & hardware monitoring (psutil)
│   ├── automation/                # Desktop execution wrappers
│   │   ├── __init__.py
│   │   ├── application_launcher.py # Safe application discovery & launching engine
│   │   ├── window_manager.py      # Window discovery, validation & management engine
│   │   ├── keyboard_controller.py # Low-level Win32 SendInput keyboard engine
│   │   ├── mouse_controller.py    # Multi-monitor bounds-checked mouse engine
│   │   ├── file_manager.py        # Safe filesystem engine with approved directory boundaries
│   │   ├── screenshot_controller.py # Desktop, monitor & window screenshot engine
│   │   └── browser_controller.py  # Safe Playwright browser engine with profile isolation
│   ├── database/                  # SQLite persistence (SQLModel + aiosqlite)
│   │   ├── models.py              # Conversation, execution & step records
│   │   ├── connection.py          # Async & sync engine factories
│   │   └── repository.py          # Database repository with CRUD operations
│   ├── memory/                    # Short-term conversational memory
│   │   └── context_manager.py     # Multi-turn context manager & reference resolution
│   └── api/                       # FastAPI REST API routes
│       └── agent.py               # Agent execution, confirmation & conversation routes
│
├── frontend/                      # React + Vite user interface
├── models/                        # Offline model storage
│   ├── wake_word/                 # Wake word models & training guidelines
│   └── piper/                     # Piper TTS ONNX weights & generated audio
│       ├── en_US-lessac-medium.onnx
│       ├── en_US-lessac-medium.onnx.json
│       └── generated/
│
├── tests/                         # Automated test suites
│   ├── test_voice.py              # Voice subsystem component tests
│   ├── test_microphone_selection.py # Hardware discovery & resampling tests
│   ├── test_audio_preprocessor.py # Gain normalization & clipping protection tests
│   ├── test_vad.py                # Batch Silero VAD tests
│   ├── test_streaming_vad.py      # Real-time WebRTC streaming VAD tests
│   ├── test_wake_word.py          # Offline wake word tests
│   ├── test_application_launcher.py # Safe application launching tests
│   └── test_window_manager.py     # Window identification & management tests (151 unit tests passing)
│
├── docs/                          # Architecture & design documentation
├── .env.example                   # Environment configuration template
├── .gitignore                     # Git rules (virtualenvs, models, caches)
├── pytest.ini                     # Pytest runner configuration
├── requirements.txt               # Master requirements linking all modules
├── PROJECT_MASTER_PROMPT.md       # Supreme project charter & execution rules
├── PROJECT_STATUS.md              # Live status and phase tracker
├── TECHNICAL_DECISIONS.md         # Architecture decision records (TDRs)
├── CHANGELOG.md                   # Detailed change history
├── TESTING.md                     # Test execution logs
└── README.md
```

---

## 🎙️ Voice Subsystem Overview

The voice subsystem (`backend/voice/`) provides end-to-end local speech interaction with real-time streaming endpoint detection, software gain normalization, neural ASR, and neural TTS:

```
[ Idle Microphone Monitoring ]
      │
      ▼
WakeWordDetector.listen_for_wake_word()  <── openWakeWord (80 ms frames, ONNX CPU)
      │ (Wake Word Detected! Stream Closed Cleanly)
      ▼
AudioRecorder.record_until_silence()     <── WebRTC Streaming VAD (Real-time endpointing)
      │
      ▼ (16 kHz mono float32)
AudioPreprocessor.process()              <── Controlled Peak Gain Normalization (-3.0 dBFS)
      │
      ▼
SpeechToText.transcribe_audio()          <── Faster-Whisper + Batch Silero VAD Filter
      │
      ▼ (transcribed text)
[ VoxPilot Agent / Ollama Qwen3 4B ]
      │
      ▼ (response text)
TextToSpeech.text_to_speech()            <── Piper TTS (en_US-lessac-medium.onnx)
      │
      ▼ (.wav output)
SoundDevice Playback
      │
      ▼
[ Speakers / Headphones ]
```

### Quick Example: Voice Pipeline

```python
from backend.voice.voice_pipeline import VoicePipeline

# Initialize the pipeline (loads Whisper base + Piper TTS)
pipeline = VoicePipeline(
    whisper_model="base",
    piper_model="models/piper/en_US-lessac-medium.onnx"
)
pipeline.initialize()

# Run a complete voice-in, voice-out round trip
result = pipeline.run_once(duration=5)
print(f"Recognized: {result['recognized_text']}")
```

### 🛑 Global Emergency Stop

VoxPilot includes an operating-system-level emergency stop feature providing instant, safe cancellation of all desktop and browser automation:

- **Windows Global Hotkey:** Press **`Ctrl + Alt + Escape`** anywhere in Windows to trigger emergency stop immediately.
  - Native Win32 `RegisterHotKey` listener runs on an independent background thread.
  - Immediately releases physical modifier keys (Ctrl, Alt, Shift, LWin, RWin) and mouse buttons (left, right, middle).
  - Terminate and unregister cleanly upon application shutdown.
- **Fail-Closed Architecture:**
  - Once triggered, the stop state latches active.
  - In-flight keyboard typing, mouse clicks, and Playwright browser sessions abort safely.
  - LangGraph workflows halt immediately, recording truthful `cancelled` status and preserving completed steps.
  - New operation registrations are strictly rejected.
  - Dynamic re-planning is blocked while emergency stop is active.
- **Safe Explicit Reset:**
  - Resets cannot be initiated by the LLM (reset is strictly omitted from `ToolRegistry`).
  - Restoring automation requires explicit operator action via the UI or `POST /agent/emergency-stop/reset`.
  - Rejects reset with HTTP 409 Conflict if active operations are still in-flight, preventing unsafe mid-action resumption.
  - Reset never auto-resumes interrupted executions.
- **REST API Endpoints:**
  - `POST /agent/emergency-stop`: Immediate trigger.
  - `GET /agent/emergency-stop/status`: Status query returning latch status and active operations.
  - `POST /agent/emergency-stop/reset`: Explicit reset validating safe terminal state.

---

## 🚀 Getting Started

### 1. Prerequisites
- **Windows 11**
- **Python 3.12**
- **Ollama** installed ([ollama.com](https://ollama.com))

### 2. Environment Setup

```powershell
# Clone or navigate to the repository
cd C:\Users\Thanu\OneDrive\Desktop\VoxPilot

# Activate the virtual environment
.\.venv\Scripts\activate
```

### 3. Install Dependencies

Install all dependencies via the master requirements file:
```powershell
pip install -r requirements.txt
```

Or install modular sets independently:
```powershell
pip install -r requirements/base.txt        # FastAPI, SQLModel, Pydantic
pip install -r requirements/ai.txt          # LangGraph, Ollama, MCP
pip install -r requirements/voice.txt       # Faster-Whisper, Piper, SoundDevice
pip install -r requirements/automation.txt  # pywinauto, PyAutoGUI, Playwright
pip install -r requirements/development.txt # pytest, pyinstaller
```

### 4. Configure Environment

Copy the template environment file:
```powershell
Copy-Item .env.example .env
```

### 5. Run Test Suite

```powershell
# Run full unit and safety test suite (519 tests)
pytest tests/ -v -m "not live"

# Run opt-in live tests (Notepad launch/window management and controlled browser automation)
pytest -m live -v
```

---

## 📖 Governance & Memory Documentation

All development on VoxPilot follows the strict rules established in the project charter:

- [`PROJECT_MASTER_PROMPT.md`](./PROJECT_MASTER_PROMPT.md) — Master vision, rules of engagement, and architectural rules.
- [`PROJECT_STATUS.md`](./PROJECT_STATUS.md) — Current development phase, completed tasks, and next steps.
- [`TECHNICAL_DECISIONS.md`](./TECHNICAL_DECISIONS.md) — Architectural decisions, hardware budgets, and safety specifications.
- [`CHANGELOG.md`](./CHANGELOG.md) — Record of completed additions, changes, and fixes.
- [`TESTING.md`](./TESTING.md) — Verified test runs, hardware checks, and benchmark results.
