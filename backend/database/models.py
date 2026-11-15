"""
VoxPilot - Database Models
Defines SQLModel persistent schema for conversations, executions, and execution steps.
Excludes transient credentials, confirmation tokens, and sensitive contents.
"""

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple
from sqlmodel import SQLModel, Field, Relationship


# ==============================================================================
# Sanitization Utilities
# ==============================================================================

SENSITIVE_KEYS = {
    "token",
    "confirmation_token",
    "password",
    "passcode",
    "secret",
    "api_key",
    "apikey",
    "auth",
    "credential",
    "credentials",
    "cookie",
    "private_key",
}

_OBVIOUS_SECRET_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    (
        "private_key_block",
        re.compile(r"-----BEGIN\s+(?:[A-Z0-9]+\s+)?PRIVATE\s+KEY-----", re.IGNORECASE),
    ),
    (
        "openai_or_sk_api_key",
        re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{16,}\b"),
    ),
    (
        "github_token",
        re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"),
    ),
    (
        "github_fine_grained_token",
        re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    ),
    (
        "google_api_key",
        re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    ),
    (
        "slack_token",
        re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"),
    ),
    (
        "aws_access_key",
        re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ),
    (
        "bearer_or_jwt_token",
        re.compile(
            r"(?:\bBearer\s+[A-Za-z0-9\-._~+/]+=*|\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "database_url_with_credentials",
        re.compile(r"\b(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|amqp)://[^:\s/]+:[^@\s]+@", re.IGNORECASE),
    ),
    (
        "explicit_password_or_pin_statement",
        re.compile(
            r"\b(?:my\s+|the\s+)?(?:password|passphrase|passcode|pin\s+code|secret\s+key|api[\s_-]*key|access[\s_-]*token|refresh[\s_-]*token|session[\s_-]*cookie)\s*(?:is|=|:)\s*\S+",
            re.IGNORECASE,
        ),
    ),
    (
        "auth_header_or_cookie",
        re.compile(
            r"\b(?:authorization|x-api-key|x-goog-api-key|x-voxpilot-desktop-token|set-cookie)\s*:\s*\S+",
            re.IGNORECASE,
        ),
    ),
    (
        "ssn_pattern",
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    ),
]


def contains_obvious_secret(text: Optional[str]) -> Tuple[bool, Optional[str]]:
    """Detect passwords, API keys, bearer tokens, private keys, or credential strings in text."""
    if not text or not isinstance(text, str):
        return False, None
    for reason, pattern in _OBVIOUS_SECRET_PATTERNS:
        if pattern.search(text):
            return True, reason
    return False, None


def scrub_text_secrets(text: str) -> str:
    """Replace any obvious secret values inside free-form text with a safe placeholder."""
    if not text or not isinstance(text, str):
        return ""
    scrubbed = text
    for _, pattern in _OBVIOUS_SECRET_PATTERNS:
        scrubbed = pattern.sub("[REDACTED_SECRET]", scrubbed)
    return scrubbed


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
        scrubbed = scrub_text_secrets(data)
        if len(scrubbed) > max_string_len:
            return scrubbed[:max_string_len] + "... [truncated]"
        return scrubbed
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


