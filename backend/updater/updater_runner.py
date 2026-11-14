"""
VoxPilot - Standalone External Updater Runner
Executes outside the main VoxPilot server lifecycle to coordinate:
1. Waiting for the running VoxPilot process (PID) to terminate cleanly.
2. Final pre-execution SHA-256 verification of the downloaded installer.
3. Launching the verified Inno Setup installer (VoxPilot-Setup.exe) with safe upgrade arguments.
4. Preserving all user data in %LOCALAPPDATA%\\VoxPilot.
5. Recording installation result status to %LOCALAPPDATA%\\VoxPilot\\updates\\update_install_result.json.
6. Optionally relaunching VoxPilot after a successful upgrade.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def compute_file_sha256(file_path: Path) -> str:
    """Compute lowercase hexadecimal SHA-256 digest of a file."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest().lower()


def is_pid_running(pid: int) -> bool:
    """Return True if a process with the given PID is still running."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                if ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return exit_code.value == STILL_ACTIVE
                return False
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def wait_for_process_exit(pid: int, timeout_seconds: float = 30.0, poll_interval: float = 0.25) -> bool:
    """Wait for the specified PID to terminate within timeout_seconds."""
    if pid <= 0:
        return True
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not is_pid_running(pid):
            return True
        time.sleep(poll_interval)
    return not is_pid_running(pid)


def write_install_result(result_file: Path, payload: Dict[str, Any]) -> None:
    """Atomically write the updater installation result JSON."""
    result_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = result_file.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp_path.replace(result_file)


def normalize_version_str(version_str: Optional[str]) -> str:
    """Normalize a semantic version string for equality comparison."""
    cleaned = (version_str or "").strip()
    if cleaned.startswith(("v", "V")):
        cleaned = cleaned[1:].strip()
    return cleaned


def resolve_installed_version(install_dir: Optional[Path]) -> Optional[str]:
    """Read the installed VoxPilot VERSION file from root or PyInstaller _internal dir."""
    if install_dir is None:
        return None
    for candidate in (install_dir / "VERSION", install_dir / "_internal" / "VERSION"):
        try:
            if candidate.is_file():
                for line in candidate.read_text(encoding="utf-8-sig").splitlines():
                    normalized = normalize_version_str(line)
                    if normalized:
                        return normalized
        except Exception:
            continue
    return None


def append_updater_log(log_path: Path, message: str) -> None:
    """Append a timestamped diagnostic message to an updater log (best-effort)."""
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).isoformat()
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(f"[{ts}] {message}\n")
    except Exception:
        pass


def verify_executable_in_install_dir(exe_path: Path, install_dir: Optional[Path]) -> bool:
    """Return True if exe_path exists and resides inside install_dir (when provided)."""
    if not exe_path.is_file():
        return False
    if install_dir is None:
        return True
    try:
        exe_resolved = exe_path.resolve()
        dir_resolved = install_dir.resolve()
        return exe_resolved.parent == dir_resolved or dir_resolved in exe_resolved.parents
    except Exception:
        return False


def verify_backend_health(
    health_url: str,
    expected_version: str,
    timeout_seconds: float = 25.0,
    poll_interval: float = 0.5,
) -> Dict[str, Any]:
    """Poll the relaunched backend /health endpoint until it reports expected_version."""
    deadline = time.monotonic() + timeout_seconds
    last_error = "Timed out waiting for backend health endpoint."
    expected_norm = normalize_version_str(expected_version)

    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(health_url, method="GET")
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                if resp.status == 200:
                    payload = json.loads(resp.read().decode("utf-8"))
                    reported = normalize_version_str(payload.get("version"))
                    if payload.get("status") == "healthy" and (not expected_norm or reported == expected_norm):
                        return {
                            "ok": True,
                            "url": health_url,
                            "status": payload.get("status"),
                            "version": reported,
                            "error": None,
                        }
                    last_error = (
                        f"Backend health reported status={payload.get('status')!r}, "
                        f"version={reported!r} (expected {expected_norm!r})."
                    )
        except Exception as exc:
            last_error = str(exc)
        time.sleep(poll_interval)

    return {
        "ok": False,
        "url": health_url,
        "status": "unreachable",
        "version": None,
        "error": last_error,
    }


def build_inno_setup_command(
    installer_path: Path,
    install_dir: Optional[Path] = None,
    log_file: Optional[Path] = None,
    extra_args: Optional[List[str]] = None,
) -> List[str]:
    """Build the command line for launching the verified Inno Setup installer."""
    cmd = [
        str(installer_path),
        "/SP-",
        "/SILENT",
        "/NORESTART",
        "/CLOSEAPPLICATIONS",
    ]
    if install_dir is not None:
        cmd.append(f"/DIR={install_dir}")
    if log_file is not None:
        cmd.append(f"/LOG={log_file}")
    if extra_args:
        cmd.extend(extra_args)
    return cmd


def execute_external_update(
    installer_path: Path,
    expected_sha256: str,
    target_version: str,
    result_file: Path,
    wait_pid: int = 0,
    wait_timeout: float = 30.0,
    install_dir: Optional[Path] = None,
    relaunch_exe: Optional[Path] = None,
    relaunch: bool = False,
    installer_extra_args: Optional[List[str]] = None,
    installer_timeout: float = 300.0,
    session_id: Optional[str] = None,
    previous_version: str = "",
    logs_dir: Optional[Path] = None,
    health_url: Optional[str] = None,
    health_timeout: float = 25.0,
) -> Dict[str, Any]:
    """Perform the complete external updater workflow and record the outcome."""
    started_at = datetime.now(timezone.utc).isoformat()
    active_session_id = session_id or f"upd_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    active_logs_dir = logs_dir or (result_file.parent.parent / "logs")
    log_file = result_file.parent / "installer_execution.log"
    runner_log = result_file.parent / "updater_runner.log"
    session_log = active_logs_dir / f"update_{active_session_id}.log"
    expected_version = normalize_version_str(target_version)

    def _log(msg: str) -> None:
        append_updater_log(runner_log, msg)
        append_updater_log(session_log, msg)

    _log(
        f"Session={active_session_id} started: previous_version={previous_version or 'unknown'}, "
        f"target_version={target_version}, installer={installer_path}, "
        f"install_dir={install_dir}, executable_path={relaunch_exe}, wait_pid={wait_pid}, relaunch={relaunch}"
    )

    def _fail(
        exit_code: int,
        error_msg: str,
        installed_ver: Optional[str] = None,
        verified_sha: Optional[str] = None,
        backend_health: Optional[Dict[str, Any]] = None,
        recovery_hint: Optional[str] = None,
        restart_result: str = "not_attempted",
    ) -> Dict[str, Any]:
        _log(f"FAILED (exit_code={exit_code}): {error_msg}")
        hint = recovery_hint or (
            "Your previous installation and user data in %LOCALAPPDATA%\\VoxPilot were preserved. "
            f"Close any running VoxPilot windows and run the verified installer manually ({installer_path}), "
            f"or inspect logs at {session_log}."
        )
        payload = {
            "status": "failed",
            "session_id": active_session_id,
            "previous_version": previous_version or None,
            "target_version": target_version,
            "installed_version": installed_ver,
            "installer_filename": installer_path.name,
            "installer_path": str(installer_path),
            "expected_sha256": (expected_sha256 or "").strip().lower(),
            "verified_sha256": verified_sha,
            "install_dir": str(install_dir) if install_dir else None,
            "executable_path": str(relaunch_exe) if relaunch_exe else None,
            "installer_log": str(log_file),
            "runner_log": str(runner_log),
            "session_log": str(session_log),
            "started_at": started_at,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "exit_code": exit_code,
            "relaunch_requested": relaunch,
            "relaunched": False,
            "restart_result": restart_result,
            "backend_health": backend_health,
            "recovery_hint": hint,
            "error": error_msg,
        }
        write_install_result(result_file, payload)
        return payload

    write_install_result(
        result_file,
        {
            "status": "installing",
            "session_id": active_session_id,
            "previous_version": previous_version or None,
            "target_version": target_version,
            "installed_version": None,
            "installer_filename": installer_path.name,
            "installer_path": str(installer_path),
            "expected_sha256": (expected_sha256 or "").strip().lower(),
            "verified_sha256": None,
            "install_dir": str(install_dir) if install_dir else None,
            "executable_path": str(relaunch_exe) if relaunch_exe else None,
            "installer_log": str(log_file),
            "runner_log": str(runner_log),
            "session_log": str(session_log),
            "started_at": started_at,
            "completed_at": None,
            "exit_code": None,
            "relaunch_requested": relaunch,
            "relaunched": False,
            "restart_result": "pending",
            "backend_health": None,
            "recovery_hint": None,
            "error": None,
        },
    )

    # 1. Validate installer exists and has expected SHA-256 checksum
    if not installer_path.is_file():
        return _fail(-1, f"Installer file not found at {installer_path}")

    expected_clean = (expected_sha256 or "").strip().lower()
    if len(expected_clean) != 64 or any(c not in "0123456789abcdef" for c in expected_clean):
        return _fail(-2, "Refusing to launch installer: missing or invalid expected SHA-256 checksum.")

    actual_sha256 = compute_file_sha256(installer_path)
    if actual_sha256 != expected_clean:
        return _fail(
            -3,
            f"Pre-execution SHA-256 verification failed: expected {expected_clean}, got {actual_sha256}.",
            verified_sha=actual_sha256,
            recovery_hint=(
                "The downloaded installer failed SHA-256 verification and was not executed. "
                "Re-download the official release from Settings -> Updates."
            ),
        )
    _log(f"SHA-256 verified: {actual_sha256}")

    # 2. Wait for main VoxPilot process to exit cleanly
    if wait_pid > 0:
        _log(f"Waiting up to {wait_timeout}s for PID {wait_pid} to exit...")
        exited = wait_for_process_exit(wait_pid, timeout_seconds=wait_timeout)
        if not exited:
            return _fail(
                -4,
                f"Timed out waiting for VoxPilot process (PID {wait_pid}) to exit.",
                verified_sha=actual_sha256,
                recovery_hint=(
                    "VoxPilot did not exit in time. Close VoxPilot completely (including tray/background windows) "
                    "and retry the update or run the downloaded installer manually."
                ),
            )
        _log(f"PID {wait_pid} exited cleanly.")

    # 3. Execute verified installer
    cmd = build_inno_setup_command(
        installer_path=installer_path,
        install_dir=install_dir,
        log_file=log_file,
        extra_args=installer_extra_args,
    )
    _log(f"Executing installer command: {cmd}")
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=installer_timeout,
            check=False,
        )
        exit_code = completed.returncode
        _log(f"Installer exited with code {exit_code}")
    except subprocess.TimeoutExpired:
        return _fail(
            -5,
            f"Installer timed out after {installer_timeout} seconds.",
            verified_sha=actual_sha256,
        )
    except Exception as exc:
        return _fail(-5, f"Failed to execute installer: {exc}", verified_sha=actual_sha256)

    if exit_code != 0:
        msg = (
            "Installer was cancelled by the user (exit code 2)."
            if exit_code == 2
            else f"Installer exited with non-zero code {exit_code}."
        )
        return _fail(exit_code, msg, verified_sha=actual_sha256)

    # 4. Post-install verification: executable existence, ownership in install_dir, and VERSION match
    if relaunch_exe is not None and not verify_executable_in_install_dir(relaunch_exe, install_dir):
        return _fail(
            -6,
            f"Post-install verification failed: executable not found or outside install_dir ({relaunch_exe}).",
            verified_sha=actual_sha256,
        )

    verified_version: Optional[str] = None
    if install_dir is not None:
        verified_version = resolve_installed_version(install_dir)
        if not verified_version:
            return _fail(
                -7,
                f"Post-install version verification failed: VERSION file not found in {install_dir}.",
                installed_ver=None,
                verified_sha=actual_sha256,
            )
        if expected_version and verified_version != expected_version:
            return _fail(
                -7,
                f"Post-install version verification failed: expected v{expected_version}, "
                f"but found v{verified_version} in {install_dir}.",
                installed_ver=verified_version,
                verified_sha=actual_sha256,
            )
        _log(f"Verified installed VERSION={verified_version} in {install_dir}")

    # 5. Optionally relaunch VoxPilot ONLY after successful verification
    relaunched = False
    restart_result = "not_requested"
    backend_health: Optional[Dict[str, Any]] = None
    relaunch_error: Optional[str] = None

    if relaunch and relaunch_exe is not None and relaunch_exe.is_file():
        try:
            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            subprocess.Popen(
                [str(relaunch_exe)],
                cwd=str(relaunch_exe.parent),
                creationflags=creationflags,
                close_fds=True,
            )
            relaunched = True
            restart_result = "relaunched"
            _log(f"Relaunched {relaunch_exe}")
        except Exception as exc:
            relaunch_error = str(exc)
            return _fail(
                -8,
                f"Update installed and verified v{verified_version}, but failed to relaunch {relaunch_exe}: {relaunch_error}",
                installed_ver=verified_version,
                verified_sha=actual_sha256,
                restart_result="failed",
            )

        if health_url:
            _log(f"Verifying relaunched backend health at {health_url}...")
            backend_health = verify_backend_health(
                health_url=health_url,
                expected_version=expected_version or (verified_version or ""),
                timeout_seconds=health_timeout,
            )
            _log(f"Backend health check result: {backend_health}")
            if not backend_health.get("ok"):
                return _fail(
                    -9,
                    f"Relaunched VoxPilot failed backend health verification: {backend_health.get('error')}",
                    installed_ver=verified_version,
                    verified_sha=actual_sha256,
                    backend_health=backend_health,
                    restart_result="health_check_failed",
                )
            restart_result = "relaunched_and_verified"

    result = {
        "status": "completed",
        "session_id": active_session_id,
        "previous_version": previous_version or None,
        "target_version": target_version,
        "installed_version": verified_version,
        "installer_filename": installer_path.name,
        "installer_path": str(installer_path),
        "expected_sha256": expected_clean,
        "verified_sha256": actual_sha256,
        "install_dir": str(install_dir) if install_dir else None,
        "executable_path": str(relaunch_exe) if relaunch_exe else None,
        "installer_log": str(log_file),
        "runner_log": str(runner_log),
        "session_log": str(session_log),
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "exit_code": 0,
        "relaunch_requested": relaunch,
        "relaunched": relaunched,
        "restart_result": restart_result,
        "backend_health": backend_health,
        "recovery_hint": None,
        "error": relaunch_error,
    }
    write_install_result(result_file, result)
    _log("External update completed and verified successfully.")
    return result


def generate_powershell_updater_script(
    script_path: Path,
    installer_path: Path,
    expected_sha256: str,
    target_version: str,
    result_file: Path,
    wait_pid: int,
    install_dir: Path,
    relaunch_exe: Path,
    relaunch: bool,
    session_id: Optional[str] = None,
    previous_version: str = "",
    logs_dir: Optional[Path] = None,
    health_url: str = "",
    installer_timeout_seconds: int = 300,
    health_timeout_seconds: int = 25,
) -> Path:
    """Write a detached PowerShell updater script into %LOCALAPPDATA%\\VoxPilot\\updates.

    Running from %LOCALAPPDATA%\\VoxPilot\\updates ensures zero file locks are held
    on %LOCALAPPDATA%\\Programs\\VoxPilot while Inno Setup upgrades VoxPilot.exe.
    """
    script_path.parent.mkdir(parents=True, exist_ok=True)
    active_session_id = session_id or f"upd_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    active_logs_dir = logs_dir or (result_file.parent.parent / "logs")
    active_logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = result_file.parent / "installer_execution.log"
    runner_log = result_file.parent / "updater_runner.log"
    session_log = active_logs_dir / f"update_{active_session_id}.log"
    relaunch_flag = "$true" if relaunch else "$false"

    ps_content = f"""# VoxPilot Detached Windows Updater Helper
