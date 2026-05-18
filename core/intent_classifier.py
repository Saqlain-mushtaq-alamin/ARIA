"""Intent classification via Ollama.

Two-stage classification:
  Stage 1 — Decide if input is CONVERSATIONAL (needs a text reply) or
             ACTIONABLE (needs a tool/function to be executed).
  Stage 2 — If actionable, extract the structured intent + parameters.

This stops errors like "how are you" triggering broken JSON parsing,
and allows multi-step commands like "open notepad and write a story" to
be decomposed into an ordered list of action steps.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List

import ollama

try:
    from vision.screen_reader import llm_busy_context
except Exception:
    from contextlib import nullcontext as llm_busy_context  # type: ignore


# ─────────────────────────────────────────────────────────────────────────────
# Stage-1 prompt: conversational vs actionable
# ─────────────────────────────────────────────────────────────────────────────
_STAGE1_SYSTEM = """You are a routing classifier for an AI desktop assistant.

Decide if the user's message is:
  - "conversational": a greeting, question, opinion, request for information,
    casual chat, or anything that should be answered with a text reply only.
  - "actionable": a command that requires controlling the computer, browser,
    files, system settings, schedule, or internet data retrieval.

Multi-step commands like "open notepad and write a story then save it" are ACTIONABLE.
Requests like "what is the weather?" or "find me a research paper on X" are ACTIONABLE
  (they need internet tools).
Greetings, how-are-you, opinions, trivia questions are CONVERSATIONAL.

Return ONLY a compact JSON: {"type": "conversational"} or {"type": "actionable"}
No markdown, no explanation.
"""

# ─────────────────────────────────────────────────────────────────────────────
# Stage-2 prompt: detailed intent + step decomposition
# ─────────────────────────────────────────────────────────────────────────────
_STAGE2_SYSTEM = """You are an intent extractor for an AI desktop assistant.

For the user command, return ONLY a compact JSON object with this structure:

Single-step command:
{
  "intent": "<intent_name>",
  "parameters": { ... },
  "app": "<optional app name>"
}

Multi-step command (when the user asks to do 2 or more things in sequence):
{
  "intent": "multi_step",
  "steps": [
    {"intent": "<intent_name>", "parameters": { ... }},
    {"intent": "<intent_name>", "parameters": { ... }}
  ]
}

AVAILABLE INTENTS AND THEIR REQUIRED PARAMETERS:

System control:
  open_app          → parameters.app_name (string)
  open_folder       → parameters.path (string)
  close_window      → parameters.app_name (string)
  set_volume        → parameters.level (int 0-100)
  get_clipboard     → no parameters
  type_text         → parameters.text (string)
  shutdown          → no parameters
  restart           → no parameters
  lock_screen       → no parameters
  sleep             → no parameters
  toggle_wifi       → parameters.state ("on" or "off")
  toggle_bluetooth  → parameters.state ("on" or "off")
  toggle_airplane   → parameters.state ("on" or "off")
  screenshot        → parameters.path (optional save path string)
  set_brightness    → parameters.level (int 0-100)
  activate_kinetic_mode   → no parameters
  deactivate_kinetic_mode → no parameters

File operations:
  open_file         → parameters.path (string)
  save_file         → parameters.path (string), parameters.content (string)
  create_file       → parameters.path (string), parameters.content (optional string)
  delete_file       → parameters.path (string)
  list_directory    → parameters.path (string)
  move_file         → parameters.source (string), parameters.destination (string)
  copy_file         → parameters.source (string), parameters.destination (string)

Browser & web:
  open_url          → parameters.url (string)
  search_web        → parameters.query (string), parameters.engine (optional: google/duckduckgo)
  click_element     → parameters.selector (string)
  fill_form         → parameters.url (string), parameters.fields (object)
  extract_text      → parameters.url (string), parameters.max_chars (optional int)
  get_weather       → parameters.location (string, optional - use "current" if not specified)
  get_news          → parameters.topic (optional string), parameters.count (optional int, default 5)
  search_papers     → parameters.query (string), parameters.source (optional: arxiv/scholar/pubmed)
  get_stock         → parameters.symbol (string)

Downloads:
    download          → parameters.url (string for file/video/audio),
                                            parameters.mode (optional: auto/video/audio/paper),
                                            parameters.query (string when mode=paper),
                                            parameters.output_name (optional string),
                                            parameters.open_folder (optional bool),
                                            parameters.open_file (optional bool)

Scheduler:
  create_schedule   → parameters.text (string with task descriptions)
  show_schedule     → no parameters
  whats_next        → no parameters
  edit_schedule     → parameters.command (string)

Upgrade features:
  comment_on_post   → parameters.tone (optional: "thoughtful", "funny", "supportive")
  explain_selected  → no parameters (reads highlighted text from screen)
  start_focus_mode  → parameters.task (string), parameters.work_minutes (optional int)
  stop_focus_mode   → no parameters
  decompose_goal    → parameters.goal (string), parameters.deadline (optional string)
  show_settings     → parameters.section (optional: "emotion", "screen_reader", "llm", etc.)
  change_setting    → parameters.key (string like "emotion.enabled"), parameters.value (any)
  query_knowledge   → parameters.topic (string)
  show_habits       → no parameters
  mark_habit        → parameters.habit_name (string like "gym", "study")
  show_profile      → no parameters