def normalize_plan_steps(raw_plan: Any) -> List[Dict[str, Any]]:
    """Non-destructively normalize stored or in-memory plan steps into List[Dict[str, Any]]."""
    if raw_plan is None:
        return []
    parsed = raw_plan
    if isinstance(raw_plan, str):
        text_val = raw_plan.strip()
        if not text_val:
            return []
        try:
            parsed = json.loads(text_val)
        except Exception:
            clean_text = scrub_text_secrets(text_val)
            return [
                {
                    "step_index": 0,
                    "action": "legacy_step",
                    "description": clean_text,
                    "parameters": {},
                    "legacy_raw": clean_text,
                }
            ]

    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, (list, tuple)):
        return []

    normalized: List[Dict[str, Any]] = []
    for idx, item in enumerate(parsed):
        if item is None:
            continue
        if isinstance(item, dict):
            step_copy = dict(item)
            if "parameters" in step_copy and not isinstance(step_copy["parameters"], dict):
                step_copy["parameters"] = {"_legacy_value": step_copy["parameters"]}
            normalized.append(step_copy)
        elif isinstance(item, str):
            clean_str = scrub_text_secrets(item)
            normalized.append(
                {
                    "step_index": idx,
                    "action": "legacy_step",
                    "description": clean_str,
                    "parameters": {},
                    "legacy_raw": clean_str,
                }
            )
        else:
            clean_repr = scrub_text_secrets(str(item))
            normalized.append(
                {
                    "step_index": idx,
                    "action": "legacy_step",
                    "description": clean_repr,
                    "parameters": {},
                    "legacy_raw": clean_repr,
                }
            )
    return normalized


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
            parsed = json.loads(self.parameters_json)
            return parsed if isinstance(parsed, dict) else {"_legacy_value": parsed}
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
    profile_id: str = Field(default="default", index=True)
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
        return normalize_plan_steps(self.plan_json)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "conversation_id": self.conversation_id,
            "profile_id": self.profile_id,
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
    profile_id: str = Field(default="default", index=True)
    title: Optional[str] = Field(default=None)
    summary: Optional[str] = Field(default=None)
    active_context_json: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    executions: List[ExecutionRecord] = Relationship(
        back_populates="conversation",
        sa_relationship_kwargs={"cascade": "all, delete-orphan", "lazy": "selectin"},
    )

    def get_active_context(self) -> Dict[str, Any]:
        """Parse persisted short-term conversation context JSON safely."""
        if not self.active_context_json:
            return {}
        try:
            parsed = json.loads(self.active_context_json)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "profile_id": self.profile_id,
            "title": self.title,
            "summary": self.summary,
            "has_active_context": bool(self.active_context_json),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


VALID_MEMORY_TYPES = (
    "user_preference",
    "user_fact",
    "instruction",
    "project_context",
    "general_context",
)


class MemoryRecord(SQLModel, table=True):
    """Persistent local long-term AI memory item (strictly local, secret-scrubbed)."""
    __tablename__ = "memoryrecord"

    id: str = Field(
        default_factory=lambda: f"mem_{uuid.uuid4().hex[:12]}",
        primary_key=True,
        index=True,
    )
    profile_id: str = Field(default="default", index=True)
    memory_type: str = Field(default="general_context", index=True)
    content: str = Field(nullable=False)
    normalized_content: str = Field(default="", index=True)
    source_conversation_id: Optional[str] = Field(default=None, index=True)
    importance: int = Field(default=3)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_accessed_at: Optional[datetime] = Field(default=None)
    metadata_json: str = Field(default="{}")

    @property
    def memory_id(self) -> str:
        return self.id

    def get_metadata(self) -> Dict[str, Any]:
        if not self.metadata_json:
            return {}
        try:
            parsed = json.loads(self.metadata_json)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "memory_id": self.id,
            "profile_id": self.profile_id,
            "memory_type": self.memory_type,
            "content": self.content,
            "normalized_content": self.normalized_content,
            "source_conversation_id": self.source_conversation_id,
            "importance": self.importance,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "last_accessed_at": self.last_accessed_at.isoformat() if self.last_accessed_at else None,
            "metadata": self.get_metadata(),
        }


VALID_PREFERENCE_CATEGORIES = (
    "communication",
    "response_format",
    "technical",
    "workflow",
    "ai_behavior",
    "interface",
)


