"""
VoxPilot - Main FastAPI Application
Entry point for the VoxPilot Desktop Control Agent REST and WebSocket APIs.
"""

import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from backend.api.agent import router as agent_router
from backend.api.updates import api_updates_router, agent_updates_router
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
    yield
    # Shutdown: stop voice mode & hotkeys and cleanly close database engines
    voice_service.shutdown()
    stop_global_hotkey_listener()
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
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS", "DELETE"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

# Include API routers
app.include_router(agent_router)
app.include_router(api_updates_router)
app.include_router(agent_updates_router)

# Mount frontend static assets if available
_frontend_dist = get_frontend_dist_dir()
if _frontend_dist.exists() and (_frontend_dist / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=str(_frontend_dist / "assets")), name="frontend_assets")


@app.get("/", tags=["system"])
def read_root(request: Request):
    """Service status, metadata, or frontend dashboard depending on Accept header."""
    accept = request.headers.get("accept", "").lower()
    index_file = _frontend_dist / "index.html"
    if "text/html" in accept and index_file.exists():
        return FileResponse(str(index_file))

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
    if full_path and target_file.is_file():
        return FileResponse(str(target_file))

    index_file = _frontend_dist / "index.html"
    accept = request.headers.get("accept", "").lower()
    if ("text/html" in accept or "*/*" in accept) and index_file.exists():
        return FileResponse(str(index_file))

    raise HTTPException(status_code=404, detail=f"Path not found: /{full_path}")


if __name__ == "__main__":
    import uvicorn
    # Enforce localhost-only server binding (127.0.0.1) for desktop security
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=False)


