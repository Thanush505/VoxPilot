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

---

## TDR-004: Dynamic Microphone Selection & Resampling

- **Date:** 2026-11-10
- **Status:** Approved
- **Decision:** Remove hardcoded audio device indexes. Enable system default selection, explicit index, and substring matching with WASAPI host API preference. Support hardware-native recording rates (e.g. 48 kHz) with automatic polyphase resampling (`scipy.signal.resample_poly`) to 16 kHz mono.
- **Reason:** Windows audio device indexes shift across reboots and USB disconnects. Capturing at device-native rates avoids WASAPI format renegotiation errors, and polyphase resampling cleanly produces 16 kHz mono float32 for downstream ASR.

---

## TDR-005: Pre-ASR Audio Gain Normalization

- **Date:** 2026-11-10
- **Status:** Approved
- **Decision:** Introduce an independent `AudioPreprocessor` applied before transcription. Target peak level is set to -3.0 dBFS, max gain ceiling capped at 20.0 dB, noise floor gate at -45.0 dBFS, and hard clamping to `[-1.0, 1.0]`.
- **Reason:** Quiet microphone recordings (-17 dBFS to -36 dBFS RMS) degrade ASR transcription accuracy. Controlled gain normalization brings speech into Whisper's optimal dynamic range without amplifying room noise or introducing digital clipping.

---

## TDR-006: Silero Voice Activity Detection (VAD) Integration

- **Date:** 2026-11-10
- **Status:** Approved
- **Decision:**
  - Utilize Faster-Whisper's built-in Silero VAD ONNX engine (`vad_filter=True`, `vad_parameters=dict(...)`) rather than adding an external VAD library.
  - Default parameters: `threshold=0.5`, `min_speech_duration_ms=250`, `min_silence_duration_ms=500`, `speech_pad_ms=200`.
  - Configurable via constructor kwargs and environment variables (`VOXPILOT_VAD_*`).
  - Sequence: Software gain normalization is applied first so quiet utterances are elevated above VAD threshold; VAD segmentation is executed inside Faster-Whisper's transcription call exactly once.
- **Reason:** Autoregressive Whisper decoders suffer from hallucinations and repetition loops when processing silent or low-energy audio frames. Silero VAD filters out silence before attention decoding, eliminating hallucinations and cutting inference latency by ~28%.

---

## TDR-007: Dual-Stage VAD Architecture (Streaming WebRTC VAD vs. Batch Silero VAD)

- **Date:** 2026-11-10
- **Status:** Approved
- **Decision:**
  - Implement a dual-stage VAD strategy:
    1. **Streaming Detection:** Real-time WebRTC VAD in `AudioRecorder.record_until_silence()` for responsive endpointing and silence termination during microphone capture.
    2. **Batch Segmentation:** Faster-Whisper built-in Silero VAD ONNX model for high-precision speech-frame segmentation during transcription.
- **Reason:** WebRTC VAD operates on 10-30ms frames with sub-millisecond CPU overhead, perfect for real-time endpointing. Silero VAD provides superior precision for deep-learning transcription filtering.

---

## TDR-008: Local Wake Word Detection (OpenWakeWord)

- **Date:** 2026-11-10
- **Status:** Approved
- **Decision:** Use OpenWakeWord ONNX models for local keyword detection with strict PortAudio stream lifecycle handover to ensure exclusive microphone ownership for speech capture.

---

## TDR-009: Safe Application Launcher Architecture (SEC-001, SEC-002, SEC-003)

- **Date:** 2026-11-10
- **Status:** Approved
- **Decision:**
  - Restrict application launching to an explicit allowlist: `notepad`, `calculator`, `chrome`, `vscode`.
  - Prohibit unconstrained PATH lookups (SEC-001).
  - Registry query precedence set to HKLM first, then HKCU (SEC-002).
  - Validate binary executable filename, directory containment, and strip arbitrary CLI arguments (SEC-003).

---

## TDR-010: Window Identification and Management (SEC-004 to SEC-008)

