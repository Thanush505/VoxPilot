"""Regression tests for natural-language desktop/known-folder file creation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from backend.agent.intent import IntentRecognizer
from backend.agent.planner import AgentPlanner
from backend.automation.file_manager import (
    FileManager,
    generate_unique_filename,
    normalize_folder_alias,
    resolve_known_folder,
)
from backend.tools.files import CreateFileInput, create_file_tool


@pytest.fixture
def isolated_known_folders(tmp_path: Path):
    """Provide isolated temporary Desktop/Downloads/Documents/Pictures directories."""
    folders = {
        "desktop": tmp_path / "Desktop",
        "downloads": tmp_path / "Downloads",
        "documents": tmp_path / "Documents",
        "pictures": tmp_path / "Pictures",
    }
    for folder in folders.values():
        folder.mkdir(parents=True, exist_ok=True)
    return folders


def _mock_known_folder_lookup(folders: dict[str, Path]):
    def _lookup(folder_key: str):
        return folders.get(folder_key)

    return _lookup


class TestKnownFolderResolution:
    """Verify natural-language alias normalization and Windows known-folder lookup."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("desktop", "desktop"),
            ("my desktop", "desktop"),
            ("on desktop", "desktop"),
            ("on my desktop", "desktop"),
            ("in the desktop folder", "desktop"),
            ("downloads", "downloads"),
            ("my downloads", "downloads"),
            ("documents", "documents"),
            ("my documents", "documents"),
            ("docs", "documents"),
            ("pictures", "pictures"),
            ("my pictures", "pictures"),
            ("unknown_place", None),
        ],
    )
    def test_normalize_folder_alias(self, raw: str, expected: str | None):
        assert normalize_folder_alias(raw) == expected

    def test_resolve_known_folder_uses_windows_known_folder_path(self, isolated_known_folders):
        with patch(
            "backend.automation.file_manager._get_windows_known_folder_path",
            side_effect=_mock_known_folder_lookup(isolated_known_folders),
        ):
            assert resolve_known_folder("my desktop") == isolated_known_folders["desktop"]
            assert resolve_known_folder("downloads") == isolated_known_folders["downloads"]
            assert resolve_known_folder("my documents") == isolated_known_folders["documents"]
            assert resolve_known_folder("pictures") == isolated_known_folders["pictures"]
            assert resolve_known_folder("nonexistent_vault") is None


