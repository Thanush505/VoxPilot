"""
VoxPilot - Packaging & Version Consistency Verification Script
Ensures one authoritative version source (VERSION) across backend, frontend,
PyInstaller spec, and Inno Setup installer configuration before distribution.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.version import DEFAULT_APP_VERSION, get_app_version, parse_semver


def verify_version_consistency(repo_root: Path = REPO_ROOT, check_dist: bool = False) -> List[str]:
    """Return a list of version-consistency errors (empty list means all checks pass)."""
    errors: List[str] = []

    version_file = repo_root / "VERSION"
    if not version_file.is_file():
        return [f"Missing canonical VERSION file at {version_file}"]

    canonical_raw = version_file.read_text(encoding="utf-8-sig").strip()
    try:
        canonical = parse_semver(canonical_raw).normalized
    except Exception as exc:
        return [f"Invalid SemVer in VERSION file ({canonical_raw!r}): {exc}"]

    if DEFAULT_APP_VERSION != canonical:
        errors.append(
            f"backend/version.py DEFAULT_APP_VERSION ({DEFAULT_APP_VERSION!r}) != VERSION ({canonical!r})"
        )

    runtime_version = get_app_version()
    if runtime_version != canonical:
        errors.append(
            f"get_app_version() returned {runtime_version!r}, expected {canonical!r}"
        )

    pkg_json = repo_root / "frontend" / "package.json"
    if pkg_json.is_file():
        pkg_data = json.loads(pkg_json.read_text(encoding="utf-8-sig"))
        if pkg_data.get("version") != canonical:
            errors.append(
                f"frontend/package.json version ({pkg_data.get('version')!r}) != VERSION ({canonical!r})"
            )
    else:
        errors.append(f"Missing frontend/package.json at {pkg_json}")

    iss_file = repo_root / "installer" / "VoxPilot.iss"
    if iss_file.is_file():
        iss_text = iss_file.read_text(encoding="utf-8-sig")
        if 'FileOpen("..\\VERSION")' not in iss_text:
            errors.append("installer/VoxPilot.iss does not dynamically read ..\\VERSION")
        if "VersionInfoVersion={#MyAppVersion}.0" not in iss_text:
            errors.append("installer/VoxPilot.iss VersionInfoVersion does not use {#MyAppVersion}.0")
        if re.search(r"AppUserModelID:\s*\"[^\"]*0\.5\.[01]", iss_text):
            errors.append("installer/VoxPilot.iss contains stale hardcoded AppUserModelID version")
    else:
        errors.append(f"Missing installer/VoxPilot.iss at {iss_file}")

    spec_file = repo_root / "VoxPilot.spec"
    if spec_file.is_file():
        spec_text = spec_file.read_text(encoding="utf-8-sig")
        if "VERSION" not in spec_text or "DEFAULT_APP_VERSION" not in spec_text:
            errors.append("VoxPilot.spec must verify and bundle VERSION against DEFAULT_APP_VERSION")
    else:
        errors.append(f"Missing VoxPilot.spec at {spec_file}")

    ver_info_file = repo_root / "assets" / "version_info.txt"
    if ver_info_file.is_file():
        ver_info_text = ver_info_file.read_text(encoding="utf-8-sig")
        if f"StringStruct('ProductVersion', '{canonical}')" not in ver_info_text:
            errors.append(f"assets/version_info.txt ProductVersion != {canonical!r}")
        if f"StringStruct('FileVersion', '{canonical}.0')" not in ver_info_text:
            errors.append(f"assets/version_info.txt FileVersion != '{canonical}.0'")
    else:
        errors.append(f"Missing assets/version_info.txt at {ver_info_file}")

    for ui_rel in ("frontend/src/components/layout/Sidebar.tsx", "frontend/src/pages/SettingsPage.tsx"):
        ui_path = repo_root / ui_rel
        if ui_path.is_file():
            ui_text = ui_path.read_text(encoding="utf-8-sig")
            if re.search(r"['\"]0\.5\.[01]['\"]", ui_text):
                errors.append(f"{ui_rel} contains stale hardcoded fallback version")

    if check_dist:
        dist_dir = repo_root / "dist" / "VoxPilot"
        if not dist_dir.is_dir():
            errors.append(f"dist/VoxPilot not found at {dist_dir}")
        else:
            internal_ver = dist_dir / "_internal" / "VERSION"
            if not internal_ver.is_file():
                errors.append(f"Missing bundled VERSION at {internal_ver}")
            for cand in (internal_ver, dist_dir / "VERSION"):
                if cand.is_file():
                    found = cand.read_text(encoding="utf-8-sig").strip()
                    if found != canonical:
                        errors.append(f"Stale VERSION in {cand}: {found!r} != {canonical!r}")

    return errors


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify VoxPilot version consistency.")
    parser.add_argument("--check-dist", action="store_true", help="Also verify dist/VoxPilot VERSION files.")
    args = parser.parse_args(argv)

    errors = verify_version_consistency(check_dist=args.check_dist)
    if errors:
        print("Version consistency check FAILED:")
        for err in errors:
            print(f"  - {err}")
        return 1

    print(f"Version consistency check PASSED (canonical version: {get_app_version()}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
