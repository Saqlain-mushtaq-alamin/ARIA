"""Agent controller for ARIA."""

from __future__ import annotations

import json

from langchain_core.tools import Tool

from core.router import dispatch_intent
from modules.content_generator import generate_text
from modules import system_control
from safety.confirmation_engine import confirm_action, requires_confirmation
from safety.harm_classifier import blocked_response, is_blocked
from .intent_classifier import classify_intent


def _get_clipboard(_: str | None = None) -> str:
    return system_control.get_clipboard()


TOOLS = [
    Tool(
        name="open_app",
        description="Open an application by name.",
        func=system_control.open_app,
    ),
    Tool(
        name="close_window",
        description="Close a running application by name.",
        func=system_control.close_window,
    ),
    Tool(
        name="set_volume",
        description="Set system volume (0-100).",
        func=system_control.set_volume,
    ),
    Tool(
        name="get_clipboard",
        description="Read the current clipboard text.",
        func=_get_clipboard,
    ),
    Tool(
        name="type_text",
        description="Type text using keyboard automation.",
        func=system_control.type_text,
    ),
]


def get_tools() -> list[Tool]:
    return TOOLS


def process_text(user_text: str) -> str:
    """Process user input and return a response string."""
    if not user_text.strip():
        return "No input received"

    try:
        payload = classify_intent(user_text)
    except Exception as exc:
        return f"Failed to classify intent: {exc}"

    if is_blocked(payload):
        return blocked_response(payload)

    intent = str(payload.get("intent", "")).strip().lower()
    parameters = payload.get("parameters") or {}
    if not isinstance(parameters, dict):
        parameters = {}

    if intent in {"answer_question", "type_generated_text"}:
        prompt = parameters.get("prompt") or user_text
        generated = generate_text(prompt)
        if not generated:
            return "No response generated"
        if intent == "type_generated_text":
            try:
                return system_control.type_text(generated)
            except Exception as exc:
                return f"Failed to type response: {exc}"
        return generated

    if requires_confirmation(payload):
        if not confirm_action(payload, seconds=5):
            return "Action cancelled"

    try:
        result = dispatch_intent(payload)
    except Exception as exc:
        return f"Failed to execute action: {exc}"

    if isinstance(result, str):
        return result

    return json.dumps(result, ensure_ascii=True, indent=2)


def run_agent(user_text: str) -> None:
    """Run the agent for a single user input."""
    response = process_text(user_text)
    print(response)


if __name__ == "__main__":
    user_input = input("You: ")
    run_agent(user_input)
