"""
VoxPilot - File and Directory Management Controller
Provides safe, allowlisted, path-validated filesystem interaction for Windows 11.
Enforces strict approved directory containment, path traversal prevention,
reparse-point escape rejection, and mandatory confirmation for destructive operations.
"""

from __future__ import annotations

import os
import re
import shutil
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any, Union

logger = logging.getLogger(__name__)


# ==============================================================================
# Security Boundaries and Allowed Directory Defaults
# ==============================================================================

def get_default_approved_directories() -> List[Path]:
    """Retrieve the default set of user data directories approved for file operations.

    Safe defaults:
      - Documents
      - Downloads
      - Desktop
      - Pictures
      - Music
      - Videos
    """
    home = Path.home().resolve()
    candidates = [
        home / "Documents",
        home / "Downloads",
        home / "Desktop",
        home / "Pictures",
        home / "Music",
        home / "Videos",
    ]
    # Filter to existing directories or create a standard user-space workspace
    approved = [c.resolve() for c in candidates if c.exists()]
    if not approved:
        # Fallback to home/Documents or dedicated folder
        docs = home / "Documents"
        docs.mkdir(parents=True, exist_ok=True)
        approved.append(docs.resolve())
    return approved


def get_default_project_root() -> Path:
    """Determine the canonical project root or executable directory to protect it from alteration."""
    from backend.paths import is_frozen, get_executable_dir, get_project_root
    if is_frozen():
        return get_executable_dir()
    return get_project_root()


# Disallowed system directory roots on Windows
DISALLOWED_SYSTEM_PATTERNS = [
    re.compile(r"^[A-Za-z]:\\Windows(?:\\.*)?$", re.IGNORECASE),
    re.compile(r"^[A-Za-z]:\\Program Files(?:\\.*)?$", re.IGNORECASE),
    re.compile(r"^[A-Za-z]:\\Program Files \(x86\)(?:\\.*)?$", re.IGNORECASE),
    re.compile(r"^[A-Za-z]:\\ProgramData(?:\\.*)?$", re.IGNORECASE),
    re.compile(r"^[A-Za-z]:\\Recovery(?:\\.*)?$", re.IGNORECASE),
    re.compile(r"^[A-Za-z]:\\System Volume Information(?:\\.*)?$", re.IGNORECASE),
    re.compile(r"^[A-Za-z]:\\\$Recycle\.Bin(?:\\.*)?$", re.IGNORECASE),
]

MAX_SEARCH_DEPTH = 10
DEFAULT_SEARCH_DEPTH = 5
MAX_SEARCH_RESULTS = 100
DEFAULT_SEARCH_RESULTS = 50
MAX_SEARCH_SCANNED_ITEMS = 1000


# ==============================================================================
# Exceptions
# ==============================================================================

class FileManagerError(Exception):
    """Base exception for all file management operations."""
    pass


class PathNotAllowedError(FileManagerError):
    """Raised when a path is outside approved directory roots or in a prohibited directory."""
    pass


class PathTraversalError(PathNotAllowedError):
    """Raised when path traversal (../, UNC, device paths, reparse point escapes) is detected."""
    pass


class PathNotFoundError(FileManagerError):
    """Raised when a target file or folder does not exist."""
    pass


class FileExistsConflictError(FileManagerError):
    """Raised when destination file or folder already exists and overwrite is not permitted."""
    pass


class ConfirmationRequiredError(FileManagerError):
    """Raised when a destructive operation is requested without explicit confirmation."""
    def __init__(self, message: str, target: str, operation: str, requires_confirmation: bool = True):
        super().__init__(message)
        self.target = target
        self.operation = operation
        self.requires_confirmation = requires_confirmation


class DirectoryNotEmptyError(FileManagerError):
    """Raised when attempting to delete a non-empty directory without recursive=True."""
    pass


class FileAccessError(FileManagerError):
    """Raised when file permission is denied or a file lock sharing violation occurs."""
    pass


class InvalidOperationError(FileManagerError):
    """Raised when an operation is invalid (e.g. file operation on directory, or invalid name)."""
    pass


# ==============================================================================
# Data Models
# ==============================================================================

