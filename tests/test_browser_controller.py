"""
VoxPilot - Browser Controller Subsystem Unit and Security Tests
Tests browser session isolation, URL scheme filtering, SSRF private IP blocking,
approved domain policies, post-redirect verification, locator safety, bounded text extraction,
consequential action confirmation, and Pydantic tool interfaces.
"""

from __future__ import annotations

import http.server
import socket
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError

from backend.automation.browser_controller import (
    BrowserController,
    PageInfo,
    BrowserControllerError,
    BrowserSessionError,
    UnsafeUrlError,
    UnsafeNavigationError,
    ElementNotFoundError,
    ElementInteractionError,
    BrowserTimeoutError,
    ConsequentialActionConfirmationError,
    DownloadSecurityError,
    validate_target_url,
)
from backend.automation.confirmation_manager import (
    ConfirmationManager,
    get_shared_confirmation_manager,
)
from backend.tools.browser import (
    StartBrowserInput,
    NavigateUrlInput,
    GetPageTextInput,
    ClickElementInput,
    EnterTextInput,
    SelectDropdownInput,
    ScrollPageInput,
    NavigateHistoryInput,
    CaptureBrowserScreenshotInput,
    DownloadFileInput,
    start_browser_tool,
    navigate_url_tool,
    get_page_info_tool,
    get_page_text_tool,
    click_element_tool,
    enter_text_tool,
    select_dropdown_tool,
    scroll_page_tool,
    navigate_history_tool,
    capture_browser_screenshot_tool,
    close_browser_tool,
    download_file_tool,
)


@pytest.fixture
def test_dirs(tmp_path: Path):
    """Fixture providing isolated temporary directories for browser downloads and screenshots."""
    dl_dir = tmp_path / "browser_downloads"
    ss_dir = tmp_path / "browser_screenshots"
    dl_dir.mkdir(parents=True, exist_ok=True)
    ss_dir.mkdir(parents=True, exist_ok=True)
    return dl_dir, ss_dir


@pytest.fixture
def mock_playwright_stack():
    """Fixture providing a fully mocked Playwright browser/page hierarchy."""
    mock_pw = MagicMock()
    mock_browser = MagicMock()
    mock_context = MagicMock()
    mock_page = MagicMock()

    mock_pw.chromium.launch.return_value = mock_browser
    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.return_value = mock_page

    mock_page.is_closed.return_value = False
    mock_page.url = "https://example.com/welcome"
    mock_page.title.return_value = "Example Domain"

    return mock_pw, mock_browser, mock_context, mock_page


@pytest.fixture
def browser_controller(test_dirs, mock_playwright_stack):
    """Fixture providing an initialized BrowserController with mocked Playwright."""
    dl_dir, ss_dir = test_dirs
    mock_pw, _, _, _ = mock_playwright_stack

    ctrl = BrowserController(
        approved_domains=["example.com", "approved.org"],
        headless=True,
        download_directory=dl_dir,
        screenshot_directory=ss_dir,
    )

    with patch("backend.automation.browser_controller.sync_playwright") as mock_sync_pw:
        mock_sync_pw.return_value.start.return_value = mock_pw
        ctrl.start_session()

    return ctrl


# ==============================================================================
# URL Security & SSRF Validation Tests
# ==============================================================================

def test_validate_target_url_valid_https():
    """Verify standard valid public HTTPS URL passes validation."""
    url = "https://example.com/search?q=voxpilot"
    assert validate_target_url(url) == url


def test_validate_target_url_prohibited_schemes():
    """Verify non-HTTP/HTTPS schemes (file, javascript, data, chrome) are rejected."""
    prohibited = [
        "file:///C:/Windows/System32/cmd.exe",
        "javascript:alert(1)",
        "data:text/html,<h1>Hacked</h1>",
        "chrome://settings",
        "about:blank",
        "ws://example.com/socket",
        "ftp://ftp.example.com",
    ]
    for bad_url in prohibited:
        with pytest.raises(UnsafeUrlError) as exc_info:
            validate_target_url(bad_url)
        assert "scheme" in str(exc_info.value).lower() or "prohibited" in str(exc_info.value).lower()


