
from backend.agent.graph import VoxPilotAgent


agent = VoxPilotAgent()

user_message = "Tell me how much RAM is being used."

print("\nUser:", user_message)

result = agent.process_request(user_message)

print("\nVoxPilot Agent Result")

print("Intent:", result.get("intent"))
print("Confidence:", result.get("confidence"))
print("Plan:", result.get("plan"))
print("Response:", result.get("response"))
print("Status:", result.get("execution_status"))

if result.get("error"):
    print("Error:", result["error"])
