"""
VoxPilot - File Manager Subsystem Unit and Security Tests
Tests all file and folder operations, path validation, directory containment,
traversal prevention, destructive confirmation guardrails, search limits, and tool interfaces.
"""

from __future__ import annotations

import os
from pathlib import Path
import pytest

from backend.automation.file_manager import (
    FileManager,
    FileInfo,
    FileManagerError,
    PathNotAllowedError,
    PathTraversalError,
    PathNotFoundError,
    FileExistsConflictError,
    ConfirmationRequiredError,
    DirectoryNotEmptyError,
    InvalidOperationError,
)
from backend.tools.files import (
    ListDirectoryInput,
    SearchFilesInput,
    GetFileMetadataInput,
    CreateFolderInput,
    CreateFileInput,
    RenameItemInput,
    CopyItemInput,
    MoveItemInput,
    DeleteFileInput,
    DeleteFolderInput,
    list_directory_tool,
    search_files_tool,
    get_file_metadata_tool,
    create_folder_tool,
    create_file_tool,
    rename_item_tool,
    copy_item_tool,
    move_item_tool,
    delete_file_tool,
    delete_folder_tool,
)
from backend.automation.confirmation_manager import get_shared_confirmation_manager


@pytest.fixture
def test_workspace(tmp_path: Path) -> Path:
    """Fixture providing an isolated temporary approved workspace directory."""
    workspace = tmp_path / "approved_workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


@pytest.fixture
def mock_project_root(tmp_path: Path) -> Path:
    """Fixture providing an isolated mock project root to test project-protection guardrails."""
    proj = tmp_path / "mock_voxpilot_project"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "PROJECT_MASTER_PROMPT.md").write_text("charter", encoding="utf-8")
    return proj


@pytest.fixture
def file_manager(test_workspace: Path, mock_project_root: Path) -> FileManager:
    """Fixture creating a FileManager bounded strictly to the temporary workspace."""
    return FileManager(
        approved_directories=[test_workspace],
        project_root=mock_project_root,
    )


# ==============================================================================
# Initialization & Path Validation Tests
# ==============================================================================

def test_file_manager_initialization(file_manager: FileManager, test_workspace: Path, mock_project_root: Path):
    """Verify FileManager initializes with canonical approved directories and project root."""
    assert test_workspace.resolve() in file_manager.approved_directories
    assert file_manager.project_root == mock_project_root.resolve()


def test_validate_path_allowed_file(file_manager: FileManager, test_workspace: Path):
    """Verify an existing file inside the approved directory resolves cleanly."""
    f = test_workspace / "sample.txt"
    f.write_text("hello", encoding="utf-8")
    resolved = file_manager.validate_path(f, must_exist=True)
    assert resolved == f.resolve()


def test_validate_path_nonexistent_raises_not_found(file_manager: FileManager, test_workspace: Path):
    """Verify requesting existence check on non-existent path raises PathNotFoundError."""
    f = test_workspace / "ghost.txt"
    with pytest.raises(PathNotFoundError):
        file_manager.validate_path(f, must_exist=True)


def test_validate_path_outside_approved_directory_rejected(file_manager: FileManager, tmp_path: Path):
    """Verify paths outside approved directories are rejected."""
    outside_dir = tmp_path / "outside_unapproved"
    outside_dir.mkdir(parents=True, exist_ok=True)
    outside_file = outside_dir / "secret.txt"
    outside_file.write_text("secret", encoding="utf-8")

    with pytest.raises(PathNotAllowedError):
        file_manager.validate_path(outside_file, must_exist=True)


def test_validate_path_directory_traversal_escape_rejected(file_manager: FileManager, test_workspace: Path):
    """Verify directory traversal using ../ to escape approved root is rejected."""
    traversal_path = str(test_workspace / ".." / "escaped.txt")
    with pytest.raises(PathNotAllowedError):
        file_manager.validate_path(traversal_path, must_exist=False)