def test_validate_target_url_localhost_blocked():
    """Verify localhost and loopback domains/IPs are blocked by default."""
    loopbacks = [
        "http://localhost",
        "http://localhost:8080/admin",
        "http://test.localhost",
        "http://127.0.0.1",
        "http://127.0.0.1:3000",
        "http://127.1.2.3",
        "http://[::1]/",
    ]
    for target in loopbacks:
        with pytest.raises(UnsafeUrlError) as exc_info:
            validate_target_url(target, allow_localhost=False)
        assert "localhost" in str(exc_info.value).lower() or "restricted" in str(exc_info.value).lower()


def test_validate_target_url_private_ips_blocked_ssrf():
    """Verify RFC 1918 private IP addresses are blocked to prevent SSRF attacks."""
    private_ips = [
        "http://10.0.0.1/admin",
        "http://10.255.255.255",
        "http://172.16.0.1",
        "http://172.31.255.255",
        "http://192.168.0.1/router",
        "http://192.168.1.254",
        "http://169.254.169.254/latest/meta-data/",  # Cloud metadata
        "http://169.254.1.1",  # Link-local
    ]
    for target in private_ips:
        with pytest.raises(UnsafeUrlError) as exc_info:
            validate_target_url(target)
        assert "private or restricted network" in str(exc_info.value).lower()


def test_validate_target_url_embedded_credentials_blocked():
    """Verify URLs with embedded credentials (user:pass@host) are blocked."""
    with pytest.raises(UnsafeUrlError) as exc_info:
        validate_target_url("http://admin:secret@example.com")
    assert "embedded credentials" in str(exc_info.value).lower()


def test_validate_target_url_approved_domains_enforcement():
    """Verify navigation is restricted to approved domains when policy is configured."""
    policy = ["example.com", "trusted-partner.org"]

    # Allowed
    assert validate_target_url("https://example.com/docs", approved_domains=policy)
    assert validate_target_url("https://sub.example.com/api", approved_domains=policy)
    assert validate_target_url("https://trusted-partner.org", approved_domains=policy)

    # Disallowed
    with pytest.raises(UnsafeUrlError) as exc_info:
        validate_target_url("https://malicious-site.com", approved_domains=policy)
    assert "not in approved domains policy" in str(exc_info.value)

    with pytest.raises(UnsafeUrlError):
        validate_target_url("https://notexample.com", approved_domains=policy)


def test_validate_target_url_nonstandard_ip_representations_blocked():
    """Verify octal, decimal integer, hex, and IPv4-mapped IPv6 representations are blocked."""
    unsafe_representations = [
        "http://0177.0.0.1",       # Octal loopback (127.0.0.1)
        "http://2130706433",       # Integer loopback (127.0.0.1)
        "http://0x7f000001",       # Hex loopback (127.0.0.1)
        "http://[::ffff:127.0.0.1]",  # IPv4-mapped IPv6 loopback
        "http://[::ffff:10.0.0.1]",   # IPv4-mapped IPv6 private network
    ]
    for target in unsafe_representations:
        with pytest.raises(UnsafeUrlError) as exc_info:
            validate_target_url(target)
        assert "private or restricted network" in str(exc_info.value).lower() or "blocked" in str(exc_info.value).lower()


def test_validate_target_url_dns_resolution_private_blocked():
    """Verify DNS resolution resolving to private or loopback addresses is blocked."""
    mock_addrinfo = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 0))]
    with patch("socket.getaddrinfo", return_value=mock_addrinfo):
        with pytest.raises(UnsafeUrlError) as exc_info:
            validate_target_url("http://rebinding-test.attacker.com")
        assert "private or restricted network" in str(exc_info.value).lower() or "ssrf" in str(exc_info.value).lower()


