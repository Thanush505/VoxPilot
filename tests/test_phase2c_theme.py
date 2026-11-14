"""
Comprehensive tests for VoxPilot Phase 2C: Theme Customization.

Covers:
1. Backend ProfileRecord theme_mode & accent_color migration, validation & API persistence
2. Playwright UI tests for Light/Dark/System modes, OS prefers-color-scheme changes,
   accent colors, restore defaults, localStorage normalization, restart persistence,
   and per-profile theme restoration on profile switch.
"""

from __future__ import annotations

import http.server
import json
import sqlite3
import socket
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright
from sqlalchemy import text
from sqlmodel import create_engine

from backend.database.connection import run_migrations_sync
from backend.database.models import VALID_ACCENT_COLORS, VALID_THEME_MODES
from backend.main import app
from backend.profiles import (
    InvalidProfileError,
    ProfileService,
    reset_shared_profile_service,
)
from tests.test_frontend_browser import (
    FRONTEND_DIST,
    StaticSpaHandler,
    setup_default_mock_routes,
)


@pytest.fixture()
def isolated_engine(tmp_path: Path):
    db_file = tmp_path / "phase2c_test.db"
    engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
    )
    run_migrations_sync(engine)
    yield engine
    engine.dispose()


# ==============================================================================
# 1. Backend Migration, Validation & API Persistence Tests
# ==============================================================================

def test_theme_columns_migrated_idempotently_preserving_profiles(tmp_path: Path):
    legacy_db = tmp_path / "legacy_pre2c.db"
    conn = sqlite3.connect(legacy_db)
    conn.executescript(
        """
        CREATE TABLE profilerecord (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            description TEXT,
            avatar TEXT NOT NULL DEFAULT 'user',
            is_active BOOLEAN NOT NULL DEFAULT 0,
            is_default BOOLEAN NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        );
        INSERT INTO profilerecord (id, name, normalized_name, avatar, is_active, is_default, created_at, updated_at)
        VALUES ('default', 'Default', 'default', 'user', 1, 1, '2026-11-15T00:00:00', '2026-11-15T00:00:00');
        """
    )
    conn.commit()
    conn.close()

    engine = create_engine(f"sqlite:///{legacy_db}", connect_args={"check_same_thread": False})
    try:
        res1 = run_migrations_sync(engine)
        assert "added_profilerecord.theme_mode" in res1["applied_steps"]
        assert "added_profilerecord.accent_color" in res1["applied_steps"]

        res2 = run_migrations_sync(engine)
        assert "added_profilerecord.theme_mode" not in res2["applied_steps"]

        with engine.connect() as db_conn:
            row = db_conn.execute(
                text("SELECT name, theme_mode, accent_color FROM profilerecord WHERE id = 'default'")
            ).fetchone()
            assert row == ("Default", "dark", "blue")
    finally:
        engine.dispose()


def test_profile_service_and_api_theme_validation(monkeypatch, isolated_engine):
    reset_shared_profile_service()
    svc = ProfileService(sync_engine=isolated_engine)
    monkeypatch.setattr("backend.api.profiles.get_shared_profile_service", lambda: svc)

    with pytest.raises(InvalidProfileError):
        svc.update_profile("default", theme_mode="neon")
    with pytest.raises(InvalidProfileError):
        svc.update_profile("default", accent_color="ultraviolet")

    client = TestClient(app)

    r_list = client.get("/api/profiles")
    assert r_list.status_code == 200
    assert r_list.json()["theme_modes"] == list(VALID_THEME_MODES)
    assert r_list.json()["accent_colors"] == list(VALID_ACCENT_COLORS)

    r_patch = client.patch(
        "/api/profiles/default",
        json={"theme_mode": "light", "accent_color": "emerald"},
        headers={"Origin": "http://localhost:5173"},
    )
    assert r_patch.status_code == 200
    assert r_patch.json()["theme_mode"] == "light"
    assert r_patch.json()["accent_color"] == "emerald"

    r_bad = client.patch(
        "/api/profiles/default",
        json={"theme_mode": "invalid_mode"},
    )
    assert r_bad.status_code == 400


# ==============================================================================
# 2. Playwright Theme UI, System Mode & Profile-Switch Persistence Tests
# ==============================================================================

@pytest.fixture(scope="module")
def ui_static_server():
    if not FRONTEND_DIST.exists() or not (FRONTEND_DIST / "index.html").exists():
        pytest.skip("Frontend dist missing. Run 'npm run build' first.")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), StaticSpaHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()


