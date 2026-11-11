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
| **Backend Framework** | FastAPI + Uvicorn (REST API) | Local REST API endpoints, task dispatch, execution status |
| **Agent Orchestration** | LangGraph | State management, tool calling, execution planning, human approval |
| **Local LLM Runtime** | Ollama (`qwen2.5:3b`) | Intent understanding and structured task planning within 6GB VRAM |
| **Speech-to-Text (STT)** | Faster-Whisper (CTranslate2) | High-speed offline transcription (CPU INT8 / CUDA FP16) |
| **Text-to-Speech (TTS)** | Piper TTS (`en_US-lessac-medium`) | Fast, local neural voice synthesis with ONNX runtime |
| **Desktop Automation** | pywinauto & PyAutoGUI | Windows UI Automation, window management, keyboard/mouse actions |
| **Browser Automation** | Playwright Python | Headless and headed web browsing and data extraction |
| **Database & ORM** | SQLite + SQLModel + aiosqlite | Task history, execution logs, and application settings |
| **Frontend Dashboard** | React + Vite | Web/desktop interface for chat, voice mode, execution history, safety controls, audit logs, and settings |
| **Testing** | pytest, Playwright & Vitest | Unit, integration, browser, and end-to-end verification (676 tests) |

---

## 📊 Current Implementation Status