def test_validate_target_url_dns_resolution_cloud_metadata_blocked():
    """Verify DNS resolution resolving to cloud metadata 169.254.169.254 is blocked."""
    mock_addrinfo = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('169.254.169.254', 0))]
    with patch("socket.getaddrinfo", return_value=mock_addrinfo):
        with pytest.raises(UnsafeUrlError) as exc_info:
            validate_target_url("http://metadata.attacker.com")
        assert "private or restricted network" in str(exc_info.value).lower() or "ssrf" in str(exc_info.value).lower()


# ==============================================================================
# Browser Session Lifecycle Tests
# ==============================================================================

def test_start_session_isolated_context(test_dirs):
    """Verify starting session launches isolated Chromium with clean ephemeral context."""
    dl_dir, ss_dir = test_dirs
    ctrl = BrowserController(download_directory=dl_dir, screenshot_directory=ss_dir)

    with patch("backend.automation.browser_controller.sync_playwright") as mock_pw_start:
        mock_pw = MagicMock()
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_page = MagicMock()

        mock_pw_start.return_value.start.return_value = mock_pw
        mock_pw.chromium.launch.return_value = mock_browser
        mock_browser.new_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page
        mock_page.is_closed.return_value = False

        res = ctrl.start_session()
        assert res["success"] is True
        assert ctrl.is_running is True

        # Verify launch args include isolation flags
        launch_args = mock_pw.chromium.launch.call_args[1]["args"]
        assert "--disable-sync" in launch_args
        assert "--no-default-browser-check" in launch_args


def test_start_session_already_active_raises(browser_controller: BrowserController):
    """Verify starting a session when one is already running raises BrowserSessionError."""
    with pytest.raises(BrowserSessionError) as exc_info:
        browser_controller.start_session()
    assert "already active" in str(exc_info.value).lower()


def test_operation_without_session_raises(test_dirs):
    """Verify executing operations before start_session raises BrowserSessionError."""
    dl_dir, ss_dir = test_dirs
    ctrl = BrowserController(download_directory=dl_dir, screenshot_directory=ss_dir)
    with pytest.raises(BrowserSessionError):
        ctrl.navigate("https://example.com")
    with pytest.raises(BrowserSessionError):
        ctrl.get_page_info()


def test_close_session_cleans_up(browser_controller: BrowserController):
    """Verify close_session terminates page, context, browser, and playwright driver."""
    res = browser_controller.close_session()
    assert res["success"] is True
    assert browser_controller.is_running is False


# ==============================================================================
# Navigation & Post-Redirect Tests
# ==============================================================================

def test_navigate_success(browser_controller: BrowserController, mock_playwright_stack):
    """Verify navigating to a valid approved URL succeeds and returns page info."""
    _, _, _, mock_page = mock_playwright_stack
    mock_response = MagicMock()
    mock_response.status = 200
    mock_page.goto.return_value = mock_response
    mock_page.url = "https://example.com/dashboard"
    mock_page.title.return_value = "Dashboard"

    res = browser_controller.navigate("https://example.com/dashboard")
    assert res["success"] is True
    assert res["final_url"] == "https://example.com/dashboard"
    assert res["title"] == "Dashboard"
    assert res["status_code"] == 200


def test_navigate_redirect_to_unapproved_target_aborted(
    browser_controller: BrowserController,
    mock_playwright_stack,
):
    """Verify if a navigation redirects to an unapproved domain, it is blocked fail-closed."""
    _, _, _, mock_page = mock_playwright_stack
    mock_response = MagicMock()
    mock_response.status = 200
    mock_page.goto.return_value = mock_response

    # Simulated redirect lands on unapproved external domain
    mock_page.url = "https://unapproved-tracker.com/login"

    with pytest.raises(UnsafeNavigationError) as exc_info:
        browser_controller.navigate("https://example.com/redirect")

    assert "redirected to an unsafe or unapproved target" in str(exc_info.value).lower()
    # Verify page was evacuated to about:blank
    mock_page.goto.assert_called_with("about:blank", timeout=5000)