class TestDesktopFileCreationPlanningAndExecution:
    """Regression tests for intent parsing, planning, and safe file creation."""

    @pytest.mark.parametrize(
        "query,folder_key,expected_filename",
        [
            ("Create a Notepad file in desktop.", "desktop", "New Text Document.txt"),
            ("Create a text file on my desktop.", "desktop", "New Text Document.txt"),
            ("Create a Python file in downloads.", "downloads", "new_file.py"),
            ("Create an HTML file on desktop.", "desktop", "index.html"),
            ("Create a file in documents.", "documents", "New Text Document.txt"),
            ("Create a markdown file in my pictures.", "pictures", "notes.md"),
        ],
    )
    def test_natural_language_file_creation_plan_and_execute(
        self,
        isolated_known_folders,
        query: str,
        folder_key: str,
        expected_filename: str,
    ):
        recognizer = IntentRecognizer()
        intent_res = recognizer.recognize_intent(query)
        assert intent_res["intent"] == "file_management"

        planner = AgentPlanner()
        with patch(
            "backend.automation.file_manager._get_windows_known_folder_path",
            side_effect=_mock_known_folder_lookup(isolated_known_folders),
        ):
            plan = planner.create_plan(query, intent=intent_res["intent"])

        steps = plan["steps"]
        assert len(steps) == 1
        assert steps[0]["action"] == "create_file"
        assert steps[0]["action"] != "ask_clarification"

        expected_path = isolated_known_folders[folder_key] / expected_filename
        params = steps[0]["parameters"]
        assert Path(params["file_path"]) == expected_path
        assert params["content"] == ""
        assert params.get("auto_rename_on_conflict") is True

        mgr = FileManager(
            approved_directories=list(isolated_known_folders.values()),
            project_root=isolated_known_folders["desktop"].parent / "Proj",
        )
        tool_res = create_file_tool(CreateFileInput(**params), manager=mgr)
        assert tool_res.success is True
        assert tool_res.status == "success"
        assert Path(tool_res.destination_path) == expected_path
        assert str(expected_path) in tool_res.message
        assert expected_path.is_file()
        assert expected_path.read_text(encoding="utf-8") == ""

    def test_existing_default_filename_is_not_overwritten(self, isolated_known_folders):
        desktop = isolated_known_folders["desktop"]
        existing = desktop / "New Text Document.txt"
        existing.write_text("keep original content", encoding="utf-8")

        planner = AgentPlanner()
        with patch(
            "backend.automation.file_manager._get_windows_known_folder_path",
            side_effect=_mock_known_folder_lookup(isolated_known_folders),
        ):
            plan = planner.create_plan("Create a Notepad file in desktop.", intent="file_management")

        step = plan["steps"][0]
        assert step["action"] == "create_file"
        planned_path = Path(step["parameters"]["file_path"])
        assert planned_path == desktop / "New Text Document (2).txt"

        mgr = FileManager(
            approved_directories=list(isolated_known_folders.values()),
            project_root=desktop.parent / "Proj",
        )
        tool_res = create_file_tool(CreateFileInput(**step["parameters"]), manager=mgr)
        assert tool_res.success is True
        assert Path(tool_res.destination_path) == desktop / "New Text Document (2).txt"
        assert (desktop / "New Text Document (2).txt").is_file()
        assert existing.read_text(encoding="utf-8") == "keep original content"

    def test_file_manager_auto_rename_on_conflict_race_guard(self, isolated_known_folders):
        desktop = isolated_known_folders["desktop"]
        first = desktop / "New Text Document.txt"
        first.write_text("existing", encoding="utf-8")

        mgr = FileManager(
            approved_directories=list(isolated_known_folders.values()),
            project_root=desktop.parent / "Proj",
        )
        res = mgr.create_file(first, content="", auto_rename_on_conflict=True)
        assert res["success"] is True
        assert Path(res["path"]) == desktop / "New Text Document (2).txt"
        assert first.read_text(encoding="utf-8") == "existing"

    def test_unknown_destination_requests_clarification(self, isolated_known_folders):
        recognizer = IntentRecognizer()
        query = "Create a file in my secret_unknown_vault"
        intent_res = recognizer.recognize_intent(query)
        assert intent_res["intent"] == "file_management"

        planner = AgentPlanner()
        with patch(
            "backend.automation.file_manager._get_windows_known_folder_path",
            side_effect=_mock_known_folder_lookup(isolated_known_folders),
        ):
            plan = planner.create_plan(query, intent=intent_res["intent"])

        assert plan["steps"][0]["action"] == "ask_clarification"

    def test_missing_destination_and_filename_requests_clarification(self, isolated_known_folders):
        planner = AgentPlanner()
        with patch(
            "backend.automation.file_manager._get_windows_known_folder_path",
            side_effect=_mock_known_folder_lookup(isolated_known_folders),
        ):
            plan = planner.create_plan("Create a file", intent="file_management")

        assert plan["steps"][0]["action"] == "ask_clarification"

    def test_explicit_filename_and_destination_creates_and_protects_existing(self, isolated_known_folders):
        desktop = isolated_known_folders["desktop"]
        planner = AgentPlanner()
        with patch(
            "backend.automation.file_manager._get_windows_known_folder_path",
            side_effect=_mock_known_folder_lookup(isolated_known_folders),
        ):
            plan = planner.create_plan(
                "Create a file named report.txt in my desktop.",
                intent="file_management",
            )

        step = plan["steps"][0]
        assert step["action"] == "create_file"
        assert Path(step["parameters"]["file_path"]) == desktop / "report.txt"
        assert "auto_rename_on_conflict" not in step["parameters"]

        mgr = FileManager(
            approved_directories=list(isolated_known_folders.values()),
            project_root=desktop.parent / "Proj",
        )
        res1 = create_file_tool(CreateFileInput(**step["parameters"]), manager=mgr)
        assert res1.success is True
        assert (desktop / "report.txt").is_file()

        # Second creation with explicit existing filename must ask confirmation, not overwrite
        res2 = create_file_tool(CreateFileInput(**step["parameters"]), manager=mgr)
        assert res2.success is False
        assert res2.requires_confirmation is True
        assert res2.status == "confirmation_required"

    def test_generate_unique_filename_increments_cleanly(self, tmp_path: Path):
        (tmp_path / "index.html").write_text("", encoding="utf-8")
        (tmp_path / "index (2).html").write_text("", encoding="utf-8")
        assert generate_unique_filename(tmp_path, "index.html") == tmp_path / "index (3).html"


