# VoxPilot — Project Status

**Current Development Phase:** Phase 7 — Testing, Packaging, and Installer (Step 7.3: Windows Installer and First-Run Distribution Validation Complete)  
**Last Updated:** 2026-11-11  

---

## 1. Phase Progress Overview

| Phase | Description | Status |
|---|---|---|
| **Phase 1** | **Environment and Project Foundation** | **Completed** |
| **Phase 2** | **Voice Interface (Whisper STT + Piper TTS)** | **Completed** |
| **Phase 3** | **Desktop Automation (Windows UI, Apps, Files, Browser)** | **Completed** |
| **Phase 4** | **AI Agent Integration (LangGraph + Ollama)** | **Completed** |
| **Phase 5** | **Agent Intelligence and Safety (Guardrails, Approvals, Emergency Stop, Rate Limiting, Budgets, Audit Trail)** | **Completed** |
| **Phase 6** | **Frontend Dashboard (React + Vite)** | **Completed (Steps 6.1, 6.2, and 6.3 Completed)** |
| **Phase 7** | **Testing, Packaging, and Installer (Step 7.1 Full Audit, Step 7.2 Standalone Packaging, Step 7.3 Windows Installer)** | **Completed** |

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
  - `audio_recorder.py`: Microphone capture, device enumeration, dynamic device resolution (name/index/default), automatic sample rate negotiation and polyphase resampling to 16kHz mono, and real-time streaming recording with dynamic endpointing (`record_until_silence()`).
  - `streaming_vad.py`: WebRTC VAD real-time streaming speech detector, onset confirmation, rolling pre-roll buffer, and silence endpointing.
  - `audio_preprocessor.py`: Controlled software gain normalization targeting -3 dBFS with clipping protection, max gain limits, and ambient noise floor safeguards.
  - `speech_to_text.py`: Faster-Whisper wrapper with auto hardware selection, INT8 CPU fallback, integrated audio gain normalization, and Silero Voice Activity Detection (VAD) filter.
  - `text_to_speech.py`: Piper TTS wrapper with auto voice model acquisition, sounddevice audio output, and emergency stop.
  - `voice_pipeline.py`: Configurable microphone selection, wake word idle monitoring, and dynamic streaming recording VAD coordination.
  - `wake_word.py`: Offline wake-word detection powered by `openwakeword` ONNX runtime (2.75 ms frame inference latency, ~3.4% single-core CPU load, ~39 MB RAM delta), with strict PortAudio stream lifecycle handover ensuring microphone availability for recording.
  - Test suites: [`tests/test_voice.py`](./tests/test_voice.py), [`tests/test_microphone_selection.py`](./tests/test_microphone_selection.py), [`tests/test_audio_preprocessor.py`](./tests/test_audio_preprocessor.py), [`tests/test_vad.py`](./tests/test_vad.py), [`tests/test_streaming_vad.py`](./tests/test_streaming_vad.py), and [`tests/test_wake_word.py`](./tests/test_wake_word.py) (73/73 tests passing).
- [x] Phase 3, Step 1: Safe Application Launching implemented & security-hardened in [`backend/automation/`](./backend/automation/) and [`backend/tools/`](./backend/tools/):
  - `application_launcher.py`: Safe Windows application discovery and launching via explicit allowlist (`notepad`, `calculator`, `chrome`, `vscode`), natural-language alias resolution, candidate path checking, secure Windows Registry `App Paths` fallback (read-only, HKLM first, filename and directory containment validated), and strict `subprocess.Popen(..., shell=False)` execution with zero argument forwarding.
  - Security Remediation: Completely eliminated unconstrained PATH lookups (SEC-001), inverted registry query precedence to HKLM first (SEC-002), and enforced executable identity, argument stripping, and approved directory containment checks (SEC-003).
  - `backend/tools/applications.py`: Pydantic input/output schemas (`LaunchApplicationInput`, `LaunchApplicationResult`) with path traversal and shell injection guards.
  - Test suite: [`tests/test_application_launcher.py`](./tests/test_application_launcher.py) (32 unit tests + 1 live Notepad test passing).
- [x] Phase 3, Step 2: Window Identification and Management (Approved & Security Remediated) in [`backend/automation/`](./backend/automation/) and [`backend/tools/`](./backend/tools/):
  - `window_manager.py`: Safe Windows top-level window discovery and basic window management. Features immutable `WindowInfo` metadata model, non-blocking responsiveness checks via `ctypes.windll.user32.IsHungAppWindow`, visibility filtering, and top-to-bottom z-order preservation.
  - Allowlist Ownership Revalidation: Enforces strict ownership checks against approved applications (`notepad`, `calculator`, `chrome`, `vscode`) immediately prior to every window operation (activate, minimize, maximize, restore). Arbitrary windows are rejected with `WindowNotAllowedError`.
  - Rigorous Calculator Package Verification (SEC-004): Completely eliminated heuristic title matching. Validates that `ApplicationFrameHost.exe` top-level window hosts a child `Windows.UI.Core.CoreWindow` whose PID belongs to `CalculatorApp.exe`, whose executable path is canonically located in `%ProgramFiles%\WindowsApps`, and whose UWP package family name matches `EXPECTED_CALCULATOR_PACKAGE_FAMILY` (`Microsoft.WindowsCalculator_8wekyb3d8bbwe`) with exact equality via `kernel32.GetPackageFamilyName()`. Enforces strict fail-closed rejection: missing, unresolvable, empty, or mismatched package identities are rejected with zero fallback.
  - OS-Compliant Foreground Activation (SEC-005): Completely removed `AttachThreadInput` and `BringWindowToTop` focus-stealing bypasses. Uses standard `win32gui.SetForegroundWindow(hwnd)` and verifies foreground status honestly via `win32gui.GetForegroundWindow()`.
  - Truthful Activation Failure Reporting (SEC-006): Activation refusal by Windows raises `WindowOperationError` and is reported truthfully by `manage_window_tool` as `success=False` with `status="activation_restricted"`.
  - HWND and Application Identity Co-Validation (SEC-007): When both `hwnd` and `app_name` are passed to `manage_window`, validates that the targeted HWND's verified `app_id` matches the resolved `app_name`, rejecting cross-application mismatches.
  - Multi-Window Ambiguity Handling: Returns complete window lists in `find_windows()`. If an ambiguous application name targets multiple windows in `manage_window()`, raises structured `WindowAmbiguityError` requiring explicit HWND targeting.
  - Agent Tools: [`backend/tools/windows.py`](./backend/tools/windows.py) provides Pydantic schemas (`FindWindowsInput`, `FindWindowsResult`, `ManageWindowInput`, `ManageWindowResult`) and functions (`find_windows_tool`, `manage_window_tool`).
  - Test suite: [`tests/test_window_manager.py`](./tests/test_window_manager.py) (53 unit tests + 1 live Notepad window test passing).
