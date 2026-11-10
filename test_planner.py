
from backend.agent.planner import AgentPlanner


planner = AgentPlanner()

test_cases = [
    {
        "user_input": "Open Google Chrome",
        "intent": "open_application"
    },
    {
        "user_input": "Find my Python project files",
        "intent": "file_management"
    },
    {
        "user_input": "Tell me how much RAM is being used",
        "intent": "system_information"
    },
    {
        "user_input": "Search for Python tutorials",
        "intent": "browser_task"
    },
    {
        "user_input": "Hello VoxPilot",
        "intent": "general_conversation"
    }
]

for test_case in test_cases:

    print("\nUser:", test_case["user_input"])

    try:
        result = planner.create_plan(
            user_input=test_case["user_input"],
            intent=test_case["intent"]
        )

        print("Plan:")

        for index, step in enumerate(result["steps"], start=1):
            print(f"\nStep {index}")
            print("Action:", step["action"])
            print("Description:", step["description"])
            print("Parameters:", step["parameters"])
            print(
                "Confirmation required:",
                step["requires_confirmation"]
            )

        print("\nResponse:", result["response"])

    except Exception as error:
        print("Planner error:", error)
