"""VoxPilot Personalization package (Phase 2A)."""

from backend.personalization.service import (
    DuplicatePreferenceError,
    InvalidPreferenceCategoryError,
    InvalidPreferenceValueError,
    PersonalizationService,
    PreferencePrivacyError,
    get_shared_personalization_service,
    normalize_preference_key,
    reset_shared_personalization_service,
)

__all__ = [
    "DuplicatePreferenceError",
    "InvalidPreferenceCategoryError",
    "InvalidPreferenceValueError",
    "PersonalizationService",
    "PreferencePrivacyError",
    "get_shared_personalization_service",
    "normalize_preference_key",
    "reset_shared_personalization_service",
]