$ErrorActionPreference = "Stop"
$SessionId = '{active_session_id.replace("'", "''")}'
$PreviousVersion = '{previous_version.replace("'", "''")}'
$InstallerPath = '{str(installer_path).replace("'", "''")}'
$InstallerFilename = '{installer_path.name.replace("'", "''")}'
$ExpectedSha256 = '{expected_sha256.strip().lower()}'
$TargetVersion = '{target_version.replace("'", "''")}'
$ResultFile = '{str(result_file).replace("'", "''")}'
$LogFile = '{str(log_file).replace("'", "''")}'
$RunnerLog = '{str(runner_log).replace("'", "''")}'
$SessionLog = '{str(session_log).replace("'", "''")}'
$WaitPid = {int(wait_pid)}
$InstallDir = '{str(install_dir).replace("'", "''")}'
$RelaunchExe = '{str(relaunch_exe).replace("'", "''")}'
$Relaunch = {relaunch_flag}
$HealthUrl = '{health_url.replace("'", "''")}'
$InstallerTimeoutMs = {int(installer_timeout_seconds) * 1000}
$HealthTimeoutSec = {int(health_timeout_seconds)}
$StartedAt = (Get-Date).ToUniversalTime().ToString("o")
$VerifiedSha256 = $null
$ProcTermSummary = "none"
$InstallerArgsSummary = ""
$BackendHealthObj = $null

