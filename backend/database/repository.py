"""
VoxPilot - Database Repository
Provides asynchronous repository operations for conversations, executions, and execution steps.
Guarantees transactional integrity and ensures transient credentials/tokens are never stored.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from sqlmodel import SQLModel, select, Session
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.database.models import (
    ConversationRecord,
    ExecutionRecord,
    ExecutionStepRecord,
    MemoryRecord,
    normalize_plan_steps,
    serialize_sanitized_json,
)
from backend.database.connection import get_sessionmaker, get_sync_engine, run_migrations_sync

logger = logging.getLogger(__name__)


class DatabaseRepository:
    """Repository managing persistent conversations and execution history with async & sync methods."""

    def __init__(self, session_maker=None, sync_engine=None):
        self._sync_engine = sync_engine or get_sync_engine()
        self._session_maker = session_maker or get_sessionmaker()
        try:
            run_migrations_sync(self._sync_engine)
        except Exception as err:
            logger.debug(f"Schema auto-initialization skipped: {err}")

    # ==========================================================================
    # Conversation Operations
    # ==========================================================================

    async def create_or_get_conversation(
        self,
        conversation_id: str,
        title: Optional[str] = None,
        summary: Optional[str] = None,
        profile_id: str = "default",
    ) -> ConversationRecord:
        """Retrieve existing conversation or create a new isolated conversation record."""
        clean_profile = (profile_id or "default").strip() or "default"
        async with self._session_maker() as session:
            stmt = select(ConversationRecord).where(ConversationRecord.conversation_id == conversation_id)
            result = await session.exec(stmt)
            conversation = result.first()

            now = datetime.now(timezone.utc)
            if conversation is None:
                conversation = ConversationRecord(
                    conversation_id=conversation_id,
                    profile_id=clean_profile,
                    title=title or f"Conversation {conversation_id[:8]}",
                    summary=summary,
                    created_at=now,
                    updated_at=now,
                )
                session.add(conversation)
                await session.commit()
                await session.refresh(conversation)
                logger.debug(f"Created conversation record: {conversation_id}")
            else:
                if (conversation.profile_id or "default") != clean_profile:
                    raise ValueError(
                        f"Conversation '{conversation_id}' belongs to another profile."
                    )
                if title:
                    conversation.title = title
                if summary:
                    conversation.summary = summary
                conversation.updated_at = now
                session.add(conversation)
                await session.commit()
                await session.refresh(conversation)

            return conversation

    async def get_conversation(
        self,
        conversation_id: str,
        profile_id: Optional[str] = None,
    ) -> Optional[ConversationRecord]:
        """Fetch conversation record by id, optionally enforcing profile ownership."""
        async with self._session_maker() as session:
            stmt = select(ConversationRecord).where(ConversationRecord.conversation_id == conversation_id)
            if profile_id is not None:
                stmt = stmt.where(ConversationRecord.profile_id == ((profile_id or "default").strip() or "default"))
            result = await session.exec(stmt)
            return result.first()

    async def list_conversations(
        self,
        limit: int = 50,
        offset: int = 0,
        profile_id: Optional[str] = None,
    ) -> List[ConversationRecord]:
        """List conversations ordered by most recently updated, optionally filtered by profile."""
        async with self._session_maker() as session:
            stmt = select(ConversationRecord)
            if profile_id is not None:
                stmt = stmt.where(ConversationRecord.profile_id == ((profile_id or "default").strip() or "default"))
            stmt = (
                stmt.order_by(ConversationRecord.updated_at.desc())
                .offset(offset)
                .limit(limit)
            )
            result = await session.exec(stmt)
            return list(result.all())

    # ==========================================================================
    # Execution Operations
    # ==========================================================================

    async def save_execution(self, state: Dict[str, Any]) -> ExecutionRecord:
        """Persist or update an agent execution session and its completed steps.
        Automatically strips confirmation tokens and transient credentials.
        """
        exec_id = state.get("execution_id")
        if not exec_id:
            exec_id = f"exec_{uuid.uuid4().hex[:12]}"

        conv_id = state.get("conversation_id")
        if not conv_id:
            conv_id = f"conv_{exec_id}"

        clean_profile = (state.get("profile_id") or "default").strip() or "default"

        # Ensure conversation exists under the active profile
        await self.create_or_get_conversation(conv_id, profile_id=clean_profile)

        user_input = state.get("user_input", "")
        intent = state.get("intent")
        status = state.get("execution_status", "started")
        plan = state.get("plan", [])
        normalized_plan = normalize_plan_steps(plan)
        current_step = state.get("current_step", 0)
        response = state.get("response")
        error = state.get("error")

        now = datetime.now(timezone.utc)
        completed_at = now if status in ("completed", "failed", "cancelled") else None

        # Sanitize normalized plan to strip any tokens without mutating caller state
        sanitized_plan_json = serialize_sanitized_json(normalized_plan)

        async with self._session_maker() as session:
            stmt = select(ExecutionRecord).where(ExecutionRecord.execution_id == exec_id)
            result = await session.exec(stmt)
            record = result.first()

            if record is None:
                record = ExecutionRecord(
                    execution_id=exec_id,
                    conversation_id=conv_id,
                    profile_id=clean_profile,
                    user_input=user_input,
                    intent=intent,
                    execution_status=status,
                    plan_json=sanitized_plan_json,
                    current_step=current_step,
                    response=response,
                    error=error,
                    created_at=now,
                    updated_at=now,
                    completed_at=completed_at,
                )
                session.add(record)
            else:
                record.execution_status = status
                record.plan_json = sanitized_plan_json
                record.current_step = current_step
                record.response = response
                record.error = error
                record.updated_at = now
                if completed_at:
                    record.completed_at = completed_at
                session.add(record)

            await session.commit()
            await session.refresh(record)

        # Persist individual step execution records if present
        exec_results = state.get("execution_results", [])
        if exec_results and isinstance(exec_results, list):
            for res in exec_results:
                if isinstance(res, dict):
                    step_idx = res.get("step_index", 0)
                    action = res.get("action", "unknown")
                    step_status = res.get("status", "completed" if res.get("success") else "failed")
                    msg = res.get("message")
                    err = res.get("error")
                    # Match with plan parameters if available
                    params = {}
                    if step_idx < len(plan) and isinstance(plan[step_idx], dict):
                        params = plan[step_idx].get("parameters", {})

                    await self.record_execution_step(
                        execution_id=exec_id,
                        step_index=step_idx,
                        action=action,
                        parameters=params,
                        status=step_status,
                        result_summary=msg,
                        error=err,
                    )

        logger.debug(f"Saved execution record: {exec_id} (status: {status})")
        return record

    async def get_execution(
        self,
        execution_id: str,
        profile_id: Optional[str] = None,
    ) -> Optional[ExecutionRecord]:
        """Fetch execution record by ID, optionally enforcing profile ownership."""
        async with self._session_maker() as session:
            stmt = select(ExecutionRecord).where(ExecutionRecord.execution_id == execution_id)
            if profile_id is not None:
                stmt = stmt.where(ExecutionRecord.profile_id == ((profile_id or "default").strip() or "default"))
            result = await session.exec(stmt)
            return result.first()

    async def list_executions_for_conversation(
        self,
        conversation_id: str,
        limit: int = 50,
        profile_id: Optional[str] = None,
    ) -> List[ExecutionRecord]:
        """List all execution records belonging to a conversation ordered chronologically."""
        async with self._session_maker() as session:
            stmt = select(ExecutionRecord).where(ExecutionRecord.conversation_id == conversation_id)
            if profile_id is not None:
                stmt = stmt.where(ExecutionRecord.profile_id == ((profile_id or "default").strip() or "default"))
            stmt = stmt.order_by(ExecutionRecord.created_at.desc()).limit(limit)
            result = await session.exec(stmt)
            return list(reversed(list(result.all())))

    # ==========================================================================
    # Execution Step Operations
    # ==========================================================================

    async def record_execution_step(
        self,
        execution_id: str,
        step_index: int,
        action: str,
        parameters: Optional[Dict[str, Any]] = None,
        status: str = "completed",
        result_summary: Optional[str] = None,
        error: Optional[str] = None,
    ) -> ExecutionStepRecord:
        """Record a single plan step outcome in the database."""
        step_id = f"{execution_id}_s{step_index}"
        sanitized_params_json = serialize_sanitized_json(parameters or {})
        now = datetime.now(timezone.utc)

        async with self._session_maker() as session:
            stmt = select(ExecutionStepRecord).where(ExecutionStepRecord.step_id == step_id)
            result = await session.exec(stmt)
            step_record = result.first()

            if step_record is None:
                step_record = ExecutionStepRecord(
                    step_id=step_id,
                    execution_id=execution_id,
                    step_index=step_index,
                    action=action,
                    parameters_json=sanitized_params_json,
                    execution_status=status,
                    result_summary=result_summary,
                    error=error,
                    started_at=now,
                    completed_at=now if status in ("completed", "failed", "cancelled") else None,
                )
                session.add(step_record)
            else:
                step_record.execution_status = status
                step_record.result_summary = result_summary
                step_record.error = error
                if status in ("completed", "failed", "cancelled"):
                    step_record.completed_at = now
                session.add(step_record)

            await session.commit()
            await session.refresh(step_record)

        return step_record

    async def get_execution_steps(self, execution_id: str) -> List[ExecutionStepRecord]:
        """Retrieve all step records for an execution ordered by sequence index."""
        async with self._session_maker() as session:
            stmt = (
                select(ExecutionStepRecord)
                .where(ExecutionStepRecord.execution_id == execution_id)
                .order_by(ExecutionStepRecord.step_index.asc())
            )
            result = await session.exec(stmt)
            return list(result.all())

    # ==========================================================================
    # Synchronous Execution Helpers (for Background / Voice callers)
    # ==========================================================================

    def create_or_get_conversation_sync(
        self,
        conversation_id: str,
        title: Optional[str] = None,
        summary: Optional[str] = None,
        profile_id: str = "default",
    ) -> ConversationRecord:
        """Synchronously create or fetch conversation record."""
        clean_profile = (profile_id or "default").strip() or "default"
        with Session(self._sync_engine) as session:
            stmt = select(ConversationRecord).where(ConversationRecord.conversation_id == conversation_id)
            conversation = session.exec(stmt).first()
            now = datetime.now(timezone.utc)
            if conversation is None:
                conversation = ConversationRecord(
                    conversation_id=conversation_id,
                    profile_id=clean_profile,
                    title=title or f"Conversation {conversation_id[:8]}",
                    summary=summary,
                    created_at=now,
                    updated_at=now,
                )
                session.add(conversation)
                session.commit()
                session.refresh(conversation)
            else:
                if (conversation.profile_id or "default") != clean_profile:
                    raise ValueError(
                        f"Conversation '{conversation_id}' belongs to another profile."
                    )
                if title:
                    conversation.title = title
                if summary:
                    conversation.summary = summary
                conversation.updated_at = now
                session.add(conversation)
                session.commit()
                session.refresh(conversation)
            return conversation

    def save_execution_sync(self, state: Dict[str, Any]) -> ExecutionRecord:
        """Synchronously persist execution record and step results."""
        exec_id = state.get("execution_id") or f"exec_{uuid.uuid4().hex[:12]}"
        conv_id = state.get("conversation_id") or f"conv_{exec_id}"
        clean_profile = (state.get("profile_id") or "default").strip() or "default"

        self.create_or_get_conversation_sync(conv_id, profile_id=clean_profile)

        user_input = state.get("user_input", "")
        intent = state.get("intent")
        status = state.get("execution_status", "started")
        plan = state.get("plan", [])
        normalized_plan = normalize_plan_steps(plan)
        current_step = state.get("current_step", 0)
        response = state.get("response")
        error = state.get("error")

        now = datetime.now(timezone.utc)
        completed_at = now if status in ("completed", "failed", "cancelled") else None
        sanitized_plan_json = serialize_sanitized_json(normalized_plan)

        with Session(self._sync_engine) as session:
            stmt = select(ExecutionRecord).where(ExecutionRecord.execution_id == exec_id)
            record = session.exec(stmt).first()

            if record is None:
                record = ExecutionRecord(
                    execution_id=exec_id,
                    conversation_id=conv_id,
                    profile_id=clean_profile,
                    user_input=user_input,
                    intent=intent,
                    execution_status=status,
                    plan_json=sanitized_plan_json,
                    current_step=current_step,
                    response=response,
                    error=error,
                    created_at=now,
                    updated_at=now,
                    completed_at=completed_at,
                )
                session.add(record)
            else:
                record.execution_status = status
                record.plan_json = sanitized_plan_json
                record.current_step = current_step
                record.response = response
                record.error = error
                record.updated_at = now
                if completed_at:
                    record.completed_at = completed_at
                session.add(record)

            session.commit()
            session.refresh(record)

        # Persist steps
        exec_results = state.get("execution_results", [])
        if exec_results and isinstance(exec_results, list):
            for res in exec_results:
                if isinstance(res, dict):
                    step_idx = res.get("step_index", 0)
                    action = res.get("action", "unknown")
                    step_status = res.get("status", "completed" if res.get("success") else "failed")
                    msg = res.get("message")
                    err = res.get("error")
                    params = {}
                    if step_idx < len(plan) and isinstance(plan[step_idx], dict):
                        params = plan[step_idx].get("parameters", {})

                    self.record_execution_step_sync(
                        execution_id=exec_id,
                        step_index=step_idx,
                        action=action,
                        parameters=params,
                        status=step_status,
                        result_summary=msg,
                        error=err,
                    )

        return record

    def record_execution_step_sync(
        self,
        execution_id: str,
        step_index: int,
        action: str,
        parameters: Optional[Dict[str, Any]] = None,
        status: str = "completed",
        result_summary: Optional[str] = None,
        error: Optional[str] = None,
    ) -> ExecutionStepRecord:
        """Synchronously record a step result."""
        step_id = f"{execution_id}_s{step_index}"
        sanitized_params_json = serialize_sanitized_json(parameters or {})
        now = datetime.now(timezone.utc)

        with Session(self._sync_engine) as session:
            stmt = select(ExecutionStepRecord).where(ExecutionStepRecord.step_id == step_id)
            step_record = session.exec(stmt).first()
            if step_record is None:
                step_record = ExecutionStepRecord(
                    step_id=step_id,
                    execution_id=execution_id,
                    step_index=step_index,
                    action=action,
                    parameters_json=sanitized_params_json,
                    execution_status=status,
                    result_summary=result_summary,
                    error=error,
                    started_at=now,
                    completed_at=now if status in ("completed", "failed", "cancelled") else None,
                )
                session.add(step_record)
            else:
                step_record.execution_status = status
                step_record.result_summary = result_summary
                step_record.error = error
                if status in ("completed", "failed", "cancelled"):
                    step_record.completed_at = now
                session.add(step_record)
            session.commit()
            session.refresh(step_record)
        return step_record

    def get_execution_sync(
        self,
        execution_id: str,
        profile_id: Optional[str] = None,
    ) -> Optional[ExecutionRecord]:
        """Synchronously retrieve execution record by ID, optionally enforcing profile ownership."""
        with Session(self._sync_engine) as session:
            stmt = select(ExecutionRecord).where(ExecutionRecord.execution_id == execution_id)
            if profile_id is not None:
                stmt = stmt.where(ExecutionRecord.profile_id == ((profile_id or "default").strip() or "default"))
            return session.exec(stmt).first()

    def get_conversation_sync(
        self,
        conversation_id: str,
        profile_id: Optional[str] = None,
    ) -> Optional[ConversationRecord]:
        """Synchronously retrieve conversation record by ID, optionally enforcing profile ownership."""
        with Session(self._sync_engine) as session:
            stmt = select(ConversationRecord).where(ConversationRecord.conversation_id == conversation_id)
            if profile_id is not None:
                stmt = stmt.where(ConversationRecord.profile_id == ((profile_id or "default").strip() or "default"))
            return session.exec(stmt).first()

    def list_executions_for_conversation_sync(
        self,
        conversation_id: str,
        limit: int = 20,
        profile_id: Optional[str] = None,
    ) -> List[ExecutionRecord]:
        """Synchronously list execution records belonging to a conversation chronologically."""
        with Session(self._sync_engine) as session:
            stmt = select(ExecutionRecord).where(ExecutionRecord.conversation_id == conversation_id)
            if profile_id is not None:
                stmt = stmt.where(ExecutionRecord.profile_id == ((profile_id or "default").strip() or "default"))
            stmt = stmt.order_by(ExecutionRecord.created_at.desc()).limit(limit)
            return list(reversed(list(session.exec(stmt).all())))

    def save_conversation_context_sync(
        self,
        conversation_id: str,
        context_data: Dict[str, Any],
        profile_id: str = "default",
    ) -> ConversationRecord:
        """Synchronously sanitize and persist active conversation context JSON."""
        clean_profile = (profile_id or "default").strip() or "default"
        sanitized_json = serialize_sanitized_json(context_data)
        now = datetime.now(timezone.utc)
        with Session(self._sync_engine) as session:
            stmt = select(ConversationRecord).where(ConversationRecord.conversation_id == conversation_id)
            conversation = session.exec(stmt).first()
            if conversation is None:
                conversation = ConversationRecord(
                    conversation_id=conversation_id,
                    profile_id=clean_profile,
                    title=f"Conversation {conversation_id[:8]}",
                    active_context_json=sanitized_json,
                    created_at=now,
                    updated_at=now,
                )
            else:
                if (conversation.profile_id or "default") != clean_profile:
                    raise ValueError(
                        f"Conversation '{conversation_id}' belongs to another profile."
                    )
                conversation.active_context_json = sanitized_json
                conversation.updated_at = now
            session.add(conversation)
            session.commit()
            session.refresh(conversation)
            return conversation