| Milestone | Scope | Status | Notes |
|---|---|---|---|
| **Phase 1: Foundation** | Environment, dependencies, config, Ollama | **Completed** | Python 3.12 `.venv` active, modular requirements, Ollama verified |
| **Phase 2: Voice Interface** | Microphone capture, STT, TTS, Pipeline | **Completed** | Full voice round-trip working (`VoicePipeline`, 73 tests) |
| **Phase 3: Desktop & Browser Automation** | App launch, windows, keyboard/mouse, files, screenshots, browser, confirmation manager | **Completed & Approved** | Full desktop & browser automation engine complete (380 unit tests, 3 live tests) |
| **Phase 4: Agent Integration** | LangGraph StateGraph, tool registry (37 tools, 11 aliases), tool executor, SQLite persistence, conversational memory, dynamic replanning, FastAPI API | **Completed & Approved** | 6-node LangGraph execution loop, dynamic replanner, 483 total tests passing (0 failed) |
| **Phase 5: Agent Safety** | Global emergency stop, safe cancellation, sliding-window rate limiting, safety checkpoints, resource budgets, audit trail export | **Completed** | Emergency stop `Ctrl+Alt+Escape`, fail-closed operation tracking, verified operator recovery, 7-stage tool pipeline, JSON/CSV audit export, 560 tests passing |
| **Phase 6: Frontend Dashboard** | React + Vite UI dashboard (Agent chat, Voice Mode, execution history, confirmation center, emergency stop, automation monitoring, audit archive & download, Settings) | **Completed** | Full dark dashboard with typed API client, official VoxPilot branding, Voice Mode controls, customizable global shortcut (`Ctrl+Shift+V`), microphone testing, emergency stop controls, automation telemetry, audit archive & safe blob downloads. 35 Vitest tests, 8 Playwright browser tests, 641 pytest tests (676 total passing tests) |
| **Phase 7: Packaging & Verification** | Full-system integration testing, security audit, PyInstaller standalone distribution, Inno Setup Windows installer, Planner, Voice & Official Icon Branding | **Completed** | Step 7.1 full-system audit complete; Step 7.2 PyInstaller standalone one-folder distribution; Step 7.3 Inno Setup Windows installer (`VoxPilot-Setup.exe`, SHA-256 verified); Planner hardening, Voice Mode + Customizable Global Shortcut (`Ctrl+Shift+V`), and complete official multi-resolution Windows & Web icon branding (`assets/icons/voxpilot.ico`); 676 total automated tests passing (638 non-live pytest + 3 live + 35 vitest) |

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
│   │   ├── graph.py               # StateGraph workflow with safety checkpoints
│   │   ├── planner.py             # Plan generation and dynamic re-planner
│   │   ├── state.py               # VoxPilotState definitions
│   │   ├── tool_executor.py       # 7-stage safety evaluation pipeline & tool execution
│   │   ├── tool_registry.py       # 37 canonical tools allowlist & parameter schemas
│   │   └── rate_limiter.py        # In-process sliding-window rate limiter & budgets
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
│   │   ├── browser_controller.py  # Safe Playwright browser engine with profile isolation
│   │   └── emergency_stop.py      # Central emergency stop manager & Win32 hotkey listener
│   ├── database/                  # SQLite persistence (SQLModel + aiosqlite)
│   │   ├── models.py              # Conversation, execution & step records
│   │   ├── connection.py          # Async & sync engine factories
│   │   ├── repository.py          # Database repository with CRUD operations
│   │   └── audit_export.py        # Sanitized audit trail export (JSON & CSV)
│   ├── memory/                    # Short-term conversational memory
│   │   └── context_manager.py     # Multi-turn context manager & reference resolution
│   └── api/                       # FastAPI REST API routes
│       └── agent.py               # Agent execution, confirmation, emergency stop & audit routes
│
├── frontend/                      # React 18 + Vite 6 desktop control dashboard
│   ├── src/
│   │   ├── api/                   # Centralized API client & types (execute, confirm, emergency-stop)
│   │   ├── components/            # Reusable UI component foundation
│   │   │   ├── common/            # LoadingState, ErrorState, EmptyState, UpcomingSection
│   │   │   ├── dashboard/         # StatusCard, BackendStatus, SystemOverview, RecentActivity
│   │   │   ├── layout/            # Header, Sidebar, AppLayout, navTypes
│   │   │   ├── agent/             # ChatMessage, ChatInput, ConversationList, ExecutionProgress
│   │   │   ├── executions/        # ExecutionList, ExecutionDetails, ExecutionStep
│   │   │   ├── confirmations/     # ConfirmationCard, ConfirmationDialog
│   │   │   └── safety/            # EmergencyStopPanel, ResetConfirmationDialog
│   │   ├── hooks/                 # Periodic backend health polling hook (useBackendHealth)
│   │   ├── pages/                 # DashboardPage, AgentPage, ExecutionsPage, ConfirmationsPage
│   │   ├── styles/                # Global & dashboard dark styling
│   │   ├── tests/                 # Vitest unit & security test suites (14 tests)
│   │   ├── App.tsx
│   │   └── main.tsx
│   ├── index.html
│   ├── vite.config.ts
│   └── package.json
│
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
- **Safe Explicit Reset & Operator Recovery:**
  - Resets cannot be initiated by the LLM (reset is strictly omitted from `ToolRegistry`).
  - Restoring automation requires explicit operator action via the UI or `POST /agent/emergency-stop/reset`.
  - Rejects reset with HTTP 409 Conflict if active operations are still in-flight, preventing unsafe mid-action resumption.
  - Calling `reset_emergency_stop(force=True)` is strictly prohibited and raises `UnsafeResetError`.
  - Verified operator recovery (`POST /agent/emergency-stop/recover`) requires documented justification ($\ge 10$ chars), explicit enumeration of all confirmed terminated operations, and verifies process PID termination via `psutil`.
  - Reset never auto-resumes interrupted executions.
- **REST API Endpoints:**
  - `POST /agent/emergency-stop`: Immediate trigger.
  - `GET /agent/emergency-stop/status`: Status query returning latch status and active operations.
  - `POST /agent/emergency-stop/reset`: Safe explicit reset validating safe terminal state.
  - `POST /agent/emergency-stop/recover`: Authorized operator recovery with termination verification.

### 🛡️ Rate Limiting, Safety Checkpoints & Budgets

VoxPilot enforces strict execution limits and rate limiting to prevent runaways and resource exhaustion:

