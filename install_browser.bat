@echo off
title VoxPilot - Playwright Chromium Browser Setup
echo ======================================================================
echo   VoxPilot - Playwright Chromium Browser Setup
echo ======================================================================
echo.
echo Installing Chromium browser for VoxPilot web automation...
echo Destination: %%LOCALAPPDATA%%\ms-playwright
echo.
if exist "%~dp0VoxPilot.exe" (
    "%~dp0VoxPilot.exe" --install-browser
) else if exist "%~dp0dist\VoxPilot\VoxPilot.exe" (
    "%~dp0dist\VoxPilot\VoxPilot.exe" --install-browser
) else (
    echo Error: Could not locate VoxPilot.exe in the current directory.
)
echo.
pause

