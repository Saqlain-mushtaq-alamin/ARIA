"""Agent controller for ARIA."""

from __future__ import annotations

import json
import re

from langchain_core.tools import Tool

from core.router import dispatch_intent
from modules.content_generator import generate_text
from modules import browser_agent, system_control
from safety.confirmation_engine import confirm_action, requires_confirmation
from safety.harm_classifier import blocked_response, is_blocked
from .intent_classifier import classify_intent

try:
    from memory.vector_store import search_memory
except Exception:  # pragma: no cover
    search_memory = None


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
    Tool(
        name="open_url",
        description="Open a URL in a browser and return the page title.",
        func=browser_agent.open_url,
    ),
    Tool(
        name="search_web",
        description="Search the web and return result titles and URLs.",
        func=browser_agent.search_web,
    ),
    Tool(
        name="click_element",
        description="Click a CSS selector on the last opened page.",
        func=browser_agent.click_element,
    ),
    Tool(
        name="fill_form",
        description="Fill a form on a page using selector/value mappings.",
        func=browser_agent.fill_form,
    ),
    Tool(
        name="extract_text",
        description="Extract visible text from a web page.",
        func=browser_agent.extract_text,
    ),
]


def get_tools() -> list[Tool]:
    return TOOLS


def _parse_browser_command(user_text: str) -> dict[str, object] | None:
    text = user_text.strip()
    if not text:
        return None

    match = re.match(r"^(open|go to|visit)\s+(.+)$", text, flags=re.IGNORECASE)
    if match:
        target = match.group(2).strip().rstrip(" .,!?:;")
        target_lower = target.lower()
        known_apps = set(system_control.APP_ALIASES.keys())
        known_apps.update(system_control.APP_ALIASES.values())
        if "app" in target_lower and "." not in target:
            return {"intent": "open_app", "parameters": {"app_name": target}}
        if target_lower in known_apps:
            return {"intent": "open_app", "parameters": {"app_name": target}}
        if "." in target or target_lower.startswith(("http://", "https://", "localhost")):
            return {"intent": "open_url", "parameters": {"url": target}}
        return {
            "intent": "open_url",
            "parameters": {"url": f"{target}.com", "use_chrome": True},
        }

    match = re.match(
        r"^search\s+(.+?)(?:\s+on\s+(google|duckduckgo|ddg))?$",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        query = match.group(1).strip()
        engine = match.group(2) or "google"
        return {
            "intent": "search_web",
            "parameters": {"query": query, "engine": engine, "use_chrome": True},
        }

    match = re.match(r"^(google|duckduckgo|ddg)\s+(.+)$", text, flags=re.IGNORECASE)
    if match:
        engine = match.group(1).strip()
        query = match.group(2).strip()
        return {
            "intent": "search_web",
            "parameters": {"query": query, "engine": engine, "use_chrome": True},
        }

    match = re.match(r"^click\s+(.+)$", text, flags=re.IGNORECASE)
    if match:
        selector = match.group(1).strip()
        return {"intent": "click_element", "parameters": {"selector": selector}}

    match = re.match(r"^extract\s+text\s+(.+)$", text, flags=re.IGNORECASE)
    if match:
        url = match.group(1).strip()
        return {"intent": "extract_text", "parameters": {"url": url}}

    return None


def process_text(user_text: str) -> str:
    """Process user input and return a response string."""
    if not user_text.strip():
        return "No input received"

    payload = _parse_browser_command(user_text)
    if payload is None:
        try:
            payload = classify_intent(user_text)
        except Exception as exc:
            return f"Failed to classify intent: {exc}"

    if payload and payload.get("intent") == "open_app":
        params = payload.get("parameters") or {}
        app_name = params.get("app_name") or params.get("name") or payload.get("app")
        if app_name:
            normalized = str(app_name).strip().lower().rstrip(" .,!?:;")
            known_apps = set(system_control.APP_ALIASES.keys())
            known_apps.update(system_control.APP_ALIASES.values())
            if normalized not in known_apps and "." not in normalized:
                payload = {
                    "intent": "open_url",
                    "parameters": {"url": f"{normalized}.com", "use_chrome": True},
                }

    if is_blocked(payload):
        return blocked_response(payload)

    intent = str(payload.get("intent", "")).strip().lower()
    parameters = payload.get("parameters") or {}
    if not isinstance(parameters, dict):
        parameters = {}

    def build_prompt_with_memory(prompt: str) -> str:
        if search_memory is None:
            return prompt
        try:
            matches = search_memory(prompt, top_k=5)
        except Exception:
            return prompt

        if not matches:
            return prompt

        memory_lines: list[str] = []
        for m in matches:
            date = m.metadata.get("date") or m.metadata.get("timestamp")
            if date:
                memory_lines.append(f"- {m.text} ({date})")
            else:
                memory_lines.append(f"- {m.text}")

        memory_block = "\n".join(memory_lines)
        return (
            "Use the following memory as context if relevant.\n\n"
            f"Memory:\n{memory_block}\n\n"
            f"User: {prompt}"
        )

    if intent in {"answer_question", "type_generated_text"}:
        prompt = parameters.get("prompt") or user_text
        augmented_prompt = build_prompt_with_memory(str(prompt))
        generated = generate_text(augmented_prompt)
        if not generated:
            return "No response generated"
        if intent == "type_generated_text":
            try:
                return system_control.type_text(generated)
            except Exception as exc:
                return f"Failed to type response: {exc}"
        return generated

    if intent == "type_text":
        text_value = parameters.get("text") or parameters.get("content")
        if text_value is None or not str(text_value).strip():
            return "No text detected"

    if requires_confirmation(payload):
        if not confirm_action(payload, seconds=5):
            return "Action cancelled"

    try:
        result = dispatch_intent(payload)
    except Exception as exc:
        return f"Failed to execute action: {exc}"

    if isinstance(result, str):
        return result

    return json.dumps(result, ensure_ascii=True, indent=2)


def run_agent(user_text: str) -> None:
    """Run the agent for a single user input."""
    response = process_text(user_text)
    print(response)


if __name__ == "__main__":
    user_input = input("You: ")
    run_agent(user_input)
