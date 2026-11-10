"""
VoxPilot - Safe File and Directory Management Tools
Provides Pydantic models and agent tool functions for safe, allowlisted,
path-validated filesystem interaction with confirmation guardrails.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, field_validator

from backend.automation.file_manager import (
    FileManager,
    FileManagerError,
    PathNotAllowedError,
    PathTraversalError,
    PathNotFoundError,
    FileExistsConflictError,
    ConfirmationRequiredError,
    DirectoryNotEmptyError,
    FileAccessError,
    InvalidOperationError,
)
from backend.automation.confirmation_manager import (
    ConfirmationError,
    MissingUserApprovalError,
    get_shared_confirmation_manager,
)

logger = logging.getLogger(__name__)


# ==============================================================================
# Pydantic Input Schemas
# ==============================================================================

class ListDirectoryInput(BaseModel):
    """Pydantic schema for listing files and folders in an approved directory."""
    directory_path: str = Field(
        ...,
        description="Path of the approved directory to list.",
        min_length=1,
    )
    show_hidden: bool = Field(
        False,
        description="Whether to include hidden files and directories in listing.",
    )


class SearchFilesInput(BaseModel):
    """Pydantic schema for searching files in an approved directory."""
    directory_path: str = Field(
        ...,
        description="Approved root directory path to search within.",
        min_length=1,
    )
    query: Optional[str] = Field(
        None,
        description="Filename substring or search pattern to filter by.",
    )
    extension: Optional[str] = Field(
        None,
        description="File extension to filter by (e.g. '.txt', 'pdf').",
    )
    max_depth: int = Field(
        5,
        description="Maximum directory recursion depth (1 to 10).",
        ge=1,
        le=10,
    )
    max_results: int = Field(
        50,
        description="Maximum number of search results to return (1 to 200).",
        ge=1,
        le=200,
    )


class GetFileMetadataInput(BaseModel):
    """Pydantic schema for getting file or folder metadata."""
    file_path: str = Field(
        ...,
        description="Path of the file or folder to inspect.",
        min_length=1,
    )


class CreateFolderInput(BaseModel):
    """Pydantic schema for creating a new folder in an approved directory."""
    folder_path: str = Field(
        ...,
        description="Path of the new folder to create.",
        min_length=1,
    )


class CreateFileInput(BaseModel):
    """Pydantic schema for creating a new empty file in an approved directory."""
    file_path: str = Field(
        ...,
        description="Path of the new file to create.",
        min_length=1,
    )
    confirm_overwrite: bool = Field(
        False,
        description="Explicit confirmation to overwrite if file already exists.",
    )


class RenameItemInput(BaseModel):
    """Pydantic schema for renaming a file or folder in an approved directory."""
    source_path: str = Field(
        ...,
        description="Path of the existing file or folder to rename.",
        min_length=1,
    )
    new_name: str = Field(
        ...,
        description="New name for the item (name only, without directory path).",
        min_length=1,
    )
    confirm_overwrite: bool = Field(
        False,
        description="Explicit confirmation if destination item already exists.",
    )


class CopyItemInput(BaseModel):
    """Pydantic schema for copying a file or folder within approved directories."""
    source_path: str = Field(
        ...,
        description="Path of the file or folder to copy.",
        min_length=1,
    )
    destination_path: str = Field(
        ...,
        description="Destination path or directory.",
        min_length=1,
    )
    confirm_overwrite: bool = Field(
        False,
        description="Explicit confirmation if destination already exists.",
    )


class MoveItemInput(BaseModel):
    """Pydantic schema for moving a file or folder within approved directories."""
    source_path: str = Field(
        ...,
        description="Path of the file or folder to move.",
        min_length=1,
    )
    destination_path: str = Field(
        ...,
        description="Destination path or directory.",
        min_length=1,
    )
    confirm_overwrite: bool = Field(
        False,
        description="Explicit confirmation if destination already exists.",
    )


class DeleteFileInput(BaseModel):
    """Pydantic schema for deleting a file in an approved directory."""
    file_path: str = Field(
        ...,
        description="Path of the file to delete.",
        min_length=1,
    )
    confirmed: bool = Field(
        False,
        description="Legacy user confirmation flag (ignored without approved confirmation_token).",
    )
    confirmation_token: Optional[str] = Field(
        None,
        description="Approved ConfirmationManager token for deletion.",
    )


class DeleteFolderInput(BaseModel):
    """Pydantic schema for deleting a folder in an approved directory."""
    folder_path: str = Field(
        ...,
        description="Path of the folder to delete.",
        min_length=1,
    )
    confirmed: bool = Field(
        False,
        description="Legacy user confirmation flag (ignored without approved confirmation_token).",
    )
    recursive: bool = Field(
        False,
        description="Required to delete a non-empty folder. Deletion is blocked if non-empty and False.",
    )
    confirmation_token: Optional[str] = Field(
        None,
        description="Approved ConfirmationManager token for folder deletion.",
    )


# ==============================================================================
# Pydantic Result Schemas
# ==============================================================================

class FileActionResult(BaseModel):
    """Structured result returned by file management tool functions."""
    success: bool
    operation: str
    source_path: Optional[str] = None
    destination_path: Optional[str] = None
    affected_items: int = 0
    requires_confirmation: bool = False
    confirmation_token: Optional[str] = None
    status: str
    message: str
    timestamp: str
    items: Optional[List[Dict[str, Any]]] = None


# ==============================================================================
# Tool Functions
# ==============================================================================

def list_directory_tool(
    data: ListDirectoryInput,
    manager: Optional[FileManager] = None,
) -> FileActionResult:
    """List files and folders in an approved directory."""
    mgr = manager if manager is not None else FileManager()
    now_ts = datetime.now(timezone.utc).isoformat()

    try:
        logger.info(f"list_directory_tool invoked for path: '{data.directory_path}'")
        file_infos = mgr.list_directory(data.directory_path, show_hidden=data.show_hidden)
        items_dict = [fi.to_dict() for fi in file_infos]

        return FileActionResult(
            success=True,
            operation="list_directory",
            source_path=data.directory_path,
            affected_items=len(items_dict),
            requires_confirmation=False,
            status="success",
            message=f"Found {len(items_dict)} items in directory.",
            timestamp=now_ts,
            items=items_dict,
        )
    except PathNotFoundError as err:
        return FileActionResult(
            success=False,
            operation="list_directory",
            source_path=data.directory_path,
            status="not_found",
            message=str(err),
            timestamp=now_ts,
        )
    except (PathNotAllowedError, PathTraversalError) as err:
        return FileActionResult(
            success=False,
            operation="list_directory",
            source_path=data.directory_path,
            status="not_allowed",
            message=str(err),
            timestamp=now_ts,
        )
    except FileManagerError as err:
        return FileActionResult(
            success=False,
            operation="list_directory",
            source_path=data.directory_path,
            status="error",
            message=str(err),
            timestamp=now_ts,
        )


def search_files_tool(
    data: SearchFilesInput,
    manager: Optional[FileManager] = None,
) -> FileActionResult:
    """Search for files in an approved directory by name pattern and/or extension."""
    mgr = manager if manager is not None else FileManager()
    now_ts = datetime.now(timezone.utc).isoformat()

    try:
        logger.info(f"search_files_tool invoked for path: '{data.directory_path}', query='{data.query}'")
        file_infos = mgr.search_files(
            dir_path=data.directory_path,
            query=data.query,
            extension=data.extension,
            max_depth=data.max_depth,
            max_results=data.max_results,
        )
        items_dict = [fi.to_dict() for fi in file_infos]

        return FileActionResult(
            success=True,
            operation="search_files",
            source_path=data.directory_path,
            affected_items=len(items_dict),
            requires_confirmation=False,
            status="success",
            message=f"Found {len(items_dict)} matching items.",
            timestamp=now_ts,
            items=items_dict,
        )
    except (PathNotAllowedError, PathTraversalError) as err:
        return FileActionResult(
            success=False,
            operation="search_files",
            source_path=data.directory_path,
            status="not_allowed",
            message=str(err),
            timestamp=now_ts,
        )
    except FileManagerError as err:
        return FileActionResult(
            success=False,
            operation="search_files",
            source_path=data.directory_path,
            status="error",
            message=str(err),
            timestamp=now_ts,
        )


def get_file_metadata_tool(
    data: GetFileMetadataInput,
    manager: Optional[FileManager] = None,
) -> FileActionResult:
    """Retrieve metadata for an approved file or directory."""
    mgr = manager if manager is not None else FileManager()
    now_ts = datetime.now(timezone.utc).isoformat()

    try:
        info = mgr.get_metadata(data.file_path)
        return FileActionResult(
            success=True,
            operation="get_metadata",
            source_path=data.file_path,
            affected_items=1,
            requires_confirmation=False,
            status="success",
            message="Metadata retrieved successfully.",
            timestamp=now_ts,
            items=[info.to_dict()],
        )
    except PathNotFoundError as err:
        return FileActionResult(
            success=False,
            operation="get_metadata",
            source_path=data.file_path,
            status="not_found",
            message=str(err),
            timestamp=now_ts,
        )
    except (PathNotAllowedError, PathTraversalError) as err:
        return FileActionResult(
            success=False,
            operation="get_metadata",
            source_path=data.file_path,
            status="not_allowed",
            message=str(err),
            timestamp=now_ts,
        )
    except FileManagerError as err:
        return FileActionResult(
            success=False,
            operation="get_metadata",
            source_path=data.file_path,
            status="error",
            message=str(err),
            timestamp=now_ts,
        )


def create_folder_tool(
    data: CreateFolderInput,
    manager: Optional[FileManager] = None,
) -> FileActionResult:
    """Create a new folder in an approved directory."""
    mgr = manager if manager is not None else FileManager()
    now_ts = datetime.now(timezone.utc).isoformat()

    try:
        res = mgr.create_folder(data.folder_path)
        return FileActionResult(
            success=True,
            operation="create_folder",
            destination_path=res.get("path", data.folder_path),
            affected_items=1,
            requires_confirmation=False,
            status="success",
            message=f"Folder created successfully at '{data.folder_path}'.",
            timestamp=now_ts,
        )
    except FileExistsConflictError as err:
        return FileActionResult(
            success=False,
            operation="create_folder",
            destination_path=data.folder_path,
            status="already_exists",
            message=str(err),
            timestamp=now_ts,
        )
    except (PathNotAllowedError, PathTraversalError) as err:
        return FileActionResult(
            success=False,
            operation="create_folder",
            destination_path=data.folder_path,
            status="not_allowed",
            message=str(err),
            timestamp=now_ts,
        )
    except FileManagerError as err:
        return FileActionResult(
            success=False,
            operation="create_folder",
            destination_path=data.folder_path,
            status="error",
            message=str(err),
            timestamp=now_ts,
        )


def create_file_tool(
    data: CreateFileInput,
    manager: Optional[FileManager] = None,
) -> FileActionResult:
    """Create a new empty file in an approved directory with overwrite confirmation guard."""
    mgr = manager if manager is not None else FileManager()
    now_ts = datetime.now(timezone.utc).isoformat()

    try:
        res = mgr.create_file(data.file_path, confirm_overwrite=data.confirm_overwrite)
        return FileActionResult(
            success=True,
            operation="create_file",
            destination_path=res.get("path", data.file_path),
            affected_items=1,
            requires_confirmation=False,
            status="success",
            message=f"File created successfully at '{data.file_path}'.",
            timestamp=now_ts,
        )
    except ConfirmationRequiredError as err:
        return FileActionResult(
            success=False,
            operation="create_file",
            destination_path=err.target,
            requires_confirmation=True,
            status="confirmation_required",
            message=str(err),
            timestamp=now_ts,
        )
    except (PathNotAllowedError, PathTraversalError) as err:
        return FileActionResult(
            success=False,
            operation="create_file",
            destination_path=data.file_path,
            status="not_allowed",
            message=str(err),
            timestamp=now_ts,
        )
    except FileManagerError as err:
        return FileActionResult(
            success=False,
            operation="create_file",
            destination_path=data.file_path,
            status="error",
            message=str(err),
            timestamp=now_ts,
        )


def rename_item_tool(
    data: RenameItemInput,
    manager: Optional[FileManager] = None,
) -> FileActionResult:
    """Rename a file or folder in an approved directory."""
    mgr = manager if manager is not None else FileManager()
    now_ts = datetime.now(timezone.utc).isoformat()

    try:
        # Determine whether source is file or directory
        src_path = mgr.validate_path(data.source_path, must_exist=True)
        if src_path.is_dir():
            res = mgr.rename_folder(src_path, data.new_name)
        else:
            res = mgr.rename_file(src_path, data.new_name, confirm_overwrite=data.confirm_overwrite)

        return FileActionResult(
            success=True,
            operation=res.get("operation", "rename_item"),
            source_path=res.get("source_path", data.source_path),
            destination_path=res.get("destination_path"),
            affected_items=1,
            requires_confirmation=False,
            status="success",
            message=f"Successfully renamed to '{data.new_name}'.",
            timestamp=now_ts,
        )
    except ConfirmationRequiredError as err:
        return FileActionResult(
            success=False,
            operation="rename_item",
            source_path=data.source_path,
            destination_path=err.target,
            requires_confirmation=True,
            status="confirmation_required",
            message=str(err),
            timestamp=now_ts,
        )
    except FileExistsConflictError as err:
        return FileActionResult(
            success=False,
            operation="rename_item",
            source_path=data.source_path,
            status="already_exists",
            message=str(err),
            timestamp=now_ts,
        )
    except (PathNotAllowedError, PathTraversalError) as err:
        return FileActionResult(
            success=False,
            operation="rename_item",
            source_path=data.source_path,
            status="not_allowed",
            message=str(err),
            timestamp=now_ts,
        )
    except FileManagerError as err:
        return FileActionResult(
            success=False,
            operation="rename_item",
            source_path=data.source_path,
            status="error",
            message=str(err),
            timestamp=now_ts,
        )


def copy_item_tool(
    data: CopyItemInput,
    manager: Optional[FileManager] = None,
) -> FileActionResult:
    """Copy a file or folder within approved directories."""
    mgr = manager if manager is not None else FileManager()
    now_ts = datetime.now(timezone.utc).isoformat()

    try:
        src_path = mgr.validate_path(data.source_path, must_exist=True)
        if src_path.is_dir():
            res = mgr.copy_folder(src_path, data.destination_path, confirm_overwrite=data.confirm_overwrite)
        else:
            res = mgr.copy_file(src_path, data.destination_path, confirm_overwrite=data.confirm_overwrite)

        return FileActionResult(
            success=True,
            operation=res.get("operation", "copy_item"),
            source_path=res.get("source_path", data.source_path),
            destination_path=res.get("destination_path", data.destination_path),
            affected_items=1,
            requires_confirmation=False,
            status="success",
            message=f"Successfully copied '{data.source_path}' to '{data.destination_path}'.",
            timestamp=now_ts,
        )
    except ConfirmationRequiredError as err:
        return FileActionResult(
            success=False,
            operation="copy_item",
            source_path=data.source_path,
            destination_path=err.target,
            requires_confirmation=True,
            status="confirmation_required",
            message=str(err),
            timestamp=now_ts,
        )
    except (PathNotAllowedError, PathTraversalError) as err:
        return FileActionResult(
            success=False,
            operation="copy_item",
            source_path=data.source_path,
            destination_path=data.destination_path,
            status="not_allowed",
            message=str(err),
            timestamp=now_ts,
        )
    except FileManagerError as err:
        return FileActionResult(
            success=False,
            operation="copy_item",
            source_path=data.source_path,
            destination_path=data.destination_path,
            status="error",
            message=str(err),
            timestamp=now_ts,
        )


def move_item_tool(
    data: MoveItemInput,
    manager: Optional[FileManager] = None,
) -> FileActionResult:
    """Move a file or folder within approved directories."""
    mgr = manager if manager is not None else FileManager()
    now_ts = datetime.now(timezone.utc).isoformat()

    try:
        src_path = mgr.validate_path(data.source_path, must_exist=True)
        if src_path.is_dir():
            res = mgr.move_folder(src_path, data.destination_path)
        else:
            res = mgr.move_file(src_path, data.destination_path, confirm_overwrite=data.confirm_overwrite)

        return FileActionResult(
            success=True,
            operation=res.get("operation", "move_item"),
            source_path=res.get("source_path", data.source_path),
            destination_path=res.get("destination_path", data.destination_path),
            affected_items=1,
            requires_confirmation=False,
            status="success",
            message=f"Successfully moved '{data.source_path}' to '{data.destination_path}'.",
            timestamp=now_ts,
        )
    except ConfirmationRequiredError as err:
        return FileActionResult(
            success=False,
            operation="move_item",
            source_path=data.source_path,
            destination_path=err.target,
            requires_confirmation=True,
            status="confirmation_required",
            message=str(err),
            timestamp=now_ts,
        )
    except (PathNotAllowedError, PathTraversalError) as err:
        return FileActionResult(
            success=False,
            operation="move_item",
            source_path=data.source_path,
            destination_path=data.destination_path,
            status="not_allowed",
            message=str(err),
            timestamp=now_ts,
        )
    except FileManagerError as err:
        return FileActionResult(
            success=False,
            operation="move_item",
            source_path=data.source_path,
            destination_path=data.destination_path,
            status="error",
            message=str(err),
            timestamp=now_ts,
        )


def delete_file_tool(
    data: DeleteFileInput,
    manager: Optional[FileManager] = None,
) -> FileActionResult:
    """Delete a file in an approved directory. Requires explicit user confirmation via ConfirmationManager."""
    mgr = manager if manager is not None else FileManager()
    cm = get_shared_confirmation_manager()
    now_ts = datetime.now(timezone.utc).isoformat()

    try:
        validated_path = mgr.validate_path(data.file_path, must_exist=True)
    except (PathNotAllowedError, PathTraversalError) as err:
        return FileActionResult(
            success=False,
            operation="delete_file",
            source_path=data.file_path,
            status="not_allowed",
            message=str(err),
            timestamp=now_ts,
        )
    except FileManagerError as err:
        return FileActionResult(
            success=False,
            operation="delete_file",
            source_path=data.file_path,
            status="error",
            message=str(err),
            timestamp=now_ts,
        )

    action_params = {"file_path": str(validated_path)}

    # Without an approved confirmation token, execution is refused
    if not data.confirmation_token:
        pending = cm.create_confirmation(
            action_type="delete_file",
            action_params=action_params,
            details={"file_path": str(validated_path)},
        )
        return FileActionResult(
            success=False,
            operation="delete_file",
            source_path=str(validated_path),
            requires_confirmation=True,
            confirmation_token=pending.token,
            status="confirmation_required",
            message=(
                f"Deleting file '{validated_path.name}' requires explicit confirmation. "
                f"Approval token generated: {pending.token}"
            ),
            timestamp=now_ts,
        )

    try:
        cm.validate_and_consume(
            token=data.confirmation_token,
            action_type="delete_file",
            action_params=action_params,
        )
    except ConfirmationError as conf_err:
        return FileActionResult(
            success=False,
            operation="delete_file",
            source_path=str(validated_path),
            requires_confirmation=True,
            confirmation_token=data.confirmation_token,
            status="confirmation_required" if isinstance(conf_err, MissingUserApprovalError) else "confirmation_failed",
            message=str(conf_err),
            timestamp=now_ts,
        )

    try:
        res = mgr.delete_file(validated_path, confirmed=True)
        return FileActionResult(
            success=True,
            operation="delete_file",
            source_path=res.get("path", str(validated_path)),
            affected_items=1,
            requires_confirmation=False,
            confirmation_token=data.confirmation_token,
            status="success",
            message=f"File '{validated_path.name}' deleted successfully.",
            timestamp=now_ts,
        )
    except FileManagerError as err:
        return FileActionResult(
            success=False,
            operation="delete_file",
            source_path=str(validated_path),
            status="error",
            message=str(err),
            timestamp=now_ts,
        )


def delete_folder_tool(
    data: DeleteFolderInput,
    manager: Optional[FileManager] = None,
) -> FileActionResult:
    """Delete a folder in an approved directory. Requires explicit user confirmation via ConfirmationManager."""
    mgr = manager if manager is not None else FileManager()
    cm = get_shared_confirmation_manager()
    now_ts = datetime.now(timezone.utc).isoformat()

    try:
        validated_path = mgr.validate_path(data.folder_path, must_exist=True)
    except (PathNotAllowedError, PathTraversalError) as err:
        return FileActionResult(
            success=False,
            operation="delete_folder",
            source_path=data.folder_path,
            status="not_allowed",
            message=str(err),
            timestamp=now_ts,
        )
    except FileManagerError as err:
        return FileActionResult(
            success=False,
            operation="delete_folder",
            source_path=data.folder_path,
            status="error",
            message=str(err),
            timestamp=now_ts,
        )

    action_params = {"folder_path": str(validated_path), "recursive": data.recursive}

    if not data.confirmation_token:
        pending = cm.create_confirmation(
            action_type="delete_folder",
            action_params=action_params,
            details={"folder_path": str(validated_path), "recursive": data.recursive},
        )
        return FileActionResult(
            success=False,
            operation="delete_folder",
            source_path=str(validated_path),
            requires_confirmation=True,
            confirmation_token=pending.token,
            status="confirmation_required",
            message=(
                f"Deleting folder '{validated_path.name}' requires explicit confirmation. "
                f"Approval token generated: {pending.token}"
            ),
            timestamp=now_ts,
        )

    try:
        cm.validate_and_consume(
            token=data.confirmation_token,
            action_type="delete_folder",
            action_params=action_params,
        )
    except ConfirmationError as conf_err:
        return FileActionResult(
            success=False,
            operation="delete_folder",
            source_path=str(validated_path),
            requires_confirmation=True,
            confirmation_token=data.confirmation_token,
            status="confirmation_required" if isinstance(conf_err, MissingUserApprovalError) else "confirmation_failed",
            message=str(conf_err),
            timestamp=now_ts,
        )

    try:
        res = mgr.delete_folder(validated_path, confirmed=True, recursive=data.recursive)
        return FileActionResult(
            success=True,
            operation="delete_folder",
            source_path=res.get("path", str(validated_path)),
            affected_items=1,
            requires_confirmation=False,
            confirmation_token=data.confirmation_token,
            status="success",
            message=f"Folder '{validated_path.name}' deleted successfully.",
            timestamp=now_ts,
        )
    except DirectoryNotEmptyError as err:
        return FileActionResult(
            success=False,
            operation="delete_folder",
            source_path=str(validated_path),
            requires_confirmation=True,
            status="directory_not_empty",
            message=str(err),
            timestamp=now_ts,
        )
    except FileManagerError as err:
        return FileActionResult(
            success=False,
            operation="delete_folder",
            source_path=str(validated_path),
            status="error",
            message=str(err),
            timestamp=now_ts,
        )

