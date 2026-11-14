"""
Targeted tests for VoxPilot Screen Awareness & AI Computer Vision Foundation.
Covers on-demand capture, authorization, emergency stop, temp cleanup,
profile isolation, honest Ollama vision capability detection, agent integration,
and frontend Playwright capture controls.
"""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from playwright.sync_api import sync_playwright

from backend.ai.hardware_monitor import (
    CpuMetrics,
    GpuMetrics,
    HardwareSnapshot,
    OllamaTelemetry,
    RamMetrics,
)
from backend.ai.vision import (
    OllamaVisionProvider,
    ScreenCaptureAccessDeniedError,
    ScreenCaptureAuthorizationError,
    ScreenCaptureError,
    ScreenCaptureNotFoundError,
    ScreenCaptureService,
    VisionAnalysisResult,
    VisionAvailabilityStatus,
    VisionProvider,
    get_shared_screen_capture_service,
    set_shared_vision_provider,
)
from backend.agent.graph import VoxPilotAgent
from backend.automation.emergency_stop import (
    EmergencyStopActiveError,
    get_shared_emergency_stop_manager,
)
from backend.main import app


def _make_test_image(width: int = 800, height: int = 600) -> Image.Image:
    return Image.new("RGB", (width, height), color=(36, 99, 235))


@pytest.fixture(autouse=True)
def _reset_screen_and_estop():
    service = get_shared_screen_capture_service()
    service.clear_all_captures()
    set_shared_vision_provider(None)
    estop = get_shared_emergency_stop_manager()
    if estop.is_emergency_stop_active():
        estop.reset_emergency_stop()
    yield
    service.clear_all_captures()
    set_shared_vision_provider(None)
    if estop.is_emergency_stop_active():
        estop.reset_emergency_stop()


# ==============================================================================
# 1. Screenshot Capture, Failures, Authorization & Temp Cleanup
# ==============================================================================

def test_capture_requires_explicit_user_authorization(tmp_path: Path):
    svc = ScreenCaptureService(grabber=lambda: _make_test_image(), temp_dir=tmp_path)
    with pytest.raises(ScreenCaptureAuthorizationError):
        svc.capture_primary_display(profile_id="default", explicit_user_action=False)


def test_capture_resizes_oversized_image_and_cleans_up_temp_file(tmp_path: Path):
    svc = ScreenCaptureService(
        grabber=lambda: _make_test_image(3840, 2160),
        temp_dir=tmp_path,
        max_dimension=1600,
        persist_temp_file=True,
    )
    entry = svc.capture_primary_display(profile_id="default", explicit_user_action=True)
    assert entry.width == 1600
    assert entry.height == 900
    assert entry.temp_file_path is not None
    assert entry.temp_file_path.exists()
    temp_path = entry.temp_file_path

    removed = svc.delete_capture(entry.capture_id, profile_id="default")
    assert removed is True
    assert not temp_path.exists()
    assert entry.image_bytes == b""
    assert entry.image_base64 == ""


def test_capture_failure_raises_screen_capture_error(tmp_path: Path):
    def failing_grabber():
        raise RuntimeError("Display handle unavailable")

    svc = ScreenCaptureService(grabber=failing_grabber, temp_dir=tmp_path)
    with pytest.raises(ScreenCaptureError, match="Display handle unavailable"):
        svc.capture_primary_display(profile_id="default", explicit_user_action=True)


def test_capture_blocked_when_emergency_stop_active(tmp_path: Path):
    estop = get_shared_emergency_stop_manager()
    estop.trigger_emergency_stop(reason="Safety test", source="pytest")
    svc = ScreenCaptureService(grabber=lambda: _make_test_image(), temp_dir=tmp_path)
    with pytest.raises(EmergencyStopActiveError):
        svc.capture_primary_display(profile_id="default", explicit_user_action=True)


def test_capture_ttl_expiration_cleans_up_entries(tmp_path: Path):
    svc = ScreenCaptureService(
        grabber=lambda: _make_test_image(),
        temp_dir=tmp_path,
        ttl_seconds=30,
        persist_temp_file=True,
    )
    entry = svc.capture_primary_display(profile_id="default", explicit_user_action=True)
    temp_file = entry.temp_file_path
    assert temp_file is not None and temp_file.exists()

    # Force expiration
    entry.created_at = time.time() - 120
    with pytest.raises(ScreenCaptureNotFoundError):
        svc.get_capture(entry.capture_id, profile_id="default")
    assert not temp_file.exists()


