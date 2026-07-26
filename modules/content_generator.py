"""LLM-based text generation helpers."""

from __future__ import annotations

import ollama

try:
    from vision.screen_reader import llm_busy_context
except Exception:
    from contextlib import nullcontext as llm_busy_context  # type: ignore


def _get_model() -> str:
    """Get the configured LLM model from settings, with fallback."""
    try:
        from config.settings import get as get_setting
        return get_setting("llm.model", "llama3")
    except Exception:
        return "llama3"


def _get_temperature() -> float:
    """Get the configured temperature from settings, with fallback."""
    try:
        from config.settings import get as get_setting
        return float(get_setting("llm.temperature", 0.7))
    except Exception:
        return 0.7


def generate_text(prompt: str, model: str = "") -> str:
    """Generate text for a given prompt using Ollama.

    Uses the model from settings unless explicitly overridden.
    """
    if not prompt.strip():
        return ""

    effective_model = model if model else _get_model()
    temperature = _get_temperature()

    _ARIA_SYSTEM = (
        "You are ARIA — an Autonomous Reasoning & Intelligent Assistant. "
        "You are the user's smart AI companion, helper, co-worker, and friend. "
        "You are thoughtful, warm, capable, and always professional. "
        "You always address the user as 'Sir'. "
        "Be concise but complete. If generating creative content (stories, poems, emails), "
        "produce high-quality, well-structured content. "
        "If answering questions, be accurate, clear, and helpful. "
        "Never refuse reasonable requests. Always try your best to assist Sir."
    )

    with llm_busy_context():
        response = ollama.chat(
            model=effective_model,
            messages=[
                {"role": "system", "content": _ARIA_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            options={"temperature": temperature},
        )

    content = response.get("message", {}).get("content", "")
    return str(content).strip()

