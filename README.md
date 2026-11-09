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
| **Phase 1: Foundation** | Environment, dependencies, config, Ollama | **In Progress** | Python 3.12 `.venv` active, modular requirements, Ollama verified |
| **Phase 2: Voice Interface** | Microphone capture, STT, TTS, Pipeline | **Completed** | Full voice round-trip working (`VoicePipeline`) |
| **Phase 3: Desktop Automation** | App launch, window control, files | *Pending* | Scaffolding created in `backend/tools/` |
| **Phase 4: Agent Integration** | LangGraph state, Ollama tool calling | *Pending* | Scaffolding created in `backend/agent/` |
| **Phase 5: Agent Safety** | Checkpoints, confirmations, emergency stop | *Pending* | Planned safety layer |
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
│   │   ├── audio_recorder.py      # Microphone capture & device enumeration
│   │   ├── speech_to_text.py      # Faster-Whisper transcription engine
│   │   ├── text_to_speech.py      # Piper TTS ONNX synthesis engine
│   │   └── voice_pipeline.py      # Unified Listen -> Transcribe -> Speak pipeline
│   ├── agent/                     # LangGraph workflows and planner
│   │   ├── graph.py
│   │   ├── planner.py
│   │   └── state.py
│   ├── tools/                     # Allowlisted automation tools
│   │   ├── applications.py        # Windows application launcher & process control
│   │   ├── browser.py             # Playwright browser interactions
│   │   ├── files.py               # Safe file & directory operations
│   │   └── system.py              # System stats & hardware monitoring (psutil)
│   ├── automation/                # Desktop execution wrappers
│   ├── memory/                    # SQLite context & session stores
│   └── api/                       # Route handlers
│
├── frontend/                      # React + Vite user interface
├── models/                        # Offline model storage
│   └── piper/                     # Piper TTS ONNX weights & generated audio
│       ├── en_US-lessac-medium.onnx
│       ├── en_US-lessac-medium.onnx.json
│       └── generated/
│
├── tests/                         # Automated test suites
│   └── test_voice.py              # Voice subsystem unit tests (7/7 passing)
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

The voice subsystem (`backend/voice/`) provides end-to-end local speech interaction:

```
[ Microphone ]
      │
      ▼
AudioRecorder.record()
      │
      ▼ (float32 numpy array)
SpeechToText.transcribe_audio()  <── Faster-Whisper (base, INT8/FP16)
      │
      ▼ (transcribed text)
[ Agent / User ]
      │
      ▼ (response text)
TextToSpeech.text_to_speech()   <── Piper TTS (en_US-lessac-medium.onnx)
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

### 5. Run Voice Subsystem Tests

```powershell
pytest tests/test_voice.py -v
```

---

## 📖 Governance & Memory Documentation

All development on VoxPilot follows the strict rules established in the project charter:

- [`PROJECT_MASTER_PROMPT.md`](./PROJECT_MASTER_PROMPT.md) — Master vision, rules of engagement, and architectural rules.
- [`PROJECT_STATUS.md`](./PROJECT_STATUS.md) — Current development phase, completed tasks, and next steps.
- [`TECHNICAL_DECISIONS.md`](./TECHNICAL_DECISIONS.md) — Architectural decisions, hardware budgets, and safety specifications.
- [`CHANGELOG.md`](./CHANGELOG.md) — Record of completed additions, changes, and fixes.
- [`TESTING.md`](./TESTING.md) — Verified test runs, hardware checks, and benchmark results.
