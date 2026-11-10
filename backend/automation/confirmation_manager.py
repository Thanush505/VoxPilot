"""
VoxPilot - Action Confirmation and Trust Boundary Subsystem
Provides cryptographically secure, action-specific, time-bounded, single-use
confirmation management to prevent autonomous LLM execution of consequential
or destructive desktop, filesystem, and browser operations.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


# ==============================================================================
# Confirmation Status Enumeration
# ==============================================================================

class ConfirmationStatus(str, Enum):
    """Lifecycle states of a confirmation token."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CONSUMED = "consumed"


# ==============================================================================
# Exceptions
# ==============================================================================

class ConfirmationError(Exception):
    """Base exception for all confirmation system errors."""
    pass


class ConfirmationRequiredError(ConfirmationError):
    """Raised when an operation requires user confirmation and none or unapproved was provided."""
    def __init__(
        self,
        message: str,
        token: Optional[str] = None,
        action_type: Optional[str] = None,
        action_params: Optional[Dict[str, Any]] = None,
        target: Optional[str] = None,
        operation: Optional[str] = None,
    ):
        super().__init__(message)
        self.token = token
        self.action_type = action_type
        self.action_params = action_params or {}
        self.target = target
        self.operation = operation
        self.requires_confirmation = True


class InvalidConfirmationError(ConfirmationError):
    """Raised when a confirmation token is unknown or malformed."""
    pass


class MissingUserApprovalError(ConfirmationError):
    """Raised when a token exists but has not received explicit human user approval."""
    pass


class ConfirmationRejectedError(ConfirmationError):
    """Raised when an action was explicitly rejected by the human user."""
    pass


class ConfirmationExpiredError(ConfirmationError):
    """Raised when a confirmation token is used after its expiration time."""
    pass


class ReplayedConfirmationError(ConfirmationError):
    """Raised when an already consumed confirmation token is reused."""
    pass


class ActionMismatchConfirmationError(ConfirmationError):
    """Raised when a token is presented for a different action type than approved."""
    pass


class TamperedActionConfirmationError(ConfirmationError):
    """Raised when the parameters of an action do not match the parameters approved."""
    pass


# ==============================================================================
# Helper Functions
# ==============================================================================

def compute_action_hash(action_type: str, action_params: Dict[str, Any]) -> str:
    """Compute a deterministic SHA-256 hash of the action type and parameters."""
    # Exclude metadata / transient approval tokens from the security parameter hash
    clean_params = {
        k: v for k, v in action_params.items()
        if k not in ("confirmation_token", "confirmed")
    }
    canonical_payload = {
        "action_type": action_type,
        "action_params": clean_params,
    }
    canonical_json = json.dumps(canonical_payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


# ==============================================================================
# Pending Confirmation Model
# ==============================================================================

@dataclass
class PendingConfirmation:
    """Metadata describing a pending or processed user confirmation request."""
    token: str
    action_type: str
    action_params: Dict[str, Any]
    action_hash: str
    created_at: float
    expires_at: float
    status: ConfirmationStatus = ConfirmationStatus.PENDING
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            "token": self.token,
            "action_type": self.action_type,
            "action_params": self.action_params,
            "action_hash": self.action_hash,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "status": self.status.value,
            "details": self.details,
            "is_expired": self.is_expired,
        }


# ==============================================================================
# Confirmation Manager
# ==============================================================================

