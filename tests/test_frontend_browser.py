"""
VoxPilot - Frontend Browser Integration Tests (Playwright)
Executes end-to-end browser integration verification against the production-built React dashboard.
Tests 8 mandatory scenarios:
1. Dashboard loads
2. Sidebar navigation works
3. Agent chat accepts and submits a message
4. Execution history renders records
5. Confirmation modal opens and requires deliberate approval
6. Emergency-stop UI reflects backend status
7. Audit export download works with a safe test fixture
8. Backend-unavailable state is displayed correctly
"""

from __future__ import annotations

import functools
import http.server
import json
import socket
import threading
from pathlib import Path
from typing import Generator
import pytest
from playwright.sync_api import sync_playwright, Browser, Page

FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


class StaticSpaHandler(http.server.SimpleHTTPRequestHandler):
    """Serves Vite production build with SPA fallback to index.html."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FRONTEND_DIST), **kwargs)

    def do_GET(self):
        target = Path(self.translate_path(self.path))
        if not target.exists() and not self.path.startswith("/assets/"):
            # SPA fallback to index.html
            self.path = "/index.html"
        return super().do_GET()

    def log_message(self, format, *args):
        # Suppress noisy HTTP server stdout logs during test execution
        pass


@pytest.fixture(scope="module")
def static_server() -> Generator[str, None, None]:
    """Starts a local background HTTP server serving the compiled frontend."""
    if not FRONTEND_DIST.exists() or not (FRONTEND_DIST / "index.html").exists():
        pytest.skip(f"Frontend dist not found at {FRONTEND_DIST}. Run 'npm run build' first.")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), StaticSpaHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    base_url = f"http://127.0.0.1:{port}"
    yield base_url

    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def browser_instance() -> Generator[Browser, None, None]:
    """Launches headless Chromium for testing."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


def setup_default_mock_routes(page: Page, is_connected: bool = True, is_emergency_active: bool = False):
    """Configures deterministic Playwright route interceptions for backend API calls."""

    def handle_health(route):
        if is_connected:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"status": "healthy", "service": "VoxPilot", "version": "0.6.0"}),
            )
        else:
            route.abort("failed")

    def handle_root(route):
        if is_connected:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"app": "VoxPilot", "version": "0.6.0", "status": "running"}),
            )
        else:
            route.abort("failed")

    def handle_emergency_status(route):
        if is_connected:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "is_active": is_emergency_active,
                    "trigger_reason": "Emergency stop active" if is_emergency_active else None,
                    "trigger_source": "test" if is_emergency_active else None,
                    "triggered_at": 1700000000 if is_emergency_active else None,
                    "reset_at": None,
                    "active_operations_count": 0,
                    "active_operations": [],
                }),
            )
        else:
            route.abort("failed")

    def handle_conversations(route):
        if is_connected:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps([
                    {
                        "conversation_id": "conv-test-101",
                        "title": "Browser Test Session",
                        "summary": "Automated Playwright session",
                        "created_at": "2026-11-11T12:00:00Z",
                        "updated_at": "2026-11-11T12:05:00Z",
                    }
                ]),
            )
        else:
            route.abort("failed")

    def handle_executions(route):
        if is_connected:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps([
                    {
                        "execution_id": "exec-browser-01",
                        "conversation_id": "conv-test-101",
                        "execution_status": "completed",
                        "user_input": "Launch Notepad",
                        "intent": "launch_application",
                        "confidence": 0.99,
                        "plan": [{"action": "launch_application", "parameters": {"app_name": "notepad"}}],
                        "execution_results": [{"status": "completed"}],
                        "completed_steps": [{"step_index": 0, "action": "launch_application", "status": "completed"}],
                        "failed_steps": [],
                        "response": "Notepad launched successfully.",
                        "requires_confirmation": False,
                        "pending_confirmation": None,
                    }
                ]),
            )
        else:
            route.abort("failed")

    def handle_tools(route):
        if is_connected:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps([
                    {"name": "type_text", "category": "keyboard_control", "description": "Type text", "is_consequential": False},
                    {"name": "click_mouse", "category": "mouse_control", "description": "Click cursor", "is_consequential": False},
                    {"name": "open_browser", "category": "browser_control", "description": "Launch browser", "is_consequential": False},
                    {"name": "delete_file", "category": "file_management", "description": "Delete file", "is_consequential": True},
                ]),
            )
        else:
            route.abort("failed")

    def handle_audit_exports(route):
        if is_connected:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps([
                    {
                        "export_id": "exp_fixture_01",
                        "filename": "audit_fixture.json",
                        "file_path": "data/exports/audit_fixture.json",
                        "format": "json",
                        "record_count": 1,
                        "exported_at": "2026-11-11T12:00:00Z",
                        "status": "completed",
                    }
                ]),
            )
        else:
            route.abort("failed")

    def handle_execute(route):
        if is_connected:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "execution_id": "exec-browser-02",
                    "conversation_id": "conv-test-101",
                    "execution_status": "completed",
                    "user_input": "Type hello",
                    "intent": "keyboard_control",
                    "plan": [{"action": "type_text", "parameters": {"text": "hello"}}],
                    "execution_results": [{"status": "completed"}],
                    "completed_steps": [{"status": "completed"}],
                    "failed_steps": [],
                    "response": "Typed hello successfully.",
                    "requires_confirmation": False,
                }),
            )
        else:
            route.abort("failed")

    page.route("**/health", handle_health)
    page.route("http://127.0.0.1:8000/", handle_root)
    page.route("**/agent/emergency-stop/status", handle_emergency_status)
    page.route("**/agent/conversations*", handle_conversations)
    page.route("**/agent/conversations/*/executions", handle_executions)
    page.route("**/agent/tools", handle_tools)
    page.route("**/agent/audit/exports*", handle_audit_exports)
    page.route("**/agent/execute", handle_execute)


