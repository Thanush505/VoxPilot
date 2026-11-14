# VoxPilot 🎙️🤖
**Your Local-First AI Desktop Control Assistant (v0.5.2)**

VoxPilot is a privacy-first personal AI desktop assistant for Windows 11. Powered by local LLMs via **Ollama (`qwen3:4b`)** by default—with optional **BYOK (OpenAI)** protected by **Windows DPAPI**—alongside LangGraph agent workflows, local speech processing (**Faster-Whisper** & **Piper TTS**), and native OS/browser automation (**pywinauto**, **PyAutoGUI**, **Playwright**), VoxPilot executes permitted desktop, file, browser, email, and custom automation workflows through natural language voice and text commands.

---

## 🧭 Project Vision & Core Principles

- **Local-First & Private by Default:** Speech-to-text, text-to-speech, memory, personalization, and default LLM inference (`qwen3:4b`) run locally on your machine. External AI requests are never sent unless you explicitly configure and select **BYOK**.
- **Computer Operator, Not Just a Chatbot:** Understands multi-step user intent and safely performs permitted Windows desktop, file, browser, email, and automation tasks.
- **Fail-Closed Safety Guardrails:** Strict tool allowlisting, Pydantic input validation, cryptographic single-use confirmation tokens for consequential actions, and a system-wide **`Ctrl + Alt + Escape`** Emergency Stop.
- **Multi-Profile & Hardware-Aware:** Per-profile isolation for memories, preferences, email accounts, automations, themes, and AI provider selection, budgeted for 16 GB RAM / 6 GB VRAM laptops.

---

## 🛠️ Technology Stack

| Component | Technology | Role / Purpose |
|---|---|---|
| **Operating System** | Windows 11 | Target host platform |
| **Backend Framework** | FastAPI + Uvicorn | Local REST API (`127.0.0.1`), execution dispatch, provider & profile management |
| **Agent Orchestration** | LangGraph | 6-node stateful workflow, planning, validation, confirmation checkpoints |
| **Local AI Runtime (Default)** | Ollama (`qwen3:4b`) | Default local LLM inference with `ModelManager` & `HardwareMonitor` tracking |
| **BYOK AI Provider (Optional)** | OpenAI REST (`httpx`) + Windows DPAPI | User-supplied API keys encrypted per profile via `CryptProtectData` |
| **Speech-to-Text (STT)** | Faster-Whisper (CTranslate2) + Silero/WebRTC VAD | Local offline transcription and streaming voice activity detection |
| **Text-to-Speech (TTS)** | Piper TTS (`en_US-lessac-medium` ONNX) | Local neural voice synthesis |
| **Desktop & Browser Automation** | pywinauto, PyAutoGUI, Playwright | Windows UI automation, window management, filesystem, and web research |
| **Database & ORM** | SQLite + SQLModel + aiosqlite | Conversations, memories, preferences, profiles, provider configs, automation history |
| **Frontend Dashboard** | React 18 + TypeScript + Vite 6 | Chat workspace, Voice Mode, Executions, Confirmations, Automations, Settings, Themes |
| **Testing** | pytest, Playwright & Vitest | Unit, integration, security, browser, and UI verification |

---

## 📊 Implemented Capabilities (Phases 1A – 8C)

| Phase / Area | Implemented Capabilities |
|---|---|
| **Core Agent & Safety** | 6-node LangGraph workflow, 37 allowlisted tools, dynamic re-planning, sliding-window rate limiting, 7-stage tool validation, `Ctrl + Alt + Escape` Emergency Stop, sanitized JSON/CSV audit export |
| **Phase 1A: Hardware & Model Lifecycle** | Live CPU/RAM/GPU/Ollama telemetry (`HardwareMonitor`) and safe model load/unload/inference locking (`ModelManager`) |
| **Phase 1B: Persistent Local Memory** | Profile-isolated long-term memory extraction, search, CRUD APIs, and conversation context hydration |
| **Phases 2A–2C: Personalization, Profiles & Themes** | Explicit user preferences, multi-profile switching & isolation (`default`, `work`, `personal`, etc.), Light/Dark/System modes with 6 accent color presets |
| **Screen Awareness & Local Vision** | Explicit on-demand primary-display capture, ephemeral profile-isolated storage, and local Ollama vision model gating (`moondream`, `qwen2.5vl:3b`) |
| **Phases 4A–4D: Specialist AI Assistants** | Desktop control, **File Assistant** (document Q&A, summarization, comparison), **Browser Research** (source-grounded, SSRF & prompt-injection protected), and **Email Assistant** (IMAP/SMTP + DPAPI + confirmation-gated sending) |
| **Phases 5A–5C: Startup, Voice & Notifications** | Windows **Wake-on-Startup** (`HKCU` Run key), **Advanced Voice Experience** (push-to-talk, `Ctrl + Shift + V`, wake word, barge-in interruption, mic testing), and native **Windows Desktop Notifications** |
| **Phases 6A–6B: Custom Automations & History** | Natural-language & visual **Automation Builder**, scheduled/manual triggers, step-level persistent execution history, safe retry, and history management |
| **Phases 7A–7B: Audit & Packaging** | Release-readiness audit, PyInstaller one-folder distribution (`VoxPilot.spec`), Inno Setup Windows installer (`installer/VoxPilot.iss`), and in-app updater |
| **Phases 8A–8C: Three-Mode AI Provider Architecture** | `AIProvider` / `AIProviderRegistry` foundation, Windows DPAPI encrypted credential storage, **OpenAI BYOK** adapter, Settings UI card, active-provider indicator in Agent workspace, and unified runtime routing across agent & specialist workflows |

