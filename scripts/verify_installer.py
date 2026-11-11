"""End-to-End verification script for VoxPilot Windows Installer (Inno Setup)."""

import os
import subprocess
import sys
import time
import urllib.request
import json
import shutil
from pathlib import Path


def wait_for_server(url: str, timeout: float = 15.0) -> bool:
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def test_installed_application(exe_path: Path, port: int = 8003) -> bool:
    print(f"\n--- Testing Installed Application: {exe_path} on port {port} ---")
    if not exe_path.exists():
        print(f"FAILED: {exe_path} does not exist!")
        return False

    env = os.environ.copy()
    env["VOXPILOT_PORT"] = str(port)
    env["VOXPILOT_AUTO_OPEN_BROWSER"] = "0"

    proc = subprocess.Popen(
        [str(exe_path)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        health_url = f"http://127.0.0.1:{port}/health"
        print(f"Waiting for server at {health_url}...")
        ready = wait_for_server(health_url, timeout=20.0)
        if not ready:
            print("Installed application server failed to respond within 20s.")
            proc.terminate()
            stdout, stderr = proc.communicate(timeout=5.0)
            print("STDOUT:\n", stdout)
            print("STDERR:\n", stderr)
            return False

        print("Installed application is running! Testing endpoints...")
        base_url = f"http://127.0.0.1:{port}"

        # 1. Health
        with urllib.request.urlopen(f"{base_url}/health", timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            assert data["status"] == "healthy"
            print("  [PASS] /health:", data)

        # 2. JSON Root
        req = urllib.request.Request(f"{base_url}/", headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            assert data["status"] == "online"
            print("  [PASS] JSON /:", data)

        # 3. HTML Dashboard
        req = urllib.request.Request(f"{base_url}/", headers={"Accept": "text/html"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            content = resp.read().decode("utf-8")
            assert 'id="root"' in content
            print("  [PASS] HTML Dashboard (bytes:", len(content), ")")

        # 4. Emergency Stop
        with urllib.request.urlopen(f"{base_url}/agent/emergency-stop/status", timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            assert data["is_active"] is False
            print("  [PASS] Emergency Stop Status: Normal")

        # 5. Tools
        with urllib.request.urlopen(f"{base_url}/agent/tools", timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            assert len(data) >= 30
            print(f"  [PASS] Tools count: {len(data)}")

        # 6. Database / Conversations
        with urllib.request.urlopen(f"{base_url}/agent/conversations", timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            assert isinstance(data, list)
            print("  [PASS] Database conversations accessible")

        return True

    finally:
        print("Stopping installed VoxPilot process...")
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        print("Process terminated.")


def main():
    sys.stdout.reconfigure(line_buffering=True)
    repo_root = Path(__file__).resolve().parent.parent
    installer_exe = repo_root / "installer" / "Output" / "VoxPilot-Setup.exe"

    print("=================================================================")
    print("      VOXPILOT INSTALLER (INNO SETUP) E2E VERIFICATION           ")
    print("=================================================================")

    # Step 1: Verify installer exists
    assert installer_exe.exists(), f"Installer missing: {installer_exe}"
    installer_size = installer_exe.stat().st_size
    print(f"1. Installer executable verified: {installer_exe} ({installer_size / (1024*1024):.2f} MB)")

    # Step 2: Test installation into a path with SPACES
    test_install_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "Temp" / "VoxPilot Custom Test Install"
    install_log = Path(os.environ.get("LOCALAPPDATA", "")) / "Temp" / "voxpilot_test_install.log"

    if test_install_dir.exists():
        shutil.rmtree(test_install_dir, ignore_errors=True)

    print(f"\n2. Installing to custom directory with spaces: {test_install_dir}")
    install_cmd = [
        str(installer_exe),
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/SP-",
        "/NORESTART",
        f'/DIR={str(test_install_dir)}',
        "/TASKS=desktopicon",
        f'/LOG={str(install_log)}',
    ]

    res = subprocess.run(install_cmd, capture_output=True, text=True)
    assert res.returncode == 0, f"Installer failed with return code {res.returncode}: {res.stderr}"
    print("   Installer exited with code 0 (success).")

    # Step 3: Verify installed file structure
    installed_exe = test_install_dir / "VoxPilot.exe"
    installed_browser_bat = test_install_dir / "install_browser.bat"
    installed_readme = test_install_dir / "README.txt"
    installed_uninstaller = test_install_dir / "unins000.exe"
    installed_internal = test_install_dir / "_internal"

    print("\n3. Verifying installed artifacts:")
    assert installed_exe.exists(), "VoxPilot.exe not found in install dir"
    print("   [x] VoxPilot.exe exists")
    assert installed_browser_bat.exists(), "install_browser.bat not found"
    print("   [x] install_browser.bat exists")
    assert installed_readme.exists(), "README.txt not found"
    print("   [x] README.txt exists")
    assert installed_uninstaller.exists(), "unins000.exe not found"
    print("   [x] unins000.exe exists")
    assert installed_internal.exists(), "_internal directory not found"
    print("   [x] _internal directory exists")

    # Step 4: Verify shortcuts
    appdata = Path(os.environ.get("APPDATA", ""))
    start_menu_group = appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "VoxPilot"
    desktop_dir = Path(os.environ.get("USERPROFILE", "")) / "Desktop"
    desktop_shortcut = desktop_dir / "VoxPilot.lnk"

    print("\n4. Verifying created shortcuts:")
    start_menu_exe = start_menu_group / "VoxPilot.lnk"
    start_menu_browser = start_menu_group / "Install Playwright Browser.lnk"
    start_menu_uninstall = start_menu_group / "Uninstall VoxPilot.lnk"

    assert start_menu_exe.exists(), f"Start Menu shortcut missing: {start_menu_exe}"
    print(f"   [x] Start Menu App shortcut: {start_menu_exe}")
    assert start_menu_browser.exists(), f"Start Menu browser setup shortcut missing: {start_menu_browser}"
    print(f"   [x] Start Menu Browser Setup shortcut: {start_menu_browser}")
    assert start_menu_uninstall.exists(), f"Start Menu Uninstaller shortcut missing: {start_menu_uninstall}"
    print(f"   [x] Start Menu Uninstaller shortcut: {start_menu_uninstall}")

    if desktop_shortcut.exists():
        print(f"   [x] Desktop shortcut: {desktop_shortcut}")
    else:
        print("   [-] Desktop shortcut: Not created (user opt-in task)")

    # Step 5: Test Execution of installed executable
    app_tested = test_installed_application(installed_exe, port=8003)
    assert app_tested is True, "Installed application test failed"

    # Step 6: Verify user data persistence
    local_appdata = Path(os.environ.get("LOCALAPPDATA", ""))
    user_db = local_appdata / "VoxPilot" / "data" / "voxpilot.db"
    user_log = local_appdata / "VoxPilot" / "logs" / "voxpilot.log"

    print("\n6. Verifying persistent user data:")
    assert user_db.exists(), f"User database missing: {user_db}"
    db_size_before = user_db.stat().st_size
    print(f"   [x] Database exists in %LOCALAPPDATA% ({db_size_before} bytes)")
    assert user_log.exists(), f"User log missing: {user_log}"
    print(f"   [x] User log exists in %LOCALAPPDATA% ({user_log.stat().st_size} bytes)")

    # Step 7: Upgrade simulation (Re-install over existing installation)
    print("\n7. Simulating application upgrade over existing test install...")
    res_upgrade = subprocess.run(install_cmd, capture_output=True, text=True)
    assert res_upgrade.returncode == 0, f"Upgrade failed: {res_upgrade.stderr}"
    assert user_db.exists(), "User database was lost during upgrade!"
    assert user_db.stat().st_size == db_size_before, "User database was modified/truncated during upgrade!"
    print("   [PASS] Upgrade completed smoothly; user database preserved intact!")

    # Step 8: Uninstall verification
    print(f"\n8. Uninstalling via {installed_uninstaller}...")
    uninstall_cmd = [
        str(installed_uninstaller),
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
    ]
    res_uninst = subprocess.run(uninstall_cmd, capture_output=True, text=True)
    assert res_uninst.returncode == 0, f"Uninstaller failed: {res_uninst.stderr}"
    time.sleep(2.0)  # Wait for file unlock and deletion

    # Verify program files removed
    assert not installed_exe.exists(), "VoxPilot.exe was NOT removed by uninstaller!"
    assert not start_menu_exe.exists(), "Start Menu shortcut was NOT removed!"
    print("   [x] Application binaries and shortcuts successfully removed.")

    # CRITICAL INVARIANT: Verify persistent user data was NOT deleted
    assert user_db.exists(), "CRITICAL FAILURE: Uninstaller deleted user database!"
    assert user_log.exists(), "CRITICAL FAILURE: Uninstaller deleted user log directory!"
    print(f"   [PASS] CRITICAL SAFETY INVARIANT PRESERVED: %LOCALAPPDATA%\\VoxPilot remains intact ({user_db.stat().st_size} bytes).")

    # Clean up test install directory if any empty root remains
    if test_install_dir.exists():
        shutil.rmtree(test_install_dir, ignore_errors=True)
    if desktop_shortcut.exists():
        try:
            desktop_shortcut.unlink()
        except Exception:
            pass

    print("\n=================================================================")
    print("   ALL INNO SETUP INSTALLER E2E VERIFICATIONS PASSED 100%!       ")
    print("=================================================================")


if __name__ == "__main__":
    main()
