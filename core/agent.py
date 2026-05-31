"""Agent controller for ARIA.

Key improvements over the original:

1. TWO-TRACK ROUTING
   - Conversational input  →  LLM gives a warm text reply (no JSON, no tools)
   - Actionable input      →  intent classified + tool dispatched

2. COPILOT-STYLE STEP NARRATION
   Every action (single or multi-step) is narrated:
     ▶  Step 1/3 — Opening Notepad...
     ✓  Step 1/3 — Notepad opened successfully.
     ▶  Step 2/3 — Typing story...
     ✓  Step 2/3 — Text typed.
     ▶  Step 3/3 — Saving file to Desktop...
     ✓  Step 3/3 — File saved to C:\\Users\\...\\Desktop\\story.txt
     ━━  All 3 steps completed.

3. SMART QUESTION ENGINE
   Before executing, the agent checks:
   a) Are required parameters missing?   →  asks a clarifying question
   b) Is this a risky/irreversible action? →  asks for confirmation
   c) Could one extra question improve the result?  →  asks an enrichment question
   The user's answer is fed back into the classifier for a second pass.

4. MULTI-STEP COMMAND SUPPORT
   "Open notepad, write a story about a lazy cat, save it to the desktop"
   is decomposed into an ordered steps list and each step is narrated live.

5. CONTENT GENERATION IN STEPS
   Steps with parameters.generate=true trigger the LLM to produce the content
   (story, email, poem, etc.) before passing it to type_text or save_file.

6. INTERNET DATA TOOLS
   get_weather, get_news, search_papers, get_stock are all first-class intents.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Generator

from langchain_core.tools import Tool

from core.router import dispatch_intent, dispatch_multi_step, INTENT_REGISTRY
from core.question_generator import (
    generate_clarification_question,
    generate_confirmation_question,
    generate_enrichment_question,
    needs_clarification,
    requires_confirmation,
    is_enrichment_eligible,
    _infer_missing_fields,
)
from modules import browser_agent, system_control
from modules.content_generator import generate_text
from safety.confirmation_engine import confirm_action
from safety.harm_classifier import (
    blocked_response,
    is_blocked,
    assess_risk,
    SAFE,
    CONFIRM,
    DANGEROUS,
    BLOCKED,
)
from safety.audit_log import (
    log_action,
    OUTCOME_SUCCESS,
    OUTCOME_BLOCKED,
    OUTCOME_CANCELLED,
    OUTCOME_ERROR,
)
from scheduler.tracker import has_plan
from .intent_classifier import classify_intent, is_conversational

try:
    from memory.vector_store import search_memory
except Exception:
    search_memory = None

try:
    from vision.screen_reader import (
        start_screen_reader,
        get_screen_context_block,
        llm_busy_context,
    )
    _SCREEN_READER_AVAILABLE = True
except Exception:
    _SCREEN_READER_AVAILABLE = False
    def get_screen_context_block() -> str:          # type: ignore[misc]
        return ""
    def llm_busy_context() -> Any:                   # type: ignore[misc]
        from contextlib import nullcontext
        return nullcontext()


_CANCEL_EVENT = threading.Event()


def request_cancel() -> None:
    _CANCEL_EVENT.set()


def clear_cancel() -> None:
    _CANCEL_EVENT.clear()


def cancel_requested() -> bool:
    return _CANCEL_EVENT.is_set()


# ─────────────────────────────────────────────────────────────────────────────
# LangChain tool wrappers (kept for external tool-call compatibility)
# ─────────────────────────────────────────────────────────────────────────────

def _get_clipboard(_: str | None = None) -> str:
    return system_control.get_clipboard()


TOOLS = [
    Tool(name="open_app",        description="Open an application by name.",              func=system_control.open_app),
    Tool(name="close_window",    description="Close a running application by name.",      func=system_control.close_window),
    Tool(name="set_volume",      description="Set system volume (0-100).",                func=system_control.set_volume),
    Tool(name="get_clipboard",   description="Read the current clipboard text.",          func=_get_clipboard),
    Tool(name="type_text",       description="Type text using keyboard automation.",      func=system_control.type_text),
    Tool(name="open_url",        description="Open a URL in the browser.",                func=browser_agent.open_url),
    Tool(name="search_web",      description="Search the web and return results.",        func=browser_agent.search_web),
    Tool(name="click_element",   description="Click a CSS selector on the last page.",   func=browser_agent.click_element),
    Tool(name="fill_form",       description="Fill a form on a web page.",                func=browser_agent.fill_form),
    Tool(name="extract_text",    description="Extract visible text from a web page.",    func=browser_agent.extract_text),
]


def get_tools() -> list[Tool]:
    return TOOLS


# ─────────────────────────────────────────────────────────────────────────────
# Narration helpers — Copilot-style step announcements
# ─────────────────────────────────────────────────────────────────────────────

_INTENT_LABELS: dict[str, str] = {
    "open_app":         "Opening {app_name}",
    "open_folder":      "Opening folder: {path}",
    "close_window":     "Closing {app_name}",
    "set_volume":       "Setting volume to {level}%",
    "set_brightness":   "Setting brightness to {level}%",
    "get_clipboard":    "Reading clipboard",
    "type_text":        "Typing text",
    "screenshot":       "Taking screenshot",
    "shutdown":         "Shutting down computer",
    "restart":          "Restarting computer",
    "lock_screen":      "Locking screen",
    "sleep":            "Putting computer to sleep",
    "toggle_wifi":      "Turning Wi-Fi {state}",
    "toggle_bluetooth": "Turning Bluetooth {state}",
    "toggle_airplane":  "Turning Airplane mode {state}",
    "open_file":        "Opening file: {path}",
    "save_file":        "Saving file to: {path}",
    "create_file":      "Creating file: {path}",
    "delete_file":      "Deleting: {path}",
    "list_directory":   "Listing folder: {path}",
    "move_file":        "Moving {source} → {destination}",
    "copy_file":        "Copying {source} → {destination}",
    "open_url":         "Opening URL: {url}",
    "search_web":       "Searching the web for: {query}",
    "click_element":    "Clicking element: {selector}",
    "fill_form":        "Filling form",
    "extract_text":     "Extracting text from: {url}",
    "get_weather":      "Fetching weather for: {location}",
    "get_news":         "Fetching news{topic_suffix}",
    "search_papers":    "Searching {source} for: {query}",
    "get_stock":        "Fetching stock price for: {symbol}",
    "download":         "Downloading: {target}",
    "create_schedule":  "Building your daily schedule",
    "show_schedule":    "Showing your schedule",
    "whats_next":       "Checking what's next on your schedule",
    "edit_schedule":    "Editing your schedule",
    "answer_question":  "Thinking...",
    "conversational":   "Thinking...",
    "type_generated_text": "Generating and typing text",
    "activate_kinetic_mode": "Activating kinetic mode",
    "deactivate_kinetic_mode": "Deactivating kinetic mode",
    # Upgrade features
    "comment_on_post":    "Generating comment for post",
    "explain_selected":   "Explaining selected text",
    "start_focus_mode":   "Starting deep work / focus mode",
    "stop_focus_mode":    "Stopping focus mode",
    "decompose_goal":     "Breaking down your goal",
    "show_settings":      "Showing settings",
    "change_setting":     "Updating setting: {key}",
    "query_knowledge":    "Searching knowledge graph for: {topic}",
    "show_habits":        "Showing your habits",
    "mark_habit":         "Logging habit: {habit_name}",
    "show_profile":       "Showing your profile",
}


def _label_for_step(intent: str, parameters: dict) -> str:
    """Build a human-readable label for a step's narration."""
    template = _INTENT_LABELS.get(intent, f"Running: {intent.replace('_', ' ')}")
    try:
        params = {k: (v or "") for k, v in parameters.items()}
        if intent == "download" and not params.get("target"):
            params["target"] = params.get("url") or params.get("query") or params.get("title") or ""
        params.setdefault("topic_suffix",
                          f" on '{parameters.get('topic')}'" if parameters.get("topic") else "")
        return template.format(**params)
    except KeyError:
        return template.split("{")[0].strip() or intent.replace("_", " ").capitalize()