class PreferenceRecord(SQLModel, table=True):
    """Persistent local explicit user personalization preference."""
    __tablename__ = "preferencerecord"

    id: str = Field(
        default_factory=lambda: f"pref_{uuid.uuid4().hex[:12]}",
        primary_key=True,
        index=True,
    )
    profile_id: str = Field(default="default", index=True)
    category: str = Field(default="communication", index=True)
    key: str = Field(nullable=False)
    normalized_key: str = Field(default="", index=True)
    value: str = Field(nullable=False)
    description: Optional[str] = Field(default=None)
    enabled: bool = Field(default=True, index=True)
    priority: int = Field(default=3)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata_json: str = Field(default="{}")

    @property
    def preference_id(self) -> str:
        return self.id

    def get_metadata(self) -> Dict[str, Any]:
        if not self.metadata_json:
            return {}
        try:
            parsed = json.loads(self.metadata_json)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "preference_id": self.id,
            "profile_id": self.profile_id,
            "category": self.category,
            "key": self.key,
            "normalized_key": self.normalized_key,
            "value": self.value,
            "description": self.description,
            "enabled": bool(self.enabled),
            "priority": self.priority,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "metadata": self.get_metadata(),
        }


VALID_PROFILE_AVATARS = (
    "user",
    "briefcase",
    "graduation-cap",
    "code",
    "home",
    "sparkles",
)

VALID_THEME_MODES = (
    "dark",
    "light",
    "system",
)

VALID_ACCENT_COLORS = (
    "blue",
    "indigo",
    "emerald",
    "teal",
    "amber",
    "rose",
)


class ProfileRecord(SQLModel, table=True):
    """Persistent local VoxPilot application profile (not a cloud account)."""
    __tablename__ = "profilerecord"

    id: str = Field(
        default_factory=lambda: f"prof_{uuid.uuid4().hex[:12]}",
        primary_key=True,
        index=True,
    )
    name: str = Field(nullable=False)
    normalized_name: str = Field(default="", index=True)
    description: Optional[str] = Field(default=None)
    avatar: str = Field(default="user")
    theme_mode: str = Field(default="dark")
    accent_color: str = Field(default="blue")
    is_active: bool = Field(default=False, index=True)
    is_default: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def profile_id(self) -> str:
        return self.id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "profile_id": self.id,
            "name": self.name,
            "normalized_name": self.normalized_name,
            "description": self.description,
            "avatar": self.avatar,
            "theme_mode": self.theme_mode or "dark",
            "accent_color": self.accent_color or "blue",
            "is_active": bool(self.is_active),
            "is_default": bool(self.is_default),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class AutomationRecord(SQLModel, table=True):
    """Persistent user-defined reusable automation workflow (Phase 6A)."""
    __tablename__ = "automations"

    automation_id: str = Field(
        default_factory=lambda: f"auto_{uuid.uuid4().hex[:12]}",
        primary_key=True,
        index=True,
    )
    profile_id: str = Field(default="default", index=True)
    name: str = Field(nullable=False)
    description: str = Field(default="")
    trigger_type: str = Field(default="manual", index=True)
    trigger_config_json: str = Field(default="{}")
    steps_json: str = Field(default="[]")
    enabled: bool = Field(default=True, index=True)
    last_execution_id: Optional[str] = Field(default=None)
    last_execution_status: Optional[str] = Field(default=None)
    last_run_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        try:
            parsed_cfg = json.loads(self.trigger_config_json) if self.trigger_config_json else {}
            trigger_config = parsed_cfg if isinstance(parsed_cfg, dict) else {}
        except Exception:
            trigger_config = {}
        steps = normalize_plan_steps(self.steps_json)
        return {
            "automation_id": self.automation_id,
            "profile_id": self.profile_id,
            "name": self.name,
            "description": self.description,
            "trigger_type": self.trigger_type,
            "trigger_config": trigger_config,
            "steps": steps,
            "enabled": bool(self.enabled),
            "last_execution_id": self.last_execution_id,
            "last_execution_status": self.last_execution_status,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class AutomationExecutionRecord(SQLModel, table=True):
    """Persistent execution history record for a custom automation run (Phase 6B)."""
    __tablename__ = "automation_executions"

    execution_id: str = Field(
        default_factory=lambda: f"autoexec_{uuid.uuid4().hex[:12]}",
        primary_key=True,
        index=True,
    )
    automation_id: str = Field(index=True)
    profile_id: str = Field(default="default", index=True)
    automation_name: str = Field(default="")
    trigger_type: str = Field(default="manual", index=True)
    status: str = Field(default="running", index=True)
    total_steps: int = Field(default=0)
    completed_steps: int = Field(default=0)
    failed_step_index: Optional[int] = Field(default=None)
    cancelled: bool = Field(default=False)
    error_summary: Optional[str] = Field(default=None)
    duration_ms: Optional[int] = Field(default=None)
    pending_confirmation_json: Optional[str] = Field(default=None)
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)
    completed_at: Optional[datetime] = Field(default=None)

    def to_dict(self) -> Dict[str, Any]:
        try:
            parsed_conf = json.loads(self.pending_confirmation_json) if self.pending_confirmation_json else None
            pending_conf = parsed_conf if isinstance(parsed_conf, dict) else None
        except Exception:
            pending_conf = None
        return {
            "execution_id": self.execution_id,
            "automation_id": self.automation_id,
            "profile_id": self.profile_id,
            "automation_name": self.automation_name,
            "trigger_type": self.trigger_type,
            "trigger_source": self.trigger_type,
            "status": self.status,
            "total_steps": self.total_steps,
            "completed_steps": self.completed_steps,
            "failed_step_index": self.failed_step_index,
            "cancelled": bool(self.cancelled),
            "error": self.error_summary,
            "error_summary": self.error_summary,
            "duration_ms": self.duration_ms,
            "pending_confirmation": pending_conf,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.completed_at.isoformat() if self.completed_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


class AutomationExecutionStepRecord(SQLModel, table=True):
    """Persistent per-step record for a custom automation execution (Phase 6B)."""
    __tablename__ = "automation_execution_steps"

    id: Optional[int] = Field(default=None, primary_key=True)
    execution_id: str = Field(index=True)
    step_index: int = Field(default=0)
    step_id: str = Field(default="")
    action: str = Field(default="")
    description: str = Field(default="")
    input_summary: str = Field(default="{}")
    status: str = Field(default="pending")
    started_at: Optional[datetime] = Field(default=None)
    completed_at: Optional[datetime] = Field(default=None)
    output_summary: Optional[str] = Field(default=None)
    error_summary: Optional[str] = Field(default=None)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_index": self.step_index,
            "step_id": self.step_id,
            "action": self.action,
            "description": self.description,
            "input_summary": self.input_summary,
            "status": self.status,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "message": self.output_summary,
            "output_summary": self.output_summary,
            "error": self.error_summary,
            "error_summary": self.error_summary,
        }


