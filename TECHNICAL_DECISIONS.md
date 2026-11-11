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

## TDR-020: Automation Rate Limiting, Safety Budgets, and Audit Trail Export Subsystem

- **Date:** 2026-11-11
- **Status:** Approved
- **Decisions:**
  - **Emergency Stop Safe Reset & Operator Recovery Correction:**
    - Removed unrestricted `force=True` reset from normal API usage in `backend/automation/emergency_stop.py`.
    - Standard `reset_emergency_stop()` strictly requires all tracked operations to have terminated and unregistered (`len(_active_operations) == 0`). If any active/stopping operations remain, raises `UnsafeResetError` with HTTP 409 Conflict.
    - Implemented documented `operator_emergency_recovery()` procedure:
      - Requires detailed operator justification (minimum 10 characters).
      - Requires explicit list of `confirmed_terminated_operations`; rejects if any active operations are unaccounted for.
      - Optional process liveness verification: if verification evidence provides PIDs, validates with `psutil` that the processes are terminated. Rejects recovery if target process is still running.
      - Never clears records merely due to elapsed time or apparent staleness.
      - Never auto-resumes interrupted executions.
    - Added API endpoints: `POST /agent/emergency-stop/reset` (safe reset only) and `POST /agent/emergency-stop/recover` (operator recovery with justification and evidence).
  - **In-Process Sliding-Window Rate Limiter & Resource Budgets (`backend/agent/rate_limiter.py`):**
    - Built thread-safe sliding-window `RateLimiter` with environment variable overrides:
      - Global requests/minute limit (default 20, `VOXPILOT_RATE_LIMIT_REQUESTS_PER_MINUTE`).
      - Concurrent active execution limit (default 2, `VOXPILOT_RATE_LIMIT_CONCURRENT_EXECUTIONS`).
      - Per-conversation requests/minute limit (default 10, `VOXPILOT_RATE_LIMIT_CONVERSATION_PER_MINUTE`).
      - Tool calls per execution budget (default 15, `VOXPILOT_LIMIT_TOOL_CALLS_PER_EXECUTION`).
      - Global tool calls/minute limit (default 60, `VOXPILOT_LIMIT_TOOL_CALLS_PER_MINUTE`).
      - Same-tool repeat invocation budget per execution (default 5, `VOXPILOT_LIMIT_SAME_TOOL_PER_EXECUTION`).
      - Plan step safety budget (default 10, `VOXPILOT_BUDGET_MAX_STEPS`).
      - Wall-clock duration budget per execution (default 120.0s, `VOXPILOT_BUDGET_MAX_DURATION_SECONDS`).
      - Browser navigations budget per execution (default 5, `VOXPILOT_BUDGET_MAX_BROWSER_NAVIGATIONS`).
      - Browser/file downloads budget per execution (default 3, `VOXPILOT_BUDGET_MAX_DOWNLOADS`).
      - Dynamic re-planning limit: reused canonical `MAX_REPLANS = 2` without duplication.
    - Sliding-window timestamp pruning on 60-second window boundaries.
    - Admitted requests and tool invocations recorded in sliding windows; rate-limit violations recorded to prevent spin loops.
  - **Tool Executor Strict Safety Evaluation Order (`backend/agent/tool_executor.py`):**
    - Enforced strict 7-stage evaluation pipeline for every step:
      1. Emergency stop check
      2. Execution wall-clock budget check
      3. Tool-call budget check
      4. Tool-specific rate limits (same-tool repeat, browser navigation, download)
      5. Parameter schema validation
      6. Confirmation requirements (backend trust boundary)
      7. Tool execution under active operation registry
    - Rejections by rate limiter return structured `ExecutionResult(status="rate_limited", success=False)`.
  - **LangGraph Workflow Safety Checkpoints (`backend/agent/graph.py` & `backend/agent/state.py`):**
    - Pre-dispatch request rate limiting and concurrent slot acquisition in `process_request` with `try...finally` slot release.
    - Plan step count validation against `rate_limiter.config.max_steps`.
    - Mid-execution duration budget checkpoints in `execute_tool`.
    - Immediate workflow halt upon receiving `rate_limited` status in `evaluate_result`, preserving completed steps without dynamic re-planning.
    - Duration budget checks in `_should_replan` preventing re-planning after duration exhaustion.
    - Pre-resumption duration and slot checks in `resume_execution`.
  - **Browser & Filesystem Safeguards:**
    - `BrowserController`: Enforced navigation count limit ($\le 5$) and download limit ($\le 3$) per session. Counters reset upon session start/close.
    - `FileManager`: Enforced `MAX_SEARCH_RESULTS = 100` and bounded filesystem scanning with `MAX_SEARCH_SCANNED_ITEMS = 1000` to prevent unbounded disk traversals.
  - **Audit Trail Export Subsystem (`backend/database/audit_export.py` & `backend/api/agent.py`):**
    - Created `AuditExporter` querying persistent SQLite `ExecutionRecord` and `ExecutionStepRecord`.
    - Formats: Structured JSON and flattened CSV.
    - Sensitive credential scrubbing: recursively scrubs tokens, confirmation tokens, passwords, keys, secrets, and auth headers, replacing them with `"[REDACTED]"`.
    - Destination directory containment: writes exclusively to approved `data/exports` directory, enforcing path traversal prevention.
    - Overwrite protection: raises `FileExistsError` unless `confirm_overwrite=True`.
    - Exported files are never opened, launched, or executed.
    - REST API: `POST /agent/audit/export` and `GET /agent/audit/exports/{export_id}/status` with localhost verification and CSRF Origin protection.
    ---

