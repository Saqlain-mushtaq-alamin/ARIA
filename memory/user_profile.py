"""ARIA — User Profile Manager.

Maintains a living, growing JSON profile about the user that makes ARIA
feel like it genuinely knows you. Every interaction teaches it something new.

What it learns automatically (no manual setup needed):
  ─────────────────────────────────────────────────────
  • Your name / preferred address form  →  ARIA addresses you as "Sir" by default
    but learns your real name if you introduce yourself
  • Your preferred apps  →  which ones you open most, in what order, at what time
  • Your command vocabulary  →  your slang, shortcuts, abbreviations, dialect
  • Your frequent contacts  →  people you message most
  • Your active hours  →  when you work, sleep, take breaks
  • Your preferred language style  →  formal vs casual, verbose vs concise
  • Your timezone  →  inferred from system clock
  • Your frequently visited URLs / search topics

Profile storage: memory/user_profile.json  (human-readable, editable)

Integration with agent.py:
  ─────────────────────────
  profile = get_profile()
  address = get_address_form()                    →  "Sir" (or "John" once learned)
  system_prompt = build_persona_system_prompt()   →  inject into every LLM call
  record_command(intent, parameters, user_text)   →  call after every action
  record_conversation(user_text, assistant_text)  →  call after every chat turn
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_PROFILE_PATH = os.path.join(
    os.path.dirname(__file__), "user_profile.json"
)

# The default address form used until the user's name is learned
_DEFAULT_ADDRESS = "Sir"

# How many interactions before the profile considers a pattern "established"
_PATTERN_MIN_COUNT = 3


# ─────────────────────────────────────────────────────────────────────────────
# Default profile structure
# ─────────────────────────────────────────────────────────────────────────────

def _default_profile() -> Dict[str, Any]:
    return {
        "meta": {
            "created_at":     datetime.now(timezone.utc).isoformat(),
            "last_updated":   datetime.now(timezone.utc).isoformat(),
            "total_commands": 0,
            "total_messages": 0,
            "profile_version": "1.0",
        },
        "identity": {
            "name":             None,           # learned from "my name is X" patterns
            "address_form":     _DEFAULT_ADDRESS,  # "Sir" until name is known
            "timezone":         _detect_timezone(),
            "language":         "en",
        },
        "style": {
            "verbosity":        "balanced",     # "concise" | "balanced" | "verbose"
            "formality":        "casual",       # "formal" | "casual"
            "response_length":  "medium",       # "short" | "medium" | "long"
            "use_emoji":        False,
        },
        "vocabulary": {
            # slang_map: {user_phrase → normalised_intent_description}
            # e.g. {"yaar open this": "open this", "bhai search karo": "search"}
            "slang_map":        {},
            "frequent_phrases": {},             # phrase → count
            "shortcuts":        {},             # shortcut → full command
        },
        "apps": {
            "most_used":        {},             # app_name → open_count
            "last_opened":      {},             # app_name → last_timestamp
            "startup_sequence": [],             # apps opened in the first 10 min of sessions
            "avoided":          [],             # apps explicitly closed quickly
        },
        "contacts": {
            "frequent":         {},             # contact_name → message_count
            "recent":           [],             # [{"name": ..., "platform": ..., "last": ...}]
        },
        "urls": {
            "frequent":         {},             # url → visit_count
            "categories":       {},             # url → category (work/study/entertainment)
        },
        "search_topics": {
            "frequent":         {},             # topic → search_count
            "recent":           [],             # last 20 search queries
        },
        "active_hours": {
            # hour (0-23) → command_count
            "by_hour":          {str(h): 0 for h in range(24)},
            "peak_work_hours":  [],             # [9, 10, 11] etc — inferred
            "typical_start":    None,           # "09:00"
            "typical_end":      None,           # "23:00"
        },
        "command_history": {
            # intent → count
            "by_intent":        {},
            "recent_intents":   [],             # last 50 intents (circular)
        },
    }


def _detect_timezone() -> str:
    try:
        import time as _time
        offset_sec = -_time.timezone if not _time.daylight else -_time.altzone
        hours = offset_sec // 3600
        mins  = (offset_sec % 3600) // 60
        return f"UTC{'+' if hours >= 0 else ''}{hours:02d}:{abs(mins):02d}"
    except Exception:
        return "UTC+00:00"


# ─────────────────────────────────────────────────────────────────────────────
# Load / save
# ─────────────────────────────────────────────────────────────────────────────

def _load_profile(path: str = DEFAULT_PROFILE_PATH) -> Dict[str, Any]:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if not os.path.exists(path):
        profile = _default_profile()
        _save_profile(profile, path)
        return profile
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return _default_profile()


def _save_profile(profile: Dict[str, Any], path: str = DEFAULT_PROFILE_PATH) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    profile.setdefault("meta", {})["last_updated"] = datetime.now(timezone.utc).isoformat()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2, ensure_ascii=False)


def get_profile(path: str = DEFAULT_PROFILE_PATH) -> Dict[str, Any]:
    """Load and return the full user profile dict."""
    return _load_profile(path)


def update_profile(
    updates: Dict[str, Any],
    path: str = DEFAULT_PROFILE_PATH,
) -> None:
    """Deep-merge updates into the profile and save.

    Example:
        update_profile({"identity": {"name": "Rahim"}})
    """
    profile = _load_profile(path)
    _deep_merge(profile, updates)
    _save_profile(profile, path)


def _deep_merge(base: Dict, overlay: Dict) -> None:
    """Recursively merge overlay into base in-place."""
    for k, v in overlay.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


# ─────────────────────────────────────────────────────────────────────────────
# Identity & address form
# ─────────────────────────────────────────────────────────────────────────────

def get_address_form(path: str = DEFAULT_PROFILE_PATH) -> str:
    """Return how ARIA should address the user.

    Returns the user's name if known (e.g. "Rahim"),
    otherwise returns the default "Sir".
    """
    profile = _load_profile(path)
    name = profile.get("identity", {}).get("name")
    address = profile.get("identity", {}).get("address_form", _DEFAULT_ADDRESS)
    if name:
        return name
    return address or _DEFAULT_ADDRESS


def set_name(name: str, path: str = DEFAULT_PROFILE_PATH) -> str:
    """Explicitly set the user's name.

    ARIA will use this name going forward instead of "Sir".
    Also called automatically when 'my name is X' is detected.
    """
    name = name.strip()
    if not name:
        return "No name provided."
    update_profile({"identity": {"name": name, "address_form": name}}, path)
    return f"Got it — I'll call you {name} from now on."


def _try_extract_name(user_text: str, path: str = DEFAULT_PROFILE_PATH) -> None:
    """Silently check if the user introduced their name and save it."""
    patterns = [
        r"my name is ([A-Za-z]+)",
        r"call me ([A-Za-z]+)",
        r"i am ([A-Za-z]+)",
        r"i'm ([A-Za-z]+)",
        r"this is ([A-Za-z]+)",
    ]
    for pattern in patterns:
        m = re.search(pattern, user_text, re.IGNORECASE)
        if m:
            candidate = m.group(1).strip().title()
            # Reject common false positives
            if candidate.lower() not in {
                "fine", "good", "okay", "ok", "here", "back", "done",
                "ready", "set", "the", "a", "an", "going",
            }:
                set_name(candidate, path)
                return


# ─────────────────────────────────────────────────────────────────────────────
# Automatic learning from commands
# ─────────────────────────────────────────────────────────────────────────────

def record_command(
    intent: str,
    parameters: Optional[Dict[str, Any]] = None,
    user_text: str = "",
    path: str = DEFAULT_PROFILE_PATH,
) -> None:
    """Record that a command was executed. Updates frequency counters.

    Call this in agent.py after every successful dispatch_intent().
    """
    profile = _load_profile(path)
    params = parameters or {}
    now    = datetime.now(timezone.utc).isoformat()
    hour   = str(datetime.now().hour)

    # Meta counters
    profile["meta"]["total_commands"] = profile["meta"].get("total_commands", 0) + 1

    # Active hours
    by_hour = profile["active_hours"]["by_hour"]
    by_hour[hour] = by_hour.get(hour, 0) + 1
    profile["active_hours"]["by_hour"] = by_hour

    # Command history by intent
    by_intent = profile["command_history"].get("by_intent", {})
    by_intent[intent] = by_intent.get(intent, 0) + 1
    profile["command_history"]["by_intent"] = by_intent

    # Recent intents circular buffer (last 50)
    recent = profile["command_history"].get("recent_intents", [])
    recent.append({"intent": intent, "at": now[:16]})
    profile["command_history"]["recent_intents"] = recent[-50:]

    # App tracking
    if intent in {"open_app", "close_window"}:
        app_name = (
            params.get("app_name") or params.get("name") or ""
        ).strip().lower()
        if app_name and intent == "open_app":
            most_used = profile["apps"]["most_used"]
            most_used[app_name] = most_used.get(app_name, 0) + 1
            profile["apps"]["most_used"] = most_used
            profile["apps"]["last_opened"][app_name] = now[:16]

    # Contact tracking
    if intent == "send_message":
        contact = params.get("contact", "").strip()
        if contact:
            freq = profile["contacts"]["frequent"]
            freq[contact] = freq.get(contact, 0) + 1
            profile["contacts"]["frequent"] = freq
            # Update recent list
            recent_contacts = profile["contacts"].get("recent", [])
            recent_contacts = [c for c in recent_contacts if c.get("name") != contact]
            recent_contacts.insert(0, {
                "name":     contact,
                "platform": params.get("platform", "unknown"),
                "last":     now[:16],
            })
            profile["contacts"]["recent"] = recent_contacts[:20]

    # URL / search tracking
    if intent == "open_url":
        url = params.get("url", "").strip()
        if url:
            freq_urls = profile["urls"]["frequent"]
            freq_urls[url] = freq_urls.get(url, 0) + 1
            profile["urls"]["frequent"] = freq_urls

    if intent == "search_web":
        query = params.get("query", "").strip()
        if query:
            freq_topics = profile["search_topics"]["frequent"]
            freq_topics[query] = freq_topics.get(query, 0) + 1
            profile["search_topics"]["frequent"] = freq_topics
            recent_searches = profile["search_topics"].get("recent", [])
            recent_searches.insert(0, query)
            profile["search_topics"]["recent"] = recent_searches[:20]

    # Vocabulary / dialect learning
    if user_text.strip():
        _update_vocabulary(profile, user_text, intent)

    # Infer peak work hours
    _update_peak_hours(profile)

    _save_profile(profile, path)


def record_conversation(
    user_text: str,
    assistant_text: str,
    path: str = DEFAULT_PROFILE_PATH,
) -> None:
    """Record a conversational turn. Learns name, style preferences.

    Call this in agent.py after every conversational LLM reply.
    """
    profile = _load_profile(path)
    profile["meta"]["total_messages"] = profile["meta"].get("total_messages", 0) + 1

    # Try to learn the user's name silently
    _try_extract_name(user_text, path)

    # Style inference: detect if user prefers short replies
    if any(phrase in user_text.lower() for phrase in (
        "short", "brief", "quick", "just tell me", "in one line", "tldr", "summarize"
    )):
        profile["style"]["verbosity"] = "concise"

    if any(phrase in user_text.lower() for phrase in (
        "explain in detail", "tell me everything", "in depth", "elaborate", "go deep"
    )):
        profile["style"]["verbosity"] = "verbose"

    _save_profile(profile, path)


def _update_vocabulary(
    profile: Dict[str, Any], user_text: str, intent: str
) -> None:
    """Detect slang / shortcut patterns and store them."""
    text = user_text.strip().lower()
    freq = profile["vocabulary"].get("frequent_phrases", {})

    # Count short phrases (2-5 words) as potential shortcuts
    words = text.split()
    if 2 <= len(words) <= 5:
        freq[text] = freq.get(text, 0) + 1
        profile["vocabulary"]["frequent_phrases"] = freq

        # If a phrase has been used 3+ times, promote to shortcuts
        if freq.get(text, 0) >= _PATTERN_MIN_COUNT:
            shortcuts = profile["vocabulary"].get("shortcuts", {})
            if text not in shortcuts:
                shortcuts[text] = intent
                profile["vocabulary"]["shortcuts"] = shortcuts


def _update_peak_hours(profile: Dict[str, Any]) -> None:
    """Infer peak work hours from the hourly command distribution."""
    by_hour = profile["active_hours"].get("by_hour", {})
    if not by_hour:
        return
    # Find top-4 active hours
    sorted_hours = sorted(by_hour.items(), key=lambda x: int(x[1]), reverse=True)
    peak = sorted([int(h) for h, _ in sorted_hours[:4]])
    profile["active_hours"]["peak_work_hours"] = peak

    # Infer typical start (earliest active hour with > 2 commands)
    active = [int(h) for h, c in by_hour.items() if int(c) > 2]
    if active:
        profile["active_hours"]["typical_start"] = f"{min(active):02d}:00"
        profile["active_hours"]["typical_end"]   = f"{max(active):02d}:59"


# ─────────────────────────────────────────────────────────────────────────────
# Profile insights
# ─────────────────────────────────────────────────────────────────────────────

def get_top_apps(n: int = 5, path: str = DEFAULT_PROFILE_PATH) -> List[str]:
    """Return the top N most-used apps."""
    profile = _load_profile(path)
    most_used = profile["apps"].get("most_used", {})
    return [app for app, _ in sorted(most_used.items(), key=lambda x: x[1], reverse=True)[:n]]


def get_top_contacts(n: int = 5, path: str = DEFAULT_PROFILE_PATH) -> List[str]:
    """Return the top N most-messaged contacts."""
    profile = _load_profile(path)
    freq = profile["contacts"].get("frequent", {})
    return [c for c, _ in sorted(freq.items(), key=lambda x: x[1], reverse=True)[:n]]


def get_shortcuts(path: str = DEFAULT_PROFILE_PATH) -> Dict[str, str]:
    """Return all learned shortcut phrases and their mapped intents."""
    profile = _load_profile(path)
    return profile["vocabulary"].get("shortcuts", {})


def get_peak_hours(path: str = DEFAULT_PROFILE_PATH) -> List[int]:
    """Return inferred peak work hours as a list of ints."""
    profile = _load_profile(path)
    return profile["active_hours"].get("peak_work_hours", [])


def get_profile_summary(path: str = DEFAULT_PROFILE_PATH) -> str:
    """Return a human-readable profile summary — used in weekly review."""
    profile = _load_profile(path)
    address = get_address_form(path)
    meta    = profile.get("meta", {})
    apps    = get_top_apps(5, path)
    contacts = get_top_contacts(5, path)
    peaks   = get_peak_hours(path)
    style   = profile.get("style", {})
    shortcuts = get_shortcuts(path)

    lines = [
        f"👤 User Profile — {address}",
        f"   Total commands : {meta.get('total_commands', 0):,}",
        f"   Total messages : {meta.get('total_messages', 0):,}",
        f"   Since          : {(meta.get('created_at') or '?')[:10]}",
        f"   Timezone       : {profile.get('identity', {}).get('timezone', '?')}",
        f"",
        f"   Top apps       : {', '.join(apps) if apps else 'none yet'}",
        f"   Top contacts   : {', '.join(contacts) if contacts else 'none yet'}",
        f"   Peak hours     : {peaks if peaks else 'still learning...'}",
        f"   Style          : {style.get('verbosity', '?')} / {style.get('formality', '?')}",
    ]
    if shortcuts:
        lines.append(f"   Shortcuts      : {len(shortcuts)} learned")
        for phrase, intent in list(shortcuts.items())[:3]:
            lines.append(f"     '{phrase}' → {intent}")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# LLM persona builder — inject into every system prompt
# ─────────────────────────────────────────────────────────────────────────────

def build_persona_system_prompt(path: str = DEFAULT_PROFILE_PATH) -> str:
    """Build a personalised system prompt block to inject into every LLM call.

    This block tells the LLM:
      - How to address the user (Sir / name)
      - User's style preferences
      - Their top apps and contacts (for smarter suggestions)
      - Their peak work hours (for schedule-aware responses)
      - Any learned shortcuts / vocabulary

    Returns a string to prepend to any system prompt.
    """
    address  = get_address_form(path)
    profile  = _load_profile(path)
    style    = profile.get("style", {})
    peaks    = get_peak_hours(path)
    apps     = get_top_apps(3, path)
    contacts = get_top_contacts(3, path)
    shortcuts = get_shortcuts(path)

    verbosity_instruction = {
        "concise":  "Keep all responses SHORT and direct. No padding.",
        "verbose":  "Be thorough and detailed in your responses.",
        "balanced": "Match response length to the complexity of the request.",
    }.get(style.get("verbosity", "balanced"), "Match response length to the request.")

    peaks_line = ""
    if peaks:
        peak_str = ", ".join(f"{h:02d}:00" for h in peaks)
        peaks_line = f"- The user's peak productive hours are around {peak_str}. Factor this into schedule advice."

    apps_line = f"- Frequently used apps: {', '.join(apps)}." if apps else ""
    contacts_line = f"- Frequent contacts: {', '.join(contacts)}." if contacts else ""

    shortcut_lines = ""
    if shortcuts:
        examples = "; ".join(f"'{k}' means '{v}'" for k, v in list(shortcuts.items())[:5])
        shortcut_lines = f"- Learned vocabulary shortcuts: {examples}."

    persona_block = f"""
