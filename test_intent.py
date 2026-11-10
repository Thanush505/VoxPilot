from backend.agent.intent import IntentRecognizer


recognizer = IntentRecognizer()

test_messages = [
    "Hello VoxPilot, how are you?",
    "Open Google Chrome",
    "Find my Python project files",
    "Tell me how much RAM is being used",
    "Search for Python tutorials"
]

for message in test_messages:

    print("\nUser:", message)

    result = recognizer.recognize_intent(message)

    print("Intent:", result["intent"])
    print("Confidence:", result["confidence"])
    print("Reason:", result["reason"])