- [x] Phase 3, Step 3: Keyboard and Mouse Automation in [`backend/automation/`](./backend/automation/) and [`backend/tools/`](./backend/tools/):
  - `keyboard_controller.py`: Low-level Win32 `SendInput` ctypes dispatch engine.
    - Unicode text typing via `KEYEVENTF_UNICODE` with UTF-16 surrogate pair expansion for characters > 0xFFFF (emojis/non-BMP).
    - Chunking mechanism: text $> 10$ characters is sent in $\le 10$-character chunks with foreground window focus re-verification between chunks (`FocusLostError` on lost focus).
    - Allowlisted individual keys (14 keys: `enter`, `tab`, `space`, `backspace`, `delete`, `up`, `down`, `left`, `right`, `home`, `end`, `pageup`, `pagedown`, `escape`).
    - Allowlisted shortcuts (16 combos: `ctrl+c`, `ctrl+v`, `ctrl+x`, `ctrl+a`, `ctrl+z`, `ctrl+y`, `ctrl+s`, `ctrl+f`, `ctrl+left`, `ctrl+right`, `ctrl+home`, `ctrl+end`, `shift+left`, `shift+right`, `shift+up`, `shift+down`).
    - Explicitly prohibited destructive system shortcuts (8 combos: `alt+f4`, `ctrl+w`, `win+r`, `win+x`, `win+d`, `win+m`, `ctrl+alt+del`, `ctrl+shift+esc`) rejected with `ProhibitedShortcutError`.
    - Modifier key release safety: pressed modifiers tracked and unconditionally released in reverse order within `finally` blocks.
    - Pre-action window validation: ownership allowlist, responsiveness, minimized state, and foreground focus verification.
    - Safe input bounds: text length $\le 500$ chars, interval between 0.005s and 0.05s.
  - `mouse_controller.py`: Safe mouse movement, clicking, and scrolling engine.
    - Virtual desktop bounds validation using `win32api.GetSystemMetrics` (`SM_XVIRTUALSCREEN`, `SM_YVIRTUALSCREEN`, `SM_CXVIRTUALSCREEN`, `SM_CYVIRTUALSCREEN`), natively supporting multi-monitor configurations with negative coordinates.
    - Window-relative coordinate translation (`relative_to_window=True`) bounded strictly within target window rect (`CoordinatesOutsideWindowError`).
    - Allowed buttons restricted to `"left"`, `"right"`, `"middle"`. Click count restricted to 1 or 2 (`InvalidClickCountError`). Scroll amount bounded to `[-2000, 2000]` units.
    - Pre-action window validation: allowlist ownership, responsiveness (`TargetWindowUnresponsiveError`), minimized state (`WindowStateError`), and foreground focus.
    - User Interface Privilege Isolation (UIPI) access denied detection (`UIPIRestrictedError`).
  - Agent Tools: [`backend/tools/keyboard.py`](./backend/tools/keyboard.py) and [`backend/tools/mouse.py`](./backend/tools/mouse.py) provide Pydantic schemas (`TypeTextInput`, `PressKeyInput`, `SendShortcutInput`, `KeyboardActionResult`, `MoveMouseInput`, `ClickMouseInput`, `ScrollMouseInput`, `MouseActionResult`) and tools (`type_text_tool`, `press_key_tool`, `send_shortcut_tool`, `move_mouse_tool`, `click_mouse_tool`, `scroll_mouse_tool`) with sensitive typed text masking in logs.
  - Test suites: [`tests/test_keyboard_controller.py`](./tests/test_keyboard_controller.py) (71 unit tests) and [`tests/test_mouse_controller.py`](./tests/test_mouse_controller.py) (34 unit tests). All 105 tests passing in ~1.4s.
- [x] Phase 3, Step 4: File Management and Screenshot Functionality in [`backend/automation/`](./backend/automation/) and [`backend/tools/`](./backend/tools/):
  - `file_manager.py`: Safe Windows file and directory management engine.
    - Configurable approved directory policy: defaults to standard user folders (Documents, Downloads, Desktop, Pictures, Music, Videos). String prefix checks prohibited; strictly enforces canonical `Path.resolve().is_relative_to(approved_root)`.
    - Path traversal guards: Blocks `..`, UNC network paths, Windows device namespaces (`\\.\`, `\\?\`), NTFS alternate data streams (`:stream`), and null bytes.
    - Symlink and reparse-point escape guards: Resolves symlink destinations and verifies containment within approved roots.
    - Prohibited target protection: Hardened against operations targeting drive roots (`C:\`), user home root, Windows system directories, and the VoxPilot project repository root.
    - 14 core filesystem operations: `list_directory`, `search_files` (bounded depth $\le 10$, max results $\le 200$), `get_metadata`, `create_folder`, `create_file`, `rename_file`, `rename_folder`, `copy_file`, `copy_folder`, `move_file`, `move_folder`, `delete_file`, and `delete_folder`.
    - Mandatory confirmation safeguards: Deleting files requires `confirmed=True`; deleting folders requires `confirmed=True` and non-empty folders require `recursive=True`; overwriting existing files during creation, copying, or moving requires explicit confirmation.
  - `screenshot_controller.py`: Privacy-preserving screenshot capture engine.
    - Desktop capture across all monitors via `PIL.ImageGrab.grab(all_screens=True)`.
    - Primary monitor capture via `EnumDisplayMonitors` primary flag detection.
    - Specific monitor selection by 1-based index with bounds validation.
    - Window-scoped screenshot capture with allowlist ownership verification, existence check, responsiveness check, and minimized window rejection (`WindowCaptureError`).
    - DWM Extended Frame Bounds detection via `DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS)` excluding invisible window margins.
    - Approved output directory containment (defaults to `~/Pictures/VoxPilot_Screenshots`).
    - Filename sanitization, traversal prevention, and overwrite protection.
  - Agent Tools: [`backend/tools/files.py`](./backend/tools/files.py) and [`backend/tools/screenshots.py`](./backend/tools/screenshots.py) provide structured Pydantic schemas and tool functions with clear preview responses on unconfirmed destructive requests.
  - Test suites: [`tests/test_file_manager.py`](./tests/test_file_manager.py) (44 unit tests) and [`tests/test_screenshot_controller.py`](./tests/test_screenshot_controller.py) (19 unit tests). All 63 new tests passing in ~1.0s.
- [x] Phase 3, Step 5: Safe Browser Automation with Playwright in [`backend/automation/`](./backend/automation/) and [`backend/tools/`](./backend/tools/):
  - `browser_controller.py`: Safe, isolated web automation controller using Playwright Chromium.
    - Ephemeral profile isolation: launches clean Chromium browser context (`browser.new_context()`) with zero access to personal user Chrome/Edge data, session cookies, or saved credentials.
    - URL validation & SSRF defense: enforces `http` and `https` schemes; blocks `file://`, `javascript:`, `data:`, `chrome://`, `about:`; blocks localhost (`localhost`, `127.0.0.1`, `::1`), private IP ranges (RFC 1918 `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `100.64.0.0/10`), link-local (`169.254.0.0/16`), and cloud metadata (`169.254.169.254`).
    - Post-redirect re-validation: verifies destination URL after redirects, evacuating session immediately to `about:blank` if redirect lands on an unsafe target.
    - DOM interaction safety: strictly relies on safe Playwright locators (`click()`, `fill()`, `select_option()`, `mouse.wheel()`). Arbitrary JavaScript execution is completely prohibited.
    - Consequential action confirmation: operations with external impact (form submission, purchases, messaging, account changes) require explicit confirmation (`confirmed=True`).
    - Safe download handling: downloads saved only into approved folder (`~/Downloads/VoxPilot_Downloads`) with filename sanitization and traversal prevention.
    - Bounded text extraction: visible page text capped at 10,000 characters to prevent context window exhaustion.
  - Agent Tools: [`backend/tools/browser.py`](./backend/tools/browser.py) provides Pydantic schemas (`StartBrowserInput`, `NavigateUrlInput`, `GetPageInfoInput`, `GetPageTextInput`, `ClickElementInput`, `EnterTextInput`, `SelectDropdownInput`, `ScrollPageInput`, `NavigateHistoryInput`, `CaptureBrowserScreenshotInput`, `CloseBrowserInput`, `BrowserActionResult`) and tool functions (`start_browser_tool`, `navigate_url_tool`, `get_page_info_tool`, `get_page_text_tool`, `click_element_tool`, `enter_text_tool`, `select_dropdown_tool`, `scroll_page_tool`, `navigate_history_tool`, `capture_browser_screenshot_tool`, `close_browser_tool`).
  - Test suite: [`tests/test_browser_controller.py`](./tests/test_browser_controller.py) (31 unit tests passing in 0.79s). Total project test suite: 357 unit tests + 2 live tests passing.

