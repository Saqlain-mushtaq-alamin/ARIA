"""Agent controller for ARIA."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
import json
import os
import re
from typing import Any

from langchain_core.tools import Tool

from core.router import dispatch_intent
from modules.content_generator import generate_text
from modules import browser_agent, system_control
from scheduler.tracker import has_plan
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

        site_aliases = {
            "google maps": "https://maps.google.com",
            "maps": "https://maps.google.com",
            "youtube": "https://www.youtube.com",
            "gmail": "https://mail.google.com",
        }
        if target_lower in site_aliases:
            return {"intent": "open_url", "parameters": {"url": site_aliases[target_lower], "use_chrome": True}}
        if target_lower.endswith(" app") and len(target_lower) > 4:
            return {
                "intent": "open_app",
                "parameters": {"app_name": target[:-4].strip()},
            }
        known_apps = set(system_control.APP_ALIASES.keys())
        known_apps.update(system_control.APP_ALIASES.values())
        if "app" in target_lower and "." not in target:
            return {"intent": "open_app", "parameters": {"app_name": target}}
        if target_lower in known_apps:
            return {"intent": "open_app", "parameters": {"app_name": target}}
        if "." in target or target_lower.startswith(("http://", "https://", "localhost")):
            return {"intent": "open_url", "parameters": {"url": target}}
        if " " in target:
            # Heuristic: if the user writes a multi-word target with no dots,
            # it's far more likely to be an app name (e.g. "task manager")
            # than a URL.
            return {"intent": "open_app", "parameters": {"app_name": target}}
        return {
            "intent": "open_url",
            "parameters": {"url": f"{target}.com", "use_chrome": True},
        }

    match = re.match(
        r"^search\s+(.+?)(?:\s+on\s+(google|goolge|duckduckgo|ddg))?$",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        query = match.group(1).strip()
        engine = match.group(2) or "google"
        if engine.strip().lower() == "goolge":
            engine = "google"
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


def _parse_system_command(user_text: str) -> dict[str, object] | None:
    text = (user_text or "").strip()
    if not text:
        return None
    lowered = text.lower().strip()

    # Volume.
    m = re.match(r"^(set|change)\s+volume\s*(?:to|at)?\s*(\d{1,3})\b", lowered)
    if m:
        level = int(m.group(2))
        level = max(0, min(100, level))
        return {"intent": "set_volume", "parameters": {"level": level}}

    m = re.match(r"^(mute|unmute)\b", lowered)
    if m:
        # Minimal: map mute to 0, unmute to 30.
        return {"intent": "set_volume", "parameters": {"level": 0 if m.group(1) == "mute" else 30}}

    return None


def _parse_scheduler_command(user_text: str) -> dict[str, object] | None:
    text = (user_text or "").strip()
    if not text:
        return None

    lowered = text.lower().strip()

    # Show schedule / timeline.
    if (
        re.search(r"\b(show|display)\b.*\b(schedule|plan|timeline|agenda)\b", lowered)
        or lowered in {
        "schedule",
        "my schedule",
        "today schedule",
        "today's schedule",
        "show schedule",
        "show plan",
        "show timeline",
        }
        or lowered in {"shwo", "shwo plan", "shwo schedule", "show"}
    ):
        return {"intent": "show_schedule", "parameters": {}}

    # What's next.
    if re.search(r"\b(what'?s\s+next|what\s+should\s+i\s+do\s+next|next\s+task|what\s+now)\b", lowered):
        return {"intent": "whats_next", "parameters": {}}

    # Edit schedule.
    if lowered.startswith(("edit", "update", "change")) and re.search(
        r"\b(schedule|plan|day\s+plan|timeline|agenda)\b", lowered
    ):
        return {"intent": "edit_schedule", "parameters": {"command": f"edit schedule: {text}"}}

    if lowered.startswith(("edit schedule", "update schedule", "change schedule")):
        return {"intent": "edit_schedule", "parameters": {"command": text}}

    # Schedule creation.
    if lowered.startswith(
        (
            "create a new plan",
            "create new plan",
            "create a new day plan",
            "create new day plan",
            "create day plan",
            "new plan",
            "make a new plan",
            "make new plan",
            "create a new schedule",
            "create new schedule",
            "new schedule",
            "schedule my day",
            "schedule",
            "plan my day",
            "make a schedule",
            "make my schedule",
            "plan:",
            "plan",
        )
    ):
        use_reference = bool(
            has_plan()
            and re.search(r"\b(new)\b", lowered)
            and re.search(r"\b(plan|schedule)\b", lowered)
        )
        stripped = re.sub(
            r"^(create\s+(a\s+)?new\s+((day\s+)?plan|schedule)|create\s+day\s+plan|make\s+(a\s+)?new\s+((day\s+)?plan|schedule)|new\s+((day\s+)?plan|schedule)|schedule(\s+my\s+day)?|plan\s+my\s+day|make\s+a\s+schedule|make\s+my\s+schedule|plan)\s*[:\-]?\s*",
            "",
            text,
            flags=re.IGNORECASE,
        ).strip()

        # Voice users often say: "create a new plan: move gym to 9am".
        # That should be treated as an edit applied on top of the existing plan,
        # not a brand-new task named "move gym".
        if use_reference and stripped.lower().startswith(
            ("move ", "reschedule ", "add ", "remove ", "make ", "set ", "put ")
        ):
            return {"intent": "edit_schedule", "parameters": {"command": f"edit schedule: {stripped}"}}

        params: dict[str, object] = {"text": stripped or text}
        if use_reference:
            params["use_reference"] = True
        return {"intent": "create_schedule", "parameters": params}

    # If there is already a plan and the user starts with a simple edit verb, assume edit.
    if has_plan() and lowered.startswith(("add ", "remove ", "move ", "reschedule ", "make ", "set ", "put ")):
        return {"intent": "edit_schedule", "parameters": {"command": f"edit schedule: {text}"}}

    return None


def process_text(user_text: str) -> str:
    """Process user input and return a response string."""
    if not user_text.strip():
        return "No input received"

    payload: dict[str, Any] = {}

    parsed_payload = _parse_system_command(user_text)
    if isinstance(parsed_payload, dict):
        payload = parsed_payload
    else:
        parsed_payload = _parse_scheduler_command(user_text)
    if isinstance(parsed_payload, dict):
        payload = parsed_payload
    else:
        parsed_payload = _parse_browser_command(user_text)
        if isinstance(parsed_payload, dict):
            payload = parsed_payload
        else:
            try:
                classified_payload = classify_intent(user_text)
            except Exception as exc:
                return f"Failed to classify intent: {exc}"
            if isinstance(classified_payload, dict):
                payload = classified_payload

    if payload.get("intent") == "open_app":
        params = payload.get("parameters") or {}
        app_name = params.get("app_name") or params.get("name") or payload.get("app")
        if app_name:
            normalized = str(app_name).strip().lower().rstrip(" .,!?:;")
            known_apps = set(system_control.APP_ALIASES.keys())
            known_apps.update(system_control.APP_ALIASES.values())
            # Only auto-convert an unknown app name into a URL when it's a single token.
            # Names with spaces (e.g. "firefox app") are far more likely to be apps,
            # and would create invalid URLs.
            if normalized not in known_apps and "." not in normalized and " " not in normalized:
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

    if intent in {"", "unknown"}:
        return (
            "I didn't understand that. Try: 'show schedule', 'what's next', "
            "'schedule my day: ...', or 'edit schedule: move gym to 7pm'."
        )

    def build_prompt_with_memory(prompt: str) -> str:
        vibe_block = ""
        try:
            vibe_path = os.path.normpath(
                os.path.join(os.path.dirname(__file__), "..", "memory", "emotion_state.json")
            )
            if os.path.exists(vibe_path):
                with open(vibe_path, "r", encoding="utf-8") as f:
                    vibe = json.load(f)
                last_update = vibe.get("last_update_utc")
                if isinstance(last_update, str) and last_update:
                    try:
                        last_dt = datetime.fromisoformat(last_update)
                        if last_dt.tzinfo is None:
                            last_dt = last_dt.replace(tzinfo=timezone.utc)
                    except Exception:
                        last_dt = None
                else:
                    last_dt = None

                # Only include if reasonably fresh.
                if last_dt is not None and (_utc := datetime.now(timezone.utc)) - last_dt <= timedelta(minutes=90):
                    dominant = vibe.get("window_dominant_state") or vibe.get("last_state")
                    dist = vibe.get("window_distribution_percent")
                    samples = vibe.get("samples")
                    window_minutes = vibe.get("window_minutes")
                    vibe_block = (
                        "Vibe (from webcam):\n"
                        f"- window={window_minutes}m, samples={samples}, dominant={dominant}\n"
                        f"- distribution%={dist}\n"
                        f"- last={vibe.get('last_state')} @ {vibe.get('last_timestamp_utc')}\n"
                    )
        except Exception:
            vibe_block = ""

        matches = []
        if search_memory is not None:
            try:
                matches = search_memory(prompt, top_k=5)
            except Exception:
                matches = []

        memory_block = ""
        if matches:
            memory_lines: list[str] = []
            for m in matches:
                text: str | None = getattr(m, "text", None)
                if text is None and isinstance(m, Mapping):
                    text = str(m.get("text") or "")
                if text is None:
                    text = str(m)

                metadata_obj: Any = getattr(m, "metadata", None)
                if metadata_obj is None and isinstance(m, Mapping):
                    metadata_obj = m.get("metadata")

                date: Any = None
                if isinstance(metadata_obj, Mapping):
                    date = metadata_obj.get("date") or metadata_obj.get("timestamp")

                if date:
                    memory_lines.append(f"- {text} ({date})")
                else:
                    memory_lines.append(f"- {text}")

            memory_block = "Memory:\n" + "\n".join(memory_lines) + "\n"

        if not vibe_block and not memory_block:
            return prompt

        context_parts = []
        if vibe_block:
            context_parts.append(vibe_block.strip())
        if memory_block:
            context_parts.append(memory_block.strip())

        return "Use the following context if relevant.\n\n" + "\n\n".join(context_parts) + f"\n\nUser: {prompt}"

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
