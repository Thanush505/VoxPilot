"""
VoxPilot - Memory Subsystem
Provides short-term conversational context, persistent conversation hydration,
local FTS5 long-term AI memory, and conservative privacy-guarded extraction.
"""

from backend.memory.context_manager import (
    ConversationTurn,
    ConversationContext,
    ConversationContextManager,
    get_shared_context_manager,
    reset_shared_context_manager,
)
from backend.memory.long_term_memory import (
    LongTermMemoryService,
    MemoryPrivacyError,
    InvalidMemoryTypeError,
    get_shared_long_term_memory_service,
    reset_shared_long_term_memory_service,
)
from backend.memory.memory_extractor import MemoryExtractor
from backend.memory.conversation_hydrator import (
    ConversationHydrator,
    get_shared_conversation_hydrator,
    reset_shared_conversation_hydrator,
)

__all__ = [
    "ConversationTurn",
    "ConversationContext",
    "ConversationContextManager",
    "get_shared_context_manager",
    "reset_shared_context_manager",
    "LongTermMemoryService",
    "MemoryPrivacyError",
    "InvalidMemoryTypeError",
    "get_shared_long_term_memory_service",
    "reset_shared_long_term_memory_service",
    "MemoryExtractor",
    "ConversationHydrator",
    "get_shared_conversation_hydrator",
    "reset_shared_conversation_hydrator",
]