function Write-RunnerLog($Message) {{
    $ts = (Get-Date).ToUniversalTime().ToString("o")
    $line = "[$ts] $Message"
    try {{ Add-Content -LiteralPath $RunnerLog -Value $line -Encoding UTF8 }} catch {{}}
    try {{ Add-Content -LiteralPath $SessionLog -Value $line -Encoding UTF8 }} catch {{}}
}}

function Write-UpdateResult($Status, $ExitCode, $Relaunched, $ErrorMsg, $InstalledVer = $null, $RestartResult = "not_attempted", $RecoveryHint = $null) {{
    $payload = @{{
        status = $Status
        session_id = $SessionId
        previous_version = if ($PreviousVersion) {{ $PreviousVersion }} else {{ $null }}
        target_version = $TargetVersion
        installed_version = $InstalledVer
        installer_filename = $InstallerFilename
        installer_path = $InstallerPath
        expected_sha256 = $ExpectedSha256
        verified_sha256 = $VerifiedSha256
        install_dir = $InstallDir
        executable_path = $RelaunchExe
        process_termination = $ProcTermSummary
        installer_args = $InstallerArgsSummary
        installer_log = $LogFile
        runner_log = $RunnerLog
        session_log = $SessionLog
        started_at = $StartedAt
        completed_at = (Get-Date).ToUniversalTime().ToString("o")
        exit_code = $ExitCode
        relaunch_requested = $Relaunch
        relaunched = $Relaunched
        restart_result = $RestartResult
        backend_health = $BackendHealthObj
        recovery_hint = $RecoveryHint
        error = $ErrorMsg
    }} | ConvertTo-Json -Depth 5
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($ResultFile, $payload, $utf8NoBom)
}}