def _step_header(step_num: int, total: int, label: str) -> str:
    if total == 1:
        return f"▶  {label}..."
    return f"▶  Step {step_num}/{total} — {label}..."


def _step_success(step_num: int, total: int, label: str, result: str) -> str:
    prefix = f"✓  Step {step_num}/{total} — " if total > 1 else "✓  "
    # Trim very long tool outputs for the narration line
    short = result.strip()
    if len(short) > 120:
        short = short[:117] + "..."
    return f"{prefix}{label}.\n    {short}" if short else f"{prefix}{label}."


def _step_error(step_num: int, total: int, label: str, error: str) -> str:
    prefix = f"✗  Step {step_num}/{total} — " if total > 1 else "✗  "
    return f"{prefix}{label} failed.\n    Error: {error}"


def _divider(total: int) -> str:
    if total > 1:
        return f"━━  All {total} steps completed."
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Short-term context tracker (last interaction memory for follow-ups)
# ─────────────────────────────────────────────────────────────────────────────

_LAST_INTERACTION: dict[str, Any] = {
    "user_text": "",
    "intent": "",
    "parameters": {},
    "response": "",
    "timestamp": "",
}


def _update_last_interaction(user_text: str, intent: str = "",
                              parameters: dict | None = None,
                              response: str = "") -> None:
    """Track the last interaction for follow-up detection."""
    _LAST_INTERACTION["user_text"] = user_text
    _LAST_INTERACTION["intent"] = intent
    _LAST_INTERACTION["parameters"] = parameters or {}
    _LAST_INTERACTION["response"] = response
    _LAST_INTERACTION["timestamp"] = datetime.now(timezone.utc).isoformat()


def _is_followup(text: str) -> bool:
    """Detect if the user's input is a follow-up to the previous interaction."""
    lower = text.strip().lower()
    followup_phrases = {
        "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "go ahead",
        "do it", "proceed", "confirm", "go", "please", "yes please",
        "you can", "you can do that", "that's fine", "that works",
        "alright", "right", "correct", "exactly", "do that",
        "no", "nah", "nope", "don't", "cancel", "stop", "never mind",
        "what", "what do you mean", "huh", "explain",
    }
    return lower in followup_phrases or len(lower.split()) <= 3


# ─────────────────────────────────────────────────────────────────────────────
# Memory + emotion + conversation context injection
# ─────────────────────────────────────────────────────────────────────────────

