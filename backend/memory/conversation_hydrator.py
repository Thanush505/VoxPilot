"""
VoxPilot - Conversation Context Persistence & Hydration
Saves and restores per-conversation short-term context (turns, active window,
recent files, recent URLs, recent application) to/from SQLite cleanly across restarts
and conversation switches while maintaining strict per-conversation isolation.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from backend.database.models import scrub_text_secrets
from backend.database.repository import DatabaseRepository
from backend.memory.context_manager import (
    ConversationContext,
    ConversationContextManager,
    ConversationTurn,
)

logger = logging.getLogger(__name__)


class ConversationHydrator:
    """Persists and restores isolated ConversationContext state via SQLite."""

    def __init__(self, repository: Optional[DatabaseRepository] = None):
        self.repository = repository or DatabaseRepository()

    def save_context(
        self,
        conversation_id: str,
        context: ConversationContext,
        profile_id: str = "default",
    ) -> bool:
        """Persist short-term conversation context to ConversationRecord.active_context_json."""
        if not conversation_id or not isinstance(conversation_id, str) or context is None:
            return False
        try:
            payload = context.to_dict()
            self.repository.save_conversation_context_sync(
                conversation_id=conversation_id.strip(),
                context_data=payload,
                profile_id=profile_id,
            )
            return True
        except Exception as err:
            logger.warning(f"Failed saving conversation context for '{conversation_id}': {err}")
            return False

    def hydrate_context(
        self,
        conversation_id: str,
        context_manager: ConversationContextManager,
        revalidate_os_handles: bool = True,
        force_reload: bool = False,
        profile_id: Optional[str] = None,
    ) -> ConversationContext:
        """Restore isolated context for conversation_id from SQLite safely.

        Falls back gracefully if persisted JSON is missing or corrupt, reconstructs
        recent turns from ExecutionRecord history when available, and optionally
        revalidates OS window handles and file existence.
        """
        if not conversation_id or not isinstance(conversation_id, str):
            raise ValueError("A valid non-empty conversation_id string is required.")

        clean_id = conversation_id.strip()
        if not force_reload and context_manager.has_context(clean_id):
            return context_manager.get_context(clean_id)

        ctx = ConversationContext(conversation_id=clean_id)
        restored_from_json = False

        try:
            record = self.repository.get_conversation_sync(clean_id, profile_id=profile_id)
        except Exception as err:
            logger.warning(f"Failed querying conversation '{clean_id}' for hydration: {err}")
            record = None

        if record is not None and record.active_context_json:
            try:
                parsed = json.loads(record.active_context_json)
                if isinstance(parsed, dict):
                    # Enforce strict isolation: only load data belonging to clean_id (or unlabeled legacy)
                    stored_cid = parsed.get("conversation_id")
                    if not stored_cid or stored_cid == clean_id:
                        ctx.load_from_dict(parsed, max_turns=context_manager.max_history_turns)
                        restored_from_json = True
                    else:
                        logger.warning(
                            f"Ignoring mismatched conversation_id '{stored_cid}' while hydrating '{clean_id}'."
                        )
                else:
                    logger.warning(
                        f"Ignoring non-object active_context_json for conversation '{clean_id}'."
                    )
            except Exception as parse_err:
                logger.warning(
                    f"Corrupt active_context_json ignored for conversation '{clean_id}': {parse_err}"
                )

        # Fallback: reconstruct turns from stored ExecutionRecord history for older schemas
        if not restored_from_json or not ctx.turns:
            try:
                exec_records = self.repository.list_executions_for_conversation_sync(
                    clean_id,
                    limit=context_manager.max_history_turns,
                    profile_id=profile_id,
                )
                for idx, ex in enumerate(exec_records):
                    plan_items = ex.get_plan()
                    plan_summary = [
                        f"{s.get('action')}: {s.get('description', '')}"
                        for s in plan_items
                        if isinstance(s, dict)
                    ]
                    completed_actions = [
                        str(s.get("action", ""))
                        for s in plan_items
                        if isinstance(s, dict) and ex.execution_status == "completed"
                    ]
                    ctx.add_turn(
                        ConversationTurn(
                            turn_id=f"turn_{idx + 1}",
                            user_input=scrub_text_secrets(ex.user_input or ""),
                            intent=ex.intent or "general_conversation",
                            plan_summary=plan_summary,
                            completed_actions=completed_actions,
                            response=scrub_text_secrets(ex.response or ""),
                            status=ex.execution_status or "completed",
                            timestamp=(
                                ex.created_at.timestamp()
                                if getattr(ex, "created_at", None) is not None
                                else ctx.created_at
                            ),
                        ),
                        max_turns=context_manager.max_history_turns,
                    )
                    # Also infer recent_application/files from execution plan steps if empty
                    for step in plan_items:
                        if isinstance(step, dict):
                            ctx.update_entities_from_step(
                                step.get("action", ""),
                                step.get("parameters", {}) if isinstance(step.get("parameters"), dict) else {},
                            )
            except Exception as exec_err:
                logger.debug(f"Could not reconstruct turns from ExecutionRecord for '{clean_id}': {exec_err}")

        context_manager.set_context(clean_id, ctx)

        if revalidate_os_handles:
            if ctx.active_window:
                context_manager.revalidate_target_window(clean_id)
            if ctx.recent_files:
                context_manager.revalidate_target_files(clean_id)

        return ctx


_SHARED_HYDRATOR: Optional[ConversationHydrator] = None


def get_shared_conversation_hydrator() -> ConversationHydrator:
    """Return shared ConversationHydrator singleton."""
    global _SHARED_HYDRATOR
    if _SHARED_HYDRATOR is None:
        _SHARED_HYDRATOR = ConversationHydrator()
    return _SHARED_HYDRATOR


def reset_shared_conversation_hydrator() -> None:
    """Reset shared ConversationHydrator singleton."""
    global _SHARED_HYDRATOR
    _SHARED_HYDRATOR = None
