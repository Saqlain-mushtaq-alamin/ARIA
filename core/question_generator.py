"""Contextual follow-up question generator.

This module makes ARIA feel less robotic by asking better clarifying questions
based on long-term semantic memory.

Example:
- User: "play something"
- Memory: "User likes lo-fi study music"
- Follow-up: "Do you want lo-fi study music like last time?"
"""

from __future__ import annotations

from typing import List

from modules.content_generator import generate_text


try:
    from memory.vector_store import search_memory
except Exception:  # pragma: no cover
    search_memory = None


def _format_memory_lines(query: str, top_k: int) -> List[str]:
    if search_memory is None:
        return []
    try:
        matches = search_memory(query, top_k=top_k)
    except Exception:
        return []

    lines: List[str] = []
    for m in matches:
        if not m.text:
            continue
        date = m.metadata.get("date") or m.metadata.get("timestamp")
        if date:
            lines.append(f"- {m.text} ({date})")
        else:
            lines.append(f"- {m.text}")
    return lines


def generate_followup_question(user_query: str, top_k: int = 5) -> str:
    """Generate a short, contextual follow-up question.

    If no useful memory exists, falls back to a generic clarification question.
    """
    query = (user_query or "").strip()
    if not query:
        return "What would you like to do?"

    memory_lines = _format_memory_lines(query, top_k=top_k)

    if memory_lines:
        memory_block = "\n".join(memory_lines)
        prompt = (
            "You generate ONE short follow-up question to clarify the user's intent. "
            "Use the Memory ONLY if it is relevant. "
            "Be natural and specific. Output ONLY the question text.\n\n"
            f"User: {query}\n\n"
            f"Memory:\n{memory_block}\n"
        )
        question = generate_text(prompt).strip()
        if question.endswith("?") and len(question) >= 5:
            return question

    # Generic fallback (no memory or LLM didn't produce a clean question)
    return "What would you like me to play/search/open?"
