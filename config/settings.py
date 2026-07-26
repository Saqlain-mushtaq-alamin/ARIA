"""ARIA — Centralised Settings Manager.

Provides a single, persistent, JSON-backed configuration system for ALL
tuneable parameters across the entire ARIA assistant.

Every setting can be changed at runtime via voice/text commands like:
    "set emotion detector to off"
    "change screen reader interval to 120 seconds"
    "switch model to mistral"
    "show settings"

Settings file: config/aria_settings.json  (human-readable, editable)

Usage from any module:
    from config.settings import get, set_value, get_all

    model = get("llm.model")           # → "llama3"
    set_value("emotion.enabled", False) # saves immediately
"""

from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────────────────────

_SETTINGS_DIR = os.path.dirname(os.path.abspath(__file__))
_SETTINGS_PATH = os.path.join(_SETTINGS_DIR, "aria_settings.json")
_LOCK = threading.Lock()

# ─────────────────────────────────────────────────────────────────────────────
# Default settings — the complete schema with sane defaults
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULTS: Dict[str, Any] = {
    # ── LLM settings ──────────────────────────────────────────────────────
    "llm": {
        "model": "llama3",                    # primary LLM model for Ollama
        "vision_model": "llava:7b",           # vision model for screen reading
        "temperature": 0.7,                   # default generation temperature
        "ollama_url": "http://localhost:11434",# Ollama API base URL
    },

    # ── Emotion detector ──────────────────────────────────────────────────
    "emotion": {
        "enabled": True,                      # on/off toggle
        "interval_seconds": 60,               # sampling interval
        "window_minutes": 60,                 # rolling summary window
        "camera_index": 0,                    # webcam index
        "require_face": True,                 # require detected face
        "min_frame_stddev": 5.0,              # reject blank/blocked frames
        "log_samples": True,                  # log each sample to console
        "save_last_frame": False,             # save last captured frame
        "rule_speak": False,                  # speak vibe rule alerts
        "rule_states": "tired,stressed,frustrated",
        "rule_mode": "anytime_sustained",
        "rule_night_start": 21,
        "rule_night_end": 6,
        "rule_tasks_to_move": 2,
        "rule_min_score": 35,
        "rule_sustained_samples": 3,
        "rule_sustained_minutes": 25,
        "rule_cooldown_minutes": 45,
    },

    # ── Screen reader ─────────────────────────────────────────────────────
    "screen_reader": {
        "enabled": True,                      # on/off toggle
        "interval_seconds": 60,               # capture interval
        "history_size": 5,                    # number of summaries to keep
        "stuck_minutes": 20,                  # minutes on same activity before nudge
        "debug": False,                       # verbose logging
        "log_summaries": True,                # log summaries to console
        "nudge_cooldown_minutes": 10,         # min minutes between proactive nudges
    },

    # ── Cognitive monitor ─────────────────────────────────────────────────
    "cognitive_monitor": {
        "enabled": True,                      # on/off toggle
        "update_interval_seconds": 300,       # recompute every N seconds
        "window_size": 100,                   # last N keystrokes to analyse
        "alert_threshold": 80,                # cognitive load score to trigger alert
    },

    # ── Productivity guardian ─────────────────────────────────────────────
    "productivity_guardian": {
        "enabled": True,                      # on/off toggle
        "check_interval_seconds": 60,         # check every N seconds
        "distraction_threshold_minutes": 3,   # alert after N minutes of distraction
        "cooldown_after_alert_minutes": 10,   # wait N minutes before next alert
        "distraction_apps": [
            "instagram", "facebook", "twitter", "tiktok", "reddit",
            "youtube", "netflix", "twitch", "snapchat", "discord",
            "whatsapp web", "telegram web",
        ],
    },

    # ── Focus mode ────────────────────────────────────────────────────────
    "focus_mode": {
        "work_minutes": 25,                   # Pomodoro work duration
        "short_break_minutes": 5,             # short break duration
        "long_break_minutes": 20,             # long break duration
        "cycles_before_long_break": 4,        # cycles before long break
        "block_domains": [
            "instagram.com", "facebook.com", "twitter.com", "x.com",
            "tiktok.com", "reddit.com", "youtube.com", "netflix.com",
            "twitch.tv", "9gag.com", "buzzfeed.com", "snapchat.com",
        ],
    },

    # ── Autonomous planner ────────────────────────────────────────────────
    "autonomous_planner": {
        "enabled": True,                      # on/off toggle
        "morning_briefing_time": "06:00",     # HH:MM
        "evening_review_time": "22:00",       # HH:MM
    },

    # ── Voice ─────────────────────────────────────────────────────────────
    "voice": {
        "mode": "wakeword",                   # wakeword | always | off
        "stt_model": "base",                  # whisper model size
        "session_seconds": 25,                # voice session timeout
        "max_empty_listens": 2,               # max empty listens before ending session
        "tts_enabled": True,                  # speak responses aloud
    },

    # ── Security ──────────────────────────────────────────────────────────
    "security": {
        "require_confirmation_for_dangerous": True,
        "audit_log_enabled": True,
        "max_dangerous_actions_per_hour": 10,
        "pin_protected": False,               # require PIN for settings changes
        "pin_hash": "",                       # SHA-256 hash of PIN
        "sensitive_data_masking": True,        # mask personal data in logs
    },

    # ── UI ────────────────────────────────────────────────────────────────
    "ui": {
        "overlay_enabled": True,
        "theme": "dark",                      # dark | light
        "show_narration": True,               # show step-by-step narration
    },

    # ── Messenger ─────────────────────────────────────────────────────────
    "messenger": {
        "enabled": True,                      # on/off toggle
        "double_confirmation": True,          # HARD RULE — cannot be disabled
        "default_platform": "whatsapp",       # whatsapp | telegram | messenger
        "auto_reply_enabled": False,          # chatbot auto-reply mode
    },

    # ── Media control ─────────────────────────────────────────────────────
    "media_control": {
        "enabled": True,                      # on/off toggle
        "default_player": "spotify",          # spotify | vlc | youtube
    },

    # ── Notifier ──────────────────────────────────────────────────────────
    "notifier": {
        "enabled": True,                      # on/off toggle
        "read_aloud": True,                   # speak notifications via TTS
        "smart_suggestions": True,            # suggest actions for notifications
        "listener_enabled": False,            # background notification listener
    },

    # ── Meta ──────────────────────────────────────────────────────────────
    "meta": {
        "version": "2.1",
        "last_modified": "",
        "last_modified_by": "system",
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — dot-notation access for nested dicts
# ─────────────────────────────────────────────────────────────────────────────

def _deep_get(d: dict, dotted_key: str, default: Any = None) -> Any:
    """Get a value from a nested dict using dot notation: 'llm.model'."""
    keys = dotted_key.split(".")
    current = d
    for k in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(k, default)
        if current is default:
            return default
    return current


def _deep_set(d: dict, dotted_key: str, value: Any) -> None:
    """Set a value in a nested dict using dot notation: 'llm.model'."""
    keys = dotted_key.split(".")
    current = d
    for k in keys[:-1]:
        if k not in current or not isinstance(current[k], dict):
            current[k] = {}
        current = current[k]
    current[keys[-1]] = value


def _deep_merge(base: dict, overlay: dict) -> None:
    """Recursively merge overlay into base (in-place)."""
    for k, v in overlay.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


# ─────────────────────────────────────────────────────────────────────────────
# Load / Save
# ─────────────────────────────────────────────────────────────────────────────

def _load() -> Dict[str, Any]:
    """Load settings from disk, merged with defaults for any missing keys."""
    settings = deepcopy(_DEFAULTS)
    if os.path.exists(_SETTINGS_PATH):
        try:
            with open(_SETTINGS_PATH, "r", encoding="utf-8") as f:
                user_settings = json.load(f)
            _deep_merge(settings, user_settings)
        except Exception:
            pass  # corrupted file — use defaults
    return settings


def _save(settings: Dict[str, Any]) -> None:
    """Save settings to disk."""
    os.makedirs(os.path.dirname(_SETTINGS_PATH), exist_ok=True)
    settings.setdefault("meta", {})["last_modified"] = datetime.now(timezone.utc).isoformat()
    with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def get(key: str, default: Any = None) -> Any:
    """Get a setting value using dot notation.

    Examples:
        get("llm.model")               → "llama3"
        get("emotion.interval_seconds") → 60
        get("screen_reader.enabled")    → True
    """
    with _LOCK:
        settings = _load()
    value = _deep_get(settings, key)
    return value if value is not None else default


def set_value(key: str, value: Any, modified_by: str = "user") -> str:
    """Set a setting value and save.

    Returns a confirmation message.

    Examples:
        set_value("llm.model", "mistral")
        set_value("emotion.enabled", False)
        set_value("screen_reader.interval_seconds", 120)
    """
    with _LOCK:
        settings = _load()
        old_value = _deep_get(settings, key)
        _deep_set(settings, key, value)
        settings["meta"]["last_modified_by"] = modified_by
        _save(settings)

    return (
        f"Setting '{key}' changed: {_format_value(old_value)} → {_format_value(value)}"
    )


def get_all() -> Dict[str, Any]:
    """Return the full settings dict."""
    with _LOCK:
        return _load()


def get_section(section: str) -> Dict[str, Any]:
    """Return a top-level section of settings.

    Example: get_section("emotion") → {enabled: True, interval_seconds: 60, ...}
    """
    with _LOCK:
        settings = _load()
    return dict(settings.get(section, {}))


def reset_section(section: str) -> str:
    """Reset a section to defaults."""
    if section not in _DEFAULTS:
        return f"Unknown settings section: '{section}'"
    with _LOCK:
        settings = _load()
        settings[section] = deepcopy(_DEFAULTS[section])
        _save(settings)
    return f"Settings section '{section}' reset to defaults."


def reset_all() -> str:
    """Reset ALL settings to defaults."""
    with _LOCK:
        _save(deepcopy(_DEFAULTS))
    return "All settings reset to defaults."


def _format_value(v: Any) -> str:
    """Human-readable formatting for setting values."""
    if isinstance(v, bool):
        return "ON" if v else "OFF"
    if isinstance(v, list):
        return f"[{len(v)} items]"
    return str(v)


# ─────────────────────────────────────────────────────────────────────────────
# Formatted display — for "show settings" command
# ─────────────────────────────────────────────────────────────────────────────

def format_settings(section: Optional[str] = None) -> str:
    """Return a formatted string of settings for display.

    Args:
        section: Optional section name. If None, shows overview of all sections.
    """
    settings = get_all()

    if section and section in settings:
        return _format_section(section, settings[section])

    # Overview of all sections
    lines = [
        "⚙️  ARIA Settings",
        "━" * 55,
    ]

    section_icons = {
        "llm": "🤖",
        "emotion": "😊",
        "screen_reader": "🖥️",
        "cognitive_monitor": "🧠",
        "productivity_guardian": "🛡️",
        "focus_mode": "🎯",
        "autonomous_planner": "📅",
        "voice": "🎤",
        "security": "🔒",
        "ui": "🎨",
        "messenger": "💬",
        "media_control": "🎵",
        "notifier": "🔔",
    }

    for key, val in settings.items():
        if key == "meta":
            continue
        if not isinstance(val, dict):
            continue
        icon = section_icons.get(key, "📌")
        enabled = val.get("enabled")
        status = ""
        if enabled is not None:
            status = f"  [{'ON' if enabled else 'OFF'}]"
        lines.append(f"  {icon}  {key.replace('_', ' ').title()}{status}")

        # Show key settings for each section
        important_keys = _get_important_keys(key)
        for ik in important_keys:
            v = val.get(ik)
            if v is not None and ik != "enabled":
                display_key = ik.replace("_", " ").title()
                lines.append(f"      {display_key}: {_format_value(v)}")

    lines.append("━" * 55)
    lines.append("Say 'show settings <section>' for details.")
    lines.append("Say 'set <setting> to <value>' to change.")
    return "\n".join(lines)


def _format_section(name: str, data: dict) -> str:
    """Format a single section in detail."""
    section_icons = {
        "llm": "🤖",
        "emotion": "😊",
        "screen_reader": "🖥️",
        "cognitive_monitor": "🧠",
        "productivity_guardian": "🛡️",
        "focus_mode": "🎯",
        "autonomous_planner": "📅",
        "voice": "🎤",
        "security": "🔒",
        "ui": "🎨",
        "messenger": "💬",
        "media_control": "🎵",
        "notifier": "🔔",
    }
    icon = section_icons.get(name, "📌")
    title = name.replace("_", " ").title()
    lines = [
        f"{icon}  {title} Settings",
        "━" * 45,
    ]
    for key, value in data.items():
        display_key = key.replace("_", " ").title()
        if isinstance(value, list):
            lines.append(f"  {display_key}:")
            for item in value[:8]:
                lines.append(f"    • {item}")
            if len(value) > 8:
                lines.append(f"    ... and {len(value) - 8} more")
        elif isinstance(value, bool):
            lines.append(f"  {display_key}: {'ON' if value else 'OFF'}")
        else:
            lines.append(f"  {display_key}: {value}")

    lines.append("━" * 45)
    lines.append(f"To change: set {name}.<key> to <value>")
    return "\n".join(lines)


def _get_important_keys(section: str) -> List[str]:
    """Return the most important keys for a section overview."""
    return {
        "llm": ["model", "vision_model", "temperature"],
        "emotion": ["interval_seconds", "window_minutes"],
        "screen_reader": ["interval_seconds", "stuck_minutes"],
        "cognitive_monitor": ["update_interval_seconds", "alert_threshold"],
        "productivity_guardian": ["check_interval_seconds", "distraction_threshold_minutes"],
        "focus_mode": ["work_minutes", "short_break_minutes"],
        "autonomous_planner": ["morning_briefing_time", "evening_review_time"],
        "voice": ["mode", "stt_model"],
        "security": ["require_confirmation_for_dangerous", "sensitive_data_masking"],
        "ui": ["theme"],
        "messenger": ["default_platform", "auto_reply_enabled"],
        "media_control": ["default_player"],
        "notifier": ["read_aloud", "smart_suggestions"],
    }.get(section, [])


# ─────────────────────────────────────────────────────────────────────────────
# Natural language setting parser — for voice/text commands
# ─────────────────────────────────────────────────────────────────────────────

# Maps common phrases to settings keys
_NL_ALIASES: Dict[str, str] = {
    "emotion detector": "emotion.enabled",
    "emotion detection": "emotion.enabled",
    "emotion interval": "emotion.interval_seconds",
    "emotion sampling": "emotion.interval_seconds",
    "screen reader": "screen_reader.enabled",
    "screen reading": "screen_reader.enabled",
    "screen reader interval": "screen_reader.interval_seconds",
    "screen interval": "screen_reader.interval_seconds",
    "cognitive monitor": "cognitive_monitor.enabled",
    "cognitive monitoring": "cognitive_monitor.enabled",
    "cognitive load monitor": "cognitive_monitor.enabled",
    "productivity guardian": "productivity_guardian.enabled",
    "distraction detector": "productivity_guardian.enabled",
    "focus mode timer": "focus_mode.work_minutes",
    "pomodoro timer": "focus_mode.work_minutes",
    "work timer": "focus_mode.work_minutes",
    "morning briefing": "autonomous_planner.morning_briefing_time",
    "evening review": "autonomous_planner.evening_review_time",
    "morning briefing time": "autonomous_planner.morning_briefing_time",
    "evening review time": "autonomous_planner.evening_review_time",
    "llm model": "llm.model",
    "model": "llm.model",
    "ai model": "llm.model",
    "local model": "llm.model",
    "response model": "llm.model",
    "vision model": "llm.vision_model",
    "temperature": "llm.temperature",
    "voice mode": "voice.mode",
    "tts": "voice.tts_enabled",
    "text to speech": "voice.tts_enabled",
    "overlay": "ui.overlay_enabled",
    "theme": "ui.theme",
    "dark mode": "ui.theme",
    "audit log": "security.audit_log_enabled",
    "data masking": "security.sensitive_data_masking",
    "planner": "autonomous_planner.enabled",
    "autonomous planner": "autonomous_planner.enabled",
    # Messenger
    "messenger": "messenger.enabled",
    "messaging": "messenger.enabled",
    "default platform": "messenger.default_platform",
    "auto reply": "messenger.auto_reply_enabled",
    "auto-reply": "messenger.auto_reply_enabled",
    "chatbot reply": "messenger.auto_reply_enabled",
    # Media control
    "media control": "media_control.enabled",
    "music control": "media_control.enabled",
    "default player": "media_control.default_player",
    "music player": "media_control.default_player",
    # Notifier
    "notifications": "notifier.enabled",
    "notification": "notifier.enabled",
    "notifier": "notifier.enabled",
    "read notifications": "notifier.read_aloud",
    "read aloud": "notifier.read_aloud",
    "notification listener": "notifier.listener_enabled",
    "smart suggestions": "notifier.smart_suggestions",
}


def parse_setting_command(text: str) -> Optional[Tuple[str, Any]]:
    """Parse a natural language settings command.

    Returns (settings_key, new_value) if parsed, else None.

    Examples:
        "turn off emotion detector"     → ("emotion.enabled", False)
        "set screen reader interval to 120" → ("screen_reader.interval_seconds", 120)
        "switch model to mistral"       → ("llm.model", "mistral")
        "enable productivity guardian"  → ("productivity_guardian.enabled", True)
    """
    import re
    text = text.strip().lower()

    # Pattern: turn on/off <feature>
    m = re.match(r"(?:turn|switch)\s+(on|off)\s+(?:the\s+)?(.+)", text)
    if m:
        state = m.group(1) == "on"
        feature = m.group(2).strip()
        key = _NL_ALIASES.get(feature)
        if key and key.endswith(".enabled"):
            return key, state

    # Pattern: enable/disable <feature>
    m = re.match(r"(enable|disable)\s+(?:the\s+)?(.+)", text)
    if m:
        state = m.group(1) == "enable"
        feature = m.group(2).strip()
        key = _NL_ALIASES.get(feature)
        if key and key.endswith(".enabled"):
            return key, state

    # Pattern: set/change <feature> to <value>
    m = re.match(r"(?:set|change|update)\s+(?:the\s+)?(.+?)\s+to\s+(.+)", text)
    if m:
        feature = m.group(1).strip()
        value_str = m.group(2).strip()
        key = _NL_ALIASES.get(feature)
        if key:
            value = _parse_value(value_str, key)
            return key, value

    # Pattern: switch model to <name>
    m = re.match(r"(?:switch|use)\s+(?:the\s+)?(?:model|llm)\s+(?:to\s+)?(.+)", text)
    if m:
        model_name = m.group(1).strip()
        return "llm.model", model_name

    # Pattern: <feature> <value> (compact)
    for alias, key in _NL_ALIASES.items():
        if text.startswith(alias):
            rest = text[len(alias):].strip()
            if rest:
                value = _parse_value(rest, key)
                return key, value

    return None


def _parse_value(value_str: str, key: str) -> Any:
    """Parse a value string into the correct type for the given key."""
    v = value_str.strip().lower()

    # Boolean
    if v in {"on", "true", "yes", "enable", "enabled", "1"}:
        return True
    if v in {"off", "false", "no", "disable", "disabled", "0"}:
        return False

    # Integer (for intervals, thresholds, etc.)
    if key.endswith(("_seconds", "_minutes", "_size", "_threshold", "_index")):
        try:
            return int(float(v.split()[0]))
        except (ValueError, IndexError):
            pass

    # Float (for temperature, etc.)
    if key.endswith("temperature") or key.endswith("stddev") or key.endswith("score"):
        try:
            return float(v)
        except ValueError:
            pass

    # Theme
    if key.endswith("theme"):
        return "dark" if "dark" in v else "light"

    # String (model names, time strings, etc.)
    return value_str.strip()


# ─────────────────────────────────────────────────────────────────────────────
# Settings-aware env var bridge
# ─────────────────────────────────────────────────────────────────────────────

def apply_to_env() -> None:
    """Push current settings into environment variables.

    Called at startup in main.py so existing env-var-based modules
    pick up settings without code changes.
    """
    s = get_all()

    # Emotion detector
    emo = s.get("emotion", {})
    os.environ["EMOTION_DETECTOR"] = "1" if emo.get("enabled", True) else "0"
    os.environ["EMOTION_INTERVAL_SECONDS"] = str(emo.get("interval_seconds", 60))
    os.environ["EMOTION_WINDOW_MINUTES"] = str(emo.get("window_minutes", 60))
    os.environ["EMOTION_CAMERA_INDEX"] = str(emo.get("camera_index", 0))
    os.environ["EMOTION_REQUIRE_FACE"] = "1" if emo.get("require_face", True) else "0"
    os.environ["EMOTION_MIN_FRAME_STDDEV"] = str(emo.get("min_frame_stddev", 5.0))
    os.environ["EMOTION_LOG_SAMPLES"] = "1" if emo.get("log_samples", True) else "0"
    os.environ["EMOTION_SAVE_LAST_FRAME"] = "1" if emo.get("save_last_frame", False) else "0"
    os.environ["EMOTION_RULE_SPEAK"] = "1" if emo.get("rule_speak", False) else "0"
    os.environ["EMOTION_RULE_STATES"] = emo.get("rule_states", "tired,stressed,frustrated")
    os.environ["EMOTION_RULE_MODE"] = emo.get("rule_mode", "anytime_sustained")
    os.environ["EMOTION_RULE_NIGHT_START"] = str(emo.get("rule_night_start", 21))
    os.environ["EMOTION_RULE_NIGHT_END"] = str(emo.get("rule_night_end", 6))
    os.environ["EMOTION_RULE_TASKS_TO_MOVE"] = str(emo.get("rule_tasks_to_move", 2))
    os.environ["EMOTION_RULE_MIN_SCORE"] = str(emo.get("rule_min_score", 35))
    os.environ["EMOTION_RULE_SUSTAINED_SAMPLES"] = str(emo.get("rule_sustained_samples", 3))
    os.environ["EMOTION_RULE_SUSTAINED_MINUTES"] = str(emo.get("rule_sustained_minutes", 25))
    os.environ["EMOTION_RULE_COOLDOWN_MIN"] = str(emo.get("rule_cooldown_minutes", 45))

    # Screen reader
    sr = s.get("screen_reader", {})
    os.environ["SCREEN_READER_DISABLED"] = "0" if sr.get("enabled", True) else "1"
    os.environ["SCREEN_READER_INTERVAL"] = str(sr.get("interval_seconds", 60))
    os.environ["SCREEN_READER_HISTORY"] = str(sr.get("history_size", 5))
    os.environ["SCREEN_READER_STUCK_MINUTES"] = str(sr.get("stuck_minutes", 20))
    os.environ["SCREEN_READER_DEBUG"] = "1" if sr.get("debug", False) else "0"
    os.environ["SCREEN_READER_LOG_SUMMARIES"] = "1" if sr.get("log_summaries", True) else "0"

    # LLM
    llm = s.get("llm", {})
    os.environ["SCREEN_READER_MODEL"] = llm.get("vision_model", "llava:7b")
    os.environ["SCREEN_READER_OLLAMA_URL"] = llm.get("ollama_url", "http://localhost:11434")

    # Voice
    voice = s.get("voice", {})
    os.environ["VOICE_MODE"] = voice.get("mode", "wakeword")
    os.environ["VOICE_MODEL"] = voice.get("stt_model", "base")
    os.environ["VOICE_SESSION_SECONDS"] = str(voice.get("session_seconds", 25))
    os.environ["VOICE_SESSION_MAX_EMPTY"] = str(voice.get("max_empty_listens", 2))


# ─────────────────────────────────────────────────────────────────────────────
# Initialise settings file on first import
# ─────────────────────────────────────────────────────────────────────────────

def _init_settings_file() -> None:
    """Create the settings file if it doesn't exist."""
    if not os.path.exists(_SETTINGS_PATH):
        _save(deepcopy(_DEFAULTS))

_init_settings_file()