def _build_context_prompt(prompt: str) -> str:
    """Inject memory, emotion state, and conversation history into a prompt."""
    vibe_block = ""
    try:
        vibe_path = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "memory", "emotion_state.json")
        )
        if os.path.exists(vibe_path):
            with open(vibe_path, "r", encoding="utf-8") as f:
                vibe = json.load(f)
            last_update = vibe.get("last_update_utc")
            last_dt = None
            if isinstance(last_update, str) and last_update:
                try:
                    last_dt = datetime.fromisoformat(last_update)
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=timezone.utc)
                except Exception:
                    pass
            if last_dt and datetime.now(timezone.utc) - last_dt <= timedelta(minutes=90):
                dominant = vibe.get("window_dominant_state") or vibe.get("last_state")
                dist = vibe.get("window_distribution_percent")
                samples = vibe.get("samples")
                window_minutes = vibe.get("window_minutes")
                vibe_block = (
                    f"Vibe (webcam): window={window_minutes}m, samples={samples}, "
                    f"dominant={dominant}, distribution%={dist}, "
                    f"last={vibe.get('last_state')} @ {vibe.get('last_timestamp_utc')}"
                )
    except Exception:
        pass

    memory_block = ""
    if search_memory is not None:
        try:
            matches = search_memory(prompt, top_k=5)
            lines: list[str] = []
            for m in matches:
                text: str | None = getattr(m, "text", None)
                if text is None and isinstance(m, Mapping):
                    text = str(m.get("text") or "")
                if not text:
                    continue
                metadata_obj: Any = getattr(m, "metadata", None)
                if metadata_obj is None and isinstance(m, Mapping):
                    metadata_obj = m.get("metadata")
                date: Any = None
                if isinstance(metadata_obj, Mapping):
                    date = metadata_obj.get("date") or metadata_obj.get("timestamp")
                lines.append(f"- {text} ({date})" if date else f"- {text}")
            if lines:
                memory_block = "Memory:\n" + "\n".join(lines)
        except Exception:
            pass

    # Screen activity context (LLaVA screen reader — no image stored)
    screen_block = get_screen_context_block()

    # Cognitive load state (keyboard timing analysis)
    cognitive_block = ""
    try:
        from vision.cognitive_monitor import get_cognitive_state
        cog = get_cognitive_state()
        if cog and cog.get("label") != "low":
            cognitive_block = (
                f"Cognitive state: load={cog.get('cognitive_load', '?')}/100 "
                f"({cog.get('label', '?')}). {cog.get('recommendation', '')}"
            )
    except Exception:
        pass

    # Conversation history (recent turns for context continuity)
    conversation_block = ""
    try:
        from memory.conversation_log import get_full_context_string
        ctx = get_full_context_string(turns=5)
        if ctx:
            conversation_block = ctx
    except Exception:
        pass

    parts = [p for p in [conversation_block, vibe_block, memory_block,
                          screen_block, cognitive_block] if p]
    if not parts:
        return prompt
    return "Context (use only if relevant):\n\n" + "\n\n".join(parts) + f"\n\nUser: {prompt}"


# ─────────────────────────────────────────────────────────────────────────────
# Content generation for steps that need LLM-produced text
# ─────────────────────────────────────────────────────────────────────────────

def _generate_step_content(step: dict) -> dict:
    """If a step has parameters.generate=true, call the LLM and fill in the text/content."""
    params = step.get("parameters") or {}
    if not params.get("generate"):
        return step

    if cancel_requested():
        return step

    step_intent = step.get("intent", "")
    prompt_text = str(params.get("prompt") or params.get("text") or "Write the requested content.")
    with llm_busy_context():
        generated = generate_text(_build_context_prompt(prompt_text)).strip()

    if cancel_requested():
        return step

    # Clone the step so we don't mutate the original
    new_params = dict(params)
    new_params.pop("generate", None)
    new_params.pop("prompt", None)

    if step_intent == "type_text":
        new_params["text"] = generated
    elif step_intent in {"save_file", "create_file"}:
        new_params["content"] = generated
    else:
        new_params["text"] = generated

    return {"intent": step_intent, "parameters": new_params}


# ─────────────────────────────────────────────────────────────────────────────
# Question engine — decide whether to ask before acting
# ─────────────────────────────────────────────────────────────────────────────

def _question_gate(
    user_text: str,
    payload: dict,
    ask_fn: Callable[[str], str | None],
) -> tuple[dict | None, str | None]:
    """Check if a question needs to be asked before executing.

    ask_fn: callable that takes a question string and returns the user's answer,
            or None if running in non-interactive mode.

    Returns:
        (updated_payload, question_asked)
        If ask_fn returns None (non-interactive), returns the original payload
        unchanged and the question text so the caller can yield it.
    """
    intent = str(payload.get("intent", "")).lower()
    parameters = payload.get("parameters") or {}
    question_asked = None

    # 1. Clarification — required fields are missing
    if needs_clarification(intent, parameters):
        missing = _infer_missing_fields(intent, parameters)
        q = generate_clarification_question(user_text, intent, parameters, missing)
        question_asked = q
        answer = ask_fn(q)
        if answer:
            # Re-classify with the answer appended for richer context
            try:
                new_payload = classify_intent(f"{user_text}. {answer}")
                if isinstance(new_payload, dict) and new_payload.get("intent") not in {"unknown", ""}:
                    return new_payload, question_asked
            except Exception:
                pass
        return payload, question_asked

    # 2. Confirmation — action is risky / irreversible
    if requires_confirmation(intent):
        q = generate_confirmation_question(user_text, intent, parameters)
        question_asked = q
        answer = ask_fn(q)
        if answer and answer.strip().lower() in {"no", "n", "cancel", "nope", "nah", "don't", "dont"}:
            return None, question_asked  # caller should abort

    # 3. Enrichment — optional question to improve quality
    elif is_enrichment_eligible(intent):
        q = generate_enrichment_question(user_text, intent, parameters)
        if q:
            question_asked = q
            answer = ask_fn(q)
            if answer and answer.strip():
                # Merge the answer into parameters by re-classifying
                try:
                    new_payload = classify_intent(f"{user_text}. {answer}")
                    if isinstance(new_payload, dict) and new_payload.get("intent") not in {"unknown", ""}:
                        return new_payload, question_asked
                except Exception:
                    pass

    return payload, question_asked


# ─────────────────────────────────────────────────────────────────────────────
# Legacy command parsers (kept for speed — bypass LLM for obvious inputs)
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_user_text(user_text: str) -> str:
    """Correct common typos/STT mistakes for deterministic parsers."""
    text = user_text or ""
    replacements = [
        (r"\bnodepad\b", "notepad"),
        (r"\bfle\s+explorere\b", "file explorer"),
        (r"\bfile\s+explorere\b", "file explorer"),
        (r"\bcamo\s+studi[o0]\b", "camo studio"),
        (r"\bactivite\b", "activate"),
        (r"\bkinitic\b", "kinetic"),
        (r"\bbluetooh\b", "bluetooth"),
    ]
    for pattern, repl in replacements:
        text = re.sub(pattern, repl, text, flags=re.IGNORECASE)
    return text


