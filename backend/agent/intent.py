
import json
import ollama


class IntentRecognitionError(Exception):
    """Raised when intent recognition fails."""


class IntentRecognizer:

    def __init__(self, model="qwen3:4b"):
        self.model = model

        self.categories = [
            "general_conversation",
            "open_application",
            "file_management",
            "system_information",
            "browser_task",
            "unknown"
        ]

    def recognize_intent(self, user_message):

        if not user_message or not user_message.strip():
            raise IntentRecognitionError(
                "User message cannot be empty."
            )

        system_prompt = f"""
You are the intent recognition module of VoxPilot.

Classify the user's request into exactly one of these categories:

{self.categories}

Return only a valid JSON object with these fields:

{{
    "intent": "category_name",
    "confidence": 0.95,
    "reason": "Short explanation"
}}

Rules:
1. Select only one category from the provided list.
2. Do not execute the requested action.
3. Do not invent missing information.
4. Use unknown if the request does not fit any category.
5. Confidence must be a number between 0 and 1.
"""

        try:
            response = ollama.chat(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": system_prompt
                    },
                    {
                        "role": "user",
                        "content": user_message.strip()
                    }
                ],
                format="json",
                options={
                    "temperature": 0
                }
            )

            response_text = response.message.content

            if not response_text:
                raise IntentRecognitionError(
                    "The AI returned an empty response."
                )

            result = json.loads(response_text)

            intent = result.get("intent")
            confidence = result.get("confidence")
            reason = result.get("reason", "")

            if intent not in self.categories:
                raise IntentRecognitionError(
                    f"Invalid intent category: {intent}"
                )

            if (
                isinstance(confidence, bool)
                or not isinstance(confidence, (int, float))
            ):
                raise IntentRecognitionError(
                    "Confidence must be a number."
                )

            if not 0 <= confidence <= 1:
                raise IntentRecognitionError(
                    "Confidence must be between 0 and 1."
                )

            if not isinstance(reason, str):
                raise IntentRecognitionError(
                    "Reason must be a string."
                )

            return {
                "intent": intent,
                "confidence": confidence,
                "reason": reason
            }

        except IntentRecognitionError:
            raise

        except Exception as error:
            raise IntentRecognitionError(
                f"Intent recognition failed: {error}"
            ) from error
