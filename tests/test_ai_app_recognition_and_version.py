"""
VoxPilot - Tests for AI Application Recognition (ISSUE 1) & Version Consistency (ISSUE 2)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from backend.agent.intent import IntentRecognizer, extract_open_command_target
from backend.agent.planner import AgentPlanner
from backend.automation.application_discovery import (
    ApplicationDiscoveryService,
    DiscoveredApplication,
    compute_soundex,
    normalize_app_name,
    phonetic_similarity,
    reset_shared_discovery_service,
)
from backend.version import DEFAULT_APP_VERSION, get_app_version
from scripts.verify_version_consistency import verify_version_consistency


def _build_mock_discovery(tmp_path: Path) -> ApplicationDiscoveryService:
    svc = ApplicationDiscoveryService(index_file=tmp_path / "app_index.json", auto_discover=False)
    svc._last_refreshed_monotonic = 1e12
    svc._entries = {}
    svc._alias_to_ids = {}

    apps = [
        DiscoveredApplication(
            app_id="whatsapp",
            name="whatsapp",
            display_name="WhatsApp",
            source="store_app",
            launch_method="store_app",
            executable_path="5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App",
            package_id="5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App",
            aliases=["whatsapp", "whats app", "whatsapp desktop"],
        ),
        DiscoveredApplication(
            app_id="google-chrome",
            name="google chrome",
            display_name="Google Chrome",
            source="registry",
            launch_method="executable",
            executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            aliases=["chrome", "google chrome", "web browser"],
        ),
        DiscoveredApplication(
            app_id="visual-studio-code",
            name="visual studio code",
            display_name="Visual Studio Code",
            source="program_files",
            launch_method="executable",
            executable_path=r"C:\Program Files\Microsoft VS Code\Code.exe",
            aliases=["vscode", "vs code", "code", "visual studio code"],
        ),
        DiscoveredApplication(
            app_id="calculator",
            name="calculator",
            display_name="Calculator",
            source="system_utility",
            launch_method="executable",
            executable_path=r"C:\Windows\System32\calc.exe",
            aliases=["calculator", "calc"],
        ),
        DiscoveredApplication(
            app_id="microsoft-word",
            name="microsoft word",
            display_name="Microsoft Word",
            source="start_menu",
            launch_method="executable",
            executable_path=r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE",
            aliases=["word", "ms word", "microsoft word"],
        ),
        DiscoveredApplication(
            app_id="wordpad",
            name="wordpad",
            display_name="WordPad",
            source="start_menu",
            launch_method="executable",
            executable_path=r"C:\Program Files\Windows NT\Accessories\wordpad.exe",
            aliases=["wordpad"],
        ),
    ]
    for app in apps:
        svc._add_or_merge_entry(app)
    return svc


# ==============================================================================
# ISSUE 1 — Application Normalization, Phonetic & Fuzzy Matching, Intent & Planner
# ==============================================================================

def test_normalize_and_soundex_general_phonetics() -> None:
    assert normalize_app_name("what's app") == "whats app"
    assert normalize_app_name("WhatsApp.exe") == "whatsapp"
    assert compute_soundex("whatsapp") == compute_soundex("watchup")
    assert phonetic_similarity("watch up", "whatsapp") >= 0.80
    assert phonetic_similarity("totally unrelated quantum", "whatsapp") == 0.0


@pytest.mark.parametrize(
    "query,expected_display",
    [
        ("watch up", "WhatsApp"),
        ("what's app", "WhatsApp"),
        ("whartsapp", "WhatsApp"),
        ("chrome", "Google Chrome"),
        ("vs code", "Visual Studio Code"),
        ("calculator", "Calculator"),
    ],
)
def test_discovery_resolves_imperfect_stt_and_aliases(tmp_path: Path, query: str, expected_display: str) -> None:
    svc = _build_mock_discovery(tmp_path)
    res = svc.resolve_query(query)
    assert res["status"] == "resolved"
    assert res["match"] is not None
    assert res["match"].display_name == expected_display
    assert res["confidence"] >= 0.72


def test_discovery_rejects_unrelated_low_confidence_query(tmp_path: Path) -> None:
    svc = _build_mock_discovery(tmp_path)
    res = svc.resolve_query("quantum banana synthesizer")
    assert res["status"] == "not_found"
    assert res["match"] is None
    assert res["confidence"] == 0.0


def test_extract_open_command_target_handles_stt_punctuation_and_prefixes() -> None:
    assert extract_open_command_target("Open, watch up.") == "watch up"
    assert extract_open_command_target("Please open what's app") == "what's app"
    assert extract_open_command_target("Hey VoxPilot, launch Chrome!") == "Chrome"
    assert extract_open_command_target("Can you start vs code?") == "vs code"
    assert extract_open_command_target("What is WhatsApp?") is None
    assert extract_open_command_target("Tell me about Chrome") is None


def test_intent_distinguishes_open_vs_conversation_vs_in_app_action() -> None:
    recognizer = IntentRecognizer()

    open_res = recognizer.recognize_intent("Open, watch up.")
    assert open_res["intent"] == "open_application"

    conv_res = recognizer.recognize_intent("What is WhatsApp?")
    assert conv_res["intent"] == "general_conversation"

    in_app_res = recognizer.recognize_intent("Type hello into Notepad")
    assert in_app_res["intent"] == "keyboard_automation"

    win_res = recognizer.recognize_intent("Minimize Notepad")
    assert win_res["intent"] == "window_management"


def test_planner_resolves_open_watch_up_to_whatsapp(tmp_path: Path) -> None:
    svc = _build_mock_discovery(tmp_path)
    planner = AgentPlanner()
    with patch("backend.agent.planner.get_shared_discovery_service", return_value=svc):
        plan = planner.create_plan("Open, watch up.", intent="open_application")

    assert len(plan["steps"]) == 1
    assert plan["steps"][0]["action"] == "launch_application"
    assert plan["steps"][0]["parameters"]["application_name"] == "WhatsApp"


def test_planner_asks_clarification_on_low_confidence_and_ambiguity(tmp_path: Path) -> None:
    svc = _build_mock_discovery(tmp_path)
    # Add two genuinely ambiguous entries sharing an alias
    svc._add_or_merge_entry(
        DiscoveredApplication(
            app_id="python-311",
            name="python 3.11",
            display_name="Python 3.11",
            source="start_menu",
            launch_method="executable",
            executable_path=r"C:\Python311\python.exe",
            aliases=["python"],
        )
    )
    svc._add_or_merge_entry(
        DiscoveredApplication(
            app_id="python-312",
            name="python 3.12",
            display_name="Python 3.12",
            source="start_menu",
            launch_method="executable",
            executable_path=r"C:\Python312\python.exe",
            aliases=["python"],
        )
    )
    planner = AgentPlanner()

    with patch("backend.agent.planner.get_shared_discovery_service", return_value=svc):
        ambig_plan = planner.create_plan("Open python", intent="open_application")

    assert ambig_plan["steps"][0]["action"] == "ask_clarification"
    assert "Python 3.11" in ambig_plan["response"]

    # Low-confidence query must never launch an unrelated application
    from backend.automation.application_launcher import ApplicationLauncher, ApplicationNotFoundError
    launcher = ApplicationLauncher(discovery_service=svc)
    with pytest.raises(ApplicationNotFoundError):
        launcher.launch("quantum banana app")


# ==============================================================================
# ISSUE 2 — Authoritative Version & Stale Bundled VERSION Protection
# ==============================================================================

def test_version_consistency_check_passes() -> None:
    errors = verify_version_consistency()
    assert errors == [], f"Version consistency errors: {errors}"


def test_stale_bundled_version_cannot_override_compiled_version_in_frozen_mode(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "_internal"
    exe_dir = tmp_path
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "VERSION").write_text("0.5.0\n", encoding="utf-8")
    (exe_dir / "VERSION").write_text("0.5.0\n", encoding="utf-8")

    with (
        patch("backend.version.is_frozen", return_value=True),
        patch("backend.version.get_bundle_dir", return_value=bundle_dir),
        patch("backend.version.get_executable_dir", return_value=exe_dir),
    ):
        assert get_app_version() == DEFAULT_APP_VERSION