## TDR-021: Frontend Dashboard Architecture, Automation Telemetry, Safe Export Downloads, and Browser Integration Testing

- **Date:** 2026-11-11
- **Status:** Approved
- **Decisions:**
  - **Frontend Architecture:**
    - React 18 + Vite 6 + TypeScript 5.7 with modular dark theme design system (`globals.css`, `dashboard.css`).
    - Centralized typed API client (`frontend/src/api/client.ts`) connecting to local FastAPI daemon.
    - Periodic health monitoring hook (`useBackendHealth.ts`) querying `/health` and `/agent/emergency-stop/status` every 15s.
  - **Automation Monitoring Telemetry Policy (`frontend/src/pages/AutomationPage.tsx`):**
    - Truthful "Status unavailable" reporting: Low-level Win32 and Playwright controllers (Keyboard, Mouse, Window, Browser, Filesystem) operate as internal background engines and do not expose unauthenticated HTTP polling daemons. The UI reports "Status unavailable" honestly rather than inventing availability or synthetic telemetry.
    - Zero Direct Hardware Controls: The automation monitoring page is strictly read-only and explicitly omits direct keyboard, mouse, or terminal execution buttons to prevent unauthenticated hardware injection. All automation is mediated by LangGraph agent planning and tool executor checkpoints.
    - Consequential Tool Distinction: Registered tools from `GET /agent/tools` are categorized and flagged with "Requires Confirmation" badges.
  - **Audit Archive & Safe Download Architecture (`frontend/src/pages/AuditLogsPage.tsx` & `backend/api/agent.py`):**
    - Multi-criteria filtering: Allows live querying and server-side export filtering by status (`completed`, `failed`, `cancelled`, `rate_limited`, `confirmation_required`), conversation ID, date range, and text search.
    - Export Format: Supports structured JSON and flattened CSV formats generated by `AuditExporter`.
    - Safe File Download Mechanism: Added `GET /agent/audit/exports/{id}/download` in `backend/api/agent.py` streaming file contents with `Content-Disposition: attachment`. The frontend client retrieves the blob, generates a temporary Object URL via `URL.createObjectURL()`, triggers a native browser download anchor, and immediately schedules `URL.revokeObjectURL()` cleanup. Downloaded files are never automatically executed or opened.
    - Path Traversal & Destination Containment: Enforced canonical path resolution ensuring files are strictly contained within `data/exports`. Slashes and `..` sequences in export IDs and custom filenames are rejected.
    - Overwrite Protection: Defaults to `confirm_overwrite: false`, requiring explicit user checkbox opt-in to overwrite existing files.
  - **Trust Boundary & Authentication Disclosures:**
    - Localhost binding (`127.0.0.1`) and CORS Origin checks protect against browser CSRF, but do not authenticate the human operator in a multi-user OS environment.
    - Prominent safety notices disclose that the confirmation and audit export systems represent local development trust boundaries.
  - **Testing Architecture:**
    - Frontend Unit & Component Tests: 29 Vitest tests in `frontend/src/tests/` covering API error handling, conversation isolation, parameter redaction, emergency stop latching, and safe blob downloads.
    - Browser Integration Tests: Added 8 Playwright browser integration scenarios in `tests/test_frontend_browser.py` running headless Chromium against the production Vite build (`frontend/dist/`), testing the full React dashboard end-to-end with mocked backend routes in under 8 seconds.

