"""Encrypted BYOK credential storage using Windows DPAPI (Phase 8B)."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from backend.ai.email_assistant import CredentialStorageError, DPAPICredentialStore
from backend.ai.providers.base import (
    CredentialStoreInterface,
    ProviderConfigurationError,
)
from backend.paths import get_credentials_dir

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[^a-zA-Z0-9_\-]")
_SECRET_PATTERN = re.compile(
    r"(?:\b(?:sk-[A-Za-z0-9_\-]{8,}|AIza[A-Za-z0-9_\-]{12,}|"
    r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b|"
    r"\b(?:x-voxpilot-desktop-token|x-api-key|x-goog-api-key|authorization)\s*[:=]\s*(?:Bearer\s+)?[^\s,;\"']+|"
    r"\bBearer\s+[A-Za-z0-9_\-\.~+/]+=*)",
    re.IGNORECASE,
)


def sanitize_secret(text: str, secret: Optional[str] = None) -> str:
    """Redact API keys, Bearer tokens, or header secrets from error strings and log messages."""
    cleaned = str(text or "")
    if secret and len(secret) >= 4:
        cleaned = cleaned.replace(secret, "[REDACTED]")
    cleaned = _SECRET_PATTERN.sub("[REDACTED]", cleaned)
    return cleaned


class SecretRedactingLogFilter(logging.Filter):
    """Logging filter that redacts API keys, session tokens, and secrets before emission."""

    @staticmethod
    def _clean(value: str) -> str:
        from backend.database.models import scrub_text_secrets

        return sanitize_secret(scrub_text_secrets(value))

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._clean(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: self._clean(v) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    self._clean(a) if isinstance(a, str) else a
                    for a in record.args
                )
        return True


def _safe_token(value: str, fallback: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return fallback
    return _TOKEN_RE.sub("_", raw)


class WindowsDPAPIProviderCredentialStore(CredentialStoreInterface):
    """Stores profile-isolated BYOK secrets encrypted via Windows DPAPI.

    Encrypted blobs are written to dedicated files under the user data
    directory (outside SQLite and outside ordinary provider config JSON).
    """

    def __init__(
        self,
        storage_dir: Optional[Path] = None,
        dpapi_backend: Optional[DPAPICredentialStore] = None,
    ) -> None:
        if storage_dir is not None:
            self.storage_dir = Path(storage_dir)
        else:
            self.storage_dir = get_credentials_dir()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._dpapi = dpapi_backend or DPAPICredentialStore(storage_dir=self.storage_dir)

    @staticmethod
    def build_ref(profile_id: str, vendor: str) -> str:
        safe_profile = _safe_token(profile_id, "default")
        safe_vendor = _safe_token(vendor.lower(), "openai")
        return f"byok:{safe_profile}:{safe_vendor}"

    def _blob_path(self, profile_id: str, vendor: str) -> Path:
        safe_profile = _safe_token(profile_id, "default")
        safe_vendor = _safe_token(vendor.lower(), "openai")
        return self.storage_dir / f"byok_{safe_profile}_{safe_vendor}.dpapi.json"

    @staticmethod
    def _parse_ref(credential_ref: str) -> Optional[tuple[str, str]]:
        raw = (credential_ref or "").strip()
        parts = raw.split(":")
        if len(parts) != 3 or parts[0] != "byok" or not parts[1] or not parts[2]:
            return None
        return parts[1], parts[2]

    def _verify_ref_ownership(self, profile_id: str, credential_ref: str) -> Optional[tuple[str, str]]:
        parsed = self._parse_ref(credential_ref)
        if parsed is None:
            return None
        ref_profile, ref_vendor = parsed
        if ref_profile != _safe_token(profile_id, "default"):
            return None
        return ref_profile, ref_vendor

    def store_credential(self, profile_id: str, vendor: str, secret: str) -> str:
        cleaned_secret = (secret or "").strip()
        if not cleaned_secret:
            raise ProviderConfigurationError("API key cannot be empty.")

        safe_profile = _safe_token(profile_id, "default")
        safe_vendor = _safe_token(vendor.lower(), "openai")
        ref = self.build_ref(safe_profile, safe_vendor)

        try:
            protected_b64 = self._dpapi.protect_secret(cleaned_secret)
        except CredentialStorageError as exc:
            raise ProviderConfigurationError(
                f"Failed to encrypt credential with Windows DPAPI: {sanitize_secret(str(exc), cleaned_secret)}"
            ) from exc

        payload = {
            "version": 1,
            "profile_id": safe_profile,
            "vendor": safe_vendor,
            "credential_ref": ref,
            "dpapi_ciphertext": protected_b64,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        path = self._blob_path(safe_profile, safe_vendor)
        tmp_path = path.with_suffix(".dpapi.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp_path, path)
        logger.info("Stored DPAPI-encrypted BYOK credential ref='%s' for profile='%s'", ref, safe_profile)
        return ref

    def has_credential(self, profile_id: str, credential_ref: str) -> bool:
        verified = self._verify_ref_ownership(profile_id, credential_ref)
        if verified is None:
            return False
        ref_profile, ref_vendor = verified
        path = self._blob_path(ref_profile, ref_vendor)
        if not path.is_file():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return False
        return bool(data.get("dpapi_ciphertext")) and data.get("profile_id") == ref_profile

    def resolve_credential(self, profile_id: str, credential_ref: str) -> Optional[str]:
        parsed = self._parse_ref(credential_ref)
        if parsed is None:
            return None
        ref_profile, ref_vendor = parsed
        safe_profile = _safe_token(profile_id, "default")
        if ref_profile != safe_profile:
            raise ProviderConfigurationError(
                "Cross-profile credential access denied."
            )

        path = self._blob_path(ref_profile, ref_vendor)
        if not path.is_file():
            return None

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ProviderConfigurationError(
                f"Stored credential metadata is unreadable for profile '{safe_profile}'."
            ) from exc

        if data.get("profile_id") != safe_profile or data.get("vendor") != ref_vendor:
            raise ProviderConfigurationError("Credential ownership mismatch.")

        ciphertext = data.get("dpapi_ciphertext")
        if not isinstance(ciphertext, str) or not ciphertext.strip():
            raise ProviderConfigurationError("Stored DPAPI credential payload is empty or invalid.")

        try:
            secret = self._dpapi.unprotect_secret(ciphertext)
        except CredentialStorageError as exc:
            raise ProviderConfigurationError(
                f"Unable to decrypt stored BYOK credential via Windows DPAPI: {exc}"
            ) from exc

        if not secret or not secret.strip():
            raise ProviderConfigurationError("Decrypted BYOK credential is empty.")
        return secret.strip()

    def delete_credential(self, profile_id: str, credential_ref: str) -> bool:
        verified = self._verify_ref_ownership(profile_id, credential_ref)
        if verified is None:
            return False
        ref_profile, ref_vendor = verified
        path = self._blob_path(ref_profile, ref_vendor)
        if not path.is_file():
            return False
        try:
            path.unlink()
            logger.info("Deleted BYOK credential ref='%s' for profile='%s'", credential_ref, ref_profile)
            return True
        except OSError as exc:
            raise ProviderConfigurationError(f"Failed to delete stored credential: {exc}") from exc