def test_navigate_timeout_handling(browser_controller: BrowserController, mock_playwright_stack):
    """Verify navigation timeout raises BrowserTimeoutError."""
    _, _, _, mock_page = mock_playwright_stack
    mock_page.goto.side_effect = PlaywrightTimeoutError("Navigation timeout")

    with pytest.raises(BrowserTimeoutError):
        browser_controller.navigate("https://example.com/slow")


# ==============================================================================
# Page Information & Text Extraction Tests
# ==============================================================================

def test_get_page_info(browser_controller: BrowserController, mock_playwright_stack):
    """Verify get_page_info returns accurate PageInfo dataclass."""
    _, _, _, mock_page = mock_playwright_stack
    mock_page.url = "https://example.com/settings"
    mock_page.title.return_value = "Settings Page"

    info = browser_controller.get_page_info()
    assert isinstance(info, PageInfo)
    assert info.url == "https://example.com/settings"
    assert info.title == "Settings Page"
    assert info.is_secure is True


def test_get_page_text_bounded(browser_controller: BrowserController, mock_playwright_stack):
    """Verify get_page_text extracts visible text and enforces maximum character limits."""
    _, _, _, mock_page = mock_playwright_stack
    long_text = "Word " * 5000  # 25,000 chars
    mock_page.locator.return_value.inner_text.return_value = long_text

    res = browser_controller.get_page_text(max_length=1000)
    assert res["success"] is True
    assert res["character_count"] == 1000
    assert res["truncated"] is True
    assert len(res["text"]) == 1000


# ==============================================================================
# Element Interaction Tests
# ==============================================================================

def test_click_element_success(browser_controller: BrowserController, mock_playwright_stack):
    """Verify click_element locates and clicks element."""
    _, _, _, mock_page = mock_playwright_stack
    mock_loc = MagicMock()
    mock_loc.count.return_value = 1
    mock_page.locator.return_value.first = mock_loc

    res = browser_controller.click_element("button#submit-btn")
    assert res["success"] is True
    assert res["selector"] == "button#submit-btn"
    mock_loc.click.assert_called_once()


def test_click_element_not_found(browser_controller: BrowserController, mock_playwright_stack):
    """Verify clicking missing element raises ElementNotFoundError."""
    _, _, _, mock_page = mock_playwright_stack
    mock_loc = MagicMock()
    mock_loc.count.return_value = 0
    mock_page.locator.return_value.first = mock_loc

    with pytest.raises(ElementNotFoundError):
        browser_controller.click_element("div.ghost")


def test_enter_text_success(browser_controller: BrowserController, mock_playwright_stack):
    """Verify enter_text fills input element with provided string."""
    _, _, _, mock_page = mock_playwright_stack
    mock_loc = MagicMock()
    mock_loc.count.return_value = 1
    mock_page.locator.return_value.first = mock_loc

    res = browser_controller.enter_text("input[name='q']", "VoxPilot Assistant", clear_first=True)
    assert res["success"] is True
    assert res["characters_entered"] == len("VoxPilot Assistant")
    mock_loc.fill.assert_called_with("VoxPilot Assistant", timeout=10000)


def test_select_dropdown_success(browser_controller: BrowserController, mock_playwright_stack):
    """Verify select_dropdown selects option by value or label."""
    _, _, _, mock_page = mock_playwright_stack
    mock_loc = MagicMock()
    mock_loc.count.return_value = 1
    mock_loc.select_option.return_value = ["option_b"]
    mock_page.locator.return_value.first = mock_loc

    res = browser_controller.select_dropdown("select#country", value="option_b")
    assert res["success"] is True
    assert res["selected_values"] == ["option_b"]