function Get-LingeringInstallProcesses {{
    param([string]$TargetDir)
    $normalized = $TargetDir.TrimEnd('\\').ToLowerInvariant()
    try {{
        return @(Get-CimInstance Win32_Process -Filter "Name = 'VoxPilot.exe'" -ErrorAction SilentlyContinue | Where-Object {{
            $_.ExecutablePath -and $_.ExecutablePath.ToLowerInvariant().StartsWith($normalized)
        }})
    }} catch {{
        return @()
    }}
}}

function Resolve-InstalledVersion {{
    param([string]$TargetDir)
    $candidates = @(
        (Join-Path $TargetDir "VERSION"),
        (Join-Path (Join-Path $TargetDir "_internal") "VERSION")
    )
    foreach ($candidate in $candidates) {{
        if (Test-Path -LiteralPath $candidate) {{
            $lines = Get-Content -LiteralPath $candidate -Encoding UTF8 -ErrorAction SilentlyContinue
            foreach ($line in $lines) {{
                $trimmed = ($line -as [string]).Trim()
                if ($trimmed) {{
                    return $trimmed.TrimStart('v', 'V').Trim()
                }}
            }}
        }}
    }}
    return $null
}}

$DefaultRecovery = "Your previous installation and user data in %LOCALAPPDATA%\\VoxPilot were preserved. Close any running VoxPilot windows and run $InstallerPath manually, or review $SessionLog."