---

## TDR-022: Full-System Integration Testing, Security Audit, Test Reconciliation, and Localhost Authentication Trust Boundary

- **Date:** 2026-11-11
- **Status:** Approved
- **Decisions:**
  - **Cross-Layer Architectural Verification:**
    - Audited full execution pipeline from user interaction to hardware and back:
      - Web UI path: React 18 frontend -> FastAPI REST client (`frontend/src/api/client.ts`) -> FastAPI endpoints (`backend/api/agent.py`) -> Thread pool execution (`anyio.to_thread.run_sync`) -> LangGraph agent (`backend/agent/graph.py`) -> Planner (`backend/agent/planner.py`) -> Tool Registry (`backend/agent/tool_registry.py`) -> Tool Executor (`backend/agent/tool_executor.py`) -> Native Automation Controllers (`backend/automation/`) -> Persistent SQLite Store (`backend/database/`) -> React UI state.
      - Voice pipeline path: Audio capture (`audio_recorder.py`) -> Streaming VAD (`streaming_vad.py`) -> Audio gain normalizer (`audio_preprocessor.py`) -> Speech-to-Text (`speech_to_text.py` / Faster-Whisper) -> Unified LangGraph agent (`VoxPilotAgent.process_request`) -> Text-to-Speech (`text_to_speech.py` / Piper TTS) -> Speaker output.
  - **Progress Reporting Protocol Investigation & Clarification:**
    - The backend and frontend communicate via standard asynchronous HTTP POST request-response (`POST /agent/execute`).
    - There are no WebSockets, Server-Sent Events (SSE), or chunked transfer streaming.
    - Visual message streaming styling in the frontend (`.messages-stream-container`) is purely CSS layout for rendering message history and execution progress bars; it does not indicate network streaming.
    - The incorrect reference to WebSockets in `README.md` was removed.
  - **Authentication & Trust Boundary Disclosures:**
    - Cryptographic confirmation tokens (`ConfirmationManager`) provide strong guarantees: 256-bit entropy (`secrets.token_urlsafe(32)`), single-use consumption (`validate_and_consume`), parameter hash binding via SHA-256, 300s expiration, and complete exclusion from the LLM tool registry. LLM-supplied `confirmed=True` parameters are rejected fail-closed.
    - Network boundary: The backend binds strictly to `127.0.0.1` and verifies `Origin` headers on sensitive endpoints, preventing browser-based CSRF attacks from external websites.
    - Local OS user process boundary limitation: The system currently operates under a local single-user desktop development trust model. It does not authenticate the human operator against other local OS processes running under the same Windows user account or other logged-in users on the same machine. This boundary is explicitly documented.
  - **Database Parity and Latent Defect Remediation:**
    - SQLite database is configured with `PRAGMA journal_mode = WAL;` and `PRAGMA foreign_keys = ON;` across both sync and async SQLAlchemy engines.
    - Resolved a latent bug in `backend/database/repository.py` where `SQLModel` was not imported, causing `SQLModel.metadata.create_all(self._sync_engine)` to fail with a `NameError` that was swallowed by debug logging.
  - **Test Suite Categorization & Reconciliation:**
    - Resolved test count discrepancies:
      - Pytest non-live tests: 569 tests (561 backend unit/integration tests + 8 Playwright browser tests in `tests/test_frontend_browser.py`).
      - Pytest live tests: 3 tests (Notepad launch, Notepad window management, Playwright browser automation).
      - Vitest frontend tests: 29 tests (`frontend/src/tests/`).
      - Total unique automated tests: exactly 601 tests (572 pytest + 29 vitest).
      - Reconciled that `tests/test_frontend_browser.py` resides inside `tests/` and is naturally included in `pytest -m "not live"`.