@dataclass(frozen=True)
class FileInfo:
    """Metadata for a file or directory item."""
    name: str
    path: str
    is_directory: bool
    size_bytes: int
    modified_at: str
    created_at: str
    extension: str
    is_symlink: bool = False
    is_readonly: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "is_directory": self.is_directory,
            "size_bytes": self.size_bytes,
            "modified_at": self.modified_at,
            "created_at": self.created_at,
            "extension": self.extension,
            "is_symlink": self.is_symlink,
            "is_readonly": self.is_readonly,
        }


# ==============================================================================
# File Manager Controller
# ==============================================================================

class FileManager:
    """Safe Windows file and folder management controller.

    Enforces strict path containment within approved directory roots, blocks
    directory traversal and symlink escapes, protects system and project directories,
    and requires explicit confirmation before any destructive operation.
    """

    def __init__(
        self,
        approved_directories: Optional[List[Union[str, Path]]] = None,
        project_root: Optional[Union[str, Path]] = None,
    ) -> None:
        """Initialize FileManager with approved directories and project root protection.

        Args:
            approved_directories: Optional custom list of approved directories. If omitted,
                                  defaults to standard user directories (Documents, Downloads, Desktop, etc.).
            project_root: Optional project root path to protect. Defaults to the VoxPilot repository root.
        """
        if approved_directories is not None:
            self._approved_directories = [Path(d).resolve() for d in approved_directories]
        else:
            self._approved_directories = get_default_approved_directories()

        if project_root is not None:
            self._project_root = Path(project_root).resolve()
        else:
            self._project_root = get_default_project_root()

        logger.info(
            f"FileManager initialized with {len(self._approved_directories)} approved directories. "
            f"Protected project root: {self._project_root}"
        )

    @property
    def approved_directories(self) -> List[Path]:
        """Return the list of canonical approved directories."""
        return list(self._approved_directories)

    @property
    def project_root(self) -> Path:
        """Return the canonical project root."""
        return self._project_root

    # --------------------------------------------------------------------------
    # Path Validation and Security
    # --------------------------------------------------------------------------

    def validate_path(
        self,
        raw_path: Union[str, Path],
        must_exist: bool = True,
        check_parent_if_new: bool = False,
    ) -> Path:
        """Validate and resolve a path against approved directories and security rules.

        Args:
            raw_path: String or Path representation of the path to validate.
            must_exist: If True, the path must already exist on disk.
            check_parent_if_new: If True and path does not exist, check its parent directory.

        Returns:
            Path: Canonical, fully-resolved Path object.

        Raises:
            PathTraversalError: If UNC paths, device paths, null bytes, or traversal detected.
            PathNotAllowedError: If path resolves outside approved directories or to a forbidden target.
            PathNotFoundError: If must_exist is True and path does not exist.
        """
        if raw_path is None:
            raise PathTraversalError("Path cannot be None.")

        path_str = str(raw_path).strip()
        if not path_str:
            raise PathTraversalError("Path cannot be empty.")

        # Reject null bytes
        if "\0" in path_str:
            raise PathTraversalError("Path contains null byte.")

        # Reject Windows device namespaces (\\.\, \\?\) and UNC network paths (\\server\share, //server/share)
        if path_str.startswith(("\\\\", "//", "\\\\?\\", "\\\\.\\")):
            raise PathTraversalError(f"UNC network and Windows device paths are rejected: {path_str!r}")

        # Reject alternate data streams (e.g. C:\path\file.txt:stream)
        # On Windows, drive letter has ':' at index 1 (e.g. C:\). Any other ':' indicates a stream or malformed path.
        clean_after_drive = path_str[2:] if (len(path_str) >= 2 and path_str[1] == ":") else path_str
        if ":" in clean_after_drive:
            raise PathTraversalError(f"Alternate data streams and stream specifiers are prohibited: {path_str!r}")

        path_obj = Path(path_str)

        # Check existence if required
        if must_exist and not path_obj.exists():
            raise PathNotFoundError(f"Path does not exist: {path_str}")

        # Resolve path to canonical representation (resolves symlinks, junctions, ../)
        try:
            resolved = path_obj.resolve()
        except Exception as err:
            raise PathTraversalError(f"Failed to resolve path '{path_str}': {err}") from err

        # If path does not exist and check_parent_if_new is requested, validate nearest ancestor
        if not resolved.exists():
            if must_exist:
                raise PathNotFoundError(f"Resolved path does not exist: {resolved}")
            if check_parent_if_new:
                # Find the nearest existing ancestor directory
                ancestor = resolved.parent
                while not ancestor.exists() and ancestor != ancestor.parent:
                    ancestor = ancestor.parent
                if not ancestor.exists():
                    raise PathNotFoundError(f"No existing ancestor directory found for: {resolved}")
                self._check_directory_containment(ancestor.resolve(), raw_path_str=path_str)

        # Check directory containment for the target path itself
        self._check_directory_containment(resolved, raw_path_str=path_str)

        # Symlink / Reparse Point verification:
        # If the original path was a symlink, verify where it pointed
        try:
            if path_obj.is_symlink():
                target_dest = Path(os.readlink(path_obj))
                if not target_dest.is_absolute():
                    target_dest = (path_obj.parent / target_dest).resolve()
                else:
                    target_dest = target_dest.resolve()
                self._check_directory_containment(target_dest, raw_path_str=str(target_dest))
        except (OSError, ValueError):
            pass

        return resolved

    def _check_directory_containment(self, resolved: Path, raw_path_str: str) -> None:
        """Check whether a resolved path complies with directory containment and prohibition rules."""
        resolved_str = str(resolved)

        # 1. Reject drive roots (e.g. C:\, D:\)
        if resolved == resolved.parent or len(resolved.parts) <= 1:
            raise PathNotAllowedError(f"Drive root access is strictly prohibited: {resolved_str}")

        # 2. Reject user profile root (e.g. C:\Users\Username itself cannot be operated on)
        user_home = Path.home().resolve()
        if resolved == user_home:
            raise PathNotAllowedError(f"Operating on user profile root is prohibited: {resolved_str}")

        # 3. Reject Windows and system directories
        for pattern in DISALLOWED_SYSTEM_PATTERNS:
            if pattern.match(resolved_str):
                raise PathNotAllowedError(f"Access to Windows system directory is prohibited: {resolved_str}")

        # 4. Reject VoxPilot project root protection
        try:
            if resolved.is_relative_to(self._project_root):
                raise PathNotAllowedError(
                    f"Access to VoxPilot project directory is strictly prohibited for security: {resolved_str}"
                )
        except (ValueError, TypeError):
            pass

        # 5. Verify path is contained inside at least one approved directory
        contained = False
        for approved_root in self._approved_directories:
            try:
                if resolved.is_relative_to(approved_root):
                    contained = True
                    break
            except (ValueError, TypeError):
                continue

        if not contained:
            raise PathNotAllowedError(
                f"Path '{raw_path_str}' resolves to '{resolved_str}' which is outside approved directories."
            )

    def _get_item_info(self, path: Path) -> FileInfo:
        """Extract FileInfo metadata from an existing Path."""
        try:
            stat = path.stat()
            size = stat.st_size if not path.is_dir() else 0
            mod_dt = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
            cre_dt = datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat()
            is_readonly = not os.access(path, os.W_OK)
            is_symlink = path.is_symlink()
        except OSError as err:
            raise FileAccessError(f"Cannot access metadata for '{path}': {err}") from err

        return FileInfo(
            name=path.name,
            path=str(path),
            is_directory=path.is_dir(),
            size_bytes=size,
            modified_at=mod_dt,
            created_at=cre_dt,
            extension=path.suffix.lower() if not path.is_dir() else "",
            is_symlink=is_symlink,
            is_readonly=is_readonly,
        )

    # --------------------------------------------------------------------------
    # 1. List Files and Folders
    # --------------------------------------------------------------------------

    def list_directory(
        self,
        dir_path: Union[str, Path],
        show_hidden: bool = False,
    ) -> List[FileInfo]:
        """List files and folders in an approved directory.

        Args:
            dir_path: Directory path to list.
            show_hidden: Whether to include hidden files (starting with '.' or with hidden attribute).

        Returns:
            List[FileInfo]: Sorted list of items in the directory.
        """
        validated_dir = self.validate_path(dir_path, must_exist=True)
        if not validated_dir.is_dir():
            raise InvalidOperationError(f"Target path is not a directory: {validated_dir}")

        items: List[FileInfo] = []
        try:
            with os.scandir(validated_dir) as entries:
                for entry in entries:
                    if not show_hidden and entry.name.startswith("."):
                        continue
                    try:
                        entry_path = Path(entry.path)
                        # Ensure individual entry does not escape through symlink
                        self.validate_path(entry_path, must_exist=True)
                        items.append(self._get_item_info(entry_path))
                    except (PathNotAllowedError, PathTraversalError, OSError) as err:
                        logger.debug(f"Skipping inaccessible or prohibited entry '{entry.name}': {err}")
                        continue
        except PermissionError as err:
            raise FileAccessError(f"Permission denied accessing directory: {validated_dir}") from err
        except OSError as err:
            raise FileAccessError(f"OS error reading directory '{validated_dir}': {err}") from err

        # Sort directories first, then alphabetical by name
        items.sort(key=lambda x: (not x.is_directory, x.name.lower()))
        return items

    # --------------------------------------------------------------------------
    # 2 & 3. Search Files by Name and Extension
    # --------------------------------------------------------------------------

    def search_files(
        self,
        dir_path: Union[str, Path],
        query: Optional[str] = None,
        extension: Optional[str] = None,
        max_depth: int = DEFAULT_SEARCH_DEPTH,
        max_results: int = DEFAULT_SEARCH_RESULTS,
    ) -> List[FileInfo]:
        """Search for files in an approved directory by name pattern and/or extension.

        Args:
            dir_path: Root directory to search within.
            query: Substring or wildcard pattern to match in filename.
            extension: File extension filter (e.g. '.txt', 'pdf').
            max_depth: Maximum directory recursion depth (1 to 10).
            max_results: Maximum results to return (1 to 200).

        Returns:
            List[FileInfo]: Matching files and folders up to max_results.
        """
        validated_dir = self.validate_path(dir_path, must_exist=True)
        if not validated_dir.is_dir():
            raise InvalidOperationError(f"Search target is not a directory: {validated_dir}")

        depth_limit = max(1, min(max_depth, MAX_SEARCH_DEPTH))
        results_limit = max(1, min(max_results, MAX_SEARCH_RESULTS))

        norm_query = query.strip().lower() if query else None
        norm_ext = extension.strip().lower() if extension else None
        if norm_ext and not norm_ext.startswith("."):
            norm_ext = f".{norm_ext}"

        matches: List[FileInfo] = []
        base_depth = len(validated_dir.parts)
        total_scanned = 0

        # Skip patterns for directory recursion
        skip_dir_names = {
            ".git", ".venv", "node_modules", "__pycache__", "$recycle.bin",
            "system volume information", ".pytest_cache", ".mypy_cache"
        }

        try:
            for root, dirs, files in os.walk(validated_dir, topdown=True, followlinks=False):
                current_depth = len(Path(root).parts) - base_depth
                if current_depth >= depth_limit:
                    dirs.clear()  # Do not recurse deeper

                # Filter out prohibited and hidden directories in-place
                dirs[:] = [
                    d for d in dirs
                    if d.lower() not in skip_dir_names and not d.startswith(".")
                ]

                # Check files in current directory
                for filename in files:
                    total_scanned += 1
                    if total_scanned >= MAX_SEARCH_SCANNED_ITEMS:
                        logger.warning(
                            f"File search item limit reached ({total_scanned}/{MAX_SEARCH_SCANNED_ITEMS} items scanned). "
                            "Terminating search early."
                        )
                        return matches

                    if len(matches) >= results_limit:
                        return matches

                    # Name filter
                    if norm_query and norm_query not in filename.lower():
                        continue

                    # Extension filter
                    file_ext = Path(filename).suffix.lower()
                    if norm_ext and file_ext != norm_ext:
                        continue

                    file_path = Path(root) / filename
                    try:
                        # Validate path to ensure no containment violation
                        self.validate_path(file_path, must_exist=True)
                        matches.append(self._get_item_info(file_path))
                    except (PathNotAllowedError, PathTraversalError, OSError):
                        continue

        except PermissionError as err:
            logger.warning(f"Permission denied during search in '{validated_dir}': {err}")
        except OSError as err:
            logger.warning(f"OS error during search in '{validated_dir}': {err}")

        return matches

    # --------------------------------------------------------------------------
    # 4. Get File Metadata
    # --------------------------------------------------------------------------

    def get_metadata(self, file_path: Union[str, Path]) -> FileInfo:
        """Retrieve detailed metadata for an approved file or directory."""
        validated_path = self.validate_path(file_path, must_exist=True)
        return self._get_item_info(validated_path)

    # --------------------------------------------------------------------------
    # 5. Create a New Folder
    # --------------------------------------------------------------------------

    def create_folder(self, folder_path: Union[str, Path]) -> Dict[str, Any]:
        """Create a new folder in an approved directory.

        Args:
            folder_path: Path of the folder to create.

        Returns:
            Dict[str, Any]: Execution result.
        """
        validated_path = self.validate_path(folder_path, must_exist=False, check_parent_if_new=True)
        if validated_path.exists():
            raise FileExistsConflictError(f"Target folder already exists: {validated_path}")

        try:
            validated_path.mkdir(parents=True, exist_ok=False)
            logger.info(f"Created folder: {validated_path}")
            return {
                "success": True,
                "operation": "create_folder",
                "path": str(validated_path),
                "status": "success",
            }
        except PermissionError as err:
            raise FileAccessError(f"Permission denied creating folder: {validated_path}") from err
        except OSError as err:
            raise FileAccessError(f"OS error creating folder '{validated_path}': {err}") from err

    # --------------------------------------------------------------------------
    # 6. Create a New Empty File
    # --------------------------------------------------------------------------

    def create_file(
        self,
        file_path: Union[str, Path],
        confirm_overwrite: bool = False,
    ) -> Dict[str, Any]:
        """Create a new empty file in an approved directory.

        Args:
            file_path: Path of the file to create.
            confirm_overwrite: Must be True to overwrite if file already exists.

        Returns:
            Dict[str, Any]: Execution result.
        """
        validated_path = self.validate_path(file_path, must_exist=False, check_parent_if_new=True)

        if validated_path.exists():
            if not confirm_overwrite:
                raise ConfirmationRequiredError(
                    f"File '{validated_path.name}' already exists. Overwrite requires explicit confirmation.",
                    target=str(validated_path),
                    operation="create_file_overwrite",
                )
            if validated_path.is_dir():
                raise InvalidOperationError(f"Cannot overwrite directory with a file: {validated_path}")

        try:
            # Create / touch empty file
            validated_path.touch(exist_ok=True)
            logger.info(f"Created empty file: {validated_path}")
            return {
                "success": True,
                "operation": "create_file",
                "path": str(validated_path),
                "overwritten": validated_path.exists(),
                "status": "success",
            }
        except PermissionError as err:
            raise FileAccessError(f"Permission denied creating file: {validated_path}") from err
        except OSError as err:
            raise FileAccessError(f"OS error creating file '{validated_path}': {err}") from err

    # --------------------------------------------------------------------------
    # 7. Rename a File
    # --------------------------------------------------------------------------

    def rename_file(
        self,
        source_path: Union[str, Path],
        new_name: str,
        confirm_overwrite: bool = False,
    ) -> Dict[str, Any]:
        """Rename a file in an approved directory.

        Args:
            source_path: Existing file path.
            new_name: New filename (name only, no directory components).
            confirm_overwrite: If target name exists, must be True to overwrite.

        Returns:
            Dict[str, Any]: Execution result.
        """
        validated_src = self.validate_path(source_path, must_exist=True)
        if validated_src.is_dir():
            raise InvalidOperationError(f"Source is a directory, not a file: {validated_src}")

        if not new_name or not new_name.strip():
            raise InvalidOperationError("New file name cannot be empty.")

        clean_name = new_name.strip()
        # Reject path separators and traversal in new_name
        if "/" in clean_name or "\\" in clean_name or ".." in clean_name:
            raise PathTraversalError(f"New name cannot contain path separators or traversal: {new_name!r}")

        dest_path = validated_src.parent / clean_name
        validated_dest = self.validate_path(dest_path, must_exist=False)

        if validated_dest.exists() and validated_dest != validated_src:
            if not confirm_overwrite:
                raise ConfirmationRequiredError(
                    f"Destination '{validated_dest.name}' already exists. Overwriting requires confirmation.",
                    target=str(validated_dest),
                    operation="rename_file_overwrite",
                )
            if validated_dest.is_dir():
                raise InvalidOperationError(f"Cannot overwrite an existing directory with a file: {validated_dest}")

        try:
            os.replace(validated_src, validated_dest)
            logger.info(f"Renamed file '{validated_src.name}' -> '{validated_dest.name}'")
            return {
                "success": True,
                "operation": "rename_file",
                "source_path": str(validated_src),
                "destination_path": str(validated_dest),
                "status": "success",
            }
        except PermissionError as err:
            raise FileAccessError(f"Permission denied renaming file: {err}") from err
        except OSError as err:
            raise FileAccessError(f"OS error renaming file: {err}") from err

    # --------------------------------------------------------------------------
    # 8. Rename a Folder
    # --------------------------------------------------------------------------

    def rename_folder(
        self,
        source_path: Union[str, Path],
        new_name: str,
    ) -> Dict[str, Any]:
        """Rename a folder in an approved directory.

        Args:
            source_path: Existing directory path.
            new_name: New folder name (name only, no directory components).

        Returns:
            Dict[str, Any]: Execution result.
        """
        validated_src = self.validate_path(source_path, must_exist=True)
        if not validated_src.is_dir():
            raise InvalidOperationError(f"Source is a file, not a directory: {validated_src}")

        # Protect approved root directories themselves from being renamed
        for approved_root in self._approved_directories:
            if validated_src == approved_root:
                raise PathNotAllowedError(f"Cannot rename approved root directory: {validated_src}")

        if not new_name or not new_name.strip():
            raise InvalidOperationError("New folder name cannot be empty.")

        clean_name = new_name.strip()
        if "/" in clean_name or "\\" in clean_name or ".." in clean_name:
            raise PathTraversalError(f"New name cannot contain path separators or traversal: {new_name!r}")

        dest_path = validated_src.parent / clean_name
        validated_dest = self.validate_path(dest_path, must_exist=False)

        if validated_dest.exists():
            raise FileExistsConflictError(f"Destination folder already exists: {validated_dest}")

        try:
            os.replace(validated_src, validated_dest)
            logger.info(f"Renamed folder '{validated_src.name}' -> '{validated_dest.name}'")
            return {
                "success": True,
                "operation": "rename_folder",
                "source_path": str(validated_src),
                "destination_path": str(validated_dest),
                "status": "success",
            }
        except PermissionError as err:
            raise FileAccessError(f"Permission denied renaming folder: {err}") from err
        except OSError as err:
            raise FileAccessError(f"OS error renaming folder: {err}") from err

    # --------------------------------------------------------------------------
    # 9. Copy a File
    # --------------------------------------------------------------------------

    def copy_file(
        self,
        source_path: Union[str, Path],
        destination_path: Union[str, Path],
        confirm_overwrite: bool = False,
    ) -> Dict[str, Any]:
        """Copy a file from source to destination within approved directories.

        Args:
            source_path: Existing source file path.
            destination_path: Destination file or directory path.
            confirm_overwrite: Required if destination file exists.

        Returns:
            Dict[str, Any]: Execution result.
        """
        validated_src = self.validate_path(source_path, must_exist=True)
        if validated_src.is_dir():
            raise InvalidOperationError(f"Source is a directory; use copy_folder instead: {validated_src}")

        # If destination is an existing directory, copy into it with same filename
        dest_candidate = Path(destination_path)
        if dest_candidate.exists() and dest_candidate.is_dir():
            target_dest = dest_candidate / validated_src.name
        else:
            target_dest = dest_candidate

        validated_dest = self.validate_path(target_dest, must_exist=False, check_parent_if_new=True)

        if validated_dest.exists():
            if not confirm_overwrite:
                raise ConfirmationRequiredError(
                    f"Destination file '{validated_dest.name}' already exists. Overwrite requires confirmation.",
                    target=str(validated_dest),
                    operation="copy_file_overwrite",
                )
            if validated_dest.is_dir():
                raise InvalidOperationError(f"Cannot overwrite a directory with a file: {validated_dest}")

        try:
            shutil.copy2(validated_src, validated_dest)
            logger.info(f"Copied file '{validated_src}' -> '{validated_dest}'")
            return {
                "success": True,
                "operation": "copy_file",
                "source_path": str(validated_src),
                "destination_path": str(validated_dest),
                "status": "success",
            }
        except PermissionError as err:
            raise FileAccessError(f"Permission denied copying file: {err}") from err
        except OSError as err:
            raise FileAccessError(f"OS error copying file: {err}") from err

    # --------------------------------------------------------------------------
    # 10. Copy a Folder
    # --------------------------------------------------------------------------

    def copy_folder(
        self,
        source_path: Union[str, Path],
        destination_path: Union[str, Path],
        confirm_overwrite: bool = False,
    ) -> Dict[str, Any]:
        """Copy a folder and its contents within approved directories.

        Args:
            source_path: Existing source folder path.
            destination_path: Target folder path.
            confirm_overwrite: Required if destination folder exists.

        Returns:
            Dict[str, Any]: Execution result.
        """
        validated_src = self.validate_path(source_path, must_exist=True)
        if not validated_src.is_dir():
            raise InvalidOperationError(f"Source is a file; use copy_file instead: {validated_src}")

        validated_dest = self.validate_path(destination_path, must_exist=False, check_parent_if_new=True)

        # Prevent copying a directory into itself or a subdirectory
        try:
            if validated_dest.is_relative_to(validated_src):
                raise InvalidOperationError("Cannot copy a directory into itself or a subdirectory.")
        except (ValueError, TypeError):
            pass

        if validated_dest.exists():
            if not confirm_overwrite:
                raise ConfirmationRequiredError(
                    f"Destination folder '{validated_dest.name}' already exists. Overwrite requires confirmation.",
                    target=str(validated_dest),
                    operation="copy_folder_overwrite",
                )

        try:
            shutil.copytree(validated_src, validated_dest, dirs_exist_ok=confirm_overwrite)
            logger.info(f"Copied folder '{validated_src}' -> '{validated_dest}'")
            return {
                "success": True,
                "operation": "copy_folder",
                "source_path": str(validated_src),
                "destination_path": str(validated_dest),
                "status": "success",
            }
        except PermissionError as err:
            raise FileAccessError(f"Permission denied copying folder: {err}") from err
        except OSError as err:
            raise FileAccessError(f"OS error copying folder: {err}") from err

    # --------------------------------------------------------------------------
    # 11. Move a File
    # --------------------------------------------------------------------------

    def move_file(
        self,
        source_path: Union[str, Path],
        destination_path: Union[str, Path],
        confirm_overwrite: bool = False,
    ) -> Dict[str, Any]:
        """Move a file within approved directories.

        Args:
            source_path: Existing source file path.
            destination_path: Destination file or directory path.
            confirm_overwrite: Required if destination file exists.

        Returns:
            Dict[str, Any]: Execution result.
        """
        validated_src = self.validate_path(source_path, must_exist=True)
        if validated_src.is_dir():
            raise InvalidOperationError(f"Source is a directory; use move_folder instead: {validated_src}")

        dest_candidate = Path(destination_path)
        if dest_candidate.exists() and dest_candidate.is_dir():
            target_dest = dest_candidate / validated_src.name
        else:
            target_dest = dest_candidate

        validated_dest = self.validate_path(target_dest, must_exist=False, check_parent_if_new=True)

        if validated_dest.exists() and validated_dest != validated_src:
            if not confirm_overwrite:
                raise ConfirmationRequiredError(
                    f"Destination file '{validated_dest.name}' already exists. Overwrite requires confirmation.",
                    target=str(validated_dest),
                    operation="move_file_overwrite",
                )
            if validated_dest.is_dir():
                raise InvalidOperationError(f"Cannot overwrite directory with a file: {validated_dest}")

        try:
            shutil.move(str(validated_src), str(validated_dest))
            logger.info(f"Moved file '{validated_src}' -> '{validated_dest}'")
            return {
                "success": True,
                "operation": "move_file",
                "source_path": str(validated_src),
                "destination_path": str(validated_dest),
                "status": "success",
            }
        except PermissionError as err:
            raise FileAccessError(f"Permission denied moving file: {err}") from err
        except OSError as err:
            raise FileAccessError(f"OS error moving file: {err}") from err

    # --------------------------------------------------------------------------
    # 12. Move a Folder
    # --------------------------------------------------------------------------

    def move_folder(
        self,
        source_path: Union[str, Path],
        destination_path: Union[str, Path],
    ) -> Dict[str, Any]:
        """Move a folder within approved directories.

        Args:
            source_path: Existing source directory path.
            destination_path: Destination directory path.

        Returns:
            Dict[str, Any]: Execution result.
        """
        validated_src = self.validate_path(source_path, must_exist=True)
        if not validated_src.is_dir():
            raise InvalidOperationError(f"Source is a file; use move_file instead: {validated_src}")

        # Protect approved root directories from being moved
        for approved_root in self._approved_directories:
            if validated_src == approved_root:
                raise PathNotAllowedError(f"Cannot move approved root directory: {validated_src}")

        validated_dest = self.validate_path(destination_path, must_exist=False, check_parent_if_new=True)

        # Prevent moving a directory into itself
        try:
            if validated_dest.is_relative_to(validated_src):
                raise InvalidOperationError("Cannot move a directory into itself or a subdirectory.")
        except (ValueError, TypeError):
            pass

        if validated_dest.exists():
            raise FileExistsConflictError(f"Destination folder already exists: {validated_dest}")

        try:
            shutil.move(str(validated_src), str(validated_dest))
            logger.info(f"Moved folder '{validated_src}' -> '{validated_dest}'")
            return {
                "success": True,
                "operation": "move_folder",
                "source_path": str(validated_src),
                "destination_path": str(validated_dest),
                "status": "success",
            }
        except PermissionError as err:
            raise FileAccessError(f"Permission denied moving folder: {err}") from err
        except OSError as err:
            raise FileAccessError(f"OS error moving folder: {err}") from err

    # --------------------------------------------------------------------------
    # 13. Delete a File (Requires Confirmation)
    # --------------------------------------------------------------------------

    def delete_file(
        self,
        file_path: Union[str, Path],
        confirmed: bool = False,
    ) -> Dict[str, Any]:
        """Delete an existing file in an approved directory.

        Requires confirmed=True. Never deletes directories.

        Args:
            file_path: File path to delete.
            confirmed: Explicit confirmation flag.

        Returns:
            Dict[str, Any]: Execution result.
        """
        validated_path = self.validate_path(file_path, must_exist=True)
        if validated_path.is_dir():
            raise InvalidOperationError(f"Target is a directory, not a file: {validated_path}")

        if not confirmed:
            raise ConfirmationRequiredError(
                f"Deleting file '{validated_path.name}' requires explicit confirmation.",
                target=str(validated_path),
                operation="delete_file",
                requires_confirmation=True,
            )

        try:
            os.remove(validated_path)
            logger.info(f"Deleted file: {validated_path}")
            return {
                "success": True,
                "operation": "delete_file",
                "path": str(validated_path),
                "status": "success",
            }
        except PermissionError as err:
            raise FileAccessError(f"Permission denied deleting file '{validated_path}': {err}") from err
        except OSError as err:
            raise FileAccessError(f"OS error deleting file '{validated_path}': {err}") from err

    # --------------------------------------------------------------------------
    # 14. Delete a Folder (Requires Confirmation & Recursive Safety)
    # --------------------------------------------------------------------------

    def delete_folder(
        self,
        folder_path: Union[str, Path],
        confirmed: bool = False,
        recursive: bool = False,
    ) -> Dict[str, Any]:
        """Delete a folder in an approved directory.

        Requires confirmed=True. If folder has contents, recursive=True is also required.
        Never deletes approved root directories, system directories, or the project root.

        Args:
            folder_path: Folder path to delete.
            confirmed: Explicit confirmation flag.
            recursive: Whether to delete non-empty folder recursively.

        Returns:
            Dict[str, Any]: Execution result.
        """
        validated_path = self.validate_path(folder_path, must_exist=True)
        if not validated_path.is_dir():
            raise InvalidOperationError(f"Target is a file, not a directory: {validated_path}")

        # Protect approved root directories themselves from being deleted
        for approved_root in self._approved_directories:
            if validated_path == approved_root:
                raise PathNotAllowedError(f"Cannot delete approved root directory: {validated_path}")

        if not confirmed:
            raise ConfirmationRequiredError(
                f"Deleting folder '{validated_path.name}' requires explicit confirmation.",
                target=str(validated_path),
                operation="delete_folder",
                requires_confirmation=True,
            )

        # Check if directory has contents
        try:
            has_contents = any(os.scandir(validated_path))
        except OSError as err:
            raise FileAccessError(f"Failed to inspect directory contents for '{validated_path}': {err}") from err

        if has_contents and not recursive:
            raise DirectoryNotEmptyError(
                f"Folder '{validated_path.name}' is not empty. "
                f"Recursive deletion must be explicitly requested with recursive=True."
            )

        try:
            if has_contents:
                shutil.rmtree(validated_path)
            else:
                os.rmdir(validated_path)

            logger.info(f"Deleted folder: {validated_path} (recursive={recursive})")
            return {
                "success": True,
                "operation": "delete_folder",
                "path": str(validated_path),
                "recursive": recursive,
                "status": "success",
            }
        except PermissionError as err:
            raise FileAccessError(f"Permission denied deleting folder '{validated_path}': {err}") from err
        except OSError as err:
            raise FileAccessError(f"OS error deleting folder '{validated_path}': {err}") from err
