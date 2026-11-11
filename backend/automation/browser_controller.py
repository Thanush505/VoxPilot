"""
VoxPilot - Safe Browser Automation Subsystem
Provides safe, allowlisted, isolated browser automation for Windows 11 using Playwright.
Enforces profile isolation, URL scheme and SSRF/private-network blocking, redirect verification,
locator safety, bounded text extraction, and consequential action confirmation.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any, Union
from urllib.parse import urlparse

from playwright.sync_api import (
    sync_playwright,
    Playwright,
    Browser,
    BrowserContext,
    Page,
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
)

from backend.automation.confirmation_manager import (
    ConfirmationManager,
    ConfirmationStatus,
    ConfirmationError,
    InvalidConfirmationError,
    MissingUserApprovalError,
    ConfirmationRejectedError,
    ConfirmationExpiredError,
    ReplayedConfirmationError,
    ActionMismatchConfirmationError,
    TamperedActionConfirmationError,
    get_shared_confirmation_manager,
)
from backend.automation.emergency_stop import (
    get_shared_emergency_stop_manager,
    EmergencyStopActiveError,
)

logger = logging.getLogger(__name__)


# ==============================================================================
# Security Boundaries and Default Policies
# ==============================================================================

ALLOWED_SCHEMES = {"http", "https"}

DEFAULT_NAVIGATION_TIMEOUT_MS = 30000
DEFAULT_ACTION_TIMEOUT_MS = 10000
MAX_EXTRACTED_TEXT_LENGTH = 10000

SAFE_FILENAME_PATTERN = re.compile(r"^[A-Za-z0-9_\-\.]+\.(png|jpg|jpeg|pdf|txt|html|json|csv)$", re.IGNORECASE)

PROHIBITED_DOWNLOAD_EXTENSIONS = {
    ".exe", ".bat", ".cmd", ".ps1", ".vbs", ".msi", ".dll",
    ".com", ".scr", ".sh", ".jar", ".reg", ".pif", ".cpl",
    ".hta", ".wsf", ".msp", ".gadget", ".app", ".vb", ".vbe",
    ".bin", ".iso", ".sys", ".drv"
}


def sanitize_download_filename(filename: str) -> str:
    """Sanitize and validate a filename for download safety.

    Prevents path traversal, path separators, hidden file dots,
    and Windows device names.
    """
    if not filename or not isinstance(filename, str) or not filename.strip():
        raise DownloadSecurityError("Download filename cannot be empty.")

    clean = filename.strip()
    if "/" in clean or "\\" in clean or ".." in clean:
        raise DownloadSecurityError(f"Download filename contains invalid path separators or traversal: '{filename}'")

    if clean.startswith("."):
        raise DownloadSecurityError(f"Download filename cannot start with a dot: '{filename}'")

    stem = clean.split(".")[0].upper()
    reserved_names = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}
    if stem in reserved_names:
        raise DownloadSecurityError(f"Download filename uses Windows reserved device name: '{clean}'")

    return clean


def get_default_browser_download_directory() -> Path:
    """Retrieve or create the approved download directory for browser automation."""
    downloads = Path.home() / "Downloads" / "VoxPilot_Downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    return downloads.resolve()


def get_default_browser_screenshot_directory() -> Path:
    """Retrieve or create the approved screenshot directory for browser captures."""
    pictures = Path.home() / "Pictures" / "VoxPilot_Screenshots"
    pictures.mkdir(parents=True, exist_ok=True)
    return pictures.resolve()


# ==============================================================================
# Exceptions
# ==============================================================================

class BrowserControllerError(Exception):
    """Base exception for all browser automation errors."""
    pass


class BrowserSessionError(BrowserControllerError):
    """Raised when browser session is not running, already active, or crashes."""
    pass


class UnsafeUrlError(BrowserControllerError):
    """Raised when a URL violates scheme, loopback, private-network, or domain policies."""
    pass


class UnsafeNavigationError(BrowserControllerError):
    """Raised when a navigation redirect targets an unapproved or unsafe address."""
    pass


class ElementNotFoundError(BrowserControllerError):
    """Raised when a locator fails to locate target element within timeout."""
    pass


class ElementInteractionError(BrowserControllerError):
    """Raised when an element cannot be clicked, filled, or interacted with."""
    pass


class BrowserTimeoutError(BrowserControllerError):
    """Raised when a page navigation or element interaction times out."""
    pass


class ConsequentialActionConfirmationError(BrowserControllerError):
    """Raised when an external or destructive action requires explicit confirmation."""
    def __init__(
        self,
        message: str,
        action_type: str,
        details: Dict[str, Any],
        requires_confirmation: bool = True,
        confirmation_token: Optional[str] = None,
    ):
        super().__init__(message)
        self.action_type = action_type
        self.details = details
        self.requires_confirmation = requires_confirmation
        self.confirmation_token = confirmation_token


class DownloadSecurityError(BrowserControllerError):
    """Raised when a download path violates approved directory containment or security rules."""
    pass


class BrowserBudgetExceededError(BrowserControllerError):
    """Raised when navigation or download quotas per session are exceeded."""
    pass


MAX_SESSION_NAVIGATIONS = 5
MAX_SESSION_DOWNLOADS = 3


# ==============================================================================
# Data Models
# ==============================================================================

@dataclass(frozen=True)
class PageInfo:
    """Metadata describing a browser page."""
    url: str
    title: str
    is_secure: bool
    status_code: Optional[int]
    timestamp: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "is_secure": self.is_secure,
            "status_code": self.status_code,
            "timestamp": self.timestamp,
        }


# ==============================================================================
# URL and Security Validation Engine
# ==============================================================================

def is_private_or_restricted_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Determine whether an IP address is private, loopback, link-local, or cloud metadata."""
    if ip.is_loopback:
        return True
    if ip.is_private:
        return True
    if ip.is_link_local:
        return True
    if ip.is_multicast or ip.is_reserved:
        return True

    # Cloud metadata endpoint (AWS, Azure, GCP, OpenStack: 169.254.169.254)
    if str(ip) == "169.254.169.254":
        return True

    # IPv4-mapped IPv6 address (e.g. ::ffff:127.0.0.1, ::ffff:10.0.0.1)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        if is_private_or_restricted_ip(ip.ipv4_mapped):
            return True

    return False