- **Date:** 2026-11-10
- **Status:** Approved
- **Decision:**
  - Window validation strictly requires owner process verification against approved allowlisted binaries.
  - Windows 11 Calculator identity verified via `GetPackageFamilyName` matching exact package family name `Microsoft.WindowsCalculator_8wekyb3d8bbwe` (SEC-004).
  - Foreground activation uses standard Win32 `SetForegroundWindow` without `AttachThreadInput` bypass hacks (SEC-005).
  - Activation refusals are tracked and reported as operation errors rather than false successes (SEC-006).
  - Window operations validate matching HWND and application name to prevent cross-app tampering (SEC-007).

---

## TDR-011: Safe Window-Scoped Keyboard Automation

- **Date:** 2026-11-10
- **Status:** Approved
- **Decision:**
  - Keyboard automation requires valid target HWND belonging to an approved application.
  - Key presses and shortcuts are strictly validated against an approved allowlist; dangerous keys/shortcuts (`Win`, `Ctrl+Alt+Del`, `Alt+F4`, etc.) are prohibited.
  - Input uses Win32 `SendInput` with pre- and post-validation of foreground focus.

---

## TDR-012: Safe Window-Scoped Mouse Automation

- **Date:** 2026-11-10
- **Status:** Approved
- **Decision:**
  - Mouse coordinates are bounded to visible virtual screen dimensions or window-relative client rectangles.
  - Clicks require window allowlist validation.
  - Fail-safe coordinates and rate limits prevent runaway cursor movements.

---

## TDR-013: Restricted Filesystem & Screenshot Automation

- **Date:** 2026-11-10
- **Status:** Approved
- **Decision:**
  - Filesystem access is strictly confined to user directories (`Documents`, `Downloads`, `Music`, `Videos`). Path traversal and system roots (`C:\Windows`, `C:\Program Files`) are blocked.
  - Destructive operations (`delete_file`, `delete_folder`) and overwrite operations require cryptographic confirmation.
  - Screenshots are window-scoped or display-bounded and stored in dedicated directories.

---

## TDR-014: Safe Browser Automation Architecture with Playwright

- **Date:** 2026-11-10
- **Status:** Approved
- **Decision:**
  - Ephemeral browser profile isolation using Chromium; no access to personal profiles or session cookies.
  - Strict URL validation, scheme restrictions (`http`, `https`), and comprehensive SSRF protections blocking private IP ranges, localhost, cloud metadata, and non-standard IP formats.
  - Consequential actions require explicit confirmation tokens.
  - Downloads confined to `~/Downloads/VoxPilot_Downloads` with executable blocking and overwrite protection.

---

## TDR-015: User Confirmation Trust Boundary Architecture (`ConfirmationManager`)

- **Date:** 2026-11-10
- **Status:** Approved
- **Decision:**
  - Implemented `ConfirmationManager` (`backend/automation/confirmation_manager.py`) with 256-bit entropy tokens (`secrets.token_urlsafe(32)`).
  - Tokens are bound to an immutable SHA-256 hash of canonical action type and action parameters.
  - Single-use consumption prevents replay attacks; token tampering is detected via parameter hash mismatch.
  - LLM tool schemas do not expose confirmation approval capabilities; only human user out-of-band requests can approve tokens.

---

## TDR-016: LangGraph Agent Execution, Tool Registry, and API Architecture

