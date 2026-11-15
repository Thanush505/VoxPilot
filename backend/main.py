"""
VoxPilot - Main FastAPI Application
Entry point for the VoxPilot Desktop Control Agent REST and WebSocket APIs.
"""

import html
import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import Depends, FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from backend.desktop.shell import get_shared_desktop_controller
from backend.api.agent import router as agent_router
from backend.api.memory import api_memory_router, agent_memory_router
from backend.api.personalization import api_personalization_router, agent_personalization_router
from backend.api.profiles import api_profiles_router, agent_profiles_router
from backend.api.screen import api_screen_router, agent_screen_router
from backend.api.system import api_system_router, agent_system_router
from backend.api.updates import api_updates_router, agent_updates_router
from backend.api.email import api_email_router
from backend.api.automations import api_automations_router, agent_automations_router
from backend.api.providers import api_providers_router, agent_providers_router
from backend.ai.vision import get_shared_screen_capture_service
from backend.automation.custom_automations import get_shared_custom_automation_service
from backend.database.connection import init_db, close_db
from backend.automation.emergency_stop import (
    start_global_hotkey_listener,
    stop_global_hotkey_listener,
)
from backend.paths import get_frontend_dist_dir
from backend.version import get_app_version
from backend.voice.voice_service import get_shared_voice_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown events."""
    # Startup: ensure SQLite database tables exist
    await init_db()
    # Start Windows global emergency stop hotkey listener (Ctrl + Alt + Escape)
    start_global_hotkey_listener()
    # Start customizable global Voice Mode shortcut listener (default: Ctrl + Shift + V)
    voice_service = get_shared_voice_service()
    if os.environ.get("VOXPILOT_ENABLE_HOTKEY", "1") != "0":
        voice_service.start_hotkey_listener()
    automation_service = get_shared_custom_automation_service()
    if os.environ.get("VOXPILOT_ENABLE_SCHEDULER", "1") != "0":
        automation_service.start_scheduler()
    yield
    # Shutdown: stop scheduler, voice mode & hotkeys, clear ephemeral screen captures, and close DB
    automation_service.stop_scheduler()
    voice_service.shutdown()
    stop_global_hotkey_listener()
    get_shared_screen_capture_service().clear_all_captures()
    await close_db()


app = FastAPI(
    title="VoxPilot Agent API",
    description="Personal AI Desktop Control Agent API - LangGraph Orchestrated",
    version=get_app_version(),
    lifespan=lifespan,
)

# Configure CORS strictly restricted to local development and packaged frontend origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_origin_regex=r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS", "DELETE"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

def _require_trusted_api_request(request: Request) -> None:
    """Enforce desktop session token and loopback Origin policy on all API routers."""
    from backend.api.agent import _verify_trusted_request

    path = request.url.path.rstrip("/")
    require_token = path not in ("/api/system/desktop/focus", "/agent/system/desktop/focus")
    _verify_trusted_request(request, require_desktop_token=require_token)


_api_router_dependencies = [Depends(_require_trusted_api_request)]

# Include API routers
app.include_router(agent_router, dependencies=_api_router_dependencies)
app.include_router(api_memory_router, dependencies=_api_router_dependencies)
app.include_router(agent_memory_router, dependencies=_api_router_dependencies)
app.include_router(api_personalization_router, dependencies=_api_router_dependencies)
app.include_router(agent_personalization_router, dependencies=_api_router_dependencies)
app.include_router(api_profiles_router, dependencies=_api_router_dependencies)
app.include_router(agent_profiles_router, dependencies=_api_router_dependencies)
app.include_router(api_screen_router, dependencies=_api_router_dependencies)
app.include_router(agent_screen_router, dependencies=_api_router_dependencies)
app.include_router(api_system_router, dependencies=_api_router_dependencies)
app.include_router(agent_system_router, dependencies=_api_router_dependencies)
app.include_router(api_updates_router, dependencies=_api_router_dependencies)
app.include_router(agent_updates_router, dependencies=_api_router_dependencies)
app.include_router(api_email_router, dependencies=_api_router_dependencies)
app.include_router(api_automations_router, dependencies=_api_router_dependencies)
app.include_router(agent_automations_router, dependencies=_api_router_dependencies)
app.include_router(api_providers_router, dependencies=_api_router_dependencies)
app.include_router(agent_providers_router, dependencies=_api_router_dependencies)

# Mount frontend static assets if available
_frontend_dist = get_frontend_dist_dir()
if _frontend_dist.exists() and (_frontend_dist / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=str(_frontend_dist / "assets")), name="frontend_assets")


import re as _re


def _is_loopback_origin(origin: str) -> bool:
    return bool(_re.match(r"^https?://(localhost|127\.0\.0\.1)(:\d+)?(/|$)", origin.strip().lower()))


def _inject_desktop_token_meta(html_text: str, token: str) -> str:
    escaped = html.escape(token, quote=True)
    meta_tag = f'<meta name="voxpilot-desktop-token" content="{escaped}" />'
    head_match = _re.search(r"<head(?:\s[^>]*)?>", html_text, flags=_re.IGNORECASE)
    if head_match:
        idx = head_match.end()
        return f"{html_text[:idx]}\n    {meta_tag}{html_text[idx:]}"
    return f"{meta_tag}\n{html_text}"


def _serve_spa_index(request: Request, index_file: Path):
    """Serve SPA index.html; inject scoped desktop session token only for trusted desktop requests."""
    controller = get_shared_desktop_controller()
    if controller is None:
        return FileResponse(str(index_file))

    origin = (request.headers.get("origin") or "").strip()
    sec_fetch_site = (request.headers.get("sec-fetch-site") or "").strip().lower()
    trusted_origin = not origin or _is_loopback_origin(origin)
    trusted_fetch_site = sec_fetch_site in ("", "none", "same-origin")

    html_text = index_file.read_text(encoding="utf-8")
    if trusted_origin and trusted_fetch_site and getattr(controller, "require_session_token", True):
        html_text = _inject_desktop_token_meta(html_text, controller.session_token)

    return HTMLResponse(
        content=html_text,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Referrer-Policy": "no-referrer",
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": "frame-ancestors 'none'",
            "Vary": "Origin, Sec-Fetch-Site",
        },
    )


@app.get("/", tags=["system"])
def read_root(request: Request):
    """Service status, metadata, or frontend dashboard depending on Accept header."""
    accept = request.headers.get("accept", "").lower()
    index_file = _frontend_dist / "index.html"
    if "text/html" in accept and index_file.exists():
        return _serve_spa_index(request, index_file)

    return {
        "app": "VoxPilot Agent API",
        "version": get_app_version(),
        "status": "online",
    }


@app.get("/health", tags=["system"])
def health_check():
    """Health check endpoint for service monitoring."""
    return {
        "status": "healthy",
        "service": "VoxPilot",
        "version": get_app_version(),
    }


@app.get("/{full_path:path}", include_in_schema=False)
async def catch_all_spa(request: Request, full_path: str):
    """Serve static files or fall back to React SPA index.html for browser routes."""
    if full_path.startswith("agent/") or full_path.startswith("api/") or full_path == "health":
        raise HTTPException(status_code=404, detail="API route not found")

    target_file = _frontend_dist / full_path
    if full_path == "index.html" and target_file.is_file():
        return _serve_spa_index(request, target_file)
    if full_path and target_file.is_file():
        return FileResponse(str(target_file))

    index_file = _frontend_dist / "index.html"
    accept = request.headers.get("accept", "").lower()
    if ("text/html" in accept or "*/*" in accept) and index_file.exists():
        return _serve_spa_index(request, index_file)

    raise HTTPException(status_code=404, detail=f"Path not found: /{full_path}")


if __name__ == "__main__":
    import uvicorn
    # Enforce localhost-only server binding (127.0.0.1) for desktop security
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=False)