- [x] Phase 3 Final Security Remediation and Approval (Approved & Hardened):
  - `confirmation_manager.py`: Implemented cryptographic, action-specific, time-bounded, single-use confirmation architecture (`ConfirmationManager`). Completely eliminated LLM self-confirmation vulnerability (SEC-009). Tokens carry 256-bit entropy, bound to immutable SHA-256 parameter hashes, requiring human user approval via dedicated approval endpoints before execution. Replay prevention, expiration, parameter tamper detection, and action mismatch checks enforced fail-closed across destructive file operations (`delete_file`, `delete_folder`) and consequential browser actions.
  - Safe Browser File Downloads: Fully implemented `download_file` on `BrowserController` and `download_file_tool` in `backend/tools/browser.py`. Enforces containment within approved download directory (`~/Downloads/VoxPilot_Downloads`), sanitizes filenames, rejects path traversal and Windows device names, unconditionally blocks dangerous executable/script extensions (`.exe`, `.bat`, `.cmd`, `.ps1`, `.vbs`, `.msi`, `.dll`, etc.), enforces overwrite confirmation via tokens, and strictly never executes downloaded files.
  - DNS Resolution Pre-Validation & SSRF Review: Pre-navigation and post-redirect URL validation actively resolves domain names via `socket.getaddrinfo()` to block domains resolving to private networks, loopback (`127.0.0.1`), or cloud metadata endpoints (`169.254.169.254`). Rejects non-standard IP representations (decimal integers, hex, octal dotted, IPv4-mapped IPv6 literals). Documented architectural limitation regarding TOCTOU dynamic DNS rebinding without an egress proxy in TDR-014.
  - Controlled Browser Live Testing: Added `@pytest.mark.live` browser test (`test_live_browser_automation`) verifying real Chromium headless lifecycle, navigation to local HTTP server (`127.0.0.1:0`), text extraction, form typing, element clicking, screenshot capture, and complete session cleanup without external network/service access.
  - Test suites: 380 unit tests (including 10 `test_confirmation_manager.py` tests, 42 `test_browser_controller.py` tests, 46 `test_file_manager.py` tests) + 3 live tests passing. Total 383 automated tests.

- [x] Phase 4, Step 1: LangGraph Agent Execution and Tool Integration implemented in [`backend/agent/`](./backend/agent/), [`backend/tools/`](./backend/tools/), and [`backend/api/`](./backend/api/):
  - `state.py`: Extended `VoxPilotState` with `current_step`, `current_action`, `current_execution_result`, `execution_results`, `completed_steps`, `failed_steps`, `pending_confirmation`, and `execution_id`.
  - `tool_registry.py`: Comprehensive tool registration and dispatch mapping approved actions to immutable `ToolDefinition` dataclasses. Pydantic schema validation rejecting unknown/unsupported arguments (extra-parameter injection protection). Aliases for natural planner queries. Backend confirmation policy check (`check_confirmation_required`).
  - `tool_executor.py`: Secure execution engine enforcing parameter validation, confirmation checking via `ConfirmationManager`, tool execution, and universal handling of Pydantic and dictionary tool returns. LLM-supplied confirmation flags cannot bypass confirmation boundaries.
  - `system.py`: Safe read-only system metrics tool (`get_system_info_tool`) using `psutil` and `platform` without shell or command execution.
  - `graph.py`: Complete compiled 6-node LangGraph StateGraph (`recognize_intent`, `create_plan`, `validate_plan`, `execute_tool`, `evaluate_result`, `generate_response`) with multi-step execution loop and execution resumption (`resume_execution`).
  - `main.py` and `backend/api/agent.py`: Safe FastAPI endpoints (`POST /agent/execute`, `GET /agent/status/{id}`, `GET /agent/confirmations/{id}`, `POST /agent/confirmations/{id}/approve`, `POST /agent/confirmations/{id}/reject`, `GET /agent/tools`, `GET /health`) backed by thread-safe in-memory session store.
  - `voice_pipeline.py`: Unified voice pipeline allowing injection of `VoxPilotAgent`, sharing the exact execution graph and confirmation safeguards.
  - Test suites: [`tests/test_tool_registry.py`](./tests/test_tool_registry.py), [`tests/test_agent_execution.py`](./tests/test_agent_execution.py), and [`tests/test_agent_api.py`](./tests/test_agent_api.py) (36/36 tests passing).
- [x] Phase 4, Step 2: Persistent Memory, Contextual Reasoning, and Dynamic Re-Planning in [`backend/database/`](./backend/database/), [`backend/memory/`](./backend/memory/), and [`backend/agent/`](./backend/agent/):
  - `models.py`: SQLModel entities (`ConversationRecord`, `ExecutionRecord`, `ExecutionStepRecord`) with cascade relationships, foreign keys, and indexes. Sensitive keys (`token`, `password`, `secret`, `api_key`, `auth`) are completely stripped before serialization via `sanitize_dict_for_storage`. Database files are strictly excluded from Git in `.gitignore`.
  - `connection.py`: Asynchronous (`sqlite+aiosqlite`) and synchronous (`sqlite`) SQLite engine factories, connection URL resolution (`data/voxpilot.db` default, CWD-independent), `init_db()`, `init_db_sync()`, `close_db()`.
  - `repository.py`: `DatabaseRepository` providing async and sync transactional CRUD methods (`create_or_get_conversation`, `save_execution`, `save_execution_sync`, `record_execution_step`, `get_execution`, `list_conversations`, `list_executions_for_conversation`).
  - `context_manager.py`: `ConversationContextManager` providing isolated, thread-safe, multi-turn memory bounded by `max_turns=5`. Tracks active window (`hwnd`, `title`, `app_name`), recent files (up to 10), recent URLs (up to 5), and recent application. Resolves pronouns ("it", "the window", "that app") and ordinal references ("first file", "second file"). Detects ambiguous references and requests explicit user clarification without guessing.
  - `planner.py` & `graph.py` (Dynamic Re-Planning): Integrated `replan_plan` node and `_should_replan` conditional edge. Supports file discovery path binding, multi-match clarification, zero-match notification, runtime HWND binding from application launches, and intermediate failure aborts. Enforces hard safety cap `MAX_REPLANS = 2`, schema validation via `validate_plan`, and completed step preservation without re-execution.
  - Security Trust Boundary Hardening: Confirmation tokens strictly removed from spoken voice output, assistant responses, LLM history, and SQLite persistence. Spoken audio says only: *"VoxPilot needs your approval before performing this action. Please review the pending confirmation in the application."* Cross-execution approval token mismatches return HTTP 403 Forbidden. Single-use token consumption (`validate_and_consume`) and parameter tamper detection are enforced.
  - FastAPI Endpoints: Added `GET /agent/conversations`, `GET /agent/conversations/{conversation_id}`, `GET /agent/conversations/{conversation_id}/executions`, and database fallback for `GET /agent/status/{execution_id}`.