def test_validate_path_unc_and_device_paths_rejected(file_manager: FileManager):
    """Verify UNC and device paths are immediately rejected."""
    with pytest.raises(PathTraversalError):
        file_manager.validate_path("\\\\server\\share\\file.txt", must_exist=False)
    with pytest.raises(PathTraversalError):
        file_manager.validate_path("//server/share/file.txt", must_exist=False)
    with pytest.raises(PathTraversalError):
        file_manager.validate_path("\\\\.\\PhysicalDrive0", must_exist=False)
    with pytest.raises(PathTraversalError):
        file_manager.validate_path("\\\\?\\C:\\Windows", must_exist=False)


def test_validate_path_alternate_data_streams_rejected(file_manager: FileManager, test_workspace: Path):
    """Verify NTFS alternate data stream specifiers are rejected."""
    stream_path = str(test_workspace / "document.txt:hidden_stream")
    with pytest.raises(PathTraversalError):
        file_manager.validate_path(stream_path, must_exist=False)


def test_validate_path_null_bytes_rejected(file_manager: FileManager, test_workspace: Path):
    """Verify paths with null bytes are rejected."""
    with pytest.raises(PathTraversalError):
        file_manager.validate_path(str(test_workspace / "doc\0.txt"), must_exist=False)


def test_validate_path_project_root_protection(file_manager: FileManager, mock_project_root: Path):
    """Verify accessing or altering files inside the VoxPilot project root is strictly blocked."""
    proj_file = mock_project_root / "PROJECT_MASTER_PROMPT.md"
    with pytest.raises(PathNotAllowedError):
        file_manager.validate_path(proj_file, must_exist=True)


def test_validate_path_user_profile_root_rejected(file_manager: FileManager):
    """Verify user home directory root cannot be directly operated on."""
    with pytest.raises(PathNotAllowedError):
        file_manager.validate_path(Path.home(), must_exist=False)


# ==============================================================================
# 1. List Files and Folders Tests
# ==============================================================================

def test_list_directory_empty(file_manager: FileManager, test_workspace: Path):
    """Verify listing an empty approved directory returns empty list."""
    items = file_manager.list_directory(test_workspace)
    assert items == []


def test_list_directory_populated(file_manager: FileManager, test_workspace: Path):
    """Verify listing populated directory returns sorted FileInfo objects with metadata."""
    f1 = test_workspace / "notes.txt"
    f1.write_text("sample content", encoding="utf-8")
    sub = test_workspace / "archive"
    sub.mkdir()
    f2 = sub / "doc.pdf"
    f2.write_text("pdf", encoding="utf-8")

    items = file_manager.list_directory(test_workspace)
    assert len(items) == 2
    # Directories first
    assert items[0].name == "archive"
    assert items[0].is_directory is True
    assert items[1].name == "notes.txt"
    assert items[1].is_directory is False
    assert items[1].size_bytes == len("sample content")
    assert items[1].extension == ".txt"


def test_list_directory_hidden_files_filter(file_manager: FileManager, test_workspace: Path):
    """Verify hidden files (starting with dot) are omitted unless show_hidden=True."""
    hidden = test_workspace / ".secret"
    hidden.write_text("hidden", encoding="utf-8")
    visible = test_workspace / "visible.txt"
    visible.write_text("visible", encoding="utf-8")

    without_hidden = file_manager.list_directory(test_workspace, show_hidden=False)
    assert len(without_hidden) == 1
    assert without_hidden[0].name == "visible.txt"

    with_hidden = file_manager.list_directory(test_workspace, show_hidden=True)
    assert len(with_hidden) == 2
    names = {item.name for item in with_hidden}
    assert ".secret" in names
    assert "visible.txt" in names


# ==============================================================================
# 2 & 3. Search Files Tests
# ==============================================================================

def test_search_files_by_name(file_manager: FileManager, test_workspace: Path):
    """Verify searching files by substring in filename."""
    (test_workspace / "quarterly_report_2026.txt").write_text("data", encoding="utf-8")
    (test_workspace / "annual_report.docx").write_text("data", encoding="utf-8")
    (test_workspace / "grocery_list.txt").write_text("eggs", encoding="utf-8")

    matches = file_manager.search_files(test_workspace, query="report")
    assert len(matches) == 2
    names = {m.name for m in matches}
    assert "quarterly_report_2026.txt" in names
    assert "annual_report.docx" in names


