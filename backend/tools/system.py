"""
VoxPilot - System Information Agent Tool
Provides safe, read-only system metrics (OS, CPU, RAM) without shell execution.
"""

from __future__ import annotations

import logging
import platform
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
import psutil

logger = logging.getLogger(__name__)


class EmptyInput(BaseModel):
    """Pydantic schema for tools taking no input parameters."""
    pass


class SystemInfoResult(BaseModel):
    """Structured result returned by system information tools."""
    success: bool
    operation: str
    status: str
    message: str
    timestamp: str
    os_name: str
    os_version: str
    cpu_percent: float
    cpu_count: int
    ram_total_gb: float
    ram_used_gb: float
    ram_percent: float
    data: Optional[Dict[str, Any]] = None


def get_system_info_tool(data: Optional[EmptyInput] = None) -> SystemInfoResult:
    """Retrieve safe, read-only host system metrics (OS, CPU, RAM usage)."""
    now_ts = datetime.now(timezone.utc).isoformat()
    try:
        mem = psutil.virtual_memory()
        cpu_pct = psutil.cpu_percent(interval=0.1)
        cpu_count = psutil.cpu_count(logical=True) or 1
        os_name = platform.system()
        os_ver = platform.version()

        ram_total_gb = round(mem.total / (1024 ** 3), 2)
        ram_used_gb = round(mem.used / (1024 ** 3), 2)
        ram_pct = mem.percent

        msg = (
            f"OS: {os_name} {os_ver}, CPU: {cpu_pct}%, "
            f"RAM: {ram_used_gb} GB / {ram_total_gb} GB ({ram_pct}% used)."
        )

        return SystemInfoResult(
            success=True,
            operation="get_system_info",
            status="success",
            message=msg,
            timestamp=now_ts,
            os_name=os_name,
            os_version=os_ver,
            cpu_percent=cpu_pct,
            cpu_count=cpu_count,
            ram_total_gb=ram_total_gb,
            ram_used_gb=ram_used_gb,
            ram_percent=ram_pct,
            data={
                "os": os_name,
                "os_version": os_ver,
                "cpu_percent": cpu_pct,
                "cpu_count": cpu_count,
                "ram_total_gb": ram_total_gb,
                "ram_used_gb": ram_used_gb,
                "ram_percent": ram_pct,
            },
        )
    except Exception as err:
        logger.error(f"Error querying system info: {err}")
        return SystemInfoResult(
            success=False,
            operation="get_system_info",
            status="error",
            message=f"Failed to query system metrics: {err}",
            timestamp=now_ts,
            os_name="",
            os_version="",
            cpu_percent=0.0,
            cpu_count=0,
            ram_total_gb=0.0,
            ram_used_gb=0.0,
            ram_percent=0.0,
        )