- **In-Process Sliding-Window Rate Limiting:**
  - Request rate limiting: 20 requests/minute globally, 10 requests/minute per conversation.
  - Execution concurrency: Maximum 2 concurrent agent executions.
  - Tool invocation rate: 60 tool calls/minute globally, 15 tool calls per execution, maximum 5 invocations of the same tool per execution.
  - Plan step budget: Maximum 10 plan steps per execution.
  - Wall-clock execution budget: Maximum 120 seconds per execution.
  - Browser safeguards: Maximum 5 navigations and 3 downloads per browser session.
  - Filesystem safeguards: Maximum 100 search results and 1,000 scanned items per directory search.
- **7-Stage Tool Executor Evaluation Pipeline:**
  1. Emergency Stop Check
  2. Execution Duration Budget Check
  3. Tool-Call Budget Check
  4. Tool-Specific Rate Limits (Repeat invocations, navigations, downloads)
  5. Parameter Schema Validation
  6. Human Confirmation Requirements (Destructive/consequential actions)
  7. Tool Execution under Active Operation Tracking
- **LangGraph Checkpoint Safety:** Checkpoints before execution, during plan validation, mid-execution in `execute_tool`, during result evaluation, before re-planning, and before resumption. Halts immediately upon budget exhaustion with status `rate_limited` (HTTP 429), preserving completed steps truthfully.

### 📁 Sanitized Audit Trail Export

- Queries persistent SQLite `ExecutionRecord` and `ExecutionStepRecord`.
- Exports execution history and step details to structured JSON or flattened CSV.
- **Sensitive Data Scrubber:** Recursively redacts tokens, confirmation tokens, passwords, API keys, and credentials (`"[REDACTED]"`).
- **Security Boundaries:** Enforces destination directory containment (`data/exports`), prevents path traversal, requires confirmation before overwriting, and never executes or opens exported files.
- Exposed via `POST /agent/audit/export` and `GET /agent/audit/exports/{export_id}/status` with CSRF Origin protection. LLM has zero access to export operations.

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

VoxPilot maintains a comprehensive test suite of **617 automated tests**:
- **Pytest Non-Live Suite:** 585 tests (577 backend unit/integration tests + 8 Playwright browser tests in `tests/test_frontend_browser.py` + 16 path resolution tests in `tests/test_paths.py`)
- **Pytest Live Suite:** 3 opt-in tests (Notepad launch, Notepad window management, Playwright browser automation)
- **Vitest Frontend Suite:** 29 tests (API client, UI security, emergency stop latching, audit downloads)
- **Standalone Distribution Suite:** Live execution and relocation verification via `scripts/verify_distribution.py`

```powershell
# Run full non-live test suite (585 tests: 577 backend + 8 Playwright browser)
pytest tests/ -v -m "not live"

# Run opt-in live tests (3 tests)
pytest -m live -v

# Run Playwright browser integration suite directly (8 tests)
pytest tests/test_frontend_browser.py -v

# Run runtime path resolution unit tests (16 tests)
pytest tests/test_paths.py -v

# Run standalone distribution and relocation verification
python scripts/verify_distribution.py
```

### 6. Frontend Setup & Development

```powershell
# Navigate to the frontend workspace
cd frontend

# Install Node dependencies
npm install

# Start the Vite development server (http://127.0.0.1:5173)
npm run dev

# Run frontend unit and security test suites (Vitest: 29 tests passing)
npm test

# Run linting and typecheck (0 errors, 0 warnings)
npm run lint
npm run typecheck

# Build for production (output in frontend/dist)
npm run build
```

### 7. 📦 Standalone Windows Distribution (PyInstaller)

VoxPilot can be built and distributed as a standalone, self-contained Windows application that requires no Python installation on target machines.

