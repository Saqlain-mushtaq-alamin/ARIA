"""Agent controller for ARIA."""

from __future__ import annotations

import json

from langchain_core.tools import Tool

from modules import system_control
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
    result = classify_intent(user_text)
    return json.dumps(result, ensure_ascii=True, indent=2)


def run_agent(user_text: str) -> None:
    """Run the agent for a single user input."""
    response = process_text(user_text)
    print(response)


if __name__ == "__main__":
    user_input = input("You: ")
    run_agent(user_input)