try {{
    Write-RunnerLog "Update session $SessionId started. Previous=$PreviousVersion Target=$TargetVersion WaitPid=$WaitPid InstallDir=$InstallDir"
    Write-UpdateResult "installing" $null $false $null $null "pending" $null

    if (-not (Test-Path -LiteralPath $InstallerPath)) {{
        Write-RunnerLog "ERROR: Installer not found at $InstallerPath"
        Write-UpdateResult "failed" -1 $false "Installer file not found at $InstallerPath." $null "not_attempted" $DefaultRecovery
        exit 1
    }}

    $hashObj = Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256
    $VerifiedSha256 = $hashObj.Hash.ToLowerInvariant()
    if ($VerifiedSha256 -ne $ExpectedSha256) {{
        Write-RunnerLog "ERROR: SHA-256 mismatch. Expected $ExpectedSha256, got $VerifiedSha256"
        Write-UpdateResult "failed" -3 $false "Pre-execution SHA-256 mismatch: expected $ExpectedSha256, got $VerifiedSha256" $null "not_attempted" "Re-download the official release from Settings -> Updates."
        exit 3
    }}
    Write-RunnerLog "SHA-256 verified: $VerifiedSha256"

    # Wait gracefully for main PID and any VoxPilot.exe processes in InstallDir (up to 20s),
    # then force-terminate lingering processes belonging strictly to InstallDir and wait up to 10s more.
    $graceDeadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $graceDeadline) {{
        $mainAlive = ($WaitPid -gt 0) -and [bool](Get-Process -Id $WaitPid -ErrorAction SilentlyContinue)
        $lingering = Get-LingeringInstallProcesses -TargetDir $InstallDir
        if (-not $mainAlive -and $lingering.Count -eq 0) {{ break }}
        Start-Sleep -Milliseconds 300
    }}

    $mainAlive = ($WaitPid -gt 0) -and [bool](Get-Process -Id $WaitPid -ErrorAction SilentlyContinue)
    $lingering = Get-LingeringInstallProcesses -TargetDir $InstallDir
    if ($mainAlive -or $lingering.Count -gt 0) {{
        $killedPids = @()
        Write-RunnerLog "Processes still running after grace period; terminating lingering VoxPilot processes in $InstallDir..."
        if ($mainAlive) {{
            Stop-Process -Id $WaitPid -Force -ErrorAction SilentlyContinue
            $killedPids += $WaitPid
        }}
        foreach ($p in $lingering) {{
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
            $killedPids += $p.ProcessId
        }}
        $ProcTermSummary = "terminated_pids=" + ($killedPids -join ",")
        $forceDeadline = (Get-Date).AddSeconds(10)
        while ((Get-Date) -lt $forceDeadline) {{
            $mainAlive = ($WaitPid -gt 0) -and [bool](Get-Process -Id $WaitPid -ErrorAction SilentlyContinue)
            $lingering = Get-LingeringInstallProcesses -TargetDir $InstallDir
            if (-not $mainAlive -and $lingering.Count -eq 0) {{ break }}
            Start-Sleep -Milliseconds 300
        }}
    }} else {{
        $ProcTermSummary = "graceful_exit"
    }}

    $mainAlive = ($WaitPid -gt 0) -and [bool](Get-Process -Id $WaitPid -ErrorAction SilentlyContinue)
    $lingering = Get-LingeringInstallProcesses -TargetDir $InstallDir
    if ($mainAlive -or $lingering.Count -gt 0) {{
        $msg = "Timed out waiting for running VoxPilot processes in $InstallDir to exit before installation."
        Write-RunnerLog "ERROR: $msg"
        Write-UpdateResult "failed" -4 $false $msg $null "not_attempted" $DefaultRecovery
        exit 4
    }}
    Write-RunnerLog "All VoxPilot processes exited cleanly ($ProcTermSummary)."

    $argsList = @(
        "/SP-",
        "/SILENT",
        "/NORESTART",
        "/CLOSEAPPLICATIONS",
        "/DIR=`"$InstallDir`"",
        "/LOG=`"$LogFile`""
    )
    $InstallerArgsSummary = $argsList -join ' '
    Write-RunnerLog "Launching Inno Setup installer: $InstallerPath $InstallerArgsSummary"
    $proc = Start-Process -FilePath $InstallerPath -ArgumentList $argsList -PassThru
    $finished = $proc.WaitForExit($InstallerTimeoutMs)
    if (-not $finished) {{
        try {{ Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }} catch {{}}
        $msg = "Installer timed out after $($InstallerTimeoutMs / 1000) seconds."
        Write-RunnerLog "ERROR: $msg"
        Write-UpdateResult "failed" -5 $false $msg $null "not_attempted" $DefaultRecovery
        exit 5
    }}
    $code = $proc.ExitCode
    Write-RunnerLog "Installer exited with code $code"
    if ($code -ne 0) {{
        $msg = if ($code -eq 2) {{ "Installer was cancelled (exit code 2)." }} else {{ "Installer exited with code $code. Check $LogFile for details." }}
        Write-UpdateResult "failed" $code $false $msg $null "not_attempted" $DefaultRecovery
        exit $code
    }}

    # Verify installed executable exists and belongs to InstallDir
    $fullInstallDir = [System.IO.Path]::GetFullPath($InstallDir).TrimEnd('\\').ToLowerInvariant()
    $exeExists = Test-Path -LiteralPath $RelaunchExe
    $exeInDir = $false
    if ($exeExists) {{
        $fullExePath = [System.IO.Path]::GetFullPath($RelaunchExe).ToLowerInvariant()
        $exeInDir = $fullExePath.StartsWith($fullInstallDir + "\\")
    }}
    if (-not $exeExists -or -not $exeInDir) {{
        $msg = "Post-install verification failed: executable missing or outside $InstallDir ($RelaunchExe)."
        Write-RunnerLog "ERROR: $msg"
        Write-UpdateResult "failed" -6 $false $msg $null "not_attempted" $DefaultRecovery
        exit 6
    }}

    # Verify installed VERSION matches TargetVersion
    $expectedVer = $TargetVersion.Trim().TrimStart('v', 'V').Trim()
    $installedVer = Resolve-InstalledVersion -TargetDir $InstallDir
    if (-not $installedVer) {{
        $msg = "Post-install version verification failed: VERSION file not found in $InstallDir."
        Write-RunnerLog "ERROR: $msg"
        Write-UpdateResult "failed" -7 $false $msg $null "not_attempted" $DefaultRecovery
        exit 7
    }}
    if ($expectedVer -and ($installedVer -ne $expectedVer)) {{
        $msg = "Post-install version verification failed: expected v$expectedVer, found v$installedVer."
        Write-RunnerLog "ERROR: $msg"
        Write-UpdateResult "failed" -7 $false $msg $installedVer "not_attempted" $DefaultRecovery
        exit 7
    }}
    Write-RunnerLog "Verified installed executable and VERSION=v$installedVer"

    $didRelaunch = $false
    $restartResult = "not_requested"
    if ($Relaunch) {{
        Write-RunnerLog "Relaunching verified application: $RelaunchExe"
        Start-Process -FilePath $RelaunchExe -WorkingDirectory $InstallDir
        $didRelaunch = $true
        $restartResult = "relaunched"

        if ($HealthUrl) {{
            Write-RunnerLog "Polling relaunched backend health endpoint: $HealthUrl"
            $healthDeadline = (Get-Date).AddSeconds($HealthTimeoutSec)
            $healthOk = $false
            $lastHealthErr = "Timed out waiting for backend health endpoint."
            while ((Get-Date) -lt $healthDeadline) {{
                try {{
                    $resp = Invoke-RestMethod -Uri $HealthUrl -Method Get -TimeoutSec 2
                    if ($resp -and $resp.status -eq "healthy" -and ($resp.version -as [string]).TrimStart('v','V') -eq $expectedVer) {{
                        $healthOk = $true
                        $BackendHealthObj = @{{ ok = $true; url = $HealthUrl; status = $resp.status; version = $resp.version; error = $null }}
                        break
                    }}
                    $lastHealthErr = "Health reported status=$($resp.status) version=$($resp.version) (expected $expectedVer)"
                }} catch {{
                    $lastHealthErr = $_.Exception.Message
                }}
                Start-Sleep -Milliseconds 500
            }}
            if (-not $healthOk) {{
                $BackendHealthObj = @{{ ok = $false; url = $HealthUrl; status = "unhealthy"; version = $null; error = $lastHealthErr }}
                $msg = "Relaunched VoxPilot failed backend health verification: $lastHealthErr"
                Write-RunnerLog "ERROR: $msg"
                Write-UpdateResult "failed" -9 $true $msg $installedVer "health_check_failed" $DefaultRecovery
                exit 9
            }}
            $restartResult = "relaunched_and_verified"
            Write-RunnerLog "Backend health verified: status=healthy version=$expectedVer"
        }}
    }}

    Write-UpdateResult "completed" 0 $didRelaunch $null $installedVer $restartResult $null
    Write-RunnerLog "Update session $SessionId completed and verified successfully."
    exit 0
}} catch {{
    $errMsg = $_.Exception.Message
    Write-RunnerLog "EXCEPTION: $errMsg"
    Write-UpdateResult "failed" -99 $false $errMsg $null "failed" $DefaultRecovery
    exit 99
}}
"""
    script_path.write_text(ps_content, encoding="utf-8")
    return script_path


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint for invoking the standalone external updater runner."""
    parser = argparse.ArgumentParser(description="VoxPilot Detached Updater Runner")
    parser.add_argument("--installer", required=True, help="Path to verified VoxPilot-Setup.exe")
    parser.add_argument("--sha256", required=True, help="Expected 64-char SHA-256 hex digest")
    parser.add_argument("--target-version", required=True, help="Target semantic version")
    parser.add_argument("--result-file", required=True, help="Path to update_install_result.json")
    parser.add_argument("--wait-pid", type=int, default=0, help="PID of running VoxPilot process to wait for")
    parser.add_argument("--wait-timeout", type=float, default=30.0, help="Seconds to wait for PID termination")
    parser.add_argument("--install-dir", default="", help="Target installation directory")
    parser.add_argument("--relaunch-exe", default="", help="Executable to relaunch after update")
    parser.add_argument("--relaunch", action="store_true", help="Relaunch VoxPilot after successful update")
    args = parser.parse_args(argv)

    result = execute_external_update(
        installer_path=Path(args.installer).resolve(),
        expected_sha256=args.sha256,
        target_version=args.target_version,
        result_file=Path(args.result_file).resolve(),
        wait_pid=args.wait_pid,
        wait_timeout=args.wait_timeout,
        install_dir=Path(args.install_dir).resolve() if args.install_dir else None,
        relaunch_exe=Path(args.relaunch_exe).resolve() if args.relaunch_exe else None,
        relaunch=bool(args.relaunch),
    )
    return 0 if result.get("status") == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())