Conversational / LLM:
  answer_question   → parameters.prompt (string - the user's full question)
  type_generated_text → parameters.prompt (string)

Notes for multi-step commands:
- "open notepad and write a story about a lazy cat then save to desktop" →
    steps: open_app(notepad), type_text(story text generated), save_file(Desktop\\story.txt)
- "search for weather in Dhaka and tell me" →
    steps: get_weather(Dhaka) — single step is fine here
- For type_text steps that follow an open_app step, if the text needs to be
  generated (like "a story", "a poem", etc.), set parameters.generate=true and
  parameters.prompt to describe what to generate.
- If user mentions typos like "nodepad", "fle explorere", or "activite kinetic",
  normalize to the proper intent and parameters.
- "show settings" or "show my settings" → show_settings
- "turn off emotion detector" or "disable screen reader" → change_setting
- "start focus mode for coding" → start_focus_mode with task="coding"
- "comment on this post" → comment_on_post
- "explain this" or "what does this mean" (when text is selected) → explain_selected
- "plan my goal: learn python in 30 days" → decompose_goal
- "download https://example.com/file.pdf" → download with url
- "download this youtube video" → download with mode=video and url
- "download paper attention is all you need" → download with mode=paper and query
- If user says "download and open it" → set download parameters.open_file=true
- If user says "download and open folder" → set download parameters.open_folder=true

Return ONLY the JSON. No markdown. No explanation.
"""


def _extract_json(text: str) -> Dict[str, Any] | None:
    """Robustly extract the first valid JSON object from model output."""
    if not text:
        return None
    # Strip markdown fences
    text = re.sub(r"```(?:json)?", "", text).strip().strip("`").strip()
    # Find first { ... } block
    start = text.find("{")
    if start == -1:
        return None
    depth, in_str, escape = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            escape = (ch == "\\" and not escape)
            if ch == '"' and not escape:
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(text[start: i + 1])
                    return obj if isinstance(obj, dict) else None
                except json.JSONDecodeError:
                    return None
    return None


def _call_ollama(system: str, user: str, model: str) -> str:
    """Call Ollama and return the raw content string."""
    with llm_busy_context():
        response = ollama.chat(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            options={"temperature": 0},
        )
    return response.get("message", {}).get("content", "").strip()


def _normalize_user_text(user_text: str) -> str:
    """Correct frequent speech-to-text and typing mistakes before classification."""
    text = user_text or ""
    replacements = [
        (r"\bnodepad\b", "notepad"),
        (r"\bfle\s+explorere\b", "file explorer"),
        (r"\bfile\s+explorere\b", "file explorer"),
        (r"\bcamo\s+studi[o0]\b", "camo studio"),
        (r"\bactivite\b", "activate"),
        (r"\bkinitic\b", "kinetic"),
        (r"\bbluetooh\b", "bluetooth"),
        (r"\bwi[\s-]?fi\b", "wifi"),
        (r"\bdownlad\b", "download"),
    ]
    for pattern, repl in replacements:
        text = re.sub(pattern, repl, text, flags=re.IGNORECASE)
    return text


def is_conversational(user_text: str, model: str = "llama3") -> bool:
    """Return True if the input is conversational (needs a text reply, not a tool call)."""
    normalized = _normalize_user_text(user_text)
    raw = _call_ollama(_STAGE1_SYSTEM, normalized, model)
    obj = _extract_json(raw)
    if obj and isinstance(obj, dict):
        return str(obj.get("type", "")).lower() == "conversational"
    # Fallback heuristics when the model fails JSON
    lowered = user_text.strip().lower()
    convo_starters = (
        "how are you", "who are you", "what are you", "tell me about yourself",
        "hello", "hi", "hey", "good morning", "good night", "thanks", "thank you",
        "what is", "what's", "explain ", "define ", "why ", "who ", "when ",
        "can you ", "could you tell", "do you know",
    )
    return any(lowered.startswith(s) for s in convo_starters)


def classify_intent(user_text: str, model: str = "llama3") -> Dict[str, Any]:
    """Two-stage classification: route, then extract intent.

    Returns a dict with at minimum {"intent": "<name>", "parameters": {...}}.
    For multi-step commands returns {"intent": "multi_step", "steps": [...]}.
    For conversational input returns {"intent": "conversational", "parameters": {"prompt": user_text}}.
    """
    if not user_text or not user_text.strip():
        return {"intent": "unknown", "parameters": {}}

    # ── Stage 1: conversational vs actionable ───────────────────────────────
    normalized = _normalize_user_text(user_text)

    if is_conversational(normalized, model):
        return {
            "intent": "conversational",
            "parameters": {"prompt": user_text},
        }

    # ── Stage 2: structured intent extraction ───────────────────────────────
    raw = _call_ollama(_STAGE2_SYSTEM, normalized, model)
    if not raw:
        raise ValueError("Empty response from model")

    obj = _extract_json(raw)
    if obj is None:
        raise ValueError(f"Model did not return valid JSON: {raw}")

    if not isinstance(obj, dict):
        return {"intent": "unknown", "parameters": {}}

    # Normalise: ensure parameters key always exists
    if "parameters" not in obj and "steps" not in obj:
        obj["parameters"] = {}
    if "parameters" in obj and not isinstance(obj["parameters"], dict):
        obj["parameters"] = {}

    return obj


def classify_intent_batch(texts: List[str], model: str = "llama3") -> List[Dict[str, Any]]:
    """Classify a list of inputs (convenience wrapper)."""
    return [classify_intent(t, model) for t in texts]