def test_search_files_by_extension(file_manager: FileManager, test_workspace: Path):
    """Verify searching files by file extension."""
    (test_workspace / "file1.txt").write_text("1", encoding="utf-8")
    (test_workspace / "file2.TXT").write_text("2", encoding="utf-8")  # Case insensitive
    (test_workspace / "photo.jpg").write_text("img", encoding="utf-8")

    matches = file_manager.search_files(test_workspace, extension=".txt")
    assert len(matches) == 2
    names = {m.name for m in matches}
    assert "file1.txt" in names
    assert "file2.TXT" in names


def test_search_files_depth_and_result_limits(file_manager: FileManager, test_workspace: Path):
    """Verify search respects depth and max_results limits."""
    # Create nested folders
    current = test_workspace
    for i in range(1, 8):
        current = current / f"level_{i}"
        current.mkdir()
        (current / f"item_{i}.txt").write_text(f"level {i}", encoding="utf-8")

    # Search with max_depth=3
    matches_depth_3 = file_manager.search_files(test_workspace, query="item", max_depth=3)
    assert len(matches_depth_3) == 3

    # Search with max_results=2
    matches_limit = file_manager.search_files(test_workspace, query="item", max_depth=8, max_results=2)
    assert len(matches_limit) == 2


# ==============================================================================
# 4. Get Metadata Tests
# ==============================================================================

def test_get_metadata_success(file_manager: FileManager, test_workspace: Path):
    """Verify get_metadata returns accurate FileInfo dataclass."""
    f = test_workspace / "sample_doc.md"
    f.write_text("# Markdown Heading", encoding="utf-8")

    meta = file_manager.get_metadata(f)
    assert meta.name == "sample_doc.md"
    assert meta.is_directory is False
    assert meta.size_bytes == len("# Markdown Heading")
    assert meta.extension == ".md"
    assert meta.modified_at != ""
    assert meta.created_at != ""


# ==============================================================================
# 5. Create Folder Tests
# ==============================================================================

def test_create_folder_success(file_manager: FileManager, test_workspace: Path):
    """Verify creating a new folder in an approved directory succeeds."""
    new_dir = test_workspace / "projects" / "ai_agent"
    res = file_manager.create_folder(new_dir)
    assert res["success"] is True
    assert new_dir.exists()
    assert new_dir.is_dir()


def test_create_folder_already_exists_fails(file_manager: FileManager, test_workspace: Path):
    """Verify creating an already existing folder raises FileExistsConflictError."""
    existing = test_workspace / "existing_folder"
    existing.mkdir()
    with pytest.raises(FileExistsConflictError):
        file_manager.create_folder(existing)


# ==============================================================================
# 6. Create Empty File Tests
# ==============================================================================

def test_create_file_success(file_manager: FileManager, test_workspace: Path):
    """Verify creating an empty file in an approved directory succeeds."""
    new_file = test_workspace / "new_document.txt"
    res = file_manager.create_file(new_file)
    assert res["success"] is True
    assert new_file.exists()
    assert new_file.stat().st_size == 0


def test_create_file_overwrite_without_confirmation_rejected(file_manager: FileManager, test_workspace: Path):
    """Verify creating a file over an existing file without confirmation raises ConfirmationRequiredError."""
    existing = test_workspace / "existing.txt"
    existing.write_text("original content", encoding="utf-8")

    with pytest.raises(ConfirmationRequiredError) as exc_info:
        file_manager.create_file(existing, confirm_overwrite=False)
    assert exc_info.value.requires_confirmation is True
    assert existing.read_text(encoding="utf-8") == "original content"


def test_create_file_overwrite_with_confirmation_succeeds(file_manager: FileManager, test_workspace: Path):
    """Verify creating a file over an existing file with confirmation succeeds."""
    existing = test_workspace / "existing.txt"
    existing.write_text("original content", encoding="utf-8")

    res = file_manager.create_file(existing, confirm_overwrite=True)
    assert res["success"] is True
    assert existing.exists()


