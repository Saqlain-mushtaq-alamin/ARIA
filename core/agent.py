"""Agent controller for ARIA."""

from __future__ import annotations

import json

from .intent_classifier import classify_intent


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