def test_scroll_page_success(browser_controller: BrowserController, mock_playwright_stack):
    """Verify scrolling page triggers mouse wheel event."""
    _, _, _, mock_page = mock_playwright_stack

    res_down = browser_controller.scroll_page(direction="down", amount=600)
    assert res_down["success"] is True
    mock_page.mouse.wheel.assert_called_with(0, 600)

    res_up = browser_controller.scroll_page(direction="up", amount=400)
    assert res_up["success"] is True
    mock_page.mouse.wheel.assert_called_with(0, -400)


def test_scroll_page_invalid_direction(browser_controller: BrowserController):
    """Verify invalid scroll direction raises ElementInteractionError."""
    with pytest.raises(ElementInteractionError):
        browser_controller.scroll_page(direction="sideways")


def test_navigate_history(browser_controller: BrowserController, mock_playwright_stack):
    """Verify history actions (back, forward, refresh)."""
    _, _, _, mock_page = mock_playwright_stack
    mock_page.url = "https://example.com/page2"

    res = browser_controller.navigate_history(action="back")
    assert res["success"] is True
    mock_page.go_back.assert_called_once()


# ==============================================================================
# Screenshot & Security Safeguard Tests
# ==============================================================================

def test_capture_screenshot_success(
    browser_controller: BrowserController,
    mock_playwright_stack,
    test_dirs,
):
    """Verify capturing browser screenshot saves file inside approved screenshot directory."""
    _, ss_dir = test_dirs
    _, _, _, mock_page = mock_playwright_stack

    def mock_screenshot_effect(path, full_page):
        Path(path).write_bytes(b"dummy_png_bytes")

    mock_page.screenshot.side_effect = mock_screenshot_effect

    res = browser_controller.capture_screenshot(filename="browser_capture.png")
    assert res["success"] is True
    assert Path(res["saved_path"]).is_relative_to(ss_dir)
    assert Path(res["saved_path"]).exists()


def test_capture_screenshot_overwrite_confirmation(
    browser_controller: BrowserController,
    test_dirs,
):
    """Verify targeting existing screenshot without confirm_overwrite raises confirmation error."""
    _, ss_dir = test_dirs
    existing = ss_dir / "target.png"
    existing.write_bytes(b"existing")

    with pytest.raises(ConsequentialActionConfirmationError) as exc_info:
        browser_controller.capture_screenshot(filename="target.png", confirm_overwrite=False)
    assert exc_info.value.requires_confirmation is True


def test_consequential_action_confirmation_guard(browser_controller: BrowserController):
    """Verify confirm_consequential_action blocks unconfirmed consequential operations."""
    with pytest.raises(ConsequentialActionConfirmationError) as exc_info:
        browser_controller.confirm_consequential_action(
            action_type="submit_payment",
            details={"target": "https://store.example.com"},
            confirmed=False,
        )
    assert exc_info.value.requires_confirmation is True

    # Confirmed does not raise
    browser_controller.confirm_consequential_action(
        action_type="submit_payment",
        details={"target": "https://store.example.com"},
        confirmed=True,
    )


# ==============================================================================
# Pydantic Tools Layer Tests
# ==============================================================================

def test_start_browser_tool(browser_controller: BrowserController):
    """Verify start_browser_tool returns structured BrowserActionResult."""
    data = StartBrowserInput(headless=True)
    res = start_browser_tool(data, controller=browser_controller)
    # Already running in fixture
    assert res.status == "already_running"


def test_navigate_url_tool_success(browser_controller: BrowserController, mock_playwright_stack):
    """Verify navigate_url_tool executes navigation."""
    _, _, _, mock_page = mock_playwright_stack
    mock_response = MagicMock()
    mock_response.status = 200
    mock_page.goto.return_value = mock_response
    mock_page.url = "https://example.com/articles"

    data = NavigateUrlInput(url="https://example.com/articles")
    res = navigate_url_tool(data, controller=browser_controller)

    assert res.success is True
    assert res.url == "https://example.com/articles"
    assert res.status == "success"