def resolve_and_verify_dns(hostname: str, allow_localhost: bool = False) -> None:
    """Resolve hostname using socket.getaddrinfo and verify that all resolved IPs are non-restricted.

    Protects against SSRF where domain names resolve to private networks, loopback,
    or cloud metadata endpoints.
    """
    if allow_localhost:
        return

    clean_host = hostname.strip("[]")

    # Check if host is direct IP literal first
    try:
        direct_ip = ipaddress.ip_address(clean_host)
        if is_private_or_restricted_ip(direct_ip):
            raise UnsafeUrlError(
                f"Access to private or restricted network IP '{clean_host}' is blocked for SSRF protection."
            )
        return
    except ValueError:
        pass

    # Resolve through DNS
    try:
        addr_infos = socket.getaddrinfo(clean_host, None)
    except socket.gaierror:
        # Hostname could not be resolved (offline or simulated test domain)
        return
    except Exception as err:
        logger.warning(f"Unexpected error during DNS resolution for '{hostname}': {err}")
        return

    for entry in addr_infos:
        sockaddr = entry[4]
        ip_str = sockaddr[0]
        try:
            ip_obj = ipaddress.ip_address(ip_str)
            if is_private_or_restricted_ip(ip_obj):
                raise UnsafeUrlError(
                    f"Access to '{hostname}' blocked: resolved to private or restricted network IP '{ip_str}' (SSRF protection)."
                )
        except ValueError:
            pass


def validate_target_url(
    url: str,
    approved_domains: Optional[List[str]] = None,
    allow_localhost: bool = False,
) -> str:
    """Validate a target URL against schemes, SSRF IP rules, and approved domains.

    Args:
        url: URL string to inspect.
        approved_domains: Optional list of approved domain suffixes (e.g. ['google.com', 'wikipedia.org']).
        allow_localhost: Whether localhost/private access is explicitly permitted (default False).

    Returns:
        str: Validated URL string.

    Raises:
        UnsafeUrlError: If scheme, host, or domain fails security checks.
    """
    if not url or not isinstance(url, str) or not url.strip():
        raise UnsafeUrlError("URL cannot be empty.")

    clean_url = url.strip()
    try:
        parsed = urlparse(clean_url)
    except Exception as err:
        raise UnsafeUrlError(f"Malformed URL: {err}") from err

    # 1. Scheme verification (http and https ONLY)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeUrlError(
            f"URL scheme '{scheme}://' is prohibited. Allowed schemes: {sorted(list(ALLOWED_SCHEMES))}."
        )

    # 2. Hostname extraction
    hostname = parsed.hostname
    if not hostname:
        raise UnsafeUrlError("URL must contain a valid hostname.")

    norm_host = hostname.lower()

    # 3. Reject credentials in URL (e.g. http://user:pass@example.com)
    if parsed.username or parsed.password:
        raise UnsafeUrlError("URLs containing embedded credentials (username/password) are prohibited.")

    # 4. Loopback, Localhost, IP and DNS check
    if not allow_localhost:
        if norm_host == "localhost" or norm_host.endswith(".localhost"):
            raise UnsafeUrlError(f"Access to localhost ('{norm_host}') is blocked by default security policy.")

        # Check for numeric or standard IP representation
        try:
            ip_obj = ipaddress.ip_address(norm_host.strip("[]"))
            if is_private_or_restricted_ip(ip_obj):
                raise UnsafeUrlError(
                    f"Access to private or restricted network IP '{norm_host}' is blocked for SSRF protection."
                )
        except ValueError:
            pass

        # Check integer or hex notation (e.g. 2130706433 or 0x7f000001)
        try:
            if norm_host.isdigit():
                num_val = int(norm_host)
                if 0 <= num_val <= 0xFFFFFFFF:
                    ip_obj = ipaddress.IPv4Address(num_val)
                    if is_private_or_restricted_ip(ip_obj):
                        raise UnsafeUrlError(
                            f"Access to private or restricted network IP '{norm_host}' ({ip_obj}) is blocked for SSRF protection."
                        )
            elif norm_host.startswith("0x") and all(c in "0123456789abcdef" for c in norm_host[2:]):
                num_val = int(norm_host, 16)
                if 0 <= num_val <= 0xFFFFFFFF:
                    ip_obj = ipaddress.IPv4Address(num_val)
                    if is_private_or_restricted_ip(ip_obj):
                        raise UnsafeUrlError(
                            f"Access to private or restricted network IP '{norm_host}' ({ip_obj}) is blocked for SSRF protection."
                        )
        except (ValueError, OverflowError):
            pass

        # Check dotted octal/decimal notation (e.g. 0177.0.0.1)
        if "." in norm_host and all(p.isdigit() for p in norm_host.split(".")):
            parts = norm_host.split(".")
            if len(parts) == 4:
                try:
                    parsed_bytes = [int(p, 8) if p.startswith("0") and len(p) > 1 else int(p, 10) for p in parts]
                    if all(0 <= b <= 255 for b in parsed_bytes):
                        parsed_ip = ipaddress.IPv4Address(bytes(parsed_bytes))
                        if is_private_or_restricted_ip(parsed_ip):
                            raise UnsafeUrlError(
                                f"Access to private or restricted network IP '{norm_host}' ({parsed_ip}) is blocked for SSRF protection."
                            )
                except (ValueError, OverflowError):
                    pass

        # Intranet / single-label domain check (e.g. http://internal/ or http://router/)
        if "." not in norm_host and norm_host != "localhost":
            raise UnsafeUrlError(
                f"Single-label intranet hostnames ('{norm_host}') are blocked by default security policy."
            )

        # Active DNS resolution check
        resolve_and_verify_dns(norm_host, allow_localhost=False)

    # 5. Approved domains policy enforcement
    if approved_domains is not None and len(approved_domains) > 0:
        matched = False
        for domain in approved_domains:
            norm_domain = domain.strip().lower()
            if norm_domain.startswith("."):
                norm_domain = norm_domain[1:]
            if norm_host == norm_domain or norm_host.endswith(f".{norm_domain}"):
                matched = True
                break

        if not matched:
            raise UnsafeUrlError(
                f"Domain '{norm_host}' is not in approved domains policy: {approved_domains}."
            )

    return clean_url