---

## TDR-023: Standalone Windows Packaging Architecture, Runtime Path Resolution, Single-Port Web/API Serving, and External Dependency Diagnostics

- **Date:** 2026-11-11
- **Status:** Approved
- **Decisions:**
  - **PyInstaller Packaging Architecture (`VoxPilot.spec`):**
    - Selected PyInstaller one-folder distribution mode (`--onedir`) over one-file (`--onefile`). One-folder provides instant cold-start performance, eliminates runtime extraction overhead, avoids filesystem clutter in `%TEMP%`, and enables reliable relative asset discovery for Piper TTS ONNX models and Playwright drivers.
    - Custom PyInstaller hook `hooks/hook-webrtcvad.py` authored to resolve wheel metadata differences on Windows between `webrtcvad` and `webrtcvad-wheels`.
    - Assets bundled into `dist/VoxPilot/_internal`: compiled React frontend (`frontend/dist`), Piper ONNX voice model (`en_US-lessac-medium.onnx` and JSON config), and Playwright driver.
  - **Runtime Path Resolution Subsystem (`backend/paths.py`):**
    - Established strict boundary between *read-only bundle assets* and *mutable user data*:
      - Read-only bundle assets (templates, static UI, neural voice models) resolve relative to `sys._MEIPASS` (in frozen mode) or repository root (in dev mode).
      - Mutable user data (SQLite database, application logs, audit trail exports, screenshots, voice scratch audio, config) resolves strictly to `%LOCALAPPDATA%\VoxPilot`.
    - Prevents write-access permission failures when installed in privileged locations (such as `C:\Program Files\VoxPilot`).
    - Integrated across `connection.py` (database), `audit_export.py` (exports), `file_manager.py` (executable directory protection), and `voice_pipeline.py` (model discovery and scratch audio).
    - Full configuration override support via environment variables (`VOXPILOT_DATA_DIR`, `VOXPILOT_DB_PATH`, `VOXPILOT_LOG_DIR`, `VOXPILOT_EXPORT_DIR`, etc.).
  - **Single-Port Full-Stack Serving (`backend/main.py`):**
    - Packaged standalone distribution eliminates the need for a separate Node.js / Vite server.
    - Uvicorn serves both the REST API and the compiled React SPA from a single port (default 8000).
    - Mounted `/assets` directory for static JavaScript and CSS chunks.
    - Dual-mode root route (`GET /`): Inspects HTTP `Accept` headers; returns HTML dashboard to web browsers, and returns JSON service metadata to REST clients and automated test runners.
    - Client-side SPA routing fallback: Non-API routes serve `index.html` to support browser refresh on frontend routes (`/chat`, `/executions`, `/confirmations`, `/automation`, `/audit`).
  - **Startup Diagnostics and External Dependency Handling (`launcher.py`):**
    - Standalone executable cannot bundle external daemons (Ollama) or large browser binaries (Chromium).
    - Launcher runs fast, non-blocking pre-flight checks:
      - Ollama HTTP health check on `http://127.0.0.1:11434`. Emits actionable instructions (`ollama serve`) if unreachable without crashing daemon startup.
      - Playwright Chromium check: Automatically sets `PLAYWRIGHT_BROWSERS_PATH` to `%LOCALAPPDATA%\ms-playwright` and fast-scans for `chrome.exe`.
      - Piper TTS voice model presence verification in bundle.
    - Emits dual logging to console stdout and `%LOCALAPPDATA%\VoxPilot\logs\voxpilot.log`.
    - Auto-opens the default Windows web browser to the dashboard URL unless suppressed via `VOXPILOT_AUTO_OPEN_BROWSER=0`.
  - **Relocatability & Portability:**
    - Zero hardcoded source repository paths in packaged binaries or configuration.
    - Verified relocation test (`scripts/verify_distribution.py`): Entire `dist/VoxPilot` directory copied to `%TEMP%\VoxPilot_Relocated_Test` runs on port 8002 with 100% test and endpoint parity.
  - **Testing Inventory & Suite Expansion:**
    - Added 16 path resolution unit tests (`tests/test_paths.py`).
    - Added end-to-end distribution and relocation verification script (`scripts/verify_distribution.py`).
    - Reconciled total automated test count: 617 tests (585 non-live pytest + 3 live pytest + 29 frontend vitest), 100% passing.