def _parse_open_target(target: str) -> dict | None:
    cleaned = (target or "").strip().rstrip(" .,!?:;")
    cleaned = re.sub(r"^(?:the|a|an)\s+", "", cleaned, flags=re.IGNORECASE)
    if not cleaned:
        return None
    lowered = cleaned.lower()

    m = re.search(
        r"(?P<name>[\w\s._-]+?)\s+folder\s+(?:inside|in)\s+(?:the\s+)?(?P<drive>[a-z])\s*drive",
        lowered,
    )
    if m:
        folder = m.group("name").strip().replace("/", "\\")
        folder = re.sub(r"\s+", " ", folder)
        path = f"{m.group('drive').upper()}:\\{folder}"
        return {"intent": "open_folder", "parameters": {"path": path}}

    m = re.search(r"\b([a-z])\s*drive\b", lowered)
    if m and "folder" in lowered:
        return {"intent": "open_folder", "parameters": {"path": f"{m.group(1).upper()}:\\"}}

    if re.match(r"^[a-zA-Z]:[\\/]", cleaned):
        return {"intent": "open_folder", "parameters": {"path": cleaned}}

    if "folder" in lowered and not lowered.endswith(".txt"):
        folder_name = re.sub(r"\bfolder\b", "", cleaned, flags=re.IGNORECASE).strip()
        if folder_name:
            return {"intent": "open_folder", "parameters": {"path": folder_name}}

    return {"intent": "open_app", "parameters": {"app_name": cleaned}}


def _parse_multistep_command(user_text: str) -> dict | None:
    text = (user_text or "").strip()
    if not text:
        return None

    if not re.search(r"\b(and|then)\b|,", text, flags=re.IGNORECASE):
        return None

    chunks = [c.strip() for c in re.split(r"\s*(?:,|;|\band then\b|\bthen\b)\s*", text, flags=re.IGNORECASE) if c.strip()]
    if len(chunks) < 2 and not re.search(r"\band\b", text, flags=re.IGNORECASE):
        return None

    steps: list[dict] = []
    for chunk in chunks:
        open_match = re.match(r"^open\s+(.+)$", chunk, re.IGNORECASE)
        if open_match:
            targets = [t.strip() for t in re.split(r"\s+\band\b\s+", open_match.group(1), flags=re.IGNORECASE) if t.strip()]
            for target in targets:
                write_in_open = re.match(r"^(?:write|type)\s+(.+)$", target, re.IGNORECASE)
                if write_in_open:
                    content = write_in_open.group(1).strip()
                    if any(k in content.lower() for k in ("story", "poem", "email", "essay", "article")):
                        steps.append({"intent": "type_text", "parameters": {"generate": True, "prompt": content}})
                    else:
                        steps.append({"intent": "type_text", "parameters": {"text": content}})
                    continue
                parsed = _parse_open_target(target)
                if parsed:
                    steps.append(parsed)
            continue

        write_match = re.match(r"^(?:write|type)\s+(.+)$", chunk, re.IGNORECASE)
        if write_match:
            content = write_match.group(1).strip()
            if any(k in content.lower() for k in ("story", "poem", "email", "essay", "article")):
                steps.append({"intent": "type_text", "parameters": {"generate": True, "prompt": content}})
            else:
                steps.append({"intent": "type_text", "parameters": {"text": content}})
            continue

        parsed = _parse_system_command(chunk) or _parse_browser_command(chunk) or _parse_scheduler_command(chunk)
        if parsed:
            steps.append(parsed)
            continue

        # If a bare app name appears after an open clause, treat it as another open step.
        if len(chunk.split()) <= 4 and not re.search(r"\b(turn|set|search|click|extract|save|delete|copy|move)\b", chunk, re.IGNORECASE):
            for part in re.split(r"\s+\band\b\s+", chunk, flags=re.IGNORECASE):
                part = part.strip()
                if not part:
                    continue
                parsed = _parse_open_target(part)
                if parsed:
                    steps.append(parsed)

    if len(steps) >= 2:
        return {"intent": "multi_step", "steps": steps}
    return None


def _parse_browser_command(user_text: str) -> dict | None:
    text = user_text.strip()
    if not text:
        return None
    match = re.match(r"^(open|go to|visit)\s+(.+)$", text, re.IGNORECASE)
    if match:
        target = match.group(2).strip().rstrip(" .,!?:;")
        t = target.lower()
        site_aliases = {
            "google maps": "https://maps.google.com",
            "maps":        "https://maps.google.com",
            "youtube":     "https://www.youtube.com",
            "gmail":       "https://mail.google.com",
        }
        if t in site_aliases:
            return {"intent": "open_url", "parameters": {"url": site_aliases[t], "use_chrome": True}}
        if t.endswith(" app") and len(t) > 4:
            return {"intent": "open_app", "parameters": {"app_name": target[:-4].strip()}}
        known = set(system_control.APP_ALIASES.keys()) | set(system_control.APP_ALIASES.values())
        if t in known:
            return {"intent": "open_app", "parameters": {"app_name": target}}
        if "." in target or t.startswith(("http://", "https://", "localhost")):
            return {"intent": "open_url", "parameters": {"url": target}}
        if " " in target:
            return {"intent": "open_app", "parameters": {"app_name": target}}
        return {"intent": "open_url", "parameters": {"url": f"{target}.com", "use_chrome": True}}

    match = re.match(r"^search\s+(.+?)(?:\s+on\s+(google|duckduckgo|ddg))?$", text, re.IGNORECASE)
    if match:
        query = match.group(1).strip()
        engine = (match.group(2) or "google").lower().replace("goolge", "google")
        return {"intent": "search_web", "parameters": {"query": query, "engine": engine, "use_chrome": True}}

    match = re.match(r"^(google|duckduckgo|ddg)\s+(.+)$", text, re.IGNORECASE)
    if match:
        return {"intent": "search_web",
                "parameters": {"query": match.group(2).strip(), "engine": match.group(1).lower(), "use_chrome": True}}

    match = re.match(r"^click\s+(.+)$", text, re.IGNORECASE)
    if match:
        return {"intent": "click_element", "parameters": {"selector": match.group(1).strip()}}

    match = re.match(r"^extract\s+text\s+(.+)$", text, re.IGNORECASE)
    if match:
        return {"intent": "extract_text", "parameters": {"url": match.group(1).strip()}}

    return None