class ConfirmationManager:
    """Manages creation, approval, validation, and single-use consumption of confirmation tokens."""

    def __init__(self, default_timeout_seconds: int = 300):
        self._default_timeout_seconds = default_timeout_seconds
        self._confirmations: Dict[str, PendingConfirmation] = {}

    def create_confirmation(
        self,
        action_type: str,
        action_params: Dict[str, Any],
        details: Optional[Dict[str, Any]] = None,
        timeout_seconds: Optional[int] = None,
    ) -> PendingConfirmation:
        """Generate a cryptographically secure, time-bounded confirmation token for an action."""
        token = secrets.token_urlsafe(32)
        now = time.time()
        timeout = timeout_seconds if timeout_seconds is not None else self._default_timeout_seconds
        expires_at = now + timeout

        action_hash = compute_action_hash(action_type, action_params)

        confirmation = PendingConfirmation(
            token=token,
            action_type=action_type,
            action_params=action_params,
            action_hash=action_hash,
            created_at=now,
            expires_at=expires_at,
            status=ConfirmationStatus.PENDING,
            details=details or {},
        )
        self._confirmations[token] = confirmation
        logger.info(
            f"Created pending confirmation token for action '{action_type}' (expires in {timeout}s)"
        )
        return confirmation

    def approve_confirmation(self, token: str) -> PendingConfirmation:
        """Mark a pending confirmation as explicitly approved by the human user."""
        if not token or token not in self._confirmations:
            raise InvalidConfirmationError(f"Confirmation token '{token}' not found.")

        conf = self._confirmations[token]

        if conf.status == ConfirmationStatus.CONSUMED:
            raise ReplayedConfirmationError(f"Cannot approve token '{token}': already consumed.")
        if conf.status == ConfirmationStatus.REJECTED:
            raise ConfirmationRejectedError(f"Cannot approve token '{token}': already rejected.")
        if conf.is_expired:
            conf.status = ConfirmationStatus.EXPIRED
            raise ConfirmationExpiredError(f"Cannot approve token '{token}': token has expired.")

        conf.status = ConfirmationStatus.APPROVED
        logger.info(f"Confirmation token '{token}' for action '{conf.action_type}' approved by user.")
        return conf

    def reject_confirmation(self, token: str) -> PendingConfirmation:
        """Mark a pending confirmation as explicitly rejected by the human user."""
        if not token or token not in self._confirmations:
            raise InvalidConfirmationError(f"Confirmation token '{token}' not found.")

        conf = self._confirmations[token]
        if conf.status == ConfirmationStatus.CONSUMED:
            raise ReplayedConfirmationError(f"Cannot reject token '{token}': already consumed.")

        conf.status = ConfirmationStatus.REJECTED
        logger.info(f"Confirmation token '{token}' for action '{conf.action_type}' rejected by user.")
        return conf

    def validate_and_consume(
        self,
        token: str,
        action_type: str,
        action_params: Dict[str, Any],
    ) -> PendingConfirmation:
        """Verify that a token was approved for this specific action and consume it (single-use).

        Raises:
            InvalidConfirmationError: If token does not exist.
            ConfirmationExpiredError: If token expired.
            ReplayedConfirmationError: If token was already consumed.
            ConfirmationRejectedError: If token was rejected by user.
            MissingUserApprovalError: If token is still pending user approval.
            ActionMismatchConfirmationError: If action_type doesn't match.
            TamperedActionConfirmationError: If action_params do not match approved parameters.
        """
        if not token or token not in self._confirmations:
            raise InvalidConfirmationError(f"Confirmation token '{token}' is invalid or does not exist.")

        conf = self._confirmations[token]

        # 1. Replay check
        if conf.status == ConfirmationStatus.CONSUMED:
            raise ReplayedConfirmationError(
                f"Confirmation token '{token}' has already been consumed and cannot be reused."
            )

        # 2. Expiration check
        if conf.is_expired or conf.status == ConfirmationStatus.EXPIRED:
            conf.status = ConfirmationStatus.EXPIRED
            raise ConfirmationExpiredError(
                f"Confirmation token '{token}' has expired (expired at {conf.expires_at})."
            )

        # 3. Rejection check
        if conf.status == ConfirmationStatus.REJECTED:
            raise ConfirmationRejectedError(
                f"Action '{action_type}' was explicitly rejected by the user."
            )

        # 4. User approval status check
        if conf.status == ConfirmationStatus.PENDING:
            raise MissingUserApprovalError(
                f"Action '{action_type}' requires explicit human approval before execution. "
                f"Token '{token}' is currently pending user approval."
            )

        if conf.status != ConfirmationStatus.APPROVED:
            raise InvalidConfirmationError(
                f"Confirmation token '{token}' is in invalid state '{conf.status}'."
            )

        # 5. Action type match check
        if conf.action_type != action_type:
            raise ActionMismatchConfirmationError(
                f"Token was issued for action '{conf.action_type}', but presented for action '{action_type}'."
            )

        # 6. Action parameters tamper check
        current_hash = compute_action_hash(action_type, action_params)
        if current_hash != conf.action_hash:
            raise TamperedActionConfirmationError(
                f"Action parameters for '{action_type}' have been modified since approval was granted."
            )

        # Transition to CONSUMED (single-use)
        conf.status = ConfirmationStatus.CONSUMED
        logger.info(f"Confirmation token '{token}' successfully consumed for action '{action_type}'.")
        return conf

    def get_confirmation(self, token: str) -> Optional[PendingConfirmation]:
        """Look up confirmation record by token."""
        return self._confirmations.get(token)

    def cleanup_expired(self) -> int:
        """Mark all timed-out pending confirmations as EXPIRED and return count."""
        count = 0
        now = time.time()
        for conf in self._confirmations.values():
            if conf.status == ConfirmationStatus.PENDING and now > conf.expires_at:
                conf.status = ConfirmationStatus.EXPIRED
                count += 1
        return count

    def clear(self) -> None:
        """Clear all confirmation tokens."""
        self._confirmations.clear()


# ==============================================================================
# Global Shared ConfirmationManager
# ==============================================================================

_SHARED_CONFIRMATION_MANAGER: Optional[ConfirmationManager] = None


def get_shared_confirmation_manager() -> ConfirmationManager:
    """Retrieve or initialize the active shared ConfirmationManager singleton."""
    global _SHARED_CONFIRMATION_MANAGER
    if _SHARED_CONFIRMATION_MANAGER is None:
        _SHARED_CONFIRMATION_MANAGER = ConfirmationManager()
    return _SHARED_CONFIRMATION_MANAGER


def reset_shared_confirmation_manager() -> None:
    """Reset the shared ConfirmationManager singleton."""
    global _SHARED_CONFIRMATION_MANAGER
    if _SHARED_CONFIRMATION_MANAGER is not None:
        _SHARED_CONFIRMATION_MANAGER.clear()
        _SHARED_CONFIRMATION_MANAGER = None