---

## 🧠 Three-Mode AI Provider Architecture (Local / BYOK / Cloud)

VoxPilot supports a profile-isolated provider architecture documented in [`docs/AI_PROVIDER_ARCHITECTURE.md`](./docs/AI_PROVIDER_ARCHITECTURE.md):

1. **Local AI (`local` — Default):** Uses local Ollama (`qwen3:4b`) with zero external data transmission.
2. **BYOK (`byok` — OpenAI):** Users can optionally save an OpenAI API key in **Settings → AI Provider & BYOK (OpenAI)**.
   - Keys are encrypted per Windows user and per profile using **Windows DPAPI** (`CryptProtectData` / `CryptUnprotectData`) and stored outside SQLite config JSON in `<user_data_dir>/credentials/`.
   - Saving a key **never** auto-activates BYOK; Local AI remains active until you explicitly switch.
   - **No Silent Fallback:** If BYOK fails (invalid key, quota/rate limit, timeout, network error) or lacks a capability (e.g. `vision`), VoxPilot surfaces an actionable error instead of silently switching providers.
   - **Local-Only Boundaries:** Microphone audio (Faster-Whisper STT), voice output (Piper TTS), tool execution, and local vision remain strictly local.
3. **VoxPilot Cloud (`voxpilot_cloud`):** Reserved placeholder for a future release.

---

## 📂 Project Structure

```text
VoxPilot/
├── backend/
│   ├── main.py                      # FastAPI entrypoint & static frontend host
│   ├── paths.py                     # Dev & PyInstaller runtime path resolution
│   ├── agent/                       # LangGraph agent (graph, intent, planner, llm, tool_executor, rate_limiter)
│   ├── ai/
│   │   ├── providers/               # base, registry, local_ollama, credentials (DPAPI), openai_adapter, runtime, placeholders
│   │   ├── hardware_monitor.py      # Host CPU/RAM/GPU & Ollama telemetry
│   │   ├── model_manager.py         # Ollama model lifecycle & inference locking
│   │   ├── file_assistant.py        # Document extraction, Q&A & comparison
│   │   ├── browser_research.py      # Source-grounded web research agent
│   │   ├── email_assistant.py       # IMAP/SMTP email assistant + DPAPI + send confirmation
│   │   └── vision.py                # On-demand screen capture & local vision provider
│   ├── api/                         # REST routers (agent, providers, system, memory, personalization, profiles, screen, email, automations)
│   ├── automation/                  # Desktop, browser, startup, notifications, emergency_stop, custom_automations
│   ├── database/                    # SQLite async/sync engines, SQLModel schemas, repository, audit_export
│   ├── memory/                      # Long-term memory extraction, hydration & context management
│   ├── personalization/             # Per-profile user preferences
│   ├── profiles/                    # Multi-profile management service
│   ├── security/                    # Secret scrubbing & trust-boundary helpers
│   ├── tools/                       # 37 allowlisted Pydantic-validated tool wrappers
│   └── voice/                       # Faster-Whisper STT, Piper TTS, VAD, wake word & VoicePipeline
├── frontend/
│   └── src/
│       ├── api/                     # Typed API client & TypeScript contracts
│       ├── components/              # agent, automation, confirmations, dashboard, executions, layout, safety, settings
│       ├── pages/                   # DashboardPage, AgentPage, AutomationPage, ExecutionsPage, ConfirmationsPage, SettingsPage
│       ├── theme/                   # Light/Dark/System & 6 accent presets
│       └── tests/                   # Vitest unit & API security suites
├── docs/
│   └── AI_PROVIDER_ARCHITECTURE.md  # Provider contracts, DPAPI storage & runtime routing docs
├── installer/                       # Inno Setup installer script (VoxPilot.iss)
├── models/                          # Local Piper TTS & wake-word ONNX assets
├── requirements/                    # Modular Python dependency manifests
├── tests/                           # Pytest backend, integration & Playwright browser tests
├── VoxPilot.spec                    # PyInstaller packaging specification
└── README.md
```

