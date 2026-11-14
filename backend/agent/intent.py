"""
VoxPilot - Natural Language Intent Recognition Subsystem
Classifies user requests into approved intent domains:
open_application, keyboard_automation, window_management, file_management,
system_information, browser_task, general_conversation, or unknown.
"""

from __future__ import annotations

import re
import json
import logging
from typing import Optional, Dict, Any
import ollama

from backend.ai.model_manager import get_shared_model_manager

logger = logging.getLogger(__name__)


class IntentRecognitionError(Exception):
    """Raised when intent recognition fails."""
    pass


def extract_open_command_target(text: str) -> Optional[str]:
    """Extract the target application/destination from a natural-language open/launch command.

    Handles imperfect STT punctuation (e.g. 'Open, watch up.'), polite prefixes
    ('Please open WhatsApp', 'Can you launch Chrome?'), and avoids misclassifying
    ordinary conversation ('What is WhatsApp?') or in-app actions.
    """
    if not text or not isinstance(text, str):
        return None
    cleaned = text.strip()
    # Strip optional wake-word or polite conversational prefixes
    cleaned = re.sub(
        r"^(?:(?:hey|hi|ok|okay)\s+)?voxpilot[,:\s]+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()
    cleaned = re.sub(
        r"^(?:please|kindly|(?:can|could|would|will)\s+you(?:\s+please)?)\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()

    match = re.match(
        r"^(?:open(?:\s+up)?|launch|start|run)\s*[,:\-;]?\s+(.+?)\s*[.?!]*$",
        cleaned,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    target = match.group(1).strip().strip("'\"")
    if not target:
        return None
    return target


class IntentRecognizer:

    def __init__(self, model: str = "qwen3:4b"):
        self.model = model

        self.categories = [
            "general_conversation",
            "open_application",
            "keyboard_automation",
            "window_management",
            "file_management",
            "system_information",
            "browser_task",
            "email_assistant",
            "unknown",
        ]

    def _deterministic_intent(self, text: str) -> Optional[Dict[str, Any]]:
        """High-precision classification for unambiguous natural language commands."""
        clean = text.strip()
        lower = clean.lower()

        # 1. Keyboard typing / keystrokes
        # "Type Hello into Notepad", "Write Testing 123 in Notepad", "Type this message into the active window"
        if re.search(
            r"^(?:type|write|input)\s+.*?\s+(?:into|in|to)\s+(?:notepad|the\s+active\s+window|active\s+window|calculator|vscode|vs\s*code|chrome|window)\b",
            lower,
        ):
            return {
                "intent": "keyboard_automation",
                "confidence": 0.99,
                "reason": "User requested keyboard typing into an application or window.",
            }
        if re.search(
            r"^type\s+(?:hello|testing|this|the\s+following|some\s+text|text|\"[^\"]+\"|'[^']+')\b",
            lower,
        ):
            return {
                "intent": "keyboard_automation",
                "confidence": 0.98,
                "reason": "User requested keyboard typing.",
            }
        if re.search(
            r"^(?:press|hit)\s+(?:key\s+)?(?:enter|tab|space|backspace|delete|escape|up|down|left|right)\b",
            lower,
        ):
            return {
                "intent": "keyboard_automation",
                "confidence": 0.99,
                "reason": "User requested pressing a keyboard key.",
            }
        if re.search(r"^(?:send|press)\s+(?:shortcut\s+)?(?:ctrl|alt|shift)\b", lower):
            return {
                "intent": "keyboard_automation",
                "confidence": 0.99,
                "reason": "User requested keyboard shortcut.",
            }

        # 2. Window management
        # "Minimize Notepad", "Maximize Calculator", "Close Notepad", "Switch to Notepad",
        # "Close the currently active window", "Minimize all windows", "Arrange my open windows"
        if re.search(
            r"^(?:minimize\s+(?:all|every)\s+windows?|(?:arrange|tile|cascade)\s+(?:my\s+)?(?:open\s+)?windows?|"
            r"close\s+(?:the\s+)?(?:currently\s+)?(?:active|current|this)\s+window|"
            r"(?:what|which|identify|show|get)\s+(?:is\s+)?(?:the\s+)?(?:currently\s+)?active\s+window|"
            r"(?:list|show)\s+(?:all\s+)?(?:running|open)\s+(?:applications|apps|windows))\b",
            lower,
        ) or re.search(
            r"^(?:minimize|maximize|restore|close|focus|bring\s+to\s+(?:the\s+)?front|switch\s+to)\s+(?:the\s+)?[a-z0-9._ -]{2,60}\b",
            lower,
        ):
            return {
                "intent": "window_management",
                "confidence": 0.98,
                "reason": "User requested window management action.",
            }

        # 3. Application discovery, search, and index refresh
        # "Show me all installed applications", "List installed apps", "Refresh application index"
        if re.search(
            r"^(?:show|list|search|find|get)\s+(?:me\s+)?(?:all\s+)?(?:installed\s+)?(?:applications|apps|programs)\b",
            lower,
        ) or re.search(
            r"^(?:refresh|rescan|update|rebuild)\s+(?:the\s+)?(?:installed\s+)?(?:application|app)\s+(?:index|list|cache|catalog)\b",
            lower,
        ):
            return {
                "intent": "open_application",
                "confidence": 0.99,
                "reason": "User requested listing, searching, or refreshing installed applications.",
            }

        # 4. Filesystem / document management & AI File Assistant
        # "Create a text file named notes.txt", "Create a text file containing these notes...",
        # "Find my resume", "Summarize this PDF", "What does this document say about...",
        # "Compare these two documents", "Find files modified recently in Downloads",
        # "Find all Python files inside my VoxPilot project", "Organize these files into folders"
        if re.search(r"^(?:create|make)\s+(?:a\s+)?(?:new\s+)?(?:text\s+)?file\b", lower):
            return {
                "intent": "file_management",
                "confidence": 0.99,
                "reason": "User requested file creation on disk.",
            }
        if re.search(r"^save\s+(?:the\s+)?(?:current\s+)?(?:document|file)\b", lower):
            return {
                "intent": "file_management",
                "confidence": 0.98,
                "reason": "User requested document saving.",
            }
        if re.search(
            r"^(?:summarize|summarise|read|inspect|analyze|analyse|extract)\s+(?:this\s+|the\s+|my\s+)?(?:pdf|docx?|txt|csv|json|md|markdown|document|file|resume|report|notes)\b",
            lower,
        ) or re.search(
            r"^(?:summarize|summarise|read|inspect)\s+.*\.(?:pdf|docx?|txt|csv|json|md|markdown|py|log)\b",
            lower,
        ) or re.search(
            r"^what\s+does\s+(?:this|the)\s+(?:document|file|pdf|docx?|report|resume|notes)\s+say\b",
            lower,
        ) or re.search(
            r"^compare\s+(?:these\s+(?:two\s+)?(?:documents|files)|.*\.(?:pdf|docx?|txt|csv|json|md|py)\b)",
            lower,
        ) or re.search(
            r"^organize\s+(?:these\s+)?files\b",
            lower,
        ):
            return {
                "intent": "file_management",
                "confidence": 0.99,
                "reason": "User requested AI File Assistant document reading, Q&A, comparison, or organization.",
            }
        if re.search(
            r"^(?:search|find|look|locate)\s+(?:for\s+)?(?:my\s+|all\s+|a\s+)?(?:files?|documents?|folders?|directory|resume|\S+\.\w+|python\s+files?)\b",
            lower,
        ):
            return {
                "intent": "file_management",
                "confidence": 0.98,
                "reason": "User requested file or folder search on disk.",
            }
        if re.search(
            r"^(?:list|show)\s+(?:my\s+)?(?:files|directory|folder|downloads|documents|desktop)\b",
            lower,
        ) or re.search(
            r"^open\s+(?:my\s+(?:files|directory|folder|downloads|documents|desktop)|(?:the\s+)?(?:downloads|documents|desktop)\s+folder)\b",
            lower,
        ):
            return {
                "intent": "file_management",
                "confidence": 0.98,
                "reason": "User requested directory inspection.",
            }

        # 4d. AI Email Assistant (Phase 4D)
        if re.search(
            r"\b(?:emails?|e-mails?|inbox|unread\s+emails?|email\s+thread)\b",
            lower,
        ) and any(
            kw in lower
            for kw in (
                "summarize", "summarise", "find", "search", "show", "list",
                "draft", "write", "reply", "identify", "unread", "attachment",
                "thread", "check", "read",
            )
        ):
            return {
                "intent": "email_assistant",
                "confidence": 0.99,
                "reason": "User requested AI Email Assistant reading, search, summarization, or drafting.",
            }
        if re.search(r"^(?:reply\s+politely\s+that|draft\s+(?:a\s+)?(?:professional\s+)?reply)\b", lower):
            return {
                "intent": "email_assistant",
                "confidence": 0.99,
                "reason": "User requested drafting an email reply.",
            }

        # 4c. Browser Research & Webpage Inspection (Phase 4C)
        if re.search(
            r"^(?:research|investigate)\b",
            lower,
        ) or re.search(
            r"^(?:open|visit|browse|go\s+to|summarize|summarise|read|inspect)\s+(?:this\s+|the\s+)?(?:website|webpage|web\s+page|article|site|page)\b",
            lower,
        ) or re.search(
            r"^compare\s+(?:the\s+information\s+from\s+these\s+.*?(?:websites|webpages|pages|sites)|.*https?://)",
            lower,
        ) or re.search(
            r"^(?:find|extract|get)\s+.*?\b(?:on\s+this\s+(?:page|website|site)|from\s+these\s+search\s+results|official\s+documentation\s+for)\b",
            lower,
        ) or re.search(r"https?://\S+", lower):
            return {
                "intent": "browser_task",
                "confidence": 0.99,
                "reason": "User requested browser navigation, webpage inspection, or web research.",
            }

        # 4b. Universal Application, Windows Settings, System Utility, and Special Folder Launching
        # Handles STT punctuation and polite prefixes (e.g. "Open, watch up.", "Please launch WhatsApp")
        target = extract_open_command_target(clean)
        if target:
            lower_target = target.lower()
            is_url = bool(re.search(r"^(?:https?://|www\.)|\.(?:com|org|net|io|gov|edu)\b", lower_target))
            is_doc_file = bool(re.search(r"\.(?:txt|md|pdf|docx?|xlsx?|pptx?|csv|json|xml|yaml|yml|py|js|ts|html|css|log)$", lower_target))
            if not is_url and not is_doc_file:
                if "and type" in lower or "and write" in lower:
                    return {
                        "intent": "open_application",
                        "confidence": 0.98,
                        "reason": "User requested launching application and typing.",
                    }
                return {
                    "intent": "open_application",
                    "confidence": 0.99,
                    "reason": "User requested launching an application, Windows Settings page, or system utility.",
                }

        # 5. System information / screenshots
        if re.search(
            r"^(?:check|show|get)\s+(?:system\s+)?(?:info|stats|metrics|status|ram|cpu|battery)\b",
            lower,
        ):
            return {
                "intent": "system_information",
                "confidence": 0.99,
                "reason": "User requested system information.",
            }
        if re.search(r"^(?:take|capture)\s+(?:a\s+)?screenshot\b", lower):
            return {
                "intent": "system_information",
                "confidence": 0.99,
                "reason": "User requested screenshot capture.",
            }

        # 6. General conversation (greetings, informational questions, & explicit user memory statements/recalls)
        if re.search(
            r"^(?:hello|hi|hey|good\s+morning|good\s+afternoon|good\s+evening|howdy|"
            r"what\s+can\s+you\s+do|who\s+are\s+you|what\s+is\b|tell\s+me\s+about\b|"
            r"do\s+you\s+(?:know|remember)\b|what\s+do\s+you\s+remember\b|how\s+do\s+i\s+use\b|"
            r"(?:please\s+)?remember(?:\s+that)?\b|keep\s+in\s+mind\b|"
            r"i\s+(?:really\s+)?prefer\b|my\s+preferred\b|my\s+favorite\b|my\s+favourite\b|"
            r"my\s+name\s+is\b|my\s+(?:main\s+|current\s+)?project\s+is\b|always\s+(?:respond|reply|answer|keep)\b)",
            lower,
        ):
            return {
                "intent": "general_conversation",
                "confidence": 0.99,
                "reason": "User greeting, memory statement, or conversational query.",
            }

        return None

    def recognize_intent(self, user_message: str) -> Dict[str, Any]:
        if not user_message or not user_message.strip():
            raise IntentRecognitionError("User message cannot be empty.")

        cleaned_msg = user_message.strip()

        # Check deterministic high-precision fast-path
        deterministic = self._deterministic_intent(cleaned_msg)
        if deterministic is not None:
            return deterministic

        categories_guide = """
Available Intent Categories and Definitions:
- "open_application": Launching or opening desktop applications (e.g. "Open Notepad", "Launch Calculator", "Start VS Code", "Open Chrome").
- "keyboard_automation": Typing text, writing text, pressing keys, or shortcuts into an application or active window (e.g. "Type Hello into Notepad", "Write Testing 123 in Notepad", "Type this into the active window", "Press enter", "Send shortcut ctrl+s"). IMPORTANT: Typing or writing text into an open editor/window is keyboard_automation, NEVER file_management.
- "window_management": Managing windows: finding, switching, activating, minimizing, maximizing, restoring, or closing windows (e.g. "Minimize Notepad", "Maximize Calculator", "Bring Notepad to the front", "Close Notepad").
- "file_management": Creating files/folders on disk, searching files, listing directory contents, moving, copying, renaming, or deleting files on disk, or saving documents to disk (e.g. "Create a text file named notes.txt", "Search for notes.txt", "List files in Downloads", "Delete file report.pdf", "Save the document"). IMPORTANT: File creation and saving target files on disk, NOT keyboard typing.
- "system_information": System stats, CPU/RAM usage, monitors, or capturing screenshots (e.g. "Check system info", "Take a screenshot of the desktop").
- "browser_task": Web browsing, navigating URLs, searching the web in browser, clicking web page elements, or downloading from the web (e.g. "Go to https://google.com", "Search the web for news").
- "general_conversation": Greetings, asking questions about VoxPilot, chitchat without desktop actions (e.g. "Hello", "How are you?", "What can you do?").
- "unknown": Requests that do not match any category above.
"""

        system_prompt = f"""
You are the intent recognition module of VoxPilot.

Classify the user's request into exactly one of these categories:
{self.categories}

{categories_guide}

Return only a valid JSON object with these fields:
{{
    "intent": "category_name",
    "confidence": 0.95,
    "reason": "Short explanation"
}}

Rules:
1. Select only one category from the provided list.
2. Typing or writing text into an application is "keyboard_automation", NOT "file_management".
3. Creating or saving a file on disk is "file_management", NOT "keyboard_automation".
4. Do not execute the requested action.
5. Do not invent missing information.
6. Use "unknown" if the request does not fit any category.
7. Confidence must be a number between 0 and 1.
"""

        try:
            from backend.ai.providers.runtime import run_provider_chat

            def _local_intent_call(local_model: str, msgs, temp: float) -> str:
                model_manager = get_shared_model_manager()
                target_model = self.model or local_model
                with model_manager.track_inference(target_model):
                    response = ollama.chat(
                        model=target_model,
                        messages=msgs,
                        format="json",
                        options={"temperature": temp},
                    )
                return response.message.content

            response_text = run_provider_chat(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": cleaned_msg},
                ],
                temperature=0,
                json_mode=True,
                local_executor=_local_intent_call,
            )
            if not response_text:
                raise IntentRecognitionError("The AI returned an empty response.")

            result = json.loads(response_text)

            intent = result.get("intent")
            confidence = result.get("confidence")
            reason = result.get("reason", "")

            # Disambiguate typing requests that the LLM mistakenly classified as file_management
            lower_msg = cleaned_msg.lower()
            if intent == "file_management" and any(w in lower_msg for w in ("type ", "write ", "keystroke", "press key")):
                intent = "keyboard_automation"
                reason = "Disambiguated to keyboard_automation: user requested typing text."

            if intent not in self.categories:
                raise IntentRecognitionError(f"Invalid intent category: {intent}")

            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                raise IntentRecognitionError("Confidence must be a number.")

            if not 0 <= confidence <= 1:
                raise IntentRecognitionError("Confidence must be between 0 and 1.")

            if not isinstance(reason, str):
                raise IntentRecognitionError("Reason must be a string.")

            return {
                "intent": intent,
                "confidence": confidence,
                "reason": reason,
            }

        except IntentRecognitionError:
            raise

        except Exception as error:
            raise IntentRecognitionError(f"Intent recognition failed: {error}") from error