# ==============================================================================
# 7 & 8. Rename File and Folder Tests
# ==============================================================================

def test_rename_file_success(file_manager: FileManager, test_workspace: Path):
    """Verify renaming a file succeeds."""
    old_file = test_workspace / "old_name.txt"
    old_file.write_text("data", encoding="utf-8")

    res = file_manager.rename_file(old_file, "new_name.txt")
    assert res["success"] is True
    assert not old_file.exists()
    assert (test_workspace / "new_name.txt").exists()


def test_rename_file_path_separators_rejected(file_manager: FileManager, test_workspace: Path):
    """Verify new name containing path separators or traversal is rejected."""
    f = test_workspace / "sample.txt"
    f.write_text("data", encoding="utf-8")

    with pytest.raises(PathTraversalError):
        file_manager.rename_file(f, "../escaped.txt")
    with pytest.raises(PathTraversalError):
        file_manager.rename_file(f, "sub/new_name.txt")


def test_rename_folder_success(file_manager: FileManager, test_workspace: Path):
    """Verify renaming a directory succeeds."""
    old_dir = test_workspace / "old_dir"
    old_dir.mkdir()
    (old_dir / "child.txt").write_text("child data", encoding="utf-8")

    res = file_manager.rename_folder(old_dir, "new_dir")
    assert res["success"] is True
    assert not old_dir.exists()
    assert (test_workspace / "new_dir" / "child.txt").exists()


def test_rename_approved_root_rejected(file_manager: FileManager, test_workspace: Path):
    """Verify approved root directory itself cannot be renamed."""
    with pytest.raises(PathNotAllowedError):
        file_manager.rename_folder(test_workspace, "renamed_root")


# ==============================================================================
# 9 & 10. Copy File and Folder Tests
# ==============================================================================

def test_copy_file_success(file_manager: FileManager, test_workspace: Path):
    """Verify copying a file within approved directory."""
    src = test_workspace / "source.txt"
    src.write_text("content", encoding="utf-8")
    dst = test_workspace / "copy.txt"

    res = file_manager.copy_file(src, dst)
    assert res["success"] is True
    assert src.exists()
    assert dst.exists()
    assert dst.read_text(encoding="utf-8") == "content"


def test_copy_file_overwrite_protection(file_manager: FileManager, test_workspace: Path):
    """Verify copying over existing file without confirmation raises ConfirmationRequiredError."""
    src = test_workspace / "source.txt"
    src.write_text("new content", encoding="utf-8")
    dst = test_workspace / "target.txt"
    dst.write_text("existing content", encoding="utf-8")

    with pytest.raises(ConfirmationRequiredError):
        file_manager.copy_file(src, dst, confirm_overwrite=False)

    # With confirmation
    res = file_manager.copy_file(src, dst, confirm_overwrite=True)
    assert res["success"] is True
    assert dst.read_text(encoding="utf-8") == "new content"


def test_copy_folder_success(file_manager: FileManager, test_workspace: Path):
    """Verify copying a folder recursively."""
    src_dir = test_workspace / "src_dir"
    src_dir.mkdir()
    (src_dir / "file.txt").write_text("hello", encoding="utf-8")
    dst_dir = test_workspace / "dst_dir"

    res = file_manager.copy_folder(src_dir, dst_dir)
    assert res["success"] is True
    assert (dst_dir / "file.txt").read_text(encoding="utf-8") == "hello"


def test_copy_folder_into_self_rejected(file_manager: FileManager, test_workspace: Path):
    """Verify copying a folder into its own subdirectory is rejected."""
    src_dir = test_workspace / "parent_dir"
    src_dir.mkdir()
    dst_dir = src_dir / "nested_copy"

    with pytest.raises(InvalidOperationError):
        file_manager.copy_folder(src_dir, dst_dir)


# ==============================================================================
# 11 & 12. Move File and Folder Tests
# ==============================================================================