def _parse_app_control_command(user_text: str) -> dict | None:
    """Fast-path parsing for common app close commands.

    This avoids LLM misclassification for things like "close chrome".
    Only triggers when the target looks like a known app alias.
    """
    text = (user_text or "").strip()
    if not text:
        return None

    match = re.match(r"^(close|quit|exit)\s+(?:the\s+)?(.+)$", text, re.IGNORECASE)
    if not match:
        return None

    target = match.group(2).strip().rstrip(" .,!?:;")
    t = target.lower()

    known = set(system_control.APP_ALIASES.keys()) | set(system_control.APP_ALIASES.values())
    if t not in known:
        return None

    return {"intent": "close_window", "parameters": {"app_name": target}}


def _strip_confirm_prefix(user_text: str) -> tuple[str, bool]:
    """Return (cleaned_text, force_confirmed) for inputs like 'confirm shutdown'."""
    text = (user_text or "").strip()
    match = re.match(r"^(?:please\s+)?confirm\s*[:\-]?\s+(.+)$", text, re.IGNORECASE)
    if match:
        rest = match.group(1).strip()
        return rest, True
    match = re.match(r"^yes\s+confirm\s*[:\-]?\s+(.+)$", text, re.IGNORECASE)
    if match:
        rest = match.group(1).strip()
        return rest, True
    return user_text, False


def _needs_dangerous_confirmation(payload: dict, force_confirmed: bool) -> bool:
    try:
        assessment = assess_risk(payload)
    except Exception:
        return False
    if assessment.level != DANGEROUS:
        return False
    if force_confirmed:
        return False
    return not bool(payload.get("confirmed", False))


def _confirmation_instructions(payload: dict) -> str:
    """Human instruction for confirming a dangerous action in non-interactive mode."""
    intent = str(payload.get("intent") or "").strip() or "this action"
    assessment = assess_risk(payload)
    return (
        "🔴 High-risk action needs confirmation.\n"
        f"Action: {intent.replace('_', ' ')}\n"
        f"Risk: {assessment.reason}\n\n"
        "To proceed, type a new message starting with: confirm ...\n"
        f"Example: confirm {intent.replace('_', ' ')}"
    )


def _parse_system_command(user_text: str) -> dict | None:
    lowered = (user_text or "").strip().lower()
    # Explicit power commands (keep strict to avoid misreading "turn off Wi-Fi")
    if lowered in {
        "shutdown",
        "shut down",
        "power off",
        "turn off pc",
        "turn off my pc",
        "turn off the pc",
        "turn off computer",
        "turn off my computer",
        "turn off the computer",
    }:
        return {"intent": "shutdown", "parameters": {}}

    if lowered in {
        "restart",
        "reboot",
        "restart pc",
        "restart my pc",
        "restart the pc",
        "restart computer",
        "restart my computer",
        "restart the computer",
        "reboot pc",
        "reboot computer",
    }:
        return {"intent": "restart", "parameters": {}}

    m = re.match(r"^(set|change)\s+volume\s*(?:to|at)?\s*(\d{1,3})\b", lowered)
    if m:
        return {"intent": "set_volume", "parameters": {"level": max(0, min(100, int(m.group(2))))}}
    m = re.match(r"^(mute|unmute)\b", lowered)
    if m:
        return {"intent": "set_volume", "parameters": {"level": 0 if m.group(1) == "mute" else 30}}

    m = re.search(r"\bturn\s+(on|off)\s+(?:the\s+)?wi[\s-]?fi\b", lowered)
    if m:
        return {"intent": "toggle_wifi", "parameters": {"state": m.group(1)}}
    if re.search(r"\b(?:enable|disable)\s+(?:the\s+)?wi[\s-]?fi\b", lowered):
        state = "on" if "enable" in lowered else "off"
        return {"intent": "toggle_wifi", "parameters": {"state": state}}

    m = re.search(r"\bturn\s+(on|off)\s+(?:the\s+)?bluetooth\b", lowered)
    if m:
        return {"intent": "toggle_bluetooth", "parameters": {"state": m.group(1)}}
    if re.search(r"\b(?:enable|disable)\s+(?:the\s+)?bluetooth\b", lowered):
        state = "on" if "enable" in lowered else "off"
        return {"intent": "toggle_bluetooth", "parameters": {"state": state}}

    m = re.search(r"\b(?:set|change|adjust)\s+brightness\s*(?:to|at)?\s*(\d{1,3})\b", lowered)
    if m:
        return {"intent": "set_brightness", "parameters": {"level": max(0, min(100, int(m.group(1))))}}

    if ("kinetic" in lowered or "gesture control" in lowered) and any(k in lowered for k in ("activate", "enable", "start", "on")):
        return {"intent": "activate_kinetic_mode", "parameters": {}}
    if ("kinetic" in lowered or "gesture control" in lowered) and any(k in lowered for k in ("deactivate", "disable", "stop", "off")):
        return {"intent": "deactivate_kinetic_mode", "parameters": {}}

    open_folder_match = re.search(
        r"\bopen\s+(.+?)\s+folder\s+(?:inside|in)\s+(?:the\s+)?([a-z])\s*drive\b",
        lowered,
    )
    if open_folder_match:
        folder = open_folder_match.group(1).strip()
        drive = open_folder_match.group(2).upper()
        return {"intent": "open_folder", "parameters": {"path": f"{drive}:\\{folder}"}}

    return None


