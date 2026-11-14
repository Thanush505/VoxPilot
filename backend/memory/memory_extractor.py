"""
VoxPilot - Conservative Long-Term Memory Extractor
Extracts durable, explicit user preferences, facts, instructions, and project context
while aggressively rejecting secrets, questions, commands, and noisy/speculative messages.
"""

from __future__ import annotations

import logging
import re
from typing import Optional, List, Dict, Any

from backend.database.models import contains_obvious_secret
from backend.memory.long_term_memory import (
    LongTermMemoryService,
    MemoryPrivacyError,
    get_shared_long_term_memory_service,
)

logger = logging.getLogger(__name__)

_COMMAND_PREFIXES = (
    "open ", "launch ", "start ", "run ", "close ", "minimize ", "maximize ",
    "restore ", "focus ", "switch to ", "type ", "write ", "press ", "hit ",
    "send ", "click ", "scroll ", "delete ", "remove ", "rename ", "copy ",
    "move ", "create ", "make ", "search ", "find ", "list ", "show ",
    "check ", "take a screenshot", "capture ", "go to ", "navigate ",
)

_SPECULATIVE_OR_QUOTED_RE = re.compile(
    r"^(?:what if|suppose|hypothetically|maybe|perhaps|imagine|pretend|let'?s say|could you imagine)\b",
    re.IGNORECASE,
)

_GREETING_ONLY_RE = re.compile(
    r"^(?:hello|hi|hey|good morning|good afternoon|good evening|thanks|thank you|ok|okay|bye|goodbye)[!.\s]*$",
    re.IGNORECASE,
)


