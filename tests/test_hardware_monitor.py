"""
Unit tests for VoxPilot HardwareMonitor service (Phase 1A).
Covers CPU, RAM, NVIDIA GPU/VRAM (available, unavailable, malformed, timeout),
and local Ollama telemetry (/api/tags, /api/ps, unreachable, malformed).
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from backend.ai.hardware_monitor import (
    HardwareMonitor,
    validate_loopback_ollama_url,
)


def _make_mock_transport(handler):
    return httpx.MockTransport(handler)


class TestLoopbackValidation:
    def test_accepts_loopback_urls(self):
        assert validate_loopback_ollama_url("http://127.0.0.1:11434") == "http://127.0.0.1:11434"
        assert validate_loopback_ollama_url("http://localhost:11434/") == "http://localhost:11434"
        assert validate_loopback_ollama_url("http://[::1]:11434") == "http://[::1]:11434"

    def test_rejects_external_hosts_and_bad_schemes(self):
        with pytest.raises(ValueError, match="forbidden"):
            validate_loopback_ollama_url("https://api.openai.com")
        with pytest.raises(ValueError, match="forbidden"):
            validate_loopback_ollama_url("http://192.168.1.50:11434")
        with pytest.raises(ValueError, match="Unsupported Ollama URL scheme"):
            validate_loopback_ollama_url("ftp://127.0.0.1:11434")


class TestCpuAndRamCollection:
    def test_cpu_and_ram_metrics_success(self):
        fake_vmem = SimpleNamespace(
            total=16 * (1024 ** 3),
            used=8 * (1024 ** 3),
            available=8 * (1024 ** 3),
            percent=50.0,
        )
        fake_freq = SimpleNamespace(current=2600.0, min=400.0, max=4800.0)
        fake_temps = {"coretemp": [SimpleNamespace(label="Package id 0", current=58.4)]}

        monitor = HardwareMonitor(cpu_sample_interval=0.0)

        with (
            patch("backend.ai.hardware_monitor.psutil.cpu_percent", return_value=23.4),
            patch("backend.ai.hardware_monitor.psutil.cpu_count", side_effect=[12, 8]),
            patch("backend.ai.hardware_monitor.psutil.cpu_freq", return_value=fake_freq),
            patch("backend.ai.hardware_monitor.psutil.virtual_memory", return_value=fake_vmem),
            patch("backend.ai.hardware_monitor.psutil.sensors_temperatures", return_value=fake_temps, create=True),
        ):
            cpu = monitor.collect_cpu_metrics()
            ram = monitor.collect_ram_metrics()

        assert cpu.utilization_percent == 23.4
        assert cpu.logical_cores == 12
        assert cpu.physical_cores == 8
        assert cpu.frequency_mhz == 2600.0
        assert cpu.min_frequency_mhz == 400.0
        assert cpu.max_frequency_mhz == 4800.0
        assert cpu.temperature_available is True
        assert cpu.temperature_celsius == 58.4

        assert ram.total_gb == 16.0
        assert ram.used_gb == 8.0
        assert ram.available_gb == 8.0
        assert ram.utilization_percent == 50.0

    def test_cpu_temperature_unavailable_returns_none_never_fabricated(self):
        fake_freq = SimpleNamespace(current=2400.0, min=0.0, max=0.0)
        monitor = HardwareMonitor(cpu_sample_interval=0.0)

        with (
            patch("backend.ai.hardware_monitor.psutil.cpu_percent", return_value=12.0),
            patch("backend.ai.hardware_monitor.psutil.cpu_count", side_effect=[12, 8]),
            patch("backend.ai.hardware_monitor.psutil.cpu_freq", return_value=fake_freq),
            patch("backend.ai.hardware_monitor.hasattr", side_effect=lambda obj, name: False if name == "sensors_temperatures" else hasattr(obj, name)),
        ):
            cpu = monitor.collect_cpu_metrics()

        assert cpu.temperature_available is False
        assert cpu.temperature_celsius is None


class TestNvidiaGpuCollection:
    def test_gpu_available_parses_float_and_vram_usage(self):
        monitor = HardwareMonitor(cpu_sample_interval=0.0)
        completed = subprocess.CompletedProcess(
            args=["nvidia-smi"],
            returncode=0,
            stdout="NVIDIA GeForce RTX 3050 6GB Laptop GPU, 18, 49.53, 6144, 2048, 4096\n",
            stderr="",
        )

        with (
            patch("backend.ai.hardware_monitor._resolve_nvidia_smi_path", return_value="C:\\Windows\\System32\\nvidia-smi.exe"),
            patch("backend.ai.hardware_monitor.subprocess.run", return_value=completed),
        ):
            gpu = monitor.collect_gpu_metrics()

        assert gpu.available is True
        assert gpu.name == "NVIDIA GeForce RTX 3050 6GB Laptop GPU"
        assert gpu.utilization_percent == 18.0
        assert gpu.temperature_celsius == 49.5
        assert gpu.vram_total_mb == 6144.0
        assert gpu.vram_used_mb == 2048.0
        assert gpu.vram_free_mb == 4096.0
        assert gpu.vram_utilization_percent == 33.3
        assert gpu.status_reason is None

    def test_gpu_unavailable_when_nvidia_smi_missing(self):
        monitor = HardwareMonitor(cpu_sample_interval=0.0)
        with patch("backend.ai.hardware_monitor._resolve_nvidia_smi_path", return_value=None):
            gpu = monitor.collect_gpu_metrics()

        assert gpu.available is False
        assert gpu.name is None
        assert gpu.vram_total_mb is None
        assert "not found" in (gpu.status_reason or "").lower()

    def test_gpu_unavailable_on_nonzero_exit_and_timeout(self):
        monitor = HardwareMonitor(cpu_sample_interval=0.0, gpu_timeout_seconds=1.0)
        failed = subprocess.CompletedProcess(
            args=["nvidia-smi"],
            returncode=9,
            stdout="",
            stderr="Failed to initialize NVML",
        )

        with (
            patch("backend.ai.hardware_monitor._resolve_nvidia_smi_path", return_value="nvidia-smi"),
            patch("backend.ai.hardware_monitor.subprocess.run", return_value=failed),
        ):
            gpu_err = monitor.collect_gpu_metrics()

        assert gpu_err.available is False
        assert "NVML" in (gpu_err.status_reason or "")

        with (
            patch("backend.ai.hardware_monitor._resolve_nvidia_smi_path", return_value="nvidia-smi"),
            patch("backend.ai.hardware_monitor.subprocess.run", side_effect=subprocess.TimeoutExpired("nvidia-smi", 1.0)),
        ):
            gpu_timeout = monitor.collect_gpu_metrics()

        assert gpu_timeout.available is False
        assert "timed out" in (gpu_timeout.status_reason or "").lower()

    def test_gpu_handles_malformed_and_not_supported_fields(self):
        malformed = HardwareMonitor.parse_nvidia_smi_output("incomplete,row")
        assert malformed.available is False
        assert "malformed" in (malformed.status_reason or "").lower()

        partial = HardwareMonitor.parse_nvidia_smi_output(
            "NVIDIA RTX 3050, [Not Supported], N/A, 6144, 1024, 5120"
        )
        assert partial.available is True
        assert partial.name == "NVIDIA RTX 3050"
        assert partial.utilization_percent is None
        assert partial.temperature_celsius is None
        assert partial.vram_total_mb == 6144.0
        assert partial.vram_used_mb == 1024.0
        assert partial.vram_free_mb == 5120.0
        assert partial.vram_utilization_percent == 16.7


class TestOllamaTelemetryCollection:
    def test_ollama_reachable_with_installed_and_running_models(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/tags":
                return httpx.Response(
                    200,
                    json={
                        "models": [
                            {
                                "name": "qwen3:4b",
                                "model": "qwen3:4b",
                                "size": 2497293931,
                                "digest": "abc123",
                                "modified_at": "2026-11-10T01:25:42Z",
                                "details": {
                                    "format": "gguf",
                                    "family": "qwen3",
                                    "families": ["qwen3"],
                                    "parameter_size": "4.0B",
                                    "quantization_level": "Q4_K_M",
                                },
                            }
                        ]
                    },
                )
            if request.url.path == "/api/ps":
                return httpx.Response(
                    200,
                    json={
                        "models": [
                            {
                                "name": "qwen3:4b",
                                "model": "qwen3:4b",
                                "size": 2900000000,
                                "size_vram": 2900000000,
                                "digest": "abc123",
                                "expires_at": "2026-11-14T23:30:00Z",
                                "context_length": 4096,
                                "details": {
                                    "parameter_size": "4.0B",
                                    "quantization_level": "Q4_K_M",
                                },
                            }
                        ]
                    },
                )
            return httpx.Response(404)

        monitor = HardwareMonitor(
            cpu_sample_interval=0.0,
            http_transport=_make_mock_transport(handler),
        )
        telemetry = monitor.collect_ollama_telemetry()

        assert telemetry.reachable is True
        assert telemetry.installed_count == 1
        assert telemetry.running_count == 1
        assert telemetry.installed_models[0].name == "qwen3:4b"
        assert telemetry.installed_models[0].parameter_size == "4.0B"
        assert telemetry.installed_models[0].quantization_level == "Q4_K_M"
        assert telemetry.running_models[0].name == "qwen3:4b"
        assert telemetry.running_models[0].vram_percent == 100.0
        assert telemetry.running_models[0].context_length == 4096
        assert telemetry.error is None

    def test_ollama_unreachable_connection_and_timeout(self):
        def connect_error_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused", request=request)

        monitor_down = HardwareMonitor(
            cpu_sample_interval=0.0,
            http_transport=_make_mock_transport(connect_error_handler),
        )
        down_res = monitor_down.collect_ollama_telemetry()
        assert down_res.reachable is False
        assert down_res.installed_models == []
        assert down_res.running_models == []
        assert "unreachable" in (down_res.error or "").lower()

        def timeout_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("Timed out", request=request)

        monitor_slow = HardwareMonitor(
            cpu_sample_interval=0.0,
            http_transport=_make_mock_transport(timeout_handler),
        )
        slow_res = monitor_slow.collect_ollama_telemetry()
        assert slow_res.reachable is False
        assert "timed out" in (slow_res.error or "").lower()

    def test_ollama_malformed_and_incomplete_responses(self):
        def bad_shape_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/tags":
                return httpx.Response(200, json={"unexpected_key": []})
            return httpx.Response(200, json={"models": []})

        monitor_bad = HardwareMonitor(
            cpu_sample_interval=0.0,
            http_transport=_make_mock_transport(bad_shape_handler),
        )
        bad_res = monitor_bad.collect_ollama_telemetry()
        assert bad_res.reachable is False
        assert "malformed" in (bad_res.error or "").lower()

        def incomplete_item_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/tags":
                return httpx.Response(
                    200,
                    json={
                        "models": [
                            "not_a_dict",
                            {"digest": "missing-name"},
                            {"name": "qwen3:4b"},
                        ]
                    },
                )
            return httpx.Response(200, json={"models": [None, {"name": "qwen3:4b", "size": "bad"}]})

        monitor_partial = HardwareMonitor(
            cpu_sample_interval=0.0,
            http_transport=_make_mock_transport(incomplete_item_handler),
        )
        partial_res = monitor_partial.collect_ollama_telemetry()
        assert partial_res.reachable is True
        assert partial_res.installed_count == 1
        assert partial_res.installed_models[0].name == "qwen3:4b"
        assert partial_res.installed_models[0].size_bytes == 0
        assert partial_res.running_count == 1
        assert partial_res.running_models[0].size_bytes == 0

    def test_get_hardware_snapshot_returns_structured_payload(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"models": []})

        monitor = HardwareMonitor(
            cpu_sample_interval=0.0,
            http_transport=_make_mock_transport(handler),
        )
        with patch.object(monitor, "collect_gpu_metrics") as mock_gpu:
            from backend.ai.hardware_monitor import GpuMetrics

            mock_gpu.return_value = GpuMetrics(available=False, status_reason="Mocked GPU absent")
            snapshot = monitor.get_hardware_snapshot()

        assert snapshot.os_name
        assert snapshot.cpu.logical_cores >= 1
        assert snapshot.ram.total_gb > 0
        assert snapshot.gpu.available is False
        assert snapshot.ollama.reachable is True