- [x] Phase 4, Step 3: Final Integration Audit, Confirmation Security, and End-to-End Verification in [`backend/agent/`](./backend/agent/), [`backend/api/`](./backend/api/), [`backend/database/`](./backend/database/), and [`backend/memory/`](./backend/memory/):
  - Authoritative Tool Registry Audit: Confirmed exactly 37 canonical tools, 11 aliases, and 2 pseudo-actions (`respond`, `ask_clarification`) in `backend/agent/tool_registry.py`. Replaced nonexistent file action names (`read_file`, `open_file`, `write_file`) with the canonical inspection tool `get_file_metadata`. Verified 1:1 parity between `AgentPlanner.allowed_actions` (39 items) and `AgentPlanner.intent_actions` (0 unmapped actions).
  - Human Approval Trust Boundary & CSRF Hardening:
    - Confirmation operations (`approve_confirmation`, `reject_confirmation`) are strictly excluded from `ToolRegistry` so the LLM cannot call or self-approve actions.
    - LLM-supplied `confirmed=True` or `is_consequential=False` flags in parameters are rejected at the execution trust boundary.
    - Approvals are strictly bound to execution ID, action type, and parameter hash via SHA-256 (`compute_action_hash`).
    - Cross-execution token mismatches return HTTP 403 Forbidden. Single-use token consumption (`validate_and_consume`) and parameter tampering detection are enforced.
    - Token leakage prevention: Tokens are never exposed in assistant responses, LLM history, spoken voice output, or SQLite storage.
    - Localhost Binding: Enforced `host="127.0.0.1"` in `backend/main.py` entrypoint.
    - CSRF Browser Protection: Implemented `_verify_trusted_request` checking `Origin` headers on `/agent/confirmations/{id}/approve` and `/agent/confirmations/{id}/reject`. Cross-origin browser requests (e.g. from `http://evil.com`) are rejected with HTTP 403 Forbidden.
  - Database Consistency, Pragmas, and Event Loop Non-Blocking:
    - Enforced SQLite `PRAGMA foreign_keys = ON;` and `PRAGMA journal_mode = WAL;` on all sync and async connections via SQLAlchemy connection event listeners.
    - Foreign key violations (e.g. orphan execution or step records) raise `IntegrityError`.
    - Wrapped synchronous agent calls (`agent.process_request`, `agent.resume_execution`) in `await anyio.to_thread.run_sync(...)` in FastAPI handlers to prevent event loop blocking.
    - Dual sync/async engine parity and data persistence verified across engine connections.
  - Conversational Memory Stale Target Revalidation:
    - Added `is_window_alive` and `is_file_accessible` validators to `ConversationContextManager`.
    - Stale target revalidation evicts dead window HWNDs and deleted file paths, asking for clarification instead of operating on invalid handles.
    - Conversation isolation verified: Conversation B does not inherit Conversation A's window context and asks for clarification.
  - End-to-End Verification: Tested all 13 core workflows across REST API, LangGraph orchestration, tool execution, and voice pipeline.
  - Phase 4 Test Suites: [`tests/test_confirmation_security.py`](./tests/test_confirmation_security.py) (14 tests), [`tests/test_database_concurrency.py`](./tests/test_database_concurrency.py) (8 tests), and [`tests/test_agent_integration.py`](./tests/test_agent_integration.py) (13 tests).
- [x] Phase 5, Prompt 1: Global Emergency Stop and Safe Automation Cancellation in [`backend/automation/emergency_stop.py`](./backend/automation/emergency_stop.py):
  - `EmergencyStopManager`: Thread-safe central latching coordinator managing emergency stop status, trigger source (`hotkey`, `api`, `manual`), reason, and timestamp.
  - Fail-Closed Active Operation Registry: Tracks in-flight automation operations (`operation_id`, `execution_id`, `category`, `description`, `start_time`, `cancellation_requested`, `status`). When emergency stop is active, new registrations are immediately rejected with `EmergencyStopActiveError`.
  - Global Windows Hotkey Listener (`Ctrl + Alt + Escape`): Native Win32 `RegisterHotKey` message loop (`GetMessageW` / `DispatchMessageW`) running on an independent background thread. Cleanly exits on `WM_QUIT` without thread aborts. Unregisters hotkey cleanly upon shutdown.
  - Direct Win32 Hardware Input Cleanup (`release_system_inputs()`): Immediately clears physical modifier keys (Ctrl, Alt, Shift, LWin, RWin) and releases mouse buttons (left, right, middle) via `user32.keybd_event` and `user32.mouse_event`.
  - Automation Controller Cancellation Checkpoints:
    - `keyboard_controller.py`: Verifies emergency stop pre-execution and between typing chunks. Aborts typing, calls `release_all_modifiers()`, and raises `EmergencyStopActiveError`.
    - `mouse_controller.py`: Verifies emergency stop before movement, clicks, and scrolls. Calls `release_all_buttons()` on halt.
    - `browser_controller.py`: Registers cleanup callback with manager; closes active browser sessions and halts navigations/clicks/downloads on emergency stop.
    - `tool_executor.py`: Registers active operations around tool execution and maps `EmergencyStopActiveError` to structured `ExecutionResult(status="cancelled")`.
  - LangGraph Workflow Cancellation:
    - `process_request` and `resume_execution` reject execution if stop is active.
    - Graph nodes (`recognize_intent`, `create_plan`, `validate_plan`, `execute_tool`, `evaluate_result`, `replan_plan`) halt immediately when active and preserve completed steps.
    - Conditional edge routing directs cancelled state immediately to response generation.
    - Dynamic re-planning is strictly prevented during emergency stop.
  - FastAPI Emergency Stop Endpoints in [`backend/api/agent.py`](./backend/api/agent.py):
    - `POST /agent/emergency-stop`: Immediate global trigger.
    - `GET /agent/emergency-stop/status`: Status inspection and active operations list.
    - `POST /agent/emergency-stop/reset`: Safe explicit reset with active operation verification. Rejects reset with HTTP 409 Conflict if active operations are running (unless `force=True`).
    - Emergency stop reset is strictly excluded from `ToolRegistry` to prevent LLM self-reset.
  - Voice Pipeline Safeguards: Truthfully informs user of active emergency stop and refuses commands until explicitly reset.
  - New Test Suites:
    - [`tests/test_emergency_stop.py`](./tests/test_emergency_stop.py) (15 tests): Core state machine, idempotent triggering, active operation tracking, safe/unsafe reset validation, cleanup callbacks, hotkey listener lifecycle.
    - [`tests/test_emergency_stop_api.py`](./tests/test_emergency_stop_api.py) (7 tests): REST API endpoints, CSRF origin verification, HTTP 409 rejection, tool registry omission.
    - [`tests/test_agent_cancellation.py`](./tests/test_agent_cancellation.py) (5 tests): Pre-execution rejection, resumption rejection, mid-workflow step halts, dynamic replanning prevention, voice pipeline safeguards.
    - [`tests/test_emergency_stop_integration.py`](./tests/test_emergency_stop_integration.py) (12 tests): Keyboard modifier release, mouse button release, browser session cleanup callbacks, and ToolExecutor cancellation lifecycle.
