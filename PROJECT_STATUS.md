# VoxPilot — Project Status

**Current Development Phase:** Phase 5 — Agent Intelligence and Safety (Step 5.1: Global Emergency Stop Complete)  
**Last Updated:** 2026-11-11  

---

## 1. Phase Progress Overview

| Phase | Description | Status |
|---|---|---|
| **Phase 1** | **Environment and Project Foundation** | **Completed** |
| **Phase 2** | **Voice Interface (Whisper STT + Piper TTS)** | **Completed** |
| **Phase 3** | **Desktop Automation (Windows UI, Apps, Files, Browser)** | **Completed** |
| **Phase 4** | **AI Agent Integration (LangGraph + Ollama)** | **Completed** |
| **Phase 5** | **Agent Intelligence and Safety (Guardrails, Approvals, Emergency Stop)** | **In Progress (Step 5.1 Completed)** |
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

---

## 3. Features In Progress
None. Phase 5, Prompt 1 is fully complete and approved.

---

## 4. Features Not Started
- Phase 5, Prompt 2+: Additional Safety Guardrails, System Confirmation Hardening, Rate Limiting.
- Phase 6: Frontend Dashboard (React + Vite).
- Phase 7: Testing, Packaging, and Distribution (PyInstaller).

---

## 5. Known Issues & Hardware Considerations
- System hardware: 16GB DDR5 RAM, Intel Core 5 205H, NVIDIA RTX 3050 (6GB VRAM).
- GPU VRAM constraint: 6GB VRAM requires lightweight quantized LLMs (e.g., Qwen 2.5 3B/Qwen 1.5/2.5 4B) and careful memory budgeting. Faster-Whisper can fall back to CPU with INT8 compute if VRAM is congested.
- Pretrained Wake Word Catalog: openWakeWord includes official models (`hey_jarvis_v0.1`, `alexa_v0.1`, `hey_mycroft_v0.1`). A custom model for "Hey VoxPilot" requires training with ~20,000–50,000 synthetic Piper TTS utterances as outlined in `models/wake_word/README.md`.
- Windows 11 Packaged Apps: Calculator (`calc.exe`) spawns via launcher stub which terminates immediately after triggering the UWP broker. The launcher captures PID cleanly upon creation without blocking.
- Symlinks / Reparse Points: Path resolution validates canonical targets against approved directory roots. Reparse-point redirection outside approved locations is rejected.
- Multi-Monitor Virtual Coordinates: Monitors arranged to the left or top of the primary monitor use negative coordinates; bounds checking queries `SM_XVIRTUALSCREEN` and `SM_YVIRTUALSCREEN` rather than assuming `(0, 0)`.
- UIPI Integrity Boundaries: Windows prevents standard user processes from injecting input into elevated/admin windows. VoxPilot detects `ERROR_ACCESS_DENIED` and raises `UIPIRestrictedError`.
- Windows DWM Frame Bounds: Standard `GetWindowRect` includes invisible window drop-shadow borders; VoxPilot queries `DwmGetWindowAttribute` with `DWMWA_EXTENDED_FRAME_BOUNDS` for pixel-accurate window cropping.
- Browser Ephemeral Isolation: Playwright executes in headless/headed Chromium using an isolated context; personal browser profiles, cookies, and saved passwords are never accessed.
- SQLite Database Location: Persistent database is stored in `data/voxpilot.db` (git-ignored) with automatic directory creation and schema migration.
- Localhost HTTP Development Boundary: REST confirmation approval on localhost (`127.0.0.1`) represents a development trust boundary; full cryptographic user authentication will be layered in Phase 6 with the frontend.
- Windows Global Hotkey Ownership: `Ctrl + Alt + Escape` hotkey is registered via Win32 `RegisterHotKey`. If another process has already registered this combo, registration logs a warning without crashing.

---

## 6. Latest Successful Tests
- `pytest tests/ -v -m "not live"`: Passed (519/519 unit and integration tests passing across Emergency Stop, Emergency Stop API, Agent Cancellation, Emergency Stop Integration, Confirmation Security, Database Concurrency, Agent Integration, Database Repository, Conversational Memory, Dynamic Re-Planning, Agent API, Agent Execution Graph, Tool Registry, Confirmation Manager, Browser Controller, File Manager, Screenshot Controller, Keyboard Controller, Mouse Controller, Window Manager, Security Regression, Application Launcher, Wake Word Detection, Streaming VAD, Batch Silero VAD, Audio Preprocessor, Microphone Selection, and Voice Subsystem).
- `pytest -m live -v`: Passed (3/3 live tests passing: Notepad launch, Notepad window management, and controlled Chromium browser automation). Total: 522 automated tests passing (0 failed).
- `python -m pip check`: Passed (no broken requirements).
- `pip install --dry-run -r requirements.txt`: Passed (all dependencies resolved cleanly without conflicts).
- Core package import check: Passed (LangGraph, LangChain, langchain-ollama, Faster-Whisper, Piper TTS, openWakeWord, PyAutoGUI, pywinauto, FastAPI, SQLModel, aiosqlite, Playwright, psutil, MCP).

---

## 7. Next Immediate Task
Phase 5, Prompt 2: Additional Agent Safety Guardrails and Action Rate Limiting.

