# VoxPilot — Technical Decisions Record (TDR)

This document tracks technical decisions, architecture choices, alternatives evaluated, and hardware-specific constraints for VoxPilot.

---

## TDR-001: Technology Stack Baseline

- **Date:** 2026-11-10
- **Status:** Approved
- **Decisions:**
  - **Backend:** FastAPI for asynchronous REST endpoints and WebSockets.
  - **Agent Framework:** LangGraph as primary agent state management, planning, and tool calling coordinator.
  - **LLM Runtime:** Local Ollama runtime using lightweight quantized models (Qwen 3B/4B class).
  - **Speech-to-Text:** Faster-Whisper (CTranslate2 backend) supporting CPU (INT8) or GPU (FP16).
  - **Text-to-Speech:** Piper TTS for fast local speech generation.
  - **Desktop Automation:** pywinauto (Windows UI Automation) with PyAutoGUI fallback.
  - **Browser Automation:** Playwright Python for reliable headless/headed web navigation.
  - **Database:** SQLite with SQLModel (SQLAlchemy 2.0 core) and aiosqlite for async local storage.
  - **Frontend:** React + Vite.
- **Hardware Context:**
  - Machine: ASUS TUF Gaming F16 (Intel Core 5 205H, NVIDIA RTX 3050 6GB VRAM, 16GB DDR5 RAM).
  - Memory budgeting is critical: The 6GB VRAM limit requires keeping local LLM memory small (~2.5GB to 3.5GB) to allow coexistence with other processes, or offloading STT to CPU.

---

## TDR-002: Modular Requirements Structure

- **Date:** 2026-11-10
- **Status:** Approved
- **Decision:** Split dependencies into `requirements/` directory containing `base.txt`, `ai.txt`, `voice.txt`, `automation.txt`, and `development.txt`. Master `requirements.txt` includes all modules using `-r requirements/...`.
- **Reason:** Enables independent installation, faster test environments, and clear dependency boundaries between core server, AI engine, voice pipeline, desktop automation, and development tools.

---

## TDR-003: Security & Tool Execution Guardrails

- **Date:** 2026-11-10
- **Status:** Approved
- **Decision:**
  - Strict separation between planning and execution.
  - No raw LLM-generated shell, Python, or PowerShell execution.
  - Strict allowlisted Pydantic-validated tool functions.
  - Mandatory confirmation checkpoints before any destructive or irreversible operation (file deletion/overwrite, external submissions, system changes).

