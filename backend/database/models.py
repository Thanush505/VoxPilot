"""
VoxPilot - Database Models
Defines SQLModel persistent schema for conversations, executions, and execution steps.
Excludes transient credentials, confirmation tokens, and sensitive contents.
"""

import json
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from sqlmodel import SQLModel, Field, Relationship


# ==============================================================================
# Sanitization Utilities
# ==============================================================================

SENSITIVE_KEYS = {
    "token",
    "confirmation_token",
    "password",
    "secret",
    "api_key",
    "auth",
    "credential",
    "credentials",
}


def sanitize_dict_for_storage(data: Any, max_string_len: int = 500) -> Any:
    """Recursively sanitize data before serializing to database.
    Strips confirmation tokens, passwords, and excessively large strings.
    """
    if isinstance(data, dict):
        cleaned = {}
        for k, v in data.items():
            k_lower = str(k).lower()
            if any(s in k_lower for s in SENSITIVE_KEYS):
                continue
            cleaned[k] = sanitize_dict_for_storage(v, max_string_len)
        return cleaned
    elif isinstance(data, list):
        return [sanitize_dict_for_storage(item, max_string_len) for item in data]
    elif isinstance(data, str):
        if len(data) > max_string_len:
            return data[:max_string_len] + "... [truncated]"
        return data
    return data


def serialize_sanitized_json(data: Any) -> Optional[str]:
    """Serialize data to JSON after stripping credentials and tokens."""
    if data is None:
        return None
    cleaned = sanitize_dict_for_storage(data)
    try:
        return json.dumps(cleaned, default=str)
    except Exception:
        return "{}"


# ==============================================================================
# Persistent SQLModel Entities
# ==============================================================================

class ExecutionStepRecord(SQLModel, table=True):
    """Persistent audit record of a single plan step executed by ToolExecutor."""
    step_id: str = Field(primary_key=True, index=True)
    execution_id: str = Field(foreign_key="executionrecord.execution_id", index=True)
    step_index: int = Field(index=True)
    action: str = Field(index=True)
    parameters_json: Optional[str] = Field(default=None)
    execution_status: str = Field(index=True)
    result_summary: Optional[str] = Field(default=None)
    error: Optional[str] = Field(default=None)
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = Field(default=None)

    execution: Optional["ExecutionRecord"] = Relationship(back_populates="steps")

    def get_parameters(self) -> Dict[str, Any]:
        if not self.parameters_json:
            return {}
        try:
            return json.loads(self.parameters_json)
        except Exception:
            return {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "execution_id": self.execution_id,
            "step_index": self.step_index,
            "action": self.action,
            "parameters": self.get_parameters(),
            "execution_status": self.execution_status,
            "result_summary": self.result_summary,
            "error": self.error,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


class ExecutionRecord(SQLModel, table=True):
    """Persistent audit record of an agent execution within a conversation."""
    execution_id: str = Field(primary_key=True, index=True)
    conversation_id: str = Field(foreign_key="conversationrecord.conversation_id", index=True)
    user_input: str = Field(nullable=False)
    intent: Optional[str] = Field(default=None)
    execution_status: str = Field(index=True)
    plan_json: Optional[str] = Field(default=None)
    current_step: int = Field(default=0)
    response: Optional[str] = Field(default=None)
    error: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = Field(default=None)

    conversation: Optional["ConversationRecord"] = Relationship(back_populates="executions")
    steps: List[ExecutionStepRecord] = Relationship(
        back_populates="execution",
        sa_relationship_kwargs={"cascade": "all, delete-orphan", "lazy": "selectin"},
    )

    def get_plan(self) -> List[Dict[str, Any]]:
        if not self.plan_json:
            return []
        try:
            return json.loads(self.plan_json)
        except Exception:
            return []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "conversation_id": self.conversation_id,
            "user_input": self.user_input,
            "intent": self.intent,
            "execution_status": self.execution_status,
            "plan": self.get_plan(),
            "current_step": self.current_step,
            "response": self.response,
            "error": self.error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


class ConversationRecord(SQLModel, table=True):
    """Persistent record of an isolated conversation session."""
    conversation_id: str = Field(primary_key=True, index=True)
    title: Optional[str] = Field(default=None)
    summary: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    executions: List[ExecutionRecord] = Relationship(
        back_populates="conversation",
        sa_relationship_kwargs={"cascade": "all, delete-orphan", "lazy": "selectin"},
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "title": self.title,
            "summary": self.summary,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
