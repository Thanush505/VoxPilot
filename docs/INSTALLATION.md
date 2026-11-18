# VoxPilot — Windows Installation & Data Lifecycle Guide

VoxPilot (`v0.5.5`) is distributed for Windows 11 as a per-user Windows Installer (`VoxPilot-Setup.exe`) that runs cleanly without requiring Administrator elevation.

---

## 1. System Requirements

- **Operating System:** Windows 11 (64-bit)
- **Web Runtime:** Microsoft Edge WebView2 Runtime (built into Windows 11)
- **Local AI (Default Provider):** Ollama running locally on `http://127.0.0.1:11434` with model `qwen3:4b` (or an optional BYOK API key for OpenAI, Anthropic Claude, or Google Gemini configured in **Settings**)
- **Optional Browser Automation:** Playwright Chromium browser (installable via Start Menu shortcut **Install Playwright Browser** or `VoxPilot.exe --install-browser`)

---

## 2. Installation Locations

- **Program Files (Per-User, No Admin Required):**
  `%LOCALAPPDATA%\Programs\VoxPilot`
  Contains `VoxPilot.exe`, bundled Python/WebView2 runtime libraries (`_internal\`), `VERSION`, `README.txt`, `voxpilot.ico`, `install_browser.bat`, and `unins000.exe`.
- **Canonical Windows AppUserModelID:**
  `VoxPilotTeam.VoxPilot.DesktopAgent.0.5.5` (consistent across Start Menu shortcuts, taskbar grouping, and Windows toast notifications).
- **Start Menu Shortcuts:**
  - `VoxPilot` — Launches the console-free native Edge WebView2 desktop shell
  - `Install Playwright Browser` — Installs Playwright Chromium into `%LOCALAPPDATA%\ms-playwright`
  - `VoxPilot Readme` — Opens `README.txt`
  - `Uninstall VoxPilot` — Launches Inno Setup uninstaller

---

## 3. User Data Separation (`%LOCALAPPDATA%\VoxPilot`)

All mutable user state is strictly isolated from the program installation directory under `%LOCALAPPDATA%\VoxPilot`:

| Directory / File | Contents |
| --- | --- |
| `data\voxpilot.db` | SQLite database (profiles, conversations, executions, preferences, automations) |
| `credentials\` | Windows DPAPI-encrypted BYOK provider & email credentials |
| `config\` | Runtime update settings and local configuration JSON |
| `backups\` | Verified SQLite Online Backup snapshots (`backup_manifest.json`) |
| `logs\` | Application runtime and updater logs (`voxpilot.log`) |
| `updates\` | Verified installer staging and external PowerShell updater runner |
| `webview\` | Persistent Edge WebView2 profile/cache storage |
| `screenshots\`, `downloads\`, `exports\`, `temp\` | Working directories for visual captures, audit exports, and voice/scratch files |

*(For development or isolated verification, setting the `VOXPILOT_USER_DATA_DIR` environment variable overrides `%LOCALAPPDATA%\VoxPilot`.)*

---

## 4. Upgrading & Uninstalling

### Upgrading Over an Existing Installation
Running a newer `VoxPilot-Setup.exe` (manually or via the in-app GitHub Updater) replaces application binaries inside `%LOCALAPPDATA%\Programs\VoxPilot` using the stable Inno Setup `AppId` (`{8F97B634-9A25-45C0-943C-65935E17F193}`) while leaving `%LOCALAPPDATA%\VoxPilot` untouched.

### Uninstalling & Preserving Data
Running **Uninstall VoxPilot** removes application binaries and Start Menu shortcuts from `%LOCALAPPDATA%\Programs\VoxPilot`. It does **not** delete `%LOCALAPPDATA%\VoxPilot`, preserving your SQLite history, profiles, encrypted API credentials, and backups automatically across reinstallations. To remove all personal data completely, delete `%LOCALAPPDATA%\VoxPilot` manually after uninstalling.
