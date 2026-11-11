"""
VoxPilot - Global Emergency Stop and Safe Automation Cancellation Subsystem
Provides a thread-safe, system-wide emergency stop mechanism capable of:
1. Interrupting future agent execution steps and tool invocations.
2. Preventing new automation actions from starting via a latched state.
3. Tracking active automation operations in real-time.
4. Safely stopping running browser sessions and releasing held keyboard/mouse inputs.
5. Requiring an explicit reset verification before automation can resume.
6. Registering a global Windows hotkey (Ctrl + Alt + Escape) running on a background message loop.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any, Callable

try:
    import win32con
except ImportError:
    win32con = None  # type: ignore

logger = logging.getLogger(__name__)


# ==============================================================================
# Windows API Constants & Input Release Helpers
# ==============================================================================

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
VK_ESCAPE = 0x1B
HOTKEY_ID_EMERGENCY_STOP = 0x5650  # ASCII "VP"

KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_MENU = 0x12     # Alt
VK_SHIFT = 0x10
VK_LWIN = 0x5B
VK_RWIN = 0x5C

MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEUP = 0x0040


def release_system_inputs() -> None:
    """Safely release all commonly held keyboard modifiers and mouse buttons.

    Uses low-level Win32 event dispatch to guarantee cleanup even if higher-level
    controllers were interrupted mid-dispatch.
    """
    try:
        user32 = ctypes.windll.user32
        # 1. Release modifier keys
        for vk in (VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN, VK_RWIN):
            try:
                user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
            except Exception as err:
                logger.debug(f"Error releasing key VK 0x{vk:02X}: {err}")

        # 2. Release mouse buttons
        try:
            user32.mouse_event(MOUSEEVENTF_LEFTUP | MOUSEEVENTF_RIGHTUP | MOUSEEVENTF_MIDDLEUP, 0, 0, 0, 0)
        except Exception as err:
            logger.debug(f"Error releasing mouse buttons: {err}")

        logger.info("Emergency input release executed (keyboard modifiers and mouse buttons released).")
    except Exception as exc:
        logger.warning(f"Could not dispatch emergency input release: {exc}")


# ==============================================================================
# Exceptions
# ==============================================================================

class EmergencyStopError(Exception):
    """Base exception for all emergency stop errors."""
    pass


class EmergencyStopActiveError(EmergencyStopError):
    """Raised when an operation is rejected because the emergency stop is active."""
    def __init__(self, message: str = "Emergency stop is active. Automation is disabled.", reason: Optional[str] = None):
        super().__init__(message)
        self.reason = reason or message


class UnsafeResetError(EmergencyStopError):
    """Raised when an emergency stop reset is attempted while active operations are still running."""
    def __init__(self, message: str, active_count: int = 0, active_operations: Optional[List[str]] = None):
        super().__init__(message)
        self.active_count = active_count
        self.active_operations = active_operations or []


# ==============================================================================
# Active Operation Tracking Model
# ==============================================================================

@dataclass
class ActiveOperationRecord:
    """Thread-safe metadata tracking an in-flight automation operation."""
    operation_id: str
    execution_id: Optional[str] = None
    category: str = "general"
    description: str = ""
    start_time: float = field(default_factory=time.time)
    cancellation_requested: bool = False
    status: str = "running"  # running, stopping, completed, cancelled

    def to_dict(self) -> Dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "execution_id": self.execution_id,
            "category": self.category,
            "description": self.description,
            "start_time": self.start_time,
            "cancellation_requested": self.cancellation_requested,
            "status": self.status,
            "duration_seconds": round(time.time() - self.start_time, 3),
        }


# ==============================================================================
# Central Emergency Stop Manager
# ==============================================================================

class EmergencyStopManager:
    """Central thread-safe coordinator for system-wide emergency stop and cancellation."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._is_active: bool = False
        self._triggered_at: Optional[float] = None
        self._trigger_reason: Optional[str] = None
        self._trigger_source: Optional[str] = None
        self._reset_at: Optional[float] = None
        self._active_operations: Dict[str, ActiveOperationRecord] = {}
        self._cleanup_callbacks: List[Callable[[], None]] = []

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._is_active

    @property
    def trigger_reason(self) -> Optional[str]:
        with self._lock:
            return self._trigger_reason

    @property
    def trigger_source(self) -> Optional[str]:
        with self._lock:
            return self._trigger_source

    @property
    def triggered_at(self) -> Optional[float]:
        with self._lock:
            return self._triggered_at

    @property
    def reset_at(self) -> Optional[float]:
        with self._lock:
            return self._reset_at

    def register_cleanup_callback(self, callback: Callable[[], None]) -> None:
        """Register a callback to be invoked immediately upon emergency stop trigger."""
        with self._lock:
            if callback not in self._cleanup_callbacks:
                self._cleanup_callbacks.append(callback)

    def trigger_emergency_stop(
        self,
        reason: str = "Emergency stop triggered by user.",
        source: str = "user",
    ) -> Dict[str, Any]:
        """Activate the latched emergency stop state immediately.

        Idempotent: Subsequent calls while already active log the event and preserve the state.
        """
        with self._lock:
            now = time.time()
            if not self._is_active:
                self._is_active = True
                self._triggered_at = now
                self._trigger_reason = reason
                self._trigger_source = source
                logger.critical(f"EMERGENCY STOP ACTIVATED! Source: '{source}', Reason: '{reason}'")
            else:
                logger.warning(
                    f"Emergency stop already active. Additional trigger from '{source}': '{reason}'"
                )

            # Request cancellation on all currently registered in-flight operations
            for op in self._active_operations.values():
                op.cancellation_requested = True
                if op.status == "running":
                    op.status = "stopping"

            active_count = len(self._active_operations)

        # Execute input release outside lock to prevent deadlocks
        release_system_inputs()

        # Execute any registered cleanup callbacks (e.g. browser shutdown)
        for cb in list(self._cleanup_callbacks):
            try:
                cb()
            except Exception as cb_err:
                logger.warning(f"Error in emergency stop cleanup callback: {cb_err}")

        return self.get_emergency_stop_status()

    def is_emergency_stop_active(self) -> bool:
        """Check whether the emergency stop latch is currently active."""
        with self._lock:
            return self._is_active

    def check_emergency_stop(self) -> None:
        """Verify emergency stop status and raise EmergencyStopActiveError if active."""
        with self._lock:
            if self._is_active:
                raise EmergencyStopActiveError(
                    message=f"Action rejected: emergency stop is active ({self._trigger_reason}).",
                    reason=self._trigger_reason,
                )

    def register_active_operation(
        self,
        operation_id: str,
        execution_id: Optional[str] = None,
        category: str = "general",
        description: str = "",
    ) -> bool:
        """Register an active automation operation before it starts.

        Fails closed: If emergency stop is active, the operation is rejected immediately.
        """
        with self._lock:
            if self._is_active:
                logger.warning(
                    f"Refusing to register operation '{operation_id}' ({category}): emergency stop is active."
                )
                raise EmergencyStopActiveError(
                    message=f"Cannot start operation '{operation_id}': emergency stop is active.",
                    reason=self._trigger_reason,
                )

            self._active_operations[operation_id] = ActiveOperationRecord(
                operation_id=operation_id,
                execution_id=execution_id,
                category=category,
                description=description,
                start_time=time.time(),
                cancellation_requested=False,
                status="running",
            )
            return True

    def unregister_active_operation(self, operation_id: str) -> None:
        """Remove a completed or cancelled operation from the active registry."""
        with self._lock:
            self._active_operations.pop(operation_id, None)

    def is_cancellation_requested(self, operation_id: str) -> bool:
        """Check if an active operation has been marked for cancellation."""
        with self._lock:
            if self._is_active:
                return True
            op = self._active_operations.get(operation_id)
            return op.cancellation_requested if op else False

    def get_active_operations(self) -> List[Dict[str, Any]]:
        """Return a snapshot of all currently tracked active operations."""
        with self._lock:
            return [op.to_dict() for op in self._active_operations.values()]

    list_active_operations = get_active_operations

    def reset_emergency_stop(self, force: bool = False) -> Dict[str, Any]:
        """Explicitly reset the emergency stop state to allow automation to resume.

        Requires that all tracked operations have terminated cleanly and been unregistered.
        Unrestricted forced resets without evidence are strictly disallowed.
        Does NOT resume previously interrupted executions.
        """
        if force:
            raise UnsafeResetError(
                "Unrestricted 'force=True' reset has been removed for safety. "
                "Use operator_emergency_recovery() with verified operator justification and termination evidence."
            )

        with self._lock:
            if not self._is_active:
                return self.get_emergency_stop_status()

            # Safety check: Verify no operations remain registered or in-flight
            active_ops = [
                op.operation_id for op in self._active_operations.values()
                if op.status in ("running", "stopping")
            ]
            if not active_ops and self._active_operations:
                active_ops = list(self._active_operations.keys())

            if active_ops:
                raise UnsafeResetError(
                    f"Cannot reset emergency stop: {len(active_ops)} active operations have not reached "
                    f"a confirmed safe terminal state ({', '.join(active_ops[:3])}). "
                    f"Wait for operations to terminate cleanly or use operator_emergency_recovery().",
                    active_count=len(active_ops),
                    active_operations=active_ops,
                )

            self._is_active = False
            self._reset_at = time.time()
            self._trigger_reason = None
            self._trigger_source = None
            logger.info("Emergency stop has been explicitly reset. Automation is re-enabled.")

        # Ensure input release upon reset to guarantee clean baseline
        release_system_inputs()

        return self.get_emergency_stop_status()

    def operator_emergency_recovery(
        self,
        operator_justification: str,
        confirmed_terminated_operations: List[str],
        verification_evidence: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Authorized operator procedure to recover from emergency stop with verified operation termination.

        Requires:
        1. A detailed operator justification (minimum 10 characters).
        2. Explicit verification of all currently active operations in confirmed_terminated_operations.
        3. Optional process verification: if verification_evidence provides process IDs (pids),
           confirms that each process is terminated. Rejects if a target process is still active.

        Never clears records merely because of elapsed time or stale status.
        Never auto-resumes interrupted executions.
        """
        if not operator_justification or len(operator_justification.strip()) < 10:
            raise ValueError(
                "Operator justification is required and must be at least 10 characters "
                "describing the verified physical or system state."
            )

        with self._lock:
            if not self._is_active:
                return self.get_emergency_stop_status()

            current_active_ids = set(self._active_operations.keys())
            confirmed_set = set(confirmed_terminated_operations or [])

            # Safety check: every active operation must be explicitly verified and accounted for
            unaccounted = current_active_ids - confirmed_set
            if unaccounted:
                raise UnsafeResetError(
                    f"Operator recovery rejected: {len(unaccounted)} active operations are not accounted for "
                    f"in confirmed_terminated_operations: {list(unaccounted)[:5]}. "
                    f"All registered operations must be explicitly verified.",
                    active_count=len(unaccounted),
                    active_operations=list(unaccounted),
                )

            # Verification evidence checking (e.g. process liveness via psutil)
            if verification_evidence:
                pids_to_check = verification_evidence.get("pids") or []
                if isinstance(pids_to_check, int):
                    pids_to_check = [pids_to_check]
                for pid in pids_to_check:
                    try:
                        import psutil
                        if psutil.pid_exists(pid):
                            proc = psutil.Process(pid)
                            if proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
                                raise UnsafeResetError(
                                    f"Operator recovery rejected: Process with PID {pid} is still actively running. "
                                    "Target process must be terminated before emergency recovery.",
                                    active_count=1,
                                    active_operations=list(current_active_ids),
                                )
                    except (ImportError, psutil.NoSuchProcess):
                        pass

            # Safely clear confirmed operations
            for op_id in confirmed_set:
                self._active_operations.pop(op_id, None)

            self._is_active = False
            self._reset_at = time.time()
            self._trigger_reason = None
            self._trigger_source = None
            logger.warning(
                f"Operator emergency recovery completed. Justification: '{operator_justification.strip()}'. "
                f"Cleared operations: {list(confirmed_set)}."
            )

        # Ensure low-level inputs are released
        release_system_inputs()

        return self.get_emergency_stop_status()

    def get_emergency_stop_status(self) -> Dict[str, Any]:
        """Retrieve complete status summary of the emergency stop coordinator."""
        with self._lock:
            return {
                "is_active": self._is_active,
                "triggered_at": self._triggered_at,
                "trigger_reason": self._trigger_reason,
                "trigger_source": self._trigger_source,
                "reset_at": self._reset_at,
                "active_operations_count": len(self._active_operations),
                "active_operations": [op.to_dict() for op in self._active_operations.values()],
            }


# Global Manager Singleton
_SHARED_EMERGENCY_STOP_MANAGER: Optional[EmergencyStopManager] = None
_MANAGER_LOCK = threading.Lock()


def get_shared_emergency_stop_manager() -> EmergencyStopManager:
    """Retrieve or initialize the active shared EmergencyStopManager singleton."""
    global _SHARED_EMERGENCY_STOP_MANAGER
    with _MANAGER_LOCK:
        if _SHARED_EMERGENCY_STOP_MANAGER is None:
            _SHARED_EMERGENCY_STOP_MANAGER = EmergencyStopManager()
        return _SHARED_EMERGENCY_STOP_MANAGER


def set_shared_emergency_stop_manager(manager: Optional[EmergencyStopManager]) -> None:
    """Set or override the shared emergency stop manager singleton (for testing)."""
    global _SHARED_EMERGENCY_STOP_MANAGER
    with _MANAGER_LOCK:
        _SHARED_EMERGENCY_STOP_MANAGER = manager


def reset_shared_emergency_stop_manager() -> None:
    """Reset shared emergency stop manager (for tests)."""
    global _SHARED_EMERGENCY_STOP_MANAGER
    with _MANAGER_LOCK:
        if _SHARED_EMERGENCY_STOP_MANAGER is not None:
            _SHARED_EMERGENCY_STOP_MANAGER.reset_emergency_stop(force=True)
            _SHARED_EMERGENCY_STOP_MANAGER = None


# ==============================================================================
# Global Windows Hotkey Listener (Ctrl + Alt + Escape)
# ==============================================================================

class GlobalHotkeyListener:
    """Background listener using native Win32 RegisterHotKey and a thread message loop."""

    def __init__(
        self,
        manager: Optional[EmergencyStopManager] = None,
        hotkey_id: int = HOTKEY_ID_EMERGENCY_STOP,
        modifiers: int = MOD_CONTROL | MOD_ALT,
        vk_code: int = VK_ESCAPE,
    ) -> None:
        self.manager = manager or get_shared_emergency_stop_manager()
        self.hotkey_id = hotkey_id
        self.modifiers = modifiers
        self.vk_code = vk_code

        self._thread: Optional[threading.Thread] = None
        self._thread_id: Optional[int] = None
        self._running: bool = False
        self._is_registered: bool = False
        self._registration_error: Optional[str] = None
        self._ready_event = threading.Event()
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return self._running and (self._thread is not None and self._thread.is_alive())

    @property
    def is_registered(self) -> bool:
        return self._is_registered

    @property
    def registration_error(self) -> Optional[str]:
        return self._registration_error

    def start(self, timeout: float = 2.0) -> bool:
        """Start the background hotkey listener thread and register the global hotkey."""
        with self._lock:
            if self.is_running:
                logger.info("Global hotkey listener is already running.")
                return True

            self._ready_event.clear()
            self._running = True
            self._thread = threading.Thread(
                target=self._message_loop,
                name="VoxPilot-EmergencyHotkeyListener",
                daemon=True,
            )
            self._thread.start()

        # Wait for thread to register hotkey
        ready = self._ready_event.wait(timeout=timeout)
        if not ready:
            logger.warning("Global hotkey listener initialization timed out.")
            return False

        return self._is_registered

    def stop(self, timeout: float = 2.0) -> None:
        """Cleanly post WM_QUIT to the listener message loop and unregister hotkey."""
        with self._lock:
            if not self._running or not self._thread:
                return

            self._running = False
            target_thread_id = self._thread_id
            target_thread = self._thread

        if target_thread_id:
            try:
                user32 = ctypes.windll.user32
                WM_QUIT = 0x0012
                user32.PostThreadMessageW(target_thread_id, WM_QUIT, 0, 0)
            except Exception as err:
                logger.debug(f"Error posting WM_QUIT to hotkey thread: {err}")

        if target_thread and target_thread.is_alive():
            target_thread.join(timeout=timeout)

        with self._lock:
            self._thread = None
            self._thread_id = None
            self._is_registered = False

        logger.info("Global hotkey listener stopped cleanly.")

    def _message_loop(self) -> None:
        """Dedicated Windows message loop for processing WM_HOTKEY events."""
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32

        self._thread_id = kernel32.GetCurrentThreadId()

        # Force creation of message queue for this thread
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0x0000)  # PM_NOREMOVE

        # Register global hotkey
        try:
            reg_result = user32.RegisterHotKey(
                None,
                self.hotkey_id,
                self.modifiers | MOD_NOREPEAT,
                self.vk_code,
            )
            if not reg_result:
                # Fallback without MOD_NOREPEAT (older Windows versions)
                reg_result = user32.RegisterHotKey(
                    None,
                    self.hotkey_id,
                    self.modifiers,
                    self.vk_code,
                )

            if reg_result:
                self._is_registered = True
                self._registration_error = None
                logger.info("Global emergency stop hotkey registered: Ctrl + Alt + Escape")
            else:
                err_code = kernel32.GetLastError()
                self._is_registered = False
                self._registration_error = f"WinError {err_code}: Could not register hotkey (already in use or restricted)."
                logger.warning(
                    f"Failed to register global hotkey Ctrl+Alt+Escape ({self._registration_error}). "
                    "Another application may have reserved this shortcut. API emergency stop endpoints remain active."
                )
        except Exception as exc:
            self._is_registered = False
            self._registration_error = str(exc)
            logger.error(f"Exception registering global hotkey: {exc}", exc_info=True)
        finally:
            self._ready_event.set()

        if not self._is_registered:
            self._running = False
            return

        # Message dispatch loop
        try:
            WM_HOTKEY = 0x0312
            while self._running:
                bRet = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if bRet == 0 or bRet == -1:  # WM_QUIT or error
                    break

                if msg.message == WM_HOTKEY and msg.wParam == self.hotkey_id:
                    logger.critical("GLOBAL EMERGENCY HOTKEY DETECTED: Ctrl + Alt + Escape pressed!")
                    self.manager.trigger_emergency_stop(
                        reason="Global emergency hotkey Ctrl + Alt + Escape pressed by user.",
                        source="hotkey",
                    )

                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as loop_err:
            logger.error(f"Error in hotkey message loop: {loop_err}", exc_info=True)
        finally:
            try:
                user32.UnregisterHotKey(None, self.hotkey_id)
                logger.info("Unregistered global emergency stop hotkey.")
            except Exception as unreg_err:
                logger.debug(f"Error unregistering hotkey: {unreg_err}")
            self._is_registered = False
            self._running = False


# Global Hotkey Listener Singleton
_SHARED_HOTKEY_LISTENER: Optional[GlobalHotkeyListener] = None
_LISTENER_LOCK = threading.Lock()


def start_global_hotkey_listener() -> Optional[GlobalHotkeyListener]:
    """Start the shared global hotkey listener instance if not already running."""
    global _SHARED_HOTKEY_LISTENER
    with _LISTENER_LOCK:
        if _SHARED_HOTKEY_LISTENER is None:
            _SHARED_HOTKEY_LISTENER = GlobalHotkeyListener()
        if not _SHARED_HOTKEY_LISTENER.is_running:
            _SHARED_HOTKEY_LISTENER.start()
        return _SHARED_HOTKEY_LISTENER


def stop_global_hotkey_listener() -> None:
    """Stop the shared global hotkey listener instance."""
    global _SHARED_HOTKEY_LISTENER
    with _LISTENER_LOCK:
        if _SHARED_HOTKEY_LISTENER is not None:
            _SHARED_HOTKEY_LISTENER.stop()
            _SHARED_HOTKEY_LISTENER = None

