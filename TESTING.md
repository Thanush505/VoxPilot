# VoxPilot — Testing Record

This file documents all verification commands executed, their environments, and their actual outputs.

---

## Test Run: 2026-11-10 — Environment & Dependency Check

- **Scope:** Phase 1 Foundation
- **Target:** Python 3.12 Virtual Environment (`.venv`)

### 1. Dependency Integrity Check
- **Command:**
  ```powershell
  .\.venv\Scripts\python.exe -m pip check
  ```
- **Result:** PASS
- **Output:** `No broken requirements found.`

### 2. Dependency Resolution Check
- **Command:**
  ```powershell
  .\.venv\Scripts\python.exe -m pip install --dry-run -r requirements.txt
  ```
- **Result:** PASS
- **Output:** All requirements satisfied without version conflict.

### 3. Core Package Import Check
- **Command:**
  ```powershell
  .\.venv\Scripts\python.exe -c "import langgraph, langchain, langchain_ollama, faster_whisper, piper, openwakeword, pyautogui, pywinauto, fastapi, sqlmodel, playwright, mcp; print('ALL_IMPORTS_SUCCESSFUL')"
  ```
- **Result:** PASS (Importable)
- **Note:** Confirms package installation only. Runtime tests for hardware devices (mic/GPU/speakers) and models remain pending per Phase 1 & 2 completion criteria.

---

## Test Run: 2026-11-10 — Voice Subsystem Unit Tests

- **Scope:** Phase 2 Voice Foundation (`backend/voice`)
- **Target:** `tests/test_voice.py`
- **Command:**
  ```powershell
  .\.venv\Scripts\pytest.exe tests/test_voice.py -v
  ```
- **Result:** PASS (7 passed in 1.09s)
- **Detailed Results:**
  - `test_audio_recorder_initialization`: PASSED (Parameter validation and error guard)
  - `test_audio_recorder_save_recording`: PASSED (PCM WAV file creation via soundfile)
  - `test_speech_to_text_initialization`: PASSED (Default model size and device configuration)
  - `test_speech_to_text_validation`: PASSED (Input type and empty array error handling)
  - `test_text_to_speech_initialization`: PASSED (Model path discovery and lazy voice check)
  - `test_text_to_speech_validation`: PASSED (Empty text and unloaded model guards)
  - `test_voice_pipeline_initialization`: PASSED (Full VoicePipeline composition)

---

## Test Run: 2026-11-10 — End-to-End Voice Pipeline Verification

- **Scope:** Phase 2 Voice Subsystem End-to-End (`backend/voice/voice_pipeline.py`)
- **Target:** `test_voice_pipeline.py`
- **Command:**
  ```powershell
  python test_voice_pipeline.py
  ```
- **Result:** PASS (Status: completed)
- **Detailed Log:**
  - Faster-Whisper base model loaded on CPU (int8)
  - Piper voice model loaded (`models/piper/en_US-lessac-medium.onnx`)
  - Ollama connection verified
  - Microphone capture: 5 seconds recorded via sounddevice
  - Speech recognition: successfully transcribed ambient audio
  - LangGraph / Ollama processing: recognized intent, generated multi-step plan, produced response
  - Piper TTS: synthesized response to `models/piper/generated/response.wav`
  - Audio playback: completed through system audio output

---

## Test Run: 2026-11-10 — Configurable Microphone Selection Test Suite

- **Scope:** Phase 2 Step 2: Configurable Microphone Selection (`backend/voice/audio_recorder.py`, `backend/voice/voice_pipeline.py`)
- **Target:** `tests/test_microphone_selection.py` & `tests/test_voice.py`
- **Command:**
  ```powershell
  .\.venv\Scripts\pytest.exe tests/ -v
  ```
- **Result:** PASS (16 passed in 7.01s, 0 failed, 0 skipped)
- **Detailed Results:**
  - `test_get_input_devices_structure`: PASSED (Verified index, name, host_api, channels, default_sample_rate dictionary structure)
  - `test_list_microphones_output`: PASSED (Verified human-readable console device enumeration)
  - `test_resolve_default_microphone`: PASSED (Verified resolution of None, empty string, and 'default' to system default)
  - `test_resolve_microphone_by_index`: PASSED (Verified resolution by integer and string integer)
  - `test_resolve_microphone_by_name_substring`: PASSED (Verified case-insensitive device name matching with WASAPI ranking)
  - `test_resolve_invalid_microphone_raises_error`: PASSED (Verified descriptive AudioRecorderError listing available devices)
  - `test_resampling_conversion_logic`: PASSED (Verified polyphase filtering downsamples 48kHz and 44.1kHz stereo to 16kHz float32 mono)
  - `test_voice_pipeline_respects_env_device`: PASSED (Verified VOXPILOT_MICROPHONE_DEVICE environment variable handling)
  - `test_voice_pipeline_explicit_device_overrides_env`: PASSED (Verified explicit parameter overrides env variable)
  - `test_voice.py` (7 tests): ALL PASSED (Initialization, parameter guards, and components preserved)

---

## Test Run: 2026-11-10 — Audio Preprocessor Test Suite & Transcription Comparison

- **Scope:** Phase 2 Step 3: Audio Preprocessing & Gain Normalization (`backend/voice/audio_preprocessor.py`, `backend/voice/speech_to_text.py`)
- **Target:** `tests/test_audio_preprocessor.py`, `tests/test_microphone_selection.py`, `tests/test_voice.py`
- **Command:**
  ```powershell
  .\.venv\Scripts\pytest.exe tests/ -v
  ```
