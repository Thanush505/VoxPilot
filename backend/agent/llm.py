"""
VoxPilot - Local Ollama LLM Interface
Provides response generation backed by Ollama (default qwen3:4b) wrapped
in ModelManager.track_inference() lifecycle protection so active inferences
safely prevent unsafe model unloading and always release tracking locks.
"""

from __future__ import annotations

import logging
import re
from typing import Optional, List, Dict, Any

import ollama

from backend.ai.model_manager import get_shared_model_manager
from backend.ai.providers.base import AIProviderError
from backend.database.models import scrub_text_secrets

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """Raised when the local language model fails."""


class OllamaLLM:
    def __init__(
        self,
        model: str = "qwen3:4b",
        system_prompt: Optional[str] = None,
    ):
        self.model = model

        self.system_prompt = system_prompt or (
            "You are VoxPilot, a personal AI desktop assistant. "
            "Speak naturally, clearly, and conversationally, like a human. "
            "Always use plain text only. "
            "Never use Markdown formatting, including asterisks, "
            "bold text, italics, headings, or code formatting. "
            "Never use emojis, emoticons, or decorative symbols. "
            "Avoid unnecessary quotation marks around words or phrases. "
            "Do not describe emojis, punctuation, or formatting. "
            "Use simple sentences that sound natural when spoken aloud. "
            "Keep responses concise unless the user requests details. "
            "Do not claim to have performed actions that you have not performed."
        )

        self.messages: List[Dict[str, str]] = [
            {
                "role": "system",
                "content": self.system_prompt,
            }
        ]

    def clean_response(self, text: str) -> str:
        """Remove reasoning blocks, formatting, and emojis before displaying or speaking."""
        if not text:
            return ""

        # Strip hidden model reasoning blocks (e.g. qwen3 <think>...</think>)
        text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE)
        text = re.sub(r"<think>[\s\S]*$", "", text, flags=re.IGNORECASE)

        # Remove Markdown formatting markers.
        text = re.sub(r"[*_`#~]+", "", text)

        # Remove quotation marks.
        text = re.sub(r"""["'“”‘’]""", "", text)

        # Remove common emoji and pictographic Unicode ranges.
        emoji_pattern = (
            "["
            "\U0001F300-\U0001F5FF"
            "\U0001F600-\U0001F64F"
            "\U0001F680-\U0001F6FF"
            "\U0001F700-\U0001F77F"
            "\U0001F780-\U0001F7FF"
            "\U0001F800-\U0001F8FF"
            "\U0001F900-\U0001F9FF"
            "\U0001FA00-\U0001FAFF"
            "\U00002600-\U000026FF"
            "\U00002700-\U000027BF"
            "]"
        )

        text = re.sub(emoji_pattern, "", text)

        # Remove leftover emoji variation and joining characters.
        text = re.sub(r"[\uFE0E\uFE0F\u200D]", "", text)

        # Normalize whitespace.
        text = re.sub(r"\s+", " ", text)

        # Remove spaces before punctuation.
        text = re.sub(r"\s+([.,!?;:])", r"\1", text)

        # Normalize spaces after punctuation.
        text = re.sub(r"([.,!?;:])(?=\S)", r"\1 ", text)

        return text.strip()

    def _call_ollama_chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
    ) -> str:
        """Invoke ollama.chat protected by ModelManager.track_inference()."""
        model_manager = get_shared_model_manager()
        with model_manager.track_inference(self.model):
            response = ollama.chat(
                model=self.model,
                messages=messages,
                options={"temperature": temperature},
            )

        assistant_message = ""
        if hasattr(response, "message") and getattr(response.message, "content", None):
            assistant_message = response.message.content
        elif isinstance(response, dict):
            msg = response.get("message") or {}
            assistant_message = msg.get("content", "")

        if not assistant_message:
            raise LLMError("The language model returned an empty response.")

        cleaned_message = self.clean_response(assistant_message)
        if not cleaned_message:
            raise LLMError("The language model returned no usable text.")
        return cleaned_message

    def generate_response(self, user_message: str) -> str:
        """Generate a stateful multi-turn conversational response."""
        if not user_message or not user_message.strip():
            raise LLMError("User message cannot be empty.")

        self.messages.append(
            {
                "role": "user",
                "content": user_message.strip(),
            }
        )

        try:
            from backend.ai.providers.runtime import run_provider_chat

            raw_reply = run_provider_chat(
                self.messages,
                temperature=0.7,
                local_executor=lambda _m, msgs, temp: self._call_ollama_chat(msgs, temperature=temp),
            )
            cleaned_message = self.clean_response(raw_reply)
            if not cleaned_message:
                raise LLMError("The language model returned no usable text.")
            self.messages.append(
                {
                    "role": "assistant",
                    "content": cleaned_message,
                }
            )
            return cleaned_message

        except Exception as error:
            self.messages.pop()

            if isinstance(error, (LLMError, AIProviderError)):
                raise

            raise LLMError(
                f"Ollama request failed: {error}"
            ) from error

    def generate_contextual_response(
        self,
        user_message: str,
        conversation_history: Optional[str] = None,
        recent_turns_text: Optional[str] = None,
        memories: Optional[List[Dict[str, Any]]] = None,
        personalization_context: Optional[str] = None,
    ) -> str:
        """Generate an isolated conversational reply enriched with preferences, memories & turns."""
        if not user_message or not user_message.strip():
            raise LLMError("User message cannot be empty.")

        context_sections: List[str] = [self.system_prompt]

        if personalization_context and personalization_context.strip():
            context_sections.append(
                "Authoritative user personalization preferences (always respect these):\n"
                + scrub_text_secrets(personalization_context.strip())
            )

        if memories:
            mem_lines = []
            for m in memories[:6]:
                if isinstance(m, dict) and m.get("content"):
                    mtype = m.get("memory_type", "general_context")
                    content = scrub_text_secrets(str(m["content"]))
                    mem_lines.append(f"- ({mtype}) {content}")
            if mem_lines:
                context_sections.append(
                    "Relevant local user memories you already know (use naturally when helpful):\n"
                    + "\n".join(mem_lines)
                )

        if recent_turns_text and recent_turns_text.strip():
            context_sections.append(
                "Recent turns in this conversation:\n" + scrub_text_secrets(recent_turns_text.strip())
            )
        elif conversation_history and conversation_history.strip() and conversation_history != "No previous context.":
            context_sections.append(
                "Active desktop context: " + scrub_text_secrets(conversation_history.strip())
            )

        messages = [
            {"role": "system", "content": "\n\n".join(context_sections)},
            {"role": "user", "content": user_message.strip()},
        ]

        try:
            from backend.ai.providers.runtime import run_provider_chat

            raw_reply = run_provider_chat(
                messages,
                temperature=0.5,
                local_executor=lambda _m, msgs, temp: self._call_ollama_chat(msgs, temperature=temp),
            )
            cleaned = self.clean_response(raw_reply)
            if not cleaned:
                raise LLMError("The language model returned no usable text.")
            return cleaned
        except Exception as error:
            if isinstance(error, (LLMError, AIProviderError)):
                raise
            raise LLMError(f"Ollama request failed: {error}") from error

    def reset_conversation(self) -> None:
        self.messages = [
            {
                "role": "system",
                "content": self.system_prompt,
            }
        ]
        logger.debug("VoxPilot conversation history cleared.")