# ==============================================================================
# Browser Controller Class
# ==============================================================================

class BrowserController:
    """Safe Windows browser automation controller powered by Playwright.

    Enforces dedicated isolated browser profiles, URL validation and SSRF defenses,
    post-redirect verification, locator interaction guards, bounded text extraction,
    and consequential action confirmation.
    """

    def __init__(
        self,
        approved_domains: Optional[List[str]] = None,
        headless: bool = True,
        download_directory: Optional[Union[str, Path]] = None,
        screenshot_directory: Optional[Union[str, Path]] = None,
        timeout_ms: int = DEFAULT_NAVIGATION_TIMEOUT_MS,
        allow_localhost: bool = False,
    ) -> None:
        """Initialize BrowserController with security policies and timeouts.

        Args:
            approved_domains: Optional list of domains permitted for navigation.
            headless: Whether to run Chromium in headless mode (default True).
            download_directory: Approved folder for downloaded files.
            screenshot_directory: Approved folder for captured browser screenshots.
            timeout_ms: Default timeout in milliseconds for navigations.
            allow_localhost: Whether localhost/private network is permitted (default False).
        """
        self._approved_domains = [d.strip().lower() for d in approved_domains] if approved_domains else None
        self._headless = headless
        self._timeout_ms = timeout_ms
        self._allow_localhost = allow_localhost

        if download_directory is not None:
            self._download_directory = Path(download_directory).resolve()
            self._download_directory.mkdir(parents=True, exist_ok=True)
        else:
            self._download_directory = get_default_browser_download_directory()

        if screenshot_directory is not None:
            self._screenshot_directory = Path(screenshot_directory).resolve()
            self._screenshot_directory.mkdir(parents=True, exist_ok=True)
        else:
            self._screenshot_directory = get_default_browser_screenshot_directory()

        # Playwright session state
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._navigation_count: int = 0
        self._download_count: int = 0

        # Register emergency stop cleanup callback
        get_shared_emergency_stop_manager().register_cleanup_callback(self._emergency_cleanup)

        logger.info(
            f"BrowserController initialized. Headless: {self._headless}, "
            f"Approved domains: {self._approved_domains or 'All Public HTTP/HTTPS'}, "
            f"Download dir: {self._download_directory}"
        )

    def _emergency_cleanup(self) -> None:
        """Emergency stop cleanup handler to immediately close browser if running."""
        try:
            if self.is_running:
                logger.warning("Emergency stop active: terminating browser session.")
                self.close_session()
        except Exception as e:
            logger.debug(f"Error during browser emergency cleanup: {e}")

    @property
    def is_running(self) -> bool:
        """Check whether an active browser session is currently running."""
        return self._browser is not None and self._page is not None and not self._page.is_closed()

    @property
    def download_directory(self) -> Path:
        """Return canonical download directory."""
        return self._download_directory

    @property
    def screenshot_directory(self) -> Path:
        """Return canonical screenshot directory."""
        return self._screenshot_directory

    @property
    def navigation_count(self) -> int:
        """Return number of navigations performed in current session."""
        return self._navigation_count

    @property
    def download_count(self) -> int:
        """Return number of downloads performed in current session."""
        return self._download_count

    # --------------------------------------------------------------------------
    # 1. Start Browser Session
    # --------------------------------------------------------------------------

    def start_session(self) -> Dict[str, Any]:
        """Start a dedicated, isolated browser session with an ephemeral profile.

        Never attaches to the user's personal browser profiles or session cookies.

        Returns:
            Dict[str, Any]: Session initialization result.

        Raises:
            BrowserSessionError: If a session is already active or failed to start.
        """
        get_shared_emergency_stop_manager().check_emergency_stop()

        if self.is_running:
            raise BrowserSessionError(
                "A browser session is already active. Close current session before starting a new one."
            )

        self._navigation_count = 0
        self._download_count = 0

        try:
            logger.info("Starting isolated Playwright Chromium instance...")
            self._playwright = sync_playwright().start()

            # Launch clean isolated chromium instance
            self._browser = self._playwright.chromium.launch(
                headless=self._headless,
                args=[
                    "--no-default-browser-check",
                    "--disable-background-networking",
                    "--disable-sync",
                ],
            )

            # Create ephemeral incognito context (zero access to personal cookies or saved credentials)
            self._context = self._browser.new_context(
                accept_downloads=True,
                viewport={"width": 1280, "height": 800},
                user_agent="VoxPilot-Assistant/1.0 (Windows NT 10.0; Win64; x64)",
            )
            self._context.set_default_timeout(DEFAULT_ACTION_TIMEOUT_MS)
            self._context.set_default_navigation_timeout(self._timeout_ms)

            # Open initial page
            self._page = self._context.new_page()

            logger.info("Browser session started successfully in isolated profile.")
            return {
                "success": True,
                "operation": "start_session",
                "headless": self._headless,
                "status": "success",
            }
        except Exception as err:
            self.close_session()
            raise BrowserSessionError(f"Failed to start browser session: {err}") from err

    # --------------------------------------------------------------------------
    # 2. Close Browser Session Safely
    # --------------------------------------------------------------------------

    def close_session(self) -> Dict[str, Any]:
        """Safely close active browser session and cleanup all underlying processes."""
        logger.info("Closing browser session and cleaning up processes...")
        try:
            if self._page is not None:
                try:
                    self._page.close()
                except Exception:
                    pass
                self._page = None

            if self._context is not None:
                try:
                    self._context.close()
                except Exception:
                    pass
                self._context = None

            if self._browser is not None:
                try:
                    self._browser.close()
                except Exception:
                    pass
                self._browser = None

            if self._playwright is not None:
                try:
                    self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None

            self._navigation_count = 0
            self._download_count = 0

            return {
                "success": True,
                "operation": "close_session",
                "status": "success",
            }
        except Exception as err:
            logger.warning(f"Error during browser session cleanup: {err}")
            return {
                "success": True,
                "operation": "close_session",
                "status": "cleaned_with_warning",
                "error": str(err),
            }

    def _ensure_active_page(self) -> Page:
        """Verify session is active and return current Page object."""
        if not self.is_running or self._page is None:
            raise BrowserSessionError(
                "No active browser session. Call start_session() before performing browser operations."
            )
        return self._page

    # --------------------------------------------------------------------------
    # 3. Navigate to a URL with Pre- and Post-Redirect Validation
    # --------------------------------------------------------------------------

    def navigate(self, url: str) -> Dict[str, Any]:
        """Navigate to a target URL with pre-validation and post-redirect verification.

        Args:
            url: Target URL string.

        Returns:
            Dict[str, Any]: Navigation outcome with final URL and page title.

        Raises:
            UnsafeUrlError: If URL fails validation.
            UnsafeNavigationError: If a redirect landed on an unsafe destination.
            BrowserTimeoutError: If navigation timed out.
            BrowserControllerError: If navigation failed.
        """
        get_shared_emergency_stop_manager().check_emergency_stop()

        if self._navigation_count >= MAX_SESSION_NAVIGATIONS:
            raise BrowserBudgetExceededError(
                f"Browser session navigation limit exceeded: {self._navigation_count}/"
                f"{MAX_SESSION_NAVIGATIONS} navigations. Start a new session."
            )

        page = self._ensure_active_page()

        # 1. Pre-navigation validation
        validated_url = validate_target_url(
            url=url,
            approved_domains=self._approved_domains,
            allow_localhost=self._allow_localhost,
        )

        logger.info(f"Navigating to validated URL: {validated_url}")
        try:
            response = page.goto(validated_url, wait_until="load", timeout=self._timeout_ms)
            final_url = page.url

            # 2. Post-redirect validation: ensure redirect didn't land on an unapproved or internal URL
            try:
                validate_target_url(
                    url=final_url,
                    approved_domains=self._approved_domains,
                    allow_localhost=self._allow_localhost,
                )
            except UnsafeUrlError as redirect_err:
                # Redirect landed on an unsafe target; immediately evacuate to about:blank
                page.goto("about:blank", timeout=5000)
                raise UnsafeNavigationError(
                    f"Navigation redirected to an unsafe or unapproved target: {final_url} ({redirect_err})"
                ) from redirect_err

            status_code = response.status if response else 200
            title = page.title()
            self._navigation_count += 1

            return {
                "success": True,
                "operation": "navigate",
                "requested_url": validated_url,
                "final_url": final_url,
                "title": title,
                "status_code": status_code,
                "status": "success",
            }
        except PlaywrightTimeoutError as err:
            raise BrowserTimeoutError(f"Navigation to '{validated_url}' timed out: {err}") from err
        except PlaywrightError as err:
            if isinstance(err, (UnsafeUrlError, UnsafeNavigationError)):
                raise
            raise BrowserControllerError(f"Navigation error: {err}") from err

    # --------------------------------------------------------------------------
    # 4. Get Page Information (URL and Title)
    # --------------------------------------------------------------------------

    def get_page_info(self) -> PageInfo:
        """Retrieve current page URL, title, and security status."""
        page = self._ensure_active_page()
        now_ts = datetime.now(timezone.utc).isoformat()
        current_url = page.url
        title = page.title()
        is_secure = current_url.lower().startswith("https://")

        return PageInfo(
            url=current_url,
            title=title,
            is_secure=is_secure,
            status_code=None,
            timestamp=now_ts,
        )

    # --------------------------------------------------------------------------
    # 5. Extract Visible Page Text with Length Bounds
    # --------------------------------------------------------------------------

    def get_page_text(self, max_length: int = MAX_EXTRACTED_TEXT_LENGTH) -> Dict[str, Any]:
        """Extract visible readable text from the active page with strict length bounds.

        Args:
            max_length: Maximum characters to return (default 10,000).

        Returns:
            Dict[str, Any]: Text content and length statistics.
        """
        page = self._ensure_active_page()
        bounded_limit = max(100, min(max_length, 50000))

        try:
            # Extract innerText from body (clean visible text, excluding script and style tags)
            raw_text = page.locator("body").inner_text(timeout=DEFAULT_ACTION_TIMEOUT_MS)
            clean_text = " ".join(raw_text.split())
            truncated = len(clean_text) > bounded_limit
            final_text = clean_text[:bounded_limit]

            return {
                "success": True,
                "operation": "get_page_text",
                "text": final_text,
                "character_count": len(final_text),
                "total_available_characters": len(clean_text),
                "truncated": truncated,
                "url": page.url,
                "status": "success",
            }
        except PlaywrightTimeoutError as err:
            raise BrowserTimeoutError(f"Timed out extracting page text: {err}") from err
        except PlaywrightError as err:
            raise BrowserControllerError(f"Failed to extract page text: {err}") from err

    # --------------------------------------------------------------------------
    # 6 & 7. Safe Locator Lookup and Click
    # --------------------------------------------------------------------------

    def click_element(
        self,
        selector: str,
        timeout_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Click a visible element matched by a safe selector.

        Args:
            selector: CSS selector, text pattern, or XPath.
            timeout_ms: Optional custom timeout in milliseconds.

        Returns:
            Dict[str, Any]: Interaction outcome.
        """
        get_shared_emergency_stop_manager().check_emergency_stop()
        page = self._ensure_active_page()
        action_timeout = timeout_ms if timeout_ms is not None else DEFAULT_ACTION_TIMEOUT_MS

        if not selector or not selector.strip():
            raise ElementInteractionError("Selector cannot be empty.")

        try:
            loc = page.locator(selector).first
            if loc.count() == 0:
                raise ElementNotFoundError(f"Element matching selector '{selector}' was not found on page.")

            loc.click(timeout=action_timeout)
            logger.info(f"Clicked element: '{selector}'")
            return {
                "success": True,
                "operation": "click_element",
                "selector": selector,
                "status": "success",
            }
        except PlaywrightTimeoutError as err:
            raise BrowserTimeoutError(f"Timed out clicking element '{selector}': {err}") from err
        except PlaywrightError as err:
            raise ElementInteractionError(f"Failed to click element '{selector}': {err}") from err

    # --------------------------------------------------------------------------
    # 8. Enter Text into Input Field
    # --------------------------------------------------------------------------

    def enter_text(
        self,
        selector: str,
        text: str,
        clear_first: bool = True,
        timeout_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Enter text into a form input element.

        Args:
            selector: Selector identifying target input element.
            text: Text to enter.
            clear_first: Whether to clear existing content before typing.
            timeout_ms: Optional custom timeout.

        Returns:
            Dict[str, Any]: Interaction outcome.
        """
        get_shared_emergency_stop_manager().check_emergency_stop()
        page = self._ensure_active_page()
        action_timeout = timeout_ms if timeout_ms is not None else DEFAULT_ACTION_TIMEOUT_MS

        if not selector or not selector.strip():
            raise ElementInteractionError("Selector cannot be empty.")

        try:
            loc = page.locator(selector).first
            if loc.count() == 0:
                raise ElementNotFoundError(f"Element matching selector '{selector}' was not found on page.")

            if clear_first:
                loc.fill(text, timeout=action_timeout)
            else:
                loc.type(text, timeout=action_timeout)

            logger.info(f"Entered text ({len(text)} characters) into element: '{selector}'")
            return {
                "success": True,
                "operation": "enter_text",
                "selector": selector,
                "characters_entered": len(text),
                "status": "success",
            }
        except PlaywrightTimeoutError as err:
            raise BrowserTimeoutError(f"Timed out typing into element '{selector}': {err}") from err
        except PlaywrightError as err:
            raise ElementInteractionError(f"Failed to enter text into '{selector}': {err}") from err

    # --------------------------------------------------------------------------
    # 9. Select Dropdown Option
    # --------------------------------------------------------------------------

    def select_dropdown(
        self,
        selector: str,
        value: Optional[str] = None,
        label: Optional[str] = None,
        index: Optional[int] = None,
        timeout_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Select an option from a HTML <select> dropdown element.

        Args:
            selector: Selector identifying target dropdown element.
            value: Option value attribute to select.
            label: Visible option text to select.
            index: 0-based option index to select.
            timeout_ms: Optional timeout.

        Returns:
            Dict[str, Any]: Interaction outcome.
        """
        get_shared_emergency_stop_manager().check_emergency_stop()
        page = self._ensure_active_page()
        action_timeout = timeout_ms if timeout_ms is not None else DEFAULT_ACTION_TIMEOUT_MS

        if value is None and label is None and index is None:
            raise ElementInteractionError("Must provide at least one of value, label, or index for selection.")

        try:
            loc = page.locator(selector).first
            if loc.count() == 0:
                raise ElementNotFoundError(f"Dropdown element '{selector}' was not found on page.")

            if value is not None:
                selected = loc.select_option(value=value, timeout=action_timeout)
            elif label is not None:
                selected = loc.select_option(label=label, timeout=action_timeout)
            else:
                selected = loc.select_option(index=index, timeout=action_timeout)

            logger.info(f"Selected option in dropdown '{selector}': {selected}")
            return {
                "success": True,
                "operation": "select_dropdown",
                "selector": selector,
                "selected_values": selected,
                "status": "success",
            }
        except PlaywrightTimeoutError as err:
            raise BrowserTimeoutError(f"Timed out selecting option in dropdown '{selector}': {err}") from err
        except PlaywrightError as err:
            raise ElementInteractionError(f"Failed to select dropdown option in '{selector}': {err}") from err

    # --------------------------------------------------------------------------
    # 10. Scroll Page
    # --------------------------------------------------------------------------

    def scroll_page(
        self,
        direction: str = "down",
        amount: int = 500,
    ) -> Dict[str, Any]:
        """Scroll page viewport in specified direction.

        Args:
            direction: 'up', 'down', 'top', or 'bottom'.
            amount: Scroll delta in pixels (default 500).

        Returns:
            Dict[str, Any]: Scroll outcome.
        """
        get_shared_emergency_stop_manager().check_emergency_stop()
        page = self._ensure_active_page()
        norm_dir = direction.strip().lower()

        try:
            if norm_dir == "down":
                page.mouse.wheel(0, amount)
            elif norm_dir == "up":
                page.mouse.wheel(0, -amount)
            elif norm_dir == "top":
                page.evaluate("window.scrollTo(0, 0)")
            elif norm_dir == "bottom":
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            else:
                raise ElementInteractionError(
                    f"Invalid scroll direction '{direction}'. Allowed: 'up', 'down', 'top', 'bottom'."
                )

            return {
                "success": True,
                "operation": "scroll_page",
                "direction": norm_dir,
                "amount": amount,
                "status": "success",
            }
        except BrowserControllerError:
            raise
        except Exception as err:
            raise BrowserControllerError(f"Failed to scroll page: {err}") from err

    # --------------------------------------------------------------------------
    # 11 & 12. History Navigation and Refresh
    # --------------------------------------------------------------------------

    def navigate_history(self, action: str = "back") -> Dict[str, Any]:
        """Navigate browser history backward, forward, or reload.

        Args:
            action: 'back', 'forward', or 'refresh'.

        Returns:
            Dict[str, Any]: Navigation outcome.
        """
        get_shared_emergency_stop_manager().check_emergency_stop()
        page = self._ensure_active_page()
        norm_action = action.strip().lower()

        try:
            if norm_action == "back":
                page.go_back(timeout=self._timeout_ms)
            elif norm_action == "forward":
                page.go_forward(timeout=self._timeout_ms)
            elif norm_action in ("refresh", "reload"):
                page.reload(timeout=self._timeout_ms)
            else:
                raise BrowserControllerError(
                    f"Invalid history action '{action}'. Allowed: 'back', 'forward', 'refresh'."
                )

            # Revalidate post-navigation URL
            final_url = page.url
            try:
                validate_target_url(
                    url=final_url,
                    approved_domains=self._approved_domains,
                    allow_localhost=self._allow_localhost,
                )
            except UnsafeUrlError as err:
                page.goto("about:blank", timeout=5000)
                raise UnsafeNavigationError(f"History navigation landed on an unsafe destination: {err}") from err

            return {
                "success": True,
                "operation": f"navigate_{norm_action}",
                "final_url": final_url,
                "title": page.title(),
                "status": "success",
            }
        except PlaywrightTimeoutError as err:
            raise BrowserTimeoutError(f"History action '{action}' timed out: {err}") from err
        except PlaywrightError as err:
            raise BrowserControllerError(f"History action '{action}' failed: {err}") from err

    # --------------------------------------------------------------------------
    # 13. Capture Browser Page Screenshot
    # --------------------------------------------------------------------------

    def capture_screenshot(
        self,
        filename: Optional[str] = None,
        full_page: bool = False,
        confirm_overwrite: bool = False,
    ) -> Dict[str, Any]:
        """Capture screenshot of the current browser page.

        Args:
            filename: Optional destination filename (must end with .png or .jpg).
            full_page: Whether to capture full scrollable height.
            confirm_overwrite: Required if destination file already exists.

        Returns:
            Dict[str, Any]: Saved screenshot metadata.
        """
        get_shared_emergency_stop_manager().check_emergency_stop()
        page = self._ensure_active_page()
        now = datetime.now(timezone.utc)

        if filename is None or not filename.strip():
            ts_str = now.strftime("%Y%m%d_%H%M%S_%f")
            safe_name = f"browser_screenshot_{ts_str}.png"
        else:
            clean_name = filename.strip()
            if "/" in clean_name or "\\" in clean_name or ".." in clean_name:
                raise DownloadSecurityError(f"Screenshot filename cannot contain path separators: {filename!r}")
            if not SAFE_FILENAME_PATTERN.match(clean_name):
                raise DownloadSecurityError(f"Invalid filename '{filename}'. Supported extensions: .png, .jpg, .jpeg.")
            safe_name = clean_name

        dest_path = (self._screenshot_directory / safe_name).resolve()

        # Verify containment
        try:
            if not dest_path.is_relative_to(self._screenshot_directory):
                raise DownloadSecurityError("Screenshot destination escapes approved screenshot directory.")
        except (ValueError, TypeError) as err:
            raise DownloadSecurityError(f"Containment check failed: {err}") from err

        if dest_path.exists() and not confirm_overwrite:
            raise ConsequentialActionConfirmationError(
                message=f"Screenshot file '{dest_path.name}' already exists. Overwrite requires confirmation.",
                action_type="screenshot_overwrite",
                details={"target_path": str(dest_path)},
                requires_confirmation=True,
            )

        try:
            page.screenshot(path=str(dest_path), full_page=full_page)
            size_bytes = dest_path.stat().st_size

            logger.info(f"Browser screenshot saved: {dest_path} ({size_bytes} bytes).")
            return {
                "success": True,
                "operation": "capture_screenshot",
                "saved_path": str(dest_path),
                "size_bytes": size_bytes,
                "full_page": full_page,
                "url": page.url,
                "status": "success",
            }
        except Exception as err:
            raise BrowserControllerError(f"Failed to capture browser screenshot: {err}") from err

    # --------------------------------------------------------------------------
    # 14. Consequential Actions Safeguards
    # --------------------------------------------------------------------------

    def confirm_consequential_action(
        self,
        action_type: str,
        details: Dict[str, Any],
        confirmed: bool = False,
        confirmation_token: Optional[str] = None,
        confirmation_manager: Optional[ConfirmationManager] = None,
    ) -> None:
        """Enforce confirmation guardrails for actions that cause external consequences.

        Blocks unconfirmed execution for submissions, emails, purchases, account changes, etc.

        Args:
            action_type: Type of consequential action (e.g. 'submit_form', 'send_message').
            details: Action parameters and target URL.
            confirmed: Explicit confirmation flag.
            confirmation_token: Validated user confirmation token.
            confirmation_manager: Optional custom ConfirmationManager instance.

        Raises:
            ConsequentialActionConfirmationError: If confirmed is False or token is invalid/unapproved.
        """
        cm = confirmation_manager or get_shared_confirmation_manager()
        if confirmation_token:
            cm.validate_and_consume(
                token=confirmation_token,
                action_type=action_type,
                action_params=details,
            )
            return

        if not confirmed:
            pending = cm.create_confirmation(
                action_type=action_type,
                action_params=details,
                details=details,
            )
            raise ConsequentialActionConfirmationError(
                message=(
                    f"External consequential action '{action_type}' requires explicit user confirmation "
                    f"before execution. Confirmation token: {pending.token}"
                ),
                action_type=action_type,
                details={**details, "confirmation_token": pending.token},
                requires_confirmation=True,
                confirmation_token=pending.token,
            )

    # --------------------------------------------------------------------------
    # 15. File Download Management
    # --------------------------------------------------------------------------

    def download_file(
        self,
        url: Optional[str] = None,
        selector: Optional[str] = None,
        filename: Optional[str] = None,
        confirm_overwrite: bool = False,
        confirmation_token: Optional[str] = None,
        timeout_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Safely download a file via browser to the approved download directory.

        Downloads are strictly confined to the approved VoxPilot download directory.
        Path traversal, directory escape, Windows device names, and executable/script
        extensions (.exe, .bat, .cmd, .ps1, .vbs, .msi, .dll, etc.) are blocked.
        Downloaded files are never executed.

        Args:
            url: Optional direct URL to initiate download from.
            selector: Optional element selector to click to trigger download.
            filename: Optional custom filename to save download as.
            confirm_overwrite: Explicit confirmation if destination file already exists.
            confirmation_token: Approved ConfirmationManager token for overwrite.
            timeout_ms: Optional download timeout in milliseconds.

        Returns:
            Dict[str, Any]: Saved download metadata including path, filename, and size.

        Raises:
            BrowserControllerError: If neither url nor selector is provided, or download fails.
            DownloadSecurityError: If filename, path traversal, or dangerous extension detected.
            ConsequentialActionConfirmationError: If file exists and overwrite is unconfirmed.
            BrowserTimeoutError: If download fails to initiate within timeout.
        """
        get_shared_emergency_stop_manager().check_emergency_stop()

        if self._download_count >= MAX_SESSION_DOWNLOADS:
            raise BrowserBudgetExceededError(
                f"Browser session download limit exceeded: {self._download_count}/"
                f"{MAX_SESSION_DOWNLOADS} downloads. Session quota reached."
            )

        page = self._ensure_active_page()
        effective_timeout = timeout_ms if timeout_ms is not None else self._timeout_ms

        if not url and not selector:
            raise BrowserControllerError("Either 'url' or 'selector' must be provided to initiate a download.")

        # Pre-validate custom filename if specified
        target_name: Optional[str] = None
        if filename:
            target_name = sanitize_download_filename(filename)
            ext = Path(target_name).suffix.lower()
            if ext in PROHIBITED_DOWNLOAD_EXTENSIONS:
                raise DownloadSecurityError(
                    f"Downloading executable or hazardous file extension '{ext}' is prohibited."
                )

        try:
            with page.expect_download(timeout=effective_timeout) as download_info:
                if selector:
                    logger.info(f"Triggering download via click on locator: '{selector}'")
                    page.locator(selector).click(timeout=DEFAULT_ACTION_TIMEOUT_MS)
                elif url:
                    validated_url = validate_target_url(
                        url,
                        approved_domains=self._approved_domains,
                        allow_localhost=self._allow_localhost,
                    )
                    logger.info(f"Triggering download via navigation to: '{validated_url}'")
                    try:
                        page.goto(validated_url, timeout=effective_timeout)
                    except PlaywrightError as nav_err:
                        # Chromium may raise ERR_ABORTED when navigation is handled as download
                        if "ERR_ABORTED" not in str(nav_err) and "download" not in str(nav_err).lower():
                            raise

            download = download_info.value
        except PlaywrightTimeoutError as err:
            raise BrowserTimeoutError(f"Timed out waiting for download to initiate: {err}") from err
        except PlaywrightError as err:
            raise BrowserControllerError(f"Failed to initiate download: {err}") from err

        # Determine final filename
        suggested = download.suggested_filename
        final_filename = target_name or sanitize_download_filename(suggested or f"download_{int(time.time())}")

        # Strict extension check on final filename
        ext = Path(final_filename).suffix.lower()
        if ext in PROHIBITED_DOWNLOAD_EXTENSIONS:
            try:
                download.cancel()
            except Exception:
                pass
            raise DownloadSecurityError(
                f"Download was aborted: file extension '{ext}' is classified as hazardous/executable."
            )

        # Containment check within approved download directory
        dest_path = (self._download_directory / final_filename).resolve()
        try:
            if not dest_path.is_relative_to(self._download_directory):
                try:
                    download.cancel()
                except Exception:
                    pass
                raise DownloadSecurityError(
                    f"Destination path '{dest_path}' escapes approved download directory '{self._download_directory}'."
                )
        except (ValueError, TypeError) as err:
            try:
                download.cancel()
            except Exception:
                pass
            raise DownloadSecurityError(f"Containment check failed: {err}") from err

        # Overwrite confirmation check
        if dest_path.exists():
            cm = get_shared_confirmation_manager()
            overwrite_confirmed = confirm_overwrite
            if confirmation_token:
                try:
                    cm.validate_and_consume(
                        token=confirmation_token,
                        action_type="browser_download_overwrite",
                        action_params={"destination_path": str(dest_path), "filename": final_filename},
                    )
                    overwrite_confirmed = True
                except ConfirmationError as conf_err:
                    try:
                        download.cancel()
                    except Exception:
                        pass
                    raise ConsequentialActionConfirmationError(
                        message=f"Overwrite confirmation failed: {conf_err}",
                        action_type="browser_download_overwrite",
                        details={"destination_path": str(dest_path), "filename": final_filename},
                    ) from conf_err

            if not overwrite_confirmed:
                pending = cm.create_confirmation(
                    action_type="browser_download_overwrite",
                    action_params={"destination_path": str(dest_path), "filename": final_filename},
                    details={"destination_path": str(dest_path), "filename": final_filename},
                )
                try:
                    download.cancel()
                except Exception:
                    pass
                raise ConsequentialActionConfirmationError(
                    message=(
                        f"Destination file '{final_filename}' already exists. Overwrite requires confirmation. "
                        f"Confirmation token: {pending.token}"
                    ),
                    action_type="browser_download_overwrite",
                    details={
                        "destination_path": str(dest_path),
                        "filename": final_filename,
                        "confirmation_token": pending.token,
                    },
                    requires_confirmation=True,
                    confirmation_token=pending.token,
                )

        # Save downloaded file (strictly without execution)
        try:
            download.save_as(str(dest_path))
            file_size = dest_path.stat().st_size if dest_path.exists() else 0
            self._download_count += 1
            logger.info(f"Successfully downloaded '{final_filename}' ({file_size} bytes) to '{dest_path}'")
            return {
                "success": True,
                "operation": "download_file",
                "path": str(dest_path),
                "filename": final_filename,
                "size_bytes": file_size,
                "status": "success",
            }
        except Exception as err:
            raise BrowserControllerError(f"Failed to save downloaded file to '{dest_path}': {err}") from err
