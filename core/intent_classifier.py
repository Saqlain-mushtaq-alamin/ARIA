"""Intent classification via Ollama."""

from __future__ import annotations

import json
import re
from typing import Any, Dict

import ollama


SYSTEM_PROMPT = (
    "You are an intent classifier. "
    "Return ONLY a compact JSON object with keys: intent, parameters (optional), app (optional). "
    "Do not include markdown, code blocks, explanations, lists, or extra keys. "
    "Use ONLY these intents: open_app, close_window, set_volume, get_clipboard, type_text, "
    "answer_question, type_generated_text, open_url, search_web, click_element, fill_form, extract_text, "
    "create_schedule, show_schedule, whats_next, edit_schedule. "
    "For open/close actions include parameters.app_name. "
    "For volume changes include parameters.level as a number. "
    "For answer_question and type_generated_text include parameters.prompt with the full request. "
    "For open_url include parameters.url. "
    "For search_web include parameters.query and optional parameters.engine (google or duckduckgo). "
    "For click_element include parameters.selector. "
    "For fill_form include parameters.url, parameters.fields (object), and optional parameters.submit. "
    "For extract_text include parameters.url and optional parameters.max_chars. "
    "For create_schedule include parameters.text with the user's schedule sentence. "
    "For show_schedule no parameters are required. "
    "For whats_next no parameters are required. "
    "For edit_schedule include parameters.command with the edit request."
)


def _try_extract_json_object(text: str) -> Dict[str, Any] | None:
    """Best-effort extraction of the first valid JSON object from a messy model response."""

    if not text:
        return None

    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue

        if ch == '"':
            in_str = True
            continue

        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : i + 1]
                try:
                    obj = json.loads(candidate)
                except Exception:
                    return None
                return obj if isinstance(obj, dict) else None

    return None


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
        return parsed if isinstance(parsed, dict) else {"intent": "unknown", "parameters": {}, "app": None}
    except json.JSONDecodeError:
        extracted = _try_extract_json_object(content)
        if extracted is not None:
            return extracted

    return {"intent": "unknown", "parameters": {}, "app": None}
