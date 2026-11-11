================================================================================
  VoxPilot - Your Personal AI Desktop Control Agent
  Standalone Windows Distribution & Installer (Version 0.5.0)
================================================================================

1. QUICK START
--------------
- Standard Installation:
  Run 'VoxPilot-Setup.exe' to install VoxPilot into your user directory:
      %LOCALAPPDATA%\Programs\VoxPilot
  No administrator elevation (UAC) is required.
  Shortcuts will be created in your Start Menu and (optionally) on your Desktop.

- Portable / Direct Execution:
  You can also launch 'VoxPilot.exe' directly from the distribution directory.

- Launching & Single-Instance Protection:
  When launched, VoxPilot starts the local backend daemon on http://127.0.0.1:8000
  and automatically opens the React dashboard in your default web browser.
  If VoxPilot is already running, launching it again safely focuses your existing
  browser dashboard instead of crashing or attempting to rebind port 8000.

- To access the dashboard manually at any time, open:
      http://127.0.0.1:8000/

2. PREREQUISITES & EXTERNAL DEPENDENCIES
---------------------------------------
- Ollama (Local AI Model Runtime):
  VoxPilot connects to a local Ollama service for intent recognition and planning.
  Install Ollama from https://ollama.com and pull the default model:
      ollama pull qwen3:4b
      ollama serve
  If Ollama is offline when VoxPilot starts, the application logs a non-blocking
  diagnostic with the exact commands needed. The dashboard remains accessible.

- Playwright Chromium (Browser Automation):
  Safe web browsing uses an ephemeral, isolated Chromium browser instance.
  VoxPilot includes a self-contained browser setup tool that requires NO Python
  or Node.js installation on your computer.
  To install Chromium:
    - Click "Install Playwright Browser" in your Windows Start Menu, OR
    - Double-click 'install_browser.bat' in the application directory, OR
    - Run from terminal: VoxPilot.exe --install-browser
  Binaries are installed into %LOCALAPPDATA%\ms-playwright.

- Speech Recognition & Synthesis:
  - Piper Neural TTS voice model ('en_US-lessac-medium.onnx') is pre-bundled
    and ready to use offline immediately.
  - Faster-Whisper speech-to-text will automatically download its base model
    (~140 MB) from Hugging Face on its first transcription run and cache it in
    %USERPROFILE%\.cache\huggingface.

3. VOICE MODE & GLOBAL KEYBOARD SHORTCUTS
-----------------------------------------
- Global Voice Mode Toggle Shortcut (Default: Ctrl + Shift + V):
  Press Ctrl + Shift + V anywhere in Windows (even while working in another
  application) to toggle VoxPilot Voice Mode on or off.
  You can customize this shortcut, select your preferred microphone, test audio
  capture, and configure Piper TTS voice output in the dashboard Settings page.
  Voice settings are persisted in:
      %LOCALAPPDATA%\VoxPilot\config\voice_settings.json

- Windows Global Emergency Stop (Reserved: Ctrl + Alt + Escape):
  Press Ctrl + Alt + Escape at any time anywhere in Windows to immediately abort
  active automation, halt Voice Mode, stop browser navigation, and release all
  physical keyboard and mouse modifier buttons.
  For safety, Ctrl + Alt + Escape is permanently reserved and cannot be
  reassigned to Voice Mode.

- Human Confirmation Safeguards:
  Consequential operations (file deletion, folder removal, browser form submission,
  or purchasing) require explicit approval in the dashboard before execution.
  Approval tokens are single-use, cryptographically generated, and bound to
  action parameters. Confirmation tokens are never spoken aloud.

4. PERSISTENT USER DATA & UPGRADE SAFETY
---------------------------------------
VoxPilot strictly isolates your personal data from application binaries.
All runtime state is stored in your Windows local application folder:
  %LOCALAPPDATA%\VoxPilot\
    ├── data/         -> SQLite database (voxpilot.db)
    ├── logs/         -> Application logs (voxpilot.log)
    ├── exports/      -> Sanitized execution audit exports (JSON/CSV)
    ├── screenshots/  -> Captured desktop/window screenshots
    ├── downloads/    -> Verified browser file downloads
    └── config/       -> Runtime configuration files

- Upgrade Safety:
  Installing an update or running a newer installer will NEVER overwrite or erase
  your database, conversation history, audit exports, or configuration.
- Uninstall Safety:
  Uninstalling VoxPilot via Windows Settings or the Start Menu shortcut cleanly
  removes all program binaries and shortcuts, but preserves your database and logs
  in %LOCALAPPDATA%\VoxPilot so your history is preserved if you reinstall.

5. SHUTDOWN & CLEANUP
---------------------
- Press Ctrl + C in the VoxPilot console window to shut down the server cleanly.
- Background threads, database engines, and hotkey listeners will be released
  safely without leaving orphaned background processes.

================================================================================
