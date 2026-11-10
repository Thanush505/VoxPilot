"""
VoxPilot - Safe Browser Automation Tool Interface
Provides Pydantic models and agent tool functions for safe, allowlisted,
isolated browser automation with consequential action guardrails.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field

from backend.automation.browser_controller import (
    BrowserController,
    BrowserControllerError,
    BrowserSessionError,
    UnsafeUrlError,
    UnsafeNavigationError,
    ElementNotFoundError,
    ElementInteractionError,
    BrowserTimeoutError,
    ConsequentialActionConfirmationError,
    DownloadSecurityError,
)
from backend.automation.confirmation_manager import (
    ConfirmationError,
    MissingUserApprovalError,
    get_shared_confirmation_manager,
)

logger = logging.getLogger(__name__)


# ==============================================================================
# Global Shared Controller Registry (Optional Singleton Pattern for Tool Calls)
# ==============================================================================

_SHARED_BROWSER_CONTROLLER: Optional[BrowserController] = None


def get_shared_browser_controller(
    approved_domains: Optional[List[str]] = None,
    headless: bool = True,
) -> BrowserController:
    """Retrieve or initialize the active shared BrowserController instance."""
    global _SHARED_BROWSER_CONTROLLER
    if _SHARED_BROWSER_CONTROLLER is None:
        _SHARED_BROWSER_CONTROLLER = BrowserController(
            approved_domains=approved_domains,
            headless=headless,
        )
    return _SHARED_BROWSER_CONTROLLER


def reset_shared_browser_controller() -> None:
    """Reset and close the shared BrowserController instance."""
    global _SHARED_BROWSER_CONTROLLER
    if _SHARED_BROWSER_CONTROLLER is not None:
        try:
            _SHARED_BROWSER_CONTROLLER.close_session()
        except Exception:
            pass
        _SHARED_BROWSER_CONTROLLER = None


# ==============================================================================
# Pydantic Input Schemas
# ==============================================================================

class StartBrowserInput(BaseModel):
    """Pydantic schema for starting an isolated browser session."""
    headless: bool = Field(
        True,
        description="Whether to run the browser in headless mode (default True).",
    )


class NavigateUrlInput(BaseModel):
    """Pydantic schema for navigating to a validated web URL."""
    url: str = Field(
        ...,
        description="Target HTTP or HTTPS URL to navigate to.",
        min_length=1,
    )


class GetPageInfoInput(BaseModel):
    """Pydantic schema for retrieving current page metadata."""
    pass


class GetPageTextInput(BaseModel):
    """Pydantic schema for extracting visible text from the active page."""
    max_length: int = Field(
        10000,
        description="Maximum characters of visible text to extract (100 to 50,000).",
        ge=100,
        le=50000,
    )


class ClickElementInput(BaseModel):
    """Pydantic schema for clicking an element on the active page."""
    selector: str = Field(
        ...,
        description="CSS selector, text pattern, or XPath identifying the element to click.",
        min_length=1,
    )
    timeout_ms: Optional[int] = Field(
        None,
        description="Optional interaction timeout in milliseconds.",
        ge=500,
        le=60000,
    )
    is_consequential: bool = Field(
        False,
        description="Whether this click causes an external consequence (submission, purchase, message).",
    )
    confirmed: bool = Field(
        False,
        description="Legacy user confirmation flag (ignored without approved confirmation_token).",
    )
    confirmation_token: Optional[str] = Field(
        None,
        description="Approved ConfirmationManager token for consequential actions.",
    )


class DownloadFileInput(BaseModel):
    """Pydantic schema for downloading a file via browser automation."""
    url: Optional[str] = Field(
        None,
        description="Direct HTTP/HTTPS URL of the file to download.",
    )
    selector: Optional[str] = Field(
        None,
        description="CSS selector or XPath identifying element to click to initiate download.",
    )
    filename: Optional[str] = Field(
        None,
        description="Optional custom filename to save download as. Must not contain directory separators.",
    )
    confirm_overwrite: bool = Field(
        False,
        description="Explicit confirmation to overwrite if file already exists in download folder.",
    )
    confirmation_token: Optional[str] = Field(
        None,
        description="Approved ConfirmationManager token for overwrite confirmation.",
    )
    timeout_ms: Optional[int] = Field(
        None,
        description="Optional timeout in milliseconds.",
        ge=1000,
        le=120000,
    )


class EnterTextInput(BaseModel):
    """Pydantic schema for typing text into a form input element."""
    selector: str = Field(
        ...,
        description="Selector identifying the input field.",
        min_length=1,
    )
    text: str = Field(
        ...,
        description="Text content to enter.",
    )
    clear_first: bool = Field(
        True,
        description="Whether to clear existing text in the input field before entering.",
    )
    timeout_ms: Optional[int] = Field(
        None,
        description="Optional interaction timeout in milliseconds.",
        ge=500,
        le=60000,
    )


class SelectDropdownInput(BaseModel):
    """Pydantic schema for selecting an option from a dropdown element."""
    selector: str = Field(
        ...,
        description="Selector identifying the dropdown <select> element.",
        min_length=1,
    )
    value: Optional[str] = Field(None, description="Option value attribute to select.")
    label: Optional[str] = Field(None, description="Visible option label to select.")
    index: Optional[int] = Field(None, description="0-based option index to select.", ge=0)


class ScrollPageInput(BaseModel):
    """Pydantic schema for scrolling the browser page."""
    direction: str = Field(
        "down",
        description="Scroll direction: 'up', 'down', 'top', or 'bottom'.",
    )
    amount: int = Field(
        500,
        description="Pixel delta to scroll for 'up' or 'down' (50 to 5,000).",
        ge=50,
        le=5000,
    )


class NavigateHistoryInput(BaseModel):
    """Pydantic schema for history navigation."""
    action: str = Field(
        "back",
        description="Navigation action: 'back', 'forward', or 'refresh'.",
    )


class CaptureBrowserScreenshotInput(BaseModel):
    """Pydantic schema for capturing a browser page screenshot."""
    filename: Optional[str] = Field(
        None,
        description="Optional screenshot filename (e.g. 'page.png').",
        max_length=100,
    )
    full_page: bool = Field(
        False,
        description="Whether to capture the entire scrollable height of the page.",
    )
    confirm_overwrite: bool = Field(
        False,
        description="Confirmation to overwrite if destination file already exists.",
    )


class CloseBrowserInput(BaseModel):
    """Pydantic schema for closing the browser session."""
    pass


# ==============================================================================
# Pydantic Result Schemas
# ==============================================================================

class BrowserActionResult(BaseModel):
    """Structured result returned by browser automation tools."""
    success: bool
    operation: str
    url: Optional[str] = None
    title: Optional[str] = None
    text: Optional[str] = None
    saved_path: Optional[str] = None
    requires_confirmation: bool = False
    confirmation_token: Optional[str] = None
    status: str
    message: str
    timestamp: str
    data: Optional[Dict[str, Any]] = None


# ==============================================================================
# Tool Functions
# ==============================================================================

def start_browser_tool(
    data: StartBrowserInput,
    controller: Optional[BrowserController] = None,
) -> BrowserActionResult:
    """Start an isolated browser session with an ephemeral profile."""
    ctrl = controller if controller is not None else get_shared_browser_controller(headless=data.headless)
    now_ts = datetime.now(timezone.utc).isoformat()

    try:
        res = ctrl.start_session()
        return BrowserActionResult(
            success=True,
            operation="start_browser",
            status="success",
            message="Isolated browser session started successfully.",
            timestamp=now_ts,
            data=res,
        )
    except BrowserSessionError as err:
        return BrowserActionResult(
            success=False,
            operation="start_browser",
            status="already_running" if "already active" in str(err) else "error",
            message=str(err),
            timestamp=now_ts,
        )
    except Exception as err:
        return BrowserActionResult(
            success=False,
            operation="start_browser",
            status="error",
            message=str(err),
            timestamp=now_ts,
        )


def navigate_url_tool(
    data: NavigateUrlInput,
    controller: Optional[BrowserController] = None,
) -> BrowserActionResult:
    """Navigate to a validated HTTP or HTTPS web URL."""
    ctrl = controller if controller is not None else get_shared_browser_controller()
    now_ts = datetime.now(timezone.utc).isoformat()

    try:
        res = ctrl.navigate(data.url)
        return BrowserActionResult(
            success=True,
            operation="navigate_url",
            url=res.get("final_url", data.url),
            title=res.get("title", ""),
            status="success",
            message=f"Successfully navigated to '{res.get('final_url', data.url)}'.",
            timestamp=now_ts,
            data=res,
        )
    except (UnsafeUrlError, UnsafeNavigationError) as err:
        return BrowserActionResult(
            success=False,
            operation="navigate_url",
            url=data.url,
            status="unsafe_url_blocked",
            message=str(err),
            timestamp=now_ts,
        )
    except BrowserTimeoutError as err:
        return BrowserActionResult(
            success=False,
            operation="navigate_url",
            url=data.url,
            status="timeout",
            message=str(err),
            timestamp=now_ts,
        )
    except BrowserControllerError as err:
        return BrowserActionResult(
            success=False,
            operation="navigate_url",
            url=data.url,
            status="error",
            message=str(err),
            timestamp=now_ts,
        )


def get_page_info_tool(
    data: Optional[GetPageInfoInput] = None,
    controller: Optional[BrowserController] = None,
) -> BrowserActionResult:
    """Retrieve metadata (URL, title, security) for current page."""
    ctrl = controller if controller is not None else get_shared_browser_controller()
    now_ts = datetime.now(timezone.utc).isoformat()

    try:
        info = ctrl.get_page_info()
        return BrowserActionResult(
            success=True,
            operation="get_page_info",
            url=info.url,
            title=info.title,
            status="success",
            message=f"Current page: '{info.title}' ({info.url})",
            timestamp=now_ts,
            data=info.to_dict(),
        )
    except BrowserControllerError as err:
        return BrowserActionResult(
            success=False,
            operation="get_page_info",
            status="error",
            message=str(err),
            timestamp=now_ts,
        )


def get_page_text_tool(
    data: Optional[GetPageTextInput] = None,
    controller: Optional[BrowserController] = None,
) -> BrowserActionResult:
    """Extract visible readable text from the active page."""
    ctrl = controller if controller is not None else get_shared_browser_controller()
    max_len = data.max_length if data is not None else 10000
    now_ts = datetime.now(timezone.utc).isoformat()

    try:
        res = ctrl.get_page_text(max_length=max_len)
        return BrowserActionResult(
            success=True,
            operation="get_page_text",
            url=res.get("url"),
            text=res.get("text"),
            status="success",
            message=f"Extracted {res.get('character_count', 0)} characters of visible text.",
            timestamp=now_ts,
            data=res,
        )
    except BrowserControllerError as err:
        return BrowserActionResult(
            success=False,
            operation="get_page_text",
            status="error",
            message=str(err),
            timestamp=now_ts,
        )


def click_element_tool(
    data: ClickElementInput,
    controller: Optional[BrowserController] = None,
) -> BrowserActionResult:
    """Click an element on the active page, enforcing consequential action confirmation."""
    ctrl = controller if controller is not None else get_shared_browser_controller()
    now_ts = datetime.now(timezone.utc).isoformat()

    # Guard consequential actions (e.g. form submission, purchase, messages)
    if data.is_consequential:
        cm = get_shared_confirmation_manager()
        # Enforce that LLM cannot self-confirm by passing confirmed=True without an approved token
        if not data.confirmation_token:
            pending = cm.create_confirmation(
                action_type="browser_consequential_click",
                action_params={"selector": data.selector},
                details={"selector": data.selector},
            )
            return BrowserActionResult(
                success=False,
                operation="click_element",
                requires_confirmation=True,
                confirmation_token=pending.token,
                status="confirmation_required",
                message=(
                    f"Consequential interaction with '{data.selector}' requires explicit user confirmation. "
                    f"Approval token: {pending.token}"
                ),
                timestamp=now_ts,
                data={"selector": data.selector, "confirmation_token": pending.token},
            )

        try:
            cm.validate_and_consume(
                token=data.confirmation_token,
                action_type="browser_consequential_click",
                action_params={"selector": data.selector},
            )
        except ConfirmationError as confirm_err:
            return BrowserActionResult(
                success=False,
                operation="click_element",
                requires_confirmation=True,
                confirmation_token=data.confirmation_token,
                status="confirmation_required" if isinstance(confirm_err, MissingUserApprovalError) else "confirmation_failed",
                message=str(confirm_err),
                timestamp=now_ts,
                data={"selector": data.selector},
            )

    try:
        res = ctrl.click_element(selector=data.selector, timeout_ms=data.timeout_ms)
        return BrowserActionResult(
            success=True,
            operation="click_element",
            status="success",
            message=f"Clicked element: '{data.selector}'.",
            timestamp=now_ts,
            data=res,
        )
    except ElementNotFoundError as err:
        return BrowserActionResult(
            success=False,
            operation="click_element",
            status="element_not_found",
            message=str(err),
            timestamp=now_ts,
        )
    except BrowserTimeoutError as err:
        return BrowserActionResult(
            success=False,
            operation="click_element",
            status="timeout",
            message=str(err),
            timestamp=now_ts,
        )
    except BrowserControllerError as err:
        return BrowserActionResult(
            success=False,
            operation="click_element",
            status="error",
            message=str(err),
            timestamp=now_ts,
        )


def enter_text_tool(
    data: EnterTextInput,
    controller: Optional[BrowserController] = None,
) -> BrowserActionResult:
    """Enter text into a form input element."""
    ctrl = controller if controller is not None else get_shared_browser_controller()
    now_ts = datetime.now(timezone.utc).isoformat()

    try:
        res = ctrl.enter_text(
            selector=data.selector,
            text=data.text,
            clear_first=data.clear_first,
            timeout_ms=data.timeout_ms,
        )
        return BrowserActionResult(
            success=True,
            operation="enter_text",
            status="success",
            message=f"Entered text into '{data.selector}'.",
            timestamp=now_ts,
            data=res,
        )
    except ElementNotFoundError as err:
        return BrowserActionResult(
            success=False,
            operation="enter_text",
            status="element_not_found",
            message=str(err),
            timestamp=now_ts,
        )
    except BrowserTimeoutError as err:
        return BrowserActionResult(
            success=False,
            operation="enter_text",
            status="timeout",
            message=str(err),
            timestamp=now_ts,
        )
    except BrowserControllerError as err:
        return BrowserActionResult(
            success=False,
            operation="enter_text",
            status="error",
            message=str(err),
            timestamp=now_ts,
        )


def select_dropdown_tool(
    data: SelectDropdownInput,
    controller: Optional[BrowserController] = None,
) -> BrowserActionResult:
    """Select an option from a dropdown element."""
    ctrl = controller if controller is not None else get_shared_browser_controller()
    now_ts = datetime.now(timezone.utc).isoformat()

    try:
        res = ctrl.select_dropdown(
            selector=data.selector,
            value=data.value,
            label=data.label,
            index=data.index,
        )
        return BrowserActionResult(
            success=True,
            operation="select_dropdown",
            status="success",
            message=f"Selected option in '{data.selector}'.",
            timestamp=now_ts,
            data=res,
        )
    except ElementNotFoundError as err:
        return BrowserActionResult(
            success=False,
            operation="select_dropdown",
            status="element_not_found",
            message=str(err),
            timestamp=now_ts,
        )
    except BrowserControllerError as err:
        return BrowserActionResult(
            success=False,
            operation="select_dropdown",
            status="error",
            message=str(err),
            timestamp=now_ts,
        )


def scroll_page_tool(
    data: ScrollPageInput,
    controller: Optional[BrowserController] = None,
) -> BrowserActionResult:
    """Scroll the page in specified direction."""
    ctrl = controller if controller is not None else get_shared_browser_controller()
    now_ts = datetime.now(timezone.utc).isoformat()

    try:
        res = ctrl.scroll_page(direction=data.direction, amount=data.amount)
        return BrowserActionResult(
            success=True,
            operation="scroll_page",
            status="success",
            message=f"Scrolled page {data.direction}.",
            timestamp=now_ts,
            data=res,
        )
    except BrowserControllerError as err:
        return BrowserActionResult(
            success=False,
            operation="scroll_page",
            status="error",
            message=str(err),
            timestamp=now_ts,
        )


def navigate_history_tool(
    data: NavigateHistoryInput,
    controller: Optional[BrowserController] = None,
) -> BrowserActionResult:
    """Navigate browser history backward, forward, or refresh."""
    ctrl = controller if controller is not None else get_shared_browser_controller()
    now_ts = datetime.now(timezone.utc).isoformat()

    try:
        res = ctrl.navigate_history(action=data.action)
        return BrowserActionResult(
            success=True,
            operation=f"navigate_{data.action}",
            url=res.get("final_url"),
            title=res.get("title"),
            status="success",
            message=f"Browser history action '{data.action}' completed.",
            timestamp=now_ts,
            data=res,
        )
    except BrowserControllerError as err:
        return BrowserActionResult(
            success=False,
            operation=f"navigate_{data.action}",
            status="error",
            message=str(err),
            timestamp=now_ts,
        )


def capture_browser_screenshot_tool(
    data: CaptureBrowserScreenshotInput,
    controller: Optional[BrowserController] = None,
) -> BrowserActionResult:
    """Capture a screenshot of the active browser page."""
    ctrl = controller if controller is not None else get_shared_browser_controller()
    now_ts = datetime.now(timezone.utc).isoformat()

    try:
        res = ctrl.capture_screenshot(
            filename=data.filename,
            full_page=data.full_page,
            confirm_overwrite=data.confirm_overwrite,
        )
        return BrowserActionResult(
            success=True,
            operation="capture_screenshot",
            saved_path=res.get("saved_path"),
            url=res.get("url"),
            status="success",
            message=f"Browser screenshot saved to '{res.get('saved_path')}'.",
            timestamp=now_ts,
            data=res,
        )
    except ConsequentialActionConfirmationError as confirm_err:
        return BrowserActionResult(
            success=False,
            operation="capture_screenshot",
            requires_confirmation=True,
            status="confirmation_required",
            message=str(confirm_err),
            timestamp=now_ts,
            data=confirm_err.details,
        )
    except BrowserControllerError as err:
        return BrowserActionResult(
            success=False,
            operation="capture_screenshot",
            status="error",
            message=str(err),
            timestamp=now_ts,
        )


def close_browser_tool(
    data: Optional[CloseBrowserInput] = None,
    controller: Optional[BrowserController] = None,
) -> BrowserActionResult:
    """Close active browser session and clean up processes."""
    global _SHARED_BROWSER_CONTROLLER
    ctrl = controller if controller is not None else _SHARED_BROWSER_CONTROLLER
    now_ts = datetime.now(timezone.utc).isoformat()

    if ctrl is None:
        return BrowserActionResult(
            success=True,
            operation="close_browser",
            status="success",
            message="No active browser session was open.",
            timestamp=now_ts,
        )

    try:
        res = ctrl.close_session()
        reset_shared_browser_controller()
        return BrowserActionResult(
            success=True,
            operation="close_browser",
            status="success",
            message="Browser session closed successfully.",
            timestamp=now_ts,
            data=res,
        )
    except Exception as err:
        reset_shared_browser_controller()
        return BrowserActionResult(
            success=True,
            operation="close_browser",
            status="closed_with_error",
            message=str(err),
            timestamp=now_ts,
        )


def download_file_tool(
    data: DownloadFileInput,
    controller: Optional[BrowserController] = None,
) -> BrowserActionResult:
    """Safely download a file using browser automation to the approved download folder."""
    ctrl = controller if controller is not None else get_shared_browser_controller()
    now_ts = datetime.now(timezone.utc).isoformat()

    try:
        res = ctrl.download_file(
            url=data.url,
            selector=data.selector,
            filename=data.filename,
            confirm_overwrite=data.confirm_overwrite,
            confirmation_token=data.confirmation_token,
            timeout_ms=data.timeout_ms,
        )
        return BrowserActionResult(
            success=True,
            operation="download_file",
            saved_path=res.get("path"),
            status="success",
            message=f"Downloaded '{res.get('filename')}' ({res.get('size_bytes', 0)} bytes).",
            timestamp=now_ts,
            data=res,
        )
    except ConsequentialActionConfirmationError as confirm_err:
        return BrowserActionResult(
            success=False,
            operation="download_file",
            requires_confirmation=True,
            confirmation_token=confirm_err.confirmation_token,
            status="confirmation_required",
            message=str(confirm_err),
            timestamp=now_ts,
            data=confirm_err.details,
        )
    except DownloadSecurityError as sec_err:
        return BrowserActionResult(
            success=False,
            operation="download_file",
            status="security_violation",
            message=str(sec_err),
            timestamp=now_ts,
        )
    except BrowserTimeoutError as err:
        return BrowserActionResult(
            success=False,
            operation="download_file",
            status="timeout",
            message=str(err),
            timestamp=now_ts,
        )
    except BrowserControllerError as err:
        return BrowserActionResult(
            success=False,
            operation="download_file",
            status="error",
            message=str(err),
            timestamp=now_ts,
        )
    except Exception as err:
        return BrowserActionResult(
            success=False,
            operation="download_file",
            status="system_error",
            message=f"Unexpected error downloading file: {err}",
            timestamp=now_ts,
        )