class MemoryExtractor:
    """Conservative extractor converting explicit user statements into long-term memories."""

    def __init__(self, memory_service: Optional[LongTermMemoryService] = None):
        self.memory_service = memory_service

    def _get_service(self) -> LongTermMemoryService:
        if self.memory_service is None:
            self.memory_service = get_shared_long_term_memory_service()
        return self.memory_service

    def _is_candidate_disallowed(self, text: str) -> bool:
        if not text or not isinstance(text, str):
            return True
        clean = text.strip()
        if len(clean) < 8 or len(clean) > 450:
            return True
        # Reject multi-line code blocks, terminal dumps, or pasted essays
        if clean.count("\n") >= 2 or "```" in clean:
            return True
        # Reject questions and speculative hypotheticals
        if clean.endswith("?"):
            return True
        if _GREETING_ONLY_RE.match(clean):
            return True
        if _SPECULATIVE_OR_QUOTED_RE.match(clean):
            return True
        # Reject pure quotation strings or indirect speech
        if (clean.startswith('"') and clean.endswith('"')) or (clean.startswith("'") and clean.endswith("'")):
            return True
        lower = clean.lower()
        if any(lower.startswith(prefix) for prefix in _COMMAND_PREFIXES):
            return True
        has_secret, _ = contains_obvious_secret(clean)
        if has_secret:
            return True
        return False

    def _classify_directive_payload(self, payload: str) -> tuple[str, int]:
        lower = payload.lower()
        if any(
            kw in lower
            for kw in ("always ", "never ", "keep responses", "respond ", "reply ", "format ", "concise", "brief")
        ):
            return "instruction", 4
        if any(
            kw in lower
            for kw in ("prefer", "preferred", "favorite", "favourite", "like ", "dark mode", "light mode")
        ):
            return "user_preference", 4
        if any(
            kw in lower
            for kw in ("project", "repo", "repository", "app", "voxpilot", "workspace", "codebase", "building ")
        ):
            return "project_context", 4
        return "user_fact", 4

    def extract_candidates(self, user_input: str) -> List[Dict[str, Any]]:
        """Extract conservative memory candidates from user input without persisting them."""
        if not user_input or not isinstance(user_input, str):
            return []

        raw_clean = re.sub(r"\s+", " ", user_input.strip())
        # Quick privacy gate on full input before splitting sentences
        has_secret, _ = contains_obvious_secret(raw_clean)
        if has_secret:
            return []

        if self._is_candidate_disallowed(raw_clean):
            return []

        candidates: List[Dict[str, Any]] = []
        seen_norms: set[str] = set()

        def _add(content: str, memory_type: str, importance: int, trigger: str) -> None:
            item_clean = content.strip().rstrip(".!")
            if len(item_clean) < 6:
                return
            has_sec, _ = contains_obvious_secret(item_clean)
            if has_sec:
                return
            item_lower = item_clean.lower()
            if item_lower.endswith("?") or any(item_lower.startswith(p) for p in _COMMAND_PREFIXES):
                return
            if item_lower in seen_norms:
                return
            seen_norms.add(item_lower)
            candidates.append(
                {
                    "content": f"{item_clean}.",
                    "memory_type": memory_type,
                    "importance": importance,
                    "metadata": {"extracted_by": "conservative_extractor", "trigger": trigger},
                }
            )

        # Split multi-clause sentences cautiously for extraction checks
        clauses = [c.strip() for c in re.split(r"(?<=[.!])\s+|;\s*", raw_clean) if c.strip()]
        for clause in clauses:
            if clause.endswith("?"):
                continue

            # 1. Explicit "Remember that ..." / "Please remember ..." / "Save to memory: ..."
            rem_match = re.match(
                r"^(?:please\s+)?(?:remember(?:\s+that)?|keep\s+in\s+mind(?:\s+that)?|save\s+(?:this\s+)?to\s+memory[:\s]+)\s*(.+)$",
                clause,
                re.IGNORECASE,
            )
            if rem_match:
                payload = rem_match.group(1).strip()
                if payload and not payload.endswith("?") and len(payload) <= 300:
                    # Also handle "remember that A and B" when B starts with "I ..." / "my ..."
                    sub_clauses = re.split(r"\s+and\s+(?=(?:i\s+|my\s+|we\s+|always\s+|the\s+project\s+))", payload, flags=re.IGNORECASE)
                    for sub in sub_clauses:
                        mtype, imp = self._classify_directive_payload(sub)
                        _add(sub, mtype, imp, "explicit_remember")
                    continue

            # 2. Explicit user preferences
            pref_patterns = (
                r"^(?:i\s+(?:really\s+)?(?:prefer|like\s+using|usually\s+use)|my\s+preferred\s+.+?\s+is|my\s+favorite\s+.+?\s+is)\s+.+$",
            )
            if any(re.match(pat, clause, re.IGNORECASE) for pat in pref_patterns):
                _add(clause, "user_preference", 4, "explicit_preference")
                continue

            # 3. Persistent assistant behavior instructions
            inst_patterns = (
                r"^(?:always\s+(?:respond|reply|answer|keep|use|format|speak|explain)|never\s+(?:use|include)|please\s+(?:always|keep\s+responses))\s+.+$",
            )
            if any(re.match(pat, clause, re.IGNORECASE) for pat in inst_patterns):
                _add(clause, "instruction", 4, "explicit_instruction")
                continue

            # 4. Explicit project context
            proj_patterns = (
                r"^(?:my\s+(?:main\s+|current\s+)?project\s+(?:is|uses|is\s+called)|this\s+project\s+(?:is|uses|runs)|we\s+are\s+(?:building|working\s+on))\s+.+$",
            )
            if any(re.match(pat, clause, re.IGNORECASE) for pat in proj_patterns):
                _add(clause, "project_context", 4, "explicit_project_context")
                continue

            # 5. Explicit user identity / fact statements
            fact_patterns = (
                r"^(?:my\s+name\s+is|i\s+am\s+a\s+(?:software|python|web|mobile|ai|ml|data|full[\s-]?stack|backend|frontend|devops|computer|cs|engineering|university)?\s*(?:developer|engineer|student|designer|architect|researcher)|i\s+work\s+(?:as|at|on)|i\s+live\s+in)\b.+$",
            )
            if any(re.match(pat, clause, re.IGNORECASE) for pat in fact_patterns):
                _add(clause, "user_fact", 4, "explicit_user_fact")
                continue

        return candidates

    def extract_and_store(
        self,
        user_input: str,
        conversation_id: Optional[str] = None,
        profile_id: str = "default",
    ) -> List[Dict[str, Any]]:
        """Conservatively extract memories from user_input and store them locally.

        Never raises or blocks conversation execution if storage fails or privacy rejects content.
        """
        stored: List[Dict[str, Any]] = []
        try:
            candidates = self.extract_candidates(user_input)
            if not candidates:
                return []
            svc = self._get_service()
            for cand in candidates:
                try:
                    saved = svc.create_memory(
                        content=cand["content"],
                        memory_type=cand["memory_type"],
                        importance=cand.get("importance", 3),
                        profile_id=profile_id,
                        source_conversation_id=conversation_id,
                        metadata=cand.get("metadata"),
                    )
                    stored.append(saved)
                except MemoryPrivacyError as priv_err:
                    logger.info(f"Skipped sensitive candidate during extraction: {priv_err}")
                except Exception as item_err:
                    logger.warning(f"Failed saving extracted memory candidate: {item_err}")
        except Exception as err:
            logger.warning(f"Non-fatal memory extraction failure: {err}")
        return stored