- **Result:** PASS (30 passed in 10.62s, 0 failed, 0 skipped)
- **Detailed Results:**
  - `tests/test_audio_preprocessor.py` (14 passed):
    - `test_linear_and_dbfs_conversions`: PASSED (Accurate conversions across 0 dBFS, -3 dBFS, -6 dBFS, -120 dBFS)
    - `test_parameter_validation`: PASSED (Blocked target > 0 dBFS, negative max gain, min >= target)
    - `test_quiet_audio_amplified_to_target`: PASSED (Quiet -17.26 dBFS sine amplified to -3.00 dBFS target)
    - `test_already_normalized_audio_unmodified`: PASSED (Audio within 0.5 dB headroom bypasses amplification)
    - `test_silent_audio_remains_silent`: PASSED (All zeros stay zeros with 0 gain)
    - `test_empty_audio_handled_safely`: PASSED (Empty array returns empty float32 array)
    - `test_very_quiet_audio_below_threshold_not_amplified`: PASSED (Noise below -45 dBFS stays unamplified)
    - `test_max_gain_limit_respected`: PASSED (Amplification capped exactly at max_gain_db)
    - `test_clipping_protection`: PASSED (Spikes clipped strictly to [-1.0, 1.0])
    - `test_nan_and_infinity_handled_safely`: PASSED (Non-finite samples sanitized to 0.0)
    - `test_output_format_and_duration_preservation`: PASSED (Array shape, length, and float32 preserved)
    - `test_stereo_downmix_to_mono`: PASSED (2D stereo downmixed to 1D mono)
    - `test_disabled_normalization`: PASSED (Enabled=False leaves audio untouched)
    - `test_does_not_mutate_input_buffer`: PASSED (Caller's input array preserved without in-place mutation)
  - `tests/test_microphone_selection.py` (9 passed): ALL PASSED
  - `tests/test_voice.py` (7 passed): ALL PASSED

### Before-and-After Transcription Comparison on `microphone_test.wav` (Step 3)
- **Audio Measurements:**
  - Original Peak Amplitude: 0.137146 (-17.26 dBFS)
  - Original RMS Amplitude: 0.015519 (-36.18 dBFS)
  - Target Peak Level: -3.00 dBFS
  - Applied Gain: +14.26 dB (Linear multiplier: 5.1620x)
  - Normalized Peak Amplitude: 0.707946 (-3.00 dBFS)
  - Normalized RMS Amplitude: 0.080107 (-21.93 dBFS)
  - Clipping Prevented: False (Clean headroom, no sample exceeded 1.0)
- **Transcription Results (Faster-Whisper `base`, CPU `int8`, beam_size=5):**
  - Original Transcription: `'Hello, hello, hello, hello.'` (Language prob: 1.0000)
  - Normalized Transcription: `'Hello, hello, hello, hello.'` (Language prob: 1.0000)
  - Observation: Both transcriptions accurately detected the spoken greeting. Normalization successfully boosted the signal by +14.26 dB into optimal ASR dynamic range without introducing any distortion, artifacts, or clipping.

---

## Test Run: 2026-11-10 — Streaming Voice Activity Detection & Dynamic Endpointing Suite

- **Scope:** Phase 2 Step 5: Streaming Voice Activity Detection (`backend/voice/streaming_vad.py`, `backend/voice/audio_recorder.py`)
- **Target:** `tests/test_streaming_vad.py`, `tests/test_audio_preprocessor.py`, `tests/test_microphone_selection.py`, `tests/test_vad.py`, `tests/test_voice.py`
- **Command:**
  ```powershell
  .\.venv\Scripts\pytest.exe tests/ -v
  ```
- **Result:** PASS (56 passed in 4.52s, 0 failed, 0 skipped)
- **Detailed Results:**
  - `tests/test_streaming_vad.py` (16 passed):
    - `test_streaming_vad_default_configuration`: PASSED (Verified default parameters: 30ms frames, aggressiveness 2, 800ms end silence, 300ms pre-roll, 300ms min speech, 30s max duration, 10s timeout)
    - `test_streaming_vad_parameter_validation`: PASSED (Verified guards against invalid frame durations, aggressiveness, sample rates, and non-positive timeouts)
    - `test_speech_onset_and_endpoint_detection`: PASSED (Verified onset state transition and trailing silence completion)
    - `test_leading_silence_and_pre_roll_preservation`: PASSED (Verified 300ms pre-roll preserves initial consonant/syllable)
    - `test_trailing_silence_preservation`: PASSED (Verified trailing silence up to 800ms retained)
    - `test_brief_inter_word_pauses_do_not_terminate`: PASSED (Verified 300ms pause between words does not prematurely cut off recording)
    - `test_no_speech_start_timeout`: PASSED (Verified 2.0s silence times out and returns empty audio without hanging)
    - `test_max_recording_duration_enforced`: PASSED (Verified 3.0s max duration ceiling cleanly caps recording)
    - `test_minimum_speech_duration_filters_clicks`: PASSED (Verified 60ms click is filtered out and does not trigger false recording)
    - `test_empty_and_varying_chunk_sizes_handled_safely`: PASSED (Verified irregular chunks of 100, 250, 512, 1024 samples buffer without frame misalignment)
    - `test_output_format_is_16khz_mono_float32`: PASSED (Verified 1D float32 output contract)
    - `test_audio_recorder_fixed_duration_still_works`: PASSED (Verified backward compatibility of record(duration=1))
    - `test_audio_recorder_record_until_silence_streaming_disabled`: PASSED (Verified fallback to fixed duration when streaming VAD is disabled)
    - `test_audio_recorder_record_until_silence_with_stream`: PASSED (Verified InputStream callback queue capture and assembly)
    - `test_voice_pipeline_streaming_vad_integration`: PASSED (Verified VoicePipeline passes streaming flag and listen() calls record_until_silence)
    - `test_env_variables_configure_streaming_vad`: PASSED (Verified VOXPILOT_STREAMING_VAD_* env overrides)
  - `tests/test_audio_preprocessor.py` (14 passed): ALL PASSED
  - `tests/test_microphone_selection.py` (9 passed): ALL PASSED
  - `tests/test_vad.py` (10 passed): ALL PASSED
  - `tests/test_voice.py` (7 passed): ALL PASSED

### Performance & Resource Usage (WebRTC Streaming VAD)
- **Frame Processing Latency:** Average 12.46 μs per 30 ms frame (p99: 57.36 μs).
- **Processing Time Budget:** At 30,000 μs per frame, WebRTC VAD consumes only **0.042%** of a single CPU core.
- **Latency from Speech Offset to Stop:** Exactly equal to `end_silence_ms` (800 ms), completely eliminating arbitrary multi-second dead recording waits.
- **Hardware Mic Verification:** Executed live speech recording on Realtek Laptop Microphone Array. User spoke "Where are you?", streaming VAD detected speech onset, captured the full utterance, and Faster-Whisper transcribed `'Where are you?'` accurately with no clipped syllables.


- **Scope:** Phase 2 Step 4: Voice Activity Detection (VAD) Integration (`backend/voice/speech_to_text.py`)
- **Target:** `tests/test_vad.py`, `tests/test_audio_preprocessor.py`, `tests/test_microphone_selection.py`, `tests/test_voice.py`
- **Command:**
  ```powershell
  .\.venv\Scripts\pytest.exe tests/ -v
  ```
- **Result:** PASS (40 passed in 5.70s, 0 failed, 0 skipped)
- **Detailed Results:**
  - `tests/test_vad.py` (10 passed):
    - `test_vad_default_configuration`: PASSED (Verified default threshold=0.5, min_speech=250ms, min_silence=500ms, pad=200ms)
    - `test_vad_enabled_passed_to_whisper`: PASSED (Verified vad_filter=True and parameter dict forwarded to transcribe)
    - `test_vad_disabled_passed_to_whisper`: PASSED (Verified vad_filter=False and vad_parameters=None forwarded when disabled)
    - `test_custom_vad_parameters_reach_whisper`: PASSED (Verified custom threshold, durations, and padding forwarded accurately)
    - `test_invalid_vad_threshold_raises_error`: PASSED (Verified threshold outside (0.0, 1.0) raises SpeechToTextError)
    - `test_invalid_vad_durations_raise_error`: PASSED (Verified negative durations and padding raise SpeechToTextError)
    - `test_audio_normalization_precedes_vad`: PASSED (Verified Preprocessor.process() runs before model.transcribe())
    - `test_vad_applied_exactly_once`: PASSED (Verified model.transcribe() called exactly once per transcribe_audio())
    - `test_file_transcription_uses_configured_vad`: PASSED (Verified transcribe_file() forwards configured VAD settings)
    - `test_env_variables_configure_vad`: PASSED (Verified VOXPILOT_VAD_* env variables correctly configure STT)
  - `tests/test_audio_preprocessor.py` (14 passed): ALL PASSED
  - `tests/test_microphone_selection.py` (9 passed): ALL PASSED
  - `tests/test_voice.py` (7 passed): ALL PASSED

### Real Benchmark on `microphone_test.wav`: Test A (VAD OFF) vs Test B (VAD ON)
- **Audio File Details:** `microphone_test.wav` (16,000 Hz, 80,000 samples, 5.00 seconds duration)
- **Preprocessing:** AudioPreprocessor enabled, gain normalized peak to 0.7079 (-3.00 dBFS)
- **Model Configuration:** Faster-Whisper `base`, CPU `int8`

| Metric | Test A: Normalization ON, VAD OFF | Test B: Normalization ON, VAD ON |
|---|---|---|
| **VAD Filter** | Disabled (`vad_filter=False`) | Enabled (`vad_filter=True`, Silero VAD) |
| **VAD Parameters** | None | threshold=0.5, min_speech=250ms, min_silence=500ms, pad=200ms |
| **Execution Time** | **2.297 s** | **1.656 s** (27.9% speedup) |
| **Transcription Text** | `"Hello, hello, hello, hello."` | `"Hello, hello, hello, hello."` |
| **Detected Language** | `en` (prob: 1.00) | `en` (prob: 1.00) |
| **Segments Count** | 1 segment | 1 segment |
| **Segment Timestamps** | `[0.00s -> 4.76s]` | `[1.53s -> 4.57s]` |
| **Raw Segment Token Text** | `" هلو هلو هلو هلو هلو"` (Silence-induced hallucination) | `" Hello, hello, hello, hello."` (Clean English tokens) |
| **Speech Duration** | 4.76 s (Includes leading silence) | **3.04 s** |
| **Silence Filtered** | 0.24 s (4.8%) | **1.96 s (39.2%)** |

**Benchmark Findings & Observations:**
1. **Silence Elimination:** Test B successfully trimmed 1.53s of initial background dead silence before the speech started, and 0.43s of trailing silence at the end, filtering out 1.96s total (39.2% of the audio clip).
2. **Hallucination Prevention:** In Test A without VAD, the 1.5s leading silence caused Faster-Whisper's decoder to produce Arabic transliterated tokens (`" هلو هلو هلو هلو هلو"`) during initial token search before settling on English output. In Test B with Silero VAD active, Faster-Whisper bypassed the silence completely and produced clean English tokens directly (`" Hello, hello, hello, hello."`).
3. **Inference Latency Reduction:** Because Faster-Whisper's decoder only processed active speech frames (3.04s instead of 5.00s), transcription execution time dropped from 2.297s to 1.656s — a 27.9% improvement in responsiveness.

---

## Test Run: 2026-11-10 — Phase 2 Step 6 Wake Word Detection Verification

- **Scope:** Phase 2 Step 6: Wake Word Detection (`backend/voice/wake_word.py`, `backend/voice/voice_pipeline.py`)
- **Target:** `tests/test_wake_word.py` & Full Test Suite `tests/`
- **Command:**
  ```powershell
  .\.venv\Scripts\pytest.exe tests/ -v
  ```
- **Result:** PASS (73 passed in 6.18s, 0 failed, 0 skipped)
- **Detailed Results (`tests/test_wake_word.py` - 16 passed):**
  - `test_wake_word_default_configuration`: PASSED (Defaults to enabled=False, model='hey_jarvis_v0.1', threshold=0.5, cooldown=2.0s)
  - `test_wake_word_parameter_validation`: PASSED (Rejects threshold <= 0 or >= 1, negative cooldown, sample rate != 16 kHz)
  - `test_missing_custom_model_raises_descriptive_error`: PASSED (Guides user when custom 'hey_voxpilot.onnx' is missing)
  - `test_detector_loads_model_successfully`: PASSED (Initializes openWakeWord ONNX framework cleanly)
  - `test_predict_frame_converts_float32_to_int16`: PASSED (Converts float32 audio to int16 PCM before inference)
  - `test_wake_word_detection_event_above_threshold`: PASSED (Triggers detection event when model score >= threshold)
  - `test_non_wake_audio_below_threshold_does_not_activate`: PASSED (Ignores low score activations below threshold)
  - `test_detection_threshold_boundary`: PASSED (Verifies exact threshold boundary conditions)
  - `test_cooldown_prevents_immediate_duplicate_activation`: PASSED (Enforces cooldown window to avoid repeated triggers)
  - `test_detector_starts_and_stops_cleanly`: PASSED (Clean stop requested termination)
  - `test_microphone_handover_releases_stream_for_audiorecorder`: PASSED (PortAudio stream completely closed before recording starts)
  - `test_voice_pipeline_disabled_wake_word_mode`: PASSED (VoicePipeline backward compatibility when wake word is disabled)
  - `test_voice_pipeline_enabled_wake_word_mode`: PASSED (VoicePipeline delegation to WakeWordDetector when enabled)
  - `test_microphone_error_raises_wake_word_error`: PASSED (Encapsulates sounddevice stream runtime errors as WakeWordError)
  - `test_env_variables_configure_wake_word`: PASSED (VOXPILOT_WAKE_WORD_* overrides applied accurately)
  - `test_detector_reset_clears_state`: PASSED (Internal flags and model prediction buffers reset cleanly)

### Real Hardware & Performance Measurements (ASUS TUF Gaming F16, Intel Core 5 205H)
- **Script:** `scratch/test_wake_word_perf.py`
- **Wake Word Engine:** openWakeWord 0.6.0 on ONNX Runtime (CPU)
- **Acoustic Model:** `hey_jarvis_v0.1.onnx` (with `melspectrogram.onnx` and `embedding_model.onnx`)
- **Audio Frame:** 80 ms (1,280 samples at 16,000 Hz)

| Metric | Measured Value |
|---|---|
| **Process PID** | 14776 |
| **Initial RAM** | 193.88 MB |
| **RAM After Model Load** | 233.17 MB (**+39.29 MB delta**) |
| **Model Load Time** | **161.60 ms** |
| **Average Frame Inference Latency** | **2.75 ms** (min: 1.84 ms, max: 5.56 ms) |
| **Real-Time Factor (RTF)** | **0.0344** (80 ms chunk evaluated in 2.75 ms) |
| **Estimated Single-Core CPU Usage** | **3.44%** |
| **Input Device Tested** | `[1] Microphone Array (Realtek(R) Audio)` |
| **Microphone Stream Handover** | **PASSED** (Idle stream closed cleanly; `AudioRecorder` acquired device immediately without collision) |

---

## Test Run: 2026-11-10 — Phase 3 Step 1 Safe Application Launching Verification

- **Scope:** Phase 3 Step 1: Safe Application Launching (`backend/automation/application_launcher.py`, `backend/tools/applications.py`)
- **Target:** `tests/test_application_launcher.py` & Full Test Suite `tests/`
- **Commands:**
  ```powershell
  # 1. Targeted Unit Tests (20 passed)
  .\.venv\Scripts\pytest.exe tests/test_application_launcher.py -v

  # 2. Opt-in Live Notepad Launch Test (1 passed)
  .\.venv\Scripts\pytest.exe tests/test_application_launcher.py -m live -v

  # 3. Complete Project Regression Test Suite (93 passed, 1 deselected live test)
  .\.venv\Scripts\pytest.exe tests/ -v
  ```
- **Result:** PASS (93 passed, 1 deselected in 15.47s; Live test passed in 0.54s)
- **Detailed Results (`tests/test_application_launcher.py` - 20 unit tests passed):**
  - `test_canonical_application_lookup`: PASSED (Resolves "notepad", "calculator", "chrome", "vscode")
  - `test_alias_resolution`: PASSED (Resolves "text editor", "notes", "calc", "google chrome", "web browser", "vs code", "code", "visual studio code")
  - `test_case_insensitive_matching`: PASSED (Resolves uppercase and mixed case queries)
  - `test_whitespace_and_extension_normalization`: PASSED (Strips whitespace and `.exe` suffixes)
  - `test_unknown_application_rejection`: PASSED (Rejects unapproved apps like "powershell", "cmd", "regedit", "taskmgr")
  - `test_empty_and_invalid_type_query_rejection`: PASSED (Rejects empty strings, whitespace, and non-string inputs)
  - `test_arbitrary_path_and_traversal_rejection`: PASSED (Rejects file paths, UNC paths, and `..\..\` traversals)
  - `test_shell_injection_patterns_rejected`: PASSED (Rejects `&`, `|`, `;`, `>`, `` ` `` shell operators)
  - `test_find_executable_from_candidates`: PASSED (Resolves valid candidate paths on disk)
  - `test_find_executable_registry_fallback`: PASSED (Falls back to Windows Registry App Paths when candidates miss)
  - `test_missing_executable_raises_not_found`: PASSED (Raises `ApplicationNotFoundError` when binary missing from disk)
  - `test_launch_approved_application_mock_popen`: PASSED (Verifies exact `[exe_path]` argument list, `shell=False`, zero argument forwarding, and PID capture)
  - `test_launch_process_spawn_os_error_handling`: PASSED (Wraps `OSError` into `ApplicationLauncherError`)
  - `test_launch_permission_error_handling`: PASSED (Wraps `PermissionError` into `ApplicationLauncherError`)
  - `test_tool_input_pydantic_validation_success`: PASSED (Validates conforming application names)
  - `test_tool_input_pydantic_validation_rejection`: PASSED (Rejects empty names, >100 char length, path separators, flags)
  - `test_launch_application_tool_success_outcome`: PASSED (Returns structured `LaunchApplicationResult` on success)
  - `test_launch_application_tool_not_allowed_outcome`: PASSED (Handles `ApplicationNotAllowedError` with clean status="rejected")
  - `test_launch_application_tool_not_found_outcome`: PASSED (Handles `ApplicationNotFoundError` with clean status="not_found")
  - `test_is_running_matches_mock_process`: PASSED (Checks active process status via `psutil`)

### Live Windows Verification (`test_live_launch_and_cleanup_notepad`)
- **Host Platform:** Windows 11 Home (Intel Core 5 205H)
- **Target Application:** Notepad (`C:\Windows\System32\notepad.exe`)
- **Execution:** Invoked through real `ApplicationLauncher().launch("notepad")`
- **Result:**
  - Spawned distinct PID (verified non-colliding with existing user processes).
  - Verified active running state and process image name (`notepad.exe`) via `psutil`.
  - Cleanly terminated spawned process in `finally` block within 0.54s without affecting pre-existing applications.

---

## Test Run: 2026-11-10 — Phase 3 Step 1 Security Remediation Verification

- **Scope:** Phase 3 Step 1 Security Hardening (SEC-001, SEC-002, SEC-003)
- **Target:** `tests/test_application_launcher.py` & Full Test Suite `tests/`
- **Commands:**
  ```powershell
  # 1. Targeted Unit Tests (32 passed, 1 deselected)
  .\.venv\Scripts\pytest.exe tests/test_application_launcher.py -v

  # 2. Opt-in Live Notepad Launch Test (1 passed, 32 deselected)
  .\.venv\Scripts\pytest.exe tests/test_application_launcher.py -m live -v

  # 3. Complete Project Regression Test Suite (105 passed, 1 deselected)
  .\.venv\Scripts\pytest.exe tests/ -q
  ```
- **Result:** PASS (105 passed, 1 deselected in 6.58s; Live test passed in 0.23s)
- **Security Regression Results (`tests/test_application_launcher.py` - 12 new tests passed):**
  - `test_sec001_path_lookup_is_never_invoked`: PASSED (Confirmed find_executable never calls ambient PATH lookup)
  - `test_sec001_executable_planted_in_path_directory_never_selected`: PASSED (Planted calc.exe in front of PATH ignored)
  - `test_sec002_registry_hklm_checked_before_hkcu`: PASSED (Confirmed HKEY_LOCAL_MACHINE queried prior to HKEY_CURRENT_USER)
  - `test_sec002_registry_hkcu_undergoes_same_strict_validation_as_hklm`: PASSED (Confirmed unapproved HKCU paths rejected identically)
  - `test_sec003_registry_filename_mismatch_rejected`: PASSED (Registry pointing to evil.exe for chrome.exe rejected)
  - `test_sec003_registry_values_with_arguments_rejected`: PASSED (Registry paths with trailing flags or unquoted flags rejected)
  - `test_sec003_registry_values_pointing_outside_approved_directories_rejected`: PASSED (Binaries outside approved_directories rejected)
  - `test_sec003_registry_unresolved_env_vars_rejected`: PASSED (Values with unexpanded %VAR% references rejected)
  - `test_sec003_registry_missing_or_non_string_values_handled_safely`: PASSED (Empty, None, integer registry values rejected)
  - `test_sec003_invalid_registry_results_cannot_bypass_allowlist`: PASSED (Unapproved apps fail allowlist before registry check)
  - `test_sec003_valid_registry_executable_in_approved_location_resolves`: PASSED (Legitimate executable in approved dir resolves)
  - `test_sec003_symlink_redirection_outside_approved_directory_rejected`: PASSED (Symlink target outside approved_directories rejected)

---

## Test Run: 2026-11-10 — Phase 3 Step 2 Window Management Verification

- **Scope:** Phase 3 Step 2 Window Identification and Management
- **Target:** `tests/test_window_manager.py` & Full Test Suite `tests/`
- **Commands:**
  ```powershell
  # 1. Targeted Unit Tests (31 passed, 1 deselected)
  .\.venv\Scripts\pytest.exe tests/test_window_manager.py -v -m "not live"

  # 2. Opt-in Live Notepad Window Management Test (1 passed, 31 deselected)
  .\.venv\Scripts\pytest.exe tests/test_window_manager.py -m live -v

  # 3. Complete Project Regression Test Suite (136 passed, 2 deselected)
  .\.venv\Scripts\pytest.exe tests/ -q

  # 4. Dependency Integrity Check
  .\.venv\Scripts\python.exe -m pip check
  ```
- **Result:** PASS (136 passed, 2 deselected in 7.65s; Live test passed in 2.62s; Pip check clean)
- **Detailed Results (`tests/test_window_manager.py` - 31 unit tests passed):**
  - `test_window_info_immutability`: PASSED (FrozenInstanceError raised on attribute mutation)
  - `test_window_info_to_dict_structure`: PASSED (Complete JSON-serializable dictionary structure)
  - `test_window_manager_init_default_registry`: PASSED (Loads DEFAULT_APPROVED_APPLICATIONS)
  - `test_window_manager_init_custom_registry`: PASSED (Accepts custom ApplicationRegistry)
  - `test_validate_ownership_notepad_success`: PASSED (Notepad binary in System32 validated)
  - `test_validate_ownership_chrome_success`: PASSED (Chrome binary in Program Files validated)
  - `test_validate_ownership_vscode_success`: PASSED (VS Code binary in Program Files validated)
  - `test_validate_ownership_calculator_uwp_success`: PASSED (ApplicationFrameHost.exe with ApplicationFrameWindow class and Calculator title validated)
  - `test_validate_ownership_applicationframehost_rejected_if_not_calculator`: PASSED (Non-calculator apps in ApplicationFrameHost rejected with WindowNotAllowedError)
  - `test_validate_ownership_unapproved_process_rejected`: PASSED (powershell.exe rejected with WindowNotAllowedError)
  - `test_validate_ownership_approved_name_in_unapproved_directory_rejected`: PASSED (notepad.exe in untrusted directory rejected)
  - `test_validate_ownership_invalid_hwnd_rejected`: PASSED (Destroyed/non-existent HWND raises WindowNotFoundError)
  - `test_validate_ownership_pid_zero_rejected`: PASSED (HWND associated with system PID 0 rejected)
  - `test_find_windows_preserves_z_order`: PASSED (Top-to-bottom z-order preserved)
  - `test_find_windows_visible_only_filtering`: PASSED (Hidden windows excluded when visible_only=True)
  - `test_find_windows_title_pattern_filter`: PASSED (Regex/substring title pattern filtering)
  - `test_find_windows_unapproved_app_raises`: PASSED (Unapproved application query raises ApplicationNotAllowedError)
  - `test_is_window_responsive`: PASSED (ctypes user32 IsHungAppWindow non-blocking check)
  - `test_activate_window_success`: PASSED (Restores if minimized and brings to foreground)
  - `test_activate_window_hung_raises_operation_error`: PASSED (Hung window activation raises WindowOperationError)
  - `test_minimize_window_success`: PASSED (Minimizes and verifies iconic state)
  - `test_maximize_window_success`: PASSED (Maximizes and verifies zoomed state)
  - `test_restore_window_success`: PASSED (Restores and verifies normal unmaximized state)
  - `test_operation_on_unapproved_window_rejected_without_modifying_state`: PASSED (WindowNotAllowedError prevents state change)
  - `test_manage_window_ambiguity_raises_window_ambiguity_error`: PASSED (Ambiguous app query raises WindowAmbiguityError with candidate list)
  - `test_manage_window_explicit_hwnd_succeeds_without_ambiguity`: PASSED (Explicit HWND targets single window directly)
  - `test_find_windows_input_sanitization`: PASSED (Path separators and injection characters rejected)
  - `test_manage_window_input_validation`: PASSED (Action validation and HWND/app_name requirement enforced)
  - `test_find_windows_tool_success`: PASSED (Returns structured FindWindowsResult)
  - `test_find_windows_tool_unapproved_app`: PASSED (Returns status="not_allowed")
  - `test_manage_window_tool_ambiguity_handling`: PASSED (Returns status="ambiguous" with matching_windows list)
- **Live Windows Verification (`test_live_notepad_window_management`):**
  - **Host Platform:** Windows 11 Home (Intel Core 5 205H)
  - **Target Application:** Notepad launched via `ApplicationLauncher().launch("notepad")`
  - **Execution:**
    1. Discovered Notepad window via `WindowManager().find_windows(app_name="notepad")`.
    2. Verified metadata (`app_id == "notepad"`, `process_name == "notepad.exe"`).
    3. Minimized window via `wm.minimize_window()` and verified `is_minimized == True`.
    4. Restored window via `wm.restore_window()` and verified `is_minimized == False`.
    5. Cleanly terminated spawned test processes in `finally` block without affecting user environment.

---

## Test Run: 2026-11-10 — Phase 3 Step 2 Security Remediation Verification

- **Scope:** Phase 3 Step 2 Security Hardening (SEC-004 through SEC-008)
- **Target:** `tests/test_window_manager.py` & Full Test Suite `tests/`
- **Commands:**
  ```powershell
  # 1. Targeted Unit Tests (46 passed, 1 deselected)
  .\.venv\Scripts\pytest.exe tests/test_window_manager.py -v -m "not live"

  # 2. Opt-in Live Notepad Window Management Test (1 passed, 46 deselected)
  .\.venv\Scripts\pytest.exe tests/test_window_manager.py -m live -v

  # 3. Complete Project Regression Test Suite (151 passed, 2 deselected)
  .\.venv\Scripts\pytest.exe tests/ -q

  # 4. Dependency Integrity Check
  .\.venv\Scripts\python.exe -m pip check
  ```
- **Result:** PASS (151 passed, 2 deselected in 9.37s; Live test passed in 2.60s; Pip check clean)
- **Security Regression Results (`tests/test_window_manager.py` - 15 new tests passed):**
  - `test_sec004_photos_with_calc_title_rejected`: PASSED (Photos window titled 'calc_notes.jpg' hosted under ApplicationFrameHost rejected)
  - `test_sec004_unrelated_packaged_app_rejected`: PASSED (Unapproved UWP app hosted in ApplicationFrameHost rejected)
  - `test_sec004_missing_or_inaccessible_child_process_rejected`: PASSED (Inaccessible/missing child process rejected fail-closed)
  - `test_sec004_calculator_outside_approved_directory_rejected`: PASSED (CalculatorApp.exe outside approved directory rejected)
  - `test_sec004_untrusted_package_family_rejected`: PASSED (CalculatorApp binary with untrusted package family rejected)
  - `test_sec004_ambiguous_child_windows_rejected`: PASSED (Conflicting multiple child processes under ApplicationFrameHost rejected)
  - `test_sec005_activate_window_refusal_returns_false`: PASSED (Activation refusal by Windows returns False)
  - `test_sec005_activate_window_api_exception_returns_false`: PASSED (OS exception during SetForegroundWindow returns False)
  - `test_sec005_no_attach_thread_input_in_activate_window`: PASSED (Verified AttachThreadInput and BringWindowToTop completely absent)
  - `test_sec006_manage_window_activation_refusal_raises_operation_error`: PASSED (manage_window raises WindowOperationError upon refusal)
  - `test_sec006_manage_window_tool_reports_activation_restricted`: PASSED (Tool reports success=False, status='activation_restricted')
  - `test_sec006_minimize_maximize_restore_unaffected`: PASSED (Minimize, maximize, and restore remain fully functional)
  - `test_sec007_hwnd_and_app_name_mismatch_rejected`: PASSED (Chrome HWND with app_name='notepad' rejected)
  - `test_sec007_hwnd_and_app_name_matching_accepted`: PASSED (Matching HWND and app_name accepted)
  - `test_sec007_invalid_app_name_with_hwnd_rejected`: PASSED (Invalid app_name with HWND raises ApplicationNotAllowedError)
  - `test_sec007_unapproved_hwnd_with_app_name_rejected`: PASSED (Unapproved HWND with valid app_name raises WindowNotAllowedError)

---

## Test Run: 2026-11-10 — Phase 3 Step 3.2 Final Calculator Identity Remediation

- **Scope:** SEC-004 Exact Package Family Verification & Fail-Closed Hardening
- **Target:** `tests/test_window_manager.py` & Full Test Suite `tests/`
- **Commands & Outputs:**
  1. **Targeted Window Manager Unit Tests:**
     ```powershell
     .\.venv\Scripts\pytest.exe tests/test_window_manager.py -v
     ```
     *Output:* `53 passed, 1 deselected in 0.70s`
  2. **Full Project Test Suite:**
     ```powershell
     .\.venv\Scripts\pytest.exe -v
     ```
     *Output:* `158 passed, 2 deselected in 10.12s`
  3. **Live Windows Verification Tests:**
     ```powershell
     .\.venv\Scripts\pytest.exe -m live -v
     ```
     *Output:* `2 passed, 158 deselected in 7.36s` (Notepad launch test + Notepad window management test)
  4. **Dependency Integrity Check:**
     ```powershell
     .\.venv\Scripts\python.exe -m pip check
     ```
     *Output:* `No broken requirements found.`
- **New SEC-004 Regression Tests Passed (`tests/test_window_manager.py`):**
  - `test_sec004_exact_package_family_accepted`: PASSED (Genuine Calculator with exact `Microsoft.WindowsCalculator_8wekyb3d8bbwe` accepted)
  - `test_sec004_prefix_match_with_different_publisher_id_rejected`: PASSED (Package matching prefix `Microsoft.WindowsCalculator_` but with wrong publisher ID rejected)
  - `test_sec004_different_package_family_rejected`: PASSED (Different package family like Alarms & Clock rejected even if named CalculatorApp.exe)
  - `test_sec004_none_package_identity_rejected`: PASSED (None package identity causes fail-closed rejection without fallback)
  - `test_sec004_empty_package_identity_rejected`: PASSED (Empty string package identity causes fail-closed rejection)
  - `test_sec004_package_retrieval_exception_rejected`: PASSED (Win32 RPC exception during package query causes safe fail-closed rejection)
  - `test_sec004_no_fallback_to_filename_or_directory_when_package_unavailable`: PASSED (Process named CalculatorApp.exe in WindowsApps directory is NEVER accepted if package identity is unavailable)

---

## Test Run: 2026-11-10 — Phase 3 Step 3 Keyboard and Mouse Automation Verification

- **Scope:** Keyboard Automation (`KeyboardController`), Mouse Automation (`MouseController`), Pydantic Tools (`backend/tools/keyboard.py`, `backend/tools/mouse.py`), Full Test Suite & Live Verification
- **Targets:** `tests/test_keyboard_controller.py`, `tests/test_mouse_controller.py`, Full `tests/` Suite
- **Commands & Outputs:**
  1. **Keyboard Controller Unit Tests:**
     ```powershell
     .\.venv\Scripts\pytest.exe tests/test_keyboard_controller.py -v
     ```
     *Output:* `71 passed in 0.87s`
  2. **Mouse Controller Unit Tests:**
     ```powershell
     .\.venv\Scripts\pytest.exe tests/test_mouse_controller.py -v
     ```
     *Output:* `34 passed in 0.54s`
  3. **Full Project Test Suite:**
     ```powershell
     .\.venv\Scripts\pytest.exe -v
     ```
     *Output:* `263 passed, 2 deselected in 9.46s`
  4. **Live Windows Verification Tests:**
     ```powershell
     .\.venv\Scripts\pytest.exe -m live -v
     ```
     *Output:* `2 passed, 263 deselected in 8.81s` (Notepad launch test + Notepad window management test)
  5. **Dependency Integrity Check:**
     ```powershell
     .\.venv\Scripts\python.exe -m pip check
     ```
     *Output:* `No broken requirements found.`
- **Test Categories Breakdown:**
  - `tests/test_keyboard_controller.py` (71 passed):
    - Initializations & Custom Registry (2 tests)
    - Key & Shortcut Normalization & Lookups (7 tests)
    - Approved Individual Keys (14 tests: enter, tab, space, backspace, delete, up, down, left, right, home, end, pageup, pagedown, escape)
    - Approved Shortcuts (16 tests: ctrl+c, ctrl+v, ctrl+x, ctrl+a, ctrl+z, ctrl+y, ctrl+s, ctrl+f, ctrl+left, ctrl+right, ctrl+home, ctrl+end, shift+left, shift+right, shift+up, shift+down)
    - Prohibited Shortcuts (8 tests: alt+f4, ctrl+w, win+r, win+x, win+d, win+m, ctrl+alt+del, ctrl+shift+esc)
    - Invalid Keys & Shortcuts (4 tests)
    - Text Typing & Validation (6 tests: basic text, empty text, max length 500 rejection, interval bounds, surrogate pairs, SendInput error)
    - Chunking & Inter-chunk Focus Lost (2 tests)
    - Pre-Action Target Window Validation (4 tests: unapproved app, hung window, minimized window, activation failure)
    - Modifier Release in Finally Block (1 test)
    - UIPI Access Denied Detection (1 test)
    - Keyboard Tool Wrappers & Schemas (7 tests: schemas, tools, sensitive content masking)
  - `tests/test_mouse_controller.py` (34 passed):
    - Initializations & Virtual Screen Bounds (3 tests: default registry, multi-monitor bounds, negative coordinate handling)
    - Out-of-Bounds Rejection (2 tests: positive & negative out-of-bounds)
    - Move Mouse (2 tests: absolute & target window validation)
    - Click Mouse (8 tests: left, right, middle, double click, clicks validation, invalid button, window relative, outside window)
    - Scroll Mouse (4 tests: up, down, bound limit -2000 to +2000 rejection, zero scroll)
    - Pre-Action Window State Validation (4 tests: unapproved app, hung window, minimized window, activation restricted)
    - UIPI Access Denied Detection (1 test)
    - Mouse Tool Wrappers & Schemas (10 tests: schemas, validation, execution)

---

## Test Run: 2026-11-10 — Phase 3 Step 4 File Management & Screenshot Verification

- **Scope:** File Management Controller (`FileManager`), Screenshot Controller (`ScreenshotController`), Pydantic Tools (`backend/tools/files.py`, `backend/tools/screenshots.py`), Full Test Suite & Live Verification
- **Targets:** `tests/test_file_manager.py`, `tests/test_screenshot_controller.py`, Full `tests/` Suite
- **Commands & Outputs:**
  1. **File Manager Unit Tests:**
     ```powershell
     .\.venv\Scripts\pytest.exe tests/test_file_manager.py -v
     ```
     *Output:* `44 passed in 0.56s`
  2. **Screenshot Controller Unit Tests:**
     ```powershell
     .\.venv\Scripts\pytest.exe tests/test_screenshot_controller.py -v
     ```
     *Output:* `19 passed in 0.48s`
  3. **Full Project Test Suite:**
     ```powershell
     .\.venv\Scripts\pytest.exe -v
     ```
     *Output:* `326 passed, 2 deselected in 12.44s`
  4. **Live Windows Verification Tests:**
     ```powershell
     .\.venv\Scripts\pytest.exe -m live -v
     ```
     *Output:* `2 passed, 326 deselected in 8.43s` (Notepad launch test + Notepad window management test)
  5. **Dependency Integrity Check:**
     ```powershell
     .\.venv\Scripts\python.exe -m pip check
     ```
     *Output:* `No broken requirements found.`
  6. **Syntax & Compilation Verification:**
     ```powershell
     .\.venv\Scripts\python.exe -m py_compile backend/automation/file_manager.py backend/tools/files.py backend/automation/screenshot_controller.py backend/tools/screenshots.py backend/automation/__init__.py backend/tools/__init__.py tests/test_file_manager.py tests/test_screenshot_controller.py
     ```
     *Output:* Clean compilation, return code 0.
- **New Test Categories Breakdown:**
  - `tests/test_file_manager.py` (44 passed):
    - Initialization & Canonical Directory Properties (1 test)
    - Allowed Path Validation & Containment (1 test)
    - Missing Path Validation (1 test)
    - Unapproved Outside Directory Rejection (1 test)
    - Directory Traversal (`../`) Escape Rejection (1 test)
    - UNC Network and Windows Device Path Rejection (1 test)
    - NTFS Alternate Data Stream Rejection (1 test)
    - Null Byte Injection Rejection (1 test)
    - VoxPilot Project Root Protection (1 test)
    - User Home Profile Root Rejection (1 test)
    - Directory Listing (empty, populated, hidden file filter) (3 tests)
    - File Search (by name, by extension, depth & result count limits) (3 tests)
    - File Metadata Retrieval (1 test)
    - Folder Creation (success, already exists error) (2 tests)
    - File Creation (success, unconfirmed overwrite rejection, confirmed overwrite) (3 tests)
    - File Renaming (success, path separators/traversal rejection) (2 tests)
    - Folder Renaming (success, approved root rename rejection) (2 tests)
    - File Copying (success, overwrite confirmation protection) (2 tests)
    - Folder Copying (success, copy into self rejection) (2 tests)
    - File Moving (success) (1 test)
    - Folder Moving (success) (1 test)
    - File Deletion (unconfirmed rejection, confirmed execution) (2 tests)
    - Folder Deletion (unconfirmed rejection, non-empty without recursive rejection, confirmed recursive success, approved root deletion rejection) (4 tests)
    - File Tools Layer Tests (list, search, delete file preview, delete file execution, delete folder preview, path not allowed status) (6 tests)
  - `tests/test_screenshot_controller.py` (19 passed):
    - Controller Initialization (1 test)
    - Filename Path Traversal Rejection (1 test)
    - Filename Extension Sanitization (1 test)
    - Safe Timestamped Filename Generation (1 test)
    - Overwrite Protection (unconfirmed rejection, confirmed execution) (2 tests)
    - Full Desktop Capture (all monitors) (1 test)
    - Primary Monitor Capture (1 test)
    - Monitor Selection Capture (valid monitor index, invalid index rejection) (2 tests)
    - Window Capture (valid window, non-positive HWND rejection, unapproved window rejection, minimized window rejection, hung window rejection) (5 tests)
    - Screenshot Tools Layer Tests (desktop tool, window tool, overwrite preview, list monitors tool) (4 tests)

---

## Test Run: 2026-11-10 — Phase 3 Final Security Remediation and Trust Boundary Approval

- **Scope:** Confirmation Trust Boundary (`ConfirmationManager`), Browser File Downloads, SSRF & DNS Pre-Resolution, Consequential Actions, and Live Controlled Browser Testing
- **Targets:** `tests/test_confirmation_manager.py`, `tests/test_browser_controller.py`, `tests/test_file_manager.py`, Full `tests/` Suite & Live Tests
- **Commands & Outputs:**
  1. **Confirmation Manager Unit Tests:**
     ```powershell
     .\.venv\Scripts\pytest.exe tests/test_confirmation_manager.py -v
     ```
     *Output:* `10 passed in 0.71s`
     - Token generation with 256-bit entropy and PENDING state
     - Valid user approval consumed once
     - Missing user approval rejected (`MissingUserApprovalError`)
     - Explicit user rejection rejected (`ConfirmationRejectedError`)
     - Expired approval rejected (`ConfirmationExpiredError`)
     - Replay prevention: consumed token reuse rejected (`ReplayedConfirmationError`)
     - Action mismatch: token for action A used for action B rejected (`ActionMismatchConfirmationError`)
     - Action parameter tamper detection: modified params rejected (`TamperedActionConfirmationError`)
     - Non-existent token rejected (`InvalidConfirmationError`)
     - Singleton lifecycle and reset verification
  2. **Browser Controller Unit Tests:**
     ```powershell
     .\.venv\Scripts\pytest.exe tests/test_browser_controller.py -v
     ```
     *Output:* `42 passed, 1 deselected in 1.20s`
     - URL validation & non-standard IP representations (octal dotted `0177.0.0.1`, decimal integer `2130706433`, hex `0x7f000001`, IPv4-mapped IPv6)
     - Active DNS pre-resolution blocking domains resolving to loopback and cloud metadata (`169.254.169.254`)
     - Safe browser file downloads within approved folder (`~/Downloads/VoxPilot_Downloads`)
     - Path traversal blocking in download filenames (`../`)
     - Dangerous executable/script extensions blocked (`.exe`, `.bat`, `.cmd`, `.ps1`, `.vbs`, `.msi`, `.dll`)
     - Overwrite confirmation with cryptographic tokens
     - Consequential browser click with approved confirmation token
     - LLM self-confirmation prevention (passing `confirmed=True` without approved token blocked)
  3. **File Manager Unit Tests:**
     ```powershell
     .\.venv\Scripts\pytest.exe tests/test_file_manager.py -v
     ```
     *Output:* `46 passed in 0.95s`
     - Approved token consumption for file deletion
     - LLM `confirmed=True` without approved token blocked
     - Approved token consumption for folder deletion
  4. **Full Project Test Suite:**
     ```powershell
     .\.venv\Scripts\pytest.exe -v
     ```
     *Output:* `380 passed, 3 deselected in 9.02s`
  5. **Live Controlled Automation Tests:**
     ```powershell
     .\.venv\Scripts\pytest.exe -m live -v
     ```
     *Output:* `3 passed, 380 deselected in 10.89s`
     - `test_live_launch_and_cleanup_notepad`: PASSED
     - `test_live_browser_automation`: PASSED (Controlled Chromium headless startup, local HTTP server navigation, text extraction, form typing, element clicking, screenshot capture, and clean shutdown)
     - `test_live_notepad_window_management`: PASSED
  6. **Dependency Integrity Check:**
     ```powershell
     .\.venv\Scripts\python.exe -m pip check
     ```
     *Output:* `No broken requirements found.`
  7. **Syntax & Compilation Verification:**
     ```powershell
     .\.venv\Scripts\python.exe -m py_compile backend/automation/confirmation_manager.py backend/automation/browser_controller.py backend/tools/browser.py backend/tools/files.py backend/automation/__init__.py backend/tools/__init__.py
     ```
     *Output:* Clean compilation, return code 0.

- **Scope:** Safe Browser Automation (`BrowserController`), Pydantic Tools (`backend/tools/browser.py`), Full Repository Regression Suite & Live Tests
- **Targets:** `tests/test_browser_controller.py`, Full `tests/` Suite
- **Commands & Outputs:**
  1. **Browser Controller Unit Tests:**
     ```powershell
     .\.venv\Scripts\pytest.exe tests/test_browser_controller.py -v
     ```
     *Output:* `31 passed in 0.79s`
  2. **Full Project Test Suite:**
     ```powershell
     .\.venv\Scripts\pytest.exe -v
     ```
     *Output:* `357 passed, 2 deselected in 9.01s`
  3. **Live Windows Verification Tests:**
     ```powershell
     .\.venv\Scripts\pytest.exe -m live -v
     ```
     *Output:* `2 passed, 357 deselected in 6.84s` (Notepad launch test + Notepad window management test)
  4. **Dependency Integrity Check:**
     ```powershell
     .\.venv\Scripts\python.exe -m pip check
     ```
     *Output:* `No broken requirements found.`
  5. **Syntax & Compilation Verification:**
     ```powershell
     .\.venv\Scripts\python.exe -m py_compile backend/automation/browser_controller.py backend/tools/browser.py backend/automation/__init__.py backend/tools/__init__.py tests/test_browser_controller.py
     ```
     *Output:* Clean compilation, return code 0.
- **New Test Categories Breakdown:**
  - `tests/test_browser_controller.py` (31 passed):
    - URL Scheme Validation (valid HTTPS accepted, prohibited schemes rejected: file, javascript, data, chrome, about, ws, ftp) (2 tests)
    - SSRF & Localhost Blocking (localhost, 127.0.0.1, [::1], 10.x, 172.16.x, 192.168.x, 169.254.x cloud metadata) (2 tests)
    - Embedded Credentials Rejection (user:pass@host blocked) (1 test)
    - Approved Domain Policy Enforcement (approved domain accepted, unapproved domain blocked) (1 test)
    - Browser Session Lifecycle (isolated launch args, duplicate session error, missing session error, cleanup) (4 tests)
    - Navigation & Post-Redirect Verification (valid navigation, redirect to unapproved domain evacuated to about:blank, navigation timeout) (3 tests)
    - Page Metadata Retrieval (URL, title, is_secure) (1 test)
    - Page Text Extraction (bounded text extraction, length capping at max_length) (1 test)
    - Safe Element Interaction (click element, element not found, enter text with clear_first, select dropdown option, scroll page directions up/down, invalid scroll direction error, navigate history back/forward/refresh) (7 tests)
    - Browser Screenshot Capture (save to approved directory, overwrite confirmation guard) (2 tests)
    - Consequential Action Confirmation Guard (unconfirmed blocked, confirmed allowed) (1 test)
    - Browser Tools Layer Tests (start tool, navigate tool, blocked unsafe URL, consequential confirmation preview, get text tool, close tool) (6 tests)












---

## Test Run: 2026-11-10 — Phase 4 Step 1: LangGraph Agent Execution and Tool Integration

- **Scope:** Phase 4 Step 1 (LangGraph Agent Execution, Tool Registry, Tool Executor, Safe System Metrics Tool, and FastAPI Agent API)
- **Target Files:**
  - `backend/agent/tool_registry.py`
  - `backend/agent/tool_executor.py`
  - `backend/tools/system.py`
  - `backend/agent/state.py`
  - `backend/agent/graph.py`
  - `backend/api/agent.py`
  - `backend/main.py`
  - `backend/voice/voice_pipeline.py`
  - `tests/test_tool_registry.py`
  - `tests/test_agent_execution.py`
  - `tests/test_agent_api.py`

### 1. Tool Registry Test Suite
- **Command:**
  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/test_tool_registry.py -v
  ```
- **Result:** PASS (10 passed in 0.31s)
- **Coverage:**
  - `test_tool_registry_has_built_in_tools`: Registered tools across applications, windows, keyboard, mouse, filesystem, screenshots, browser, and system.
  - `test_tool_aliases`: Alias resolution (`open_application`, `activate_window`, `press_keys`, `click`, `take_screenshot`, `search_web`, `browse`).
  - `test_unknown_tool_rejection`: Unknown action names raise `UnknownToolError`.
  - `test_parameter_validation_success`: Pydantic input schemas validate correct arguments.
  - `test_parameter_validation_missing_required`: Missing required parameters raise `ToolArgumentValidationError`.
  - `test_parameter_validation_rejects_unknown_fields`: Extra injected arguments strictly rejected (`extra='forbid'`).
  - `test_parameter_validation_type_mismatch`: Invalid argument types rejected.
  - `test_confirmation_requirement_detection`: Backend confirmation policy detection for destructive file operations, overwrites, and consequential clicks.
  - `test_custom_tool_registration`: Registration of custom `ToolDefinition` and overwrite flag enforcement.
  - `test_shared_registry_singleton`: Stable singleton factory.

### 2. Agent Execution Workflow Test Suite
- **Command:**
  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/test_agent_execution.py -v
  ```
- **Result:** PASS (15 passed in 2.10s)
- **Coverage:**
  - `test_plan_validation_empty_plan`: Empty plans for automation intents fail validation (`validation_failed`).
  - `test_plan_validation_general_conversation_empty_plan`: Conversational intent without tool steps completes gracefully.
  - `test_plan_validation_exceeds_max_steps`: Plans exceeding 10 steps halted by safety guard.
  - `test_plan_validation_unknown_action`: Unregistered action names fail validation.
  - `test_plan_validation_invalid_parameters`: Schema validation failures stop plan.
  - `test_plan_validation_unknown_parameters`: Injected parameters fail validation.
  - `test_plan_validation_malformed_step_structure`: Non-dict steps fail validation.
  - `test_successful_single_tool_execution`: Execution of safe single-step tool (`get_system_info`).
  - `test_successful_multi_step_execution_loop`: Sequential looping across multi-step plans.
  - `test_execution_failure_stops_workflow`: Step failure immediately halts workflow (no false successes).
  - `test_destructive_tool_pauses_for_confirmation`: Destructive operations pause with `confirmation_required` and generate cryptographic token.
  - `test_llm_confirmed_flag_cannot_bypass_confirmation_boundary`: Adversarial LLM `confirmed=True` cannot bypass confirmation.
  - `test_resume_execution_after_approval`: User approval via `ConfirmationManager` allows `resume_execution` to proceed.
  - `test_resume_execution_invalid_state_guard`: Resuming unpaused state raises `ValueError`.
  - `test_empty_user_input_guard`: Empty/whitespace input raises `ValueError`.

### 3. Agent FastAPI API Test Suite
- **Command:**
  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/test_agent_api.py -v
  ```
- **Result:** PASS (11 passed in 2.33s)
- **Coverage:**
  - `test_root_endpoint`: `GET /` service metadata and online status.
  - `test_health_check_endpoint`: `GET /health` healthy status.
  - `test_execute_empty_input_rejected`: `POST /agent/execute` rejects empty/whitespace input with 400.
  - `test_execute_successful_request`: `POST /agent/execute` processes request and persists state.
  - `test_execute_custom_execution_id`: Custom `execution_id` honored and persisted.
  - `test_get_status_not_found`: `GET /agent/status/{id}` returns 404 for unknown session.
  - `test_confirmation_lifecycle_approve`: Consequential action pauses -> inspect confirmation -> approve -> completed.
  - `test_confirmation_lifecycle_reject`: Consequential action pauses -> reject confirmation -> cancelled.
  - `test_confirmation_token_mismatch_rejected`: Incorrect confirmation token rejected with 403.
  - `test_confirmation_approve_not_awaiting`: Approving unpaused execution returns 400.
  - `test_list_tools_endpoint`: `GET /agent/tools` returns registered tools with metadata.

### 4. Full Regression & Live Verification
- **Full Unit Test Suite:**
  ```powershell
  .\.venv\Scripts\python.exe -m pytest -v -m "not live"
  ```
  *Output:* `416 passed, 3 deselected in 13.08s`
- **Live Desktop & Browser Verification:**
  ```powershell
  .\.venv\Scripts\python.exe -m pytest -v -m "live"
  ```
  *Output:* `3 passed, 416 deselected in 11.41s` (Notepad launch, Notepad window management, Playwright browser)
- **Total:** `419 passed, 0 failed` across entire repository.
- **Dependency Integrity:** `python -m pip check` -> `No broken requirements found.`

---

## Test Run: 2026-11-10 — Phase 4 Step 2: Persistent Memory, Contextual Reasoning, and Dynamic Re-Planning

- **Scope:** Phase 4 Step 2 Subsystems (`backend/database`, `backend/memory`, `backend/agent`, `backend/api`, `backend/voice`)
- **Target Files:**
  - `tests/test_database.py`
  - `tests/test_conversation_memory.py`
  - `tests/test_dynamic_replanning.py`
  - `tests/test_agent_api.py`

### 1. SQLite Database Subsystem Unit Tests
- **Command:**
  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/test_database.py -v
  ```
- **Result:** PASS (10 passed in 1.93s)
- **Coverage:**
  - `test_sanitize_dict_strips_sensitive_keys`: Redacts/strips passwords, tokens, API keys, and sensitive auth metadata.
  - `test_serialize_sanitized_json_encodes_cleanly`: Serializes JSON without persisting raw secrets.
  - `test_init_db_async_and_sync`: Async and sync schema initialization idempotent and creates tables.
  - `test_create_or_get_conversation`: Conversation record creation, idempotency, and retrieval.
  - `test_list_conversations`: Listing conversations sorted chronologically by update timestamp.
  - `test_save_execution_and_retrieval_async`: Asynchronous execution persistence and query.
  - `test_save_execution_sync`: Synchronous execution persistence for background/sync callers.
  - `test_record_and_get_execution_steps`: Execution step recording, sanitization, and step order query.
  - `test_nonexistent_ids_return_none_safely`: Safe `None` return on missing conversation/execution IDs.
  - `test_persistence_across_separate_sessions`: Database records persist to disk across separate engine connections.

### 2. Conversational Memory Subsystem Tests
- **Command:**
  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/test_conversation_memory.py -v
  ```
- **Result:** PASS (9 passed in 2.66s)
- **Coverage:**
  - `test_context_window_bounds_to_max_turns`: Context sliding window prunes oldest turns beyond `max_turns=5`.
  - `test_resolve_pronoun_to_active_window`: Resolves "it" and "the window" to tracked `active_window` HWND and title.
  - `test_resolve_ordinal_file_reference`: Resolves "open the first file" and "open the second file" to indexed results.
  - `test_ambiguity_detection_when_multiple_candidates_exist`: Flags ambiguity and requests clarification without guessing.
  - `test_conversation_isolation`: Separate conversations maintain strict isolation with zero crosstalk.
  - `test_reset_conversation`: Resetting conversation clears turns and entity state.
  - `test_target_revalidation_fails_safely_when_window_no_longer_exists`: Verifies target window exists before binding.
  - `test_agent_multiturn_context_propagation`: Full multi-turn execution propagates conversation context into planner.
  - `test_confirmation_token_cannot_be_reused_in_subsequent_turn`: Single-use tokens cannot be replayed across turns.

### 3. Dynamic Re-Planning Subsystem Tests
- **Command:**
  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/test_dynamic_replanning.py -v
  ```
- **Result:** PASS (8 passed in 2.83s)
- **Coverage:**
  - `test_single_search_result_binds_path_to_subsequent_step`: Single file match dynamically binds path to remaining operations.
  - `test_zero_search_results_aborts_subsequent_steps`: Zero matches replaces steps with safe user notification.
  - `test_multiple_search_results_requests_clarification`: Multiple matches requests clarification without guessing.
  - `test_application_launch_binds_hwnd_to_subsequent_interaction`: Application launch binds runtime HWND to typing steps.
  - `test_intermediate_failure_stops_dependent_consequential_actions`: Intermediate step failure aborts dependent actions.
  - `test_invalid_revised_plan_rejected_by_validation_node`: Invalid revised plan rejected by `validate_plan` node.
  - `test_max_replans_bound_enforced`: Hard limit `MAX_REPLANS = 2` enforced against infinite replanning loops.
  - `test_completed_steps_preserved_and_never_reexecuted`: Completed consequential actions never re-executed upon replanning.

### 4. Extended FastAPI Agent API Tests
- **Command:**
  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/test_agent_api.py -v
  ```
- **Result:** PASS (13 passed in 3.72s)
- **Coverage:**
  - `test_get_status_database_fallback`: SQLite database fallback when execution evicted from in-memory store.
  - `test_conversation_management_endpoints`: `GET /agent/conversations`, `GET /agent/conversations/{id}`, and `GET /agent/conversations/{id}/executions`.

### 5. Full Repository Regression & Live Test Verification
- **Full Unit & Integration Suite:**
  ```powershell
  .\.venv\Scripts\python.exe -m pytest -v -m "not live"
  ```
  *Output:* `445 passed, 3 deselected in 14.81s` (0 failed, 0 errors).
- **Live Desktop & Browser Verification:**
  ```powershell
  .\.venv\Scripts\python.exe -m pytest -v -m "live"
  ```
  *Output:* `3 passed, 445 deselected in 11.45s` (Notepad launch, Notepad window management, Playwright browser).
- **Total Combined Tests:** `448 passed, 0 failed`.
---

## Test Run: 2026-11-10 — Phase 4 Step 3 Final Integration Audit, Confirmation Security, and Database Concurrency

- **Scope:** Complete Phase 4 End-to-End Verification, Confirmation Security Trust Boundary, SQLite Pragmas & Concurrency, Dynamic Re-Planning, Conversational Memory, and REST API
- **Target Files:**
  - `tests/test_confirmation_security.py`
  - `tests/test_database_concurrency.py`
  - `tests/test_agent_integration.py`
  - `tests/test_dynamic_replanning.py`
  - `tests/test_agent_api.py`

### 1. Action Confirmation & Trust Boundary Security Tests
- **Command:**
  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/test_confirmation_security.py -v
  ```
- **Result:** PASS (14 passed in 7.83s)
- **Coverage:**
  - `test_confirmation_operations_not_in_tool_registry`: Approval/rejection operations strictly excluded from `ToolRegistry` (LLM cannot call or self-approve).
  - `test_llm_cannot_bypass_confirmation_with_confirmed_flag`: LLM-supplied `confirmed=True` cannot bypass confirmation.
  - `test_llm_cannot_bypass_confirmation_with_fake_token`: Fabricated tokens rejected with confirmation_failed status.
  - `test_unapproved_token_cannot_be_consumed`: Pending unapproved token cannot be consumed (`MissingUserApprovalError`).
  - `test_parameter_tampering_detected_and_rejected`: Altering approved parameters detected via SHA-256 hash mismatch (`TamperedActionConfirmationError`).
  - `test_action_type_mismatch_detected_and_rejected`: Action mismatch rejected (`ActionMismatchConfirmationError`).
  - `test_single_use_token_replay_rejected`: Consumed token replay raises `ReplayedConfirmationError`.
  - `test_expired_token_rejected`: Token expiration strictly enforced (`ConfirmationExpiredError`).
  - `test_rejected_token_cannot_be_approved_or_consumed`: Token rejected by user cannot be approved or consumed.
  - `test_cross_execution_token_mismatch_rejected_http_403`: Cross-execution approval attempt returns HTTP 403 Forbidden.
  - `test_csrf_cross_origin_approval_blocked_http_403`: Cross-origin browser Origin header (`http://evil.com`) blocked with HTTP 403 Forbidden. Localhost origins permitted.
  - `test_confirmation_tokens_stripped_from_storage_and_models`: Sensitive credentials stripped before database serialization.
  - `test_voice_pipeline_never_speaks_confirmation_tokens`: Voice pipeline speech output never speaks or exposes raw tokens.
  - `test_localhost_server_binding_enforced`: `backend/main.py` entrypoint enforces `127.0.0.1` binding.

### 2. SQLite Database Consistency and Concurrency Tests
- **Command:**
  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/test_database_concurrency.py -v
  ```
- **Result:** PASS (8 passed in 2.10s)
- **Coverage:**
  - `test_sqlite_pragmas_enabled_on_async_engine`: Async engine enforces `PRAGMA foreign_keys = 1` and `PRAGMA journal_mode = WAL`.
  - `test_sqlite_pragmas_enabled_on_sync_engine`: Sync engine enforces `PRAGMA foreign_keys = 1` and `PRAGMA journal_mode = WAL`.
  - `test_foreign_key_violation_raises_integrity_error_async`: Orphan execution insertion fails foreign key constraint (`IntegrityError`).
  - `test_foreign_key_violation_raises_integrity_error_sync`: Orphan execution step insertion fails foreign key constraint (`IntegrityError`).
  - `test_concurrent_async_read_write_operations`: 15 concurrent async read/write operations execute without locking or corruption.
  - `test_transaction_rollback_on_failure`: Partial modifications cleanly rolled back on mid-transaction error.
  - `test_dual_engine_sync_async_parity`: Synchronous repository writes immediately queryable by asynchronous repository.
  - `test_persistence_across_connection_reopening`: Closing and reopening engine retains all committed conversations and executions.

### 3. End-to-End Agent Integration Workflows
- **Command:**
  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/test_agent_integration.py -v
  ```
- **Result:** PASS (13 passed in 4.43s)
- **Coverage (All 13 Workflows):**
  - `test_workflow_01_general_conversation`: General conversation responds without executing automation tools.
  - `test_workflow_02_application_launch`: Application launch executes `launch_application` and captures active window HWND.
  - `test_workflow_03_system_info_query`: System metrics query executes `get_system_info` tool and returns hardware stats.
  - `test_workflow_04_file_search_discovery`: File search executes `search_files` and tracks discovered paths.
  - `test_workflow_05_ambiguous_file_clarification`: Multiple file candidates + ambiguous 'open it' flags ambiguity and asks clarification.
  - `test_workflow_06_multiturn_window_reference_resolution`: Turn 2 'minimize it' binds active window from Turn 1.
  - `test_workflow_07_conversation_isolation`: Conversation B does not inherit Conversation A's window context and asks for clarification.
  - `test_workflow_08_unsupported_action_rejected`: Unregistered tool action rejected at plan validation node.
  - `test_workflow_09_invalid_parameters_halt_execution`: Missing required parameters halts execution at validation node.
  - `test_workflow_10_intermediate_step_failure_halts_execution`: Step 1 failure halts workflow and prevents Step 2 from executing.
  - `test_workflow_11_consequential_action_pauses_for_confirmation`: Consequential action pauses in `confirmation_required` with token.
  - `test_workflow_12_resume_execution_after_approval`: Token approval via REST API resumes execution and completes the step.
  - `test_workflow_13_session_persistence_across_reopening`: Multi-turn conversation logs persist across SQLite engine restarts.

### 4. Full Repository Regression & Live Test Verification
- **Full Unit & Integration Suite:**
  ```powershell
  .\.venv\Scripts\python.exe -m pytest -v -m "not live"
  ```
  *Output:* `480 passed, 3 deselected in 27.74s` (0 failed, 0 errors).
- **Live Desktop & Browser Verification:**
  ```powershell
  .\.venv\Scripts\python.exe -m pytest -v -m "live"
  ```
  *Output:* `3 passed, 480 deselected in 16.71s` (Notepad launch, Notepad window management, Playwright browser).
- **Total Combined Tests:** `483 passed, 0 failed`.
- **Dependency Integrity:**
  ```powershell
  .\.venv\Scripts\python.exe -m pip check
  ```
  *Output:* `No broken requirements found.`
- **Python Syntax Compilation:**
  ```powershell
  .\.venv\Scripts\python.exe -m py_compile backend/agent/graph.py backend/api/agent.py backend/database/connection.py backend/main.py backend/memory/context_manager.py tests/test_dynamic_replanning.py tests/test_confirmation_security.py tests/test_database_concurrency.py tests/test_agent_integration.py
  ```
  *Output:* Clean compilation, 0 errors.

---

## Test Run: 2026-11-11 — Phase 5, Step 1: Global Emergency Stop & Automation Cancellation Verification

- **Scope:** Phase 5 Step 1 Global Emergency Stop Subsystem, Hotkey Listener, Input Cleanup, and Controller Integration
- **Target:** `backend/automation/emergency_stop.py`, `backend/automation/keyboard_controller.py`, `backend/automation/mouse_controller.py`, `backend/automation/browser_controller.py`, `backend/agent/tool_executor.py`, `backend/agent/graph.py`, `backend/api/agent.py`, `backend/main.py`, `backend/voice/voice_pipeline.py`

### 1. Dedicated Emergency Stop Test Suites
- **Command:**
  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/test_emergency_stop.py tests/test_emergency_stop_api.py tests/test_agent_cancellation.py tests/test_emergency_stop_integration.py -v
  ```
- **Result:** PASS (39 passed in 7.12s)
- **Breakdown:**
  - `tests/test_emergency_stop.py` (15/15 passed):
    - `test_initial_state_inactive`: Baseline state is inactive, no active operations.
    - `test_check_emergency_stop_when_inactive`: Executes silently without raising when inactive.
    - `test_trigger_emergency_stop`: Sets latched state, reason, source, timestamp; `check_emergency_stop` raises `EmergencyStopActiveError`.
    - `test_repeated_trigger_is_idempotent_and_safe`: Repeated triggers do not raise and preserve initial metadata.
    - `test_operation_registration_and_unregistration`: Tracks active operations and cleans up on unregister.
    - `test_register_operation_fails_when_latched`: Fails closed and raises `EmergencyStopActiveError` on new registration.
    - `test_unsafe_reset_rejected_with_active_operations`: Rejects reset with `UnsafeResetError` when operations are running.
    - `test_force_reset_succeeds_even_with_active_operations`: Allows reset when `force=True`.
    - `test_safe_reset_after_all_operations_unregister`: Clean reset succeeds when operations are completed.
    - `test_cleanup_callbacks_invoked_on_trigger`: Subsystem callbacks run synchronously upon emergency stop.
    - `test_reset_does_not_auto_resume`: Interrupted executions are never auto-dispatched on reset.
    - `test_listener_initialization_and_properties`: Listener properties and defaults verified.
    - `test_listener_graceful_stop_when_not_started`: Safe shutdown before start.
    - `test_listener_starts_and_stops_cleanly`: Clean thread startup, message loop, and `WM_QUIT` termination.
    - `test_release_system_inputs_executes_safely`: Win32 keybd_event and mouse_event dispatches run safely.
  - `tests/test_emergency_stop_api.py` (7/7 passed):
    - `test_get_status_initial`: Returns 200 OK with inactive status.
    - `test_trigger_emergency_stop_endpoint`: `POST /agent/emergency-stop` triggers latch immediately.
    - `test_safe_reset_endpoint_when_clear`: `POST /agent/emergency-stop/reset` resets latch when safe.
    - `test_unsafe_reset_rejected_with_http_409`: Returns HTTP 409 Conflict when active operations are running.
    - `test_force_reset_succeeds_with_running_operations`: Force reset succeeds with HTTP 200.
    - `test_csrf_origin_protection_on_emergency_stop_endpoints`: Untrusted Origin headers rejected with HTTP 403 Forbidden.
    - `test_reset_emergency_stop_strictly_omitted_from_llm_tool_registry`: Verified zero reset tools exposed to LLM.
  - `tests/test_agent_cancellation.py` (5/5 passed):
    - `test_process_request_rejected_immediately_when_emergency_stop_active`: Pre-execution rejection with status `cancelled`.
    - `test_resume_execution_rejected_immediately_when_emergency_stop_active`: Rejects resumption after confirmation.
    - `test_mid_workflow_step_halts_and_preserves_completed_steps`: Step 1 preserved in `completed_steps`, Step 2 not dispatched.
    - `test_emergency_stop_prevents_dynamic_replanning`: Re-planning planner call strictly blocked.
    - `test_voice_pipeline_rejects_command_when_emergency_stop_active`: Informs user truthfully without leaking secrets.
  - `tests/test_emergency_stop_integration.py` (12/12 passed):
    - `test_keyboard_type_text_aborts_immediately_when_stop_active`: Aborts typing and calls `release_all_modifiers()`.
    - `test_keyboard_type_text_chunk_aborts_mid_typing`: Aborts midway through chunked typing.
    - `test_keyboard_press_key_aborts_on_emergency_stop`: Aborts single key press.
    - `test_keyboard_send_shortcut_aborts_on_emergency_stop`: Aborts shortcut dispatch.
    - `test_mouse_move_to_aborts_on_emergency_stop`: Aborts mouse move.
    - `test_mouse_click_aborts_on_emergency_stop`: Aborts mouse click.
    - `test_mouse_scroll_aborts_on_emergency_stop`: Aborts mouse scroll.
    - `test_mouse_release_all_buttons_calls_system_release`: Verifies button release helper.
    - `test_browser_methods_abort_on_emergency_stop`: Verifies browser methods check emergency stop.
    - `test_browser_emergency_cleanup_callback_closes_active_session`: Closes active browser session on stop.
    - `test_tool_executor_operation_registration_and_cleanup`: Verifies active operation lifecycle.
    - `test_tool_executor_returns_cancelled_when_emergency_stop_triggered_inside_tool`: Returns `status="cancelled"`.

---

## Test Run: 2026-11-11 — Phase 6 Step 3: Automation Monitoring, Audit Logs & Browser Integration Testing

- **Scope:** Complete frontend dashboard, automation monitoring, audit archive & download, safe API client, Vitest component suite, Playwright browser integration suite, and backend regression.
- **Environment:** Windows 11, Python 3.12.10 (`.venv`), Node.js v26.11.1, npm 11.20.0, Vite 6.4.6, React 18.3.1, TypeScript 5.7.2, Vitest 5.1.0, Playwright 1.56.0 Chromium.

### 1. Frontend Unit & Component Tests (Vitest)
- **Command:**
  ```powershell
  cd frontend; npm test
  ```
- **Result:** PASS (29/29 tests passed in 1.59s)
- **Test Files:**
  - `src/tests/agentAndSecurity.test.ts`: 5 passed
  - `src/tests/apiClient.test.ts`: 12 passed
  - `src/tests/automationAndAudit.test.ts`: 12 passed

### 2. Frontend Lint & Typecheck
- **Commands:**
  ```powershell
  cd frontend; npm run lint; npm run typecheck
  ```
- **Result:** PASS (0 errors, 0 warnings)

### 3. Frontend Production Build
- **Command:**
  ```powershell
  cd frontend; npm run build
  ```
- **Result:** PASS (1620 modules transformed; `dist/index.html` 0.49 kB, `dist/assets/index-CjMtuHW4.css` 48.54 kB, `dist/assets/index-D2-u7DV2.js` 251.23 kB in 3.83s)

### 4. Playwright Browser Integration Tests
- **Command:**
  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/test_frontend_browser.py -v
  ```
- **Result:** PASS (8/8 scenarios passed in 7.73s)
  - `test_01_dashboard_loads`: PASSED (Dashboard title, layout, and header connectivity pill verified)
  - `test_02_sidebar_navigation_works`: PASSED (Clean page switching across AI Agent, Executions, Confirmations, Automation, and Audit Logs)
  - `test_03_agent_chat_accepts_and_submits_message`: PASSED (Chat textarea accepts input, sends command, and message bubble renders in thread)
  - `test_04_execution_history_renders_records`: PASSED (Persistent execution rows render with status badges)
  - `test_05_confirmation_modal_requires_deliberate_approval`: PASSED (Confirmation center renders; zero token leakage)
  - `test_06_emergency_stop_ui_reflects_backend_status`: PASSED (Safety latch reflects NORMAL state; STOP button visible)
  - `test_07_audit_export_download_works_with_fixture`: PASSED (Audit archive table renders fixture and download button functions)
  - `test_08_backend_unavailable_state_displayed_correctly`: PASSED (Network disconnection triggers clear disconnected pill)

### 5. Backend Regression Tests
- **Non-Live Test Suite:**
  ```powershell
  .\.venv\Scripts\python.exe -m pytest -m "not live"
  ```
  *Output:* `569 passed, 3 deselected in 47.62s` (0 failed, 0 errors).
- **Opt-In Live Test Suite:**
  ```powershell
  .\.venv\Scripts\python.exe -m pytest -m "live" -v
  ```
  *Output:* `3 passed, 569 deselected in 12.66s` (Notepad launch, Notepad window management, Playwright browser).
- **Total Combined Tests:** `601 passed, 0 failed` (572 pytest + 29 vitest).
- **Dependency Integrity:**
  ```powershell
  .\.venv\Scripts\python.exe -m pip check
  ```
  *Output:* `No broken requirements found.`

- **Full Unit & Integration Suite:**
  ```powershell
  .\.venv\Scripts\python.exe -m pytest -m "not live"
  ```
  *Output:* `519 passed, 3 deselected in 15.67s` (0 failed, 0 errors).
- **Live Desktop & Browser Verification:**
  ```powershell
  .\.venv\Scripts\python.exe -m pytest -m "live" -v
  ```
  *Output:* `3 passed, 519 deselected in 11.49s` (Notepad launch, Notepad window management, Playwright browser).
- **Total Combined Tests:** `522 passed, 0 failed`.
- **Dependency Integrity:**
  ```powershell
  .\.venv\Scripts\python.exe -m pip check
  ```
  *Output:* `No broken requirements found.`
- **Python Syntax Compilation:**
  ```powershell
  .\.venv\Scripts\python.exe -m py_compile backend/automation/emergency_stop.py backend/automation/keyboard_controller.py backend/automation/mouse_controller.py backend/automation/browser_controller.py backend/agent/tool_executor.py backend/agent/graph.py backend/api/agent.py backend/main.py backend/voice/voice_pipeline.py tests/test_emergency_stop.py tests/test_emergency_stop_api.py tests/test_agent_cancellation.py tests/test_emergency_stop_integration.py
  ```
  *Output:* Clean compilation, 0 errors.

---

## Test Run: 2026-11-11 — Phase 5, Step 2: Rate Limiting, Safety Checkpoints, and Audit Trail Export Verification

- **Scope:** Phase 5 Step 2: Emergency Stop Reset Correction, Sliding-Window Rate Limiter, 7-Stage Tool Executor Pipeline, LangGraph Safety Checkpoints, Browser/Filesystem Safeguards, and Audit Trail Export Subsystem.
- **Target:** `backend/automation/emergency_stop.py`, `backend/agent/rate_limiter.py`, `backend/agent/tool_executor.py`, `backend/agent/graph.py`, `backend/agent/state.py`, `backend/automation/browser_controller.py`, `backend/automation/file_manager.py`, `backend/database/audit_export.py`, `backend/api/agent.py`.

### 1. Dedicated Rate Limiting, Budgets, and Audit Export Test Suites
- **Command:**
  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/test_emergency_stop_reset.py tests/test_rate_limiter.py tests/test_execution_budgets.py tests/test_safety_checkpoints.py tests/test_audit_export.py -v
  ```
- **Result:** PASS (38 passed in 5.86s)
- **Breakdown:**
  - `tests/test_emergency_stop_reset.py` (9/9 passed):
    - `test_force_true_reset_is_rejected_with_unsafe_reset_error`: Calling `reset_emergency_stop(force=True)` raises `UnsafeResetError`.
    - `test_safe_reset_succeeds_when_no_active_operations`: Standard reset succeeds cleanly when active operations count is 0.
    - `test_safe_reset_rejected_when_active_operations_exist`: Standard reset raises `UnsafeResetError` with active operations.
    - `test_reset_at_timestamp_updated_on_successful_reset`: Verified `reset_at` property tracking.
    - `test_operator_recovery_fails_with_insufficient_justification`: Rejects justification shorter than 10 characters with `ValueError`.
    - `test_operator_recovery_fails_when_unaccounted_operations_remain`: Rejects recovery when operations remain unaccounted for.
    - `test_operator_recovery_fails_when_pid_is_still_running`: Validates live process PID termination via `psutil`.
    - `test_operator_recovery_succeeds_with_valid_terminated_pids`: Recovers latch and clears accounted operations with dead PIDs.
    - `test_reset_and_recovery_strictly_excluded_from_tool_registry`: Verified zero reset or recovery tools exposed to LLM in `ToolRegistry`.
  - `tests/test_rate_limiter.py` (7/7 passed):
    - `test_request_rate_limiter_allows_under_limit`: Requests within sliding-window limit succeed.
    - `test_request_rate_limiter_blocks_over_limit`: Sliding-window exhaustion raises `RateLimitExceededError`.
    - `test_per_conversation_rate_limiter`: Enforces per-conversation sliding window isolation.
    - `test_sliding_window_prunes_expired_timestamps`: Timestamp pruning older than 60s verified.
    - `test_concurrent_execution_slot_acquisition_and_release`: Execution slots bounded to `max_concurrent_executions`.
    - `test_execution_budget_tracker_duration_and_steps`: Duration and step increments tracked accurately.
    - `test_tool_call_rate_limits`: Global tool calls per minute and same-tool repeat limits enforced.
  - `tests/test_execution_budgets.py` (7/7 passed):
    - `test_tool_calls_per_execution_budget_exceeded`: Stage 3 tool call budget halts execution with `status="rate_limited"`.
    - `test_same_tool_call_budget_exceeded`: Stage 4 same-tool repetition limit halts execution with `status="rate_limited"`.
    - `test_execution_duration_budget_exceeded`: Stage 2 duration budget check halts execution with `status="rate_limited"`.
    - `test_tool_executor_evaluation_pipeline_order`: Verified strict 7-stage order (emergency stop -> duration -> tool budget -> rate limit -> schema -> confirmation -> execution).
    - `test_browser_navigation_budget_enforced`: BrowserController enforces $\le 5$ navigations per session.
    - `test_browser_download_budget_enforced`: BrowserController enforces $\le 3$ downloads per session.
    - `test_file_search_result_and_scanned_item_bounds`: FileManager enforces `MAX_SEARCH_RESULTS = 100` and `MAX_SEARCH_SCANNED_ITEMS = 1000`.
  - `tests/test_safety_checkpoints.py` (5/5 passed):
    - `test_process_request_rate_limited_returns_rate_limited_status`: LangGraph rejects incoming request if sliding window is exhausted.
    - `test_plan_step_count_exceeding_budget_halts_workflow`: LangGraph plan validation halts if steps exceed `max_steps`.
    - `test_mid_execution_duration_budget_halts_and_preserves_completed_steps`: Duration budget exhaustion halts execution, preserves Step 1 in `completed_steps`, and produces truthful response.
    - `test_tool_call_budget_halts_and_preserves_completed_steps`: Tool call budget exhaustion halts execution cleanly without re-planning loops.
    - `test_rate_limited_execution_returns_http_429`: FastAPI `/agent/execute` endpoint returns HTTP 429 when execution status is `"rate_limited"`.
  - `tests/test_audit_export.py` (10/10 passed):
    - `test_sensitive_data_scrubber_redacts_credentials`: Scrubs tokens, passwords, keys, and secrets with `"[REDACTED]"`.
    - `test_audit_export_json_structure`: Exports execution records and steps to valid JSON with metadata.
    - `test_audit_export_csv_structure`: Exports execution records and steps to valid flattened CSV.
    - `test_audit_export_directory_containment_prevents_traversal`: Path traversal attempts outside `data/exports` raise `ValueError`.
    - `test_audit_export_overwrite_protection`: Overwrite requires explicit `confirm_overwrite=True`.
    - `test_audit_export_date_and_status_filtering`: Supports filtering exports by date range and execution status.
    - `test_audit_export_api_endpoints`: REST `POST /agent/audit/export` and `GET /agent/audit/exports/{export_id}/status` return 200 OK.
    - `test_audit_export_api_csrf_origin_protection`: Untrusted Origin headers rejected with HTTP 403 Forbidden.
    - `test_audit_export_strictly_excluded_from_tool_registry`: Verified zero audit export tools in `ToolRegistry`.
    - `test_exported_files_are_never_executed_or_opened`: Verifies export routines never trigger subprocess or OS execution.

---

## Test Run: 2026-11-11 — Phase 6 Step 3: Automation Monitoring, Audit Logs & Browser Integration Testing

- **Scope:** Complete frontend dashboard, automation monitoring, audit archive & download, safe API client, Vitest component suite, Playwright browser integration suite, and backend regression.
- **Environment:** Windows 11, Python 3.12.10 (`.venv`), Node.js v26.11.1, npm 11.20.0, Vite 6.4.6, React 18.3.1, TypeScript 5.7.2, Vitest 5.1.0, Playwright 1.56.0 Chromium.

### 1. Frontend Unit & Component Tests (Vitest)
- **Command:**
  ```powershell
  cd frontend; npm test
  ```
- **Result:** PASS (29/29 tests passed in 1.59s)
- **Test Files:**
  - `src/tests/agentAndSecurity.test.ts`: 5 passed
  - `src/tests/apiClient.test.ts`: 12 passed
  - `src/tests/automationAndAudit.test.ts`: 12 passed

### 2. Frontend Lint & Typecheck
- **Commands:**
  ```powershell
  cd frontend; npm run lint; npm run typecheck
  ```
- **Result:** PASS (0 errors, 0 warnings)

### 3. Frontend Production Build
- **Command:**
  ```powershell
  cd frontend; npm run build
  ```
- **Result:** PASS (1620 modules transformed; `dist/index.html` 0.49 kB, `dist/assets/index-CjMtuHW4.css` 48.54 kB, `dist/assets/index-D2-u7DV2.js` 251.23 kB in 3.83s)

### 4. Playwright Browser Integration Tests
- **Command:**
  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/test_frontend_browser.py -v
  ```
- **Result:** PASS (8/8 scenarios passed in 7.73s)
  - `test_01_dashboard_loads`: PASSED (Dashboard title, layout, and header connectivity pill verified)
  - `test_02_sidebar_navigation_works`: PASSED (Clean page switching across AI Agent, Executions, Confirmations, Automation, and Audit Logs)
  - `test_03_agent_chat_accepts_and_submits_message`: PASSED (Chat textarea accepts input, sends command, and message bubble renders in thread)
  - `test_04_execution_history_renders_records`: PASSED (Persistent execution rows render with status badges)
  - `test_05_confirmation_modal_requires_deliberate_approval`: PASSED (Confirmation center renders; zero token leakage)
  - `test_06_emergency_stop_ui_reflects_backend_status`: PASSED (Safety latch reflects NORMAL state; STOP button visible)
  - `test_07_audit_export_download_works_with_fixture`: PASSED (Audit archive table renders fixture and download button functions)
  - `test_08_backend_unavailable_state_displayed_correctly`: PASSED (Network disconnection triggers clear disconnected pill)

### 5. Backend Regression Tests
- **Non-Live Test Suite:**
  ```powershell
  .\.venv\Scripts\python.exe -m pytest -m "not live"
  ```
  *Output:* `569 passed, 3 deselected in 47.62s` (0 failed, 0 errors).
- **Opt-In Live Test Suite:**
  ```powershell
  .\.venv\Scripts\python.exe -m pytest -m "live" -v
  ```
  *Output:* `3 passed, 569 deselected in 12.66s` (Notepad launch, Notepad window management, Playwright browser).
- **Total Combined Tests:** `601 passed, 0 failed` (572 pytest + 29 vitest).
- **Dependency Integrity:**
  ```powershell
  .\.venv\Scripts\python.exe -m pip check
  ```
  *Output:* `No broken requirements found.`

---

## Test Run: 2026-11-11 — Phase 7, Step 1 Full-System Integration & Security Audit

- **Scope:** Phase 7 Step 1 Full-System Integration Testing, Security Verification, and Test Suite Reconciliation
- **Target:** Full repository (React 18 frontend, FastAPI backend, LangGraph agent, Win32/Playwright automation, Faster-Whisper/Piper voice pipeline, SQLite persistence)

### 1. Frontend Unit & Component Tests (Vitest)
- **Command:**
  ```powershell
  cd frontend; npm test
  ```
- **Result:** PASS (29/29 tests passed in 1.57s)
- **Test Files:**
  - `src/tests/agentAndSecurity.test.ts`: 5 passed
  - `src/tests/apiClient.test.ts`: 12 passed
  - `src/tests/automationAndAudit.test.ts`: 12 passed

### 2. Frontend Lint, Typecheck, and Production Build
- **Commands:**
  ```powershell
  cd frontend; npm run lint; npm run typecheck; npm run build
  ```
- **Result:** PASS (0 lint errors, 0 type errors)
- **Build Output:**
  ```
  vite v6.4.6 building for production...
  transforming...
  ✓ 1620 modules transformed.
  rendering chunks...
  computing gzip size...
  dist/index.html                   0.49 kB │ gzip:  0.32 kB
  dist/assets/index-CjMtuHW4.css   48.54 kB │ gzip:  7.48 kB
  dist/assets/index-D2-u7DV2.js   251.23 kB │ gzip: 71.36 kB
  ✓ built in 4.26s
  ```

### 3. Playwright Headless Browser Integration Tests
- **Command:**
  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/test_frontend_browser.py -v
  ```
- **Result:** PASS (8/8 passed in 7.86s)
- **Detailed Results:**
  - `test_01_dashboard_loads`: PASSED [12%]
  - `test_02_sidebar_navigation_works`: PASSED [25%]
  - `test_03_agent_chat_accepts_and_submits_message`: PASSED [37%]
  - `test_04_execution_history_renders_records`: PASSED [50%]
  - `test_05_confirmation_modal_requires_deliberate_approval`: PASSED [62%]
  - `test_06_emergency_stop_ui_reflects_backend_status`: PASSED [75%]
  - `test_07_audit_export_download_works_with_fixture`: PASSED [87%]
  - `test_08_backend_unavailable_state_displayed_correctly`: PASSED [100%]

### 4. Full Pytest Non-Live Test Suite (Backend + Browser Integration)
- **Command:**
  ```powershell
  .\.venv\Scripts\python.exe -m pytest -m "not live"
  ```
- **Result:** PASS (569 passed, 3 deselected in 36.94s)
- **Taxonomy Breakdown:**
  - Backend unit & integration tests: 561 tests
  - Playwright browser integration tests: 8 tests
  - Total non-live tests: 569 tests

### 5. Opt-In Live Desktop & Browser Verification Suite
- **Command:**
  ```powershell
  .\.venv\Scripts\python.exe -m pytest -m "live" -v
  ```
- **Result:** PASS (3 passed, 569 deselected in 9.72s)
- **Detailed Results:**
  - `tests/test_application_launcher.py::test_live_launch_and_cleanup_notepad`: PASSED [33%]
  - `tests/test_browser_controller.py::test_live_browser_automation`: PASSED [66%]
  - `tests/test_window_manager.py::test_live_notepad_window_management`: PASSED [100%]

### 6. Dependency Integrity Check
- **Command:**
  ```powershell
  .\.venv\Scripts\python.exe -m pip check
  ```
- **Result:** PASS
- **Output:** `No broken requirements found.`

### 7. Total Reconciled Test Inventory
- Pytest non-live tests: 569
- Pytest live tests: 3
- Vitest frontend tests: 29
- **Grand Total Unique Automated Tests:** 601 tests (601 passed, 0 failed)

---

## Test Run: 2026-11-11 — Phase 7, Step 2 Standalone Windows Packaging & Distribution Verification

- **Scope:** Phase 7 Step 2 Standalone Windows Distribution (PyInstaller One-Folder Distribution, Runtime Path Resolution, Single-Port Hosting, Dependency Diagnostics, and Relocation Testing)
- **Target:** `dist/VoxPilot/VoxPilot.exe`, `backend/paths.py`, `launcher.py`, `VoxPilot.spec`, `tests/test_paths.py`, `scripts/verify_distribution.py`

### 1. Runtime Path Resolution Unit Tests
- **Command:**
  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/test_paths.py -v
  ```
- **Result:** PASS (16/16 passed in 0.60s)
- **Coverage:**
  - `test_is_frozen_in_normal_python`: Development mode detection.
  - `test_is_frozen_when_sys_meipass_present`: Frozen runtime bundle detection.
  - `test_get_bundle_dir_in_normal_python`: Repository root resolution.
  - `test_get_bundle_dir_when_frozen`: PyInstaller extraction directory resolution (`sys._MEIPASS`).
  - `test_get_user_data_dir_default_windows`: `%LOCALAPPDATA%\VoxPilot` resolution.
  - `test_get_user_data_dir_override`: `VOXPILOT_DATA_DIR` environment override.
  - `test_get_database_path_default_and_override`: Database path resolution & env override.
  - `test_get_log_dir_default_and_override`: Log path resolution & env override.
  - `test_get_export_dir_default_and_override`: Export directory resolution & env override.
  - `test_get_config_dir_default_and_override`: Config directory resolution & env override.
  - `test_get_voice_temp_dir_default_and_override`: Voice audio scratch directory resolution & env override.
  - `test_get_frontend_dist_dir_dev_and_frozen`: React dist resolution in dev vs frozen.
  - `test_get_piper_model_path_dev_and_frozen`: Piper ONNX model resolution in dev vs frozen.
  - `test_ensure_user_directories`: Automatic provisioning of user data subdirectories.

### 2. Frontend Production Compilation
- **Command:**
  ```powershell
  cd frontend; npm run lint; npm run typecheck; npm run build
  ```
- **Result:** PASS (0 lint errors, 0 typecheck errors)
- **Build Output:**
  ```
  vite v6.4.6 building for production...
  transforming...
  ✓ 1620 modules transformed.
  rendering chunks...
  computing gzip size...
  dist/index.html                   0.49 kB │ gzip:  0.32 kB
  dist/assets/index-CjMtuHW4.css   48.54 kB │ gzip:  7.48 kB
  dist/assets/index-D2-u7DV2.js   251.23 kB │ gzip: 71.36 kB
  ✓ built in 3.48s
  ```

### 3. Standalone Distribution Packaging (PyInstaller)
- **Command:**
  ```powershell
  .\.venv\Scripts\pyinstaller.exe VoxPilot.spec --noconfirm
  ```
- **Result:** PASS (Exit code 0; generated `dist/VoxPilot/`)
- **Package Inventory:**
  - `dist/VoxPilot/VoxPilot.exe`: 31,689,356 bytes (~31.6 MB)
  - `dist/VoxPilot/_internal/`: Runtime shared libraries, bundled dependencies, ONNX model (`_internal/models/piper/en_US-lessac-medium.onnx`), and React frontend (`_internal/frontend/dist/`)
  - `dist/VoxPilot/README.txt`: 2,216 bytes quickstart instructions

### 4. Standalone Executable In-Place & Relocation Verification
- **Command:**
  ```powershell
  .\.venv\Scripts\python.exe scripts\verify_distribution.py
  ```
- **Result:** PASS (All tests passed)
- **Detailed Results:**
  - **In-Place Execution (`dist/VoxPilot/VoxPilot.exe` on port 8001):**
    - `GET /health`: `{"status":"healthy","service":"VoxPilot","version":"0.5.0"}`
    - `GET /` (JSON): `{"app":"VoxPilot Agent API","version":"0.5.0","status":"online"}`
    - `GET /` (HTML): Status 200, length 483, contains `<div id="root"></div>`
    - `GET /agent/emergency-stop/status`: `{"is_active":false,...}`
    - `GET /agent/tools`: 37 registered tools enumerated
    - `GET /agent/conversations`: Database session query executed successfully
  - **User Data Isolation Check:**
    - `%LOCALAPPDATA%\VoxPilot\data\voxpilot.db`: Exists (4,096 bytes)
    - `%LOCALAPPDATA%\VoxPilot\logs\voxpilot.log`: Exists (5,150 bytes)
  - **Relocated Execution (`%TEMP%\VoxPilot_Relocated_Test` on port 8002):**
    - Full distribution folder copied outside repository tree
    - Launched `VoxPilot.exe` on port 8002 with `VOXPILOT_AUTO_OPEN_BROWSER=0`
    - All endpoints (`/health`, `/` HTML/JSON, `/agent/emergency-stop/status`, `/agent/tools`, `/agent/conversations`) responded with status 200
    - Clean process shutdown and temporary folder cleanup

### 5. Full Backend Pytest Regression Test Suite
- **Command:**
  ```powershell
  .\.venv\Scripts\python.exe -m pytest -m "not live"
  ```
- **Result:** PASS (585 passed, 3 deselected in 34.67s)
- **Inventory:** 577 backend unit & integration tests + 8 Playwright browser integration tests. 0 failed, 0 errors.

### 6. Opt-In Live Desktop & Browser Verification Suite
- **Command:**
  ```powershell
  .\.venv\Scripts\python.exe -m pytest -m "live" -v
  ```
- **Result:** PASS (3 passed, 585 deselected in 9.63s)
  - `test_live_launch_and_cleanup_notepad`: PASSED
  - `test_live_browser_automation`: PASSED
  - `test_live_notepad_window_management`: PASSED

### 7. Frontend Vitest Test Suite
- **Command:**
  ```powershell
  cd frontend; npm test
  ```
- **Result:** PASS (29/29 passed in 1.48s)

### 8. Python Dependency Integrity
- **Command:**
  ```powershell
  .\.venv\Scripts\python.exe -m pip check
  ```
- **Result:** PASS (`No broken requirements found.`)

---

## Test Run: 2026-11-11 — Phase 7 Step 3: Windows Installer Compilation & E2E Verification

- **Scope:** Phase 7 Step 3 Windows Installer & First-Run Validation
- **Target:** `installer/VoxPilot.iss`, `installer/Output/VoxPilot-Setup.exe`, `scripts/verify_installer.py`

### 1. Inno Setup Compiler Compilation
- **Command:**
  ```powershell
  & "C:\Users\Thanu\AppData\Local\Programs\Inno Setup 6\ISCC.exe" installer\VoxPilot.iss
  ```
- **Result:** PASS (Exit code 0)
- **Compiler Version:** Inno Setup 6.7.3 Command-Line Compiler
- **Compression:** LZMA2/ultra64 solid compression
- **Output Executable:** `installer\Output\VoxPilot-Setup.exe`
- **File Size:** 230,734,929 bytes (~220.05 MB)
- **SHA-256 Checksum:** `A47B5220D7C4BE15983FDBDF34E2177C47640C6964AA9FAFA29D9B0367B5CF57`

### 2. End-to-End Installer Lifecycle Test Suite
- **Command:**
  ```powershell
  .\.venv\Scripts\python.exe scripts\verify_installer.py
  ```
- **Result:** PASS (All 8 lifecycle checks passed 100%)
- **Detailed Execution Breakdown:**
  1. **Installer Binary Check:** `installer\Output\VoxPilot-Setup.exe` verified present and accessible (220.05 MB).
  2. **Silent Custom Installation:** Executed `VoxPilot-Setup.exe /VERYSILENT /SUPPRESSMSGBOXES /SP- /NORESTART /DIR="C:\Users\Thanu\AppData\Local\Temp\VoxPilot Custom Test Install"`. Exited cleanly with return code 0.
  3. **Installed Artifact Verification:**
     - `VoxPilot.exe`: Verified present
     - `install_browser.bat`: Verified present
     - `README.txt`: Verified present
     - `unins000.exe`: Verified present
     - `_internal\`: Verified present
  4. **Start Menu Shortcut Verification:**
     - `VoxPilot.lnk`: Verified in `AppData\Roaming\Microsoft\Windows\Start Menu\Programs\VoxPilot`
     - `Install Playwright Browser.lnk`: Verified present
     - `Uninstall VoxPilot.lnk`: Verified present
  5. **Installed Executable Live Runtime Test (Port 8003):**
     - Launched `VoxPilot.exe` from custom install path with `VOXPILOT_PORT=8003` and `VOXPILOT_AUTO_OPEN_BROWSER=0`.
     - `GET http://127.0.0.1:8003/health`: Status 200 `{'status': 'healthy', 'service': 'VoxPilot', 'version': '0.5.0'}`.
     - `GET http://127.0.0.1:8003/` (JSON): Status 200 `{'app': 'VoxPilot Agent API', 'version': '0.5.0', 'status': 'online'}`.
     - `GET http://127.0.0.1:8003/` (HTML): Status 200, length 483 bytes, contains `<div id="root"></div>`.
     - `GET http://127.0.0.1:8003/agent/emergency-stop/status`: Status 200, `is_active: false`.
     - `GET http://127.0.0.1:8003/agent/tools`: Status 200, 37 canonical tools returned.
     - `GET http://127.0.0.1:8003/agent/conversations`: Status 200, SQLite database query successful.
     - Graceful process termination verified.
  6. **User Data Isolation Verification:**
     - Confirmed database created at `%LOCALAPPDATA%\VoxPilot\data\voxpilot.db` (4,096 bytes).
     - Confirmed log file created at `%LOCALAPPDATA%\VoxPilot\logs\voxpilot.log` (11,831 bytes).
  7. **Upgrade Persistence Test:**
     - Re-ran `VoxPilot-Setup.exe` silently over existing test install.
     - Verified installation completed cleanly and `%LOCALAPPDATA%\VoxPilot\data\voxpilot.db` was untouched and preserved.
  8. **Silent Uninstallation & Data Retention Invariant:**
     - Executed `unins000.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART`.
     - Application directory and Start Menu shortcuts completely removed.
     - **CRITICAL SAFETY INVARIANT PRESERVED:** `%LOCALAPPDATA%\VoxPilot\data\voxpilot.db` (4,096 bytes) and logs strictly preserved on disk.

### 3. Full Backend Pytest Regression Test Suite
- **Command:**
  ```powershell
  .\.venv\Scripts\python.exe -m pytest -m "not live"
  ```
- **Result:** PASS (585 passed, 3 deselected in 37.73s)
- **Inventory:** 577 backend unit & integration tests + 8 Playwright browser integration tests. 0 failed, 0 errors.

### 4. Opt-In Live Desktop & Browser Verification Suite
- **Command:**
  ```powershell
  .\.venv\Scripts\python.exe -m pytest -m "live" -v
  ```
- **Result:** PASS (3 passed, 585 deselected in 9.20s)
  - `test_live_launch_and_cleanup_notepad`: PASSED
  - `test_live_browser_automation`: PASSED
  - `test_live_notepad_window_management`: PASSED

### 5. Frontend Vitest Test Suite
- **Command:**
  ```powershell
  cd frontend; npm test
  ```
- **Result:** PASS (29/29 passed in 1.47s across 3 test suites)

### 6. Frontend Build, Lint, and Typecheck
- **Commands:**
  ```powershell
  cd frontend; npm run lint; npm run typecheck; npm run build
  ```
- **Result:** PASS (0 ESLint errors, 0 TypeScript errors; production bundle built in 4.47s)

### 7. Python Dependency Integrity
- **Command:**
  ```powershell
  .\.venv\Scripts\python.exe -m pip check
  ```
- **Result:** PASS (`No broken requirements found.`)

### 8. Total Reconciled Project Test Inventory
- Pytest non-live tests: 585
- Pytest live tests: 3
- Vitest frontend tests: 29
- Standalone distribution & relocation suite: 1 (passes all validation checks)
- Inno Setup installer lifecycle E2E suite: 1 (passes all 8 validation checks)
- **Grand Total Unique Automated Tests:** 617 tests (617 passed, 0 failed) + 2 live verification scripts.

---

## Test Run: 2026-11-11 — Voice Mode Integration, Global Keyboard Shortcut, & Rebuilt Distribution Verification

- **Scope:** Voice Mode UI Integration, Customizable Global Shortcut (`Ctrl+Shift+V`), `SettingsPage`, Agent Planner Fixes, Standalone Exe & Installer Rebuild
- **Target:** `tests/test_voice_mode_and_shortcut.py`, `tests/test_planner_bugfixes.py`, `frontend/src/tests/voiceAndSettings.test.ts`, `dist/VoxPilot/VoxPilot.exe`, `installer/Output/VoxPilot-Setup.exe`

### 1. Backend Non-Live Pytest Suite
- **Command:**
  ```powershell
  .\.venv\Scripts\python.exe -m pytest -m "not live"
  ```
- **Result:** PASS (637 passed, 3 deselected in 30.84s)

### 2. Backend Opt-In Live Pytest Suite
- **Command:**
  ```powershell
  .\.venv\Scripts\python.exe -m pytest -m "live" -v
  ```
- **Result:** PASS (3 passed, 637 deselected in 11.77s)

### 3. Frontend ESLint, Vitest, and Production Build
- **Commands:**
  ```powershell
  npm --prefix frontend run lint
  npm --prefix frontend test
  npm --prefix frontend run build
  ```
- **Result:** PASS
  - ESLint: 0 errors, 0 warnings
  - Vitest: 35 passed across 4 test files in 1.47s
  - Build: Production bundle built in 3.68s (`dist/assets/index-Bd8mLTSM.css` 53.95 kB, `dist/assets/index-LhVy5szg.js` 277.00 kB)

### 4. Standalone Distribution & Relocation Verification (`scripts/verify_distribution.py`)
- **Command:**
  ```powershell
  .\.venv\Scripts\python.exe scripts\verify_distribution.py
  ```
- **Result:** PASS (In-place port 8001 + relocated `%TEMP%\VoxPilot_Relocated_Test` port 8002 passed 100%, including `/agent/voice/status` returning `{'phase': 'idle', 'shortcut': 'Ctrl+Shift+V', 'shortcut_registered': True}`)

### 5. Rebuilt Inno Setup Windows Installer (`installer\Output\VoxPilot-Setup.exe`)
- **Command:**
  ```powershell
  & "C:\Users\Thanu\AppData\Local\Programs\Inno Setup 6\ISCC.exe" installer\VoxPilot.iss
  ```
- **Result:** PASS (Exit code 0 in 308.671s)
- **File Size:** 230,792,262 bytes (~220.10 MB)
- **SHA-256 Checksum:** `82E3358735672ECDEF5104AC2951FDD6DB91E6428BDA33A96BBD3F5CA0B6F666`
- **Grand Total Automated Tests:** **675 passed, 0 failed** (637 non-live pytest + 3 live pytest + 35 frontend Vitest).

---

## Test Run: 2026-11-11 — In-App Software Update System (Phase 1) & Rebuilt Distribution Verification

- **Scope:** Centralized Version Authority (`VERSION`, `backend/version.py`), GitHub Release Update Service (`backend/updater/update_service.py`), External Windows Updater Runner (`backend/updater/updater_runner.py`), FastAPI Update Endpoints (`backend/api/updates.py`), Frontend Update Banner & Settings UI (`UpdateNotificationBanner.tsx`, `SettingsPage.tsx`), Standalone `VoxPilot.exe`, and Inno Setup `VoxPilot-Setup.exe`
- **Commands & Results:**
  - `.\.venv\Scripts\pytest.exe tests/test_update_system.py -v`: **39 passed** in 14.27s
  - `.\.venv\Scripts\pytest.exe -m "not live" -q`: **677 passed**, 3 deselected in 38.32s
  - `.\.venv\Scripts\pytest.exe -m "live" -q`: **3 passed**, 677 deselected in 15.72s
  - `npm --prefix frontend test`: **44 passed** across 5 test files (`updateSystem.test.ts`: 9 passed) in 1.46s
  - `npm --prefix frontend run lint`: **0 errors, 0 warnings**
  - `npm --prefix frontend run typecheck`: **0 errors**
  - `npm --prefix frontend run build`: **PASS** (`dist/assets/index-Dk0ol29s.css` 57.70 kB, `dist/assets/index-DgfCa3aM.js` 300.11 kB)
  - `.\.venv\Scripts\python.exe scripts\verify_distribution.py`: **PASS** (in-place port 8001 + relocated `%TEMP%\VoxPilot_Relocated_Test` port 8002, including `/api/version` & `/api/updates/status`)
  - `dist\VoxPilot\VoxPilot.exe`: **31,857,586 bytes** (SHA-256: `E6DF5681D895E861A02AEC0075D3CAAC7AFE91D1179263E851C17E8A9F2F53AC`)
  - `installer\Output\VoxPilot-Setup.exe`: **233,384,613 bytes** (SHA-256: `17D7B0C40E3B3542C709B5A0DCF24B083841DC9E56852C85FB185BC9B9AAD79B`)
  - **Grand Total Automated Tests:** **724 passed, 0 failed** (677 non-live pytest + 3 live pytest + 44 frontend Vitest).






