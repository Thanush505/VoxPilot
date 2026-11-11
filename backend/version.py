"""
VoxPilot - Centralized Application Version Authority & Semantic Version Utilities
Provides the single source of truth for the installed VoxPilot version across
the FastAPI backend, standalone launcher, update service, and frontend UI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple, Union

from backend.paths import get_bundle_dir, get_executable_dir, get_project_root, is_frozen

DEFAULT_APP_VERSION = "0.5.1"

# Strict semantic version regex: optional leading 'v' or 'V', major.minor.patch,
# optional pre-release (-alpha.1, -beta.2, -rc.1) and optional build metadata (+build.123)
_SEMVER_PATTERN = re.compile(
    r"^[vV]?(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


class InvalidVersionError(ValueError):
    """Raised when a version string does not conform to semantic versioning."""


@dataclass(frozen=True)
class SemanticVersion:
    """Parsed semantic version supporting comparison according to SemVer 2.0.0."""

    major: int
    minor: int
    patch: int
    prerelease: Tuple[Union[int, str], ...] = ()
    build: Optional[str] = None
    raw: str = ""

    @property
    def is_prerelease(self) -> bool:
        """Return True if this version has a pre-release identifier (e.g. -beta.1)."""
        return len(self.prerelease) > 0

    @property
    def normalized(self) -> str:
        """Return canonical semantic version string without leading 'v'."""
        base = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            pre = ".".join(str(part) for part in self.prerelease)
            base = f"{base}-{pre}"
        return base

    def _comparison_key(self) -> Tuple[int, int, int, int, Tuple[Tuple[int, Union[int, str]], ...]]:
        """Build a tuple suitable for total ordering per SemVer 2.0.0 rules.

        Normal releases sort higher than pre-releases for the same major.minor.patch
        (is_stable_flag = 1 vs 0). Numeric pre-release identifiers sort lower than
        alphanumeric identifiers.
        """
        if not self.prerelease:
            return (self.major, self.minor, self.patch, 1, ())
        pre_key = tuple(
            (0, part) if isinstance(part, int) else (1, part)
            for part in self.prerelease
        )
        return (self.major, self.minor, self.patch, 0, pre_key)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SemanticVersion):
            return NotImplemented
        return self._comparison_key() < other._comparison_key()

    def __le__(self, other: object) -> bool:
        if not isinstance(other, SemanticVersion):
            return NotImplemented
        return self._comparison_key() <= other._comparison_key()

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, SemanticVersion):
            return NotImplemented
        return self._comparison_key() > other._comparison_key()

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, SemanticVersion):
            return NotImplemented
        return self._comparison_key() >= other._comparison_key()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SemanticVersion):
            return NotImplemented
        return self._comparison_key() == other._comparison_key()


def parse_semver(version_str: str) -> SemanticVersion:
    """Parse a semantic version string (e.g. '0.5.0', 'v0.6.1', '1.0.0-beta.1').

    Raises InvalidVersionError if the input is empty, non-string, or malformed.
    """
    if not isinstance(version_str, str):
        raise InvalidVersionError(f"Version must be a string, got {type(version_str).__name__}")

    cleaned = version_str.strip()
    if not cleaned:
        raise InvalidVersionError("Version string cannot be empty")

    match = _SEMVER_PATTERN.match(cleaned)
    if not match:
        raise InvalidVersionError(f"Invalid semantic version format: '{version_str}'")

    major = int(match.group("major"))
    minor = int(match.group("minor"))
    patch = int(match.group("patch"))
    pre_raw = match.group("prerelease")
    build = match.group("build")

    prerelease_parts: List[Union[int, str]] = []
    if pre_raw:
        for token in pre_raw.split("."):
            if token.isdigit():
                prerelease_parts.append(int(token))
            else:
                prerelease_parts.append(token.lower())

    return SemanticVersion(
        major=major,
        minor=minor,
        patch=patch,
        prerelease=tuple(prerelease_parts),
        build=build,
        raw=cleaned,
    )


def try_parse_semver(version_str: Optional[str]) -> Optional[SemanticVersion]:
    """Attempt to parse a semantic version string, returning None on invalid input."""
    if not version_str:
        return None
    try:
        return parse_semver(version_str)
    except InvalidVersionError:
        return None


def compare_versions(version_a: str, version_b: str) -> int:
    """Compare two semantic version strings.

    Returns:
      -1 if version_a < version_b
       0 if version_a == version_b
      +1 if version_a > version_b

    Raises InvalidVersionError if either version string is invalid.
    """
    sem_a = parse_semver(version_a)
    sem_b = parse_semver(version_b)
    if sem_a < sem_b:
        return -1
    if sem_a > sem_b:
        return 1
    return 0


def is_newer_version(installed_version: str, candidate_version: str, allow_prerelease: bool = False) -> bool:
    """Return True strictly when candidate_version is newer than installed_version.

    Never returns True if candidate_version is equal to, older than, or invalid compared
    to installed_version. If allow_prerelease is False, pre-release candidates are ignored.
    """
    installed = try_parse_semver(installed_version)
    candidate = try_parse_semver(candidate_version)
    if installed is None or candidate is None:
        return False
    if candidate.is_prerelease and not allow_prerelease:
        return False
    return candidate > installed


def get_version_file_candidates() -> List[Path]:
    """Return ordered candidate locations for the canonical VERSION file."""
    candidates: List[Path] = []
    if is_frozen():
        candidates.append(get_bundle_dir() / "VERSION")
        candidates.append(get_executable_dir() / "VERSION")
    candidates.append(get_project_root() / "VERSION")
    return candidates


def get_app_version() -> str:
    """Read and return the canonical installed VoxPilot version string."""
    for candidate in get_version_file_candidates():
        try:
            if candidate.is_file():
                content = candidate.read_text(encoding="utf-8").strip()
                parsed = try_parse_semver(content)
                if parsed is not None:
                    return parsed.normalized
        except Exception:
            continue
    return DEFAULT_APP_VERSION


APP_VERSION: str = get_app_version()