- [x] Phase 5, Step 2: Rate Limiting, Safety Checkpoints, and Audit Trail Export in [`backend/agent/`](./backend/agent/), [`backend/automation/`](./backend/automation/), and [`backend/database/`](./backend/database/):
  - Emergency Stop Safe Reset & Operator Recovery:
    - Removed unrestricted `force=True` reset from normal operation in `backend/automation/emergency_stop.py`.
    - Standard `reset_emergency_stop()` requires that all tracked operations have cleanly terminated and unregistered (`len(_active_operations) == 0`); raises `UnsafeResetError` (HTTP 409) if operations are running.
    - Implemented documented `operator_emergency_recovery()`: requires detailed operator justification ($\ge 10$ chars), explicit enumeration of all confirmed terminated operations, and verifies process PID termination via `psutil`. Never clears records merely due to elapsed time; never auto-resumes interrupted executions.
    - Endpoints: `POST /agent/emergency-stop/reset` and `POST /agent/emergency-stop/recover` protected by localhost and CSRF Origin checks.
  - In-Process Sliding-Window Rate Limiter & Resource Budgets ([`backend/agent/rate_limiter.py`](./backend/agent/rate_limiter.py)):
    - Configurable defaults and environment variable overrides: requests/min (20), concurrent active executions (2), conversation requests/min (10), tool calls/execution (15), global tool calls/min (60), same-tool repetition/execution (5), plan step budget (10), execution duration budget (120s), browser navigations budget (5), downloads budget (3).
    - Thread-safe sliding-window implementation pruning expired timestamps older than 60s.
    - Admitted and rejected attempts recorded to prevent infinite retry loops.
  - Tool Executor Strict Safety Evaluation Order ([`backend/agent/tool_executor.py`](./backend/agent/tool_executor.py)):
    - Enforced 7-stage evaluation pipeline: (1) Emergency stop, (2) Execution duration budget, (3) Tool-call budget, (4) Tool-specific rate limits, (5) Parameter validation, (6) Confirmation requirements, (7) Tool execution under active operation tracking.
    - Exceeded limits return structured `ExecutionResult(status="rate_limited", success=False)`.
  - LangGraph Safety Checkpoints ([`backend/agent/graph.py`](./backend/agent/graph.py) & [`backend/agent/state.py`](./backend/agent/state.py)):
    - Pre-dispatch request rate limiting and concurrent slot acquisition/release in `process_request`.
    - Plan step count safety validation against `rate_limiter.config.max_steps`.
    - Mid-execution duration budget check in `execute_tool`.
    - Immediate execution halt upon receiving `rate_limited` status in `evaluate_result`, preserving completed steps without dynamic re-planning.
    - Duration budget check in `_should_replan` preventing re-planning after duration exhaustion.
    - Pre-resumption duration and slot check in `resume_execution`.
  - Browser & Filesystem Safeguards:
    - `BrowserController`: Enforced navigation count limit ($\le 5$) and download limit ($\le 3$) per session with automatic session counter resets.
    - `FileManager`: Enforced `MAX_SEARCH_RESULTS = 100` and `MAX_SEARCH_SCANNED_ITEMS = 1000` to prevent unbounded filesystem traversals.
  - Audit Trail Export Subsystem ([`backend/database/audit_export.py`](./backend/database/audit_export.py) & [`backend/api/agent.py`](./backend/api/agent.py)):
    - `AuditExporter`: Queries persistent SQLite `ExecutionRecord` and `ExecutionStepRecord`.
    - Formats: Structured JSON and flattened CSV.
    - Sensitive data scrubber: recursively redacts tokens, passwords, keys, and credentials with `"[REDACTED]"`.
    - Approved destination containment: outputs strictly confined to `data/exports`, preventing path traversal.
    - Overwrite confirmation: requires `confirm_overwrite=True` if target file exists.
    - Exported files are never opened or executed.
    - REST API: `POST /agent/audit/export` and `GET /agent/audit/exports/{export_id}/status`.
    - Reset, recovery, and audit export tools are strictly excluded from `ToolRegistry` (inaccessible to LLM).
  - New Test Suites:
    - [`tests/test_emergency_stop_reset.py`](./tests/test_emergency_stop_reset.py) (9 tests)
    - [`tests/test_rate_limiter.py`](./tests/test_rate_limiter.py) (7 tests)
    - [`tests/test_execution_budgets.py`](./tests/test_execution_budgets.py) (7 tests)
    - [`tests/test_safety_checkpoints.py`](./tests/test_safety_checkpoints.py) (5 tests)
    - [`tests/test_audit_export.py`](./tests/test_audit_export.py) (10 tests)
- [x] Phase 6, Prompt 1: React + Vite Frontend Dashboard Foundation in [`frontend/`](./frontend/):
  - Application Architecture: Clean React 18 + Vite 6 + TypeScript desktop application scaffold with modular component organization.
  - Design System: Dark modern desktop-agent theme implemented in [`frontend/src/styles/globals.css`](./frontend/src/styles/globals.css) and [`frontend/src/styles/dashboard.css`](./frontend/src/styles/dashboard.css) featuring neutral charcoal background, subtle borders, restrained accents, and responsive layout across desktop, laptop, and mobile viewports.
  - Centralized API Client ([`frontend/src/api/client.ts`](./frontend/src/api/client.ts)): Typed request methods for existing FastAPI backend routes (`/health`, `/`, `/agent/emergency-stop/status`, `/agent/conversations`, `/agent/tools`) with timeout handling, error normalization, and configurable `VITE_API_BASE_URL` (defaulting to `http://127.0.0.1:8000`).
  - Backend Connectivity & Health Hook ([`frontend/src/hooks/useBackendHealth.ts`](./frontend/src/hooks/useBackendHealth.ts)): Periodic 15s health-check polling tracking connection state (`connected`, `disconnected`, `checking`), backend service metadata, emergency-stop status, and manual refresh triggers.
  - Reusable Component Foundation:
    - Common states: `LoadingState.tsx`, `ErrorState.tsx`, `EmptyState.tsx`, `UpcomingSection.tsx`.
    - Layout: `Header.tsx` (title, status pill, last checked timestamp, refresh button, settings quick link), `Sidebar.tsx` (responsive collapsible sidebar, navigation, emergency-stop footer badge), `AppLayout.tsx`.
    - Dashboard: `StatusCard.tsx` (reusable metric cards), `BackendStatus.tsx` (detailed connectivity telemetry), `SystemOverview.tsx` (clear unavailable state explaining unexposed system metrics route per security boundaries), `RecentActivity.tsx` (queries persistent SQLite conversation sessions, displaying empty state when clean).
  - Navigation: 7 navigation items (Dashboard fully functional; AI Agent, Executions, Confirmations, Automation, Audit Logs, and Settings cleanly marked as upcoming with dedicated informational views and zero fake API calls).
  - Backend CORS Integration: Added `CORSMiddleware` to [`backend/main.py`](./backend/main.py) restricted strictly to local frontend origins (`http://localhost:5173`, `http://127.0.0.1:5173`, `http://localhost:4173`, `http://127.0.0.1:4173`).
  - Phase 5 Test-Count Investigation: Verified exact collection of 560 tests (557 non-live + 3 live). Confirmed Phase 5 Prompt 2 added 38 new tests to the Phase 5 Prompt 1 baseline of 519 non-live tests ($519 + 38 = 557$), with 0 replaced or removed tests.
  - Frontend Verification: Passed `npm run lint` (0 errors, 0 warnings), `npm run typecheck` (0 errors), `npm run build` (clean production bundle generated).
- [x] Phase 6, Prompt 2: AI Agent Chat, Execution History, Confirmations, and Safety Controls in [`frontend/`](./frontend/):
  - AI Agent Chat Interface ([`frontend/src/pages/AgentPage.tsx`](./frontend/src/pages/AgentPage.tsx)):
    - Integrated with `POST /agent/execute` and conversation management endpoints.
    - Multiturn conversation sessions stored in SQLite with dynamic session switching and fresh session initialization via `ConversationList.tsx`.
    - Message stream rendering user prompts, agent responses, tool step execution summaries, cancellation notices, and pending confirmation alerts via `ChatMessage.tsx`.
    - Auto-scrolling message container and duplicate submission prevention during active processing.
    - Real-time execution progression banner (`ExecutionProgress.tsx`) reflecting true backend statuses (`executing`, `confirmation_required`, `completed`, `failed`, `cancelled`, `rate_limited`).
  - Execution History & Step Telemetry ([`frontend/src/pages/ExecutionsPage.tsx`](./frontend/src/pages/ExecutionsPage.tsx)):
    - Fetches persistent execution records from SQLite across all conversations (`ExecutionList.tsx`).
    - Filterable by status (`all`, `completed`, `failed`, `cancelled`, `confirmation_required`, `rate_limited`).
    - Detailed execution inspection modal (`ExecutionDetails.tsx`) and step viewer (`ExecutionStep.tsx`) rendering planned sequences, tool outputs, and sanitized parameter summaries.
  - Human-in-the-Loop Confirmation Center ([`frontend/src/pages/ConfirmationsPage.tsx`](./frontend/src/pages/ConfirmationsPage.tsx)):
    - Dedicated security review queue discovering pending consequential actions (`ConfirmationCard.tsx`).
    - Explicit approval and rejection modal dialogs (`ConfirmationDialog.tsx`) bound to cryptographic confirmation tokens with optional rejection reasons.
    - Prominent development security notice banner explaining the localhost desktop trust boundary.
    - Zero token leakage, zero assistant self-approval, single-use token consumption.
  - Global Emergency Stop Safety Controls ([`frontend/src/components/safety/EmergencyStopPanel.tsx`](./frontend/src/components/safety/EmergencyStopPanel.tsx)):
    - Prominently visible emergency stop panel integrated directly on the Dashboard and sidebar.
    - Big red "EMERGENCY STOP" trigger button invoking `POST /agent/emergency-stop`.
    - Safe reset confirmation dialog (`ResetConfirmationDialog.tsx`) enforcing active operation zero-check before reset, handling HTTP 409 Conflict.
  - Navigation Integration: Dashboard, AI Agent, Executions, and Confirmations sidebar items are fully functional. Automation, Audit Logs, and Settings remain cleanly designated as upcoming.
  - Automation Monitoring Page ([`frontend/src/pages/AutomationPage.tsx`](./frontend/src/pages/AutomationPage.tsx)):
    - Real-time telemetry monitoring for desktop automation controllers (Keyboard, Mouse, Window, Browser, Filesystem).
    - Truthful "Status unavailable" reporting for internal controllers without unauthenticated public polling routes.
    - Integrated with emergency-stop status and active background operation tracking.
    - Category-filtered tool allowlist display from `GET /agent/tools` identifying consequential operations requiring human confirmation.
    - Strict read-only safety guardrails: zero direct keyboard, mouse, or shell command buttons exposed to the browser.
  - Audit Logs & Sanitized Export Archive ([`frontend/src/pages/AuditLogsPage.tsx`](./frontend/src/pages/AuditLogsPage.tsx)):
    - Execution audit log list with status, conversation, date range, and text search filters.
    - Detailed audit record inspection drawer with plan steps, tool results, and verified credential scrubbing.
    - Export trigger form for structured JSON and flattened CSV with custom filename validation (blocking path traversal like `../`).
    - Overwrite protection requiring explicit confirmation checkbox to prevent accidental file overwrites.
    - Safe browser blob download handling: streams from `GET /agent/audit/exports/{id}/download`, creates temporary Object URLs, schedules immediate cleanup, and never auto-executes downloaded files.
  - Backend API Client & Endpoints:
    - Extended `frontend/src/api/client.ts` with `exportAuditTrail()`, `getAuditExportStatus()`, `listAuditExports()`, and `downloadAuditExport()`.
    - Added `GET /agent/audit/exports` and `GET /agent/audit/exports/{id}/download` in `backend/api/agent.py` with CSRF Origin validation, canonical export directory containment, and `Content-Disposition` header exposure.
  - Navigation Activation:
    - Activated `Automation` and `Audit Logs` in `navTypes.tsx`.
    - `Settings` cleanly designated as planned for future configuration.
  - Complete Frontend & Browser Test Suites:
    - Vitest unit & component test suite expanded to 29 tests across all pages and safety invariants.
    - End-to-end browser integration suite in `tests/test_frontend_browser.py` verifying 8 mandatory scenarios using headless Chromium.