- **Date:** 2026-11-10
- **Status:** Approved
- **Decisions:**
  - **LangGraph StateGraph Workflow Orchestration:**
    - Implemented `VoxPilotAgent` (`backend/agent/graph.py`) using LangGraph `StateGraph(VoxPilotState)`.
    - 6-node state machine:
      1. `recognize_intent`: Classifies natural language queries via `IntentRecognizer` (`qwen3:4b`). Resumption bypasses intent classification.
      2. `create_plan`: Synthesizes ordered plan steps via `AgentPlanner`. Steps capped at maximum safety limit of 10 steps.
      3. `validate_plan`: Structural, registration, and Pydantic schema validation. Blocks empty automation plans, unknown actions, and malformed structures fail-closed.
      4. `execute_tool`: Executes active step via `ToolExecutor` and injects approved confirmation tokens if present.
      5. `evaluate_result`: Evaluates outcome; pauses with status `confirmation_required` on consequential operations; halts immediately on step failure (no false success); advances step index on success.
      6. `generate_response`: Constructs truthful summaries based on actual tool execution outcomes.
    - Resumption Mechanism: Paused executions are resumed using `resume_execution(state, confirmation_token)`. Resumption transitions status to `resuming`, skips re-planning, and routes directly to the paused step with the validated token.
  - **Secure Tool Registry (`ToolRegistry`):**
    - Implemented `ToolRegistry` (`backend/agent/tool_registry.py`) mapping approved actions to immutable `ToolDefinition` objects.
    - Parameter Validation: Validates inputs against tool-specific Pydantic schemas, rejecting unknown or unsupported parameters (extra-parameter injection protection).
    - Aliases: Normalizes synonyms (`open_application` $\to$ `launch_application`, `activate_window` $\to$ `manage_window`, `press_keys` $\to$ `press_key`, `search_web` $\to$ `navigate_url`, `take_screenshot` $\to$ `capture_desktop`).
    - Confirmation Policy: Enforces backend confirmation rules (`check_confirmation_required`). Strips transient credentials (`confirmation_token`) before computing canonical action parameter hashes to ensure tamper-detection integrity.
  - **Tool Execution Engine (`ToolExecutor`):**
    - Implemented `ToolExecutor` (`backend/agent/tool_executor.py`) acting as the execution gateway.
    - Enforces cryptographic confirmation trust boundary (`ConfirmationManager`). LLM-supplied flags (`confirmed=True` or `is_consequential=False`) are never trusted to bypass confirmation checks.
    - Universally handles both Pydantic model outputs and dictionary return structures.
  - **Safe Read-Only System Metrics Tool:**
    - Created `backend/tools/system.py` providing `get_system_info_tool` using `psutil` and `platform`.
    - Safely returns OS details, CPU percent, and RAM usage without shell or subprocess execution.
  - **FastAPI REST API Architecture:**
    - Created `backend/api/agent.py` and `backend/main.py`.
    - Endpoints:
      - `POST /agent/execute`: Start agent execution session.
      - `GET /agent/status/{execution_id}`: Query session state and result log.
      - `GET /agent/confirmations/{execution_id}`: Retrieve pending confirmation details.
      - `POST /agent/confirmations/{execution_id}/approve`: Approve token and resume execution.
      - `POST /agent/confirmations/{execution_id}/reject`: Reject token and cancel execution.
      - `GET /agent/tools`: Enumerate registered tools with safety flags.
      - `GET /` & `GET /health`: Service health and metadata.
    - Thread-safe in-memory session store (`_EXECUTIONS`, `threading.Lock`).
  - **Voice Pipeline Integration:**
    - Updated `VoicePipeline` (`backend/voice/voice_pipeline.py`) to support dependency-injected `VoxPilotAgent`, unifying voice and REST entry points.

---

## TDR-017: Persistent SQLite History, Conversational Memory, Dynamic Re-Planning, and Security Trust Boundary Hardening

