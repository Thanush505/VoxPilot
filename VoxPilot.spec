# -*- mode: python ; coding: utf-8 -*-
"""
VoxPilot - PyInstaller Specification File
Builds a self-contained, relocatable Windows one-folder distribution.
Bundles the FastAPI backend, compiled React frontend, Piper voice assets,
and native automation hooks while strictly excluding dev-only dependencies.
"""

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

block_cipher = None
spec_dir = Path(SPECPATH).resolve()

# 1. Collect required binary libraries
binaries = []
binaries += collect_dynamic_libs('ctranslate2')
binaries += collect_dynamic_libs('onnxruntime')

# 2. Collect read-only runtime data assets
datas = []

# Compiled React frontend production build
frontend_dist = spec_dir / 'frontend' / 'dist'
if frontend_dist.exists():
    datas.append((str(frontend_dist), 'frontend/dist'))

# Piper ONNX voice model and configuration (excluding test recordings)
piper_model = spec_dir / 'models' / 'piper' / 'en_US-lessac-medium.onnx'
piper_config = spec_dir / 'models' / 'piper' / 'en_US-lessac-medium.onnx.json'
if piper_model.exists():
    datas.append((str(piper_model), 'models/piper'))
if piper_config.exists():
    datas.append((str(piper_config), 'models/piper'))

# Distribution README and canonical VERSION file
readme_dist = spec_dir / 'README_DIST.txt'
if readme_dist.exists():
    datas.append((str(readme_dist), '.'))

version_file = spec_dir / 'VERSION'
if version_file.exists():
    datas.append((str(version_file), '.'))

# Official VoxPilot application icon assets
assets_icons_dir = spec_dir / 'assets' / 'icons'
if assets_icons_dir.exists():
    datas.append((str(assets_icons_dir), 'assets/icons'))

# Playwright node driver executable and packages
try:
    import playwright
    playwright_driver = Path(playwright.__file__).parent / 'driver'
    if playwright_driver.exists():
        datas.append((str(playwright_driver), 'playwright/driver'))
except Exception as err:
    print(f"Warning: Could not locate Playwright driver: {err}")

# Package data files for piper phoneme tables and models
datas += collect_data_files('piper')
datas += collect_data_files('openwakeword')
datas += collect_data_files('faster_whisper')

# 3. Explicit hidden imports for dynamic ASGI/Uvicorn, SQLite, and Win32 modules
hiddenimports = [
    'uvicorn',
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespans',
    'uvicorn.lifespans.on',
    'fastapi',
    'fastapi.staticfiles',
    'sqlmodel',
    'aiosqlite',
    'sqlalchemy.dialects.sqlite',
    'sqlalchemy.dialects.sqlite.aiosqlite',
    'sounddevice',
    'soundfile',
    'piper',
    'ctranslate2',
    'faster_whisper',
    'openwakeword',
    'playwright',
    'playwright.sync_api',
    'PIL',
    'PIL.Image',
    'PIL.ImageGrab',
    'pyautogui',
    'pywinauto',
    'win32gui',
    'win32con',
    'win32api',
    'win32process',
    'backend.paths',
    'backend.version',
    'backend.main',
    'backend.api.agent',
    'backend.api.updates',
    'backend.updater',
    'backend.updater.update_service',
    'backend.updater.updater_runner',
    'backend.database.connection',
    'backend.database.models',
    'backend.database.repository',
    'backend.database.audit_export',
    'backend.agent.graph',
    'backend.agent.intent',
    'backend.agent.llm',
    'backend.agent.planner',
    'backend.agent.state',
    'backend.agent.tool_executor',
    'backend.agent.tool_registry',
    'backend.agent.rate_limiter',
    'backend.memory.context_manager',
    'backend.tools.applications',
    'backend.tools.browser',
    'backend.tools.files',
    'backend.tools.keyboard',
    'backend.tools.mouse',
    'backend.tools.screenshots',
    'backend.tools.system',
    'backend.tools.windows',
    'backend.automation.emergency_stop',
    'backend.automation.confirmation_manager',
    'backend.automation.application_launcher',
    'backend.automation.window_manager',
    'backend.automation.keyboard_controller',
    'backend.automation.mouse_controller',
    'backend.automation.file_manager',
    'backend.automation.screenshot_controller',
    'backend.automation.browser_controller',
    'backend.voice.voice_pipeline',
    'backend.voice.audio_recorder',
    'backend.voice.audio_preprocessor',
    'backend.voice.streaming_vad',
    'backend.voice.speech_to_text',
    'backend.voice.text_to_speech',
    'backend.voice.wake_word',
    'backend.voice.hotkey',
    'backend.voice.voice_service',
]

# 4. Strict excludes: avoid dragging test frameworks and heavy unused dev dependencies
excludes = [
    'tests',
    'pytest',
    '_pytest',
    'vitest',
    'tkinter',
    'matplotlib',
    'scipy.tests',
    'numpy.tests',
]

a = Analysis(
    ['launcher.py'],
    pathex=[str(spec_dir)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=['hooks'],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

app_icon_file = spec_dir / 'assets' / 'icons' / 'voxpilot.ico'
version_info_file = spec_dir / 'assets' / 'version_info.txt'

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='VoxPilot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(app_icon_file) if app_icon_file.exists() else None,
    version=str(version_info_file) if version_info_file.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='VoxPilot',
)