- [x] Phase 7, Step 1: Full-System Integration Testing and Security Audit:
  - Architecture Integration & Execution Auditing:
    - Verified cross-layer execution flow: React frontend (`frontend/src/`) -> FastAPI REST endpoints (`backend/api/agent.py`) -> LangGraph agent (`backend/agent/graph.py`) -> Intent recognition & planning (`backend/agent/planner.py`) -> Tool Registry (`backend/agent/tool_registry.py`) -> Tool Executor (`backend/agent/tool_executor.py`) -> Win32/Playwright Controllers (`backend/automation/`) -> SQLite database (`backend/database/`) -> React state update.
    - Verified voice pipeline loop parity: Audio capture -> Streaming VAD -> Speech-to-Text (Faster-Whisper) -> LangGraph agent (`process_request`) -> Text-to-Speech (Piper TTS) -> Audio playback.
  - End-to-End Workflow Verification: Verified all 10 core system workflows:
    - Workflow A: General Conversational Queries (zero tool execution, pure LLM response).
    - Workflow B: Single Application Launching (Notepad launched safely, PID tracked, allowlist enforced).
    - Workflow C: Multi-Step Application Control (Launch Notepad, wait for ready, bring to foreground, type text, take window screenshot).
    - Workflow D: Filesystem Discovery & Inspection (`list_directory`, `search_files`, `get_file_metadata` within user folder boundaries).
    - Workflow E: Follow-Up Pronoun & Contextual Resolution ("minimize it", "delete the first file" resolved via `ConversationContextManager`).
    - Workflow F: Safe Browser Automation (Playwright headless Chromium, ephemeral context, domain resolution, SSRF blocking, DOM extraction).
    - Workflow G: Confirmation-Required Consequential Actions (`delete_file`, `delete_folder`, consequential browser actions requiring cryptographic single-use tokens).
    - Workflow H: Global Emergency Stop & Reset (`Ctrl+Alt+Escape` Win32 hotkey, immediate modifier/button release, fail-closed latching, 409 rejection on active operations).
    - Workflow I: Rate Limiting & Resource Budgets (sliding-window request limits, duration budget, step budget, tool-call budgets).
    - Workflow J: Audit Trail Browsing & Safe Export (sanitized JSON/CSV generation in `data/exports`, path traversal guards, safe browser blob download).
  - Security & Trust Boundary Audit:
    - Confirmation tokens: 256-bit entropy (`secrets.token_urlsafe(32)`), single-use consumption (`validate_and_consume`), SHA-256 parameter hashing, 300s TTL. Excluded from `ToolRegistry` to prevent LLM self-approval. Zero token leakage in speech, logs, exports, or DOM.
    - Localhost boundary disclosure: Bound strictly to `127.0.0.1`. CORS and `Origin` validation protect against browser CSRF attacks (e.g. malicious sites calling localhost APIs), but does not authenticate the human operator against other local processes or multi-user OS accounts.
    - Emergency Stop: Fail-closed latching, Win32 `RegisterHotKey` message loop, `release_system_inputs()` releases Ctrl/Alt/Shift/Win and mouse buttons. Safe reset strictly enforces zero active operations.
  - Progress Reporting Protocol Clarification:
    - Investigated progress mechanism: Confirmed that agent execution uses asynchronous HTTP POST request-response (`POST /agent/execute`), with the frontend managing state rendering and visual message streams. There are NO WebSockets or Server-Sent Events (SSE). UI container naming (`.messages-stream-container`) represents UI message layout, not network streaming.
  - Latent Defect Remediation:
    - Discovered and resolved missing `SQLModel` import in `backend/database/repository.py` causing swallowed `NameError` during sync metadata creation.
  - Git Working-Tree Discrepancy Clarification:
    - Investigated Git working-tree status: 19 modified files and 10 untracked directories represent intentional unstaged work from previous phases preserved per "do not commit changes" instruction. The colloquial phrase "clean working tree" in previous handover reports referred to the absence of leftover temporary/crash files, not Git commit status.
  - Test Suite Reconciliation:
    - Pytest non-live: 569 passed (561 backend unit/integration tests + 8 Playwright browser integration tests in `tests/test_frontend_browser.py`).
    - Pytest live: 3 passed (Notepad launch, Notepad window management, Playwright browser lifecycle).
    - Vitest frontend: 29 passed.
    - Total unique tests: 601 passed, 0 failed.