- **Date:** 2026-11-10
- **Status:** Approved
- **Decisions:**
  - **Persistent Execution History Subsystem:**
    - Implemented SQLModel entities in `backend/database/models.py`:
      - `ConversationRecord`: Top-level conversation metadata (`conversation_id`, `title`, `summary`, timestamps).
      - `ExecutionRecord`: Execution session record (`execution_id`, `conversation_id`, `user_input`, `intent`, `execution_status`, sanitized `plan_json`, `current_step`, `response`, `error`, timestamps).
      - `ExecutionStepRecord`: Granular step outcomes (`step_id`, `execution_id`, `step_index`, `action`, sanitized `parameters_json`, `execution_status`, `result_summary`, `error`, timestamps).
    - Database Connection (`backend/database/connection.py`):
      - Asynchronous engine using `sqlite+aiosqlite` for non-blocking FastAPI endpoints.
      - Synchronous engine using `sqlite` for background and synchronous LangGraph/voice execution.
      - Database path defaulted to `data/voxpilot.db` (anchored to project root, CWD-independent) with environment variable overrides (`VOXPILOT_DB_URL`, `VOXPILOT_DB_PATH`).
      - All database files (`*.db`, `*.sqlite`, `*.sqlite3`, `data/`, `backend/data/`) strictly excluded from Git in `.gitignore`.
    - Data Sanitization:
      - `sanitize_dict_for_storage` and `serialize_sanitized_json` recursively strip sensitive keys (`token`, `confirmation_token`, `password`, `secret`, `api_key`, `auth`, `credential`). Secrets are completely omitted from persistence.
    - Repository Pattern (`backend/database/repository.py`):
      - Provides both asynchronous and synchronous CRUD methods (`save_execution`, `save_execution_sync`, `record_execution_step`, `get_execution`, `list_conversations`, `list_executions_for_conversation`).
      - Automatic table schema creation (`SQLModel.metadata.create_all`) ensures immediate readiness.
  - **Short-Term Conversational Memory & Contextual Reasoning:**
    - Implemented `ConversationContextManager` in `backend/memory/context_manager.py`.
    - Turn Bounding: Capped at `max_turns=5` to prevent context explosion and memory leaks.
    - Tracked Entities:
      - `active_window`: Window HWND, title, application name.
      - `recent_files`: List of up to 10 recently accessed or discovered file paths.
      - `recent_urls`: List of up to 5 recently navigated URLs.
      - `recent_application`: Most recently launched or controlled application.
    - Reference & Pronoun Resolution (`resolve_references`):
      - Resolves pronouns ("it", "the window", "that app") to the tracked `active_window`.
      - Resolves ordinal references ("open the first file", "read the second result") to tracked `recent_files`.
      - Ambiguity Safeguard: When multiple candidates exist and user gives an ambiguous reference ("open it"), flags ambiguity and generates clarification request without blindly guessing.
    - Context Isolation: Conversations isolated by unique `conversation_id`; no crosstalk across sessions.
    - Target Revalidation: Validates that window HWNDs and paths exist before executing follow-up actions.
  - **Dynamic Re-Planning Loop in LangGraph:**
    - Extended `VoxPilotState` with `conversation_id`, `conversation_history`, `replan_count`, `max_replans`, `needs_replan`, `replan_reason`, `replan_history`.
    - Added `replan_plan` node to StateGraph with conditional edge routing:
      `evaluate_result` $\to$ `replan_plan` (if `needs_replan`) $\to$ `validate_plan` $\to$ `execute_tool`.
    - Structured Result Adaptation (`replan_from_results`):
      - Single file discovery binds path to subsequent operations.
      - Multiple file matches request clarification via `ask_clarification`.
      - Zero file matches replace actions with safe user notification.
      - Application launch binds runtime window HWND to subsequent typing/window management steps.
      - Intermediate failures safely abort dependent consequential actions.
    - Safety Constraints:
      - Capped at `MAX_REPLANS = 2` hard bound.
      - Completed steps are preserved and never re-executed (`current_step = len(completed)`).
      - Revised plans are re-validated through `validate_plan` before execution.
  - **Security Trust Boundary Hardening:**
    - Confirmation Token Removal from Voice & Assistant Text:
      - Spoken voice output strictly states: *"VoxPilot needs your approval before performing this action. Please review the pending confirmation in the application."*
      - Confirmation tokens are never exposed in assistant responses, LLM history, or spoken audio.
    - Approval Endpoint Trust Boundary:
      - Human approvals are strictly processed through `POST /agent/confirmations/{execution_id}/approve`.
      - Token mismatch across execution sessions rejected with HTTP 403 Forbidden.
      - Parameter tamper verification hashes clean parameters excluding transient token metadata.
      - Single-use consumption enforced via `ConfirmationManager.validate_and_consume`.

---

## TDR-018: Final Integration Audit, Confirmation Security Trust Boundary, Database Concurrency, and Re-planning Safety

