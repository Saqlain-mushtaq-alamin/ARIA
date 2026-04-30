"""Agent controller for ARIA."""

from __future__ import annotations

from .intent_classifier import classify_intent


def run_agent(user_text: str) -> None:
    """Run the agent for a single user input."""
    result = classify_intent(user_text)
    print(result)


if __name__ == "__main__":
    user_input = input("You: ")
    run_agent(user_input)