def _parse_scheduler_command(user_text: str) -> dict | None:
    text = (user_text or "").strip()
    lowered = text.lower()

    if (re.search(r"\b(show|display)\b.*\b(schedule|plan|timeline|agenda)\b", lowered)
            or lowered in {"schedule", "my schedule", "today schedule", "today's schedule",
                           "show schedule", "show plan", "show timeline"}):
        return {"intent": "show_schedule", "parameters": {}}

    if re.search(r"\b(what'?s\s+next|what\s+should\s+i\s+do\s+next|next\s+task|what\s+now)\b", lowered):
        return {"intent": "whats_next", "parameters": {}}

    if (lowered.startswith(("edit", "update", "change"))
            and re.search(r"\b(schedule|plan|day\s+plan|timeline|agenda)\b", lowered)):
        return {"intent": "edit_schedule", "parameters": {"command": f"edit schedule: {text}"}}

    if lowered.startswith(("edit schedule", "update schedule", "change schedule")):
        return {"intent": "edit_schedule", "parameters": {"command": text}}

    schedule_starters = (
        "create a new plan", "create new plan", "create a new day plan", "create new day plan",
        "create day plan", "new plan", "make a new plan", "make new plan",
        "create a new schedule", "create new schedule", "new schedule",
        "schedule my day", "schedule", "plan my day", "make a schedule",
        "make my schedule", "plan:", "plan",
    )
    if lowered.startswith(schedule_starters):
        use_reference = bool(
            has_plan()
            and re.search(r"\b(new)\b", lowered)
            and re.search(r"\b(plan|schedule)\b", lowered)
        )
        stripped = re.sub(
            r"^(create\s+(a\s+)?new\s+((day\s+)?plan|schedule)|create\s+day\s+plan|"
            r"make\s+(a\s+)?new\s+((day\s+)?plan|schedule)|new\s+((day\s+)?plan|schedule)|"
            r"schedule(\s+my\s+day)?|plan\s+my\s+day|make\s+a\s+schedule|"
            r"make\s+my\s+schedule|plan)\s*[:\-]?\s*",
            "", text, flags=re.IGNORECASE,
        ).strip()
        if use_reference and stripped.lower().startswith(
                ("move ", "reschedule ", "add ", "remove ", "make ", "set ", "put ")):
            return {"intent": "edit_schedule", "parameters": {"command": f"edit schedule: {stripped}"}}
        params: dict = {"text": stripped or text}
        if use_reference:
            params["use_reference"] = True
        return {"intent": "create_schedule", "parameters": params}

    if has_plan() and lowered.startswith(
            ("add ", "remove ", "move ", "reschedule ", "make ", "set ", "put ")):
        return {"intent": "edit_schedule", "parameters": {"command": f"edit schedule: {text}"}}

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Core processing — streaming generator
# ─────────────────────────────────────────────────────────────────────────────

