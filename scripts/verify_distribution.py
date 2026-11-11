"""Verification script for VoxPilot standalone distribution."""

import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
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


def run_tests_against_server(port: int) -> dict:
    results = {}
    base_url = f"http://127.0.0.1:{port}"

    # 1. Health check
    try:
        req = urllib.request.Request(f"{base_url}/health")
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            assert data["status"] == "healthy"
            assert data["service"] == "VoxPilot"
            results["health"] = data
    except Exception as e:
        results["health_error"] = str(e)

    # 2. JSON Root check
    try:
        req = urllib.request.Request(f"{base_url}/", headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            assert data["status"] == "online"
            results["json_root"] = data
    except Exception as e:
        results["json_root_error"] = str(e)

    # 3. HTML Dashboard check
    try:
        req = urllib.request.Request(f"{base_url}/", headers={"Accept": "text/html"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            content = resp.read().decode("utf-8")
            assert "<title>VoxPilot" in content or "VoxPilot" in content
            assert 'id="root"' in content
            results["html_dashboard"] = {
                "status_code": resp.status,
                "content_length": len(content),
                "has_root_div": 'id="root"' in content,
            }
    except Exception as e:
        results["html_dashboard_error"] = str(e)

    # 4. Emergency stop status
    try:
        req = urllib.request.Request(f"{base_url}/agent/emergency-stop/status")
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results["emergency_stop"] = data
    except Exception as e:
        results["emergency_stop_error"] = str(e)

    # 5. Tools list
    try:
        req = urllib.request.Request(f"{base_url}/agent/tools")
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results["tools_count"] = len(data)
            results["sample_tools"] = [t["name"] for t in data[:5]]
    except Exception as e:
        results["tools_error"] = str(e)

    # 6. Database / Conversation history
    try:
        req = urllib.request.Request(f"{base_url}/agent/conversations")
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results["conversations_accessible"] = isinstance(data, list)
    except Exception as e:
        results["conversations_error"] = str(e)

    # 7. Voice Mode Status & Shortcut Config
    try:
        req = urllib.request.Request(f"{base_url}/agent/voice/status")
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            assert "phase" in data
            assert "shortcut_status" in data
            results["voice_status"] = {
                "phase": data.get("phase"),
                "shortcut": data["shortcut_status"].get("shortcut"),
                "shortcut_registered": data["shortcut_status"].get("is_registered"),
            }
    except Exception as e:
        results["voice_status_error"] = repr(e)

    # 8. Favicon & Brand Logo Assets Served
    try:
        with urllib.request.urlopen(f"{base_url}/favicon.ico", timeout=3.0) as resp:
            favicon_bytes = resp.read()
            assert resp.status == 200 and len(favicon_bytes) > 10_000
        with urllib.request.urlopen(f"{base_url}/voxpilot-logo.png", timeout=3.0) as resp:
            logo_bytes = resp.read()
            assert resp.status == 200 and len(logo_bytes) > 10_000
        results["branding_assets"] = {
            "favicon_bytes": len(favicon_bytes),
            "logo_bytes": len(logo_bytes),
        }
    except Exception as e:
        results["branding_assets_error"] = repr(e)

    # 8b. Version & Software Update System Endpoints
    try:
        with urllib.request.urlopen(f"{base_url}/api/version", timeout=3.0) as resp:
            ver_data = json.loads(resp.read().decode("utf-8"))
            assert ver_data.get("installed_version") == "0.5.1"
        with urllib.request.urlopen(f"{base_url}/api/updates/status", timeout=3.0) as resp:
            upd_data = json.loads(resp.read().decode("utf-8"))
            assert upd_data.get("installed_version") == "0.5.1"
            assert upd_data.get("auto_install_enabled") is False
        results["update_system"] = {
            "installed_version": ver_data.get("installed_version"),
            "state": upd_data.get("state"),
            "channel": upd_data.get("channel"),
            "release_repo": upd_data.get("release_repo"),
        }
    except Exception as e:
        results["update_system_error"] = repr(e)

    # 9. Agent Planner Smoke Test ("Open Notepad" -> Bug A & Bug B verification)
    try:
        payload = json.dumps({"user_input": "Open Notepad"}).encode("utf-8")
        req = urllib.request.Request(
            f"{base_url}/agent/execute",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Origin": "http://127.0.0.1:8000",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20.0) as resp:
            exec_data = json.loads(resp.read().decode("utf-8"))
            raw_plan = exec_data.get("plan", [])
            plan_steps = raw_plan.get("steps", []) if isinstance(raw_plan, dict) else raw_plan
            assert len(plan_steps) >= 1, f"Expected >= 1 plan step, got {plan_steps}"
            first_step = plan_steps[0]
            assert first_step.get("action") == "launch_application", f"Unexpected action: {first_step}"
            assert first_step.get("parameters", {}).get("application_name", "").lower() == "notepad"
            assert "application" not in first_step.get("parameters", {})
            results["planner_smoke_test"] = {
                "status": exec_data.get("execution_status"),
                "intent": exec_data.get("intent"),
                "first_step": first_step,
            }
            # Clean up any Notepad instance launched during smoke test
            subprocess.run(["taskkill", "/F", "/IM", "notepad.exe"], capture_output=True)
    except Exception as e:
        results["planner_smoke_test_error"] = repr(e)

    return results


def test_exe(exe_path: Path, port: int = 8001) -> bool:
    print(f"\n--- Testing Executable: {exe_path} on port {port} ---")
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
        ready = wait_for_server(health_url, timeout=45.0)
        if not ready:
            print("Server failed to respond within 45 seconds.")
            proc.terminate()
            stdout, stderr = proc.communicate(timeout=5.0)
            print("STDOUT:\n", stdout)
            print("STDERR:\n", stderr)
            return False

        print("Server is up and responding! Running test matrix...")
        results = run_tests_against_server(port)

        print("\nTest Results:")
        for k, v in results.items():
            print(f"  {k}: {v}")

        success = (
            "health" in results
            and "json_root" in results
            and "html_dashboard" in results
            and "emergency_stop" in results
            and "voice_status" in results
            and "branding_assets" in results
            and "update_system" in results
            and "planner_smoke_test" in results
            and results.get("tools_count", 0) >= 30
            and results.get("conversations_accessible") is True
        )
        return success

    finally:
        print("\nStopping VoxPilot process...")
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        print("Process terminated.")


def test_persistence_and_paths():
    print("\n--- Verifying %LOCALAPPDATA% Persistence and Paths ---")
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    voxpilot_dir = Path(local_app_data) / "VoxPilot"

    db_path = voxpilot_dir / "data" / "voxpilot.db"
    log_path = voxpilot_dir / "logs" / "voxpilot.log"

    print(f"Checking {db_path}...")
    assert db_path.exists(), f"Database file missing: {db_path}"
    print(f"  Database exists! Size: {db_path.stat().st_size} bytes")

    print(f"Checking {log_path}...")
    assert log_path.exists(), f"Log file missing: {log_path}"
    print(f"  Log file exists! Size: {log_path.stat().st_size} bytes")

    print("Persistence check passed successfully!")
    return True


def test_relocated_execution(original_dist: Path, temp_dir: Path) -> bool:
    print(f"\n--- Testing Relocated Distribution ---")
    print(f"Copying {original_dist} -> {temp_dir}...")
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    shutil.copytree(original_dist, temp_dir)

    relocated_exe = temp_dir / "VoxPilot.exe"
    try:
        success = test_exe(relocated_exe, port=8002)
        return success
    finally:
        print(f"Cleaning up {temp_dir}...")
        try:
            shutil.rmtree(temp_dir)
            print("Cleaned up successfully.")
        except Exception as e:
            print(f"Warning: Failed to clean up temp dir: {e}")


def main():
    repo_root = Path(__file__).resolve().parent.parent
    dist_dir = repo_root / "dist" / "VoxPilot"
    exe_path = dist_dir / "VoxPilot.exe"

    # Test 1: In-place distribution execution
    res1 = test_exe(exe_path, port=8001)
    if not res1:
        print("In-place distribution test failed!")
        sys.exit(1)

    # Test 2: Verify persistence
    res2 = test_persistence_and_paths()
    if not res2:
        print("Persistence check failed!")
        sys.exit(1)

    # Test 3: Relocated distribution execution
    temp_dist = Path(os.environ.get("TEMP", "C:/Windows/Temp")) / "VoxPilot_Relocated_Test"
    res3 = test_relocated_execution(dist_dir, temp_dist)
    if not res3:
        print("Relocated distribution test failed!")
        sys.exit(1)

    print("\n========================================================")
    print("ALL DISTRIBUTION AND RELOCATION TESTS PASSED SUCCESSFULLY!")
    print("========================================================")


if __name__ == "__main__":
    main()

