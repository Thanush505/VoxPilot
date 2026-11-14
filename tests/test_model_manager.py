"""
Unit and API integration tests for VoxPilot ModelManager and /api/system endpoints (Phase 1A).
Uses deterministic mocked hardware and Ollama HTTP transports (no real GPU required).
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.agent.rate_limiter import get_shared_rate_limiter
from backend.automation.emergency_stop import get_shared_emergency_stop_manager
from backend.ai.hardware_monitor import (
    GpuMetrics,
    HardwareMonitor,
    HardwareSnapshot,
    OllamaInstalledModel,
    OllamaRunningModel,
    OllamaTelemetry,
    RamMetrics,
    CpuMetrics,
    reset_shared_hardware_monitor,
    set_shared_hardware_monitor,
)
from backend.ai.model_manager import (
    DEFAULT_PRIMARY_MODEL,
    ActiveInferenceConflictError,
    ConcurrentModelOperationError,
    InsufficientResourcesError,
    InvalidModelNameError,
    ModelLoadTimeoutError,
    ModelManager,
    ModelNotFoundError,
    ModelResourcePolicy,
    OllamaConnectionError,
    reset_shared_model_manager,
    set_shared_model_manager,
)
from backend.main import app


@pytest.fixture(autouse=True)
def reset_singletons():
    yield
    reset_shared_model_manager()
    reset_shared_hardware_monitor()


def _make_stub_monitor(
    available_ram_gb: float = 8.0,
    gpu_available: bool = True,
    free_vram_mb: float = 4300.0,
    total_vram_mb: float = 6144.0,
    ollama_reachable: bool = True,
) -> HardwareMonitor:
    monitor = MagicMock(spec=HardwareMonitor)
    monitor.ollama_base_url = "http://127.0.0.1:11434"
    monitor.collect_ram_metrics.return_value = RamMetrics(
        total_bytes=16 * (1024 ** 3),
        used_bytes=int((16.0 - available_ram_gb) * (1024 ** 3)),
        available_bytes=int(available_ram_gb * (1024 ** 3)),
        total_gb=16.0,
        used_gb=round(16.0 - available_ram_gb, 2),
        available_gb=available_ram_gb,
        utilization_percent=round(((16.0 - available_ram_gb) / 16.0) * 100.0, 1),
    )
    if gpu_available:
        used_vram = max(0.0, total_vram_mb - free_vram_mb)
        monitor.collect_gpu_metrics.return_value = GpuMetrics(
            available=True,
            name="NVIDIA GeForce RTX 3050 6GB Laptop GPU",
            utilization_percent=10.0,
            temperature_celsius=49.5,
            vram_total_mb=total_vram_mb,
            vram_used_mb=used_vram,
            vram_free_mb=free_vram_mb,
            vram_utilization_percent=round((used_vram / total_vram_mb) * 100.0, 1),
        )
    else:
        monitor.collect_gpu_metrics.return_value = GpuMetrics(
            available=False,
            status_reason="nvidia-smi utility not found on host.",
        )

    monitor.collect_cpu_metrics.return_value = CpuMetrics(
        utilization_percent=14.2,
        logical_cores=12,
        physical_cores=8,
        frequency_mhz=2500.0,
        temperature_celsius=None,
        temperature_available=False,
    )
    monitor.collect_ollama_telemetry.return_value = OllamaTelemetry(
        reachable=ollama_reachable,
        base_url="http://127.0.0.1:11434",
        installed_models=[
            OllamaInstalledModel(
                name="qwen3:4b",
                model="qwen3:4b",
                size_bytes=2497293931,
                size_gb=2.33,
                parameter_size="4.0B",
                quantization_level="Q4_K_M",
            )
        ] if ollama_reachable else [],
        running_models=[],
        installed_count=1 if ollama_reachable else 0,
        running_count=0,
        error=None if ollama_reachable else "Ollama unreachable",
    )
    monitor.get_hardware_snapshot.return_value = HardwareSnapshot(
        timestamp="2026-11-14T18:00:00Z",
        os_name="Windows",
        os_version="10.0.26100",
        os_release="11",
        machine="AMD64",
        cpu=monitor.collect_cpu_metrics.return_value,
        ram=monitor.collect_ram_metrics.return_value,
        gpu=monitor.collect_gpu_metrics.return_value,
        ollama=monitor.collect_ollama_telemetry.return_value,
    )
    return monitor


class FakeOllamaState:
    def __init__(
        self,
        installed: Optional[List[Dict[str, Any]]] = None,
        running: Optional[List[Dict[str, Any]]] = None,
        offline: bool = False,
        timeout_on_generate: bool = False,
        generate_delay: float = 0.0,
    ):
        self.installed = installed or [
            {
                "name": "qwen3:4b",
                "model": "qwen3:4b",
                "size": 2497293931,
                "digest": "digest-qwen3",
                "details": {"parameter_size": "4.0B", "quantization_level": "Q4_K_M", "family": "qwen3"},
            },
            {
                "name": "moondream:1.8b",
                "model": "moondream:1.8b",
                "size": 1700000000,
                "digest": "digest-moondream",
                "details": {"parameter_size": "1.8B", "quantization_level": "Q4_0", "family": "moondream"},
            },
            {
                "name": "llama3:70b",
                "model": "llama3:70b",
                "size": 40 * (1024 ** 3),
                "digest": "digest-huge",
                "details": {"parameter_size": "70B", "quantization_level": "Q4_0", "family": "llama"},
            },
        ]
        self.running = running or []
        self.offline = offline
        self.timeout_on_generate = timeout_on_generate
        self.generate_delay = generate_delay
        self.generate_calls: List[Dict[str, Any]] = []

    def transport(self) -> httpx.MockTransport:
        import json

        def handler(request: httpx.Request) -> httpx.Response:
            if self.offline:
                raise httpx.ConnectError("Connection refused", request=request)

            if request.url.path == "/api/tags":
                return httpx.Response(200, json={"models": list(self.installed)})

            if request.url.path == "/api/ps":
                return httpx.Response(200, json={"models": list(self.running)})

            if request.url.path == "/api/show":
                body = json.loads(request.content.decode("utf-8"))
                return httpx.Response(
                    200,
                    json={
                        "details": {"family": "qwen3", "parameter_size": "4.0B"},
                        "capabilities": ["completion", "tools"],
                    },
                )

            if request.url.path == "/api/generate":
                if self.generate_delay > 0:
                    time.sleep(self.generate_delay)
                if self.timeout_on_generate:
                    raise httpx.ReadTimeout("Timed out", request=request)
                body = json.loads(request.content.decode("utf-8"))
                self.generate_calls.append(body)
                model_name = body.get("model")
                keep_alive = body.get("keep_alive")

                if keep_alive == 0 or str(keep_alive) == "0":
                    self.running = [m for m in self.running if m["name"] != model_name]
                else:
                    matched = next((m for m in self.installed if m["name"] == model_name), None)
                    if matched and not any(m["name"] == model_name for m in self.running):
                        self.running.append(
                            {
                                "name": matched["name"],
                                "model": matched["model"],
                                "size": matched["size"],
                                "size_vram": matched["size"],
                                "digest": matched["digest"],
                                "details": matched.get("details", {}),
                            }
                        )
                return httpx.Response(200, json={"done": True})

            return httpx.Response(404)

        return httpx.MockTransport(handler)


class TestModelManagerDiscoveryAndValidation:
    def test_installed_and_running_discovery_and_metadata(self):
        state = FakeOllamaState(
            running=[
                {
                    "name": "qwen3:4b",
                    "model": "qwen3:4b",
                    "size": 2497293931,
                    "size_vram": 2497293931,
                    "digest": "digest-qwen3",
                }
            ]
        )
        manager = ModelManager(
            hardware_monitor=_make_stub_monitor(),
            http_transport=state.transport(),
        )

        installed = manager.list_installed_models()
        assert len(installed) == 3
        assert installed[0].name == "qwen3:4b"

        running = manager.list_running_models()
        assert len(running) == 1
        assert manager.is_model_running("qwen3:4b") is True
        assert manager.is_model_running("moondream:1.8b") is False

        meta = manager.get_model_metadata("qwen3:4b")
        assert meta["name"] == "qwen3:4b"
        assert "tools" in meta["capabilities"]

    def test_rejects_invalid_and_unknown_models(self):
        state = FakeOllamaState()
        manager = ModelManager(
            hardware_monitor=_make_stub_monitor(),
            http_transport=state.transport(),
        )

        for bad_name in ("", "   ", "../etc/passwd", "bad\\model", "bad model!"):
            with pytest.raises(InvalidModelNameError):
                manager.validate_model_name(bad_name)

        with pytest.raises(ModelNotFoundError):
            manager.load_model("nonexistent:7b")

        with pytest.raises(InvalidModelNameError):
            manager.load_model("qwen3:4b", keep_alive="0")


class TestModelManagerLifecycleAndSafeguards:
    def test_load_and_unload_installed_model(self):
        state = FakeOllamaState()
        manager = ModelManager(
            hardware_monitor=_make_stub_monitor(available_ram_gb=8.0, free_vram_mb=4300.0),
            http_transport=state.transport(),
        )

        load_res = manager.load_model("qwen3:4b", keep_alive="10m")
        assert load_res.success is True
        assert load_res.already_loaded is False
        assert load_res.model == "qwen3:4b"
        assert manager.get_active_model() == "qwen3:4b"
        assert manager.is_model_running("qwen3:4b") is True
        assert state.generate_calls[-1] == {
            "model": "qwen3:4b",
            "prompt": "",
            "keep_alive": "10m",
            "stream": False,
        }

        # Second load recognizes model is already running
        already_res = manager.load_model("qwen3:4b")
        assert already_res.already_loaded is True
        assert len(state.generate_calls) == 1

        # Unload inactive model cleanly via keep_alive=0
        unload_res = manager.unload_model("qwen3:4b")
        assert unload_res.success is True
        assert unload_res.was_running is True
        assert manager.is_model_running("qwen3:4b") is False
        assert state.generate_calls[-1]["keep_alive"] == 0

        # Primary default remains qwen3:4b
        assert manager.default_model == DEFAULT_PRIMARY_MODEL

    def test_prevents_unsafe_unload_during_active_inference_or_execution(self):
        state = FakeOllamaState(
            running=[
                {
                    "name": "qwen3:4b",
                    "model": "qwen3:4b",
                    "size": 2497293931,
                    "size_vram": 2497293931,
                }
            ]
        )
        manager = ModelManager(
            hardware_monitor=_make_stub_monitor(),
            http_transport=state.transport(),
        )

        # Direct inference tracking lock
        with manager.track_inference("qwen3:4b"):
            with pytest.raises(ActiveInferenceConflictError):
                manager.unload_model("qwen3:4b")

        assert manager.is_model_running("qwen3:4b") is True

        # Active LangGraph RateLimiter execution slot protection
        limiter = get_shared_rate_limiter()
        acquired, _ = limiter.acquire_execution_slot("exec-active-1")
        assert acquired is True
        try:
            with pytest.raises(ActiveInferenceConflictError, match="active agent inference"):
                manager.unload_model("qwen3:4b")
        finally:
            limiter.release_execution_slot("exec-active-1")

        # After inference ends, unload succeeds
        res = manager.unload_model("qwen3:4b")
        assert res.success is True
        assert manager.is_model_running("qwen3:4b") is False

    def test_rejects_load_on_insufficient_ram_or_vram(self):
        state = FakeOllamaState()

        # Insufficient GPU VRAM (70B model on 6GB RTX 3050)
        gpu_constrained_mgr = ModelManager(
            hardware_monitor=_make_stub_monitor(available_ram_gb=10.0, gpu_available=True, free_vram_mb=4300.0),
            http_transport=state.transport(),
        )
        with pytest.raises(InsufficientResourcesError, match="VRAM"):
            gpu_constrained_mgr.load_model("llama3:70b")

        # Insufficient System RAM (< min_free_ram_mb reserve)
        low_ram_mgr = ModelManager(
            hardware_monitor=_make_stub_monitor(available_ram_gb=0.8, gpu_available=False),
            http_transport=state.transport(),
        )
        with pytest.raises(InsufficientResourcesError, match="RAM"):
            low_ram_mgr.load_model("qwen3:4b")

    def test_unloads_other_inactive_model_when_vram_is_reclaimable(self):
        state = FakeOllamaState(
            running=[
                {
                    "name": "qwen3:4b",
                    "model": "qwen3:4b",
                    "size": 2497293931,
                    "size_vram": 2497293931,
                }
            ]
        )
        manager = ModelManager(
            hardware_monitor=_make_stub_monitor(available_ram_gb=8.0, gpu_available=True, free_vram_mb=1500.0),
            policy=ModelResourcePolicy(min_free_vram_mb=512.0, auto_unload_other_models=True),
            http_transport=state.transport(),
        )
        manager.set_active_model("qwen3:4b")

        res = manager.load_model("moondream:1.8b", keep_alive="60s")
        assert res.success is True
        assert "qwen3:4b" in res.unloaded_models
        assert manager.is_model_running("moondream:1.8b") is True
        assert manager.is_model_running("qwen3:4b") is False

    def test_connection_failure_and_timeout_handling(self):
        offline_state = FakeOllamaState(offline=True)
        offline_mgr = ModelManager(
            hardware_monitor=_make_stub_monitor(ollama_reachable=False),
            http_transport=offline_state.transport(),
        )
        with pytest.raises(OllamaConnectionError):
            offline_mgr.list_installed_models()
        with pytest.raises(OllamaConnectionError):
            offline_mgr.load_model("qwen3:4b")

        timeout_state = FakeOllamaState(timeout_on_generate=True)
        timeout_mgr = ModelManager(
            hardware_monitor=_make_stub_monitor(),
            http_transport=timeout_state.transport(),
        )
        with pytest.raises(ModelLoadTimeoutError):
            timeout_mgr.load_model("qwen3:4b")

    def test_concurrent_model_operation_protection(self):
        state = FakeOllamaState(generate_delay=0.25)
        manager = ModelManager(
            hardware_monitor=_make_stub_monitor(),
            http_transport=state.transport(),
        )

        errors: List[Exception] = []

        def first_load():
            manager.load_model("qwen3:4b")

        t1 = threading.Thread(target=first_load)
        t1.start()
        time.sleep(0.05)  # Allow thread 1 to acquire _operation_lock

        try:
            manager.load_model("moondream:1.8b")
        except ConcurrentModelOperationError as exc:
            errors.append(exc)
        finally:
            t1.join(timeout=2.0)

        assert len(errors) == 1


class TestSystemApiEndpoints:
    def test_api_system_hardware_and_models_endpoints(self):
        state = FakeOllamaState(
            running=[
                {
                    "name": "qwen3:4b",
                    "model": "qwen3:4b",
                    "size": 2497293931,
                    "size_vram": 2497293931,
                }
            ]
        )
        stub_monitor = _make_stub_monitor()
        stub_monitor.collect_ollama_telemetry.return_value = OllamaTelemetry(
            reachable=True,
            base_url="http://127.0.0.1:11434",
            installed_models=[
                OllamaInstalledModel(
                    name="qwen3:4b",
                    model="qwen3:4b",
                    size_bytes=2497293931,
                    size_gb=2.33,
                    parameter_size="4.0B",
                    quantization_level="Q4_K_M",
                )
            ],
            running_models=[
                OllamaRunningModel(
                    name="qwen3:4b",
                    model="qwen3:4b",
                    size_bytes=2497293931,
                    size_gb=2.33,
                    size_vram_bytes=2497293931,
                    size_vram_mb=2381.6,
                    vram_percent=100.0,
                )
            ],
            installed_count=1,
            running_count=1,
        )
        stub_monitor.get_hardware_snapshot.return_value.ollama = stub_monitor.collect_ollama_telemetry.return_value

        manager = ModelManager(hardware_monitor=stub_monitor, http_transport=state.transport())
        set_shared_hardware_monitor(stub_monitor)
        set_shared_model_manager(manager)

        client = TestClient(app)

        hw_resp = client.get("/api/system/hardware")
        assert hw_resp.status_code == 200
        hw_json = hw_resp.json()
        assert hw_json["gpu"]["name"] == "NVIDIA GeForce RTX 3050 6GB Laptop GPU"
        assert hw_json["ram"]["total_gb"] == 16.0

        models_resp = client.get("/api/system/models")
        assert models_resp.status_code == 200
        assert models_resp.json()["default_model"] == "qwen3:4b"
        assert models_resp.json()["installed_count"] == 1

        running_resp = client.get("/api/system/models/running")
        assert running_resp.status_code == 200
        assert running_resp.json()["running_count"] == 1

        # Cross-origin blocked by CSRF guard
        csrf_resp = client.post(
            "/api/system/models/unload",
            json={"model_name": "qwen3:4b"},
            headers={"Origin": "https://evil.example"},
        )
        assert csrf_resp.status_code == 403

        # Unload running model via API
        unload_resp = client.post(
            "/api/system/models/unload",
            json={"model_name": "qwen3:4b"},
        )
        assert unload_resp.status_code == 200
        assert unload_resp.json()["was_running"] is True

        # Load installed model via /agent/system alias
        load_resp = client.post(
            "/agent/system/models/load",
            json={"model_name": "moondream:1.8b", "keep_alive": "5m"},
        )
        assert load_resp.status_code == 200
        assert load_resp.json()["model"] == "moondream:1.8b"

        # Unknown model -> 404 structured error
        missing_resp = client.post(
            "/api/system/models/load",
            json={"model_name": "unknown-model:latest"},
        )
        assert missing_resp.status_code == 404
        assert missing_resp.json()["error_code"] == "model_not_found"

        # Invalid model name -> 400 structured error
        bad_resp = client.post(
            "/api/system/models/load",
            json={"model_name": "../bad"},
        )
        assert bad_resp.status_code == 400
        assert bad_resp.json()["error_code"] == "invalid_model_name"

        # Insufficient resources -> 409 structured error
        oom_resp = client.post(
            "/api/system/models/load",
            json={"model_name": "llama3:70b"},
        )
        assert oom_resp.status_code == 409
        assert oom_resp.json()["error_code"] == "insufficient_resources"

        # Model metadata endpoint
        meta_resp = client.get("/api/system/models/metadata", params={"model_name": "moondream:1.8b"})
        assert meta_resp.status_code == 200
        assert meta_resp.json()["name"] == "moondream:1.8b"

        # Active inference conflict -> 409 structured error
        with manager.track_inference("moondream:1.8b"):
            busy_resp = client.post(
                "/api/system/models/unload",
                json={"model_name": "moondream:1.8b"},
            )
            assert busy_resp.status_code == 409
            assert busy_resp.json()["error_code"] == "active_inference_conflict"

        # Timeout on load -> 504 structured error
        timeout_state = FakeOllamaState(timeout_on_generate=True)
        set_shared_model_manager(
            ModelManager(hardware_monitor=stub_monitor, http_transport=timeout_state.transport())
        )
        timeout_resp = client.post(
            "/api/system/models/load",
            json={"model_name": "qwen3:4b"},
        )
        assert timeout_resp.status_code == 504
        assert timeout_resp.json()["error_code"] == "model_load_timeout"

        # Unreachable Ollama -> 503 structured error
        offline_state = FakeOllamaState(offline=True)
        set_shared_model_manager(
            ModelManager(
                hardware_monitor=_make_stub_monitor(ollama_reachable=False),
                http_transport=offline_state.transport(),
            )
        )
        offline_resp = client.post(
            "/api/system/models/load",
            json={"model_name": "qwen3:4b"},
        )
        assert offline_resp.status_code == 503
        assert offline_resp.json()["error_code"] == "ollama_unavailable"

    def test_active_operation_blocks_unload_and_auto_unload(self):
        state = FakeOllamaState(
            running=[
                {
                    "name": "qwen3:4b",
                    "model": "qwen3:4b",
                    "size": 2497293931,
                    "size_vram": 2497293931,
                }
            ]
        )
        manager = ModelManager(
            hardware_monitor=_make_stub_monitor(available_ram_gb=8.0, gpu_available=True, free_vram_mb=1500.0),
            http_transport=state.transport(),
        )
        stop_mgr = get_shared_emergency_stop_manager()
        stop_mgr.register_active_operation(
            operation_id="op-llm-test",
            execution_id="exec-llm-test",
            category="llm",
            description="Synthesizing response",
        )
        try:
            with pytest.raises(ActiveInferenceConflictError):
                manager.unload_model("qwen3:4b")
            with pytest.raises(ActiveInferenceConflictError):
                manager.load_model("moondream:1.8b")
        finally:
            stop_mgr.unregister_active_operation("op-llm-test")