class TestNaturalLanguageFolderListing:
    """Regression tests for natural-language folder discovery and directory listing."""

    def test_list_files_in_major_project_folder_with_spaces(self, isolated_known_folders):
        from backend.tools.files import ListDirectoryInput, list_directory_tool

        major_dir = isolated_known_folders["desktop"] / "Major Project"
        major_dir.mkdir()
        (major_dir / "README.md").write_text("# Major Project", encoding="utf-8")
        (major_dir / "main.py").write_text("print('ok')", encoding="utf-8")

        query = "List files in my Major Project folder."
        intent_res = IntentRecognizer().recognize_intent(query)
        assert intent_res["intent"] == "file_management"

        approved = list(isolated_known_folders.values())
        planner = AgentPlanner()
        with (
            patch(
                "backend.automation.file_manager._get_windows_known_folder_path",
                side_effect=_mock_known_folder_lookup(isolated_known_folders),
            ),
            patch(
                "backend.automation.file_manager.get_default_approved_directories",
                return_value=approved,
            ),
        ):
            plan = planner.create_plan(query, intent=intent_res["intent"])

        steps = plan["steps"]
        assert len(steps) == 1
        assert steps[0]["action"] == "list_directory"
        assert Path(steps[0]["parameters"]["directory_path"]) == major_dir.resolve()

        mgr = FileManager(
            approved_directories=approved,
            project_root=isolated_known_folders["desktop"].parent / "Proj",
        )
        res = list_directory_tool(ListDirectoryInput(**steps[0]["parameters"]), manager=mgr)
        assert res.success is True
        assert res.affected_items == 2
        names = [item["name"] for item in res.items]
        assert "README.md" in names
        assert "main.py" in names
        assert all("size_bytes" in item and "modified_at" in item for item in res.items)

    @pytest.mark.parametrize(
        "actual_dirname,query",
        [
            ("Major_Project", "List files in my Major Project folder."),
            ("MAJOR PROJECT", "List files in my major_project folder"),
            ("Major-Project", "Show files in Major Project folder."),
        ],
    )
    def test_case_and_separator_insensitive_folder_matching(
        self, isolated_known_folders, actual_dirname: str, query: str
    ):
        target_dir = isolated_known_folders["documents"] / actual_dirname
        target_dir.mkdir()
        (target_dir / "notes.txt").write_text("hi", encoding="utf-8")

        approved = list(isolated_known_folders.values())
        planner = AgentPlanner()
        with (
            patch(
                "backend.automation.file_manager._get_windows_known_folder_path",
                side_effect=_mock_known_folder_lookup(isolated_known_folders),
            ),
            patch(
                "backend.automation.file_manager.get_default_approved_directories",
                return_value=approved,
            ),
        ):
            plan = planner.create_plan(query, intent="file_management")

        assert plan["steps"][0]["action"] == "list_directory"
        assert Path(plan["steps"][0]["parameters"]["directory_path"]) == target_dir.resolve()

    def test_multiple_matching_directories_requests_clarification(self, isolated_known_folders):
        d1 = isolated_known_folders["desktop"] / "Major Project"
        d2 = isolated_known_folders["documents"] / "Major_Project"
        d1.mkdir()
        d2.mkdir()

        approved = list(isolated_known_folders.values())
        planner = AgentPlanner()
        with (
            patch(
                "backend.automation.file_manager._get_windows_known_folder_path",
                side_effect=_mock_known_folder_lookup(isolated_known_folders),
            ),
            patch(
                "backend.automation.file_manager.get_default_approved_directories",
                return_value=approved,
            ),
        ):
            plan = planner.create_plan("List files in my Major Project folder.", intent="file_management")

        step = plan["steps"][0]
        assert step["action"] == "ask_clarification"
        candidates = step["parameters"]["candidates"]
        assert str(d1.resolve()) in candidates
        assert str(d2.resolve()) in candidates
        assert str(d1.resolve()) in plan["response"]
        assert str(d2.resolve()) in plan["response"]

    def test_no_matching_directory_asks_where_folder_is(self, isolated_known_folders):
        approved = list(isolated_known_folders.values())
        planner = AgentPlanner()
        with (
            patch(
                "backend.automation.file_manager._get_windows_known_folder_path",
                side_effect=_mock_known_folder_lookup(isolated_known_folders),
            ),
            patch(
                "backend.automation.file_manager.get_default_approved_directories",
                return_value=approved,
            ),
        ):
            plan = planner.create_plan("List files in my Nonexistent Quantum folder.", intent="file_management")

        assert plan["steps"][0]["action"] == "ask_clarification"
        assert "Nonexistent Quantum" in plan["response"]

    def test_explicit_valid_and_invalid_absolute_paths(self, isolated_known_folders):
        valid_dir = isolated_known_folders["desktop"] / "ExplicitDir"
        valid_dir.mkdir()
        invalid_dir = isolated_known_folders["desktop"] / "MissingExplicitDir"

        approved = list(isolated_known_folders.values())
        planner = AgentPlanner()
        with (
            patch(
                "backend.automation.file_manager._get_windows_known_folder_path",
                side_effect=_mock_known_folder_lookup(isolated_known_folders),
            ),
            patch(
                "backend.automation.file_manager.get_default_approved_directories",
                return_value=approved,
            ),
        ):
            valid_plan = planner.create_plan(f"List files in {valid_dir}", intent="file_management")
            invalid_plan = planner.create_plan(f"List files in {invalid_dir}", intent="file_management")

        assert valid_plan["steps"][0]["action"] == "list_directory"
        assert Path(valid_plan["steps"][0]["parameters"]["directory_path"]) == valid_dir.resolve()
        assert invalid_plan["steps"][0]["action"] == "ask_clarification"

    def test_nested_folder_resolution_inside_documents(self, isolated_known_folders):
        # Create Major Project in both Desktop and Documents; scoping to Documents must disambiguate
        (isolated_known_folders["desktop"] / "Major Project").mkdir()
        docs_major = isolated_known_folders["documents"] / "Major Project"
        docs_major.mkdir()

        approved = list(isolated_known_folders.values())
        planner = AgentPlanner()
        with (
            patch(
                "backend.automation.file_manager._get_windows_known_folder_path",
                side_effect=_mock_known_folder_lookup(isolated_known_folders),
            ),
            patch(
                "backend.automation.file_manager.get_default_approved_directories",
                return_value=approved,
            ),
        ):
            plan = planner.create_plan(
                "List files in Major Project folder inside Documents.",
                intent="file_management",
            )

        assert plan["steps"][0]["action"] == "list_directory"
        assert Path(plan["steps"][0]["parameters"]["directory_path"]) == docs_major.resolve()

    def test_permission_denied_directory_handled_cleanly(self, isolated_known_folders):
        from backend.tools.files import ListDirectoryInput, list_directory_tool

        locked_dir = isolated_known_folders["desktop"] / "Locked"
        locked_dir.mkdir()
        mgr = FileManager(
            approved_directories=list(isolated_known_folders.values()),
            project_root=isolated_known_folders["desktop"].parent / "Proj",
        )
        with patch("backend.automation.file_manager.os.scandir", side_effect=PermissionError("Access denied")):
            res = list_directory_tool(ListDirectoryInput(directory_path=str(locked_dir)), manager=mgr)

        assert res.success is False
        assert res.status == "error"
        assert "Permission denied" in res.message

    def test_empty_directory_handled_gracefully(self, isolated_known_folders):
        from backend.tools.files import ListDirectoryInput, list_directory_tool

        empty_dir = isolated_known_folders["downloads"] / "EmptyProject"
        empty_dir.mkdir()
        mgr = FileManager(
            approved_directories=list(isolated_known_folders.values()),
            project_root=isolated_known_folders["desktop"].parent / "Proj",
        )
        res = list_directory_tool(ListDirectoryInput(directory_path=str(empty_dir)), manager=mgr)
        assert res.success is True
        assert res.status == "success"
        assert res.affected_items == 0
        assert res.items == []
        assert "empty" in res.message.lower()

