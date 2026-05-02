"""Rule-based safety filter for dangerous intents."""

from __future__ import annotations

from typing import Any, Dict


BLOCKED_INTENTS = {
    "delete_files",
    "shutdown_system",
    "format_disk",
}


def is_blocked(payload: Dict[str, Any]) -> bool:
    """Return True if the intent is blocked."""
    intent = str(payload.get("intent", "")).strip().lower()
    return intent in BLOCKED_INTENTS


def blocked_response(payload: Dict[str, Any]) -> str:
    """Return a safe response for blocked actions."""
    intent = str(payload.get("intent", "")).strip()
    if intent:
        return f"Blocked unsafe action: {intent}"
    return "Blocked unsafe action"