#### Distribution Architecture:
- **Packaging Mode:** PyInstaller one-folder distribution (`dist/VoxPilot/`).
- **Entrypoint:** `dist/VoxPilot/VoxPilot.exe` (launches background FastAPI server, pre-flight diagnostics, and opens the default web browser).
- **Single-Port Hosting:** Serves both the compiled React frontend dashboard (`dist/VoxPilot/_internal/frontend/dist`) and the REST API from a single local port (`http://127.0.0.1:8000`).
- **Bundled Assets:** Piper neural TTS voice model (`en_US-lessac-medium.onnx`) and Playwright node driver are bundled directly in `_internal/`.
- **Runtime Path Resolution (`backend.paths`):** Dynamic resolution distinguishes between dev mode (local repository) and frozen mode (PyInstaller bundle).
- **Persistent User Data:** All user data, state, and logs are strictly isolated from the application bundle and written to `%LOCALAPPDATA%\VoxPilot`:
  - Database: `%LOCALAPPDATA%\VoxPilot\data\voxpilot.db`
  - Logs: `%LOCALAPPDATA%\VoxPilot\logs\voxpilot.log`
  - Audit Exports: `%LOCALAPPDATA%\VoxPilot\exports\`
  - Screenshots: `%LOCALAPPDATA%\VoxPilot\screenshots\`
  - Browser Downloads: `%LOCALAPPDATA%\VoxPilot\downloads\`
  - Temporary Voice Audio: `%LOCALAPPDATA%\VoxPilot\voice_temp\`
- **Relocatability:** The `dist/VoxPilot` folder can be freely moved or copied anywhere on Windows (e.g. to `C:\Program Files\VoxPilot` or a USB drive) and operates completely independently.

#### Building the Standalone Distribution:
```powershell
# 1. Build the production React frontend
cd frontend
npm run build
cd ..

# 2. Package the standalone distribution using PyInstaller
.\.venv\Scripts\pyinstaller.exe VoxPilot.spec --noconfirm

# 3. Copy distribution documentation
Copy-Item README_DIST.txt dist\VoxPilot\README.txt -Force
```

#### Running the Standalone Application:
```powershell
# Run the executable directly
.\dist\VoxPilot\VoxPilot.exe

# Optional environment configurations:
$env:VOXPILOT_PORT = "8080"              # Custom port (default: 8000)
$env:VOXPILOT_AUTO_OPEN_BROWSER = "0"    # Disable auto-opening browser
.\dist\VoxPilot\VoxPilot.exe
```

#### External Prerequisites:
1. **Ollama LLM:** Ollama must be installed and running locally with the `qwen3:4b` model (`ollama serve`). If Ollama is offline, the startup pre-flight check logs a warning while permitting dashboard access and non-LLM operations.
2. **Playwright Chromium:** Playwright Chromium browser binaries should be installed on the host machine (`playwright install chromium`), which stores them in `%LOCALAPPDATA%\ms-playwright`.
3. **Emergency Stop:** Press **`Ctrl + Alt + Escape`** anywhere in Windows to immediately halt running automations.
4. **Global Voice Mode Shortcut:** Press **`Ctrl + Shift + V`** (customizable in Dashboard **Settings**) anywhere in Windows to toggle Voice Mode on/off even when another Windows application is in the foreground.

### 8. 💿 Windows Installer (Inno Setup) & First-Run Setup

For typical Windows 11 end-users, VoxPilot provides a professional, self-contained Windows setup executable built with Inno Setup.

#### Key Installer Features:
- **Installer Package:** `installer/Output/VoxPilot-Setup.exe` (~222.57 MB / 233,384,613 bytes).
- **Cryptographic Checksum (SHA-256):** `17D7B0C40E3B3542C709B5A0DCF24B083841DC9E56852C85FB185BC9B9AAD79B`
- **In-App Software Update System:** Check GitHub Releases (`Thanush505/VoxPilot`) directly from the dashboard or Settings page, stream the official `VoxPilot-Setup.exe` into `%LOCALAPPDATA%\VoxPilot\updates`, verify its SHA-256 checksum and Authenticode signature status, and launch the detached Windows updater after explicit user confirmation while preserving all `%LOCALAPPDATA%\VoxPilot` user data.
- **Official Application Branding:** Multi-resolution Windows icon (`assets/icons/voxpilot.ico` with 16x16, 24x24, 32x32, 48x48, 64x64, 128x128, and 256x256 frames) and PE version metadata embedded into `VoxPilot.exe`, `VoxPilot-Setup.exe`, Start Menu/Desktop shortcuts, and React web favicons.
- **Lowest-Privilege Execution (`PrivilegesRequired=lowest`):** Installs per-user into `%LOCALAPPDATA%\Programs\VoxPilot`. No Windows UAC elevation or administrator permissions are required.
- **Start Menu Shortcuts:**
  - `VoxPilot`: Launches the application and opens the dashboard in your default browser.
  - `Install Playwright Browser`: Sets up Playwright Chromium without needing Python or Node.js.
  - `VoxPilot Readme`: Quick access to release notes, dependency guides, and safety controls.
  - `Uninstall VoxPilot`: Standard Windows uninstaller shortcut (also registered in Windows Settings "Installed apps").
- **Optional Desktop Shortcut:** User-selectable task during setup.
- **Upgrade & Data Retention Safety Invariant:**
  - Application binaries install into `%LOCALAPPDATA%\Programs\VoxPilot`.
  - All user state, conversations, task history, and logs reside in `%LOCALAPPDATA%\VoxPilot`.
  - Upgrading to a new release or running the uninstaller leaves `%LOCALAPPDATA%\VoxPilot\data\voxpilot.db` 100% intact.
- **Single-Instance Protection:**
  - If VoxPilot is already running, launching the shortcut again detects the active daemon via `GET /health` and focuses/opens your existing browser dashboard rather than crashing on port 8000.
- **Bundled Zero-Python Browser Installer:**
  - End-users without Python or Node.js can install Playwright Chromium by running the Start Menu shortcut `Install Playwright Browser`, double-clicking `install_browser.bat`, or executing `VoxPilot.exe --install-browser`. It invokes the bundled Playwright Node runtime directly.

#### Building and Verifying the Installer:
```powershell
# Compile the installer using Inno Setup 6 (ISCC.exe)
& "C:\Users\Thanu\AppData\Local\Programs\Inno Setup 6\ISCC.exe" installer\VoxPilot.iss