# ==============================================================================
# 2. Profile Isolation
# ==============================================================================

def test_capture_profile_isolation(tmp_path: Path):
    svc = ScreenCaptureService(grabber=lambda: _make_test_image(), temp_dir=tmp_path)
    entry = svc.capture_primary_display(profile_id="work", explicit_user_action=True)

    with pytest.raises(ScreenCaptureAccessDeniedError):
        svc.get_capture(entry.capture_id, profile_id="personal")

    with pytest.raises(ScreenCaptureAccessDeniedError):
        svc.delete_capture(entry.capture_id, profile_id="personal")

    # Owning profile still has access
    owned = svc.get_capture(entry.capture_id, profile_id="work")
    assert owned.capture_id == entry.capture_id


# ==============================================================================
# 3. Vision Provider Availability & Honest Model Detection
# ==============================================================================

def _fake_hardware_snapshot() -> HardwareSnapshot:
    return HardwareSnapshot(
        timestamp="2026-11-15T00:00:00Z",
        os_name="Windows",
        os_release="11",
        os_version="10.0.26100",
        machine="AMD64",
        cpu=CpuMetrics(utilization_percent=18.0, logical_cores=12, physical_cores=8),
        ram=RamMetrics(
            total_bytes=16 * 1024**3,
            used_bytes=8 * 1024**3,
            available_bytes=8 * 1024**3,
            total_gb=16.0,
            used_gb=8.0,
            available_gb=8.0,
            utilization_percent=50.0,
        ),
        gpu=GpuMetrics(
            available=True,
            name="NVIDIA GeForce RTX 3050 Laptop GPU",
            vram_total_mb=6144.0,
            vram_used_mb=1200.0,
            vram_free_mb=4944.0,
            vram_utilization_percent=19.5,
        ),
        ollama=OllamaTelemetry(reachable=True, base_url="http://127.0.0.1:11434"),
    )


def test_ollama_vision_provider_honest_when_only_qwen3_4b_installed():
    mock_hw = MagicMock()
    mock_hw.get_hardware_snapshot.return_value = _fake_hardware_snapshot()

    mock_client = MagicMock()
    tags_resp = MagicMock(status_code=200)
    tags_resp.json.return_value = {"models": [{"name": "qwen3:4b"}]}
    show_resp = MagicMock(status_code=200)
    show_resp.json.return_value = {"capabilities": ["completion", "tools", "thinking"]}
    mock_client.get.return_value = tags_resp
    mock_client.post.return_value = show_resp

    cm = MagicMock()
    cm.__enter__.return_value = mock_client
    cm.__exit__.return_value = False

    provider = OllamaVisionProvider(hardware_monitor=mock_hw)
    with patch("backend.ai.vision.httpx.Client", return_value=cm):
        status = provider.check_availability()

    assert status.vision_available is False
    assert status.default_text_model == "qwen3:4b"
    assert status.default_text_model_supports_vision is False
    assert "qwen3:4b" in status.message
    assert any(rec.name == "moondream" and rec.fits_hardware for rec in status.recommendations)


def test_ollama_vision_provider_analyzes_when_vision_model_installed():
    mock_hw = MagicMock()
    mock_hw.get_hardware_snapshot.return_value = _fake_hardware_snapshot()

    mock_client = MagicMock()
    tags_resp = MagicMock(status_code=200)
    tags_resp.json.return_value = {"models": [{"name": "qwen3:4b"}, {"name": "moondream:latest"}]}

    def fake_show(url, json=None):
        resp = MagicMock(status_code=200)
        name = (json or {}).get("name", "")
        if "moondream" in name:
            resp.json.return_value = {"capabilities": ["completion", "vision"]}
        else:
            resp.json.return_value = {"capabilities": ["completion", "tools", "thinking"]}
        return resp

    mock_client.get.return_value = tags_resp
    mock_client.post.side_effect = fake_show

    cm = MagicMock()
    cm.__enter__.return_value = mock_client
    cm.__exit__.return_value = False

    provider = OllamaVisionProvider(hardware_monitor=mock_hw)
    with patch("backend.ai.vision.httpx.Client", return_value=cm), patch(
        "backend.ai.vision.ollama.chat",
        return_value={"message": {"content": "You have a Settings window open."}},
    ) as mock_chat:
        res = provider.analyze_image("ZmFrZWJhc2U2NA==", "What is on my screen?")

    assert res.available is True
    assert res.status == "completed"
    assert res.model_used == "moondream:latest"
    assert "Settings window" in res.answer
    sent_messages = mock_chat.call_args.kwargs["messages"]
    assert sent_messages[1]["images"] == ["ZmFrZWJhc2U2NA=="]


