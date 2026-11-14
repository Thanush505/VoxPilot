"""
VoxPilot - Three-Mode AI Provider Foundation & BYOK API Endpoints (Phases 8A–8B)
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.ai.providers.base import (
    AIProviderError,
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderNotConfiguredError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from backend.ai.providers.credentials import sanitize_secret
from backend.ai.providers.registry import get_shared_provider_registry
from backend.api.agent import _verify_trusted_request

api_providers_router = APIRouter(prefix="/api/providers", tags=["providers"])
agent_providers_router = APIRouter(prefix="/agent/providers", tags=["providers"])


class ProviderConfigValidateRequest(BaseModel):
    provider_id: str = Field(..., min_length=1, max_length=64)
    config: Dict[str, Any] = Field(default_factory=dict)


class ProviderSelectRequest(BaseModel):
    provider_id: str = Field(..., min_length=1, max_length=64)
    config: Optional[Dict[str, Any]] = None


class OpenAIBYOKConfigureRequest(BaseModel):
    api_key: str = Field(..., min_length=1, max_length=512)
    model: Optional[str] = Field(default=None, max_length=128)
    validate_connection: bool = True


class OpenAIBYOKValidateRequest(BaseModel):
    api_key: Optional[str] = Field(default=None, max_length=512)
    model: Optional[str] = Field(default=None, max_length=128)


class OpenAIBYOKModelRequest(BaseModel):
    model: str = Field(..., min_length=1, max_length=128)


def _provider_error_response(exc: Exception) -> JSONResponse:
    msg = sanitize_secret(str(exc))
    if isinstance(exc, ProviderAuthenticationError):
        return JSONResponse(status_code=401, content={"detail": msg})
    if isinstance(exc, ProviderConfigurationError):
        return JSONResponse(status_code=422, content={"detail": msg})
    if isinstance(exc, ProviderTimeoutError):
        return JSONResponse(status_code=504, content={"detail": msg})
    if isinstance(exc, (ProviderUnavailableError, ProviderNotConfiguredError)):
        return JSONResponse(status_code=400, content={"detail": msg})
    if isinstance(exc, AIProviderError):
        return JSONResponse(status_code=400, content={"detail": msg})
    return JSONResponse(status_code=500, content={"detail": msg})


def _register_routes(r: APIRouter) -> None:
    @r.get("")
    async def list_providers(request: Request):
        _verify_trusted_request(request)
        reg = get_shared_provider_registry()
        return {
            "providers": reg.list_providers(),
            "byok_models": reg.list_byok_openai_models(),
        }

    @r.get("/active")
    async def get_active_provider(request: Request):
        _verify_trusted_request(request)
        reg = get_shared_provider_registry()
        cfg = reg.get_profile_config()
        internal_cfg = reg._get_internal_profile_config()
        provider = reg.get_active_provider()
        return {
            "profile_config": cfg,
            "health": provider.health_check(internal_cfg).to_dict(),
            "capabilities": provider.capabilities.to_dict(),
        }

    @r.post("/validate")
    async def validate_provider_config(request: Request, req: ProviderConfigValidateRequest):
        _verify_trusted_request(request)
        reg = get_shared_provider_registry()
        try:
            return reg.validate_provider_config(req.provider_id, req.config)
        except Exception as exc:
            return _provider_error_response(exc)

    @r.post("/select")
    async def select_provider(request: Request, req: ProviderSelectRequest):
        _verify_trusted_request(request)
        reg = get_shared_provider_registry()
        try:
            return reg.select_provider(req.provider_id, config=req.config)
        except Exception as exc:
            return _provider_error_response(exc)

    @r.post("/byok/openai/configure")
    async def configure_byok_openai(request: Request, req: OpenAIBYOKConfigureRequest):
        _verify_trusted_request(request)
        reg = get_shared_provider_registry()
        try:
            return reg.configure_byok_openai(
                api_key=req.api_key,
                model=req.model,
                validate=req.validate_connection,
            )
        except Exception as exc:
            return _provider_error_response(exc)

    @r.post("/byok/openai/validate")
    async def validate_byok_openai(request: Request, req: OpenAIBYOKValidateRequest):
        _verify_trusted_request(request)
        reg = get_shared_provider_registry()
        try:
            return reg.validate_byok_connection(
                api_key=req.api_key,
                model=req.model,
            )
        except Exception as exc:
            return _provider_error_response(exc)

    @r.post("/byok/openai/model")
    async def set_byok_openai_model(request: Request, req: OpenAIBYOKModelRequest):
        _verify_trusted_request(request)
        reg = get_shared_provider_registry()
        try:
            return reg.set_byok_openai_model(model=req.model)
        except Exception as exc:
            return _provider_error_response(exc)

    @r.delete("/byok/openai/credentials")
    async def delete_byok_openai_credentials(request: Request):
        _verify_trusted_request(request)
        reg = get_shared_provider_registry()
        try:
            return reg.remove_byok_credential(vendor="openai")
        except Exception as exc:
            return _provider_error_response(exc)


_register_routes(api_providers_router)
_register_routes(agent_providers_router)