# Run the end-to-end installer verification suite
python scripts\verify_installer.py
```

### 9. Communication Protocol & Security Trust Boundaries

- **Progress & Execution Protocol:** VoxPilot agent commands execute via standard asynchronous HTTP POST request-response (`POST /agent/execute`), with the frontend handling real-time execution state transitions. No WebSockets or Server-Sent Events (SSE) streaming connections are used.
- **Localhost Trust Boundary:** The FastAPI daemon binds strictly to `127.0.0.1`. CORS restrictions and `Origin` header validation protect against browser-based Cross-Site Request Forgery (CSRF). However, localhost HTTP does not authenticate the human operator against other local processes or multiple OS user accounts.
- **Confirmation Cryptography:** Consequential actions require single-use, 256-bit cryptographically secure tokens bound to parameter hashes via SHA-256 with a 5-minute TTL. Tokens are never exposed to LLM context, voice synthesis, or browser DOM.
- **Global Emergency Stop:** Fail-closed Win32 hotkey (`Ctrl + Alt + Escape`) immediately halts active operations and releases physical modifiers and mouse buttons. Reset requires explicit confirmation that zero operations remain active (HTTP 409 Conflict if active).

---

## 📖 Governance & Memory Documentation

All development on VoxPilot follows the strict rules established in the project charter:

- [`PROJECT_MASTER_PROMPT.md`](./PROJECT_MASTER_PROMPT.md) — Master vision, rules of engagement, and architectural rules.
- [`PROJECT_STATUS.md`](./PROJECT_STATUS.md) — Current development phase, completed tasks, and next steps.
- [`TECHNICAL_DECISIONS.md`](./TECHNICAL_DECISIONS.md) — Architectural decisions, hardware budgets, and safety specifications.
- [`CHANGELOG.md`](./CHANGELOG.md) — Record of completed additions, changes, and fixes.
- [`TESTING.md`](./TESTING.md) — Verified test runs, hardware checks, and benchmark results.