def test_navigate_url_tool_blocked_unsafe(browser_controller: BrowserController):
    """Verify navigate_url_tool returns unsafe_url_blocked for prohibited scheme."""
    data = NavigateUrlInput(url="file:///C:/Windows/win.ini")
    res = navigate_url_tool(data, controller=browser_controller)

    assert res.success is False
    assert res.status == "unsafe_url_blocked"
    assert "prohibited" in res.message.lower()


def test_click_element_tool_consequential_confirmation_preview(
    browser_controller: BrowserController,
):
    """Verify click_element_tool with is_consequential=True and confirmed=False returns confirmation_required."""
    data = ClickElementInput(
        selector="button#place-order",
        is_consequential=True,
        confirmed=False,
    )
    res = click_element_tool(data, controller=browser_controller)

    assert res.success is False
    assert res.requires_confirmation is True
    assert res.status == "confirmation_required"
    assert "explicit user confirmation" in res.message.lower()


def test_get_page_text_tool(browser_controller: BrowserController, mock_playwright_stack):
    """Verify get_page_text_tool extracts text."""
    _, _, _, mock_page = mock_playwright_stack
    mock_page.locator.return_value.inner_text.return_value = "Welcome to VoxPilot."

    data = GetPageTextInput(max_length=500)
    res = get_page_text_tool(data, controller=browser_controller)

    assert res.success is True
    assert res.text == "Welcome to VoxPilot."
    assert res.status == "success"


def test_close_browser_tool(browser_controller: BrowserController):
    """Verify close_browser_tool safely terminates session."""
    res = close_browser_tool(controller=browser_controller)
    assert res.success is True
    assert res.status == "success"
    assert browser_controller.is_running is False


# ==============================================================================
# Browser Download and Consequential Confirmation Tests
# ==============================================================================

def test_download_file_success(browser_controller: BrowserController, mock_playwright_stack):
    """Verify safe browser download saves file within approved download directory."""
    _, _, _, mock_page = mock_playwright_stack
    mock_download = MagicMock()
    mock_download.suggested_filename = "financial_report.pdf"

    def fake_save_as(path_str):
        p = Path(path_str)
        p.write_bytes(b"%PDF-1.4 simulated pdf content")

    mock_download.save_as.side_effect = fake_save_as

    mock_download_context = MagicMock()
    mock_download_context.value = mock_download
    mock_page.expect_download.return_value.__enter__.return_value = mock_download_context

    res = browser_controller.download_file(
        selector="#download-pdf",
        filename="report.pdf",
    )

    assert res["success"] is True
    assert res["operation"] == "download_file"
    assert res["filename"] == "report.pdf"
    assert Path(res["path"]).exists()
    assert res["size_bytes"] > 0
    assert res["status"] == "success"


def test_download_file_path_traversal_blocked(browser_controller: BrowserController):
    """Verify path traversal in custom download filename is blocked."""
    traversal_names = [
        "../escaped.pdf",
        "..\\escaped.pdf",
        "nested/sub/file.pdf",
        ".hidden_file.txt",
        "CON.txt",
    ]
    for bad_name in traversal_names:
        with pytest.raises(DownloadSecurityError):
            browser_controller.download_file(
                url="https://example.com/sample.pdf",
                filename=bad_name,
            )


def test_download_file_executable_extension_blocked(browser_controller: BrowserController):
    """Verify dangerous executable/script extensions are blocked from downloading."""
    prohibited_names = [
        "payload.exe",
        "script.bat",
        "command.cmd",
        "automation.ps1",
        "macro.vbs",
        "installer.msi",
        "library.dll",
    ]
    for bad_ext in prohibited_names:
        with pytest.raises(DownloadSecurityError) as exc_info:
            browser_controller.download_file(
                url="https://example.com/file",
                filename=bad_ext,
            )
        assert "prohibited" in str(exc_info.value).lower() or "hazardous" in str(exc_info.value).lower()