class ProfileProviderConfigRecord(SQLModel, table=True):
    """Profile-specific non-secret AI provider configuration (Phase 8A).

    Raw API keys or secrets must NEVER be stored in this table.
    """
    __tablename__ = "profile_provider_configs"

    profile_id: str = Field(primary_key=True, index=True)
    active_provider_id: str = Field(default="local")
    local_model: str = Field(default="qwen3:4b")
    byok_vendor: Optional[str] = Field(default=None)
    byok_model: Optional[str] = Field(default=None)
    byok_credential_ref: Optional[str] = Field(default=None)
    cloud_tier: Optional[str] = Field(default=None)
    config_json: str = Field(default="{}")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        try:
            parsed_cfg = json.loads(self.config_json) if self.config_json else {}
            extra_cfg = parsed_cfg if isinstance(parsed_cfg, dict) else {}
        except Exception:
            extra_cfg = {}
        return {
            "profile_id": self.profile_id,
            "active_provider_id": self.active_provider_id or "local",
            "local_model": self.local_model or "qwen3:4b",
            "byok_vendor": self.byok_vendor,
            "byok_model": self.byok_model,
            "has_byok_credential": bool(self.byok_credential_ref),
            "cloud_tier": self.cloud_tier,
            "config": extra_cfg,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