ARIA PERSONA INSTRUCTIONS (always follow these):
- Always address the user as "{address}" — this is mandatory in every response.
- Begin responses warmly. Example: "Of course, {address}." / "Right away, {address}." / "Sure, {address}."
- {verbosity_instruction}
- Your tone is professional but warm — like a highly capable personal assistant.
- Never be robotic or use filler phrases like "Certainly!" without context.
- After completing an action, offer a relevant follow-up suggestion when natural.
{peaks_line}
{apps_line}
{contacts_line}
{shortcut_lines}
""".strip()

    return persona_block


def get_style_preferences(path: str = DEFAULT_PROFILE_PATH) -> Dict[str, str]:
    """Return the user's current style preferences dict."""
    profile = _load_profile(path)
    return dict(profile.get("style", {}))


def reset_profile(path: str = DEFAULT_PROFILE_PATH) -> str:
    """Reset the profile to defaults (keeps name if set)."""
    old = _load_profile(path)
    name = old.get("identity", {}).get("name")
    new_profile = _default_profile()
    if name:
        new_profile["identity"]["name"] = name
        new_profile["identity"]["address_form"] = name
    _save_profile(new_profile, path)
    address = name or _DEFAULT_ADDRESS
    return f"Profile reset, {address}. Usage history cleared."
