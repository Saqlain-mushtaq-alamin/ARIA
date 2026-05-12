"""LLM-based text generation helpers."""

from __future__ import annotations

import ollama

try:
    from vision.screen_reader import llm_busy_context
except Exception:
    from contextlib import nullcontext as llm_busy_context  # type: ignore


def generate_text(prompt: str, model: str = "llama3") -> str:
    """Generate text for a given prompt using Ollama."""
    if not prompt.strip():
        return ""

    with llm_busy_context():
        response = ollama.chat(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a helpful writing assistant. "
                        "Answer clearly and concisely unless asked for a story."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            options={"temperature": 0.7},
        )

    content = response.get("message", {}).get("content", "")
    return str(content).strip()
