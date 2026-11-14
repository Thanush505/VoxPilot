"""VoxPilot Local Multi-Profile package (Phase 2B)."""

from backend.profiles.service import (
    DefaultProfileDeletionError,
    DuplicateProfileError,
    InvalidProfileError,
    ProfileNotFoundError,
    ProfilePrivacyError,
    ProfileService,
    get_shared_profile_service,
    normalize_profile_name,
    reset_shared_profile_service,
)

__all__ = [
    "DefaultProfileDeletionError",
    "DuplicateProfileError",
    "InvalidProfileError",
    "ProfileNotFoundError",
    "ProfilePrivacyError",
    "ProfileService",
    "get_shared_profile_service",
    "normalize_profile_name",
    "reset_shared_profile_service",
]
