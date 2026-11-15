"""
VoxPilot - Profile-Aware AI Provider Registry & Configuration Manager (Phase 8A)
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlmodel import Session

from backend.ai.providers.anthropic_adapter import (
    AnthropicBYOKAdapter,
    DEFAULT_ANTHROPIC_MODEL,
)
from backend.ai.providers.base import (
    AIProvider,
    AIProviderRequest,
    AIProviderResponse,
    CredentialStoreInterface,
    PROVIDER_BYOK,
    PROVIDER_LOCAL,
    ProviderConfigurationError,
    ProviderNotConfiguredError,
    ProviderUnavailableError,
    assert_no_plaintext_secrets,
)
from backend.ai.providers.credentials import WindowsDPAPIProviderCredentialStore
from backend.ai.providers.gemini_adapter import (
    DEFAULT_GEMINI_MODEL,
    GeminiBYOKAdapter,
)
from backend.ai.providers.local_ollama import LocalOllamaProvider
from backend.ai.providers.openai_adapter import DEFAULT_OPENAI_MODEL, OpenAIBYOKAdapter
from backend.ai.providers.placeholders import (
    BYOKProvider,
    CONFIGURABLE_BYOK_VENDORS,
    VoxPilotCloudProvider,
)
from backend.database.connection import get_sync_engine
from backend.database.models import ProfileProviderConfigRecord
from backend.profiles.service import get_shared_profile_service


class AIProviderRegistry:
    """Registers AI providers and resolves profile-specific provider selection."""

    def __init__(
        self,
        engine=None,
        credential_store: Optional[CredentialStoreInterface] = None,
        openai_adapter: Optional[OpenAIBYOKAdapter] = None,
        anthropic_adapter: Optional[AnthropicBYOKAdapter] = None,
        gemini_adapter: Optional[GeminiBYOKAdapter] = None,
    ) -> None:
        self._engine = engine
        self._lock = threading.RLock()
        self._providers: Dict[str, AIProvider] = {}
        self.credential_store: CredentialStoreInterface = (
            credential_store if credential_store is not None else WindowsDPAPIProviderCredentialStore()
        )
        self.openai_adapter: OpenAIBYOKAdapter = (
            openai_adapter if openai_adapter is not None else OpenAIBYOKAdapter()
        )
        self.anthropic_adapter: AnthropicBYOKAdapter = (
            anthropic_adapter if anthropic_adapter is not None else AnthropicBYOKAdapter()
        )
        self.gemini_adapter: GeminiBYOKAdapter = (
            gemini_adapter if gemini_adapter is not None else GeminiBYOKAdapter()
        )
        self._byok_adapters: Dict[str, Any] = {
            "openai": self.openai_adapter,
            "anthropic": self.anthropic_adapter,
            "google": self.gemini_adapter,
        }
        self._byok_default_models: Dict[str, str] = {
            "openai": DEFAULT_OPENAI_MODEL,
            "anthropic": DEFAULT_ANTHROPIC_MODEL,
            "google": DEFAULT_GEMINI_MODEL,
        }
        self.register(LocalOllamaProvider())
        self.register(
            BYOKProvider(
                credential_store=self.credential_store,
                openai_adapter=self.openai_adapter,
                anthropic_adapter=self.anthropic_adapter,
                gemini_adapter=self.gemini_adapter,
            )
        )
        self.register(VoxPilotCloudProvider())

    def _get_engine(self):
        return self._engine if self._engine is not None else get_sync_engine()

    @staticmethod
    def _resolve_profile_id(profile_id: Optional[str] = None) -> str:
        if profile_id and profile_id.strip():
            return profile_id.strip()
        try:
            return get_shared_profile_service().get_active_profile_id() or "default"
        except Exception:
            return "default"

    def register(self, provider: AIProvider) -> None:
        with self._lock:
            self._providers[provider.provider_id] = provider

    def get_provider(self, provider_id: str) -> AIProvider:
        with self._lock:
            provider = self._providers.get(provider_id)
            if provider is None:
                raise ProviderConfigurationError(f"Unknown AI provider '{provider_id}'.")
            return provider

    def _get_internal_profile_config(self, profile_id: Optional[str] = None) -> Dict[str, Any]:
        pid = self._resolve_profile_id(profile_id)
        with Session(self._get_engine()) as session:
            rec = session.get(ProfileProviderConfigRecord, pid)
            if rec is None:
                rec = ProfileProviderConfigRecord(profile_id=pid)
            data = rec.to_dict()
            data["byok_credential_ref"] = rec.byok_credential_ref
            return data

    def get_profile_config(self, profile_id: Optional[str] = None) -> Dict[str, Any]:
        pid = self._resolve_profile_id(profile_id)
        with Session(self._get_engine()) as session:
            rec = session.get(ProfileProviderConfigRecord, pid)
            if rec is None:
                return ProfileProviderConfigRecord(profile_id=pid).to_dict()
            return rec.to_dict()

    def list_providers(self, profile_id: Optional[str] = None) -> List[Dict[str, Any]]:
        cfg = self._get_internal_profile_config(profile_id)
        active_id = cfg["active_provider_id"]
        result: List[Dict[str, Any]] = []
        with self._lock:
            providers = list(self._providers.values())
        for p in providers:
            health = p.health_check(cfg)
            models = [m.to_dict() for m in p.list_models(cfg)] if health.available else []
            caps = p.get_capabilities(cfg) if hasattr(p, "get_capabilities") else p.capabilities
            result.append({
                "provider_id": p.provider_id,
                "display_name": p.display_name,
                "is_active": p.provider_id == active_id,
                "capabilities": caps.to_dict(),
                "health": health.to_dict(),
                "models": models,
            })
        return result

    def validate_provider_config(
        self,
        provider_id: str,
        config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        assert_no_plaintext_secrets(config)
        provider = self.get_provider(provider_id)
        clean_cfg = provider.validate_config(config)
        health = provider.health_check(clean_cfg)
        return {
            "provider_id": provider_id,
            "valid": True,
            "normalized_config": clean_cfg,
            "health": health.to_dict(),
        }

    def _get_byok_adapter(self, vendor: str):
        norm = (vendor or "").strip().lower()
        adapter = self._byok_adapters.get(norm)
        if adapter is None:
            raise ProviderConfigurationError(
                f"Unsupported BYOK vendor '{vendor}'. Supported: {', '.join(CONFIGURABLE_BYOK_VENDORS)}."
            )
        return norm, adapter

    @staticmethod
    def _load_extra_config(rec: ProfileProviderConfigRecord) -> Dict[str, Any]:
        try:
            data = json.loads(rec.config_json) if rec.config_json else {}
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def select_provider(
        self,
        provider_id: str,
        *,
        profile_id: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        assert_no_plaintext_secrets(config)
        pid = self._resolve_profile_id(profile_id)
        existing_cfg = self._get_internal_profile_config(pid)
        incoming = dict(config or {})

        if provider_id == PROVIDER_BYOK:
            target_vendor = str(
                incoming.get("byok_vendor")
                or incoming.get("vendor")
                or existing_cfg.get("byok_vendor")
                or "openai"
            ).strip().lower()
            extra = existing_cfg.get("config") or {}
            vendor_map = extra.get("byok_vendors") if isinstance(extra.get("byok_vendors"), dict) else {}
            vendor_entry = vendor_map.get(target_vendor) if isinstance(vendor_map.get(target_vendor), dict) else {}
            default_model = self._byok_default_models.get(target_vendor, DEFAULT_OPENAI_MODEL)
            target_model = (
                incoming.get("byok_model")
                or vendor_entry.get("model")
                or (existing_cfg.get("byok_model") if existing_cfg.get("byok_vendor") == target_vendor else None)
                or default_model
            )
            incoming["byok_vendor"] = target_vendor
            incoming["byok_model"] = target_model
            incoming["byok_credential_ref"] = WindowsDPAPIProviderCredentialStore.build_ref(pid, target_vendor)

        merged_cfg = {**existing_cfg, **incoming, "profile_id": pid}

        provider = self.get_provider(provider_id)
        clean_cfg = provider.validate_config(merged_cfg)
        health = provider.health_check({**merged_cfg, **clean_cfg})
        if not health.available:
            raise ProviderUnavailableError(
                health.reason or f"Provider '{provider_id}' is currently unavailable."
            )

        with Session(self._get_engine()) as session:
            rec = session.get(ProfileProviderConfigRecord, pid)
            if rec is None:
                rec = ProfileProviderConfigRecord(profile_id=pid)
            rec.active_provider_id = provider_id
            if "local_model" in clean_cfg and clean_cfg["local_model"]:
                rec.local_model = clean_cfg["local_model"]
            if provider_id == PROVIDER_BYOK:
                if clean_cfg.get("byok_vendor"):
                    rec.byok_vendor = clean_cfg["byok_vendor"]
                if clean_cfg.get("byok_model"):
                    rec.byok_model = clean_cfg["byok_model"]
                if clean_cfg.get("byok_vendor"):
                    rec.byok_credential_ref = WindowsDPAPIProviderCredentialStore.build_ref(
                        pid, clean_cfg["byok_vendor"]
                    )
            rec.updated_at = datetime.now(timezone.utc)
            session.add(rec)
            session.commit()
            session.refresh(rec)
            return {
                "profile_config": rec.to_dict(),
                "health": health.to_dict(),
            }

    def configure_byok_vendor(
        self,
        vendor: str,
        *,
        profile_id: Optional[str] = None,
        api_key: str,
        model: Optional[str] = None,
        validate: bool = True,
    ) -> Dict[str, Any]:
        """Encrypt & store a BYOK key for a specific vendor without auto-activating BYOK."""
        norm_vendor, adapter = self._get_byok_adapter(vendor)
        cleaned_key = (api_key or "").strip()
        if not cleaned_key:
            raise ProviderConfigurationError(f"{adapter.display_name} API key cannot be empty.")

        pid = self._resolve_profile_id(profile_id)
        existing_cfg = self.get_profile_config(pid)
        extra = existing_cfg.get("config") or {}
        vendor_map = extra.get("byok_vendors") if isinstance(extra.get("byok_vendors"), dict) else {}
        vendor_entry = vendor_map.get(norm_vendor) if isinstance(vendor_map.get(norm_vendor), dict) else {}
        default_model = self._byok_default_models[norm_vendor]
        target_model = (
            (model or "").strip()
            or str(vendor_entry.get("model") or "").strip()
            or (
                (existing_cfg.get("byok_model") or "").strip()
                if existing_cfg.get("byok_vendor") == norm_vendor
                else ""
            )
            or default_model
        )

        if validate:
            health = adapter.validate_credential(cleaned_key, model=target_model)
        else:
            health = None

        cred_ref = self.credential_store.store_credential(pid, norm_vendor, cleaned_key)

        with Session(self._get_engine()) as session:
            rec = session.get(ProfileProviderConfigRecord, pid)
            if rec is None:
                rec = ProfileProviderConfigRecord(profile_id=pid, active_provider_id=PROVIDER_LOCAL)
            rec_extra = self._load_extra_config(rec)
            vendors_cfg = rec_extra.setdefault("byok_vendors", {})
            vendors_cfg[norm_vendor] = {"model": target_model}
            rec.config_json = json.dumps(rec_extra)

            # Update top-level BYOK selection only if no other BYOK vendor is actively selected.
            if rec.active_provider_id != PROVIDER_BYOK or not rec.byok_vendor or rec.byok_vendor == norm_vendor:
                rec.byok_vendor = norm_vendor
                rec.byok_model = target_model
                rec.byok_credential_ref = cred_ref

            rec.updated_at = datetime.now(timezone.utc)
            session.add(rec)
            session.commit()
            session.refresh(rec)
            cfg_dict = rec.to_dict()

        if health is None:
            health = self.get_provider(PROVIDER_BYOK).health_check({
                **self._get_internal_profile_config(pid),
                "byok_vendor": norm_vendor,
                "byok_model": target_model,
                "byok_credential_ref": cred_ref,
            })

        return {
            "profile_config": cfg_dict,
            "vendor": norm_vendor,
            "health": health.to_dict(),
            "validated": bool(validate),
        }

    def configure_byok_openai(
        self,
        *,
        profile_id: Optional[str] = None,
        api_key: str,
        model: Optional[str] = None,
        validate: bool = True,
    ) -> Dict[str, Any]:
        return self.configure_byok_vendor(
            "openai",
            profile_id=profile_id,
            api_key=api_key,
            model=model,
            validate=validate,
        )

    def validate_byok_connection(
        self,
        *,
        vendor: Optional[str] = None,
        profile_id: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Explicitly validate either a supplied key or the profile's stored DPAPI key for a vendor."""
        pid = self._resolve_profile_id(profile_id)
        cfg = self._get_internal_profile_config(pid)
        target_vendor = str(vendor or cfg.get("byok_vendor") or "openai").strip().lower()
        norm_vendor, adapter = self._get_byok_adapter(target_vendor)

        extra = cfg.get("config") or {}
        vendor_map = extra.get("byok_vendors") if isinstance(extra.get("byok_vendors"), dict) else {}
        vendor_entry = vendor_map.get(norm_vendor) if isinstance(vendor_map.get(norm_vendor), dict) else {}
        target_model = (
            (model or "").strip()
            or str(vendor_entry.get("model") or "").strip()
            or (
                (cfg.get("byok_model") or "").strip()
                if cfg.get("byok_vendor") == norm_vendor
                else ""
            )
            or self._byok_default_models[norm_vendor]
        )

        secret = (api_key or "").strip()
        if not secret:
            cred_ref = WindowsDPAPIProviderCredentialStore.build_ref(pid, norm_vendor)
            if not self.credential_store.has_credential(pid, cred_ref):
                raise ProviderNotConfiguredError(
                    f"No {adapter.display_name} API key is stored for this profile."
                )
            secret = self.credential_store.resolve_credential(pid, cred_ref) or ""
            if not secret:
                raise ProviderNotConfiguredError(
                    f"Stored {adapter.display_name} API key could not be found."
                )

        health = adapter.validate_credential(secret, model=target_model)
        return {
            "provider_id": PROVIDER_BYOK,
            "vendor": norm_vendor,
            "valid": True,
            "health": health.to_dict(),
        }

    def set_byok_vendor_model(
        self,
        vendor: str,
        *,
        profile_id: Optional[str] = None,
        model: str,
    ) -> Dict[str, Any]:
        """Update the selected model for a BYOK vendor without changing active provider."""
        norm_vendor, adapter = self._get_byok_adapter(vendor)
        target_model = (model or "").strip()
        if not target_model:
            raise ProviderConfigurationError(f"{adapter.display_name} model name cannot be empty.")

        pid = self._resolve_profile_id(profile_id)
        with Session(self._get_engine()) as session:
            rec = session.get(ProfileProviderConfigRecord, pid)
            if rec is None:
                rec = ProfileProviderConfigRecord(profile_id=pid, active_provider_id=PROVIDER_LOCAL)
            rec_extra = self._load_extra_config(rec)
            vendors_cfg = rec_extra.setdefault("byok_vendors", {})
            vendors_cfg[norm_vendor] = {"model": target_model}
            rec.config_json = json.dumps(rec_extra)

            if not rec.byok_vendor or rec.byok_vendor == norm_vendor:
                rec.byok_vendor = norm_vendor
                rec.byok_model = target_model
            rec.updated_at = datetime.now(timezone.utc)
            session.add(rec)
            session.commit()
            session.refresh(rec)
            return {"profile_config": rec.to_dict(), "vendor": norm_vendor}

    def set_byok_openai_model(
        self,
        *,
        profile_id: Optional[str] = None,
        model: str,
    ) -> Dict[str, Any]:
        return self.set_byok_vendor_model("openai", profile_id=profile_id, model=model)

    def discover_byok_vendor_models(
        self,
        vendor: str,
        *,
        profile_id: Optional[str] = None,
        use_remote: bool = True,
    ) -> List[Dict[str, Any]]:
        norm_vendor, adapter = self._get_byok_adapter(vendor)
        pid = self._resolve_profile_id(profile_id)
        cred_ref = WindowsDPAPIProviderCredentialStore.build_ref(pid, norm_vendor)
        if use_remote and hasattr(adapter, "discover_models") and self.credential_store.has_credential(pid, cred_ref):
            secret = self.credential_store.resolve_credential(pid, cred_ref)
            if secret:
                return [m.to_dict() for m in adapter.discover_models(secret)]
        return [m.to_dict() for m in adapter.list_models()]

    def list_byok_vendors(self, profile_id: Optional[str] = None) -> List[Dict[str, Any]]:
        pid = self._resolve_profile_id(profile_id)
        cfg = self.get_profile_config(pid)
        active_id = cfg.get("active_provider_id") or PROVIDER_LOCAL
        active_vendor = cfg.get("byok_vendor")
        extra = cfg.get("config") or {}
        vendor_map = extra.get("byok_vendors") if isinstance(extra.get("byok_vendors"), dict) else {}

        vendors_out: List[Dict[str, Any]] = []
        for vendor in CONFIGURABLE_BYOK_VENDORS:
            adapter = self._byok_adapters[vendor]
            cred_ref = WindowsDPAPIProviderCredentialStore.build_ref(pid, vendor)
            configured = self.credential_store.has_credential(pid, cred_ref)
            vendor_entry = vendor_map.get(vendor) if isinstance(vendor_map.get(vendor), dict) else {}
            selected_model = (
                str(vendor_entry.get("model") or "").strip()
                or (
                    str(cfg.get("byok_model") or "").strip()
                    if active_vendor == vendor
                    else ""
                )
                or self._byok_default_models[vendor]
            )
            vendors_out.append({
                "vendor": vendor,
                "display_name": adapter.display_name,
                "configured": configured,
                "is_active": active_id == PROVIDER_BYOK and active_vendor == vendor,
                "selected_model": selected_model,
                "capabilities": adapter.capabilities.to_dict(),
                "models": [m.to_dict() for m in adapter.list_models()],
            })
        return vendors_out

    def remove_byok_credential(
        self,
        *,
        profile_id: Optional[str] = None,
        vendor: str = "openai",
    ) -> Dict[str, Any]:
        """Delete the profile's encrypted BYOK key for `vendor` and revert to Local AI if active."""
        norm_vendor, _adapter = self._get_byok_adapter(vendor)
        pid = self._resolve_profile_id(profile_id)
        removed = False
        with Session(self._get_engine()) as session:
            rec = session.get(ProfileProviderConfigRecord, pid)
            cred_ref = WindowsDPAPIProviderCredentialStore.build_ref(pid, norm_vendor)
            removed = self.credential_store.delete_credential(pid, cred_ref)
            if rec is None:
                return {
                    "profile_config": ProfileProviderConfigRecord(profile_id=pid).to_dict(),
                    "vendor": norm_vendor,
                    "removed": removed,
                }
            rec_extra = self._load_extra_config(rec)
            vendors_cfg = rec_extra.get("byok_vendors")
            if isinstance(vendors_cfg, dict) and norm_vendor in vendors_cfg:
                vendors_cfg.pop(norm_vendor, None)
                rec.config_json = json.dumps(rec_extra)

            if rec.byok_vendor == norm_vendor or rec.byok_credential_ref == cred_ref:
                rec.byok_credential_ref = None
                rec.byok_vendor = None
                rec.byok_model = None
                if rec.active_provider_id == PROVIDER_BYOK:
                    rec.active_provider_id = PROVIDER_LOCAL

            rec.updated_at = datetime.now(timezone.utc)
            session.add(rec)
            session.commit()
            session.refresh(rec)
            return {
                "profile_config": rec.to_dict(),
                "vendor": norm_vendor,
                "removed": removed,
            }

    def list_byok_openai_models(self) -> List[Dict[str, Any]]:
        return [m.to_dict() for m in self.openai_adapter.list_models()]

    def get_active_provider(self, profile_id: Optional[str] = None) -> AIProvider:
        cfg = self.get_profile_config(profile_id)
        active_id = cfg.get("active_provider_id") or PROVIDER_LOCAL
        return self.get_provider(active_id)

    def generate(
        self,
        request: AIProviderRequest,
        *,
        profile_id: Optional[str] = None,
    ) -> AIProviderResponse:
        pid = self._resolve_profile_id(profile_id)
        cfg = self._get_internal_profile_config(pid)
        active_id = cfg.get("active_provider_id") or PROVIDER_LOCAL
        provider = self.get_provider(active_id)
        return provider.generate(request, config=cfg)


_shared_provider_registry: Optional[AIProviderRegistry] = None
_shared_provider_lock = threading.Lock()


def get_shared_provider_registry() -> AIProviderRegistry:
    global _shared_provider_registry
    with _shared_provider_lock:
        if _shared_provider_registry is None:
            _shared_provider_registry = AIProviderRegistry()
        return _shared_provider_registry