- [x] Phase 7, Step 2: Standalone Windows Packaging and Distribution (PyInstaller One-Folder Distribution):
  - `backend/paths.py`: Unified runtime path resolution subsystem supporting both development mode and frozen PyInstaller bundle (`sys._MEIPASS` / `sys.executable` detection):
    - `is_frozen()`: Detects whether application is running inside a PyInstaller frozen bundle.
    - `get_bundle_dir()`: Returns PyInstaller bundle extraction root (`_internal`) or repository root in dev.
    - `get_executable_dir()`: Returns the directory containing the launched executable or repository root.
    - `get_user_data_dir()`: Returns `%LOCALAPPDATA%\VoxPilot` (with fallback to `~/.voxpilot`), ensuring application data and logs never write into frozen installation directories.
    - Configured paths: `get_database_path()` (`data/voxpilot.db`), `get_log_dir()` (`logs/`), `get_export_dir()` (`exports/`), `get_config_dir()` (`config/`), `get_voice_temp_dir()` (`voice_temp/`), `get_frontend_dist_dir()` (`frontend/dist/`), `get_piper_model_path()` (`models/piper/en_US-lessac-medium.onnx`).
    - Environment overrides: `VOXPILOT_DATA_DIR`, `VOXPILOT_DB_PATH`, `VOXPILOT_LOG_DIR`, `VOXPILOT_EXPORT_DIR`, `VOXPILOT_CONFIG_DIR`, `VOXPILOT_VOICE_TEMP_DIR`, `VOXPILOT_FRONTEND_DIST`, `VOXPILOT_PIPER_MODEL`.
  - Single-Port Full-Stack Hosting in `backend/main.py`:
    - Serves compiled React frontend dashboard from single local port (default 8000).
    - Mounted `/assets` directory for Vite production chunks (CSS, JS).
    - Dual-mode root route (`GET /`): Returns `index.html` when `Accept: text/html` is requested; returns JSON metadata when `Accept: application/json` is requested (preserving automated API test compatibility).
    - Client-side SPA routing fallback: Non-API routes serve `index.html` to support browser refresh on frontend routes (`/chat`, `/executions`, `/confirmations`, `/automation`, `/audit`).
  - Standalone Daemon Entrypoint in `launcher.py`:
    - Dual console and file logging to `%LOCALAPPDATA%\VoxPilot\logs\voxpilot.log`.
    - Startup preflight diagnostics: Checks Ollama LLM endpoint (`http://127.0.0.1:11434`), Piper TTS voice model file, and Playwright Chromium executable. Reports actionable guidance on missing dependencies without crashing.
    - Startup banner displaying version, frozen mode, server URL, database path, and emergency stop hotkey.
    - Auto-launches default browser to dashboard (can be disabled via `VOXPILOT_AUTO_OPEN_BROWSER=0`).
    - Clean Uvicorn server execution and graceful shutdown handling.
  - Distribution Packaging (`VoxPilot.spec` and `hooks/`):
    - One-folder PyInstaller specification producing `dist/VoxPilot/` containing `VoxPilot.exe` (~31.6 MB) and `_internal/`.
    - Bundled assets: Compiled React frontend (`frontend/dist`), Piper ONNX voice model, and Playwright node driver.
    - Custom hook `hooks/hook-webrtcvad.py` resolving metadata discrepancies between `webrtcvad` and `webrtcvad-wheels`.
    - Distribution readme `README_DIST.txt` copied to `dist/VoxPilot/README.txt`.
  - End-to-End Distribution & Relocation Verification (`scripts/verify_distribution.py`):
    - In-place execution test: Starts `dist/VoxPilot/VoxPilot.exe` on port 8001; verifies `/health`, JSON `/`, HTML React `/`, `/agent/emergency-stop/status`, `/agent/tools` (37 registered tools), and `/agent/conversations` (database query).
    - Data persistence verification: Confirms `%LOCALAPPDATA%\VoxPilot\data\voxpilot.db` and `%LOCALAPPDATA%\VoxPilot\logs\voxpilot.log` are populated and persist across restarts.
    - Relocation test: Copies entire `dist/VoxPilot/` to a temporary directory outside the repository (`%TEMP%\VoxPilot_Relocated_Test`), executes `VoxPilot.exe` on port 8002, verifies complete API and UI responsiveness, and cleans up cleanly.
  - Test suites:
    - `tests/test_paths.py`: 16 comprehensive unit tests covering dev mode, frozen mode simulation, environment variable overrides, and directory creation.
    - `scripts/verify_distribution.py`: Live standalone executable validation and relocation testing passing 100%.

- [x] Phase 7, Step 3: Windows Installer and First-Run Distribution Validation (Inno Setup 6):
  - `installer/VoxPilot.iss`: Inno Setup 6 packaging script compiling the PyInstaller standalone one-folder distribution into `installer/Output/VoxPilot-Setup.exe`.
  - Modern, Lowest-Privilege Per-User Installation:
    - Default directory: `{localappdata}\Programs\VoxPilot` (`PrivilegesRequired=lowest`).
    - Eliminates Windows UAC elevation prompts and administrator privilege requirements.
    - Unique application GUID: `AppId={{8F97B634-9A25-45C0-943C-65935E17F193}}`.
  - Start Menu & Desktop Integration:
    - Creates Start Menu group `VoxPilot` with shortcuts: `VoxPilot` (application), `Install Playwright Browser` (browser setup utility), `VoxPilot Readme` (documentation), and `Uninstall VoxPilot` (uninstaller).
    - Optional desktop shortcut task (`Tasks: desktopicon`).
  - Pre-Installation Informational Notice (`installer/INSTALL_NOTICE.txt`):
    - Informs users about data retention in `%LOCALAPPDATA%\VoxPilot`, external Ollama requirements (`qwen3:4b`), bundled Playwright browser setup, built-in speech models, and Emergency Stop (`Ctrl + Alt + Escape`).
  - First-Run Bundled Browser Installer (`install_browser.bat` and `VoxPilot.exe --install-browser`):
    - Directly executes bundled Playwright Node runtime (`_internal\playwright\driver\node.exe` with `cli.js install chromium`) to install Chromium into `%LOCALAPPDATA%\ms-playwright`.
    - Zero Python or Node.js prerequisites required on end-user machines.
  - Single-Instance Daemon Handling in `launcher.py`:
    - Fast HTTP `/health` probe checks if an existing instance of VoxPilot is running on host/port.
    - If already running, focuses/opens the browser dashboard at `http://127.0.0.1:8000/` and exits gracefully with code 0 instead of crashing or raising a port binding exception.
  - Non-Blocking Dependency Preflight:
    - Ollama model tags inspection (`/api/tags`) checking for `qwen3:4b`.
    - Hugging Face cache check for `Systran/faster-whisper-base`.
    - Actionable diagnostics emitted without preventing dashboard startup.
  - Upgrade and Uninstall Safety Invariant:
    - Application binaries are decoupled in `%LOCALAPPDATA%\Programs\VoxPilot`.
    - Persistent state (SQLite `voxpilot.db`, logs, audit exports, screenshots) resides strictly in `%LOCALAPPDATA%\VoxPilot`.
    - Upgrading over an existing installation preserves database and history without modification.
    - Uninstallation cleanly removes binaries and shortcuts while leaving `%LOCALAPPDATA%\VoxPilot` 100% intact with a post-uninstall advisory dialog.
  - Compiled Standalone Installer:
    - Output: `installer/Output/VoxPilot-Setup.exe` (233,334,015 bytes, ~222.52 MB).
    - SHA-256: `0036BC257FABCA414E9F0AA0B5A91554C04C292272B09A9D04FE50154285DAE0`.
  - Comprehensive Verification Suite (`scripts/verify_installer.py`):
    - Automated silent installation into custom path containing spaces (`%TEMP%\VoxPilot Custom Test Install`).
    - Verified installed binary and shortcut layout.
    - Live server startup and endpoint validation on port 8003 (`/health`, JSON `/`, HTML React `/`, Emergency Stop status, 37 registered tools, and SQLite query).
    - Verified data persistence in `%LOCALAPPDATA%\VoxPilot`.
    - Verified smooth upgrade over existing install with database preserved.
    - Verified silent uninstallation removes binaries/shortcuts while preserving `%LOCALAPPDATA%\VoxPilot` (4,096 bytes database intact).

