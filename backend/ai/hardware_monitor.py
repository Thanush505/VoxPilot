"""
VoxPilot - Hardware & Local AI Runtime Telemetry Monitor
Collects read-only host metrics for CPU, RAM, NVIDIA GPU/VRAM (via nvidia-smi),
and local Ollama model inventory / active runtime status without fabricating values.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx
import psutil
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
_NVIDIA_UNSUPPORTED_TOKENS = {"", "n/a", "[n/a]", "not supported", "[not supported]", "unknown"}
_CPU_TEMP_SENSOR_KEYS = ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz", "package id 0")


# ==============================================================================
# Pydantic Telemetry Schemas
# ==============================================================================

class CpuMetrics(BaseModel):
    utilization_percent: float
    logical_cores: int
    physical_cores: Optional[int] = None
    frequency_mhz: Optional[float] = None
    min_frequency_mhz: Optional[float] = None
    max_frequency_mhz: Optional[float] = None
    temperature_celsius: Optional[float] = None
    temperature_available: bool = False


class RamMetrics(BaseModel):
    total_bytes: int
    used_bytes: int
    available_bytes: int
    total_gb: float
    used_gb: float
    available_gb: float
    utilization_percent: float


class GpuMetrics(BaseModel):
    available: bool
    name: Optional[str] = None
    utilization_percent: Optional[float] = None
    temperature_celsius: Optional[float] = None
    vram_total_mb: Optional[float] = None
    vram_used_mb: Optional[float] = None
    vram_free_mb: Optional[float] = None
    vram_utilization_percent: Optional[float] = None
    status_reason: Optional[str] = None


class OllamaInstalledModel(BaseModel):
    name: str
    model: str
    size_bytes: int = 0
    size_gb: float = 0.0
    digest: Optional[str] = None
    modified_at: Optional[str] = None
    family: Optional[str] = None
    families: List[str] = Field(default_factory=list)
    parameter_size: Optional[str] = None
    quantization_level: Optional[str] = None
    format: Optional[str] = None


class OllamaRunningModel(BaseModel):
    name: str
    model: str
    size_bytes: int = 0
    size_gb: float = 0.0
    size_vram_bytes: int = 0
    size_vram_mb: float = 0.0
    vram_percent: Optional[float] = None
    digest: Optional[str] = None
    expires_at: Optional[str] = None
    parameter_size: Optional[str] = None
    quantization_level: Optional[str] = None
    context_length: Optional[int] = None


class OllamaTelemetry(BaseModel):
    reachable: bool
    base_url: str
    installed_models: List[OllamaInstalledModel] = Field(default_factory=list)
    running_models: List[OllamaRunningModel] = Field(default_factory=list)
    installed_count: int = 0
    running_count: int = 0
    error: Optional[str] = None


class HardwareSnapshot(BaseModel):
    timestamp: str
    os_name: str
    os_version: str
    os_release: str
    machine: str
    cpu: CpuMetrics
    ram: RamMetrics
    gpu: GpuMetrics
    ollama: OllamaTelemetry


# ==============================================================================
# Parsing & Validation Helpers
# ==============================================================================

def validate_loopback_ollama_url(raw_url: str) -> str:
    """Validate that the configured Ollama base URL targets only local loopback."""
    candidate = (raw_url or DEFAULT_OLLAMA_BASE_URL).strip().rstrip("/")
    parsed = urlparse(candidate)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported Ollama URL scheme: '{parsed.scheme}'. Only HTTP/HTTPS on loopback is allowed.")
    host = (parsed.hostname or "").lower()
    if host not in _LOOPBACK_HOSTS:
        raise ValueError(
            f"External Ollama host '{host}' is forbidden. HardwareMonitor permits loopback hosts only."
        )
    return candidate


def _parse_optional_float(raw_value: Any) -> Optional[float]:
    """Parse float defensively, treating unsupported/missing tokens as None."""
    if raw_value is None or isinstance(raw_value, bool):
        return None
    if isinstance(raw_value, (int, float)):
        return float(raw_value)
    text = str(raw_value).strip()
    if text.lower() in _NVIDIA_UNSUPPORTED_TOKENS:
        return None
    try:
        return float(text)
    except (ValueError, TypeError):
        return None


def _parse_non_negative_int(raw_value: Any, default: int = 0) -> int:
    """Parse integer defensively without crashing on unexpected types."""
    if raw_value is None or isinstance(raw_value, bool):
        return default
    try:
        val = int(raw_value)
        return val if val >= 0 else default
    except (ValueError, TypeError):
        return default


def _parse_optional_str(raw_value: Any) -> Optional[str]:
    if raw_value is None:
        return None
    text = str(raw_value).strip()
    return text if text else None


def _resolve_nvidia_smi_path() -> Optional[str]:
    """Locate nvidia-smi binary without shell lookup."""
    found = shutil.which("nvidia-smi")
    if found:
        return found
    if os.name == "nt":
        win_sys32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "nvidia-smi.exe"
        if win_sys32.is_file():
            return str(win_sys32)
    return None


def parse_installed_models_payload(payload: Any) -> List[OllamaInstalledModel]:
    """Parse Ollama /api/tags JSON response defensively."""
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise ValueError("Malformed Ollama /api/tags response: expected object with 'models' list.")

    parsed_models: List[OllamaInstalledModel] = []
    for item in payload["models"]:
        if not isinstance(item, dict):
            continue
        name = _parse_optional_str(item.get("name")) or _parse_optional_str(item.get("model"))
        if not name:
            continue
        model_id = _parse_optional_str(item.get("model")) or name
        size_bytes = _parse_non_negative_int(item.get("size"), 0)
        details = item.get("details") if isinstance(item.get("details"), dict) else {}
        families_raw = details.get("families") if isinstance(details.get("families"), list) else []
        families = [str(f).strip() for f in families_raw if isinstance(f, str) and f.strip()]

        parsed_models.append(
            OllamaInstalledModel(
                name=name,
                model=model_id,
                size_bytes=size_bytes,
                size_gb=round(size_bytes / (1024 ** 3), 2) if size_bytes > 0 else 0.0,
                digest=_parse_optional_str(item.get("digest")),
                modified_at=_parse_optional_str(item.get("modified_at")),
                family=_parse_optional_str(details.get("family")),
                families=families,
                parameter_size=_parse_optional_str(details.get("parameter_size")),
                quantization_level=_parse_optional_str(details.get("quantization_level")),
                format=_parse_optional_str(details.get("format")),
            )
        )
    return parsed_models


def parse_running_models_payload(payload: Any) -> List[OllamaRunningModel]:
    """Parse Ollama /api/ps JSON response defensively."""
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise ValueError("Malformed Ollama /api/ps response: expected object with 'models' list.")

    running_models: List[OllamaRunningModel] = []
    for item in payload["models"]:
        if not isinstance(item, dict):
            continue
        name = _parse_optional_str(item.get("name")) or _parse_optional_str(item.get("model"))
        if not name:
            continue
        model_id = _parse_optional_str(item.get("model")) or name
        size_bytes = _parse_non_negative_int(item.get("size"), 0)
        size_vram_bytes = _parse_non_negative_int(item.get("size_vram"), 0)
        details = item.get("details") if isinstance(item.get("details"), dict) else {}

        vram_pct: Optional[float] = None
        if size_bytes > 0:
            vram_pct = round(min(100.0, (size_vram_bytes / size_bytes) * 100.0), 1)

        ctx_raw = item.get("context_length") if item.get("context_length") is not None else details.get("context_length")
        context_length = _parse_non_negative_int(ctx_raw, 0) or None

        running_models.append(
            OllamaRunningModel(
                name=name,
                model=model_id,
                size_bytes=size_bytes,
                size_gb=round(size_bytes / (1024 ** 3), 2) if size_bytes > 0 else 0.0,
                size_vram_bytes=size_vram_bytes,
                size_vram_mb=round(size_vram_bytes / (1024 ** 2), 1) if size_vram_bytes > 0 else 0.0,
                vram_percent=vram_pct,
                digest=_parse_optional_str(item.get("digest")),
                expires_at=_parse_optional_str(item.get("expires_at")),
                parameter_size=_parse_optional_str(details.get("parameter_size")),
                quantization_level=_parse_optional_str(details.get("quantization_level")),
                context_length=context_length,
            )
        )
    return running_models


# ==============================================================================
# HardwareMonitor Service
# ==============================================================================

class HardwareMonitor:
    """Read-only hardware and local Ollama runtime telemetry collector."""

    def __init__(
        self,
        ollama_base_url: Optional[str] = None,
        cpu_sample_interval: float = 0.05,
        gpu_timeout_seconds: float = 3.0,
        ollama_timeout_seconds: float = 3.0,
        http_transport: Optional[httpx.BaseTransport] = None,
    ):
        env_url = ollama_base_url or os.environ.get("OLLAMA_HOST") or DEFAULT_OLLAMA_BASE_URL
        if env_url and "://" not in env_url:
            env_url = f"http://{env_url}"
        self.ollama_base_url = validate_loopback_ollama_url(env_url)
        self.cpu_sample_interval = max(0.0, float(cpu_sample_interval))
        self.gpu_timeout_seconds = max(0.5, float(gpu_timeout_seconds))
        self.ollama_timeout_seconds = max(0.5, float(ollama_timeout_seconds))
        self._http_transport = http_transport

    def collect_cpu_metrics(self) -> CpuMetrics:
        """Collect host CPU utilization, cores, frequency, and optional sensor temperature."""
        utilization = float(psutil.cpu_percent(interval=self.cpu_sample_interval))
        logical_cores = int(psutil.cpu_count(logical=True) or 1)
        physical_cores_raw = psutil.cpu_count(logical=False)
        physical_cores = int(physical_cores_raw) if physical_cores_raw else None

        freq_mhz: Optional[float] = None
        min_freq_mhz: Optional[float] = None
        max_freq_mhz: Optional[float] = None
        try:
            freq = psutil.cpu_freq()
            if freq is not None:
                if freq.current and freq.current > 0:
                    freq_mhz = round(float(freq.current), 1)
                if freq.min and freq.min > 0:
                    min_freq_mhz = round(float(freq.min), 1)
                if freq.max and freq.max > 0:
                    max_freq_mhz = round(float(freq.max), 1)
        except Exception as exc:
            logger.debug(f"CPU frequency unavailable: {exc}")

        temp_c: Optional[float] = None
        temp_available = False
        if hasattr(psutil, "sensors_temperatures"):
            try:
                sensors = psutil.sensors_temperatures(fahrenheit=False)  # type: ignore[attr-defined]
                if isinstance(sensors, dict) and sensors:
                    temp_c, temp_available = self._extract_cpu_temperature(sensors)
            except Exception as exc:
                logger.debug(f"CPU temperature sensors unavailable: {exc}")

        return CpuMetrics(
            utilization_percent=round(utilization, 1),
            logical_cores=logical_cores,
            physical_cores=physical_cores,
            frequency_mhz=freq_mhz,
            min_frequency_mhz=min_freq_mhz,
            max_frequency_mhz=max_freq_mhz,
            temperature_celsius=temp_c,
            temperature_available=temp_available,
        )

    @staticmethod
    def _extract_cpu_temperature(sensors: Dict[str, List[Any]]) -> Tuple[Optional[float], bool]:
        """Extract a reliable CPU package/core temperature if reported by psutil."""
        for preferred_key in _CPU_TEMP_SENSOR_KEYS:
            for sensor_name, entries in sensors.items():
                if preferred_key in sensor_name.lower() and entries:
                    for entry in entries:
                        current = _parse_optional_float(getattr(entry, "current", None))
                        if current is not None and -20.0 <= current <= 130.0:
                            return round(current, 1), True
        return None, False

    def collect_ram_metrics(self) -> RamMetrics:
        """Collect host physical RAM capacity and usage."""
        mem = psutil.virtual_memory()
        total_bytes = int(mem.total)
        used_bytes = int(mem.used)
        available_bytes = int(mem.available)
        return RamMetrics(
            total_bytes=total_bytes,
            used_bytes=used_bytes,
            available_bytes=available_bytes,
            total_gb=round(total_bytes / (1024 ** 3), 2),
            used_gb=round(used_bytes / (1024 ** 3), 2),
            available_gb=round(available_bytes / (1024 ** 3), 2),
            utilization_percent=round(float(mem.percent), 1),
        )

    def collect_gpu_metrics(self) -> GpuMetrics:
        """Collect read-only NVIDIA GPU and VRAM telemetry via nvidia-smi when available."""
        smi_path = _resolve_nvidia_smi_path()
        if not smi_path:
            return GpuMetrics(
                available=False,
                status_reason="nvidia-smi utility not found on host.",
            )

        cmd = [
            smi_path,
            "--query-gpu=name,utilization.gpu,temperature.gpu,memory.total,memory.used,memory.free",
            "--format=csv,noheader,nounits",
        ]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.gpu_timeout_seconds,
                shell=False,
                check=False,
                creationflags=creationflags,
            )
        except subprocess.TimeoutExpired:
            logger.warning("nvidia-smi timed out while querying GPU metrics.")
            return GpuMetrics(
                available=False,
                status_reason=f"nvidia-smi timed out after {self.gpu_timeout_seconds:.1f}s.",
            )
        except OSError as exc:
            logger.warning(f"Failed to execute nvidia-smi: {exc}")
            return GpuMetrics(
                available=False,
                status_reason=f"Failed to execute nvidia-smi: {exc}",
            )

        if result.returncode != 0:
            err_msg = (result.stderr or result.stdout or "").strip() or f"exit code {result.returncode}"
            logger.debug(f"nvidia-smi returned non-zero status: {err_msg}")
            return GpuMetrics(
                available=False,
                status_reason=f"nvidia-smi error: {err_msg}",
            )

        return self.parse_nvidia_smi_output(result.stdout)

    @staticmethod
    def parse_nvidia_smi_output(raw_stdout: Optional[str]) -> GpuMetrics:
        """Parse CSV output lines from nvidia-smi without fabricating missing fields."""
        if not raw_stdout:
            return GpuMetrics(available=False, status_reason="nvidia-smi returned empty output.")

        first_line = next((line.strip() for line in raw_stdout.splitlines() if line.strip()), "")
        if not first_line:
            return GpuMetrics(available=False, status_reason="nvidia-smi returned no GPU rows.")

        parts = [part.strip() for part in first_line.split(",")]
        if len(parts) < 6 or not parts[0]:
            return GpuMetrics(
                available=False,
                status_reason="Malformed nvidia-smi CSV output.",
            )

        name = parts[0]
        gpu_util = _parse_optional_float(parts[1])
        gpu_temp = _parse_optional_float(parts[2])
        vram_total = _parse_optional_float(parts[3])
        vram_used = _parse_optional_float(parts[4])
        vram_free = _parse_optional_float(parts[5])

        if vram_free is None and vram_total is not None and vram_used is not None:
            vram_free = max(0.0, round(vram_total - vram_used, 1))

        vram_util_pct: Optional[float] = None
        if vram_total is not None and vram_total > 0 and vram_used is not None:
            vram_util_pct = round(min(100.0, max(0.0, (vram_used / vram_total) * 100.0)), 1)

        return GpuMetrics(
            available=True,
            name=name,
            utilization_percent=round(gpu_util, 1) if gpu_util is not None else None,
            temperature_celsius=round(gpu_temp, 1) if gpu_temp is not None else None,
            vram_total_mb=round(vram_total, 1) if vram_total is not None else None,
            vram_used_mb=round(vram_used, 1) if vram_used is not None else None,
            vram_free_mb=round(vram_free, 1) if vram_free is not None else None,
            vram_utilization_percent=vram_util_pct,
            status_reason=None,
        )

    def collect_ollama_telemetry(self) -> OllamaTelemetry:
        """Query local Ollama /api/tags and /api/ps in strictly read-only mode."""
        installed_models: List[OllamaInstalledModel] = []
        running_models: List[OllamaRunningModel] = []

        try:
            client_kwargs: Dict[str, Any] = {
                "base_url": self.ollama_base_url,
                "timeout": self.ollama_timeout_seconds,
            }
            if self._http_transport is not None:
                client_kwargs["transport"] = self._http_transport
            with httpx.Client(**client_kwargs) as client:
                tags_resp = client.get("/api/tags")
                tags_resp.raise_for_status()
                installed_models = parse_installed_models_payload(tags_resp.json())

                ps_resp = client.get("/api/ps")
                ps_resp.raise_for_status()
                running_models = parse_running_models_payload(ps_resp.json())

            return OllamaTelemetry(
                reachable=True,
                base_url=self.ollama_base_url,
                installed_models=installed_models,
                running_models=running_models,
                installed_count=len(installed_models),
                running_count=len(running_models),
                error=None,
            )
        except httpx.TimeoutException:
            return OllamaTelemetry(
                reachable=False,
                base_url=self.ollama_base_url,
                error=f"Ollama connection timed out after {self.ollama_timeout_seconds:.1f}s.",
            )
        except httpx.RequestError as exc:
            return OllamaTelemetry(
                reachable=False,
                base_url=self.ollama_base_url,
                error=f"Ollama is unreachable at {self.ollama_base_url}: {exc}",
            )
        except httpx.HTTPStatusError as exc:
            return OllamaTelemetry(
                reachable=False,
                base_url=self.ollama_base_url,
                error=f"Ollama HTTP error {exc.response.status_code} on {exc.request.url.path}.",
            )
        except ValueError as exc:
            return OllamaTelemetry(
                reachable=False,
                base_url=self.ollama_base_url,
                error=str(exc),
            )
        except Exception as exc:
            logger.error(f"Unexpected error collecting Ollama telemetry: {exc}")
            return OllamaTelemetry(
                reachable=False,
                base_url=self.ollama_base_url,
                error=f"Unexpected Ollama telemetry error: {exc}",
            )

    def get_hardware_snapshot(self, include_ollama: bool = True) -> HardwareSnapshot:
        """Collect a complete point-in-time snapshot of CPU, RAM, GPU, and Ollama state."""
        cpu = self.collect_cpu_metrics()
        ram = self.collect_ram_metrics()
        gpu = self.collect_gpu_metrics()
        ollama = (
            self.collect_ollama_telemetry()
            if include_ollama
            else OllamaTelemetry(reachable=False, base_url=self.ollama_base_url, error="Ollama polling skipped.")
        )
        return HardwareSnapshot(
            timestamp=datetime.now(timezone.utc).isoformat(),
            os_name=platform.system(),
            os_version=platform.version(),
            os_release=platform.release(),
            machine=platform.machine(),
            cpu=cpu,
            ram=ram,
            gpu=gpu,
            ollama=ollama,
        )


# ==============================================================================
# Shared Singleton Management
# ==============================================================================

_SHARED_HARDWARE_MONITOR: Optional[HardwareMonitor] = None
_HARDWARE_MONITOR_LOCK = threading.Lock()


def get_shared_hardware_monitor() -> HardwareMonitor:
    """Retrieve or initialize the shared HardwareMonitor singleton."""
    global _SHARED_HARDWARE_MONITOR
    with _HARDWARE_MONITOR_LOCK:
        if _SHARED_HARDWARE_MONITOR is None:
            _SHARED_HARDWARE_MONITOR = HardwareMonitor()
        return _SHARED_HARDWARE_MONITOR


def set_shared_hardware_monitor(monitor: HardwareMonitor) -> None:
    """Inject or override the shared HardwareMonitor singleton (for testing)."""
    global _SHARED_HARDWARE_MONITOR
    with _HARDWARE_MONITOR_LOCK:
        _SHARED_HARDWARE_MONITOR = monitor


def reset_shared_hardware_monitor() -> HardwareMonitor:
    """Reset the shared HardwareMonitor singleton to a fresh instance."""
    global _SHARED_HARDWARE_MONITOR
    with _HARDWARE_MONITOR_LOCK:
        _SHARED_HARDWARE_MONITOR = HardwareMonitor()
        return _SHARED_HARDWARE_MONITOR
