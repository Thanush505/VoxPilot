"""
VoxPilot - Screenshot Controller Subsystem Unit and Security Tests
Tests desktop capture, monitor selection, window capture, window state validation,
output path containment, filename sanitization, overwrite guards, and tool interfaces.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from PIL import Image

from backend.automation.window_manager import (
    WindowInfo,
    WindowManager,
    WindowNotFoundError,
    WindowNotAllowedError,
)
from backend.automation.screenshot_controller import (
    ScreenshotController,
    ScreenshotInfo,
    ScreenshotError,
    OutputDirectoryNotAllowedError,
    ScreenshotOverwriteError,
    WindowCaptureError,
    InvalidMonitorError,
    InvalidFilenameError,
)
from backend.tools.screenshots import (
    CaptureDesktopInput,
    CaptureMonitorInput,
    CaptureWindowInput,
    capture_desktop_tool,
    capture_primary_monitor_tool,
    capture_monitor_tool,
    capture_window_tool,
    list_monitors_tool,
)


@pytest.fixture
def test_screenshot_dir(tmp_path: Path) -> Path:
    """Fixture creating an isolated temporary approved directory for screenshots."""
    out_dir = tmp_path / "approved_screenshots"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


@pytest.fixture
def mock_dummy_image() -> Image.Image:
    """Fixture providing a mock 800x600 RGB PIL Image."""
    return Image.new("RGB", (800, 600), color="blue")


@pytest.fixture
def mock_window_manager() -> MagicMock:
    """Fixture providing a mocked WindowManager."""
    wm = MagicMock(spec=WindowManager)
    # Default valid window info
    wm.validate_window_ownership.return_value = None
    wm.is_window_responsive.return_value = True
    wm.get_window_info.return_value = WindowInfo(
        hwnd=12345,
        title="Untitled - Notepad",
        app_id="notepad",
        display_name="Notepad",
        pid=1001,
        process_name="notepad.exe",
        executable_path="C:\\Windows\\System32\\notepad.exe",
        is_visible=True,
        is_minimized=False,
        is_maximized=False,
        is_responsive=True,
        rect={"left": 100, "top": 100, "right": 900, "bottom": 700, "width": 800, "height": 600},
    )
    return wm


@pytest.fixture
def screenshot_controller(test_screenshot_dir: Path, mock_window_manager: MagicMock) -> ScreenshotController:
    """Fixture creating a ScreenshotController with temporary output directory and mock WindowManager."""
    return ScreenshotController(
        output_directory=test_screenshot_dir,
        window_manager=mock_window_manager,
    )


# ==============================================================================
# Initialization & Path Resolution Tests
# ==============================================================================

def test_screenshot_controller_initialization(
    screenshot_controller: ScreenshotController,
    test_screenshot_dir: Path,
):
    """Verify ScreenshotController initializes with canonical output directory."""
    assert screenshot_controller.output_directory == test_screenshot_dir.resolve()


def test_filename_traversal_rejected(screenshot_controller: ScreenshotController):
    """Verify filenames with directory traversal are rejected."""
    with pytest.raises(InvalidFilenameError):
        screenshot_controller._resolve_output_path(filename="../escaped.png")
    with pytest.raises(InvalidFilenameError):
        screenshot_controller._resolve_output_path(filename="sub/test.png")


def test_invalid_filename_extension_rejected(screenshot_controller: ScreenshotController):
    """Verify filenames with unsupported extensions or shell characters are rejected."""
    with pytest.raises(InvalidFilenameError):
        screenshot_controller._resolve_output_path(filename="script.exe")
    with pytest.raises(InvalidFilenameError):
        screenshot_controller._resolve_output_path(filename="image.bmp")
    with pytest.raises(InvalidFilenameError):
        screenshot_controller._resolve_output_path(filename="evil;calc.png")


def test_auto_generated_filename_is_safe(screenshot_controller: ScreenshotController, test_screenshot_dir: Path):
    """Verify auto-generated filename is inside approved directory with timestamp."""
    dest = screenshot_controller._resolve_output_path(filename=None, capture_type="desktop")
    assert dest.is_relative_to(test_screenshot_dir)
    assert dest.name.startswith("screenshot_desktop_")
    assert dest.suffix == ".png"


def test_overwrite_without_confirmation_rejected(
    screenshot_controller: ScreenshotController,
    test_screenshot_dir: Path,
):
    """Verify targeting an existing file without confirm_overwrite=True raises ScreenshotOverwriteError."""
    existing = test_screenshot_dir / "existing.png"
    existing.write_bytes(b"existing_bytes")

    with pytest.raises(ScreenshotOverwriteError) as exc_info:
        screenshot_controller._resolve_output_path(
            filename="existing.png",
            confirm_overwrite=False,
        )
    assert exc_info.value.requires_confirmation is True


def test_overwrite_with_confirmation_allowed(
    screenshot_controller: ScreenshotController,
    test_screenshot_dir: Path,
):
    """Verify targeting an existing file with confirm_overwrite=True succeeds."""
    existing = test_screenshot_dir / "existing.png"
    existing.write_bytes(b"existing_bytes")

    dest = screenshot_controller._resolve_output_path(
        filename="existing.png",
        confirm_overwrite=True,
    )
    assert dest == existing.resolve()


# ==============================================================================
# Desktop & Primary Monitor Capture Tests
# ==============================================================================

@patch("PIL.ImageGrab.grab")
def test_capture_desktop_success(
    mock_grab: MagicMock,
    screenshot_controller: ScreenshotController,
    mock_dummy_image: Image.Image,
    test_screenshot_dir: Path,
):
    """Verify capturing the full desktop saves image and returns accurate metadata."""
    mock_grab.return_value = mock_dummy_image

    info = screenshot_controller.capture_desktop(filename="desktop_capture.png")

    assert mock_grab.called
    assert mock_grab.call_args[1]["all_screens"] is True
    assert info.saved_path == str(test_screenshot_dir / "desktop_capture.png")
    assert info.image_width == 800
    assert info.image_height == 600
    assert info.capture_type == "desktop"
    assert info.size_bytes > 0
    assert Path(info.saved_path).exists()


@patch("PIL.ImageGrab.grab")
def test_capture_primary_monitor_success(
    mock_grab: MagicMock,
    screenshot_controller: ScreenshotController,
    mock_dummy_image: Image.Image,
):
    """Verify capturing the primary monitor queries primary bounding box and saves image."""
    mock_grab.return_value = mock_dummy_image

    with patch.object(screenshot_controller, "_get_primary_monitor_rect", return_value=(0, 0, 1920, 1080)):
        info = screenshot_controller.capture_primary_monitor(filename="primary.png")

        assert mock_grab.called
        assert mock_grab.call_args[1]["bbox"] == (0, 0, 1920, 1080)
        assert info.capture_type == "primary_monitor"


# ==============================================================================
# Monitor Selection Capture Tests
# ==============================================================================

@patch("PIL.ImageGrab.grab")
def test_capture_monitor_success(
    mock_grab: MagicMock,
    screenshot_controller: ScreenshotController,
    mock_dummy_image: Image.Image,
):
    """Verify capturing a specific monitor by index."""
    mock_grab.return_value = mock_dummy_image
    mock_monitors = [
        {"index": 1, "is_primary": True, "rect": {"left": 0, "top": 0, "right": 1920, "bottom": 1080, "width": 1920, "height": 1080}},
        {"index": 2, "is_primary": False, "rect": {"left": 1920, "top": 0, "right": 3840, "bottom": 1080, "width": 1920, "height": 1080}},
    ]

    with patch.object(screenshot_controller, "get_monitors_info", return_value=mock_monitors):
        info = screenshot_controller.capture_monitor(monitor_index=2, filename="mon2.png")

        assert mock_grab.call_args[1]["bbox"] == (1920, 0, 3840, 1080)
        assert info.capture_type == "monitor_2"


def test_capture_monitor_invalid_index(screenshot_controller: ScreenshotController):
    """Verify out-of-range monitor index raises InvalidMonitorError."""
    with patch.object(screenshot_controller, "get_monitors_info", return_value=[{"index": 1}]):
        with pytest.raises(InvalidMonitorError):
            screenshot_controller.capture_monitor(monitor_index=99)
        with pytest.raises(InvalidMonitorError):
            screenshot_controller.capture_monitor(monitor_index=0)


# ==============================================================================
# Window Capture & State Validation Tests
# ==============================================================================

@patch("PIL.ImageGrab.grab")
def test_capture_window_success(
    mock_grab: MagicMock,
    screenshot_controller: ScreenshotController,
    mock_dummy_image: Image.Image,
    mock_window_manager: MagicMock,
):
    """Verify capturing an approved, visible, responsive application window."""
    mock_grab.return_value = mock_dummy_image

    with patch("backend.automation.screenshot_controller.win32gui") as mock_w32:
        mock_w32.IsWindow.return_value = True
        with patch.object(screenshot_controller, "_get_window_bounds", return_value=(100, 100, 900, 700)):
            info = screenshot_controller.capture_window(target_hwnd=12345, filename="notepad.png")

            mock_window_manager.validate_window_ownership.assert_called_once_with(12345)
            mock_window_manager.is_window_responsive.assert_called_once_with(12345)
            assert mock_grab.call_args[1]["bbox"] == (100, 100, 900, 700)
            assert info.capture_type == "window"
            assert info.target_hwnd == 12345


def test_capture_window_invalid_hwnd(screenshot_controller: ScreenshotController):
    """Verify non-positive HWND raises WindowCaptureError."""
    with pytest.raises(WindowCaptureError):
        screenshot_controller.capture_window(target_hwnd=0)
    with pytest.raises(WindowCaptureError):
        screenshot_controller.capture_window(target_hwnd=-5)


def test_capture_window_unapproved_rejected(
    screenshot_controller: ScreenshotController,
    mock_window_manager: MagicMock,
):
    """Verify unapproved window raises WindowCaptureError fail-closed."""
    mock_window_manager.validate_window_ownership.side_effect = WindowNotAllowedError("Window not allowed")
    with pytest.raises(WindowCaptureError):
        screenshot_controller.capture_window(target_hwnd=999)


def test_capture_window_minimized_rejected(
    screenshot_controller: ScreenshotController,
    mock_window_manager: MagicMock,
):
    """Verify minimized window raises WindowCaptureError."""
    mock_window_manager.get_window_info.return_value = WindowInfo(
        hwnd=12345,
        title="Notepad",
        app_id="notepad",
        display_name="Notepad",
        pid=1001,
        process_name="notepad.exe",
        executable_path="C:\\Windows\\System32\\notepad.exe",
        is_visible=True,
        is_minimized=True,  # Minimized!
        is_maximized=False,
        is_responsive=True,
        rect={"left": -32000, "top": -32000, "right": -31840, "bottom": -31972, "width": 160, "height": 28},
    )
    with patch("backend.automation.screenshot_controller.win32gui") as mock_w32:
        mock_w32.IsWindow.return_value = True
        with pytest.raises(WindowCaptureError) as exc_info:
            screenshot_controller.capture_window(target_hwnd=12345)
        assert "minimized" in str(exc_info.value).lower()


def test_capture_window_hung_rejected(
    screenshot_controller: ScreenshotController,
    mock_window_manager: MagicMock,
):
    """Verify unresponsive/hung window raises WindowCaptureError."""
    mock_window_manager.is_window_responsive.return_value = False
    with patch("backend.automation.screenshot_controller.win32gui") as mock_w32:
        mock_w32.IsWindow.return_value = True
        with pytest.raises(WindowCaptureError) as exc_info:
            screenshot_controller.capture_window(target_hwnd=12345)
        assert "unresponsive" in str(exc_info.value).lower()


# ==============================================================================
# Tool Layer Tests
# ==============================================================================

@patch("PIL.ImageGrab.grab")
def test_capture_desktop_tool(
    mock_grab: MagicMock,
    screenshot_controller: ScreenshotController,
    mock_dummy_image: Image.Image,
):
    """Verify capture_desktop_tool returns structured ScreenshotActionResult."""
    mock_grab.return_value = mock_dummy_image
    data = CaptureDesktopInput(filename="tool_desktop.png")
    res = capture_desktop_tool(data, controller=screenshot_controller)

    assert res.success is True
    assert res.capture_type == "desktop"
    assert res.image_width == 800
    assert res.image_height == 600
    assert res.status == "success"
    assert res.saved_path is not None


@patch("PIL.ImageGrab.grab")
def test_capture_window_tool(
    mock_grab: MagicMock,
    screenshot_controller: ScreenshotController,
    mock_dummy_image: Image.Image,
):
    """Verify capture_window_tool executes and returns structured result."""
    mock_grab.return_value = mock_dummy_image
    with patch("backend.automation.screenshot_controller.win32gui") as mock_w32:
        mock_w32.IsWindow.return_value = True
        with patch.object(screenshot_controller, "_get_window_bounds", return_value=(0, 0, 800, 600)):
            data = CaptureWindowInput(hwnd=12345, filename="tool_win.png")
            res = capture_window_tool(data, controller=screenshot_controller)

            assert res.success is True
            assert res.capture_type == "window"
            assert res.target_hwnd == 12345
            assert res.status == "success"


def test_capture_tool_overwrite_confirmation_preview(
    screenshot_controller: ScreenshotController,
    test_screenshot_dir: Path,
):
    """Verify tool returns confirmation_required when overwriting an existing file without confirmation."""
    existing = test_screenshot_dir / "existing_screen.png"
    existing.write_bytes(b"content")

    data = CaptureDesktopInput(filename="existing_screen.png", confirm_overwrite=False)
    res = capture_desktop_tool(data, controller=screenshot_controller)

    assert res.success is False
    assert res.requires_confirmation is True
    assert res.status == "confirmation_required"
    assert "already exists" in res.message


def test_list_monitors_tool(screenshot_controller: ScreenshotController):
    """Verify list_monitors_tool returns monitor geometry."""
    with patch.object(screenshot_controller, "get_monitors_info", return_value=[{"index": 1, "is_primary": True}]):
        res = list_monitors_tool(controller=screenshot_controller)
        assert res.success is True
        assert res.status == "success"
        assert res.monitors is not None
        assert len(res.monitors) == 1

