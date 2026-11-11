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
) -> Dict[str, Any]:
    """Perform the complete external updater workflow and record the outcome."""
    started_at = datetime.now(timezone.utc).isoformat()
    log_file = result_file.parent / "installer_execution.log"

    write_install_result(
        result_file,
        {
            "status": "installing",
            "target_version": target_version,
            "installer_path": str(installer_path),
            "started_at": started_at,
            "completed_at": None,
            "exit_code": None,
            "relaunch_requested": relaunch,
            "relaunched": False,
            "error": None,
        },
    )

    # 1. Validate installer exists and has expected SHA-256 checksum
    if not installer_path.is_file():
        result = {
            "status": "failed",
            "target_version": target_version,
            "installer_path": str(installer_path),
            "started_at": started_at,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "exit_code": -1,
            "relaunch_requested": relaunch,
            "relaunched": False,
            "error": f"Installer file not found at {installer_path}",
        }
        write_install_result(result_file, result)
        return result

    expected_clean = (expected_sha256 or "").strip().lower()
    if len(expected_clean) != 64 or any(c not in "0123456789abcdef" for c in expected_clean):
        result = {
            "status": "failed",
            "target_version": target_version,
            "installer_path": str(installer_path),
            "started_at": started_at,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "exit_code": -2,
            "relaunch_requested": relaunch,
            "relaunched": False,
            "error": "Refusing to launch installer: missing or invalid expected SHA-256 checksum.",
        }
        write_install_result(result_file, result)
        return result

    actual_sha256 = compute_file_sha256(installer_path)
    if actual_sha256 != expected_clean:
        result = {
            "status": "failed",
            "target_version": target_version,
            "installer_path": str(installer_path),
            "started_at": started_at,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "exit_code": -3,
            "relaunch_requested": relaunch,
            "relaunched": False,
            "error": (
                f"Pre-execution SHA-256 verification failed: expected {expected_clean}, "
                f"got {actual_sha256}."
            ),
        }
        write_install_result(result_file, result)
        return result

    # 2. Wait for main VoxPilot process to exit cleanly
    if wait_pid > 0:
        exited = wait_for_process_exit(wait_pid, timeout_seconds=wait_timeout)
        if not exited:
            result = {
                "status": "failed",
                "target_version": target_version,
                "installer_path": str(installer_path),
                "started_at": started_at,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "exit_code": -4,
                "relaunch_requested": relaunch,
                "relaunched": False,
                "error": f"Timed out waiting for VoxPilot process (PID {wait_pid}) to exit.",
            }
            write_install_result(result_file, result)
            return result

    # 3. Execute verified installer
    cmd = build_inno_setup_command(
        installer_path=installer_path,
        install_dir=install_dir,
        log_file=log_file,
        extra_args=installer_extra_args,
    )
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=installer_timeout,
            check=False,
        )
        exit_code = completed.returncode
    except Exception as exc:
        result = {
            "status": "failed",
            "target_version": target_version,
            "installer_path": str(installer_path),
            "started_at": started_at,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "exit_code": -5,
            "relaunch_requested": relaunch,
            "relaunched": False,
            "error": f"Failed to execute installer: {exc}",
        }
        write_install_result(result_file, result)
        return result

    if exit_code != 0:
        result = {
            "status": "failed",
            "target_version": target_version,
            "installer_path": str(installer_path),
            "started_at": started_at,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "exit_code": exit_code,
            "relaunch_requested": relaunch,
            "relaunched": False,
            "error": f"Installer exited with non-zero code {exit_code}.",
        }
        write_install_result(result_file, result)
        return result

    # 4. Optionally relaunch VoxPilot after successful installation
    relaunched = False
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
        except Exception as exc:
            relaunch_error = str(exc)

    result = {
        "status": "completed",
        "target_version": target_version,
        "installer_path": str(installer_path),
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "exit_code": 0,
        "relaunch_requested": relaunch,
        "relaunched": relaunched,
        "error": relaunch_error,
    }
    write_install_result(result_file, result)
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
) -> Path:
    """Write a detached PowerShell updater script into %LOCALAPPDATA%\\VoxPilot\\updates.

    Running from %LOCALAPPDATA%\\VoxPilot\\updates ensures zero file locks are held
    on %LOCALAPPDATA%\\Programs\\VoxPilot while Inno Setup upgrades VoxPilot.exe.
    """
    script_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = result_file.parent / "installer_execution.log"
    relaunch_flag = "$true" if relaunch else "$false"

    ps_content = f"""# VoxPilot Detached Windows Updater Helper
$ErrorActionPreference = "Stop"
$InstallerPath = '{str(installer_path).replace("'", "''")}'
$ExpectedSha256 = '{expected_sha256.strip().lower()}'
$TargetVersion = '{target_version.replace("'", "''")}'
$ResultFile = '{str(result_file).replace("'", "''")}'
$LogFile = '{str(log_file).replace("'", "''")}'
$WaitPid = {int(wait_pid)}
$InstallDir = '{str(install_dir).replace("'", "''")}'
$RelaunchExe = '{str(relaunch_exe).replace("'", "''")}'
$Relaunch = {relaunch_flag}
$StartedAt = (Get-Date).ToUniversalTime().ToString("o")

function Write-UpdateResult($Status, $ExitCode, $Relaunched, $ErrorMsg) {{
    $payload = @{{
        status = $Status
        target_version = $TargetVersion
        installer_path = $InstallerPath
        started_at = $StartedAt
        completed_at = (Get-Date).ToUniversalTime().ToString("o")
        exit_code = $ExitCode
        relaunch_requested = $Relaunch
        relaunched = $Relaunched
        error = $ErrorMsg
    }} | ConvertTo-Json -Depth 4
    Set-Content -Path $ResultFile -Value $payload -Encoding UTF8
}}

try {{
    Write-UpdateResult "installing" $null $false $null

    if (-not (Test-Path -LiteralPath $InstallerPath)) {{
        Write-UpdateResult "failed" -1 $false "Installer file not found."
        exit 1
    }}

    $hashObj = Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256
    $actualHash = $hashObj.Hash.ToLowerInvariant()
    if ($actualHash -ne $ExpectedSha256) {{
        Write-UpdateResult "failed" -3 $false "Pre-execution SHA-256 mismatch: expected $ExpectedSha256, got $actualHash"
        exit 3
    }}

    if ($WaitPid -gt 0) {{
        $deadline = (Get-Date).AddSeconds(30)
        while ((Get-Date) -lt $deadline) {{
            $proc = Get-Process -Id $WaitPid -ErrorAction SilentlyContinue
            if (-not $proc) {{ break }}
            Start-Sleep -Milliseconds 300
        }}
    }}

    $argsList = @(
        "/SP-",
        "/SILENT",
        "/NORESTART",
        "/CLOSEAPPLICATIONS",
        "/DIR=$InstallDir",
        "/LOG=$LogFile"
    )
    $proc = Start-Process -FilePath $InstallerPath -ArgumentList $argsList -Wait -PassThru
    $code = $proc.ExitCode
    if ($code -ne 0) {{
        Write-UpdateResult "failed" $code $false "Installer exited with code $code"
        exit $code
    }}

    $didRelaunch = $false
    if ($Relaunch -and (Test-Path -LiteralPath $RelaunchExe)) {{
        Start-Process -FilePath $RelaunchExe -WorkingDirectory $InstallDir
        $didRelaunch = $true
    }}

    Write-UpdateResult "completed" 0 $didRelaunch $null
    exit 0
}} catch {{
    Write-UpdateResult "failed" -99 $false $_.Exception.Message
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