def test_move_file_success(file_manager: FileManager, test_workspace: Path):
    """Verify moving a file."""
    src = test_workspace / "movable.txt"
    src.write_text("move me", encoding="utf-8")
    dst = test_workspace / "moved.txt"

    res = file_manager.move_file(src, dst)
    assert res["success"] is True
    assert not src.exists()
    assert dst.exists()
    assert dst.read_text(encoding="utf-8") == "move me"


def test_move_folder_success(file_manager: FileManager, test_workspace: Path):
    """Verify moving a folder."""
    src_dir = test_workspace / "source_folder"
    src_dir.mkdir()
    (src_dir / "item.txt").write_text("item", encoding="utf-8")
    dst_dir = test_workspace / "destination_folder"

    res = file_manager.move_folder(src_dir, dst_dir)
    assert res["success"] is True
    assert not src_dir.exists()
    assert (dst_dir / "item.txt").exists()


# ==============================================================================
# 13 & 14. Delete File and Folder Destructive Safety Tests
# ==============================================================================

def test_delete_file_without_confirmation_rejected(file_manager: FileManager, test_workspace: Path):
    """Verify deleting a file without confirmation is blocked fail-closed."""
    target = test_workspace / "critical_data.txt"
    target.write_text("important", encoding="utf-8")

    with pytest.raises(ConfirmationRequiredError) as exc_info:
        file_manager.delete_file(target, confirmed=False)

    assert exc_info.value.requires_confirmation is True
    assert target.exists()  # File was NOT deleted


def test_delete_file_with_confirmation_succeeds(file_manager: FileManager, test_workspace: Path):
    """Verify deleting a file with confirmed=True deletes the file."""
    target = test_workspace / "disposable.txt"
    target.write_text("bye", encoding="utf-8")

    res = file_manager.delete_file(target, confirmed=True)
    assert res["success"] is True
    assert not target.exists()


def test_delete_folder_without_confirmation_rejected(file_manager: FileManager, test_workspace: Path):
    """Verify deleting a folder without confirmation is blocked."""
    folder = test_workspace / "target_folder"
    folder.mkdir()

    with pytest.raises(ConfirmationRequiredError):
        file_manager.delete_folder(folder, confirmed=False)
    assert folder.exists()


def test_delete_non_empty_folder_without_recursive_flag_rejected(file_manager: FileManager, test_workspace: Path):
    """Verify deleting a non-empty folder without recursive=True raises DirectoryNotEmptyError."""
    folder = test_workspace / "non_empty"
    folder.mkdir()
    (folder / "file.txt").write_text("item", encoding="utf-8")

    with pytest.raises(DirectoryNotEmptyError):
        file_manager.delete_folder(folder, confirmed=True, recursive=False)
    assert folder.exists()


def test_delete_non_empty_folder_with_confirmation_and_recursive_succeeds(
    file_manager: FileManager,
    test_workspace: Path,
):
    """Verify deleting a non-empty folder with both confirmed=True and recursive=True succeeds."""
    folder = test_workspace / "non_empty_target"
    folder.mkdir()
    (folder / "file.txt").write_text("item", encoding="utf-8")

    res = file_manager.delete_folder(folder, confirmed=True, recursive=True)
    assert res["success"] is True
    assert not folder.exists()


def test_delete_approved_root_rejected(file_manager: FileManager, test_workspace: Path):
    """Verify approved root directory itself cannot be deleted under any circumstances."""
    with pytest.raises(PathNotAllowedError):
        file_manager.delete_folder(test_workspace, confirmed=True, recursive=True)
    assert test_workspace.exists()


# ==============================================================================
# Pydantic Tools Layer Tests
# ==============================================================================

def test_list_directory_tool(file_manager: FileManager, test_workspace: Path):
    """Verify list_directory_tool returns structured FileActionResult."""
    (test_workspace / "file_a.txt").write_text("A", encoding="utf-8")
    data = ListDirectoryInput(directory_path=str(test_workspace))
    res = list_directory_tool(data, manager=file_manager)

    assert res.success is True
    assert res.operation == "list_directory"
    assert res.affected_items == 1
    assert res.status == "success"
    assert res.items is not None
    assert res.items[0]["name"] == "file_a.txt"