def process_text_stream(
    user_text: str,
    ask_fn: Callable[[str], str | None] | None = None,
) -> Generator[str, None, None]:
    """Process user input and yield narration lines one at a time (streaming).

    This is the main entry point for the agent. It yields strings progressively
    so the UI can show Copilot-style step narration in real time.

    Args:
        user_text:  Raw user input (voice-transcribed or typed).
        ask_fn:     Optional callable that presents a question to the user and
                    returns their answer string, or None if non-interactive.
                    If None, questions are yielded as output lines and execution
                    continues with the original parameters.

    Yields:
        Narration lines (strings) — one per event.
    """
    clear_cancel()
    if not user_text or not user_text.strip():
        yield "No input received."
        return
    normalized_text = _normalize_user_text(user_text)

    # ── Follow-up detection ──────────────────────────────────────────────────
    # Handle short affirmations/negations that reference the previous interaction
    if _is_followup(user_text) and _LAST_INTERACTION.get("user_text"):
        lower = user_text.strip().lower()
        # Negative follow-ups → cancel
        if lower in {"no", "nah", "nope", "don't", "cancel", "stop", "never mind"}:
            yield "Okay, never mind."
            return
        # Positive follow-ups → re-execute with context
        if lower in {"yes", "yeah", "yep", "yup", "sure", "ok", "okay",
                      "go ahead", "do it", "proceed", "confirm", "go",
                      "please", "yes please", "you can", "you can do that",
                      "that's fine", "that works", "alright", "do that"}:
            last_intent = _LAST_INTERACTION.get("intent", "")
            last_params = _LAST_INTERACTION.get("parameters", {})
            if last_intent and last_intent not in {"conversational", "answer_question", "unknown", ""}:
                # Re-execute the last action with confirmation
                user_text = _LAST_INTERACTION.get("user_text", user_text)
                normalized_text = _normalize_user_text(user_text)
                yield f"Got it — proceeding with: {user_text}"

    # ── Optional: inline confirmation prefix (non-interactive UI/voice) ────
    # Supports: "confirm shutdown", "confirm delete file ..." etc.
    user_text, force_confirmed = _strip_confirm_prefix(user_text)

    # ── Track 1: Conversational ─────────────────────────────────────────────
    # Fast heuristic check first (no LLM call needed for obvious greetings)
    try:
        if is_conversational(normalized_text):
            if cancel_requested():
                yield "Canceled."
                return
            augmented = _build_context_prompt(user_text)
            with llm_busy_context():
                reply = generate_text(augmented).strip()
            if cancel_requested():
                yield "Canceled."
                return
            yield reply or "I'm not sure how to respond to that."
            # Subconscious layer: detect latent concerns
            try:
                from core.subconscious_layer import analyse
                nudge = analyse(user_text)
                if nudge:
                    yield f"\n💡 {nudge}"
            except Exception:
                pass
            return
    except Exception:
        pass  # fall through to action classification

    # ── Build payload ───────────────────────────────────────────────────────
    payload: dict[str, Any] = {}

    # Fast parsers first (no Ollama call, instant)
    for parser in (_parse_system_command, _parse_scheduler_command, _parse_browser_command):
        result = parser(user_text)
        if isinstance(result, dict):
            payload = result
            break

    # LLM classification if fast parsers didn't match
    if not payload:
        try:
            classified = classify_intent(normalized_text)
        except Exception as exc:
            yield f"I had trouble understanding that: {exc}"
            return

        if not isinstance(classified, dict):
            yield "I didn't understand that. Try rephrasing."
            return

        # Conversational intent from the LLM classifier
        if classified.get("intent") in {"conversational", "answer_question"}:
            prompt = classified.get("parameters", {}).get("prompt") or user_text
            augmented = _build_context_prompt(prompt)
            if cancel_requested():
                yield "Canceled."
                return
            with llm_busy_context():
                _reply = generate_text(augmented).strip()
            if cancel_requested():
                yield "Canceled."
                return
            yield _reply or "I'm not sure how to respond."
            # Subconscious layer: detect latent concerns
            try:
                from core.subconscious_layer import analyse
                nudge = analyse(user_text)
                if nudge:
                    yield f"\n💡 {nudge}"
            except Exception:
                pass
            return

        payload = classified

    # ── Safety gate (BLOCKED) ───────────────────────────────────────────────
    if is_blocked(payload):
        try:
            assessment = assess_risk(payload)
            log_action(
                intent=str(payload.get("intent", "unknown")),
                parameters=payload.get("parameters") or {},
                risk_level=assessment.level,
                outcome=OUTCOME_BLOCKED,
                result_summary=assessment.reason,
                user_input=user_text,
            )
        except Exception:
            pass
        yield blocked_response(payload)
        return

    intent = str(payload.get("intent", "")).strip().lower()

    if intent in {"", "unknown"}:
        yield (
            "I didn't quite understand that. Try something like:\n"
            "  • 'open Chrome'\n"
            "  • 'what's the weather in Dhaka?'\n"
            "  • 'search ArXiv for transformer models'\n"
            "  • 'show my schedule'\n"
            "  • 'turn off Wi-Fi'"
        )
        return

    # ── LLM answer intents (generate text, don't dispatch tool) ─────────────
    if intent in {"answer_question", "type_generated_text"}:
        prompt = (payload.get("parameters") or {}).get("prompt") or user_text
        augmented = _build_context_prompt(prompt)
        if cancel_requested():
            yield "Canceled."
            return
        with llm_busy_context():
            generated = generate_text(augmented).strip()
        if cancel_requested():
            yield "Canceled."
            return
        if not generated:
            yield "No response generated."
            return
        if intent == "type_generated_text":
            yield "▶  Generating text..."
            try:
                res = system_control.type_text(generated)
                yield f"✓  Text typed: {generated[:80]}{'...' if len(generated) > 80 else ''}"
            except Exception as exc:
                yield f"✗  Failed to type text: {exc}"
        else:
            yield generated
        return

    # ── Multi-step command ───────────────────────────────────────────────────
    if intent == "multi_step":
        steps: list[dict] = payload.get("steps") or []
        if not steps:
            yield "No steps found in multi-step command."
            return

        total = len(steps)
        yield f"Got it — I'll do {total} things for you.\n"

        results_all: list[str] = []

        for i, raw_step in enumerate(steps, 1):
            if cancel_requested():
                yield "Canceled."
                return
            step_intent = str(raw_step.get("intent", "")).strip().lower()
            step_params = raw_step.get("parameters") or {}

            # Generate content if this step needs LLM-produced text
            step = _generate_step_content(raw_step)
            step_params = step.get("parameters") or {}
            if cancel_requested():
                yield "Canceled."
                return

            label = _label_for_step(step_intent, step_params)
            yield _step_header(i, total, label)

            if cancel_requested():
                yield "Canceled."
                return

            # Safety check per step
            if is_blocked(step):
                try:
                    assessment = assess_risk(step)
                    log_action(
                        intent=step_intent,
                        parameters=step_params,
                        risk_level=assessment.level,
                        outcome=OUTCOME_BLOCKED,
                        result_summary=assessment.reason,
                        user_input=user_text,
                        extra={"step": i, "total_steps": total},
                    )
                except Exception:
                    pass
                yield _step_error(i, total, label, "This action is blocked for safety.")
                results_all.append(f"Step {i}: blocked")
                continue

            # Dangerous actions need explicit confirmation.
            if _needs_dangerous_confirmation(step, force_confirmed=force_confirmed):
                msg = _confirmation_instructions(step)
                try:
                    assessment = assess_risk(step)
                    log_action(
                        intent=step_intent,
                        parameters=step_params,
                        risk_level=assessment.level,
                        outcome=OUTCOME_CANCELLED,
                        result_summary="Confirmation required (non-interactive mode).",
                        user_input=user_text,
                        extra={"step": i, "total_steps": total},
                    )
                except Exception:
                    pass
                yield _step_error(i, total, label, "Confirmation required.")
                yield msg
                return

            if force_confirmed and assess_risk(step).level == DANGEROUS:
                step = {**step, "confirmed": True}

            try:
                outcome = dispatch_intent(step)
                result_str = str(outcome).strip() if outcome is not None else "Done."
                try:
                    assessment = assess_risk(step)
                    log_action(
                        intent=step_intent,
                        parameters=step_params,
                        risk_level=assessment.level,
                        outcome=OUTCOME_SUCCESS,
                        result_summary=result_str,
                        user_input=user_text,
                        extra={"step": i, "total_steps": total},
                    )
                except Exception:
                    pass
                yield _step_success(i, total, label, result_str)
                results_all.append(f"Step {i}: {result_str[:60]}")
            except Exception as exc:
                try:
                    assessment = assess_risk(step)
                    log_action(
                        intent=step_intent,
                        parameters=step_params,
                        risk_level=assessment.level,
                        outcome=OUTCOME_ERROR,
                        result_summary=str(exc),
                        user_input=user_text,
                        extra={"step": i, "total_steps": total},
                    )
                except Exception:
                    pass
                yield _step_error(i, total, label, str(exc))
                results_all.append(f"Step {i}: error — {exc}")

        divider = _divider(total)
        if divider:
            yield divider
        return

    # ── Single-step command ──────────────────────────────────────────────────
    parameters = payload.get("parameters") or {}
    if not isinstance(parameters, dict):
        parameters = {}

    # Question gate (clarification → confirmation → enrichment)
    _noop_ask = lambda q: None  # noqa: E731
    effective_ask = ask_fn if callable(ask_fn) else _noop_ask

    updated_payload, question_text = _question_gate(user_text, payload, effective_ask)

    # If ask_fn is None (non-interactive), yield the question as output
    if ask_fn is None and question_text:
        yield f"❓  {question_text}"

    if updated_payload is None:
        # User said no to confirmation
        yield "Action cancelled."
        return

    payload = updated_payload
    intent = str(payload.get("intent", "")).strip().lower()
    parameters = payload.get("parameters") or {}

    # Re-check safety after question gate may have updated payload
    if is_blocked(payload):
        try:
            assessment = assess_risk(payload)
            log_action(
                intent=intent,
                parameters=parameters,
                risk_level=assessment.level,
                outcome=OUTCOME_BLOCKED,
                result_summary=assessment.reason,
                user_input=user_text,
            )
        except Exception:
            pass
        yield blocked_response(payload)
        return

    # Dangerous actions must be explicitly confirmed.
    if _needs_dangerous_confirmation(payload, force_confirmed=force_confirmed):
        msg = _confirmation_instructions(payload)
        try:
            assessment = assess_risk(payload)
            log_action(
                intent=intent,
                parameters=parameters,
                risk_level=assessment.level,
                outcome=OUTCOME_CANCELLED,
                result_summary="Confirmation required (non-interactive mode).",
                user_input=user_text,
            )
        except Exception:
            pass
        yield msg
        return

    if callable(ask_fn) and assess_risk(payload).level == DANGEROUS and not force_confirmed:
        ok = confirm_action(payload, ask_fn=ask_fn)
        if not ok:
            try:
                assessment = assess_risk(payload)
                log_action(
                    intent=intent,
                    parameters=parameters,
                    risk_level=assessment.level,
                    outcome=OUTCOME_CANCELLED,
                    result_summary="User cancelled confirmation.",
                    user_input=user_text,
                )
            except Exception:
                pass
            yield "Action cancelled."
            return

    if (force_confirmed or bool(payload.get("confirmed"))) and assess_risk(payload).level == DANGEROUS:
        payload = {**payload, "confirmed": True}

    # Content generation for single-step if needed
    step = _generate_step_content({"intent": intent, "parameters": parameters})
    intent = str(step.get("intent", intent))
    parameters = step.get("parameters") or parameters

    label = _label_for_step(intent, parameters)
    yield _step_header(1, 1, label)

    if cancel_requested():
        yield "Canceled."
        return

    try:
        result = dispatch_intent({"intent": intent, "parameters": parameters,
                                  **{k: v for k, v in payload.items()
                                     if k not in {"intent", "parameters"}}})
        if cancel_requested():
            yield "Canceled."
            return
        result_str = result if isinstance(result, str) else json.dumps(result, indent=2, ensure_ascii=False)
        try:
            assessment = assess_risk(payload)
            log_action(
                intent=intent,
                parameters=parameters,
                risk_level=assessment.level,
                outcome=OUTCOME_SUCCESS,
                result_summary=str(result_str),
                user_input=user_text,
            )
        except Exception:
            pass
        yield _step_success(1, 1, label, result_str)
    except Exception as exc:
        try:
            assessment = assess_risk(payload)
            log_action(
                intent=intent,
                parameters=parameters,
                risk_level=assessment.level,
                outcome=OUTCOME_ERROR,
                result_summary=str(exc),
                user_input=user_text,
            )
        except Exception:
            pass
        yield _step_error(1, 1, label, str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Non-streaming convenience wrapper (collects all streamed lines into one string)
# ─────────────────────────────────────────────────────────────────────────────

def process_text(
    user_text: str,
    ask_fn: Callable[[str], str | None] | None = None,
) -> str:
    """Process user input and return the full response as a single string.

    This is the backward-compatible entry point. It collects all streamed
    narration lines and joins them with newlines.

    For real-time Copilot-style output, use process_text_stream() instead.
    """
    lines = list(process_text_stream(user_text, ask_fn=ask_fn))
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Interactive ask_fn for CLI use
# ─────────────────────────────────────────────────────────────────────────────

def _cli_ask(question: str) -> str | None:
    """Print a question and read the user's answer from stdin."""
    print(f"\n❓  {question}")
    try:
        answer = input("Your answer: ").strip()
        return answer if answer else None
    except (EOFError, KeyboardInterrupt):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Entry points
# ─────────────────────────────────────────────────────────────────────────────

def run_agent(user_text: str, interactive: bool = True) -> None:
    """Run the agent for a single user input with live streaming output.

    Args:
        user_text:   Raw user command or question.
        interactive: If True, the agent asks clarifying questions via stdin.
                     Set False for non-interactive / voice-only mode.
    """
    ask = _cli_ask if interactive else None
    print()
    for line in process_text_stream(user_text, ask_fn=ask):
        print(line)
    print()


if __name__ == "__main__":
    print("ARIA is ready. Type your command or question.")
    print("Type 'exit' or 'quit' to stop.\n")
    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break
        if user_input.lower() in {"exit", "quit", "bye"}:
            print("Goodbye.")
            break
        if not user_input:
            continue
        run_agent(user_input, interactive=True)