---

## TDR-024: Inno Setup Windows Installer Architecture, Per-User Lowest-Privilege Installation, Upgrade/Uninstall Data Retention Invariants, and Bundled Playwright Setup

- **Date:** 2026-11-11
- **Status:** Approved
- **Decisions:**
  - **Installer Technology Selection (Inno Setup 6):**
    - Selected Inno Setup 6 (ISCC.exe) over NSIS or WiX for packaging the PyInstaller standalone one-folder distribution.
    - Advantages: Native Windows 11 modern wizard style, declarative script model, robust Pascal scripting engine for uninstall callbacks, excellent silent automation support (`/VERYSILENT /SUPPRESSMSGBOXES /SP- /NORESTART`), and high-ratio LZMA2/ultra64 solid compression compressing the 742 MB distribution folder into a ~230.7 MB setup executable (`installer/Output/VoxPilot-Setup.exe`).
  - **Lowest-Privilege Per-User Installation (`PrivilegesRequired=lowest`):**
    - Default target directory: `{localappdata}\Programs\VoxPilot`.
    - Eliminates Windows UAC elevation prompts and local administrator requirements. Any standard user on Windows 11 can install and run VoxPilot without security prompts or IT administrative intervention.
    - Prevents privilege escalation risks associated with writing to `C:\Program Files` or modifying machine-wide registry hives.
    - Application identity secured with stable GUID: `AppId={{8F97B634-9A25-45C0-943C-65935E17F193}}`.
  - **Upgrade and Uninstall Data Retention Safety Invariant:**
    - Strict architectural decoupling between application binaries and persistent state:
      - Executable binaries, assets, and drivers reside in `{localappdata}\Programs\VoxPilot`.
      - Persistent SQLite database (`voxpilot.db`), logs, audit exports, and screenshots reside in `%LOCALAPPDATA%\VoxPilot`.
    - Upgrade Safety: Running a newer installer over an existing installation replaces application binaries but leaves `%LOCALAPPDATA%\VoxPilot` untouched.
    - Uninstall Safety: Standard uninstallation via `unins000.exe` or Windows Settings removes all installed binaries and Start Menu shortcuts, while strictly preserving `%LOCALAPPDATA%\VoxPilot`. An informational post-uninstall message informs the user where their history and data remain preserved.
  - **Bundled Python-Free Playwright Chromium Setup:**
    - End-users running the standalone distribution do not have Python or Node.js in their PATH.
    - To eliminate dependency friction, `launcher.py` and `install_browser.bat` harness the bundled Node.js runtime inside `_internal\playwright\driver\node.exe` with `cli.js install chromium`, downloading Chromium into `%LOCALAPPDATA%\ms-playwright`.
    - Exposed via Start Menu shortcut "Install Playwright Browser" and CLI flag `VoxPilot.exe --install-browser`.
  - **Single-Instance Daemon Protection:**
    - Double-clicking the application shortcut when VoxPilot is already running previously risked port binding collisions (`[WinError 10048]`).
    - Implemented `is_voxpilot_already_running(host, port)` in `launcher.py`: sends a fast HTTP `/health` probe; if a healthy VoxPilot daemon is detected, it opens/focuses the dashboard in the default browser and exits cleanly with code 0.
  - **Non-Blocking External Dependency Preflight:**
    - `launcher.py` checks Ollama `/api/tags` for `qwen3:4b` and Hugging Face cache for `faster-whisper-base`.
    - Emits clear, non-fatal advisory logs with exact remediation commands (`ollama pull qwen3:4b`), allowing the dashboard to open even if the local LLM is still offline.
  - **Automated Verification:**
    - Implemented `scripts/verify_installer.py` executing 8 comprehensive end-to-end checks (silent install into paths with spaces, shortcut checks, live daemon startup on port 8003, user data persistence, upgrade retention, and uninstallation safety). All checks passing 100%.