def test_search_files_tool(file_manager: FileManager, test_workspace: Path):
    """Verify search_files_tool returns structured result."""
    (test_workspace / "searchable_note.md").write_text("note", encoding="utf-8")
    data = SearchFilesInput(directory_path=str(test_workspace), query="searchable")
    res = search_files_tool(data, manager=file_manager)

    assert res.success is True
    assert res.affected_items == 1
    assert res.items is not None
    assert res.items[0]["name"] == "searchable_note.md"


def test_delete_file_tool_unconfirmed_preview(file_manager: FileManager, test_workspace: Path):
    """Verify delete_file_tool with confirmed=False returns confirmation_required status."""
    f = test_workspace / "to_delete.txt"
    f.write_text("content", encoding="utf-8")

    data = DeleteFileInput(file_path=str(f), confirmed=False)
    res = delete_file_tool(data, manager=file_manager)

    assert res.success is False
    assert res.requires_confirmation is True
    assert res.status == "confirmation_required"
    assert "explicit confirmation" in res.message.lower()
    assert f.exists()  # File still exists!


def test_delete_file_tool_confirmed_execution(file_manager: FileManager, test_workspace: Path):
    """Verify delete_file_tool with approved confirmation_token executes successfully."""
    f = test_workspace / "to_delete.txt"
    f.write_text("content", encoding="utf-8")

    cm = get_shared_confirmation_manager()
    pending = cm.create_confirmation("delete_file", {"file_path": str(f.resolve())})
    cm.approve_confirmation(pending.token)

    data = DeleteFileInput(file_path=str(f), confirmed=True, confirmation_token=pending.token)
    res = delete_file_tool(data, manager=file_manager)

    assert res.success is True
    assert res.status == "success"
    assert not f.exists()


def test_delete_file_tool_confirmed_flag_alone_does_not_bypass(file_manager: FileManager, test_workspace: Path):
    """Verify passing confirmed=True without an approved token returns confirmation_required."""
    f = test_workspace / "must_not_delete.txt"
    f.write_text("critical data", encoding="utf-8")

    # LLM attempts to pass confirmed=True without an approved token
    data = DeleteFileInput(file_path=str(f), confirmed=True, confirmation_token=None)
    res = delete_file_tool(data, manager=file_manager)

    assert res.success is False
    assert res.requires_confirmation is True
    assert res.status == "confirmation_required"
    assert res.confirmation_token is not None
    assert f.exists()  # Crucial: file was NOT deleted!


def test_delete_folder_tool_confirmed_execution(file_manager: FileManager, test_workspace: Path):
    """Verify delete_folder_tool with approved confirmation_token executes successfully."""
    folder = test_workspace / "del_approved"
    folder.mkdir()

    cm = get_shared_confirmation_manager()
    pending = cm.create_confirmation("delete_folder", {"folder_path": str(folder.resolve()), "recursive": False})
    cm.approve_confirmation(pending.token)

    data = DeleteFolderInput(folder_path=str(folder), confirmed=True, confirmation_token=pending.token)
    res = delete_folder_tool(data, manager=file_manager)

    assert res.success is True
    assert res.status == "success"
    assert not folder.exists()


def test_delete_folder_tool_unconfirmed_preview(file_manager: FileManager, test_workspace: Path):
    """Verify delete_folder_tool returns confirmation_required when unconfirmed."""
    folder = test_workspace / "del_folder"
    folder.mkdir()

    data = DeleteFolderInput(folder_path=str(folder), confirmed=False)
    res = delete_folder_tool(data, manager=file_manager)

    assert res.success is False
    assert res.requires_confirmation is True
    assert res.status == "confirmation_required"
    assert folder.exists()


def test_tools_path_not_allowed_status(file_manager: FileManager, tmp_path: Path):
    """Verify tools handle unapproved paths gracefully by returning not_allowed status."""
    unapproved = tmp_path / "forbidden_folder"
    unapproved.mkdir()

    data = ListDirectoryInput(directory_path=str(unapproved))
    res = list_directory_tool(data, manager=file_manager)

    assert res.success is False
    assert res.status == "not_allowed"
    assert "outside approved directories" in res.message

