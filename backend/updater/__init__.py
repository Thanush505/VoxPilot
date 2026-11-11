"""
VoxPilot - In-App Software Update Package
Provides GitHub release checking, secure installer download, SHA-256 verification,
Authenticode signature inspection, and detached Windows updater orchestration.
"""

from backend.updater.update_service import (
    UpdateService,
    UpdateSettingsManager,
    get_shared_update_service,
)

__all__ = [
    "UpdateService",
    "UpdateSettingsManager",
    "get_shared_update_service",
]