def test_download_file_overwrite_without_confirmation_raises(
    browser_controller: BrowserController,
    mock_playwright_stack,
):
    """Verify downloading to an existing file without confirmation raises ConsequentialActionConfirmationError."""
    _, _, _, mock_page = mock_playwright_stack
    mock_download = MagicMock()
    mock_download.suggested_filename = "existing.pdf"

    mock_download_context = MagicMock()
    mock_download_context.value = mock_download
    mock_page.expect_download.return_value.__enter__.return_value = mock_download_context

    # Pre-create existing file
    existing_file = browser_controller._download_directory / "existing.pdf"
    existing_file.write_text("prior content", encoding="utf-8")

    with pytest.raises(ConsequentialActionConfirmationError) as exc_info:
        browser_controller.download_file(
            selector="#download",
            filename="existing.pdf",
            confirm_overwrite=False,
        )

    assert exc_info.value.requires_confirmation is True
    assert exc_info.value.confirmation_token is not None
    assert mock_download.cancel.called


def test_download_file_overwrite_with_approved_token_succeeds(
    browser_controller: BrowserController,
    mock_playwright_stack,
):
    """Verify overwriting an existing download succeeds with an approved confirmation token."""
    _, _, _, mock_page = mock_playwright_stack
    mock_download = MagicMock()
    mock_download.suggested_filename = "existing.pdf"

    def fake_save_as(path_str):
        Path(path_str).write_bytes(b"overwritten content")

    mock_download.save_as.side_effect = fake_save_as

    mock_download_context = MagicMock()
    mock_download_context.value = mock_download
    mock_page.expect_download.return_value.__enter__.return_value = mock_download_context

    existing_file = browser_controller._download_directory / "existing.pdf"
    existing_file.write_text("old content", encoding="utf-8")

    # Generate and approve confirmation token
    cm = get_shared_confirmation_manager()
    pending = cm.create_confirmation(
        action_type="browser_download_overwrite",
        action_params={"destination_path": str(existing_file.resolve()), "filename": "existing.pdf"},
    )
    cm.approve_confirmation(pending.token)

    res = browser_controller.download_file(
        selector="#download",
        filename="existing.pdf",
        confirmation_token=pending.token,
    )

    assert res["success"] is True
    assert res["filename"] == "existing.pdf"
    assert existing_file.read_bytes() == b"overwritten content"


def test_download_file_tool_success(browser_controller: BrowserController, mock_playwright_stack):
    """Verify download_file_tool executes via controller and returns structured result."""
    _, _, _, mock_page = mock_playwright_stack
    mock_download = MagicMock()
    mock_download.suggested_filename = "data.csv"

    def fake_save_as(path_str):
        Path(path_str).write_bytes(b"id,name\n1,vox")

    mock_download.save_as.side_effect = fake_save_as

    mock_download_context = MagicMock()
    mock_download_context.value = mock_download
    mock_page.expect_download.return_value.__enter__.return_value = mock_download_context

    data = DownloadFileInput(
        url="https://example.com/data.csv",
        filename="data.csv",
    )
    res = download_file_tool(data, controller=browser_controller)

    assert res.success is True
    assert res.operation == "download_file"
    assert res.status == "success"
    assert "data.csv" in res.message


def test_click_element_tool_consequential_with_approved_token_succeeds(
    browser_controller: BrowserController,
    mock_playwright_stack,
):
    """Verify consequential click succeeds when a valid approved token is provided."""
    _, _, _, mock_page = mock_playwright_stack
    mock_loc = MagicMock()
    mock_loc.count.return_value = 1
    mock_page.locator.return_value.first = mock_loc

    selector = "button#confirm-checkout"

    cm = get_shared_confirmation_manager()
    pending = cm.create_confirmation(
        action_type="browser_consequential_click",
        action_params={"selector": selector},
    )
    cm.approve_confirmation(pending.token)

    data = ClickElementInput(
        selector=selector,
        is_consequential=True,
        confirmation_token=pending.token,
    )
    res = click_element_tool(data, controller=browser_controller)

    assert res.success is True
    assert res.status == "success"
    mock_loc.click.assert_called_once()