- **Date:** 2026-11-10
- **Status:** Approved
- **Decisions:**
  - **Authoritative Tool Registry Audit:**
    - Confirmed exactly 37 canonical tools, 11 aliases, and 2 pseudo-actions (`respond`, `ask_clarification`) in `backend/agent/tool_registry.py`.
    - Resolved nonexistent file tools (`read_file`, `open_file`, `write_file`) by normalizing references to the actual canonical file inspection tool (`get_file_metadata`).
    - Verified 1:1 parity between `AgentPlanner.allowed_actions` (39 items) and canonical registry tools + pseudo-actions (0 unmapped actions).
    - Verified `AgentPlanner.intent_actions` contains 0 unmapped actions across all 11 intents.
  - **Confirmation & Human Approval Trust Boundary Hardening:**
    - Confirmation operations (`approve_confirmation`, `reject_confirmation`, `bypass_confirmation`) are strictly excluded from `ToolRegistry` so the LLM has zero capability to self-approve or manipulate confirmation tokens.
    - LLM-supplied `confirmed=True` or `is_consequential=False` flags in step parameters are rejected at the execution trust boundary and cannot bypass confirmation.
    - Approvals are strictly bound to execution ID, action type, and parameter hash via SHA-256 (`compute_action_hash`).
    - Cross-execution token mismatches return HTTP 403 Forbidden.
    - Single-use consumption (`validate_and_consume`) strictly rejects token replay (`ReplayedConfirmationError`) and expired tokens (`ConfirmationExpiredError`).
    - Parameter tampering triggers `TamperedActionConfirmationError`.
    - Localhost Binding: Enforced `host="127.0.0.1"` binding in `backend/main.py` entrypoint.
    - CSRF Browser Protection: Implemented `_verify_trusted_request` checking `Origin` headers on `/agent/confirmations/{id}/approve` and `/agent/confirmations/{id}/reject`. Cross-origin browser requests (e.g. from `http://evil.com`) are rejected with HTTP 403 Forbidden.
    - Documented development trust boundary: Localhost HTTP approval represents a local desktop boundary; full cryptographic user authentication will be layered in Phase 6 with frontend integration.
  - **Database Consistency, Pragmas, and Event Loop Non-Blocking:**
    - Attached SQLAlchemy connection event listeners on `Engine` enforcing `PRAGMA foreign_keys = ON;` and `PRAGMA journal_mode = WAL;` across all sync and async connections.
    - Foreign key violations (e.g. orphan execution or execution step records) raise `IntegrityError`.
    - Dual sync and async engines are verified to use the identical database file (`data/voxpilot.db` or env override) with immediate cross-engine read parity.
    - Wrapped synchronous agent calls (`agent.process_request`, `agent.resume_execution`) inside `await anyio.to_thread.run_sync(...)` in FastAPI route handlers to prevent blocking the asyncio event loop during OS automation calls.
  - **Conversational Memory Stale Target Revalidation:**
    - Added `is_window_alive` and `is_file_accessible` validators to `ConversationContextManager`.
    - When `revalidate_targets=True`, dead window HWNDs and deleted file paths are evicted from active memory, and the agent asks for clarification rather than operating on invalid handles.
    - Conversation isolation verified: Conversation B does not inherit Conversation A's window context and asks for clarification.
  - **End-to-End Workflow Verification:**
    - Verified all 13 core workflows across REST API, LangGraph orchestration, tool execution, and voice pipeline (480 unit/integration tests and 3 live tests passing, 0 failures).

---

## TDR-019: Global Emergency Stop Subsystem, Windows Hotkey Loop, and Safe Automation Cancellation

