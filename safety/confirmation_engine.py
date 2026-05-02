"""Confirmation workflow for sensitive actions."""

from __future__ import annotations

import time
from typing import Any, Dict, Optional


CONFIRM_INTENTS = {
    "close_window",
    "set_volume",
}


def requires_confirmation(payload: Dict[str, Any]) -> bool:
    """Return True if the intent needs confirmation."""
    intent = str(payload.get("intent", "")).strip().lower()
    return intent in CONFIRM_INTENTS


def confirm_action(
    payload: Dict[str, Any],
    seconds: int = 5,
    cancel_checker: Optional[callable] = None,
) -> bool:
    """Return True if the action is confirmed after a countdown."""
    intent = str(payload.get("intent", "")).strip() or "action"
    print(f"Confirming {intent} in {seconds} seconds... (Ctrl+C to cancel)")
    try:
        for remaining in range(seconds, 0, -1):
            if cancel_checker and cancel_checker():
                return False
            print(f"{remaining}...")
            time.sleep(1)
    except KeyboardInterrupt:
        return False

    return True