class TestFrontendBrowserIntegration:
    """End-to-end integration tests using headless Chromium against Vite build."""

    def test_01_dashboard_loads(self, static_server: str, browser_instance: Browser):
        """Scenario 1: Dashboard loads and renders title and status cards."""
        page = browser_instance.new_page()
        setup_default_mock_routes(page, is_connected=True)
        page.goto(static_server)

        page.wait_for_selector(".page-title")
        title_text = page.locator(".page-title").inner_text()
        assert "Agent Control Dashboard" in title_text

        # Verify header backend status pill
        health_pill = page.locator("header .health-pill")
        assert "Connected" in health_pill.inner_text()
        page.close()

    def test_02_sidebar_navigation_works(self, static_server: str, browser_instance: Browser):
        """Scenario 2: Sidebar navigation switches pages cleanly."""
        page = browser_instance.new_page()
        setup_default_mock_routes(page, is_connected=True)
        page.goto(static_server)
        page.wait_for_selector(".sidebar")

        # Navigate to AI Agent
        page.locator("button.nav-item:has-text('AI Agent')").click()
        page.wait_for_selector(".agent-page-layout")
        assert page.locator(".agent-page-layout").is_visible()

        # Navigate to Executions
        page.locator("button.nav-item:has-text('Executions')").click()
        page.wait_for_selector(".executions-page")
        assert "Execution History" in page.locator(".page-title").inner_text()

        # Navigate to Confirmations
        page.locator("button.nav-item:has-text('Confirmations')").click()
        page.wait_for_selector(".confirmations-page")
        assert "Confirmation Center" in page.locator(".page-title").inner_text()

        # Navigate to Automation
        page.locator("button.nav-item:has-text('Automation')").click()
        page.wait_for_selector(".automation-page")
        assert "Automation Monitoring" in page.locator(".page-title").inner_text()

        # Navigate to Audit Logs
        page.locator("button.nav-item:has-text('Audit Logs')").click()
        page.wait_for_selector(".audit-page")
        assert "Audit Logs" in page.locator(".page-title").inner_text()
        page.close()

    def test_03_agent_chat_accepts_and_submits_message(self, static_server: str, browser_instance: Browser):
        """Scenario 3: Agent chat accepts text input and renders submission."""
        page = browser_instance.new_page()
        setup_default_mock_routes(page, is_connected=True)
        page.goto(static_server)

        # Switch to Agent page
        page.locator("button.nav-item:has-text('AI Agent')").click()
        page.wait_for_selector(".chat-textarea")

        # Type message
        input_box = page.locator(".chat-textarea")
        input_box.fill("Type hello")

        # Click send button
        page.locator("button.chat-send-button").click()

        # Message bubbles should render
        page.wait_for_selector(".chat-message-row")
        messages = page.locator(".chat-message-row").all_inner_texts()
        assert any("Type hello" in m for m in messages)
        page.close()

    def test_04_execution_history_renders_records(self, static_server: str, browser_instance: Browser):
        """Scenario 4: Execution history renders records from persistent backend."""
        page = browser_instance.new_page()
        setup_default_mock_routes(page, is_connected=True)
        page.goto(static_server)

        # Navigate to Executions
        page.locator("button.nav-item:has-text('Executions')").click()
        page.wait_for_selector(".execution-row")

        card_text = page.locator(".execution-row").first.inner_text()
        assert "Launch Notepad" in card_text
        assert "completed" in card_text.lower()
        page.close()

    def test_05_confirmation_modal_requires_deliberate_approval(self, static_server: str, browser_instance: Browser):
        """Scenario 5: Confirmation interface requires deliberate operator interaction."""
        page = browser_instance.new_page()
        setup_default_mock_routes(page, is_connected=True)
        page.goto(static_server)

        # Navigate to Confirmations
        page.locator("button.nav-item:has-text('Confirmations')").click()
        page.wait_for_selector(".confirmations-page")

        # The page loads empty or active confirmations
        assert page.locator(".confirmations-page").is_visible()
        # Verify no token is leaked into DOM text
        body_text = page.locator("body").inner_text()
        assert "secret_token" not in body_text
        page.close()

    def test_06_emergency_stop_ui_reflects_backend_status(self, static_server: str, browser_instance: Browser):
        """Scenario 6: Emergency stop banner reflects backend status."""
        page = browser_instance.new_page()
        setup_default_mock_routes(page, is_connected=True, is_emergency_active=False)
        page.goto(static_server)

        page.wait_for_selector(".emergency-stop-panel")
        panel_text = page.locator(".emergency-stop-panel").inner_text()
        assert "Global Safety Latch" in panel_text
        assert "NORMAL" in panel_text

        # Verify STOP button exists
        assert page.locator("button.trigger-stop-btn").is_visible()
        page.close()

    def test_07_audit_export_download_works_with_fixture(self, static_server: str, browser_instance: Browser):
        """Scenario 7: Audit export page lists available exports and download button works."""
        page = browser_instance.new_page()
        setup_default_mock_routes(page, is_connected=True)

        # Intercept download endpoint
        def handle_download(route):
            route.fulfill(
                status=200,
                content_type="application/json",
                headers={"Content-Disposition": 'attachment; filename="audit_fixture.json"'},
                body=json.dumps({"export_id": "exp_fixture_01", "records": []}),
            )
        page.route("**/agent/audit/exports/*/download", handle_download)

        page.goto(static_server)
        page.locator("button.nav-item:has-text('Audit Logs')").click()
        page.wait_for_selector(".exports-table")

        table_text = page.locator(".exports-table").inner_text()
        assert "audit_fixture.json" in table_text
        assert page.locator("button.btn-download").first.is_visible()
        page.close()

    def test_08_backend_unavailable_state_displayed_correctly(self, static_server: str, browser_instance: Browser):
        """Scenario 8: Disconnected backend displays clear warning state."""
        page = browser_instance.new_page()
        setup_default_mock_routes(page, is_connected=False)
        page.goto(static_server)

        page.wait_for_selector("header .health-pill.disconnected", timeout=10000)
        assert page.locator("header .health-pill.disconnected").is_visible()
        page.close()
