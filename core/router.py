"""Route intents to system control actions."""

from __future__ import annotations

from typing import Any, Callable, Dict

from modules import browser_agent, system_control

from scheduler.tracker import create_schedule_from_text, edit_schedule, show_schedule, whats_next


INTENT_REGISTRY: Dict[str, Callable[..., Any]] = {
    "open_app": system_control.open_app,
    "close_window": system_control.close_window,
    "set_volume": system_control.set_volume,
    "get_clipboard": system_control.get_clipboard,
    "type_text": system_control.type_text,
    "open_url": browser_agent.open_url,
    "search_web": browser_agent.search_web,
    "click_element": browser_agent.click_element,
    "fill_form": browser_agent.fill_form,
    "extract_text": browser_agent.extract_text,

    # Scheduler
    "create_schedule": create_schedule_from_text,
    "show_schedule": show_schedule,
    "whats_next": whats_next,
    "edit_schedule": edit_schedule,
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
    "open_url": "open_url",
    "browse": "open_url",
    "go_to": "open_url",
    "visit": "open_url",
    "search": "search_web",
    "web_search": "search_web",
    "click": "click_element",
    "form_fill": "fill_form",
    "extract": "extract_text",

    # Scheduler aliases
    "schedule": "create_schedule",
    "plan_day": "create_schedule",
    "make_schedule": "create_schedule",
    "show_plan": "show_schedule",
    "show_timeline": "show_schedule",
    "next_task": "whats_next",
    "what_next": "whats_next",
    "edit_plan": "edit_schedule",
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

    if intent == "open_url":
        url = parameters.get("url") or payload.get("url")
        if not url:
            raise ValueError("URL is required")
        headless = parameters.get("headless", False)
        use_chrome = parameters.get("use_chrome", True)
        return handler(str(url), headless=bool(headless), use_chrome=bool(use_chrome))

    if intent == "search_web":
        query = parameters.get("query") or payload.get("query")
        if not query:
            raise ValueError("Search query is required")
        engine = parameters.get("engine", "google")
        max_results = parameters.get("max_results", 5)
        headless = parameters.get("headless", False)
        use_chrome = parameters.get("use_chrome", True)
        return handler(
            str(query),
            max_results=int(max_results),
            engine=str(engine),
            headless=bool(headless),
            use_chrome=bool(use_chrome),
        )

    if intent == "click_element":
        selector = parameters.get("selector") or payload.get("selector")
        if not selector:
            raise ValueError("Selector is required")
        headless = parameters.get("headless", False)
        use_chrome = parameters.get("use_chrome", True)
        return handler(
            str(selector),
            headless=bool(headless),
            use_chrome=bool(use_chrome),
        )

    if intent == "fill_form":
        form_data = parameters or payload.get("data")
        if not form_data:
            raise ValueError("Form data is required")
        if isinstance(form_data, dict):
            headless = form_data.get("headless", False)
            use_chrome = form_data.get("use_chrome", True)
        else:
            headless = False
            use_chrome = True
        return handler(form_data, headless=bool(headless), use_chrome=bool(use_chrome))

    if intent == "extract_text":
        url = parameters.get("url") or payload.get("url")
        if not url:
            raise ValueError("URL is required")
        max_chars = parameters.get("max_chars", 5000)
        headless = parameters.get("headless", False)
        use_chrome = parameters.get("use_chrome", True)
        return handler(
            str(url),
            max_chars=int(max_chars),
            headless=bool(headless),
            use_chrome=bool(use_chrome),
        )

    return handler(**parameters)
