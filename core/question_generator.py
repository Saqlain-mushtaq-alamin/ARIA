"""Smart clarification and follow-up question engine.

This module makes ARIA feel like a thoughtful human assistant rather than a
command-line tool. It handles three distinct question types:

1. CLARIFICATION — The command is ambiguous and needs more information before
   any action can be taken. Example:
     User: "delete the file"  →  "Which file should I delete? Can you give me
                                   the filename or folder path?"

2. CONFIRMATION — The action is clear but risky/irreversible. A yes/no question
   is generated with a clear explanation of what will happen. Example:
     User: "delete all downloads"  →  "Just to confirm — I'll permanently delete
                                       everything in your Downloads folder. Are
                                       you sure you want to do that?"

3. ENRICHMENT — The command is valid but the agent spotted something it can do
   better if it asks one smart question. Example:
     User: "search for AI papers"  →  "Do you want results from ArXiv, Semantic
                                        Scholar, or PubMed?"

Memory is used to personalise every question. If the user has done something
similar before, the question references their past behaviour instead of asking
from scratch.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from modules.content_generator import generate_text

try:
    from memory.vector_store import search_memory
except Exception:  # memory module not yet initialised
    search_memory = None  # type: ignore[assignment]


# ─────────────────────────────────────────────────────────────────────────────
# Intent-aware clarification templates
# These are used BEFORE calling the LLM so the response is instant when the
# rule matches exactly. The LLM is called for anything that doesn't match.
# ─────────────────────────────────────────────────────────────────────────────

# Maps (intent, missing_field) → question template
# {memory} is replaced with a memory snippet if available.
_CLARIFICATION_TEMPLATES: Dict[tuple, str] = {
    ("open_file", "path"):
        "Which file should I open? You can give me the filename or the full path.{memory}",
    ("save_file", "path"):
        "Where should I save the file? You can say something like 'Desktop' or give a full path.{memory}",
    ("save_file", "content"):
        "What content should I write into the file?",
    ("create_file", "path"):
        "What should I name the new file, and where should I create it?{memory}",
    ("delete_file", "path"):
        "Which file should I delete? Please give me the filename or full path so I don't remove the wrong thing.{memory}",
    ("move_file", "source"):
        "Which file or folder should I move?{memory}",
    ("move_file", "destination"):
        "Where should I move it to?{memory}",
    ("copy_file", "source"):
        "Which file or folder should I copy?{memory}",
    ("copy_file", "destination"):
        "Where should I copy it to?{memory}",
    ("list_directory", "path"):
        "Which folder should I list? You can say 'Desktop', 'Downloads', or give a full path.{memory}",
    ("open_app", "app_name"):
        "Which app should I open?{memory}",
    ("close_window", "app_name"):
        "Which app or window should I close?{memory}",
    ("set_volume", "level"):
        "What volume level would you like? (0–100){memory}",
    ("set_brightness", "level"):
        "What brightness level would you like? (0–100){memory}",
    ("search_web", "query"):
        "What would you like me to search for?{memory}",
    ("open_url", "url"):
        "Which website should I open?{memory}",
    ("send_message", "contact"):
        "Who should I send the message to?{memory}",
    ("send_message", "message"):
        "What message should I send?",
    ("get_weather", "location"):
        "Which city or location would you like the weather for?{memory}",
    ("get_news", "topic"):
        "Any specific topic you want news on, or should I get the top general headlines?",
    ("search_papers", "query"):
        "What topic or keywords should I search for in research papers?{memory}",
    ("search_papers", "source"):
        "Which database should I search — ArXiv, Semantic Scholar, or PubMed?{memory}",
    ("get_stock", "symbol"):
        "Which stock symbol would you like the price for? (e.g. AAPL, TSLA, GOOGL)",
    ("type_text", "text"):
        "What text should I type?",
    ("create_schedule", "text"):
        "What tasks should I include in your schedule? Tell me your fixed commitments and goals for the day.",
    ("edit_schedule", "command"):
        "What change should I make to your schedule? For example: 'move gym to 7pm' or 'add coding at 3pm'.",
    ("toggle_wifi", "state"):
        "Should I turn Wi-Fi on or off?",
    ("toggle_bluetooth", "state"):
        "Should I turn Bluetooth on or off?",
    ("toggle_airplane", "state"):
        "Should I turn Airplane mode on or off?",
    ("fill_form", "fields"):
        "What information should I fill into the form?",
    ("extract_text", "url"):
        "Which webpage should I extract text from? Please give me the URL.{memory}",
}

# Confirmation questions for dangerous/irreversible intents
_CONFIRMATION_TEMPLATES: Dict[str, str] = {
    "delete_file":
        "Just to confirm — I'll permanently delete '{target}'. This can't be undone. Should I go ahead?",
    "shutdown":
        "I'm about to shut down your computer. Any unsaved work will be lost. Are you sure?",
    "restart":
        "I'm about to restart your computer. Any unsaved work will be lost. Are you sure?",
    "format":
        "⚠️ This will erase ALL data on the drive. This is irreversible. Are you absolutely certain?",
    "send_message":
        "I'm about to send this message to {target}:\n\n\"{content}\"\n\nShould I send it?",
    "fill_form":
        "I'm about to submit the form at {target} with your details. Should I proceed?",
    "move_file":
        "I'll move '{source}' to '{destination}'. If a file with the same name exists there it may be overwritten. Continue?",
}

# Enrichment questions — the intent is clear but one extra detail improves the result
_ENRICHMENT_TEMPLATES: Dict[str, str] = {
    "search_web":
        "Should I search on Google or DuckDuckGo?{memory}",
    "search_papers":
        "Which database should I use — ArXiv (CS/Physics/Math), Semantic Scholar (all fields), or PubMed (medical)?{memory}",
    "get_news":
        "Do you want headlines for a specific topic, or general top news?",
    "open_app":
        "Should I just open {app} or do you also want me to do something once it's open?{memory}",
    "create_schedule":
        "Do you want me to include your usual fixed commitments like gym and class, or start fresh?{memory}",
    "screenshot":
        "Where should I save the screenshot — Desktop, or somewhere else?{memory}",
    "extract_text":
        "Should I extract the full page text, or just a specific section?{memory}",
    "get_weather":
        "Do you want current weather or a multi-day forecast?{memory}",
}


# ─────────────────────────────────────────────────────────────────────────────
# Memory helper
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_memory_snippet(query: str, top_k: int = 3) -> str:
    """Return a short memory snippet for personalising a question, or empty string."""
    if search_memory is None or not query:
        return ""
    try:
        matches = search_memory(query, top_k=top_k)
    except Exception:
        return ""
    lines: List[str] = []
    for m in matches:
        text: Optional[str] = getattr(m, "text", None)
        if text is None and isinstance(m, dict):
            text = str(m.get("text") or "")
        if not text:
            continue
        date = None
        metadata = getattr(m, "metadata", None) or (m.get("metadata") if isinstance(m, dict) else None)
        if isinstance(metadata, dict):
            date = metadata.get("date") or metadata.get("timestamp")
        lines.append(f"{text} ({date})" if date else text)
    if not lines:
        return ""
    # Summarise into a short parenthetical
    snippet = lines[0]
    if len(snippet) > 80:
        snippet = snippet[:77] + "..."
    return f" (Last time: {snippet})"


# ─────────────────────────────────────────────────────────────────────────────
# LLM-powered fallback generator
# ─────────────────────────────────────────────────────────────────────────────

def _llm_clarification(user_query: str, intent: str, missing_fields: List[str],
                        memory_snippet: str) -> str:
    """Ask the LLM to generate a natural clarifying question when no template matches."""
    memory_block = f"\nRelevant memory: {memory_snippet}" if memory_snippet else ""
    fields_str = ", ".join(missing_fields) if missing_fields else "unclear details"
    prompt = (
        "You are a helpful AI desktop assistant. Generate ONE short, natural clarifying question "
        "to ask the user before executing their command. Be specific and friendly. "
        "Do NOT execute anything. Output ONLY the question text — no explanation, no prefix.\n\n"
        f"User said: \"{user_query}\"\n"
        f"Detected intent: {intent}\n"
        f"Missing information: {fields_str}"
        f"{memory_block}\n"
    )
    result = generate_text(prompt).strip()
    # Ensure it ends with a question mark
    if result and not result.endswith("?"):
        result += "?"
    return result if len(result) >= 8 else f"Could you give me more details about '{user_query}'?"


def _llm_enrichment(user_query: str, intent: str, memory_snippet: str) -> str:
    """Ask the LLM to generate an enrichment question to improve the action."""
    memory_block = f"\nRelevant memory: {memory_snippet}" if memory_snippet else ""
    prompt = (
        "You are a helpful AI desktop assistant. The user's command is clear enough to execute, "
        "but asking ONE smart follow-up question would make the result significantly better. "
        "Generate that question. Be concise and natural. "
        "Output ONLY the question — no prefix, no explanation.\n\n"
        f"User said: \"{user_query}\"\n"
        f"Detected intent: {intent}"
        f"{memory_block}\n"
    )
    result = generate_text(prompt).strip()
    if result and not result.endswith("?"):
        result += "?"
    return result if len(result) >= 8 else ""


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def generate_clarification_question(
    user_query: str,
    intent: str,
    parameters: Dict[str, Any],
    missing_fields: Optional[List[str]] = None,
) -> str:
    """Generate a clarifying question when required information is missing.

    Args:
        user_query:     The raw user input.
        intent:         The classified intent name (e.g. 'delete_file').
        parameters:     The parameters already extracted (so we know what's missing).
        missing_fields: Explicit list of missing field names. If None, we infer them.

    Returns:
        A single, natural question string ready to speak/display to the user.
    """
    query = (user_query or "").strip()
    intent = (intent or "unknown").strip().lower()

    # Infer missing fields if not provided
    if missing_fields is None:
        missing_fields = _infer_missing_fields(intent, parameters)

    memory_snippet = _fetch_memory_snippet(query)
    memory_tag = memory_snippet  # already formatted as " (Last time: ...)"

    # Try template match on (intent, first_missing_field)
    if missing_fields:
        key = (intent, missing_fields[0])
        if key in _CLARIFICATION_TEMPLATES:
            return _CLARIFICATION_TEMPLATES[key].format(
                memory=memory_tag,
                target=parameters.get("path") or parameters.get("app_name") or query,
            )

    # Fallback to LLM
    return _llm_clarification(query, intent, missing_fields, memory_snippet)


def generate_confirmation_question(
    user_query: str,
    intent: str,
    parameters: Dict[str, Any],
) -> str:
    """Generate a yes/no confirmation question for risky or irreversible actions.

    Args:
        user_query:  The raw user input.
        intent:      The classified intent name.
        parameters:  Extracted parameters (used to build a specific warning).

    Returns:
        A confirmation question string.
    """
    intent = (intent or "unknown").strip().lower()
    template = _CONFIRMATION_TEMPLATES.get(intent)

    if template:
        # Fill in template placeholders safely
        target = (
            parameters.get("path")
            or parameters.get("app_name")
            or parameters.get("contact")
            or parameters.get("url")
            or user_query
        )
        content = parameters.get("message") or parameters.get("text") or ""
        source = parameters.get("source") or ""
        destination = parameters.get("destination") or ""
        return template.format(
            target=target, content=content, source=source, destination=destination
        )

    # Generic fallback
    return (
        f"I'm about to {intent.replace('_', ' ')} — are you sure you want to proceed?"
    )


def generate_enrichment_question(
    user_query: str,
    intent: str,
    parameters: Dict[str, Any],
) -> str:
    """Generate an optional enrichment question to improve the action quality.

    Returns empty string if no enrichment question is appropriate.

    Args:
        user_query:  The raw user input.
        intent:      The classified intent name.
        parameters:  Already extracted parameters.

    Returns:
        An enrichment question, or "" if none is needed.
    """
    intent = (intent or "unknown").strip().lower()
    memory_snippet = _fetch_memory_snippet(user_query)
    memory_tag = memory_snippet

    template = _ENRICHMENT_TEMPLATES.get(intent)
    if template:
        app = parameters.get("app_name") or parameters.get("app") or "it"
        return template.format(memory=memory_tag, app=app)

    # For most intents no enrichment is needed — avoid being annoying
    return ""


def generate_followup_question(user_query: str, top_k: int = 5) -> str:
    """Legacy-compatible entry point. Routes to clarification or enrichment based on context.

    This keeps backward compatibility with any code that calls the old API.
    For new code use generate_clarification_question() directly.
    """
    query = (user_query or "").strip()
    if not query:
        return "What would you like me to do?"

    memory_snippet = _fetch_memory_snippet(query, top_k=top_k)
    memory_block = f"\nMemory: {memory_snippet}" if memory_snippet else ""

    prompt = (
        "You are a helpful AI desktop assistant. The user said something vague. "
        "Ask ONE short, specific follow-up question to clarify their intent. "
        "Use the memory if it is relevant. Be natural and conversational. "
        "Output ONLY the question text.\n\n"
        f"User: {query}"
        f"{memory_block}\n"
    )
    result = generate_text(prompt).strip()
    if result and result.endswith("?") and len(result) >= 8:
        return result

    return f"Could you give me more details about what you'd like to do with '{query}'?"


# ─────────────────────────────────────────────────────────────────────────────
# Intent-aware missing field detection
# ─────────────────────────────────────────────────────────────────────────────

# Maps intent → list of required parameter fields
_REQUIRED_FIELDS: Dict[str, List[str]] = {
    "open_app":         ["app_name"],
    "close_window":     ["app_name"],
    "set_volume":       ["level"],
    "set_brightness":   ["level"],
    "type_text":        ["text"],
    "open_file":        ["path"],
    "save_file":        ["path", "content"],
    "create_file":      ["path"],
    "delete_file":      ["path"],
    "list_directory":   ["path"],
    "move_file":        ["source", "destination"],
    "copy_file":        ["source", "destination"],
    "open_url":         ["url"],
    "search_web":       ["query"],
    "click_element":    ["selector"],
    "extract_text":     ["url"],
    "get_weather":      [],             # location is optional (defaults to current)
    "get_news":         [],             # topic is optional
    "search_papers":    ["query"],
    "get_stock":        ["symbol"],
    "toggle_wifi":      ["state"],
    "toggle_bluetooth": ["state"],
    "toggle_airplane":  ["state"],
    "fill_form":        ["fields"],
    "create_schedule":  ["text"],
    "edit_schedule":    ["command"],
    "send_message":     ["contact", "message"],
}


def _infer_missing_fields(intent: str, parameters: Dict[str, Any]) -> List[str]:
    """Return the list of required fields that are absent from parameters."""
    required = _REQUIRED_FIELDS.get(intent, [])
    missing: List[str] = []
    for field in required:
        value = parameters.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(field)
    return missing


def needs_clarification(intent: str, parameters: Dict[str, Any]) -> bool:
    """Return True if the intent is missing required parameters."""
    return bool(_infer_missing_fields(intent, parameters))


# ─────────────────────────────────────────────────────────────────────────────
# Risk classification helpers (used by agent.py)
# ─────────────────────────────────────────────────────────────────────────────

# Intents that always require confirmation before executing
CONFIRMATION_REQUIRED: frozenset = frozenset({
    "delete_file",
    "shutdown",
    "restart",
    "send_message",
    "fill_form",
    "move_file",
    "toggle_airplane",
})

# Intents that should offer an enrichment question (but can skip if user seems expert)
ENRICHMENT_ELIGIBLE: frozenset = frozenset({
    "search_web",
    "search_papers",
    "get_news",
    "open_app",
    "create_schedule",
    "screenshot",
    "extract_text",
    "get_weather",
})


def requires_confirmation(intent: str) -> bool:
    """Return True if this intent must be confirmed before execution."""
    return intent.strip().lower() in CONFIRMATION_REQUIRED


def is_enrichment_eligible(intent: str) -> bool:
    """Return True if an enrichment question might improve this intent's result."""
    return intent.strip().lower() in ENRICHMENT_ELIGIBLE