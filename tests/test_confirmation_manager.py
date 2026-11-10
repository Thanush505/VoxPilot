"""
VoxPilot - Unit Tests for ConfirmationManager and Trust Boundary
Verifies cryptographic tokens, pending state, user approval, expiration,
replay prevention, action mismatches, and parameter tampering checks.
"""

import time
import pytest
from backend.automation.confirmation_manager import (
    ConfirmationManager,
    ConfirmationStatus,
    InvalidConfirmationError,
    MissingUserApprovalError,
    ConfirmationRejectedError,
    ConfirmationExpiredError,
    ReplayedConfirmationError,
    ActionMismatchConfirmationError,
    TamperedActionConfirmationError,
    get_shared_confirmation_manager,
    reset_shared_confirmation_manager,
)


@pytest.fixture
def confirmation_mgr():
    """Fresh ConfirmationManager fixture."""
    return ConfirmationManager(default_timeout_seconds=300)


def test_create_confirmation_generates_token_and_pending_state(confirmation_mgr: ConfirmationManager):
    """Verify create_confirmation generates a secure token with PENDING status."""
    action = "delete_file"
    params = {"file_path": "C:/Users/test/Documents/report.docx"}

    pending = confirmation_mgr.create_confirmation(action, params)

    assert pending.token is not None
    assert len(pending.token) >= 32
    assert pending.action_type == action
    assert pending.action_params == params
    assert pending.status == ConfirmationStatus.PENDING
    assert pending.is_expired is False


def test_valid_approval_consumed_once(confirmation_mgr: ConfirmationManager):
    """Verify that an approved token is successfully consumed once."""
    action = "delete_file"
    params = {"file_path": "C:/Users/test/Documents/report.docx"}

    pending = confirmation_mgr.create_confirmation(action, params)
    confirmation_mgr.approve_confirmation(pending.token)

    assert pending.status == ConfirmationStatus.APPROVED

    # Consume
    consumed = confirmation_mgr.validate_and_consume(pending.token, action, params)
    assert consumed.status == ConfirmationStatus.CONSUMED


def test_missing_user_approval_rejected(confirmation_mgr: ConfirmationManager):
    """Verify that consuming a token that is still PENDING raises MissingUserApprovalError."""
    action = "delete_folder"
    params = {"folder_path": "C:/Users/test/Documents/project"}

    pending = confirmation_mgr.create_confirmation(action, params)

    # Attempt to consume without user approval
    with pytest.raises(MissingUserApprovalError) as exc_info:
        confirmation_mgr.validate_and_consume(pending.token, action, params)

    assert "pending user approval" in str(exc_info.value).lower()


def test_rejected_approval_rejected(confirmation_mgr: ConfirmationManager):
    """Verify that an explicitly rejected token raises ConfirmationRejectedError upon consumption."""
    action = "delete_file"
    params = {"file_path": "C:/Users/test/Documents/critical.db"}

    pending = confirmation_mgr.create_confirmation(action, params)
    confirmation_mgr.reject_confirmation(pending.token)

    assert pending.status == ConfirmationStatus.REJECTED

    with pytest.raises(ConfirmationRejectedError) as exc_info:
        confirmation_mgr.validate_and_consume(pending.token, action, params)

    assert "rejected" in str(exc_info.value).lower()


def test_expired_approval_rejected(confirmation_mgr: ConfirmationManager):
    """Verify that an expired token raises ConfirmationExpiredError."""
    action = "format_drive"
    params = {"drive": "D:"}

    # Set 0-second timeout so it immediately expires
    pending = confirmation_mgr.create_confirmation(action, params, timeout_seconds=0)
    time.sleep(0.01)

    assert pending.is_expired is True

    # Attempt to approve an expired token
    with pytest.raises(ConfirmationExpiredError):
        confirmation_mgr.approve_confirmation(pending.token)

    # Attempt to validate and consume
    with pytest.raises(ConfirmationExpiredError):
        confirmation_mgr.validate_and_consume(pending.token, action, params)


def test_replayed_confirmation_rejected(confirmation_mgr: ConfirmationManager):
    """Verify that a consumed token cannot be reused/replayed."""
    action = "submit_order"
    params = {"item_id": 1234, "amount": 99.99}

    pending = confirmation_mgr.create_confirmation(action, params)
    confirmation_mgr.approve_confirmation(pending.token)

    # First consumption succeeds
    confirmation_mgr.validate_and_consume(pending.token, action, params)

    # Second consumption must be rejected with ReplayedConfirmationError
    with pytest.raises(ReplayedConfirmationError) as exc_info:
        confirmation_mgr.validate_and_consume(pending.token, action, params)

    assert "already been consumed" in str(exc_info.value).lower()


def test_approval_for_different_action_rejected(confirmation_mgr: ConfirmationManager):
    """Verify that a token approved for action A cannot be used for action B."""
    action_a = "preview_file"
    action_b = "delete_file"
    params = {"file_path": "C:/Users/test/Documents/file.txt"}

    pending = confirmation_mgr.create_confirmation(action_a, params)
    confirmation_mgr.approve_confirmation(pending.token)

    # Attempt to consume using action_b
    with pytest.raises(ActionMismatchConfirmationError) as exc_info:
        confirmation_mgr.validate_and_consume(pending.token, action_b, params)

    assert "issued for action" in str(exc_info.value).lower()


def test_modified_action_parameters_after_approval_rejected(confirmation_mgr: ConfirmationManager):
    """Verify that modifying action parameters after approval raises TamperedActionConfirmationError."""
    action = "delete_file"
    approved_params = {"file_path": "C:/Users/test/Documents/harmless.txt"}
    tampered_params = {"file_path": "C:/Users/test/Documents/passwords.txt"}

    pending = confirmation_mgr.create_confirmation(action, approved_params)
    confirmation_mgr.approve_confirmation(pending.token)

    # Attempt to consume with modified parameters
    with pytest.raises(TamperedActionConfirmationError) as exc_info:
        confirmation_mgr.validate_and_consume(pending.token, action, tampered_params)

    assert "modified since approval" in str(exc_info.value).lower()


def test_invalid_or_nonexistent_token_rejected(confirmation_mgr: ConfirmationManager):
    """Verify that non-existent tokens raise InvalidConfirmationError."""
    with pytest.raises(InvalidConfirmationError):
        confirmation_mgr.approve_confirmation("non-existent-token")

    with pytest.raises(InvalidConfirmationError):
        confirmation_mgr.validate_and_consume("non-existent-token", "some_action", {})


def test_shared_confirmation_manager_singleton():
    """Verify get_shared_confirmation_manager returns singleton and reset_shared clears it."""
    reset_shared_confirmation_manager()
    mgr1 = get_shared_confirmation_manager()
    mgr2 = get_shared_confirmation_manager()
    assert mgr1 is mgr2

    reset_shared_confirmation_manager()
    mgr3 = get_shared_confirmation_manager()
    assert mgr3 is not mgr1

