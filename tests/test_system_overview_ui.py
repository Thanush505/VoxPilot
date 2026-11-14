"""
Playwright UI tests for SystemOverview hardware telemetry and Ollama model controls (Phase 1A).
"""

from __future__ import annotations

import json
import pytest
from playwright.sync_api import sync_playwright, Browser

from tests.test_frontend_browser import (
    FRONTEND_DIST,
    StaticSpaHandler,
    setup_default_mock_routes,
)


@pytest.fixture(scope="module")
def ui_static_server():
    import http.server
    import socket
    import threading

    if not FRONTEND_DIST.exists() or not (FRONTEND_DIST / "index.html").exists():
        pytest.skip("Frontend dist missing. Run 'npm run build' first.")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), StaticSpaHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def headless_browser():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


class TestSystemOverviewUI:
    def test_renders_live_hardware_and_ollama_models(self, ui_static_server: str, headless_browser: Browser):
        page = headless_browser.new_page()
        setup_default_mock_routes(page, is_connected=True)

        running_flag = {"loaded": False}

        def build_ollama():
            installed = [
                {
                    "name": "qwen3:4b",
                    "model": "qwen3:4b",
                    "size_bytes": 2497293931,
                    "size_gb": 2.33,
                    "digest": "abc",
                    "modified_at": None,
                    "family": "qwen3",
                    "families": ["qwen3"],
                    "parameter_size": "4.0B",
                    "quantization_level": "Q4_K_M",
                    "format": "gguf",
                }
            ]
            running = (
                [
                    {
                        "name": "qwen3:4b",
                        "model": "qwen3:4b",
                        "size_bytes": 2497293931,
                        "size_gb": 2.33,
                        "size_vram_bytes": 2497293931,
                        "size_vram_mb": 2381.6,
                        "vram_percent": 100.0,
                        "digest": "abc",
                        "expires_at": None,
                        "parameter_size": "4.0B",
                        "quantization_level": "Q4_K_M",
                        "context_length": 4096,
                    }
                ]
                if running_flag["loaded"]
                else []
            )
            return installed, running

        def handle_hardware(route):
            installed, running = build_ollama()
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "timestamp": "2026-11-14T18:00:00Z",
                    "os_name": "Windows",
                    "os_version": "10.0.26100",
                    "os_release": "11",
                    "machine": "AMD64",
                    "cpu": {
                        "utilization_percent": 18.5,
                        "logical_cores": 12,
                        "physical_cores": 8,
                        "frequency_mhz": 2500.0,
                        "min_frequency_mhz": 400.0,
                        "max_frequency_mhz": 4800.0,
                        "temperature_celsius": None,
                        "temperature_available": False,
                    },
                    "ram": {
                        "total_bytes": 17179869184,
                        "used_bytes": 8589934592,
                        "available_bytes": 8589934592,
                        "total_gb": 16.0,
                        "used_gb": 8.0,
                        "available_gb": 8.0,
                        "utilization_percent": 50.0,
                    },
                    "gpu": {
                        "available": True,
                        "name": "NVIDIA GeForce RTX 3050 6GB Laptop GPU",
                        "utilization_percent": 12.0,
                        "temperature_celsius": 49.5,
                        "vram_total_mb": 6144.0,
                        "vram_used_mb": 1651.0,
                        "vram_free_mb": 4351.0,
                        "vram_utilization_percent": 26.9,
                        "status_reason": None,
                    },
                    "ollama": {
                        "reachable": True,
                        "base_url": "http://127.0.0.1:11434",
                        "installed_models": installed,
                        "running_models": running,
                        "installed_count": len(installed),
                        "running_count": len(running),
                        "error": None,
                    },
                }),
            )

        def handle_models(route):
            installed, running = build_ollama()
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "reachable": True,
                    "base_url": "http://127.0.0.1:11434",
                    "default_model": "qwen3:4b",
                    "active_model": "qwen3:4b",
                    "installed_models": installed,
                    "running_models": running,
                    "installed_count": len(installed),
                    "running_count": len(running),
                    "error": None,
                }),
            )

        def handle_load(route):
            running_flag["loaded"] = True
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "success": True,
                    "action": "load",
                    "model": "qwen3:4b",
                    "active_model": "qwen3:4b",
                    "default_model": "qwen3:4b",
                    "already_loaded": False,
                    "unloaded_models": [],
                    "keep_alive": "15m",
                    "message": "Model 'qwen3:4b' loaded successfully.",
                    "timestamp": "2026-11-14T18:00:05Z",
                }),
            )

        page.route("**/api/system/hardware", handle_hardware)
        page.route("**/api/system/models", handle_models)
        page.route("**/api/system/models/load", handle_load)

        page.goto(ui_static_server)
        page.wait_for_selector(".ollama-runtime-panel")

        text = page.locator(".dashboard-card", has_text="Host Hardware & Local AI Runtime").inner_text()
        assert "18.5 %" in text
        assert "50.0 %" in text
        assert "49.5°C" in text
        assert "NVIDIA GeForce RTX 3050 6GB Laptop GPU" in text
        assert "26.9 %" in text
        assert "reachable" in text.lower()
        assert "qwen3:4b" in text

        # Click Load button and verify action banner updates
        page.locator("button.btn-model-action.load").click()
        page.wait_for_selector(".ollama-action-notice.success")
        notice_text = page.locator(".ollama-action-notice.success").inner_text()
        assert "loaded successfully" in notice_text
        page.close()

    def test_renders_unavailable_gpu_and_unreachable_ollama_gracefully(self, ui_static_server: str, headless_browser: Browser):
        page = headless_browser.new_page()
        setup_default_mock_routes(page, is_connected=True)

        def handle_hardware(route):
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "timestamp": "2026-11-14T18:00:00Z",
                    "os_name": "Windows",
                    "os_version": "10.0.26100",
                    "os_release": "11",
                    "machine": "AMD64",
                    "cpu": {
                        "utilization_percent": 5.0,
                        "logical_cores": 12,
                        "physical_cores": 8,
                        "frequency_mhz": 2200.0,
                        "temperature_celsius": None,
                        "temperature_available": False,
                    },
                    "ram": {
                        "total_bytes": 17179869184,
                        "used_bytes": 8589934592,
                        "available_bytes": 8589934592,
                        "total_gb": 16.0,
                        "used_gb": 8.0,
                        "available_gb": 8.0,
                        "utilization_percent": 50.0,
                    },
                    "gpu": {
                        "available": False,
                        "status_reason": "nvidia-smi utility not found on host.",
                    },
                    "ollama": {
                        "reachable": False,
                        "base_url": "http://127.0.0.1:11434",
                        "installed_models": [],
                        "running_models": [],
                        "installed_count": 0,
                        "running_count": 0,
                        "error": "Ollama connection refused.",
                    },
                }),
            )

        def handle_models(route):
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "reachable": False,
                    "base_url": "http://127.0.0.1:11434",
                    "default_model": "qwen3:4b",
                    "active_model": "qwen3:4b",
                    "installed_models": [],
                    "running_models": [],
                    "installed_count": 0,
                    "running_count": 0,
                    "error": "Ollama connection refused.",
                }),
            )

        page.route("**/api/system/hardware", handle_hardware)
        page.route("**/api/system/models", handle_models)

        page.goto(ui_static_server)
        page.wait_for_selector(".ollama-runtime-panel")

        text = page.locator(".dashboard-card", has_text="Host Hardware & Local AI Runtime").inner_text()
        assert "Unavailable" in text
        assert "nvidia-smi utility not found on host." in text
        assert "unreachable" in text.lower()
        assert "Ollama connection refused." in text
        page.close()
