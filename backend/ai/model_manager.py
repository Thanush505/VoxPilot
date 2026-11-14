"""
VoxPilot - Local Ollama Model Lifecycle & Resource Safeguard Manager
Provides safe, resource-aware loading, unloading, discovery, and active-inference
protection for local Ollama models without deleting or downloading models.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Tuple

import httpx
from pydantic import BaseModel, Field

from backend.agent.rate_limiter import get_shared_rate_limiter
from backend.ai.hardware_monitor import (
    DEFAULT_OLLAMA_BASE_URL,
    HardwareMonitor,
    OllamaInstalledModel,
    OllamaRunningModel,
    get_shared_hardware_monitor,
    parse_installed_models_payload,
    parse_running_models_payload,
    validate_loopback_ollama_url,
)
from backend.automation.emergency_stop import get_shared_emergency_stop_manager

logger = logging.getLogger(__name__)

DEFAULT_PRIMARY_MODEL = "qwen3:4b"
MODEL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_KEEP_ALIVE_PATTERN = re.compile(r"^([0-9]+(ns|us|µs|ms|s|m|h)?|-1|0)$")


# ==============================================================================
# ModelManager Exceptions
# ==============================================================================

class ModelManagerError(Exception):
    """Base exception for local model lifecycle errors."""
    pass


class InvalidModelNameError(ModelManagerError):
    """Raised when a model name is empty or fails validation."""
    pass


class ModelNotFoundError(ModelManagerError):
    """Raised when a requested model is not installed locally in Ollama."""
    pass


class OllamaConnectionError(ModelManagerError):
    """Raised when local Ollama cannot be reached or returns an invalid API error."""
    pass


class ModelLoadTimeoutError(ModelManagerError):
    """Raised when loading or unloading a model exceeds the configured timeout."""
    pass


class InsufficientResourcesError(ModelManagerError):
    """Raised when system RAM or GPU VRAM is insufficient to safely load a model."""
    pass


class ActiveInferenceConflictError(ModelManagerError):
    """Raised when attempting to unload a model while active inference is running."""
    pass


class ConcurrentModelOperationError(ModelManagerError):
    """Raised when another model load/unload operation is already in progress."""
    pass


# ==============================================================================
# Configuration & Structured Result Schemas
# ==============================================================================

class ModelResourcePolicy(BaseModel):
    """Conservative resource safeguards for 16GB RAM / 6GB VRAM hosts."""
    min_free_ram_mb: float = Field(default=1536.0, ge=0.0)
    min_free_vram_mb: float = Field(default=512.0, ge=0.0)
    model_overhead_ratio: float = Field(default=1.15, ge=1.0)
    min_overhead_mb: float = Field(default=256.0, ge=0.0)
    default_keep_alive: str = "15m"
    auto_unload_other_models: bool = True


class ResourceCheckResult(BaseModel):
    """Detailed pre-flight resource assessment before loading a model."""
    allowed: bool
    model_name: str
    estimated_required_mb: float
    available_ram_mb: float
    gpu_available: bool
    free_vram_mb: Optional[float] = None
    reclaimable_vram_mb: float = 0.0
    models_to_unload: List[str] = Field(default_factory=list)
    reason: str


class ModelOperationResult(BaseModel):
    """Structured result returned by model load and unload operations."""
    success: bool
    action: str
    model: str
    active_model: Optional[str] = None
    default_model: str = DEFAULT_PRIMARY_MODEL
    already_loaded: Optional[bool] = None
    was_running: Optional[bool] = None
    unloaded_models: List[str] = Field(default_factory=list)
    keep_alive: Optional[str] = None
    resource_check: Optional[ResourceCheckResult] = None
    message: str
    timestamp: str


# ==============================================================================
# ModelManager Service
# ==============================================================================

class ModelManager:
    """Thread-safe local Ollama model lifecycle and resource safeguard manager."""

    def __init__(
        self,
        hardware_monitor: Optional[HardwareMonitor] = None,
        ollama_base_url: Optional[str] = None,
        default_model: str = DEFAULT_PRIMARY_MODEL,
        policy: Optional[ModelResourcePolicy] = None,
        api_timeout_seconds: float = 5.0,
        load_timeout_seconds: float = 60.0,
        http_transport: Optional[httpx.BaseTransport] = None,
    ):
        self.hardware_monitor = hardware_monitor or get_shared_hardware_monitor()
        env_url = ollama_base_url or getattr(self.hardware_monitor, "ollama_base_url", None) or os.environ.get("OLLAMA_HOST") or DEFAULT_OLLAMA_BASE_URL
        if env_url and "://" not in env_url:
            env_url = f"http://{env_url}"
        self.ollama_base_url = validate_loopback_ollama_url(env_url)
        self.default_model = self.validate_model_name(default_model)
        self.policy = policy or ModelResourcePolicy()
        self.api_timeout_seconds = max(0.5, float(api_timeout_seconds))
        self.load_timeout_seconds = max(1.0, float(load_timeout_seconds))
        self._http_transport = http_transport

        self._operation_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._active_model: str = self.default_model
        self._active_inference_counts: Dict[str, int] = {}

    # --------------------------------------------------------------------------
    # Validation & Name Resolution
    # --------------------------------------------------------------------------

    @staticmethod
    def validate_model_name(model_name: str) -> str:
        """Validate model identifier format and reject unsafe path characters."""
        if not isinstance(model_name, str):
            raise InvalidModelNameError("Model name must be a string.")
        cleaned = model_name.strip()
        if not cleaned or ".." in cleaned or "\\" in cleaned:
            raise InvalidModelNameError(f"Invalid Ollama model name: '{model_name}'.")
        if not MODEL_NAME_PATTERN.match(cleaned):
            raise InvalidModelNameError(
                f"Invalid Ollama model name '{model_name}': contains unsupported characters."
            )
        return cleaned

    @staticmethod
    def validate_keep_alive(keep_alive: Optional[str]) -> str:
        """Validate Ollama keep_alive duration format (e.g. '15m', '60s', '0', '-1')."""
        if keep_alive is None:
            return "15m"
        cleaned = str(keep_alive).strip()
        if not _KEEP_ALIVE_PATTERN.match(cleaned):
            raise InvalidModelNameError(
                f"Invalid keep_alive value '{keep_alive}'. Expected duration like '15m', '60s', '0', or '-1'."
            )
        return cleaned

    @staticmethod
    def _match_model_name(target: str, available_names: List[str]) -> Optional[str]:
        """Resolve exact model name or ':latest' shorthand without fuzzy ambiguity."""
        target_lower = target.lower()
        for name in available_names:
            if name.lower() == target_lower:
                return name
        if ":" not in target:
            with_latest = f"{target_lower}:latest"
            for name in available_names:
                if name.lower() == with_latest:
                    return name
        return None

    # --------------------------------------------------------------------------
    # Active Model & Inference Protection Tracking
    # --------------------------------------------------------------------------

    def get_active_model(self) -> str:
        """Return the tracked active model name (defaults to primary qwen3:4b)."""
        with self._state_lock:
            return self._active_model

    def set_active_model(self, model_name: str) -> str:
        """Explicitly update the tracked active model name after validation."""
        validated = self.validate_model_name(model_name)
        with self._state_lock:
            self._active_model = validated
            return self._active_model

    def register_inference_start(self, model_name: Optional[str] = None) -> None:
        """Mark an inference session as active for a model."""
        key = (model_name or self.get_active_model()).lower()
        with self._state_lock:
            self._active_inference_counts[key] = self._active_inference_counts.get(key, 0) + 1

    def register_inference_end(self, model_name: Optional[str] = None) -> None:
        """Decrement active inference count for a model."""
        key = (model_name or self.get_active_model()).lower()
        with self._state_lock:
            current = self._active_inference_counts.get(key, 0)
            if current <= 1:
                self._active_inference_counts.pop(key, None)
            else:
                self._active_inference_counts[key] = current - 1

    @contextlib.contextmanager
    def track_inference(self, model_name: Optional[str] = None) -> Iterator[None]:
        """Context manager protecting a model from unsafe unloading during inference."""
        target = model_name or self.get_active_model()
        self.register_inference_start(target)
        try:
            yield
        finally:
            self.register_inference_end(target)

    def is_inference_active(self, model_name: Optional[str] = None) -> Tuple[bool, Optional[str]]:
        """Check whether direct inference or active agent execution is currently using AI models."""
        with self._state_lock:
            if model_name:
                norm = model_name.lower()
                count = self._active_inference_counts.get(norm, 0)
                if ":" not in norm:
                    count += self._active_inference_counts.get(f"{norm}:latest", 0)
                if count > 0:
                    return True, f"Model '{model_name}' has {count} active inference request(s)."
            else:
                total = sum(self._active_inference_counts.values())
                if total > 0:
                    return True, f"Ollama currently has {total} active inference request(s)."

        # Also check LangGraph active executions via shared RateLimiter
        limiter = get_shared_rate_limiter()
        active_execs = limiter.get_active_execution_count()
        if active_execs > 0:
            active_model = self.get_active_model()
            if (
                model_name is None
                or model_name.lower() == active_model.lower()
                or model_name.lower() == self.default_model.lower()
            ):
                return True, (
                    f"Cannot interrupt active agent inference: {active_execs} execution(s) currently active."
                )

        # Also inspect EmergencyStopManager active operations list for LLM/agent operations
        stop_mgr = get_shared_emergency_stop_manager()
        active_ops = stop_mgr.list_active_operations()
        for op in active_ops:
            category = str(op.get("category", "")).lower()
            if category in ("agent", "llm", "inference", "planner"):
                return True, f"Active operation '{op.get('description', category)}' is using local inference."

        return False, None

    # --------------------------------------------------------------------------
    # HTTP Helpers
    # --------------------------------------------------------------------------

    def _create_client(self, timeout_seconds: float) -> httpx.Client:
        kwargs: Dict[str, Any] = {
            "base_url": self.ollama_base_url,
            "timeout": timeout_seconds,
        }
        if self._http_transport is not None:
            kwargs["transport"] = self._http_transport
        return httpx.Client(**kwargs)

    # --------------------------------------------------------------------------
    # Discovery & Metadata
    # --------------------------------------------------------------------------

    def list_installed_models(self) -> List[OllamaInstalledModel]:
        """Query Ollama /api/tags to discover locally installed models."""
        try:
            with self._create_client(self.api_timeout_seconds) as client:
                resp = client.get("/api/tags")
                resp.raise_for_status()
                return parse_installed_models_payload(resp.json())
        except httpx.TimeoutException as exc:
            raise OllamaConnectionError(
                f"Timed out querying installed models at {self.ollama_base_url}/api/tags."
            ) from exc
        except httpx.RequestError as exc:
            raise OllamaConnectionError(
                f"Could not connect to local Ollama at {self.ollama_base_url}: {exc}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise OllamaConnectionError(
                f"Ollama /api/tags returned HTTP {exc.response.status_code}."
            ) from exc
        except ValueError as exc:
            raise OllamaConnectionError(str(exc)) from exc

    def list_running_models(self) -> List[OllamaRunningModel]:
        """Query Ollama /api/ps to inspect currently loaded models in memory/VRAM."""
        try:
            with self._create_client(self.api_timeout_seconds) as client:
                resp = client.get("/api/ps")
                resp.raise_for_status()
                return parse_running_models_payload(resp.json())
        except httpx.TimeoutException as exc:
            raise OllamaConnectionError(
                f"Timed out querying running models at {self.ollama_base_url}/api/ps."
            ) from exc
        except httpx.RequestError as exc:
            raise OllamaConnectionError(
                f"Could not connect to local Ollama at {self.ollama_base_url}: {exc}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise OllamaConnectionError(
                f"Ollama /api/ps returned HTTP {exc.response.status_code}."
            ) from exc
        except ValueError as exc:
            raise OllamaConnectionError(str(exc)) from exc

    def is_model_running(self, model_name: str) -> bool:
        """Check whether the given model is currently loaded in Ollama."""
        validated = self.validate_model_name(model_name)
        running = self.list_running_models()
        matched = self._match_model_name(validated, [m.name for m in running])
        return matched is not None

    def get_model_metadata(self, model_name: str) -> Dict[str, Any]:
        """Retrieve detailed metadata for an installed model via /api/show with tag fallback."""
        validated = self.validate_model_name(model_name)
        installed = self.list_installed_models()
        matched_name = self._match_model_name(validated, [m.name for m in installed])
        if not matched_name:
            raise ModelNotFoundError(
                f"Model '{validated}' is not installed in local Ollama."
            )

        base_info = next(m for m in installed if m.name == matched_name).model_dump()
        try:
            with self._create_client(self.api_timeout_seconds) as client:
                resp = client.post("/api/show", json={"model": matched_name})
                resp.raise_for_status()
                show_data = resp.json()
                if isinstance(show_data, dict):
                    base_info["details"] = show_data.get("details") or {}
                    base_info["capabilities"] = show_data.get("capabilities") or []
                    base_info["parameters"] = show_data.get("parameters")
                    base_info["license"] = show_data.get("license")
                    if isinstance(show_data.get("model_info"), dict):
                        base_info["model_info_keys"] = list(show_data["model_info"].keys())
        except Exception as exc:
            logger.debug(f"/api/show metadata lookup fell back to /api/tags for '{matched_name}': {exc}")

        return base_info

    # --------------------------------------------------------------------------
    # Resource Assessment
    # --------------------------------------------------------------------------

    def check_resources_for_model(
        self,
        model_name: str,
        model_size_bytes: int,
        running_models: Optional[List[OllamaRunningModel]] = None,
    ) -> ResourceCheckResult:
        """Evaluate system RAM and GPU VRAM headroom before loading a model."""
        ram = self.hardware_monitor.collect_ram_metrics()
        gpu = self.hardware_monitor.collect_gpu_metrics()
        current_running = running_models if running_models is not None else self.list_running_models()

        available_ram_mb = round(ram.available_bytes / (1024 ** 2), 1)
        size_mb = model_size_bytes / (1024 ** 2) if model_size_bytes > 0 else 0.0
        estimated_required_mb = round(
            max(size_mb * self.policy.model_overhead_ratio, size_mb + self.policy.min_overhead_mb),
            1,
        )

        # Always protect OS stability by enforcing minimum system RAM reserve
        if available_ram_mb < self.policy.min_free_ram_mb:
            return ResourceCheckResult(
                allowed=False,
                model_name=model_name,
                estimated_required_mb=estimated_required_mb,
                available_ram_mb=available_ram_mb,
                gpu_available=gpu.available,
                free_vram_mb=gpu.vram_free_mb,
                reason=(
                    f"Insufficient system RAM to load '{model_name}': "
                    f"{available_ram_mb:.0f} MB free is below minimum reserve ({self.policy.min_free_ram_mb:.0f} MB)."
                ),
            )

        # Identify other running Ollama models that could be unloaded to free memory
        other_running = [
            m for m in current_running if m.name.lower() != model_name.lower()
        ]
        reclaimable_vram_mb = round(sum(m.size_vram_mb for m in other_running), 1)
        reclaimable_ram_mb = round(sum(m.size_bytes / (1024 ** 2) for m in other_running), 1)

        if gpu.available and gpu.vram_free_mb is not None:
            needed_vram_with_reserve = estimated_required_mb + self.policy.min_free_vram_mb
            if gpu.vram_free_mb >= needed_vram_with_reserve:
                return ResourceCheckResult(
                    allowed=True,
                    model_name=model_name,
                    estimated_required_mb=estimated_required_mb,
                    available_ram_mb=available_ram_mb,
                    gpu_available=True,
                    free_vram_mb=gpu.vram_free_mb,
                    reclaimable_vram_mb=reclaimable_vram_mb,
                    reason=(
                        f"Sufficient GPU VRAM ({gpu.vram_free_mb:.0f} MB free) and RAM "
                        f"({available_ram_mb:.0f} MB free) for '{model_name}' (~{estimated_required_mb:.0f} MB estimated)."
                    ),
                )

            # Check if unloading other inactive running models clears enough VRAM
            if self.policy.auto_unload_other_models and other_running:
                projected_free_vram = gpu.vram_free_mb + reclaimable_vram_mb
                if projected_free_vram >= needed_vram_with_reserve:
                    return ResourceCheckResult(
                        allowed=True,
                        model_name=model_name,
                        estimated_required_mb=estimated_required_mb,
                        available_ram_mb=available_ram_mb,
                        gpu_available=True,
                        free_vram_mb=gpu.vram_free_mb,
                        reclaimable_vram_mb=reclaimable_vram_mb,
                        models_to_unload=[m.name for m in other_running],
                        reason=(
                            f"Free VRAM ({gpu.vram_free_mb:.0f} MB) requires unloading "
                            f"{[m.name for m in other_running]} (+{reclaimable_vram_mb:.0f} MB reclaimable) "
                            f"before loading '{model_name}' (~{estimated_required_mb:.0f} MB estimated)."
                        ),
                    )

            # Cannot fit cleanly inside GPU VRAM even after reclaiming other models;
            # check if RAM would at least survive CPU offload fallback—Wait: on 6GB RTX 3050,
            # loading oversized models causes severe VRAM/PCIe thrashing, so reject conservatively!
            return ResourceCheckResult(
                allowed=False,
                model_name=model_name,
                estimated_required_mb=estimated_required_mb,
                available_ram_mb=available_ram_mb,
                gpu_available=True,
                free_vram_mb=gpu.vram_free_mb,
                reclaimable_vram_mb=reclaimable_vram_mb,
                reason=(
                    f"Insufficient GPU VRAM for '{model_name}': estimated requirement "
                    f"~{estimated_required_mb:.0f} MB + {self.policy.min_free_vram_mb:.0f} MB reserve "
                    f"exceeds available VRAM ({gpu.vram_free_mb:.0f} MB free, "
                    f"{reclaimable_vram_mb:.0f} MB reclaimable)."
                ),
            )

        # CPU-only host fallback check (nvidia-smi unavailable)
        needed_ram_with_reserve = estimated_required_mb + self.policy.min_free_ram_mb
        if available_ram_mb >= needed_ram_with_reserve:
            return ResourceCheckResult(
                allowed=True,
                model_name=model_name,
                estimated_required_mb=estimated_required_mb,
                available_ram_mb=available_ram_mb,
                gpu_available=False,
                free_vram_mb=None,
                reason=(
                    f"GPU telemetry unavailable; sufficient system RAM ({available_ram_mb:.0f} MB free) "
                    f"for '{model_name}' (~{estimated_required_mb:.0f} MB estimated)."
                ),
            )

        if self.policy.auto_unload_other_models and other_running:
            if (available_ram_mb + reclaimable_ram_mb) >= needed_ram_with_reserve:
                return ResourceCheckResult(
                    allowed=True,
                    model_name=model_name,
                    estimated_required_mb=estimated_required_mb,
                    available_ram_mb=available_ram_mb,
                    gpu_available=False,
                    free_vram_mb=None,
                    models_to_unload=[m.name for m in other_running],
                    reason=(
                        f"Sufficient system RAM after unloading {[m.name for m in other_running]} "
                        f"for '{model_name}'."
                    ),
                )

        return ResourceCheckResult(
            allowed=False,
            model_name=model_name,
            estimated_required_mb=estimated_required_mb,
            available_ram_mb=available_ram_mb,
            gpu_available=False,
            free_vram_mb=None,
            reason=(
                f"Insufficient system RAM for '{model_name}': needs ~{estimated_required_mb:.0f} MB "
                f"+ {self.policy.min_free_ram_mb:.0f} MB reserve, only {available_ram_mb:.0f} MB available."
            ),
        )

    # --------------------------------------------------------------------------
    # Load & Unload Lifecycle
    # --------------------------------------------------------------------------

    def _send_keep_alive(self, model_name: str, keep_alive: Any, timeout: float) -> None:
        """Invoke Ollama /api/generate with empty prompt to load or unload weights."""
        try:
            with self._create_client(timeout) as client:
                resp = client.post(
                    "/api/generate",
                    json={
                        "model": model_name,
                        "prompt": "",
                        "keep_alive": keep_alive,
                        "stream": False,
                    },
                )
                resp.raise_for_status()
        except httpx.TimeoutException as exc:
            raise ModelLoadTimeoutError(
                f"Timed out waiting for Ollama lifecycle request on '{model_name}' after {timeout:.1f}s."
            ) from exc
        except httpx.RequestError as exc:
            raise OllamaConnectionError(
                f"Lost connection to Ollama at {self.ollama_base_url}: {exc}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            detail = ""
            try:
                body = exc.response.json()
                if isinstance(body, dict) and body.get("error"):
                    detail = f": {body['error']}"
            except Exception:
                pass
            raise OllamaConnectionError(
                f"Ollama rejected lifecycle request for '{model_name}' with HTTP {exc.response.status_code}{detail}."
            ) from exc

    def load_model(
        self,
        model_name: str,
        keep_alive: Optional[str] = None,
    ) -> ModelOperationResult:
        """Safely load a locally installed Ollama model after resource and lock checks."""
        validated = self.validate_model_name(model_name)
        keep_alive_val = self.validate_keep_alive(keep_alive or self.policy.default_keep_alive)
        if keep_alive_val == "0":
            raise InvalidModelNameError("Use unload_model() rather than load_model(..., keep_alive='0').")

        if not self._operation_lock.acquire(blocking=False):
            raise ConcurrentModelOperationError(
                "Another model load/unload operation is already in progress. Please wait for it to finish."
            )

        try:
            installed = self.list_installed_models()
            matched_name = self._match_model_name(validated, [m.name for m in installed])
            if not matched_name:
                raise ModelNotFoundError(
                    f"Model '{validated}' is not installed locally in Ollama. Automatic downloads are disabled."
                )

            installed_info = next(m for m in installed if m.name == matched_name)
            running = self.list_running_models()
            if self._match_model_name(matched_name, [m.name for m in running]):
                self.set_active_model(matched_name)
                return ModelOperationResult(
                    success=True,
                    action="load",
                    model=matched_name,
                    active_model=matched_name,
                    default_model=self.default_model,
                    already_loaded=True,
                    keep_alive=keep_alive_val,
                    message=f"Model '{matched_name}' is already loaded in Ollama.",
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )

            resource_check = self.check_resources_for_model(
                matched_name,
                installed_info.size_bytes,
                running_models=running,
            )
            if not resource_check.allowed:
                raise InsufficientResourcesError(resource_check.reason)

            # Unload conflicting inactive models first if required by resource budget
            unloaded_models: List[str] = []
            for other_model in resource_check.models_to_unload:
                busy, busy_reason = self.is_inference_active(other_model)
                if busy:
                    raise ActiveInferenceConflictError(
                        f"Cannot unload '{other_model}' to make room for '{matched_name}': {busy_reason}"
                    )
                self._send_keep_alive(other_model, 0, self.api_timeout_seconds)
                unloaded_models.append(other_model)

            self._send_keep_alive(matched_name, keep_alive_val, self.load_timeout_seconds)
            self.set_active_model(matched_name)

            return ModelOperationResult(
                success=True,
                action="load",
                model=matched_name,
                active_model=matched_name,
                default_model=self.default_model,
                already_loaded=False,
                unloaded_models=unloaded_models,
                keep_alive=keep_alive_val,
                resource_check=resource_check,
                message=f"Model '{matched_name}' loaded successfully (keep_alive={keep_alive_val}).",
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        finally:
            self._operation_lock.release()

    def unload_model(self, model_name: str) -> ModelOperationResult:
        """Safely unload a running Ollama model unless active inference is using it."""
        validated = self.validate_model_name(model_name)

        if not self._operation_lock.acquire(blocking=False):
            raise ConcurrentModelOperationError(
                "Another model load/unload operation is already in progress. Please wait for it to finish."
            )

        try:
            busy, busy_reason = self.is_inference_active(validated)
            if busy:
                raise ActiveInferenceConflictError(
                    busy_reason or f"Model '{validated}' is currently in active inference."
                )

            installed = self.list_installed_models()
            matched_installed = self._match_model_name(validated, [m.name for m in installed])
            if not matched_installed:
                raise ModelNotFoundError(
                    f"Model '{validated}' is not installed locally in Ollama."
                )

            running = self.list_running_models()
            matched_running = self._match_model_name(matched_installed, [m.name for m in running])
            if not matched_running:
                return ModelOperationResult(
                    success=True,
                    action="unload",
                    model=matched_installed,
                    active_model=self.get_active_model(),
                    default_model=self.default_model,
                    was_running=False,
                    keep_alive="0",
                    message=f"Model '{matched_installed}' is already unloaded.",
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )

            # Re-check active inference on canonical resolved name before issuing keep_alive=0
            busy_resolved, busy_resolved_reason = self.is_inference_active(matched_running)
            if busy_resolved:
                raise ActiveInferenceConflictError(
                    busy_resolved_reason or f"Model '{matched_running}' is currently in active inference."
                )

            self._send_keep_alive(matched_running, 0, self.api_timeout_seconds)

            # Keep primary default fallback reference safe without deleting anything
            with self._state_lock:
                remaining = [m.name for m in running if m.name.lower() != matched_running.lower()]
                if self._active_model.lower() == matched_running.lower():
                    self._active_model = remaining[0] if remaining else self.default_model

            return ModelOperationResult(
                success=True,
                action="unload",
                model=matched_running,
                active_model=self.get_active_model(),
                default_model=self.default_model,
                was_running=True,
                keep_alive="0",
                message=f"Model '{matched_running}' unloaded from memory.",
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        finally:
            self._operation_lock.release()


# ==============================================================================
# Shared Singleton Management
# ==============================================================================

_SHARED_MODEL_MANAGER: Optional[ModelManager] = None
_MODEL_MANAGER_LOCK = threading.Lock()


def get_shared_model_manager() -> ModelManager:
    """Retrieve or initialize the shared ModelManager singleton."""
    global _SHARED_MODEL_MANAGER
    with _MODEL_MANAGER_LOCK:
        if _SHARED_MODEL_MANAGER is None:
            _SHARED_MODEL_MANAGER = ModelManager()
        return _SHARED_MODEL_MANAGER


def set_shared_model_manager(manager: ModelManager) -> None:
    """Inject or override the shared ModelManager singleton (for testing)."""
    global _SHARED_MODEL_MANAGER
    with _MODEL_MANAGER_LOCK:
        _SHARED_MODEL_MANAGER = manager


def reset_shared_model_manager() -> ModelManager:
    """Reset the shared ModelManager singleton to a fresh instance."""
    global _SHARED_MODEL_MANAGER
    with _MODEL_MANAGER_LOCK:
        _SHARED_MODEL_MANAGER = ModelManager()
        return _SHARED_MODEL_MANAGER
