"""
VoxPilot - Short-Term Conversational Context and Memory Subsystem
Maintains isolated, turn-bounded conversational context across turns within a conversation session.
Resolves pronoun references ("it", "that window", "the first file") and tracks active entities
(windows, files, URLs) with mandatory target revalidation safeguards.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List, Callable

logger = logging.getLogger(__name__)


# ==============================================================================
# Data Models
# ==============================================================================

@dataclass
class ConversationTurn:
    """Structured record of a single conversational interaction turn."""
    turn_id: str
    user_input: str
    intent: str
    plan_summary: List[str]
    completed_actions: List[str]
    response: str
    status: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class ConversationContext:
    """Isolated short-term conversational context for a single conversation session."""
    conversation_id: str
    active_window: Optional[Dict[str, Any]] = None
    recent_files: List[str] = field(default_factory=list)
    recent_urls: List[str] = field(default_factory=list)
    recent_application: Optional[str] = None
    turns: List[ConversationTurn] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @property
    def active_window_hwnd(self) -> Optional[int]:
        return self.active_window.get("hwnd") if self.active_window else None

    @property
    def active_window_title(self) -> Optional[str]:
        return self.active_window.get("title") if self.active_window else None

    def add_turn(self, turn: ConversationTurn, max_turns: int = 5) -> None:
        """Add a turn and maintain bounded turn history."""
        self.turns.append(turn)
        if len(self.turns) > max_turns:
            self.turns = self.turns[-max_turns:]
        self.updated_at = time.time()

    def update_entities_from_step(
        self,
        action: str,
        parameters: Dict[str, Any],
        result_data: Optional[Dict[str, Any]] = None,
        result_message: Optional[str] = None,
    ) -> None:
        """Extract and update tracked entities (windows, files, URLs) from successful tool results."""
        data = result_data or {}

        # 1. Application Launch or Window Management
        if action in ("launch_application", "open_application"):
            app_name = parameters.get("application_name")
            if app_name:
                self.recent_application = str(app_name).lower().strip()
            # If data contains window info, track it
            if data.get("hwnd"):
                self.active_window = {
                    "hwnd": int(data["hwnd"]),
                    "app_name": self.recent_application,
                    "title": data.get("title", ""),
                }
        elif action in ("manage_window", "find_windows"):
            if data.get("hwnd"):
                self.active_window = {
                    "hwnd": int(data["hwnd"]),
                    "app_name": data.get("app_name", self.recent_application),
                    "title": data.get("title", ""),
                }
            elif data.get("windows") and len(data["windows"]) > 0:
                first_win = data["windows"][0]
                if isinstance(first_win, dict) and first_win.get("hwnd"):
                    self.active_window = {
                        "hwnd": int(first_win["hwnd"]),
                        "app_name": first_win.get("app_name", self.recent_application),
                        "title": first_win.get("title", ""),
                    }

        # 2. Filesystem Operations
        if action in ("search_files", "list_directory"):
            matches = data.get("matches") or data.get("files") or data.get("items")
            if matches and isinstance(matches, list):
                # Filter for strings and deduplicate
                clean_paths = [str(p) for p in matches if isinstance(p, (str, dict))]
                # If dicts with 'path' key
                normalized_paths = []
                for p in clean_paths:
                    if isinstance(p, dict) and "path" in p:
                        normalized_paths.append(str(p["path"]))
                    else:
                        normalized_paths.append(str(p))
                if normalized_paths:
                    self.recent_files = normalized_paths[:10]
        elif action in ("create_file", "copy_item", "move_item", "get_file_metadata"):
            file_path = parameters.get("file_path") or parameters.get("destination_path") or parameters.get("path")
            if file_path:
                norm_path = str(file_path).strip()
                if norm_path not in self.recent_files:
                    self.recent_files.insert(0, norm_path)
                self.recent_files = self.recent_files[:10]

        # 3. Browser Operations
        if action in ("navigate_url", "start_browser", "get_page_info", "search_web"):
            url = parameters.get("url") or data.get("url")
            if url:
                norm_url = str(url).strip()
                if norm_url not in self.recent_urls:
                    self.recent_urls.insert(0, norm_url)
                self.recent_urls = self.recent_urls[:5]

    def get_summary_text(self) -> str:
        """Construct concise natural language summary of active context for planner injection."""
        parts = []
        if self.recent_application:
            parts.append(f"Recent application: {self.recent_application}")
        if self.active_window:
            parts.append(
                f"Active window: '{self.active_window.get('title')}' "
                f"(app={self.active_window.get('app_name')}, hwnd={self.active_window.get('hwnd')})"
            )
        if self.recent_files:
            file_preview = ", ".join(f"[{i+1}] {f}" for i, f in enumerate(self.recent_files[:3]))
            parts.append(f"Recent files: {file_preview}")
        if self.recent_urls:
            parts.append(f"Recent URL: {self.recent_urls[0]}")
        return "; ".join(parts) if parts else "No previous context."


# ==============================================================================
# Helper Functions for Target Revalidation
# ==============================================================================

def is_window_alive_os(hwnd: int) -> bool:
    """Check if window handle is alive and valid in the Windows OS."""
    try:
        import win32gui
        return bool(win32gui.IsWindow(hwnd))
    except Exception:
        try:
            import ctypes
            return bool(ctypes.windll.user32.IsWindow(hwnd))
        except Exception:
            return False


def is_file_accessible_os(path: str) -> bool:
    """Check if file exists and is accessible on the local filesystem."""
    try:
        return os.path.exists(path)
    except Exception:
        return False


# ==============================================================================
# Context Manager
# ==============================================================================

class ConversationContextManager:
    """Thread-safe manager providing isolated short-term memory per conversation."""

    def __init__(
        self,
        max_history_turns: int = 5,
        max_turns: Optional[int] = None,
        window_validator: Optional[Callable[[int], bool]] = None,
        file_validator: Optional[Callable[[str], bool]] = None,
        revalidate_targets: bool = False,
    ):
        self._max_history_turns = max_turns if max_turns is not None else max_history_turns
        self._contexts: Dict[str, ConversationContext] = {}
        self._lock = threading.Lock()
        self._window_validator = window_validator
        self._file_validator = file_validator
        self._revalidate_targets = revalidate_targets

    def is_window_alive(self, hwnd: int) -> bool:
        """Verify whether a given window handle is alive and accessible."""
        if self._window_validator is not None:
            return self._window_validator(hwnd)
        return is_window_alive_os(hwnd)

    def is_file_accessible(self, path: str) -> bool:
        """Verify whether a given file path exists and is accessible."""
        if self._file_validator is not None:
            return self._file_validator(path)
        return is_file_accessible_os(path)

    def revalidate_target_window(self, conversation_id: str) -> bool:
        """Check active window target and evict if closed."""
        ctx = self.get_context(conversation_id)
        if not ctx.active_window:
            return False
        hwnd = ctx.active_window.get("hwnd", 0)
        if hwnd and not self.is_window_alive(hwnd):
            logger.info(f"Evicting stale window handle {hwnd} for conversation '{conversation_id}'")
            ctx.active_window = None
            return False
        return True

    def revalidate_target_files(self, conversation_id: str) -> List[str]:
        """Check recent files and evict missing or inaccessible files."""
        ctx = self.get_context(conversation_id)
        valid_files = [f for f in ctx.recent_files if self.is_file_accessible(f)]
        ctx.recent_files = valid_files
        return valid_files

    def get_context(self, conversation_id: str) -> ConversationContext:
        """Retrieve existing context or create a new empty context strictly for conversation_id."""
        if not conversation_id or not isinstance(conversation_id, str):
            raise ValueError("A valid non-empty conversation_id string is required.")

        clean_id = conversation_id.strip()
        with self._lock:
            if clean_id not in self._contexts:
                self._contexts[clean_id] = ConversationContext(conversation_id=clean_id)
            return self._contexts[clean_id]

    def record_turn(
        self,
        conversation_id: str,
        user_input: str,
        intent: str,
        plan: List[Dict[str, Any]],
        execution_results: List[Dict[str, Any]],
        completed_steps: List[Dict[str, Any]],
        response: str,
        status: str = "completed",
    ) -> None:
        """Record turn completion and update context entity state."""
        ctx = self.get_context(conversation_id)

        plan_summary = [
            f"{s.get('action')}: {s.get('description', '')}"
            for s in plan if isinstance(s, dict)
        ]
        completed_actions = [
            s.get("action", "") for s in completed_steps if isinstance(s, dict)
        ]

        turn = ConversationTurn(
            turn_id=f"turn_{len(ctx.turns) + 1}",
            user_input=user_input,
            intent=intent,
            plan_summary=plan_summary,
            completed_actions=completed_actions,
            response=response,
            status=status,
        )

        with self._lock:
            ctx.add_turn(turn, max_turns=self._max_history_turns)

            # Update entities from execution results
            for res in execution_results:
                if isinstance(res, dict):
                    action = res.get("action", "")
                    data = res.get("data")
                    msg = res.get("message")
                    idx = res.get("step_index", 0)
                    params = {}
                    if idx < len(plan) and isinstance(plan[idx], dict):
                        params = plan[idx].get("parameters", {})
                    ctx.update_entities_from_step(action, params, data, msg)

    def resolve_references(
        self,
        conversation_id: str,
        user_input: str,
        revalidate: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Inspect natural language input and resolve ambiguous pronoun references against conversation context.

        Returns:
            Dict containing resolved entities, e.g. resolved_window, resolved_file, needs_clarification.
        """
        ctx = self.get_context(conversation_id)
        lower_input = user_input.lower().strip()
        should_revalidate = self._revalidate_targets if revalidate is None else revalidate

        result: Dict[str, Any] = {
            "resolved_window": None,
            "resolved_file": None,
            "resolved_app": None,
            "needs_clarification": False,
            "clarification_message": "",
        }

        # 1. Window reference resolution ("it", "that window", "the window", "minimize it", "type Hello")
        window_pronouns = [" it", " that window", " the window", " this window", "it "]
        refers_to_window = any(p in lower_input for p in window_pronouns) or lower_input in ("minimize it", "maximize it", "close it", "restore it")

        if refers_to_window or ("type " in lower_input and not any(app in lower_input for app in ("notepad", "chrome", "vscode", "calculator"))):
            if ctx.active_window:
                hwnd = ctx.active_window.get("hwnd", 0)
                if should_revalidate and hwnd and not self.is_window_alive(hwnd):
                    stale_title = ctx.active_window.get("title", "window")
                    ctx.active_window = None
                    result["needs_clarification"] = True
                    result["clarification_message"] = f"The previous target window '{stale_title}' has closed. Which window or application would you like to interact with?"
                else:
                    result["resolved_window"] = dict(ctx.active_window)
                    result["resolved_app"] = ctx.recent_application
            elif should_revalidate:
                result["needs_clarification"] = True
                result["clarification_message"] = "No active window is currently selected. Which window or application would you like to interact with?"

        # 2. File reference resolution ("first result", "the file", "open the first one", "first resume")
        file_first_phrases = ["first result", "first file", "the first one", "first one", "1st result"]
        file_second_phrases = ["second result", "second file", "the second one", "second one", "2nd result"]
        if any(p in lower_input for p in file_first_phrases):
            if ctx.recent_files:
                candidate = ctx.recent_files[0]
                if should_revalidate and not self.is_file_accessible(candidate):
                    ctx.recent_files = [f for f in ctx.recent_files if f != candidate]
                    result["needs_clarification"] = True
                    result["clarification_message"] = f"The referenced file '{candidate}' is no longer accessible or has been deleted."
                else:
                    result["resolved_file"] = candidate
            else:
                result["needs_clarification"] = True
                result["clarification_message"] = "No recent files or search results were found in this conversation."
        elif any(p in lower_input for p in file_second_phrases):
            if len(ctx.recent_files) > 1:
                candidate = ctx.recent_files[1]
                if should_revalidate and not self.is_file_accessible(candidate):
                    ctx.recent_files = [f for f in ctx.recent_files if f != candidate]
                    result["needs_clarification"] = True
                    result["clarification_message"] = f"The referenced file '{candidate}' is no longer accessible or has been deleted."
                else:
                    result["resolved_file"] = candidate
            else:
                result["needs_clarification"] = True
                result["clarification_message"] = "No second file found in recent results."
        elif "the file" in lower_input or "open it" in lower_input:
            if len(ctx.recent_files) == 1:
                candidate = ctx.recent_files[0]
                if should_revalidate and not self.is_file_accessible(candidate):
                    ctx.recent_files = [f for f in ctx.recent_files if f != candidate]
                    result["needs_clarification"] = True
                    result["clarification_message"] = f"The referenced file '{candidate}' is no longer accessible or has been deleted."
                else:
                    result["resolved_file"] = candidate
            elif len(ctx.recent_files) > 1:
                # Ambiguity detected: do not guess!
                result["needs_clarification"] = True
                options = ", ".join(f"[{i+1}] {f}" for i, f in enumerate(ctx.recent_files[:3]))
                result["clarification_message"] = (
                    f"Multiple files were found in previous results ({options}). "
                    "Which specific file would you like to open?"
                )

        return result

    def clear_context(self, conversation_id: str) -> None:
        """Remove conversation context from active memory."""
        with self._lock:
            self._contexts.pop(conversation_id, None)

    def reset_context(self, conversation_id: str) -> None:
        """Alias for clear_context."""
        self.clear_context(conversation_id)

    def clear_all(self) -> None:
        """Clear all contexts in memory."""
        with self._lock:
            self._contexts.clear()


# Global Singleton ContextManager
_SHARED_CONTEXT_MANAGER: Optional[ConversationContextManager] = None


def get_shared_context_manager() -> ConversationContextManager:
    """Retrieve or initialize the active shared ConversationContextManager singleton."""
    global _SHARED_CONTEXT_MANAGER
    if _SHARED_CONTEXT_MANAGER is None:
        _SHARED_CONTEXT_MANAGER = ConversationContextManager()
    return _SHARED_CONTEXT_MANAGER


def reset_shared_context_manager() -> None:
    """Reset shared context manager singleton."""
    global _SHARED_CONTEXT_MANAGER
    if _SHARED_CONTEXT_MANAGER is not None:
        _SHARED_CONTEXT_MANAGER.clear_all()
        _SHARED_CONTEXT_MANAGER = None
