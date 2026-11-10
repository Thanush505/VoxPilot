"""
VoxPilot - Memory Subsystem
Provides short-term conversational context, entity tracking, and reference resolution.
"""

from backend.memory.context_manager import (
    ConversationTurn,
    ConversationContext,
    ConversationContextManager,
    get_shared_context_manager,
    reset_shared_context_manager,
)

__all__ = [
    "ConversationTurn",
    "ConversationContext",
    "ConversationContextManager",
    "get_shared_context_manager",
    "reset_shared_context_manager",
]

