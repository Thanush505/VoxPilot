"""
VoxPilot - Screen Awareness & Local AI Vision Foundation
Provides explicit, privacy-preserving on-demand primary-display capture,
ephemeral profile-isolated storage with guaranteed temp cleanup, and an
honest local Ollama vision provider interface.
"""

from __future__ import annotations

import base64
import io
import logging
import os
import tempfile
import threading
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx
import ollama
from PIL import Image, ImageGrab
from pydantic import BaseModel, Field

from backend.ai.hardware_monitor import (
    DEFAULT_OLLAMA_BASE_URL,
    HardwareMonitor,
    get_shared_hardware_monitor,
    validate_loopback_ollama_url,
)
from backend.ai.model_manager import get_shared_model_manager
from backend.automation.emergency_stop import (
    EmergencyStopActiveError,
    get_shared_emergency_stop_manager,
)
from backend.database.models import scrub_text_secrets

logger = logging.getLogger(__name__)

# Maximum dimension for captured screen images sent to local vision models
DEFAULT_MAX_SCREEN_DIMENSION = 1600
DEFAULT_JPEG_QUALITY = 85
DEFAULT_CAPTURE_TTL_SECONDS = 900  # 15 minutes max ephemeral lifetime

# Known lightweight local vision models compatible with Ollama
RECOMMENDED_VISION_MODELS: List[Dict[str, Any]] = [
    {
        "name": "moondream",
        "display_name": "Moondream 2 (1.8B)",
        "storage_gb": 1.7,
        "vram_gb": 2.2,
        "ram_gb": 3.0,
        "notes": "Fastest lightweight vision model for UI inspection and screen Q&A.",
    },
    {
        "name": "llava-phi3",
        "display_name": "LLaVA-Phi3 (3.8B)",
        "storage_gb": 2.9,
        "vram_gb": 3.5,
        "ram_gb": 4.5,
        "notes": "Balanced vision-language model for error dialogs and application help.",
    },
    {
        "name": "qwen2.5vl:3b",
        "display_name": "Qwen2.5-VL 3B",
        "storage_gb": 3.2,
        "vram_gb": 3.8,
        "ram_gb": 5.0,
        "notes": "Strong UI text and error reading; fits 6GB VRAM when qwen3:4b is unloaded.",
    },
]

_KNOWN_VISION_NAME_TOKENS = (
    "vision",
    "vl",
    "llava",
    "moondream",
    "minicpm-v",
    "bakllava",
    "granite3.2-vision",
    "llama3.2-vision",
)


class ScreenCaptureError(Exception):
    """Base exception for on-demand screen awareness capture."""


class ScreenCaptureAuthorizationError(ScreenCaptureError):
    """Raised when capture is requested without explicit user authorization."""


class ScreenCaptureNotFoundError(ScreenCaptureError):
    """Raised when an ephemeral capture ID does not exist or has expired."""


class ScreenCaptureAccessDeniedError(ScreenCaptureError):
    """Raised when a profile attempts to access another profile's capture."""


class VisionModelRecommendation(BaseModel):
    name: str
    display_name: str
    storage_gb: float
    vram_gb: float
    ram_gb: float
    fits_hardware: bool
    hardware_note: str
    pull_command: str
    notes: str


class VisionAvailabilityStatus(BaseModel):
    vision_available: bool
    active_vision_model: Optional[str] = None
    installed_vision_models: List[str] = Field(default_factory=list)
    default_text_model: str = "qwen3:4b"
    default_text_model_supports_vision: bool = False
    ollama_reachable: bool = False
    vram_total_mb: Optional[float] = None
    vram_free_mb: Optional[float] = None
    ram_free_gb: Optional[float] = None
    message: str
    recommendations: List[VisionModelRecommendation] = Field(default_factory=list)


class VisionAnalysisResult(BaseModel):
    available: bool
    status: str  # "completed", "vision_model_unavailable", "error"
    model_used: Optional[str] = None
    answer: str
    error: Optional[str] = None
    recommendations: List[VisionModelRecommendation] = Field(default_factory=list)


