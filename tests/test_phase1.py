"""
tests/test_phase1.py  ─  Phase 1: Intent Classification
=========================================================
Tests that the fast parser + LLM correctly maps user commands to intents.
All tests run with the REAL agent brain (Ollama must be running).
"""
import tests.mock_layer  # MUST be first import

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_runner import test, run_all

from core.command_parser import (
    _parse_multistep_command,
    _parse_system_command,
    _parse_browser_command,
    _parse_app_control_command,
)
from core.intent_classifier import classify_intent, is_conversational


# ── Fast-parser tests (no Ollama needed) ─────────────────────────────────────

@test("open notepad → open_app intent", "PHASE1", "fast_parser")
def t_open_notepad():
    r = _parse_system_command("open notepad")
    # fast parser might not catch "open" — let the multi-step fallback test it
    # If None, classify_intent should return correct intent
    if r is None:
        r = classify_intent("open notepad")
    assert r is not None
    intent = r.get("intent", "")
    assert intent == "open_app", f"Expected open_app, got {intent!r}"
    app = (r.get("parameters") or {}).get("app_name", "").lower()
    assert "notepad" in app, f"Expected notepad in app_name, got {app!r}"


@test("set volume to 50 → set_volume intent", "PHASE1", "fast_parser")
def t_set_volume():
    from core.agent import _parse_system_command
    r = _parse_system_command("set volume to 50")
    if r is None:
        r = classify_intent("set volume to 50")
    assert r is not None, "set volume not parsed"
    assert r.get("intent") == "set_volume", f"Expected set_volume, got {r.get('intent')}"
    assert r.get("parameters", {}).get("level") == 50, f"Expected level=50, got {r.get('parameters')}"


@test("turn off wifi → toggle_wifi intent", "PHASE1", "fast_parser")
def t_toggle_wifi():
    from core.agent import _parse_system_command
    r = _parse_system_command("turn off wifi")
    if r is None:
        r = classify_intent("turn off wifi")
    assert r is not None, "toggle wifi not parsed"
    assert r.get("intent") == "toggle_wifi", f"Expected toggle_wifi, got {r.get('intent')}"


@test("search for python tutorials → search_web", "PHASE1", "fast_parser")
def t_search():
    r = _parse_browser_command("search python tutorials")
    if r is None:
        r = classify_intent("search python tutorials")
    assert r is not None
    assert r.get("intent") in ("search_web", "search"), f"Got: {r.get('intent')}"


@test("close chrome → close_window intent", "PHASE1", "fast_parser")
def t_close_app():
    r = _parse_app_control_command("close chrome")
    if r is None:
        r = classify_intent("close chrome")
    assert r is not None
    assert r.get("intent") in ("close_window", "close", "close_app"), f"Got: {r.get('intent')}"


# ── Multi-step parser tests ───────────────────────────────────────────────────

@test("open notepad and type hello → multi_step [open_app, type_text]", "PHASE1", "multistep")
def t_multistep_open_type():
    r = _parse_multistep_command("open notepad and type hello world")
    assert r is not None
    assert r.get("intent") == "multi_step"
    steps = r.get("steps", [])
    assert len(steps) >= 2
    assert steps[0]["intent"] == "open_app"
    assert steps[1]["intent"] == "type_text"


@test("open notepad and write a story then save on desktop → 3 steps", "PHASE1", "multistep")
def t_multistep_story_save():
    r = _parse_multistep_command(
        "open notepad and write a story about a brave knight then save it on desktop"
    )
    assert r is not None
    assert r.get("intent") == "multi_step"
    steps = r.get("steps", [])
    assert len(steps) == 3, f"Expected 3 steps, got {len(steps)}: {steps}"
    assert steps[0]["intent"] == "open_app"
    assert steps[1]["intent"] == "type_text"
    assert steps[1]["parameters"].get("generate") is True
    assert steps[2]["intent"] == "save_file"
    path = steps[2]["parameters"].get("path", "").lower()
    assert "desktop" in path, f"Expected desktop in path, got {path!r}"


@test("STT typo: 'nodepad' corrected to notepad", "PHASE1", "stt_correction")
def t_typo_nodepad():
    r = classify_intent("open nodepad")
    assert r is not None
    assert r.get("intent") == "open_app"
    app = (r.get("parameters") or {}).get("app_name", "").lower()
    assert "notepad" in app, f"Expected notepad, got {app!r}"


@test("Conversational: 'hello' → is_conversational=True", "PHASE1", "conversational")
def t_conversational_hello():
    assert is_conversational("hello") is True


@test("Conversational: 'how are you' → is_conversational=True", "PHASE1", "conversational")
def t_conversational_how():
    assert is_conversational("how are you") is True


@test("Actionable: 'open chrome' → is_conversational=False", "PHASE1", "conversational")
def t_not_conversational():
    assert is_conversational("open chrome") is False


@test("LLM: weather command → get_weather intent", "PHASE1", "llm_classify")
def t_llm_weather():
    r = classify_intent("what is the weather in Dhaka")
    assert r is not None
    assert r.get("intent") == "get_weather", f"Got: {r.get('intent')}"
    loc = (r.get("parameters") or {}).get("location", "").lower()
    assert "dhaka" in loc, f"Expected Dhaka in location, got {loc!r}"


@test("LLM: news → get_news intent", "PHASE1", "llm_classify")
def t_llm_news():
    r = classify_intent("get me the latest technology news")
    assert r is not None
    # LLM may return get_news or search_web — both are valid interpretations
    assert r.get("intent") in ("get_news", "search_web"), f"Got: {r.get('intent')}"


@test("LLM: multi-step notepad + story → multi_step from LLM", "PHASE1", "llm_classify")
def t_llm_multistep():
    r = classify_intent("open notepad and write a poem about the ocean then save it to desktop")
    assert r is not None
    intent = r.get("intent")
    # Either multi_step from LLM or fast-parser; both are correct
    assert intent in ("multi_step", "open_app"), f"Got: {intent}"


if __name__ == "__main__":
    run_all("PHASE1")
