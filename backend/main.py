"""
VoxPilot - Main FastAPI Application
Entry point for the VoxPilot Desktop Control Agent REST and WebSocket APIs.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from backend.api.agent import router as agent_router
from backend.database.connection import init_db, close_db
from backend.automation.emergency_stop import (
    start_global_hotkey_listener,
    stop_global_hotkey_listener,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown events."""
    # Startup: ensure SQLite database tables exist
    await init_db()
    # Start Windows global emergency stop hotkey listener (Ctrl + Alt + Escape)
    start_global_hotkey_listener()
    yield
    # Shutdown: stop hotkey listener and cleanly close database engines
    stop_global_hotkey_listener()
    await close_db()


app = FastAPI(
    title="VoxPilot Agent API",
    description="Personal AI Desktop Control Agent API - LangGraph Orchestrated",
    version="0.5.0",
    lifespan=lifespan,
)

# Include API routers
app.include_router(agent_router)


@app.get("/", tags=["system"])
def read_root():
    """Service status and metadata."""
    return {
        "app": "VoxPilot Agent API",
        "version": "0.5.0",
        "status": "online",
    }


@app.get("/health", tags=["system"])
def health_check():
    """Health check endpoint for service monitoring."""
    return {
        "status": "healthy",
        "service": "VoxPilot",
        "version": "0.5.0",
    }


if __name__ == "__main__":
    import uvicorn
    # Enforce localhost-only server binding (127.0.0.1) for desktop security
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=False)


