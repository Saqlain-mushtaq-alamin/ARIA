"""Route intents to system control actions."""

from __future__ import annotations

from typing import Any, Callable, Dict

from modules import system_control


INTENT_REGISTRY: Dict[str, Callable[..., Any]] = {
    "open_app": system_control.open_app,
    "close_window": system_control.close_window,
    "set_volume": system_control.set_volume,
    "get_clipboard": system_control.get_clipboard,
    "type_text": system_control.type_text,
}

INTENT_ALIASES = {
    "open": "open_app",
    "open_application": "open_app",
    "launch_app": "open_app",
    "close": "close_window",
    "close_app": "close_window",
    "close_application": "close_window",
    "volume_change": "set_volume",
    "change_volume": "set_volume",
    "set_sound": "set_volume",
    "clipboard": "get_clipboard",
    "read_clipboard": "get_clipboard",
    "type": "type_text",
    "typing": "type_text",
}


def _normalize_intent(intent: str) -> str:
    key = intent.strip().lower()
    return INTENT_ALIASES.get(key, key)


def dispatch_intent(payload: Dict[str, Any]) -> Any:
    """Dispatch an intent payload to the appropriate function."""
    if not isinstance(payload, dict):
        raise ValueError("Payload must be a dict")

    intent = payload.get("intent")
    if not intent:
        raise ValueError("Missing intent")

    intent = _normalize_intent(str(intent))

    handler = INTENT_REGISTRY.get(intent)
    if handler is None:
        raise KeyError(f"Unknown intent: {intent}")

    parameters = payload.get("parameters") or {}
    if not isinstance(parameters, dict):
        parameters = {}

    if intent in {"open_app", "close_window"}:
        name = (
            parameters.get("app_name")
            or parameters.get("name")
            or payload.get("app")
        )
        if not name:
            raise ValueError("App name is required")
        return handler(name)

    if intent == "set_volume":
        level = (
            parameters.get("level")
            or parameters.get("volume")
            or parameters.get("value")
        )
        if level is None:
            raise ValueError("Volume level is required")
        return handler(int(level))

    if intent == "type_text":
        text = parameters.get("text") or parameters.get("content")
        if text is None:
            raise ValueError("Text is required")
        return handler(text)

    if intent == "get_clipboard":
        return handler()

    return handler(**parameters)