# ==============================================================================
# 4. API Endpoints & Agent Integration
# ==============================================================================

def test_screen_api_requires_confirmation_and_enforces_profile_isolation():
    client = TestClient(app)
    svc = get_shared_screen_capture_service()

    # Unconfirmed capture must be rejected
    unconfirmed = client.post("/api/screen/capture", json={"confirmed": False})
    assert unconfirmed.status_code == 403

    # Confirmed capture succeeds with mocked grabber
    with patch.object(svc, "_grabber", return_value=_make_test_image(1280, 720)):
        captured = client.post(
            "/api/screen/capture",
            json={"confirmed": True, "profile_id": "work"},
        )
    assert captured.status_code == 200
    data = captured.json()
    cid = data["capture_id"]
    assert data["width"] == 1280
    assert data["height"] == 720
    assert data["preview_data_url"].startswith("data:image/jpeg;base64,")

    # Different profile cannot delete or analyze it
    denied_del = client.delete(f"/api/screen/capture/{cid}?profile_id=personal")
    assert denied_del.status_code == 403

    # Owning profile can delete it
    ok_del = client.delete(f"/api/screen/capture/{cid}?profile_id=work")
    assert ok_del.status_code == 200
    assert ok_del.json()["removed"] is True


def test_agent_screen_awareness_does_not_save_to_long_term_memory():
    svc = get_shared_screen_capture_service()
    with patch.object(svc, "_grabber", return_value=_make_test_image(1024, 768)):
        entry = svc.capture_primary_display(profile_id="default", explicit_user_action=True)

    class _StubVisionProvider(VisionProvider):
        def check_availability(self) -> VisionAvailabilityStatus:
            return VisionAvailabilityStatus(
                vision_available=True,
                active_vision_model="moondream",
                installed_vision_models=["moondream"],
                ollama_reachable=True,
                message="Ready",
            )

        def analyze_image(self, image_base64: str, user_prompt: str, system_prompt=None) -> VisionAnalysisResult:
            return VisionAnalysisResult(
                available=True,
                status="completed",
                model_used="moondream",
                answer="Visible error dialog: Connection refused on port 8000.",
            )

    set_shared_vision_provider(_StubVisionProvider())

    mock_extractor = MagicMock()
    mock_repo = MagicMock()
    mock_repo.get_conversation_sync.return_value = None
    mock_memory_svc = MagicMock()
    mock_memory_svc.search_memories.return_value = []
    mock_pers_svc = MagicMock()
    mock_pers_svc.build_personalization_context.return_value = {"summary_text": "", "preferences": []}
    mock_profile_svc = MagicMock()
    mock_profile_svc.get_active_profile_id.return_value = "default"

    agent = VoxPilotAgent(
        memory_extractor=mock_extractor,
        repository=mock_repo,
        memory_service=mock_memory_svc,
        personalization_service=mock_pers_svc,
        profile_service=mock_profile_svc,
    )

    state = agent.process_request(
        user_input="Explain this error.",
        profile_id="default",
        capture_id=entry.capture_id,
    )

    assert state["execution_status"] == "completed"
    assert state["intent"] == "screen_awareness"
    assert state["screen_analysis_used"] is True
    assert "Connection refused" in state["response"]
    # Crucial privacy assertion: screenshot turn must NEVER invoke memory_extractor
    mock_extractor.extract_and_store.assert_not_called()
    # Temporary capture must be cleaned up after use
    with pytest.raises(ScreenCaptureNotFoundError):
        svc.get_capture(entry.capture_id, profile_id="default")


# ==============================================================================
# 5. Frontend Playwright Screen Awareness Controls
# ==============================================================================

