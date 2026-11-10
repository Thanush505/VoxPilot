
from backend.agent.graph import VoxPilotAgent


agent = VoxPilotAgent()

test_requests = [
    "Hello VoxPilot, how are you?",
    "Open Google Chrome",
    "Find my Python project files",
    "Tell me how much RAM is being used",
    "Search for Python tutorials"
]

for request in test_requests:

    print("\n" + "=" * 60)
    print("USER REQUEST:", request)

    result = agent.process_request(request)

    print("\nINTENT:", result.get("intent"))
    print("CONFIDENCE:", result.get("confidence"))

    print("\nPLAN:")

    for index, step in enumerate(
        result.get("plan", []),
        start=1
    ):
        print(f"\nStep {index}")
        print("Action:", step["action"])
        print("Description:", step["description"])
        print("Parameters:", step["parameters"])

    print("\nRESPONSE:", result.get("response"))
    print("STATUS:", result.get("execution_status"))

    if result.get("error"):
        print("ERROR:", result["error"])
