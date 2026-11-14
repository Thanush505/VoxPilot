"""
VoxPilot - BYOK & VoxPilot Cloud Provider Adapters (Phases 8A–8B)

BYOKProvider integrates encrypted DPAPI credential resolution and the OpenAI
adapter (Phase 8B), while VoxPilotCloudProvider remains an honest unavailable
placeholder until managed cloud integration.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.ai.providers.base import (
    AIProvider,
    AIProviderRequest,
    AIProviderResponse,
    CredentialStoreInterface,
    PROVIDER_BYOK,
    PROVIDER_VOXPILOT_CLOUD,
    ProviderCapabilities,
    ProviderConfigurationError,
    ProviderHealth,
    ProviderModelInfo,
    ProviderNotConfiguredError,
    assert_no_plaintext_secrets,
)
from backend.ai.providers.openai_adapter import DEFAULT_OPENAI_MODEL, OpenAIBYOKAdapter

SUPPORTED_BYOK_VENDORS = ("openai", "anthropic", "google", "openai_compatible")


class BYOKProvider(AIProvider):
    """Bring-Your-Own-Key provider adapter (OpenAI supported in Phase 8B)."""

    provider_id = PROVIDER_BYOK
    display_name = "BYOK (Bring Your Own Key)"
    capabilities = ProviderCapabilities(
        streaming=False,
        structured_output=True,
        tool_calling=False,
        vision=False,
        local_only=False,
    )

    def __init__(
        self,
        credential_store: Optional[CredentialStoreInterface] = None,
        openai_adapter: Optional[OpenAIBYOKAdapter] = None,
    ) -> None:
        self._credential_store = credential_store
        self._openai_adapter = openai_adapter or OpenAIBYOKAdapter()

    def validate_config(self, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        assert_no_plaintext_secrets(config)
        cfg = dict(config or {})
        vendor = cfg.get("byok_vendor") or cfg.get("vendor")
        if vendor is not None:
            vendor_str = str(vendor).strip().lower()
            if vendor_str and vendor_str not in SUPPORTED_BYOK_VENDORS:
                raise ProviderConfigurationError(
                    f"Unsupported BYOK vendor '{vendor}'. Supported: {', '.join(SUPPORTED_BYOK_VENDORS)}."
                )
        else:
            vendor_str = None
        return {
            "byok_vendor": vendor_str or None,
            "byok_model": str(cfg["byok_model"]).strip() if cfg.get("byok_model") else None,
            "byok_credential_ref": str(cfg["byok_credential_ref"]).strip() if cfg.get("byok_credential_ref") else None,
        }

    def health_check(self, config: Optional[Dict[str, Any]] = None) -> ProviderHealth:
        clean = self.validate_config(config)
        vendor = clean.get("byok_vendor")
        cred_ref = clean.get("byok_credential_ref")
        profile_id = str((config or {}).get("profile_id") or "default").strip() or "default"
        model = clean.get("byok_model") or (DEFAULT_OPENAI_MODEL if vendor == "openai" else None)

        if not vendor or not cred_ref:
            return ProviderHealth(
                provider_id=self.provider_id,
                available=False,
                configured=False,
                status="not_configured",
                reason="No BYOK vendor or encrypted credential is configured for this profile.",
                active_model=model,
            )

        if vendor != "openai":
            return ProviderHealth(
                provider_id=self.provider_id,
                available=False,
                configured=False,
                status="not_configured",
                reason=f"BYOK vendor '{vendor}' is not yet implemented.",
                active_model=model,
            )

        if self._credential_store is None or not self._credential_store.has_credential(profile_id, cred_ref):
            return ProviderHealth(
                provider_id=self.provider_id,
                available=False,
                configured=False,
                status="not_configured",
                reason="Encrypted OpenAI API key is missing for this profile.",
                active_model=model,
            )

        return ProviderHealth(
            provider_id=self.provider_id,
            available=True,
            configured=True,
            status="ready",
            reason="OpenAI BYOK credential configured.",
            active_model=model,
        )

    def list_models(self, config: Optional[Dict[str, Any]] = None) -> List[ProviderModelInfo]:
        clean = self.validate_config(config)
        vendor = clean.get("byok_vendor")
        if vendor in (None, "openai"):
            return self._openai_adapter.list_models()
        return []

    def generate(
        self,
        request: AIProviderRequest,
        config: Optional[Dict[str, Any]] = None,
    ) -> AIProviderResponse:
        clean = self.validate_config(config)
        vendor = clean.get("byok_vendor")
        cred_ref = clean.get("byok_credential_ref")
        profile_id = str((config or {}).get("profile_id") or "default").strip() or "default"

        if not vendor or not cred_ref or self._credential_store is None:
            raise ProviderNotConfiguredError(
                "BYOK provider is not configured with an encrypted credential for this profile."
            )
        if vendor != "openai":
            raise ProviderNotConfiguredError(
                f"BYOK vendor '{vendor}' is not yet supported."
            )

        api_key = self._credential_store.resolve_credential(profile_id, cred_ref)
        if not api_key:
            raise ProviderNotConfiguredError(
                "Encrypted OpenAI API key is missing for this profile."
            )

        return self._openai_adapter.generate(
            request,
            api_key=api_key,
            model=clean.get("byok_model"),
        )


class VoxPilotCloudProvider(AIProvider):
    """Managed VoxPilot Cloud provider placeholder (explicitly unavailable in Phase 8A)."""

    provider_id = PROVIDER_VOXPILOT_CLOUD
    display_name = "VoxPilot Cloud"
    capabilities = ProviderCapabilities(
        streaming=True,
        structured_output=True,
        tool_calling=True,
        vision=True,
        local_only=False,
    )

    def validate_config(self, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        assert_no_plaintext_secrets(config)
        cfg = dict(config or {})
        tier = str(cfg["cloud_tier"]).strip() if cfg.get("cloud_tier") else None
        return {"cloud_tier": tier}

    def health_check(self, config: Optional[Dict[str, Any]] = None) -> ProviderHealth:
        return ProviderHealth(
            provider_id=self.provider_id,
            available=False,
            configured=False,
            status="unavailable",
            reason="VoxPilot Cloud managed service is not yet available in this local-first build.",
            active_model=None,
        )

    def list_models(self, config: Optional[Dict[str, Any]] = None) -> List[ProviderModelInfo]:
        return []

    def generate(
        self,
        request: AIProviderRequest,
        config: Optional[Dict[str, Any]] = None,
    ) -> AIProviderResponse:
        raise ProviderNotConfiguredError(
            "VoxPilot Cloud is not available in Phase 8A."
        )
