"""
VoxPilot - Application-Side Software Update Service
Handles:
- GitHub release discovery and semantic version comparison
- Update settings persistence in %LOCALAPPDATA%\\VoxPilot\\config\\update_settings.json
- Secure HTTPS installer download with trusted GitHub host validation
- Real-time byte progress reporting and mid-download cancellation
- Mandatory SHA-256 checksum verification + Windows Authenticode inspection
- Detached external Windows updater process launch after explicit user approval
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.paths import (
    get_config_dir,
    get_executable_dir,
    get_project_root,
    get_updates_dir,
    is_frozen,
)
from backend.updater.updater_runner import (
    compute_file_sha256,
    generate_powershell_updater_script,
)
from backend.version import (
    InvalidVersionError,
    compare_versions,
    get_app_version,
    is_newer_version,
    parse_semver,
    try_parse_semver,
)

logger = logging.getLogger("voxpilot.updater")

DEFAULT_GITHUB_REPO = "Thanush505/VoxPilot"
DEFAULT_INSTALLER_NAME = "VoxPilot-Setup.exe"
DEFAULT_CHECK_CACHE_TTL_SECONDS = 300.0
DEFAULT_HTTP_TIMEOUT_SECONDS = 10.0
DOWNLOAD_CHUNK_SIZE = 65536

TRUSTED_GITHUB_HOSTS = frozenset(
    {
        "github.com",
        "api.github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
        "github-releases.githubusercontent.com",
        "codeload.github.com",
    }
)

_REPO_SLUG_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_GIT_REMOTE_GITHUB_PATTERN = re.compile(
    r"github\.com[:/](?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?$"
)
_SHA256_HEX_PATTERN = re.compile(r"\b([a-fA-F0-9]{64})\b")
_LABELED_SHA256_PATTERN = re.compile(
    r"(?:sha-?256|checksum|digest)\s*[:=`\s]+\s*([a-fA-F0-9]{64})\b",
    re.IGNORECASE,
)


class UpdateError(Exception):
    """Base exception for update system errors."""


class UntrustedDownloadUrlError(UpdateError):
    """Raised when an installer download URL is not HTTPS or not from a trusted GitHub host."""


class InvalidInstallerAssetError(UpdateError):
    """Raised when a release asset filename or metadata is unsafe or missing."""


class ChecksumVerificationError(UpdateError):
    """Raised when SHA-256 verification fails or checksum metadata is missing."""


class RateLimitExceededError(UpdateError):
    """Raised when GitHub API rate limits are exceeded."""


def discover_github_repo(project_root: Optional[Path] = None) -> str:
    """Discover the canonical GitHub 'owner/repo' slug from environment or .git/config."""
    env_repo = os.environ.get("VOXPILOT_GITHUB_REPO", "").strip()
    if env_repo and _REPO_SLUG_PATTERN.match(env_repo):
        return env_repo

    root = project_root or get_project_root()
    git_config = root / ".git" / "config"
    try:
        if git_config.is_file():
            content = git_config.read_text(encoding="utf-8", errors="ignore")
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("url ="):
                    url_val = stripped.split("=", 1)[1].strip()
                    match = _GIT_REMOTE_GITHUB_PATTERN.search(url_val)
                    if match:
                        return f"{match.group('owner')}/{match.group('repo')}"
    except Exception:
        pass

    return DEFAULT_GITHUB_REPO


def validate_download_url(url: str) -> str:
    """Validate that a download URL uses HTTPS and points to an official GitHub release host."""
    if not isinstance(url, str) or not url.strip():
        raise UntrustedDownloadUrlError("Download URL cannot be empty.")

    cleaned = url.strip()
    parsed = urllib.parse.urlparse(cleaned)

    if parsed.scheme.lower() != "https":
        raise UntrustedDownloadUrlError(
            f"Insecure download URL scheme '{parsed.scheme}'. Only HTTPS URLs are permitted."
        )

    hostname = (parsed.hostname or "").lower()
    if not hostname or hostname not in TRUSTED_GITHUB_HOSTS:
        raise UntrustedDownloadUrlError(
            f"Untrusted download host '{hostname}'. Only official GitHub release hosts are allowed."
        )

    if ".." in parsed.path or "\\" in parsed.path:
        raise UntrustedDownloadUrlError("Download URL path contains unsafe traversal characters.")

    return cleaned


def validate_installer_filename(filename: str) -> str:
    """Validate that the installer asset filename is a safe Windows .exe basename."""
    if not isinstance(filename, str) or not filename.strip():
        raise InvalidInstallerAssetError("Installer asset filename cannot be empty.")

    cleaned = filename.strip()
    if (
        ".." in cleaned
        or "/" in cleaned
        or "\\" in cleaned
        or ":" in cleaned
        or "\x00" in cleaned
    ):
        raise InvalidInstallerAssetError(
            f"Unsafe installer filename '{filename}': path separators and traversal are prohibited."
        )

    if not cleaned.lower().endswith(".exe") or len(cleaned) <= 4:
        raise InvalidInstallerAssetError(
            f"Invalid installer filename '{filename}': only .exe installer packages are allowed."
        )

    return cleaned


def extract_sha256_from_text(text: str, installer_name: str = DEFAULT_INSTALLER_NAME) -> Optional[str]:
    """Extract a 64-character SHA-256 hex digest from release notes or a checksum file."""
    if not text or not isinstance(text, str):
        return None

    # 1. Look for a line specifically referencing the installer name alongside a 64-hex hash
    installer_lower = installer_name.lower()
    for line in text.splitlines():
        if installer_lower in line.lower():
            match = _SHA256_HEX_PATTERN.search(line)
            if match:
                return match.group(1).lower()

    # 2. Look for an explicitly labeled SHA-256 field (e.g. "SHA-256: <64-hex>")
    labeled = _LABELED_SHA256_PATTERN.search(text)
    if labeled:
        return labeled.group(1).lower()

    # 3. If the text is a dedicated .sha256 file containing a single 64-hex token
    tokens = _SHA256_HEX_PATTERN.findall(text)
    if len(tokens) == 1:
        return tokens[0].lower()

    return None


def inspect_authenticode_signature(file_path: Path) -> Dict[str, Any]:
    """Inspect Windows Authenticode digital signature status of an installer executable.

    Explicitly distinguishes between SHA-256 integrity verification and publisher
    Authenticode code-signing identity verification.
    """
    default_info: Dict[str, Any] = {
        "signature_checked": False,
        "signature_status": "Unavailable",
        "signature_valid": False,
        "signer_subject": None,
        "integrity_verification": "SHA-256 checksum verified against official release metadata",
        "publisher_verification_note": (
            "SHA-256 checksum verifies package integrity. Authenticode signature verification "
            "was not available in this environment."
        ),
    }
    if sys.platform != "win32" or not file_path.is_file():
        return default_info

    try:
        ps_cmd = (
            f"$sig = Get-AuthenticodeSignature -LiteralPath '{str(file_path).replace(chr(39), chr(39)*2)}'; "
            "$subj = if ($sig.SignerCertificate) { $sig.SignerCertificate.Subject } else { $null }; "
            "@{ Status = [string]$sig.Status; Subject = $subj } | ConvertTo-Json -Compress"
        )
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=8.0,
            check=False,
        )
        if completed.returncode == 0 and completed.stdout.strip():
            data = json.loads(completed.stdout.strip())
            status_str = str(data.get("Status") or "Unknown")
            subject = data.get("Subject")
            is_valid = status_str.lower() == "valid"
            if is_valid:
                note = (
                    f"Package integrity verified via SHA-256 and publisher identity verified "
                    f"via valid Windows Authenticode signature ({subject or 'Trusted Signer'})."
                )
            elif status_str.lower() == "notsigned":
                note = (
                    "Package integrity verified via official SHA-256 checksum. "
                    "Installer binary is not Authenticode code-signed (unsigned build)."
                )
            else:
                note = (
                    f"Package integrity verified via official SHA-256 checksum. "
                    f"Authenticode signature status: {status_str}."
                )
            return {
                "signature_checked": True,
                "signature_status": status_str,
                "signature_valid": is_valid,
                "signer_subject": subject,
                "integrity_verification": "SHA-256 checksum verified against official release metadata",
                "publisher_verification_note": note,
            }
    except Exception as exc:
        logger.debug("Authenticode inspection skipped/failed: %s", exc)

    return default_info


class UpdateSettingsManager:
    """Manages persistent update configuration in %LOCALAPPDATA%\\VoxPilot\\config\\update_settings.json."""

    def __init__(self, config_path: Optional[Path] = None) -> None:
        self._config_path = config_path

    @property
    def config_path(self) -> Path:
        if self._config_path is not None:
            self._config_path.parent.mkdir(parents=True, exist_ok=True)
            return self._config_path
        return get_config_dir() / "update_settings.json"

    def default_settings(self) -> Dict[str, Any]:
        return {
            "auto_check_enabled": True,
            "channel": "stable",
            "release_repo": discover_github_repo(),
            "last_checked_at": None,
            "last_available_version": None,
            "dismissed_version": None,
            "auto_install_enabled": False,
        }

    def load(self) -> Dict[str, Any]:
        defaults = self.default_settings()
        path = self.config_path
        if not path.is_file():
            return defaults

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return defaults
        except Exception as exc:
            logger.warning("Failed to read update_settings.json (%s); using defaults.", exc)
            return defaults

        channel = str(raw.get("channel", defaults["channel"])).strip().lower()
        if channel not in ("stable", "beta"):
            channel = "stable"

        repo = str(raw.get("release_repo", defaults["release_repo"])).strip()
        if not _REPO_SLUG_PATTERN.match(repo):
            repo = defaults["release_repo"]

        # Env override takes precedence for active repo if set
        env_repo = os.environ.get("VOXPILOT_GITHUB_REPO", "").strip()
        if env_repo and _REPO_SLUG_PATTERN.match(env_repo):
            repo = env_repo

        return {
            "auto_check_enabled": bool(raw.get("auto_check_enabled", True)),
            "channel": channel,
            "release_repo": repo,
            "last_checked_at": raw.get("last_checked_at") if isinstance(raw.get("last_checked_at"), str) else None,
            "last_available_version": (
                raw.get("last_available_version")
                if isinstance(raw.get("last_available_version"), str)
                else None
            ),
            "dismissed_version": (
                raw.get("dismissed_version") if isinstance(raw.get("dismissed_version"), str) else None
            ),
            # Automatic installation without user confirmation is strictly prohibited
            "auto_install_enabled": False,
        }

    def save(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        current = self.load()
        if "auto_check_enabled" in updates and updates["auto_check_enabled"] is not None:
            current["auto_check_enabled"] = bool(updates["auto_check_enabled"])

        if "channel" in updates and updates["channel"] is not None:
            ch = str(updates["channel"]).strip().lower()
            if ch not in ("stable", "beta"):
                raise ValueError("Invalid update channel. Allowed values: 'stable', 'beta'.")
            current["channel"] = ch

        if "release_repo" in updates and updates["release_repo"] is not None:
            repo = str(updates["release_repo"]).strip()
            if not _REPO_SLUG_PATTERN.match(repo):
                raise ValueError("Invalid GitHub repository format. Expected 'owner/repo'.")
            current["release_repo"] = repo

        if "last_checked_at" in updates:
            current["last_checked_at"] = updates["last_checked_at"]

        if "last_available_version" in updates:
            current["last_available_version"] = updates["last_available_version"]

        if "dismissed_version" in updates:
            current["dismissed_version"] = updates["dismissed_version"]

        # Enforce auto_install_enabled == False unconditionally
        current["auto_install_enabled"] = False

        path = self.config_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(current, indent=2), encoding="utf-8")
        tmp_path.replace(path)
        return current


class UpdateService:
    """Coordinates GitHub release checks, verified downloads, and detached installer execution."""

    def __init__(
        self,
        settings_manager: Optional[UpdateSettingsManager] = None,
        updates_dir: Optional[Path] = None,
        installed_version: Optional[str] = None,
        cache_ttl_seconds: float = DEFAULT_CHECK_CACHE_TTL_SECONDS,
        http_timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
    ) -> None:
        self._lock = threading.RLock()
        self._settings_manager = settings_manager or UpdateSettingsManager()
        self._updates_dir = updates_dir
        self._installed_version_override = installed_version
        self._cache_ttl_seconds = cache_ttl_seconds
        self._http_timeout_seconds = http_timeout_seconds

        self._last_check_monotonic: Optional[float] = None
        self._cancel_event = threading.Event()
        self._download_thread: Optional[threading.Thread] = None

        # Runtime update state
        self._state: str = "idle"
        self._update_available: bool = False
        self._latest_version: Optional[str] = None
        self._release_tag: Optional[str] = None
        self._release_title: Optional[str] = None
        self._release_notes: Optional[str] = None
        self._published_at: Optional[str] = None
        self._release_url: Optional[str] = None
        self._installer_asset_name: Optional[str] = None
        self._installer_download_url: Optional[str] = None
        self._installer_size_bytes: Optional[int] = None
        self._expected_sha256: Optional[str] = None

        # Download & verification state
        self._downloaded_bytes: int = 0
        self._total_bytes: int = 0
        self._download_progress_pct: float = 0.0
        self._downloaded_installer_path: Optional[Path] = None
        self._computed_sha256: Optional[str] = None
        self._sha256_verified: bool = False
        self._signature_info: Optional[Dict[str, Any]] = None
        self._error_code: Optional[str] = None
        self._error_message: Optional[str] = None
        self._status_message: str = "Ready to check for updates."

    @property
    def installed_version(self) -> str:
        if self._installed_version_override:
            return self._installed_version_override
        return get_app_version()

    @property
    def updates_dir(self) -> Path:
        if self._updates_dir is not None:
            self._updates_dir.mkdir(parents=True, exist_ok=True)
            return self._updates_dir.resolve()
        return get_updates_dir()

    @property
    def install_result_file(self) -> Path:
        return self.updates_dir / "update_install_result.json"

    def get_last_install_result(self) -> Optional[Dict[str, Any]]:
        """Read the result of the most recent external installer run if present."""
        path = self.install_result_file
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def get_version_info(self) -> Dict[str, Any]:
        """Return authoritative installed version metadata."""
        settings = self._settings_manager.load()
        parsed = try_parse_semver(self.installed_version)
        return {
            "app_name": "VoxPilot",
            "installed_version": self.installed_version,
            "is_prerelease": bool(parsed.is_prerelease) if parsed else False,
            "channel": settings["channel"],
            "release_repo": settings["release_repo"],
            "is_frozen": is_frozen(),
            "last_checked_at": settings["last_checked_at"],
        }

    def get_settings(self) -> Dict[str, Any]:
        """Return current persistent update settings."""
        return self._settings_manager.load()

    def update_settings(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update and persist update preferences."""
        with self._lock:
            saved = self._settings_manager.save(updates)
            # Invalidate check cache if channel or repo changed
            if "channel" in updates or "release_repo" in updates:
                self._last_check_monotonic = None
            return saved

    def dismiss_notification(self, version: Optional[str] = None) -> Dict[str, Any]:
        """Record that the user clicked 'Remind Me Later' for a specific version."""
        with self._lock:
            target_ver = version or self._latest_version
            saved = self._settings_manager.save({"dismissed_version": target_ver})
            return self._build_status_payload(settings=saved)

    def get_status(self) -> Dict[str, Any]:
        """Return full current update status snapshot."""
        with self._lock:
            return self._build_status_payload()

    def _build_status_payload(self, settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        cfg = settings or self._settings_manager.load()
        dismissed = cfg.get("dismissed_version")
        notification_dismissed = bool(
            self._update_available and self._latest_version and dismissed == self._latest_version
        )
        return {
            "state": self._state,
            "installed_version": self.installed_version,
            "latest_version": self._latest_version,
            "update_available": self._update_available,
            "notification_dismissed": notification_dismissed,
            "channel": cfg["channel"],
            "auto_check_enabled": cfg["auto_check_enabled"],
            "auto_install_enabled": False,
            "release_repo": cfg["release_repo"],
            "last_checked_at": cfg["last_checked_at"],
            "release": (
                {
                    "version": self._latest_version,
                    "tag_name": self._release_tag,
                    "title": self._release_title,
                    "notes": self._release_notes or "",
                    "published_at": self._published_at,
                    "release_url": self._release_url,
                    "installer_name": self._installer_asset_name,
                    "installer_url": self._installer_download_url,
                    "installer_size_bytes": self._installer_size_bytes,
                    "expected_sha256": self._expected_sha256,
                }
                if self._latest_version
                else None
            ),
            "download": {
                "downloaded_bytes": self._downloaded_bytes,
                "total_bytes": self._total_bytes,
                "progress_pct": round(self._download_progress_pct, 1),
                "installer_path": str(self._downloaded_installer_path) if self._downloaded_installer_path else None,
                "computed_sha256": self._computed_sha256,
                "sha256_verified": self._sha256_verified,
                "signature": self._signature_info,
            },
            "last_install_result": self.get_last_install_result(),
            "status_message": self._status_message,
            "error_code": self._error_code,
            "error_message": self._error_message,
        }

    def _build_github_headers(self) -> Dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "User-Agent": f"VoxPilot/{self.installed_version} (Windows; UpdateService)",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _fetch_json_url(self, url: str) -> Tuple[Any, Dict[str, str]]:
        """Perform an HTTPS GET request and parse JSON response."""
        validate_download_url(url)
        req = urllib.request.Request(url, headers=self._build_github_headers(), method="GET")
        with urllib.request.urlopen(req, timeout=self._http_timeout_seconds) as resp:
            raw_bytes = resp.read()
            headers = {k.lower(): v for k, v in resp.headers.items()}
            data = json.loads(raw_bytes.decode("utf-8"))
            return data, headers

    def _fetch_text_url(self, url: str, max_bytes: int = 65536) -> str:
        """Fetch a small text asset (such as a .sha256 or SHA256SUMS.txt file) over HTTPS."""
        validate_download_url(url)
        headers = self._build_github_headers()
        headers["Accept"] = "application/octet-stream, text/plain, */*"
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=self._http_timeout_seconds) as resp:
            return resp.read(max_bytes).decode("utf-8", errors="replace")

    def _select_candidate_release(
        self, payload: Any, channel: str
    ) -> Optional[Dict[str, Any]]:
        """Select the highest valid non-draft release matching the requested channel."""
        releases: List[Dict[str, Any]] = []
        if isinstance(payload, dict):
            # Single release response from /releases/latest
            if payload.get("tag_name") is not None or payload.get("draft") is not None:
                releases = [payload]
            else:
                raise UpdateError("Malformed GitHub release response object.")
        elif isinstance(payload, list):
            releases = [item for item in payload if isinstance(item, dict)]
        else:
            raise UpdateError("Malformed GitHub release API payload.")

        allow_prerelease = channel == "beta"
        valid_candidates: List[Tuple[Any, Dict[str, Any]]] = []
        saw_non_draft = False
        saw_invalid_tag = False

        for rel in releases:
            if bool(rel.get("draft", False)):
                continue
            saw_non_draft = True
            is_pre = bool(rel.get("prerelease", False))
            if is_pre and not allow_prerelease:
                continue

            tag_name = str(rel.get("tag_name") or "").strip()
            try:
                parsed_ver = parse_semver(tag_name)
            except InvalidVersionError:
                saw_invalid_tag = True
                continue

            if parsed_ver.is_prerelease and not allow_prerelease:
                continue

            valid_candidates.append((parsed_ver, rel))

        if not valid_candidates:
            if saw_non_draft and saw_invalid_tag:
                raise InvalidVersionError(
                    "Published release contains an invalid semantic version tag."
                )
            return None

        valid_candidates.sort(key=lambda item: item[0], reverse=True)
        return valid_candidates[0][1]

    def _resolve_installer_and_checksum(
        self, release: Dict[str, Any]
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """Find the official VoxPilot-Setup.exe asset and its expected SHA-256 checksum."""
        assets_raw = release.get("assets")
        assets: List[Dict[str, Any]] = (
            [a for a in assets_raw if isinstance(a, dict)]
            if isinstance(assets_raw, list)
            else []
        )

        installer_asset: Optional[Dict[str, Any]] = None
        checksum_assets: List[Dict[str, Any]] = []

        for asset in assets:
            name = str(asset.get("name") or "").strip()
            name_lower = name.lower()
            if name_lower == DEFAULT_INSTALLER_NAME.lower():
                installer_asset = asset
            elif (
                installer_asset is None
                and name_lower.endswith(".exe")
                and "voxpilot" in name_lower
                and "setup" in name_lower
            ):
                installer_asset = asset
            elif (
                name_lower.endswith(".sha256")
                or name_lower in ("sha256sums.txt", "sha256sums", "checksums.txt")
            ):
                checksum_assets.append(asset)

        installer_name = (
            str(installer_asset.get("name")).strip()
            if installer_asset and installer_asset.get("name")
            else DEFAULT_INSTALLER_NAME
        )

        # 1. Check GitHub asset 'digest' field (e.g. "sha256:<64-hex>")
        if installer_asset:
            digest_field = str(installer_asset.get("digest") or "").strip()
            if digest_field.lower().startswith("sha256:"):
                candidate_hex = digest_field.split(":", 1)[1].strip().lower()
                if len(candidate_hex) == 64 and all(c in "0123456789abcdef" for c in candidate_hex):
                    return installer_asset, candidate_hex

        # 2. Check companion checksum asset (.sha256 or SHA256SUMS.txt)
        for chk_asset in checksum_assets:
            dl_url = str(chk_asset.get("browser_download_url") or "").strip()
            if not dl_url:
                continue
            try:
                chk_text = self._fetch_text_url(dl_url)
                extracted = extract_sha256_from_text(chk_text, installer_name=installer_name)
                if extracted:
                    return installer_asset, extracted
            except Exception as exc:
                logger.warning("Could not read checksum asset %s: %s", chk_asset.get("name"), exc)

        # 3. Check release body / notes for SHA-256 checksum
        body_text = str(release.get("body") or "")
        extracted_from_body = extract_sha256_from_text(body_text, installer_name=installer_name)
        return installer_asset, extracted_from_body

    def check_for_updates(self, force: bool = False) -> Dict[str, Any]:
        """Query GitHub Releases for the latest official version and compare against installed."""
        with self._lock:
            # Do not interrupt an active download, verification, or installation
            if self._state in ("downloading", "verifying", "installing"):
                return self._build_status_payload()

            now_mono = time.monotonic()
            if (
                not force
                and self._last_check_monotonic is not None
                and (now_mono - self._last_check_monotonic) < self._cache_ttl_seconds
                and self._state in ("up_to_date", "update_available", "ready_to_install")
            ):
                return self._build_status_payload()

            self._state = "checking"
            self._error_code = None
            self._error_message = None
            self._status_message = "Checking GitHub Releases for updates..."

        settings = self._settings_manager.load()
        channel = settings["channel"]
        repo = settings["release_repo"]

        custom_api_url = os.environ.get("VOXPILOT_RELEASE_API_URL", "").strip()
        if custom_api_url:
            api_url = custom_api_url
        elif channel == "beta":
            api_url = f"https://api.github.com/repos/{repo}/releases?per_page=15"
        else:
            api_url = f"https://api.github.com/repos/{repo}/releases/latest"

        checked_at_iso = datetime.now(timezone.utc).isoformat()

        try:
            payload, headers = self._fetch_json_url(api_url)
        except UntrustedDownloadUrlError as exc:
            with self._lock:
                self._state = "failed"
                self._error_code = "untrusted_release_url"
                self._error_message = str(exc)
                self._status_message = f"Update check blocked: {exc}"
                return self._build_status_payload(settings=settings)
        except urllib.error.HTTPError as exc:
            with self._lock:
                if exc.code == 404:
                    # 404 on /releases/latest means no public release has been published yet
                    self._state = "up_to_date"
                    self._update_available = False
                    self._latest_version = self.installed_version
                    self._error_code = None
                    self._error_message = None
                    self._status_message = (
                        f"You are running VoxPilot v{self.installed_version}. "
                        f"No newer public release is published on GitHub ({repo})."
                    )
                    self._last_check_monotonic = time.monotonic()
                    saved = self._settings_manager.save({"last_checked_at": checked_at_iso})
                    return self._build_status_payload(settings=saved)

                remaining = (exc.headers.get("X-RateLimit-Remaining") if exc.headers else None)
                if exc.code in (403, 429) or remaining == "0":
                    self._state = "failed"
                    self._error_code = "rate_limit_exceeded"
                    self._error_message = (
                        "GitHub API rate limit exceeded. Please wait a few minutes before checking again."
                    )
                    self._status_message = self._error_message
                    return self._build_status_payload(settings=settings)

                self._state = "failed"
                self._error_code = f"http_{exc.code}"
                self._error_message = f"GitHub Releases API returned HTTP {exc.code}."
                self._status_message = self._error_message
                return self._build_status_payload(settings=settings)
        except urllib.error.URLError as exc:
            with self._lock:
                self._state = "offline"
                self._error_code = "network_unavailable"
                self._error_message = (
                    f"Unable to reach GitHub Releases ({exc.reason}). "
                    "Check your internet connection and try again."
                )
                self._status_message = "Offline — unable to reach GitHub Releases."
                return self._build_status_payload(settings=settings)
        except Exception as exc:
            with self._lock:
                self._state = "failed"
                self._error_code = "invalid_release_metadata"
                self._error_message = f"Invalid response from release server: {exc}"
                self._status_message = self._error_message
                return self._build_status_payload(settings=settings)

        # Parse and select candidate release
        try:
            candidate = self._select_candidate_release(payload, channel=channel)
        except InvalidVersionError as exc:
            with self._lock:
                self._state = "failed"
                self._error_code = "invalid_release_version"
                self._error_message = f"Invalid release version metadata: {exc}"
                self._status_message = self._error_message
                return self._build_status_payload(settings=settings)
        except UpdateError as exc:
            with self._lock:
                self._state = "failed"
                self._error_code = "invalid_release_metadata"
                self._error_message = str(exc)
                self._status_message = self._error_message
                return self._build_status_payload(settings=settings)

        if candidate is None:
            with self._lock:
                self._state = "up_to_date"
                self._update_available = False
                self._latest_version = self.installed_version
                self._error_code = None
                self._error_message = None
                self._status_message = f"VoxPilot v{self.installed_version} is up to date."
                self._last_check_monotonic = time.monotonic()
                saved = self._settings_manager.save({"last_checked_at": checked_at_iso})
                return self._build_status_payload(settings=saved)

        tag_name = str(candidate.get("tag_name") or "").strip()
        parsed_latest = parse_semver(tag_name)
        latest_normalized = parsed_latest.normalized
        allow_pre = channel == "beta"
        newer = is_newer_version(
            self.installed_version,
            latest_normalized,
            allow_prerelease=allow_pre,
        )

        release_title = str(candidate.get("name") or tag_name).strip()
        release_notes = str(candidate.get("body") or "").strip()
        published_at = candidate.get("published_at")
        release_url = str(
            candidate.get("html_url")
            or f"https://github.com/{repo}/releases/tag/{tag_name}"
        ).strip()

        if not newer:
            with self._lock:
                self._state = "up_to_date"
                self._update_available = False
                self._latest_version = latest_normalized
                self._release_tag = tag_name
                self._release_title = release_title
                self._release_notes = release_notes
                self._published_at = published_at
                self._release_url = release_url
                self._error_code = None
                self._error_message = None
                self._status_message = f"VoxPilot v{self.installed_version} is up to date."
                self._last_check_monotonic = time.monotonic()
                saved = self._settings_manager.save({"last_checked_at": checked_at_iso})
                return self._build_status_payload(settings=saved)

        # Release is newer than installed version -> inspect installer asset and SHA-256
        installer_asset, expected_sha256 = self._resolve_installer_and_checksum(candidate)
        if not installer_asset:
            with self._lock:
                self._latest_version = latest_normalized
                self._release_tag = tag_name
                self._release_title = release_title
                self._release_notes = release_notes
                self._published_at = published_at
                self._release_url = release_url
                self._update_available = False
                self._state = "failed"
                self._error_code = "missing_installer_asset"
                self._error_message = (
                    f"Release v{latest_normalized} was found, but the official installer asset "
                    f"({DEFAULT_INSTALLER_NAME}) is missing from the release."
                )
                self._status_message = self._error_message
                saved = self._settings_manager.save({"last_checked_at": checked_at_iso})
                return self._build_status_payload(settings=saved)

        raw_asset_name = str(installer_asset.get("name") or DEFAULT_INSTALLER_NAME)
        raw_download_url = str(installer_asset.get("browser_download_url") or "")
        asset_size = int(installer_asset.get("size") or 0)

        try:
            safe_asset_name = validate_installer_filename(raw_asset_name)
            safe_download_url = validate_download_url(raw_download_url)
        except UpdateError as exc:
            with self._lock:
                self._state = "failed"
                self._update_available = False
                self._error_code = "unsafe_installer_asset"
                self._error_message = str(exc)
                self._status_message = str(exc)
                saved = self._settings_manager.save({"last_checked_at": checked_at_iso})
                return self._build_status_payload(settings=saved)

        with self._lock:
            self._latest_version = latest_normalized
            self._release_tag = tag_name
            self._release_title = release_title
            self._release_notes = release_notes
            self._published_at = published_at
            self._release_url = release_url
            self._installer_asset_name = safe_asset_name
            self._installer_download_url = safe_download_url
            self._installer_size_bytes = asset_size
            self._expected_sha256 = expected_sha256
            self._update_available = True
            self._error_code = None
            self._error_message = None
            self._last_check_monotonic = time.monotonic()

            # Check if we already have a verified installer on disk for this exact version & hash
            if (
                self._downloaded_installer_path
                and self._downloaded_installer_path.is_file()
                and self._sha256_verified
                and expected_sha256
                and self._computed_sha256 == expected_sha256.lower()
            ):
                self._state = "ready_to_install"
                self._status_message = (
                    f"VoxPilot v{latest_normalized} is downloaded and verified. Ready to install."
                )
            else:
                self._state = "update_available"
                self._status_message = (
                    f"Update available: VoxPilot v{latest_normalized} "
                    f"(currently installed: v{self.installed_version})."
                )

            saved = self._settings_manager.save(
                {
                    "last_checked_at": checked_at_iso,
                    "last_available_version": latest_normalized,
                }
            )
            return self._build_status_payload(settings=saved)

    def start_download(self, background: bool = True) -> Dict[str, Any]:
        """Start downloading and verifying the available official installer."""
        with self._lock:
            if self._state == "downloading":
                return self._build_status_payload()

            if not self._update_available or not self._installer_download_url or not self._latest_version:
                raise UpdateError("No verified update is currently available to download.")

            # Validate URL and filename before initiating download
            safe_url = validate_download_url(self._installer_download_url)
            safe_name = validate_installer_filename(self._installer_asset_name or DEFAULT_INSTALLER_NAME)

            if not self._expected_sha256 or len(self._expected_sha256) != 64:
                self._state = "failed"
                self._error_code = "missing_checksum_metadata"
                self._error_message = (
                    "Refusing to download installer: official release does not provide "
                    "a valid SHA-256 checksum for integrity verification."
                )
                self._status_message = self._error_message
                return self._build_status_payload()

            self._cancel_event.clear()
            self._state = "downloading"
            self._downloaded_bytes = 0
            self._total_bytes = int(self._installer_size_bytes or 0)
            self._download_progress_pct = 0.0
            self._downloaded_installer_path = None
            self._computed_sha256 = None
            self._sha256_verified = False
            self._signature_info = None
            self._error_code = None
            self._error_message = None
            self._status_message = f"Downloading {safe_name} (v{self._latest_version})..."

            target_version = self._latest_version
            expected_sha256 = self._expected_sha256.lower()

        if background:
            t = threading.Thread(
                target=self._download_and_verify_worker,
                args=(safe_url, safe_name, target_version, expected_sha256),
                daemon=True,
                name="VoxPilotUpdateDownloader",
            )
            with self._lock:
                self._download_thread = t
            t.start()
            return self.get_status()
        else:
            self._download_and_verify_worker(safe_url, safe_name, target_version, expected_sha256)
            return self.get_status()

    def _download_and_verify_worker(
        self,
        download_url: str,
        asset_name: str,
        target_version: str,
        expected_sha256: str,
    ) -> None:
        """Stream installer to %LOCALAPPDATA%\\VoxPilot\\updates, support cancellation, and verify SHA-256."""
        upd_dir = self.updates_dir
        stem = Path(asset_name).stem
        final_filename = f"{stem}-{target_version}.exe"
        validate_installer_filename(final_filename)

        final_path = (upd_dir / final_filename).resolve()
        part_path = (upd_dir / f"{final_filename}.part").resolve()

        # Strict path confinement check
        if final_path.parent != upd_dir.resolve() or part_path.parent != upd_dir.resolve():
            with self._lock:
                self._state = "failed"
                self._error_code = "path_traversal_blocked"
                self._error_message = "Installer destination path escaped the approved updates directory."
                self._status_message = self._error_message
            return

        # Clean up any stale .part file
        try:
            if part_path.exists():
                part_path.unlink()
        except Exception:
            pass

        headers = self._build_github_headers()
        headers["Accept"] = "application/octet-stream, */*"

        try:
            req = urllib.request.Request(download_url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=self._http_timeout_seconds) as resp:
                # Verify final redirected URL is also HTTPS and on a trusted GitHub host
                final_url = resp.geturl()
                if final_url:
                    validate_download_url(final_url)

                content_length_hdr = resp.headers.get("Content-Length") if resp.headers else None
                content_length = int(content_length_hdr) if content_length_hdr and content_length_hdr.isdigit() else 0

                with self._lock:
                    if content_length > 0:
                        self._total_bytes = content_length

                hasher = hashlib.sha256()
                bytes_so_far = 0

                with open(part_path, "wb") as out_file:
                    while True:
                        if self._cancel_event.is_set():
                            break
                        chunk = resp.read(DOWNLOAD_CHUNK_SIZE)
                        if not chunk:
                            break
                        out_file.write(chunk)
                        hasher.update(chunk)
                        bytes_so_far += len(chunk)

                        with self._lock:
                            self._downloaded_bytes = bytes_so_far
                            total = self._total_bytes
                            if total > 0:
                                self._download_progress_pct = min(99.9, (bytes_so_far / total) * 100.0)
                            self._status_message = (
                                f"Downloading VoxPilot v{target_version} installer "
                                f"({bytes_so_far:,} / {total:,} bytes)..."
                                if total > 0
                                else f"Downloading VoxPilot v{target_version} installer ({bytes_so_far:,} bytes)..."
                            )

                if self._cancel_event.is_set():
                    try:
                        if part_path.exists():
                            part_path.unlink()
                    except Exception:
                        pass
                    with self._lock:
                        self._state = "update_available"
                        self._downloaded_bytes = 0
                        self._download_progress_pct = 0.0
                        self._error_code = None
                        self._error_message = None
                        self._status_message = "Update download cancelled."
                    return

                # Check for truncated download
                with self._lock:
                    expected_total = self._total_bytes

                if bytes_so_far == 0 or (expected_total > 0 and bytes_so_far != expected_total):
                    try:
                        if part_path.exists():
                            part_path.unlink()
                    except Exception:
                        pass
                    with self._lock:
                        self._state = "failed"
                        self._error_code = "incomplete_download"
                        self._error_message = (
                            f"Installer download was incomplete (received {bytes_so_far} bytes, "
                            f"expected {expected_total} bytes)."
                        )
                        self._status_message = self._error_message
                    return

                # Transition to verifying state
                with self._lock:
                    self._state = "verifying"
                    self._download_progress_pct = 100.0
                    self._status_message = "Verifying installer SHA-256 checksum and signature..."

                # Compute full on-disk SHA-256 of the downloaded .part file
                disk_sha256 = compute_file_sha256(part_path)
                if disk_sha256 != expected_sha256.lower():
                    try:
                        if part_path.exists():
                            part_path.unlink()
                    except Exception:
                        pass
                    with self._lock:
                        self._state = "failed"
                        self._computed_sha256 = disk_sha256
                        self._sha256_verified = False
                        self._error_code = "checksum_mismatch"
                        self._error_message = (
                            f"SHA-256 checksum verification failed! Expected {expected_sha256.lower()}, "
                            f"got {disk_sha256}. The corrupted or unverified file has been deleted."
                        )
                        self._status_message = self._error_message
                    return

                # Move verified .part file to final installer path
                part_path.replace(final_path)
                sig_info = inspect_authenticode_signature(final_path)

                with self._lock:
                    self._downloaded_installer_path = final_path
                    self._computed_sha256 = disk_sha256
                    self._sha256_verified = True
                    self._signature_info = sig_info
                    self._state = "ready_to_install"
                    self._error_code = None
                    self._error_message = None
                    self._status_message = (
                        f"VoxPilot v{target_version} installer downloaded and verified (SHA-256 match). "
                        "Awaiting user confirmation to install."
                    )

        except UntrustedDownloadUrlError as exc:
            try:
                if part_path.exists():
                    part_path.unlink()
            except Exception:
                pass
            with self._lock:
                self._state = "failed"
                self._error_code = "untrusted_download_url"
                self._error_message = str(exc)
                self._status_message = str(exc)
        except Exception as exc:
            try:
                if part_path.exists():
                    part_path.unlink()
            except Exception:
                pass
            with self._lock:
                self._state = "failed"
                self._error_code = "download_failed"
                self._error_message = f"Failed to download update installer: {exc}"
                self._status_message = self._error_message

    def cancel_download(self) -> Dict[str, Any]:
        """Cancel an in-progress installer download and clean up partial files."""
        self._cancel_event.set()
        thread = None
        with self._lock:
            thread = self._download_thread

        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

        with self._lock:
            # Ensure any leftover .part files in updates_dir are cleaned up
            for part_file in self.updates_dir.glob("*.part"):
                try:
                    part_file.unlink()
                except Exception:
                    pass

            if self._state == "downloading":
                self._state = "update_available" if self._update_available else "idle"
                self._downloaded_bytes = 0
                self._download_progress_pct = 0.0
                self._status_message = "Update download cancelled."
            return self._build_status_payload()

    def trigger_install(
        self,
        confirm: bool,
        relaunch: bool = True,
        shutdown_app: bool = True,
        shutdown_callback: Optional[Callable[[], None]] = None,
    ) -> Dict[str, Any]:
        """Launch the separate external Windows updater after explicit user confirmation.

        Never executes an unverified installer and never overwrites the running process in-place.
        """
        if not confirm:
            raise PermissionError(
                "Explicit user confirmation (confirm=True) is required before installing an update."
            )

        with self._lock:
            if (
                self._state != "ready_to_install"
                or not self._sha256_verified
                or not self._downloaded_installer_path
                or not self._expected_sha256
            ):
                raise ChecksumVerificationError(
                    "Cannot install update: no verified installer is ready to install."
                )

            installer_path = self._downloaded_installer_path.resolve()
            if installer_path.parent != self.updates_dir.resolve() or not installer_path.is_file():
                self._state = "failed"
                self._error_code = "installer_missing"
                self._error_message = "Verified installer file is missing from the updates directory."
                raise ChecksumVerificationError(self._error_message)

            # Final pre-launch SHA-256 verification on disk
            actual_sha256 = compute_file_sha256(installer_path)
            if actual_sha256 != self._expected_sha256.lower():
                try:
                    installer_path.unlink()
                except Exception:
                    pass
                self._state = "failed"
                self._sha256_verified = False
                self._error_code = "checksum_mismatch"
                self._error_message = (
                    "Pre-installation SHA-256 re-verification failed! Refusing to execute installer."
                )
                self._status_message = self._error_message
                raise ChecksumVerificationError(self._error_message)

            target_version = self._latest_version or "unknown"
            expected_sha256 = self._expected_sha256.lower()
            install_dir = get_executable_dir()
            relaunch_exe = install_dir / "VoxPilot.exe"
            current_pid = os.getpid() if shutdown_app else 0

            # Generate the external PowerShell updater helper inside %LOCALAPPDATA%\VoxPilot\updates
            ps_script = generate_powershell_updater_script(
                script_path=self.updates_dir / "run_voxpilot_updater.ps1",
                installer_path=installer_path,
                expected_sha256=expected_sha256,
                target_version=target_version,
                result_file=self.install_result_file,
                wait_pid=current_pid,
                install_dir=install_dir,
                relaunch_exe=relaunch_exe,
                relaunch=relaunch,
            )

            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

            subprocess.Popen(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-WindowStyle",
                    "Hidden",
                    "-File",
                    str(ps_script),
                ],
                cwd=str(self.updates_dir),
                creationflags=creationflags,
                close_fds=True,
            )

            self._state = "installing"
            self._status_message = (
                f"External updater launched for VoxPilot v{target_version}. "
                "Closing VoxPilot so the installer can upgrade the application..."
            )
            payload = self._build_status_payload()

        if shutdown_app:
            if shutdown_callback is not None:
                shutdown_callback()
            else:
                def _delayed_shutdown() -> None:
                    time.sleep(0.8)
                    os._exit(0)

                threading.Thread(target=_delayed_shutdown, daemon=True).start()

        return payload


_shared_update_service: Optional[UpdateService] = None
_shared_lock = threading.Lock()


def get_shared_update_service() -> UpdateService:
    """Return the singleton UpdateService instance for the FastAPI application."""
    global _shared_update_service
    with _shared_lock:
        if _shared_update_service is None:
            _shared_update_service = UpdateService()
        return _shared_update_service