@dataclass
class EphemeralScreenCapture:
    """In-memory + optional temp-file container for an explicit screen capture."""

    capture_id: str
    profile_id: str
    width: int
    height: int
    mime_type: str
    size_bytes: int
    created_at: float
    created_at_iso: str
    image_bytes: bytes = field(repr=False)
    image_base64: str = field(repr=False)
    temp_file_path: Optional[Path] = None
    monitor_note: str = "Primary display"

    def to_public_dict(self, include_preview: bool = True) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "capture_id": self.capture_id,
            "profile_id": self.profile_id,
            "width": self.width,
            "height": self.height,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "created_at": self.created_at_iso,
            "monitor_note": self.monitor_note,
            "ephemeral": True,
        }
        if include_preview:
            payload["preview_data_url"] = f"data:{self.mime_type};base64,{self.image_base64}"
        return payload

    def scrub_and_cleanup(self) -> None:
        """Delete any temporary file on disk and clear image buffers from memory."""
        if self.temp_file_path is not None:
            try:
                if self.temp_file_path.exists():
                    self.temp_file_path.unlink()
            except Exception as err:
                logger.warning(f"Failed to remove temporary screenshot file: {err}")
            finally:
                self.temp_file_path = None
        self.image_bytes = b""
        self.image_base64 = ""


def _default_primary_grabber() -> Image.Image:
    """Capture the primary display only, handling multi-monitor setups safely."""
    try:
        # ImageGrab.grab(all_screens=False) captures the primary monitor on Windows.
        return ImageGrab.grab(all_screens=False)
    except TypeError:
        return ImageGrab.grab()


class ScreenCaptureService:
    """On-demand, privacy-first, profile-isolated screen capture manager."""

    def __init__(
        self,
        grabber: Optional[Callable[[], Image.Image]] = None,
        temp_dir: Optional[Path] = None,
        max_dimension: int = DEFAULT_MAX_SCREEN_DIMENSION,
        jpeg_quality: int = DEFAULT_JPEG_QUALITY,
        ttl_seconds: int = DEFAULT_CAPTURE_TTL_SECONDS,
        persist_temp_file: bool = False,
    ) -> None:
        self._grabber = grabber or _default_primary_grabber
        self._temp_dir = Path(temp_dir) if temp_dir else (Path(tempfile.gettempdir()) / "voxpilot_screen_tmp")
        self._max_dimension = max(320, int(max_dimension))
        self._jpeg_quality = min(95, max(50, int(jpeg_quality)))
        self._ttl_seconds = max(30, int(ttl_seconds))
        self._persist_temp_file = bool(persist_temp_file)
        self._captures: Dict[str, EphemeralScreenCapture] = {}
        self._lock = threading.Lock()

    def _purge_expired_locked(self, now: Optional[float] = None) -> int:
        ts = now if now is not None else time.time()
        expired_ids = [
            cid
            for cid, item in self._captures.items()
            if (ts - item.created_at) > self._ttl_seconds
        ]
        for cid in expired_ids:
            entry = self._captures.pop(cid, None)
            if entry is not None:
                entry.scrub_and_cleanup()
        return len(expired_ids)

    def _encode_image(self, image: Image.Image) -> tuple[bytes, int, int, str]:
        """Resize oversized images and encode compactly for local analysis."""
        working = image.convert("RGB")
        width, height = working.size
        if width <= 0 or height <= 0:
            raise ScreenCaptureError("Captured screen image has invalid dimensions.")

        max_side = max(width, height)
        if max_side > self._max_dimension:
            scale = self._max_dimension / float(max_side)
            new_size = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
            working = working.resize(new_size, Image.Resampling.LANCZOS)
            width, height = working.size

        buf = io.BytesIO()
        working.save(buf, format="JPEG", quality=self._jpeg_quality, optimize=True)
        raw_bytes = buf.getvalue()
        return raw_bytes, width, height, "image/jpeg"

    def capture_primary_display(
        self,
        *,
        profile_id: str,
        explicit_user_action: bool,
        save_temp_file: Optional[bool] = None,
    ) -> EphemeralScreenCapture:
        """Capture the primary display ONLY after explicit user authorization."""
        if not explicit_user_action:
            raise ScreenCaptureAuthorizationError(
                "Screen capture requires explicit user confirmation."
            )

        manager = get_shared_emergency_stop_manager()
        if manager.is_emergency_stop_active():
            raise EmergencyStopActiveError(
                f"Screen capture blocked: emergency stop is active ({manager.trigger_reason})."
            )

        clean_profile = (profile_id or "default").strip() or "default"

        try:
            raw_image = self._grabber()
            if raw_image is None:
                raise ScreenCaptureError("Screen grabber returned no image.")
            img_bytes, width, height, mime_type = self._encode_image(raw_image)
        except (EmergencyStopActiveError, ScreenCaptureError):
            raise
        except Exception as err:
            raise ScreenCaptureError(f"Failed to capture primary display: {err}") from err

        capture_id = f"scr_{uuid.uuid4().hex[:16]}"
        now = time.time()
        created_iso = datetime.now(timezone.utc).isoformat()
        img_b64 = base64.b64encode(img_bytes).decode("ascii")

        temp_path: Optional[Path] = None
        should_write_temp = self._persist_temp_file if save_temp_file is None else bool(save_temp_file)
        if should_write_temp:
            self._temp_dir.mkdir(parents=True, exist_ok=True)
            temp_path = self._temp_dir / f"{capture_id}.jpg"
            temp_path.write_bytes(img_bytes)

        entry = EphemeralScreenCapture(
            capture_id=capture_id,
            profile_id=clean_profile,
            width=width,
            height=height,
            mime_type=mime_type,
            size_bytes=len(img_bytes),
            created_at=now,
            created_at_iso=created_iso,
            image_bytes=img_bytes,
            image_base64=img_b64,
            temp_file_path=temp_path,
        )

        with self._lock:
            self._purge_expired_locked(now=now)
            self._captures[capture_id] = entry

        logger.info(
            "On-demand primary screen captured (%sx%s, %s bytes, profile=%s).",
            width,
            height,
            len(img_bytes),
            clean_profile,
        )
        return entry

    def get_capture(self, capture_id: str, profile_id: str) -> EphemeralScreenCapture:
        """Retrieve a capture strictly verifying profile ownership."""
        clean_profile = (profile_id or "default").strip() or "default"
        with self._lock:
            self._purge_expired_locked()
            entry = self._captures.get(capture_id)
            if entry is None:
                raise ScreenCaptureNotFoundError(
                    f"Screen capture '{capture_id}' not found or expired."
                )
            if entry.profile_id != clean_profile:
                raise ScreenCaptureAccessDeniedError(
                    "Screen capture belongs to a different profile."
                )
            return entry

    def delete_capture(self, capture_id: str, profile_id: str) -> bool:
        """Remove a capture and clean up any temporary file."""
        clean_profile = (profile_id or "default").strip() or "default"
        with self._lock:
            entry = self._captures.get(capture_id)
            if entry is None:
                return False
            if entry.profile_id != clean_profile:
                raise ScreenCaptureAccessDeniedError(
                    "Screen capture belongs to a different profile."
                )
            self._captures.pop(capture_id, None)
            entry.scrub_and_cleanup()
            return True

    def clear_profile_captures(self, profile_id: str) -> int:
        """Clear all ephemeral captures owned by a profile."""
        clean_profile = (profile_id or "default").strip() or "default"
        removed = 0
        with self._lock:
            to_delete = [
                cid for cid, entry in self._captures.items() if entry.profile_id == clean_profile
            ]
            for cid in to_delete:
                entry = self._captures.pop(cid, None)
                if entry is not None:
                    entry.scrub_and_cleanup()
                    removed += 1
        return removed

    def clear_all_captures(self) -> int:
        """Clear all ephemeral captures (used on shutdown or tests)."""
        with self._lock:
            count = len(self._captures)
            for entry in self._captures.values():
                entry.scrub_and_cleanup()
            self._captures.clear()
            return count