def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_frontend_screen_capture_controls_playwright():
    dist_index = Path(__file__).resolve().parents[1] / "frontend" / "dist" / "index.html"
    if not dist_index.exists():
        pytest.skip("frontend/dist not built")

    import uvicorn

    port = _find_free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    try:
        for _ in range(30):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)

        base_url = f"http://127.0.0.1:{port}"
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})

            tiny_png_data_url = (
                "data:image/png;base64,"
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
            )

            page.route(
                "**/health",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='{"status":"healthy","service":"VoxPilot","version":"0.5.2"}',
                ),
            )
            page.route(
                "**/agent/emergency-stop/status",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='{"is_active":false,"active_operations_count":0,"active_operations":[]}',
                ),
            )
            page.route(
                "**/api/profiles*",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=(
                        '{"profiles":[{"id":"default","name":"Default","normalized_name":"default",'
                        '"avatar":"bot","theme_mode":"dark","accent_color":"blue","is_active":true,"is_default":true}],'
                        '"active_profile":{"id":"default","name":"Default","normalized_name":"default",'
                        '"avatar":"bot","theme_mode":"dark","accent_color":"blue","is_active":true,"is_default":true},'
                        '"total":1,"avatars":["bot"]}'
                    ),
                ),
            )
            page.route(
                "**/agent/conversations*",
                lambda route: route.fulfill(status=200, content_type="application/json", body="[]"),
            )
            page.route(
                "**/agent/voice/status*",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='{"is_active":false,"phase":"idle","is_recording":false,"events":[],"latest_event_id":0,"settings":{"shortcut":"Ctrl+Shift+V"}}',
                ),
            )

            page.route(
                "**/api/screen/capture",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=(
                        '{"capture_id":"scr_test_123","profile_id":"default","width":1920,"height":1080,'
                        '"mime_type":"image/png","size_bytes":24576,"created_at":"2026-11-15T00:00:00Z",'
                        '"monitor_note":"Primary display","ephemeral":true,'
                        f'"preview_data_url":"{tiny_png_data_url}",'
                        '"vision_status":{"vision_available":false,"active_vision_model":null,'
                        '"installed_vision_models":[],"default_text_model":"qwen3:4b",'
                        '"default_text_model_supports_vision":false,"ollama_reachable":true,'
                        '"message":"No local vision model installed.","recommendations":[]}}'
                    ),
                ),
            )
            page.route(
                "**/api/screen/capture/scr_test_123*",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='{"capture_id":"scr_test_123","removed":true,"profile_id":"default"}',
                ),
            )

            def handle_execute(route):
                body = route.request.post_data_json or {}
                assert body.get("capture_id") == "scr_test_123"
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=(
                        '{"execution_id":"exec_scr_1","conversation_id":"conv_scr_1",'
                        '"execution_status":"completed","user_input":"What is on my screen?",'
                        '"intent":"screen_awareness","confidence":1.0,"plan":[],"execution_results":[],'
                        '"completed_steps":[{"action":"screen_awareness_analysis","status":"completed"}],'
                        '"failed_steps":[],"response":"Screen analysis: Settings dialog is open.",'
                        '"requires_confirmation":false,"replan_count":0,"replan_history":[]}'
                    ),
                )

            page.route("**/agent/execute", handle_execute)

            page.goto(base_url, wait_until="domcontentloaded")
            page.locator("header .health-pill.connected").wait_for(state="visible", timeout=10000)
            page.locator("button.nav-item:has-text('AI Agent')").click()

            capture_btn = page.locator('[data-testid="capture-screen-btn"]:not([disabled])')
            capture_btn.wait_for(state="visible", timeout=5000)
            capture_btn.click()

            confirm_bar = page.locator('[data-testid="screen-capture-confirm-bar"]')
            confirm_bar.wait_for(state="visible", timeout=5000)
            page.locator('[data-testid="confirm-screen-capture-btn"]').click()

            preview_card = page.locator('[data-testid="screen-preview-card"]')
            preview_card.wait_for(state="visible", timeout=5000)
            assert "1920x1080" in preview_card.inner_text()

            # Test remove screenshot button
            page.locator('[data-testid="remove-screen-capture-btn"]').click()
            preview_card.wait_for(state="hidden", timeout=5000)

            # Re-capture and send a visual question
            capture_btn.wait_for(state="visible", timeout=5000)
            capture_btn.click()
            page.locator('[data-testid="confirm-screen-capture-btn"]').click()
            preview_card.wait_for(state="visible", timeout=5000)

            textarea = page.locator(".chat-textarea")
            textarea.fill("What is on my screen?")
            page.locator(".chat-send-button").click()

            page.locator("text=Screen analysis: Settings dialog is open.").wait_for(
                state="visible", timeout=5000
            )
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=3)