def test_click_element_tool_consequential_self_confirmation_prevented(
    browser_controller: BrowserController,
):
    """Verify that an LLM passing confirmed=True without an approved token is blocked."""
    data = ClickElementInput(
        selector="button#delete-account",
        is_consequential=True,
        confirmed=True,  # LLM attempts self-confirmation
        confirmation_token=None,
    )
    res = click_element_tool(data, controller=browser_controller)

    assert res.success is False
    assert res.requires_confirmation is True
    assert res.status == "confirmation_required"
    assert res.confirmation_token is not None


# ==============================================================================
# Live Controlled Browser Integration Test
# ==============================================================================

class _ControlledLocalHttpHandler(http.server.BaseHTTPRequestHandler):
    """Minimal in-process HTTP handler for safe, offline live browser integration tests."""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        html = """<!DOCTYPE html>
<html>
<head><title>VoxPilot Test Page</title></head>
<body>
  <h1>VoxPilot Safe Browser Verification</h1>
  <p id="description">Testing local browser automation safely.</p>
  <input type="text" id="test-input" name="test-input" />
  <button id="test-button" onclick="document.getElementById('result').innerText = 'Clicked Successfully';">Click Me</button>
  <div id="result">Initial State</div>
</body>
</html>"""
        self.wfile.write(html.encode("utf-8"))

    def log_message(self, format, *args):
        pass  # Suppress stderr noise in pytest


@pytest.mark.live
def test_live_browser_automation(tmp_path: Path):
    """Controlled live browser test launching real Chromium, performing safe interactions, and verifying cleanup."""
    server = http.server.HTTPServer(("127.0.0.1", 0), _ControlledLocalHttpHandler)
    port = server.server_port
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    dl_dir = tmp_path / "live_downloads"
    ss_dir = tmp_path / "live_screenshots"
    dl_dir.mkdir(parents=True, exist_ok=True)
    ss_dir.mkdir(parents=True, exist_ok=True)

    controller = BrowserController(
        headless=True,
        allow_localhost=True,
        download_directory=dl_dir,
        screenshot_directory=ss_dir,
    )

    try:
        # 1. Start session
        start_res = controller.start_session()
        assert start_res["success"] is True
        assert controller.is_running is True

        # 2. Navigate to local server
        test_url = f"http://127.0.0.1:{port}/"
        nav_res = controller.navigate(test_url)
        assert nav_res["success"] is True
        assert nav_res["title"] == "VoxPilot Test Page"
        assert nav_res["status_code"] == 200

        # 3. Read page text
        text_res = controller.get_page_text()
        assert text_res["success"] is True
        assert "VoxPilot Safe Browser Verification" in text_res["text"]

        # 4. Type into input
        type_res = controller.enter_text("#test-input", "Automated Typing Test")
        assert type_res["success"] is True

        # 5. Click button and verify state change
        click_res = controller.click_element("#test-button")
        assert click_res["success"] is True

        updated_text = controller.get_page_text()
        assert "Clicked Successfully" in updated_text["text"]

        # 6. Capture screenshot
        ss_res = controller.capture_screenshot(filename="live_browser_test.png")
        assert ss_res["success"] is True
        saved_file = Path(ss_res["saved_path"])
        assert saved_file.exists()
        assert saved_file.stat().st_size > 0

        # 7. Close session and verify complete cleanup
        close_res = controller.close_session()
        assert close_res["success"] is True
        assert controller.is_running is False
        assert controller._browser is None
        assert controller._playwright is None
    finally:
        if controller.is_running:
            controller.close_session()
        server.shutdown()
        server.server_close()