- **Date:** 2026-11-11
- **Status:** Approved
- **Decisions:**
  - **Global Emergency Stop Subsystem Architecture:**
    - Implemented central `EmergencyStopManager` coordinator singleton in `backend/automation/emergency_stop.py`.
    - State latching: once triggered, the stop state latches active. It prevents new operations, halts active workflows, and marks in-flight executions as `cancelled`.
    - Explicit reset validation: reset requires explicit operator action (`reset_emergency_stop()`) and verifies that all active operations have reached a confirmed safe terminal state. Rejects reset with `UnsafeResetError` if operations are running, unless `force=True`. Reset never auto-resumes interrupted executions.
  - **Native Windows Hotkey Listener (`Ctrl + Alt + Escape`):**
    - Built `GlobalHotkeyListener` on an independent background thread using native Win32 `user32.RegisterHotKey` with `MOD_CONTROL | MOD_ALT | MOD_NOREPEAT` and `VK_ESCAPE` (0x1B).
    - Established thread message queue via `user32.PeekMessageW(..., PM_NOREMOVE)` before calling `RegisterHotKey`.
    - Implemented non-aborting Win32 message loop (`GetMessageW` / `TranslateMessage` / `DispatchMessageW`).
    - Clean shutdown via `user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)` breaking the message loop cleanly without thread termination or unhandled exceptions.
    - Hotkey unregistration via `user32.UnregisterHotKey` in thread `finally` block.
    - Gracefully logs if hotkey registration fails (e.g. key combo already claimed by another process) without crashing the application.
  - **Direct Hardware Input State Cleanup:**
    - Implemented `release_system_inputs()` using direct Win32 `user32.keybd_event` and `user32.mouse_event`.
    - Immediately releases Ctrl (`VK_CONTROL`), Alt (`VK_MENU`), Shift (`VK_SHIFT`), LWin (`VK_LWIN`), and RWin (`VK_RWIN`) with `KEYEVENTF_KEYUP`.
    - Immediately releases mouse buttons (left, right, middle) with `MOUSEEVENTF_LEFTUP | MOUSEEVENTF_RIGHTUP | MOUSEEVENTF_MIDDLEUP`.
    - Executes automatically on emergency stop trigger, on explicit reset, and during controller abort handlers.
  - **Fail-Closed Active Operation Registry:**
    - Thread-safe registry tracks in-flight automation operations (`operation_id`, `execution_id`, `category`, `description`, `start_time`, `cancellation_requested`, `status`).
    - Rejects registration of new operations with `EmergencyStopActiveError` whenever the stop latch is active.
  - **Automation Controllers Integration:**
    - `KeyboardController`: Checks emergency stop before execution and between 50-character typing chunks. On active stop, calls `release_all_modifiers()` and raises `EmergencyStopActiveError`.
    - `MouseController`: Checks emergency stop before `move_to`, `click`, and `scroll`. Provides `release_all_buttons()` helper.
    - `BrowserController`: Registers cleanup callback with manager; terminates active Playwright browser sessions and halts navigations, clicks, and downloads on emergency stop.
    - `ToolExecutor`: Wraps all tool executions in active operation registration and cleanup. Catches `EmergencyStopActiveError` and returns structured `ExecutionResult(status="cancelled")`.
  - **LangGraph Workflow Integration:**
    - Checks emergency stop at `process_request` and `resume_execution` entry points, returning state with `execution_status="cancelled"`.
    - Checks emergency stop across all StateGraph nodes (`recognize_intent`, `create_plan`, `validate_plan`, `execute_tool`, `evaluate_result`, `replan_plan`).
    - Mid-workflow cancellation halts subsequent steps and truthfully preserves completed steps in `completed_steps`.
    - Dynamic re-planning is strictly prevented during emergency stop.
    - Response generation truthfully reports user-requested cancellation.
  - **REST API Endpoints and Security:**
    - `POST /agent/emergency-stop`: Immediate trigger returning updated latch status.
    - `GET /agent/emergency-stop/status`: Status query returning latch status and active operations.
    - `POST /agent/emergency-stop/reset`: Explicit reset validating safe terminal state. Returns HTTP 409 Conflict if active operations are running (unless `force=True`).
    - CSRF Origin protection enforced via `_verify_trusted_request`.
    - Reset operation is strictly excluded from `ToolRegistry` so the LLM cannot bypass emergency stop.
  - **Voice Pipeline Safeguards:**
    - When emergency stop is active, `VoicePipeline.generate_response()` immediately informs the user that automation is halted and requires manual reset, refusing all automated actions.