def test_theme_modes_accents_persistence_and_profile_restore_ui(ui_static_server: str):
    profiles_store = [
        {
            "id": "default",
            "profile_id": "default",
            "name": "Default",
            "normalized_name": "default",
            "description": "Default local VoxPilot profile",
            "avatar": "user",
            "theme_mode": "dark",
            "accent_color": "blue",
            "is_active": True,
            "is_default": True,
            "created_at": "2026-11-15T00:00:00+00:00",
            "updated_at": "2026-11-15T00:00:00+00:00",
        },
        {
            "id": "prof_work",
            "profile_id": "prof_work",
            "name": "Work",
            "normalized_name": "work",
            "description": "Work profile",
            "avatar": "briefcase",
            "theme_mode": "light",
            "accent_color": "indigo",
            "is_active": False,
            "is_default": False,
            "created_at": "2026-11-15T00:01:00+00:00",
            "updated_at": "2026-11-15T00:01:00+00:00",
        },
    ]

    def active_profile():
        for p in profiles_store:
            if p["is_active"]:
                return p
        return profiles_store[0]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(color_scheme="dark")
        page = context.new_page()
        setup_default_mock_routes(page)

        def handle_profiles(route):
            req = route.request
            method = req.method
            url = req.url

            if method == "GET" and url.endswith("/api/profiles"):
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "profiles": profiles_store,
                            "active_profile": active_profile(),
                            "total": len(profiles_store),
                            "avatars": ["user", "briefcase", "graduation-cap", "code", "home", "sparkles"],
                            "theme_modes": list(VALID_THEME_MODES),
                            "accent_colors": list(VALID_ACCENT_COLORS),
                        }
                    ),
                )
                return

            if method == "PATCH" and "/api/profiles/" in url:
                prof_id = url.rsplit("/", 1)[-1]
                payload = json.loads(req.post_data or "{}")
                for prof in profiles_store:
                    if prof["id"] == prof_id:
                        prof.update(payload)
                        route.fulfill(status=200, content_type="application/json", body=json.dumps(prof))
                        return
                route.fulfill(status=404, content_type="application/json", body=json.dumps({"detail": "Not found"}))
                return

            if method == "POST" and "/activate" in url:
                prof_id = url.split("/api/profiles/")[1].split("/activate")[0]
                target = None
                for prof in profiles_store:
                    prof["is_active"] = prof["id"] == prof_id
                    if prof["id"] == prof_id:
                        target = prof
                route.fulfill(status=200, content_type="application/json", body=json.dumps(target))
                return

            route.fallback_()

        page.route("**/api/profiles**", handle_profiles)
        page.route(
            "**/api/personalization**",
            lambda r: r.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"preferences": [], "total": 0, "limit": 100, "offset": 0, "categories": []}),
            ),
        )

        # Seed invalid localStorage value to verify safe normalization
        page.goto(ui_static_server)
        page.evaluate(
            """() => {
                localStorage.setItem('voxpilot.theme.lastActive', JSON.stringify({themeMode: 'broken', accentColor: 'nope'}));
            }"""
        )
        page.reload(wait_until="networkidle")

        # Default dark theme applied
        assert page.evaluate("document.documentElement.getAttribute('data-theme')") == "dark"

        # Navigate to Settings -> Appearance & Theme
        page.locator("button.nav-item:has-text('Settings')").click()
        theme_card = page.get_by_test_id("theme-settings-card")
        theme_card.wait_for(state="visible", timeout=10000)

        # Switch to Light mode & Emerald accent immediately without reload
        page.get_by_test_id("theme-mode-light").click()
        page.get_by_test_id("accent-color-emerald").click()

        assert page.evaluate("document.documentElement.getAttribute('data-theme')") == "light"
        assert "Active: Light" in page.get_by_test_id("active-theme-badge").inner_text()
        bg_canvas = page.evaluate(
            "getComputedStyle(document.documentElement).getPropertyValue('--bg-canvas').trim()"
        )
        assert bg_canvas == "#f8fafc"
        accent_val = page.evaluate(
            "getComputedStyle(document.documentElement).getPropertyValue('--accent-blue').trim()"
        )
        assert accent_val == "#047857"

        # Verify persistence across full page reload (restart simulation)
        page.reload(wait_until="networkidle")
        assert page.evaluate("document.documentElement.getAttribute('data-theme')") == "light"
        assert (
            page.evaluate(
                "getComputedStyle(document.documentElement).getPropertyValue('--accent-blue').trim()"
            )
            == "#047857"
        )

        # Navigate back to Settings and test System mode following OS color scheme
        page.locator("button.nav-item:has-text('Settings')").click()
        page.get_by_test_id("theme-mode-system").click()
        assert page.evaluate("document.documentElement.getAttribute('data-theme')") == "dark"
        page.emulate_media(color_scheme="light")
        page.wait_for_timeout(150)
        assert page.evaluate("document.documentElement.getAttribute('data-theme')") == "light"
        page.emulate_media(color_scheme="dark")
        page.wait_for_timeout(150)
        assert page.evaluate("document.documentElement.getAttribute('data-theme')") == "dark"

        # Restore defaults
        page.get_by_test_id("restore-default-theme-btn").click()
        assert page.evaluate("document.documentElement.getAttribute('data-theme')") == "dark"
        assert (
            page.evaluate(
                "getComputedStyle(document.documentElement).getPropertyValue('--accent-blue').trim()"
            )
            == "#3b82f6"
        )

        # Switch to Work profile -> restores Work's Light + Indigo theme
        page.get_by_label("Switch Active Profile").select_option("prof_work")
        page.wait_for_timeout(200)
        assert page.evaluate("document.documentElement.getAttribute('data-theme')") == "light"
        assert (
            page.evaluate(
                "getComputedStyle(document.documentElement).getPropertyValue('--accent-blue').trim()"
            )
            == "#4f46e5"
        )

        # Switch back to Default profile -> restores Default's Dark + Blue theme
        page.get_by_label("Switch Active Profile").select_option("default")
        page.wait_for_timeout(200)
        assert page.evaluate("document.documentElement.getAttribute('data-theme')") == "dark"
        assert (
            page.evaluate(
                "getComputedStyle(document.documentElement).getPropertyValue('--accent-blue').trim()"
            )
            == "#3b82f6"
        )

        browser.close()