- [x] Post-Phase 7 Enhancements: Critical Agent Planning Bug Fix & Voice Mode Integration with Customizable Global Keyboard Shortcut:
  - Agent Planner Schema Enforcement & Multi-Step Command Decomposition (`backend/agent/planner.py`, `backend/agent/tool_registry.py`):
    - Injected exact required/optional parameter names from `ToolRegistry` into LLM planning prompts so the LLM generates exact schema keys (`application_name`, `action`, `text`, etc.).
    - Implemented deterministic boundary normalization (`_normalize_plan_steps`) mapping common LLM parameter synonyms (e.g., `application` -> `application_name`, `operation` -> `action`, `key_name` -> `key`, `shortcut_keys` -> `shortcut`) without weakening `ToolRegistry.validate_parameters()` (`extra="forbid"`).
    - Implemented deterministic multi-step decomposition (`_build_deterministic_app_plan`, `_extract_app_and_typing_payload`) for application launch, window focus (`manage_window`), text typing (`type_text`), and Enter key press (`press_key`).
    - Added 32 regression tests in [`tests/test_planner_bugfixes.py`](./tests/test_planner_bugfixes.py).
  - Voice Mode Dashboard Integration & Customizable Windows Global Hotkey (`backend/voice/hotkey.py`, `backend/voice/voice_service.py`, `frontend/src/pages/AgentPage.tsx`, `frontend/src/pages/SettingsPage.tsx`):
    - Reused existing `VoicePipeline`, `AudioRecorder`, `SpeechToText` (Faster-Whisper), `TextToSpeech` (Piper ONNX), and `StreamingVAD` (WebRTC VAD).
    - Implemented `VoiceHotkeyListener` using Win32 `RegisterHotKey` on a dedicated background thread (`HOTKEY_ID_VOICE_MODE = 2`, default `Ctrl+Shift+V`) with `MOD_NOREPEAT`, software debounce, automatic rollback to previous valid shortcut on `WinError 1409` conflict, clean `WM_QUIT` + `UnregisterHotKey` shutdown, and strict reservation blocking `Ctrl+Alt+Escape`.
    - Implemented `VoiceSettingsManager` persisting user voice & shortcut preferences in `%LOCALAPPDATA%\VoxPilot\config\voice_settings.json`.
    - Added interactive microphone toggle button, live voice phase indicator (`listening`, `transcribing`, `processing`, `speaking`, `error`, `idle`), live transcript/response synchronization, and `Stop Voice` cancellation in `AgentPage.tsx` and `ChatInput.tsx`.
    - Implemented `SettingsPage.tsx` with interactive "Change Shortcut" key capture, microphone selector, privacy-preserving in-memory `Test Microphone` check, Piper TTS toggle & voice selector, and Faster-Whisper / Streaming VAD status cards.
    - Fixed frontend initial health check race condition so `AgentPage` never falsely flashes `"Backend disconnected"`.
    - Added 20 backend tests in [`tests/test_voice_mode_and_shortcut.py`](./tests/test_voice_mode_and_shortcut.py) and 6 frontend tests in [`frontend/src/tests/voiceAndSettings.test.ts`](./frontend/src/tests/voiceAndSettings.test.ts).
  - Complete Application Icon Replacement & Branding (`assets/icons/`, `VoxPilot.spec`, `installer/VoxPilot.iss`, `frontend/`):
    - Generated official multi-resolution Windows `.ico` (`assets/icons/voxpilot.ico` containing 16x16, 24x24, 32x32, 48x48, 64x64, 128x128, and 256x256 frames) and high-resolution `.png` assets (`16x16` through `1024x1024`) from the authoritative VoxPilot icon design via [`scripts/generate_icons.py`](./scripts/generate_icons.py).
    - Embedded official icon and Windows PE version metadata ([`assets/version_info.txt`](./assets/version_info.txt)) into `VoxPilot.exe` via `VoxPilot.spec` and configured Windows 11 `AppUserModelID` and `WM_SETICON` in `launcher.py`.
    - Replaced default `/vite.svg` placeholder with `/favicon.ico`, `/favicon-32x32.png`, `/favicon-16x16.png`, `/apple-touch-icon.png`, and `/voxpilot-logo.png` across `frontend/index.html`, `Sidebar.tsx`, and `Header.tsx`.
    - Updated `installer/VoxPilot.iss` with `SetupIconFile`, `WizardSmallImageFile`, version info metadata, and explicit `IconFilename` and `AppUserModelID` bindings for Start Menu, Desktop, and Uninstaller shortcuts.
  - In-App Software Update System (Phase 1 — Application-Side Update Functionality):
    - Centralized version authority in [`VERSION`](./VERSION) (`0.5.0`) and [`backend/version.py`](./backend/version.py) with SemVer 2.0.0 comparison.
    - GitHub release checker, HTTPS & trusted-host validator, streaming downloader with cancellation, mandatory SHA-256 checksum verifier, Windows Authenticode inspector, and detached external updater orchestrator in [`backend/updater/update_service.py`](./backend/updater/update_service.py) and [`backend/updater/updater_runner.py`](./backend/updater/updater_runner.py).
    - Persistent update preferences in `%LOCALAPPDATA%\VoxPilot\config\update_settings.json` (`auto_check_enabled`, `channel`, `release_repo`, `dismissed_version`, and enforced `auto_install_enabled: False`).
    - Dashboard update notification banner ([`UpdateNotificationBanner.tsx`](./frontend/src/components/updates/UpdateNotificationBanner.tsx)) and Settings page Software Updates card ([`SettingsPage.tsx`](./frontend/src/pages/SettingsPage.tsx)) with explicit Installation Confirmation modal.
    - Added 39 backend/updater tests ([`tests/test_update_system.py`](./tests/test_update_system.py)) and 9 frontend tests ([`frontend/src/tests/updateSystem.test.ts`](./frontend/src/tests/updateSystem.test.ts)).

---

## 3. Features In Progress
None. All planned implementation phases (Phases 1 through 7) and post-release enhancements are complete.

---

## 4. Features Not Started
None. All 7 planned development phases and milestone tasks are fully complete and verified.

---

## 5. Known Issues & Hardware Considerations
- System hardware: 16GB DDR5 RAM, Intel Core 5 205H, NVIDIA RTX 3050 (6GB VRAM).
- GPU VRAM constraint: 6GB VRAM requires lightweight quantized LLMs (e.g., Qwen 2.5 3B/Qwen 1.5/2.5 4B) and careful memory budgeting. Faster-Whisper can fall back to CPU with INT8 compute if VRAM is congested.
- External Dependency Notice: The standalone `VoxPilot.exe` requires the local Ollama daemon running on `http://127.0.0.1:11434` with `qwen3:4b` for LLM planning. The pre-flight check logs a clear diagnostic if Ollama is offline.
- Playwright Chromium Path in Frozen Mode: Chromium browser binaries reside in `%LOCALAPPDATA%\ms-playwright`. `launcher.py` and `install_browser.bat` automate this installation without requiring Python on target machines.
- SQLite Database Location: In standalone and installed mode, the database resides in `%LOCALAPPDATA%\VoxPilot\data\voxpilot.db`. In development mode, it resides in `data/voxpilot.db`.
- Windows Global Hotkey Ownership: `Ctrl + Alt + Escape` (Emergency Stop) and `Ctrl + Shift + V` (Voice Mode toggle) are registered via Win32 `RegisterHotKey`. If another process has already registered a shortcut, VoxPilot reports the conflict clearly without crashing.

---

## 6. Latest Successful Tests
- `pytest tests/ -v -m "not live"`: Passed (677/677 non-live tests passing across 669 backend unit/integration tests and 8 frontend browser integration tests in 38.32s).
- `pytest -m live -v`: Passed (3/3 live tests passing: Notepad launch, Notepad window management, and controlled Chromium browser automation in 15.72s).
- Total Pytest Suite: Passed (680/680 tests passing: 677 non-live + 3 live).
- `frontend/ npm test`: Passed (44/44 Vitest tests passing in 1.46s).
- `frontend/ npm run lint`: Passed (0 errors, 0 warnings).
- `frontend/ npm run typecheck`: Passed (0 errors).
- `frontend/ npm run build`: Passed (production bundle 300.11 kB JS, 57.70 kB CSS generated in 3.40s).
- `python -m pip check`: Passed (no broken requirements found).
- `scripts/verify_distribution.py`: Passed (in-place `dist/VoxPilot/VoxPilot.exe` execution, `/api/version` & `/api/updates/status` verification, `/agent/voice/status` verification, `/favicon.ico` & `/voxpilot-logo.png` verification, database persistence, and external relocation testing).
- Total automated tests passing: 724 tests (680 pytest + 44 vitest).

---

## 7. Next Immediate Task
Phase 7 Step 3 (Windows Installer and First-Run Distribution Validation) is complete. Deliver the comprehensive final ChatGPT Handover Report.

