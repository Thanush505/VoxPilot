
import re
import ollama


class LLMError(Exception):
    """Raised when the local language model fails."""


class OllamaLLM:
    def __init__(
        self,
        model="qwen3:4b",
        system_prompt=None
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

        self.messages = [
            {
                "role": "system",
                "content": self.system_prompt
            }
        ]

    def clean_response(self, text):
        """Remove formatting and emojis before displaying or speaking."""

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

    def generate_response(self, user_message):
        if not user_message or not user_message.strip():
            raise LLMError("User message cannot be empty.")

        self.messages.append(
            {
                "role": "user",
                "content": user_message.strip()
            }
        )

        try:
            response = ollama.chat(
                model=self.model,
                messages=self.messages,
                options={
                    "temperature": 0.7
                }
            )

            assistant_message = response.message.content

            if not assistant_message:
                raise LLMError(
                    "The language model returned an empty response."
                )

            cleaned_message = self.clean_response(
                assistant_message
            )

            if not cleaned_message:
                raise LLMError(
                    "The language model returned no usable text."
                )

            self.messages.append(
                {
                    "role": "assistant",
                    "content": cleaned_message
                }
            )

            return cleaned_message

        except Exception as error:
            self.messages.pop()

            if isinstance(error, LLMError):
                raise

            raise LLMError(
                f"Ollama request failed: {error}"
            ) from error

    def reset_conversation(self):
        self.messages = [
            {
                "role": "system",
                "content": self.system_prompt
            }
        ]

        print("VoxPilot conversation history cleared.")
