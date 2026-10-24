"""Check which models in .env still work (30-second timeout, tiny request)."""

import os
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

client = genai.Client(
    api_key=os.getenv("GEMINI_API_KEY"),
    http_options=types.HttpOptions(timeout=30000),   # 30 seconds
)

models = [m.strip() for m in os.getenv("GEMINI_MODEL", "").split(",") if m.strip()]
print("Models in .env:", models, "\n")

for model in models:
    try:
        reply = client.models.generate_content(model=model, contents="Reply with: OK")
        print(f"  {model}: WORKS -> {reply.text.strip()[:30]}")
    except Exception as error:
        text = str(error)
        if "PerDay" in text:
            print(f"  {model}: daily quota finished")
        elif "503" in text:
            print(f"  {model}: server busy, try later")
        else:
            print(f"  {model}: error -> {text[:120]}")
