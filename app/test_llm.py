"""Step 3 check: list Gemini models for your key, then test one."""

from llm import MODEL, client, ask_llm

print("Models available to your key (for text generation):\n")
for model in client.models.list():
    actions = getattr(model, "supported_actions", None) or []
    if "generateContent" in actions and "gemini" in model.name:
        print("  ", model.name.replace("models/", ""))

if MODEL:
    print(f"\nTesting model: {MODEL}")
    print("Answer:", ask_llm("Reply with exactly: LLM connection works"))
else:
    print("\nNow add GEMINI_MODEL=<one name from the list> to your .env file")
    print("and run this script again.")
