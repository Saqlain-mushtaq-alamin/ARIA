"""Intent classification via Ollama."""

from __future__ import annotations

import json
import re
from typing import Any, Dict

import ollama


SYSTEM_PROMPT = (
    "You are an intent classifier. "
    "Return ONLY a compact JSON object with keys: intent, parameters (optional), app (optional). "
    "Use ONLY these intents: open_app, close_window, set_volume, get_clipboard, type_text, "
    "answer_question, type_generated_text. "
    "For open/close actions include parameters.app_name. "
    "For volume changes include parameters.level as a number. "
    "For answer_question and type_generated_text include parameters.prompt with the full request."
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

    if content.startswith("```"):
        content = content.strip("`\n ")

    match = re.search(r"\{[\s\S]*\}", content)
    if match:
        content = match.group(0)

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Model did not return valid JSON: {content}") from exc

    if not isinstance(parsed, dict):
        raise ValueError("Model JSON must be an object")

    return parsed