---

## TDR-025: Voice Mode Dashboard Integration, Customizable Win32 Global Shortcut (`Ctrl+Shift+V`), and Privacy-First Audio Lifecycle

- **Date:** 2026-11-11
- **Status:** Approved
- **Decisions:**
  - **Reuse of Canonical Voice Pipeline (`backend/voice/voice_service.py`):**
    - `VoiceService` orchestrates interactive multi-turn voice sessions directly over the existing `VoicePipeline`, `AudioRecorder`, `StreamingVAD` (WebRTC VAD), `SpeechToText` (Faster-Whisper + Silero VAD), `TextToSpeech` (Piper ONNX), and `VoxPilotAgent`. Zero duplicate speech or agent execution pipelines were created.
    - All voice-triggered agent executions run through the exact 7-stage safety evaluation pipeline, confirmation manager, rate limiter, emergency stop, and SQLite conversation/execution persistence (`save_execution`).
  - **Customizable Windows Global Hotkey (`backend/voice/hotkey.py`):**
    - Uses native Win32 `RegisterHotKey` / `GetMessageW` / `PostThreadMessageW(WM_QUIT)` / `UnregisterHotKey` on a dedicated daemon thread with `HOTKEY_ID_VOICE_MODE = 2` (distinct from `HOTKEY_ID_EMERGENCY_STOP = 1`).
    - Default shortcut: `Ctrl + Shift + V`.
    - Key-repeat suppression: Combines Win32 `MOD_NOREPEAT` (0x4000) with a 400ms software debounce window so holding the shortcut never rapidly toggles voice mode.
    - Reserved Emergency Stop protection: `parse_shortcut()` unconditionally rejects `Ctrl + Alt + Escape` with `ReservedShortcutError` on both backend and frontend.
    - Conflict rollback: When updating the shortcut at runtime (`update_shortcut`), if Windows reports `WinError 1409` (hotkey already registered by another application), `VoiceHotkeyListener` automatically rolls back and re-registers the previous valid shortcut.
  - **Persistent User Voice Configuration (`%LOCALAPPDATA%\VoxPilot\config\voice_settings.json`):**
    - `VoiceSettingsManager` atomically persists `shortcut_enabled`, `shortcut`, `microphone_device`, `tts_enabled`, `piper_voice`, and `streaming_vad_enabled` via temp-file swap in `%LOCALAPPDATA%\VoxPilot\config\voice_settings.json`.
  - **Strict Microphone Privacy & Hardware Lifecycle:**
    - Voice mode never auto-records at application startup (`phase="idle"`).
    - `AudioRecorder.record()` and `record_until_silence()` poll `stop_event` and `_cancel_event` every 100ms inside a `with sd.InputStream(...):` context manager, guaranteeing immediate PortAudio stream closure (`<= 100ms`) upon user stop, global hotkey toggle-off, or Emergency Stop trigger.
    - Raw microphone audio stays strictly in memory (`np.ndarray`); temporary Piper TTS WAV files are deleted immediately after playback (`cleanup_temp_file=True`).
    - `test_microphone()` captures a brief 0.5s sample in memory to compute RMS/peak levels and immediately releases the device without writing any file to disk.
  - **Frontend Connection State Accuracy (`useBackendHealth.ts` & `App.tsx`):**
    - Eliminated false `"Backend disconnected"` banners by treating only confirmed `'disconnected'` state (after automatic single-retry and 10s timeout) as disconnected, rather than treating initial `'checking'` state as disconnected, and offloading all synchronous voice/agent endpoints via `anyio.to_thread.run_sync`.

---

## TDR-026: Application-Side In-App Software Update System, Mandatory SHA-256 Verification, and Detached External Updater Architecture

