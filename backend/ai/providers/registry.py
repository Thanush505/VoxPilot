"""
VoxPilot - Profile-Aware AI Provider Registry & Configuration Manager (Phase 8A)
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlmodel import Session

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
from backend.ai.providers.local_ollama import LocalOllamaProvider
from backend.ai.providers.openai_adapter import DEFAULT_OPENAI_MODEL, OpenAIBYOKAdapter
from backend.ai.providers.placeholders import BYOKProvider, VoxPilotCloudProvider
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
        self.register(LocalOllamaProvider())
        self.register(
            BYOKProvider(
                credential_store=self.credential_store,
                openai_adapter=self.openai_adapter,
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
            result.append({
                "provider_id": p.provider_id,
                "display_name": p.display_name,
                "is_active": p.provider_id == active_id,
                "capabilities": p.capabilities.to_dict(),
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
        merged_cfg = {**existing_cfg, **dict(config or {}), "profile_id": pid}

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
            if "byok_model" in clean_cfg and clean_cfg["byok_model"]:
                rec.byok_model = clean_cfg["byok_model"]
            rec.updated_at = datetime.now(timezone.utc)
            session.add(rec)
            session.commit()
            session.refresh(rec)
            return {
                "profile_config": rec.to_dict(),
                "health": health.to_dict(),
            }

    def configure_byok_openai(
        self,
        *,
        profile_id: Optional[str] = None,
        api_key: str,
        model: Optional[str] = None,
        validate: bool = True,
    ) -> Dict[str, Any]:
        """Encrypt & store an OpenAI BYOK key for a profile without auto-activating BYOK."""
        cleaned_key = (api_key or "").strip()
        if not cleaned_key:
            raise ProviderConfigurationError("OpenAI API key cannot be empty.")

        pid = self._resolve_profile_id(profile_id)
        existing_cfg = self.get_profile_config(pid)
        target_model = (
            (model or "").strip()
            or (existing_cfg.get("byok_model") or "").strip()
            or DEFAULT_OPENAI_MODEL
        )

        if validate:
            health = self.openai_adapter.validate_credential(cleaned_key, model=target_model)
        else:
            health = None

        cred_ref = self.credential_store.store_credential(pid, "openai", cleaned_key)

        with Session(self._get_engine()) as session:
            rec = session.get(ProfileProviderConfigRecord, pid)
            if rec is None:
                rec = ProfileProviderConfigRecord(profile_id=pid, active_provider_id=PROVIDER_LOCAL)
            rec.byok_vendor = "openai"
            rec.byok_model = target_model
            rec.byok_credential_ref = cred_ref
            rec.updated_at = datetime.now(timezone.utc)
            session.add(rec)
            session.commit()
            session.refresh(rec)
            cfg_dict = rec.to_dict()

        if health is None:
            health = self.get_provider(PROVIDER_BYOK).health_check(
                self._get_internal_profile_config(pid)
            )

        return {
            "profile_config": cfg_dict,
            "health": health.to_dict(),
            "validated": bool(validate),
        }

    def validate_byok_connection(
        self,
        *,
        profile_id: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Explicitly validate either a supplied OpenAI key or the profile's stored DPAPI key."""
        pid = self._resolve_profile_id(profile_id)
        cfg = self._get_internal_profile_config(pid)
        target_model = (
            (model or "").strip()
            or (cfg.get("byok_model") or "").strip()
            or DEFAULT_OPENAI_MODEL
        )

        secret = (api_key or "").strip()
        if not secret:
            cred_ref = cfg.get("byok_credential_ref")
            if not cred_ref:
                raise ProviderNotConfiguredError("No OpenAI API key is stored for this profile.")
            secret = self.credential_store.resolve_credential(pid, cred_ref) or ""
            if not secret:
                raise ProviderNotConfiguredError("Stored OpenAI API key could not be found.")

        health = self.openai_adapter.validate_credential(secret, model=target_model)
        return {
            "provider_id": PROVIDER_BYOK,
            "vendor": "openai",
            "valid": True,
            "health": health.to_dict(),
        }

    def set_byok_openai_model(
        self,
        *,
        profile_id: Optional[str] = None,
        model: str,
    ) -> Dict[str, Any]:
        """Update the selected OpenAI BYOK model for a profile without changing active provider."""
        target_model = (model or "").strip()
        if not target_model:
            raise ProviderConfigurationError("OpenAI model name cannot be empty.")

        pid = self._resolve_profile_id(profile_id)
        with Session(self._get_engine()) as session:
            rec = session.get(ProfileProviderConfigRecord, pid)
            if rec is None:
                rec = ProfileProviderConfigRecord(profile_id=pid, active_provider_id=PROVIDER_LOCAL)
            rec.byok_vendor = rec.byok_vendor or "openai"
            rec.byok_model = target_model
            rec.updated_at = datetime.now(timezone.utc)
            session.add(rec)
            session.commit()
            session.refresh(rec)
            return {"profile_config": rec.to_dict()}

    def remove_byok_credential(
        self,
        *,
        profile_id: Optional[str] = None,
        vendor: str = "openai",
    ) -> Dict[str, Any]:
        """Delete the profile's encrypted BYOK key and revert to Local AI if BYOK was active."""
        pid = self._resolve_profile_id(profile_id)
        removed = False
        with Session(self._get_engine()) as session:
            rec = session.get(ProfileProviderConfigRecord, pid)
            if rec is None:
                return {
                    "profile_config": ProfileProviderConfigRecord(profile_id=pid).to_dict(),
                    "removed": False,
                }
            cred_ref = rec.byok_credential_ref or WindowsDPAPIProviderCredentialStore.build_ref(pid, vendor)
            removed = self.credential_store.delete_credential(pid, cred_ref)
            rec.byok_credential_ref = None
            rec.byok_vendor = None
            if rec.active_provider_id == PROVIDER_BYOK:
                rec.active_provider_id = PROVIDER_LOCAL
            rec.updated_at = datetime.now(timezone.utc)
            session.add(rec)
            session.commit()
            session.refresh(rec)
            return {
                "profile_config": rec.to_dict(),
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
