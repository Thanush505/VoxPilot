from backend.agent.llm import OllamaLLM

ai = OllamaLLM()

text = 'Ah! You meant **"Python"** 😄. It is a popular language.'

cleaned_text = ai.clean_response(text)

print("Original:", text)
print("Cleaned:", cleaned_text)