- **Date:** 2026-11-11
- **Status:** Approved
- **Decisions:**
  - **Centralized Version Authority (`VERSION` & `backend/version.py`):**
    - Established a canonical root `VERSION` file (`0.5.0`) bundled into `dist/VoxPilot/_internal/VERSION` via `VoxPilot.spec` and read by `backend/version.py` (`get_app_version()`).
    - Implemented strict SemVer 2.0.0 parsing and comparison (`parse_semver`, `compare_versions`, `is_newer_version`) supporting `major.minor.patch` and pre-release tags (`-beta.1`, `-rc.1`) while rejecting malformed version tags (`InvalidVersionError`).
    - Exposed via `GET /api/version` (and `GET /agent/version`) and displayed dynamically in `Sidebar.tsx`, `SettingsPage.tsx`, and `UpdateNotificationBanner.tsx`.
  - **GitHub Releases Update Checker (`backend/updater/update_service.py`):**
    - Automatically discovers the canonical GitHub repository slug (`Thanush505/VoxPilot`) from `.git/config` or environment variable overrides (`VOXPILOT_GITHUB_REPO`, `VOXPILOT_RELEASE_API_URL`) without embedding or logging any personal access tokens.
    - Queries `https://api.github.com/repos/Thanush505/VoxPilot/releases/latest` (for `stable` channel) or `/releases?per_page=15` (for `beta` channel) with a 10s timeout and 5-minute TTL cache.
    - Filters out `draft == true` releases and filters out `prerelease == true` unless `channel == "beta"`.
    - Handles HTTP 404 (when no release is published yet on GitHub), HTTP 403/429 rate-limit headers (`X-RateLimit-Remaining: 0`), and network offline states cleanly without crashing or faking update availability.
  - **Secure Installer Download & Mandatory SHA-256 Verification:**
    - Enforces HTTPS-only URLs and an explicit allowlist of official GitHub release hosts (`github.com`, `api.github.com`, `objects.githubusercontent.com`, `release-assets.githubusercontent.com`, `github-releases.githubusercontent.com`, `codeload.github.com`).
    - Enforces `.exe` installer filename sanitization (`validate_installer_filename`), blocking `..`, `/`, `\`, `:`, and non-`.exe` extensions, and confines downloads strictly within `%LOCALAPPDATA%\VoxPilot\updates\`.
    - Streams downloads in 64 KB chunks to a `.part` file with real-time byte and percentage progress reporting and immediate cancellation (`cancel_download()`), deleting `.part` files upon cancellation or error.
    - Requires a 64-character SHA-256 hex digest from release metadata (GitHub asset `digest` field, `.sha256` / `SHA256SUMS.txt` asset, or release notes body) and verifies the on-disk SHA-256 before moving `.part` to the final `.exe`. Corrupted or mismatched downloads are deleted immediately.
    - Inspects Windows Authenticode digital signature status via `Get-AuthenticodeSignature`, explicitly distinguishing package integrity verification (SHA-256) from publisher code-signing identity verification.
  - **Detached External Windows Updater Process (`backend/updater/updater_runner.py`):**
    - A running `VoxPilot.exe` process never overwrites itself in-place.
    - Installation requires explicit user approval (`confirm=True` via the Installation Confirmation modal warning the user to save work). Automatic installation without user confirmation is permanently disabled (`auto_install_enabled: False`).
    - Upon confirmation, re-verifies the installer's SHA-256 checksum on disk, stages a detached updater script in `%LOCALAPPDATA%\VoxPilot\updates\run_voxpilot_updater.ps1` (outside `%LOCALAPPDATA%\Programs\VoxPilot` so Inno Setup encounters zero file locks), waits for the running `VoxPilot.exe` PID to exit, executes `VoxPilot-Setup.exe /SP- /SILENT /NORESTART /CLOSEAPPLICATIONS /DIR="<install_dir>"`, records the outcome in `%LOCALAPPDATA%\VoxPilot\updates\update_install_result.json`, and optionally relaunches `VoxPilot.exe`.
    - Strictly preserves all user data in `%LOCALAPPDATA%\VoxPilot` (`data\voxpilot.db`, `config\voice_settings.json`, `config\update_settings.json`, `logs\`, `exports\`, `screenshots\`, `downloads\`).