class VisionProvider(ABC):
    """Abstract local vision provider interface."""

    @abstractmethod
    def check_availability(self) -> VisionAvailabilityStatus:
        """Inspect local runtime and hardware for compatible vision models."""

    @abstractmethod
    def analyze_image(
        self,
        image_base64: str,
        user_prompt: str,
        system_prompt: Optional[str] = None,
    ) -> VisionAnalysisResult:
        """Analyze a base64-encoded screenshot locally."""


class OllamaVisionProvider(VisionProvider):
    """Local-only Ollama vision provider with honest capability detection."""

    def __init__(
        self,
        base_url: str = DEFAULT_OLLAMA_BASE_URL,
        hardware_monitor: Optional[HardwareMonitor] = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        self.base_url = validate_loopback_ollama_url(base_url)
        self.hardware_monitor = hardware_monitor or get_shared_hardware_monitor()
        self.timeout_seconds = float(timeout_seconds)

    def _build_recommendations(
        self,
        vram_total_mb: Optional[float],
        ram_free_gb: Optional[float],
    ) -> List[VisionModelRecommendation]:
        vram_total_gb = (vram_total_mb / 1024.0) if vram_total_mb is not None else None
        recs: List[VisionModelRecommendation] = []
        for item in RECOMMENDED_VISION_MODELS:
            fits = True
            notes: List[str] = []
            if vram_total_gb is not None:
                if item["vram_gb"] > vram_total_gb - 0.5:
                    fits = False
                    notes.append(
                        f"Needs ~{item['vram_gb']:.1f} GB VRAM (GPU has {vram_total_gb:.1f} GB)."
                    )
                else:
                    notes.append(
                        f"Fits {vram_total_gb:.1f} GB VRAM (~{item['vram_gb']:.1f} GB needed)."
                    )
            if ram_free_gb is not None and item["ram_gb"] > ram_free_gb:
                notes.append(
                    f"Free RAM is {ram_free_gb:.1f} GB; unload unused apps/models before loading."
                )
            if not notes:
                notes.append(
                    f"Requires ~{item['storage_gb']:.1f} GB disk and ~{item['vram_gb']:.1f} GB VRAM."
                )

            recs.append(
                VisionModelRecommendation(
                    name=item["name"],
                    display_name=item["display_name"],
                    storage_gb=item["storage_gb"],
                    vram_gb=item["vram_gb"],
                    ram_gb=item["ram_gb"],
                    fits_hardware=fits,
                    hardware_note=" ".join(notes),
                    pull_command=f"ollama pull {item['name']}",
                    notes=item["notes"],
                )
            )
        return recs

    def _inspect_model_vision_support(self, client: httpx.Client, model_name: str) -> bool:
        """Query Ollama /api/show to check whether a model advertises vision capability."""
        try:
            resp = client.post(f"{self.base_url}/api/show", json={"name": model_name})
            if resp.status_code == 200:
                payload = resp.json()
                capabilities = payload.get("capabilities")
                if isinstance(capabilities, list):
                    lowered = [str(c).lower() for c in capabilities]
                    if "vision" in lowered:
                        return True
                    # Explicit capabilities list without 'vision' means text-only
                    return False
                model_info = payload.get("model_info") or {}
                if any("vision" in str(k).lower() for k in model_info.keys()):
                    return True
        except Exception:
            pass

        lowered_name = model_name.lower()
        return any(tok in lowered_name for tok in _KNOWN_VISION_NAME_TOKENS)

    def check_availability(self) -> VisionAvailabilityStatus:
        hw = self.hardware_monitor.get_hardware_snapshot(include_ollama=False)
        vram_total_mb = hw.gpu.vram_total_mb if hw.gpu and hw.gpu.available else None
        vram_free_mb = hw.gpu.vram_free_mb if hw.gpu and hw.gpu.available else None
        ram_free_gb = hw.ram.available_gb if hw.ram else None
        recommendations = self._build_recommendations(vram_total_mb, ram_free_gb)

        ollama_reachable = False
        installed_vision: List[str] = []
        qwen3_supports_vision = False

        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                tags_resp = client.get(f"{self.base_url}/api/tags")
                if tags_resp.status_code == 200:
                    ollama_reachable = True
                    models = (tags_resp.json() or {}).get("models") or []
                    for m in models:
                        name = str(m.get("name") or "").strip()
                        if not name:
                            continue
                        supports = self._inspect_model_vision_support(client, name)
                        if name.lower().startswith("qwen3:4b"):
                            qwen3_supports_vision = supports
                        if supports:
                            installed_vision.append(name)
        except Exception as err:
            logger.debug(f"Ollama vision capability check failed: {err}")

        if installed_vision:
            active_model = installed_vision[0]
            return VisionAvailabilityStatus(
                vision_available=True,
                active_vision_model=active_model,
                installed_vision_models=installed_vision,
                default_text_model="qwen3:4b",
                default_text_model_supports_vision=qwen3_supports_vision,
                ollama_reachable=ollama_reachable,
                vram_total_mb=vram_total_mb,
                vram_free_mb=vram_free_mb,
                ram_free_gb=ram_free_gb,
                message=f"Local vision model '{active_model}' is available for on-demand screen analysis.",
                recommendations=recommendations,
            )

        if not ollama_reachable:
            msg = (
                "Local Ollama service is unreachable, and qwen3:4b is a text-only model. "
                "Start Ollama and pull a lightweight local vision model such as 'moondream' (~1.7 GB) to enable screen analysis."
            )
        else:
            msg = (
                "No vision-capable local model is currently installed in Ollama. "
                "Your active model 'qwen3:4b' is text-only and does not accept image input. "
                "To enable local screen understanding without cloud APIs, pull a lightweight vision model such as 'moondream' (~1.7 GB disk, ~2.2 GB VRAM) or 'qwen2.5vl:3b' (~3.2 GB disk, ~3.8 GB VRAM)."
            )

        return VisionAvailabilityStatus(
            vision_available=False,
            active_vision_model=None,
            installed_vision_models=[],
            default_text_model="qwen3:4b",
            default_text_model_supports_vision=qwen3_supports_vision,
            ollama_reachable=ollama_reachable,
            vram_total_mb=vram_total_mb,
            vram_free_mb=vram_free_mb,
            ram_free_gb=ram_free_gb,
            message=msg,
            recommendations=recommendations,
        )

    def analyze_image(
        self,
        image_base64: str,
        user_prompt: str,
        system_prompt: Optional[str] = None,
    ) -> VisionAnalysisResult:
        try:
            from backend.ai.providers.base import PROVIDER_LOCAL
            from backend.ai.providers.runtime import resolve_runtime_provider

            active_id, active_provider, _cfg = resolve_runtime_provider()
            if active_id != PROVIDER_LOCAL and not active_provider.capabilities.vision:
                return VisionAnalysisResult(
                    available=False,
                    status="unsupported_capability",
                    model_used=None,
                    answer=(
                        f"Active AI provider '{active_id}' does not support vision or screen image analysis. "
                        "Switch to Local AI with a vision-capable model (such as 'moondream') to analyze screen captures."
                    ),
                    recommendations=[],
                )
        except Exception:
            pass

        availability = self.check_availability()
        if not availability.vision_available or not availability.active_vision_model:
            return VisionAnalysisResult(
                available=False,
                status="vision_model_unavailable",
                model_used=None,
                answer=availability.message,
                recommendations=availability.recommendations,
            )

        vision_model = availability.active_vision_model
        sys_msg = system_prompt or (
            "You are VoxPilot analyzing a single user-authorized screenshot of their primary computer display. "
            "Answer the user's question accurately in plain, concise text based only on what is visible. "
            "Do not invent UI elements, errors, or file paths that are not visible."
        )
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": sys_msg},
            {
                "role": "user",
                "content": (user_prompt or "Describe what is visible on my screen.").strip(),
                "images": [image_base64],
            },
        ]

        try:
            model_manager = get_shared_model_manager()
            with model_manager.track_inference(vision_model):
                response = ollama.chat(
                    model=vision_model,
                    messages=messages,
                )

            raw_answer = ""
            if hasattr(response, "message") and getattr(response.message, "content", None):
                raw_answer = str(response.message.content)
            elif isinstance(response, dict):
                raw_answer = str((response.get("message") or {}).get("content") or "")

            cleaned = scrub_text_secrets(raw_answer).strip()
            if not cleaned:
                cleaned = "The local vision model returned an empty description for the captured screen."

            return VisionAnalysisResult(
                available=True,
                status="completed",
                model_used=vision_model,
                answer=cleaned,
                recommendations=availability.recommendations,
            )
        except Exception as err:
            logger.error(f"Local vision analysis failed for model '{vision_model}': {err}")
            return VisionAnalysisResult(
                available=True,
                status="error",
                model_used=vision_model,
                answer=f"Local vision analysis failed with model '{vision_model}': {err}",
                error=str(err),
                recommendations=availability.recommendations,
            )


_SHARED_SCREEN_CAPTURE_SERVICE: Optional[ScreenCaptureService] = None
_SHARED_VISION_PROVIDER: Optional[VisionProvider] = None


def get_shared_screen_capture_service() -> ScreenCaptureService:
    global _SHARED_SCREEN_CAPTURE_SERVICE
    if _SHARED_SCREEN_CAPTURE_SERVICE is None:
        _SHARED_SCREEN_CAPTURE_SERVICE = ScreenCaptureService()
    return _SHARED_SCREEN_CAPTURE_SERVICE


def get_shared_vision_provider() -> VisionProvider:
    global _SHARED_VISION_PROVIDER
    if _SHARED_VISION_PROVIDER is None:
        _SHARED_VISION_PROVIDER = OllamaVisionProvider()
    return _SHARED_VISION_PROVIDER


def set_shared_vision_provider(provider: Optional[VisionProvider]) -> None:
    """Allow test harnesses to inject a custom VisionProvider."""
    global _SHARED_VISION_PROVIDER
    _SHARED_VISION_PROVIDER = provider
