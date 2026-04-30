"""Intent classification via Ollama."""

from __future__ import annotations

import json
from typing import Any, Dict

import ollama


SYSTEM_PROMPT = (
    "You are an intent classifier. "
    "Return ONLY a compact JSON object with keys: intent, app (optional)."
)


def classify_intent(user_text: str, model: str = "llama3") -> Dict[str, Any]:
    """Classify user intent using Ollama and return a dict.

    Args:
        user_text: Raw user input to classify.
        model: Ollama model name.

    Returns:
        Parsed JSON object as a Python dict.
    """
    response = ollama.chat(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ],
        options={"temperature": 0},
    )

    content = response.get("message", {}).get("content", "").strip()
    if not content:
        raise ValueError("Empty response from model")

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Model did not return valid JSON: {content}") from exc

    if not isinstance(parsed, dict):
        raise ValueError("Model JSON must be an object")

    return parsed