---

## 🚀 Getting Started

### 1. Prerequisites
- **Windows 11**
- **Python 3.12**
- **Node.js 18+** (for frontend development/builds)
- **Ollama** installed ([ollama.com](https://ollama.com)) with `qwen3:4b` pulled for Local AI:
  ```powershell
  ollama pull qwen3:4b
  ```

### 2. Backend Setup
```powershell
cd C:\Users\Thanu\OneDrive\Desktop\VoxPilot
.\.venv\Scripts\activate
pip install -r requirements.txt
Copy-Item .env.example .env
```

Start the FastAPI backend:
```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

### 3. Frontend Setup & Build
```powershell
# Development server (http://127.0.0.1:5173)
npm --prefix frontend run dev

# Production build (outputs to frontend/dist)
npm --prefix frontend run build
```

---

## 🧪 Running Tests

### Backend (Pytest)
```powershell
# Run non-live backend test suite
.\.venv\Scripts\python.exe -m pytest tests/ -v -m "not live"

# Run AI provider & runtime integration suites (Phases 8A–8C)
.\.venv\Scripts\python.exe -m pytest tests/test_phase8a_providers.py tests/test_phase8b_byok_openai.py tests/test_phase8c_runtime_integration.py -q

# Run opt-in live tests
.\.venv\Scripts\python.exe -m pytest -m live -v
```

### Frontend (Vitest)
```powershell
npm --prefix frontend run test
```

---

## 📦 Standalone Windows Distribution & Installer

VoxPilot can be packaged into a self-contained Windows one-folder bundle via PyInstaller and an Inno Setup installer (`PrivilegesRequired=lowest`, per-user install in `%LOCALAPPDATA%\Programs\VoxPilot`, persistent user data isolated in `%LOCALAPPDATA%\VoxPilot`):

```powershell
# 1. Build frontend assets
npm --prefix frontend run build

# 2. Build PyInstaller bundle (dist/VoxPilot/VoxPilot.exe)
.\.venv\Scripts\pyinstaller.exe VoxPilot.spec --noconfirm

# 3. Compile Inno Setup installer (optional)
& "C:\Users\Thanu\AppData\Local\Programs\Inno Setup 6\ISCC.exe" installer\VoxPilot.iss
```

---

## 🛡️ Security & Trust Boundaries

- **Localhost Binding & CSRF Protection:** FastAPI binds to `127.0.0.1` with CORS and `Origin` validation.
- **DPAPI Secret Isolation:** Email passwords and BYOK OpenAI keys are encrypted via Windows DPAPI and never stored in plaintext or returned in API responses/browser storage.
- **Confirmation Cryptography:** Sensitive operations (destructive file actions, email sending, automation steps requiring approval) use single-use 256-bit tokens bound to parameter hashes.
- **Global Emergency Stop:** Pressing **`Ctrl + Alt + Escape`** latches emergency stop immediately, cancels active tool/LLM work, releases physical modifiers, and requires explicit operator reset when no operations remain in-flight.

---

## 📖 Governance & Architecture Documentation

- [`docs/AI_PROVIDER_ARCHITECTURE.md`](./docs/AI_PROVIDER_ARCHITECTURE.md) — Three-mode AI provider contracts, DPAPI credential storage, and runtime routing.
- [`PROJECT_MASTER_PROMPT.md`](./PROJECT_MASTER_PROMPT.md) — Project charter and engineering rules.
- [`PROJECT_STATUS.md`](./PROJECT_STATUS.md) — Development phase tracker.
- [`TECHNICAL_DECISIONS.md`](./TECHNICAL_DECISIONS.md) — Architecture decision records.
- [`CHANGELOG.md`](./CHANGELOG.md) — Detailed change history.
- [`TESTING.md`](./TESTING.md) — Test execution logs and verification notes.
