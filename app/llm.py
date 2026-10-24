"""
The ONLY file that talks to the LLM.
To switch from Gemini to Claude later, change this file only.
GEMINI_MODEL in .env can hold several names separated by commas:
when one runs out of daily quota, the next one is used automatically.
"""

import os
import time

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")
MODELS = [m.strip() for m in os.getenv("GEMINI_MODEL", "").split(",") if m.strip()]
MODEL = MODELS[0] if MODELS else ""

client = genai.Client(api_key=API_KEY)
_current = 0


def ask_llm(prompt, system=None, temperature=0.3, retries=3):
    """Send a prompt to the model and return the answer as text."""
    global _current
    if not MODELS:
        raise SystemExit("GEMINI_MODEL is not set in your .env file.")

    config = types.GenerateContentConfig(
        system_instruction=system,
        temperature=temperature,
    )

    attempt = 0
    while attempt < retries:
        model = MODELS[_current]
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=config,
            )
            return response.text
        except Exception as error:
            message = str(error)
            # Daily quota finished, or model not available: use the next model
            if "PerDay" in message or "NOT_FOUND" in message:
                print(f"  [{model} is unavailable today, switching model]")
                _current += 1
                if _current >= len(MODELS):
                    raise SystemExit(
                        "All models in GEMINI_MODEL are out of quota. "
                        "Add more names (comma-separated) or try tomorrow."
                    )
                continue
            # Temporary problem (busy server, per-minute limit): wait and retry
            attempt += 1
            print(f"LLM error (try {attempt}/{retries}): {message[:150]}")
            if attempt == retries:
                raise
            time.sleep(10 * attempt)
