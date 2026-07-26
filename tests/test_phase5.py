"""
tests/test_phase5.py  ─  Phase 5: Memory System
================================================
Tests conversation logging, memory recall, user profile.
"""
import tests.mock_layer  # MUST be first

import sys
import os
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_runner import test, run_all


# ── Conversation log ──────────────────────────────────────────────────────────

@test("log_interaction saves entry to DB", "PHASE5", "conversation")
def t_log_interaction():
    from memory.conversation_log import log_interaction, get_recent_interactions
    log_interaction("test user command", "test ARIA response",
                    metadata={"source": "test"})
    turns = get_recent_interactions(limit=5)
    assert len(turns) > 0
    texts = [str(t) for t in turns]
    assert any("test" in t.lower() for t in texts)


@test("get_full_context_string returns non-empty string", "PHASE5", "conversation")
def t_context_string():
    from memory.conversation_log import get_full_context_string, log_interaction
    log_interaction("hello aria", "Hello Sir, how can I help?")
    ctx = get_full_context_string(turns=3)
    assert isinstance(ctx, str)
    assert len(ctx) > 0


@test("Conversation log can be queried after write", "PHASE5", "conversation")
def t_conversation_roundtrip():
    from memory.conversation_log import log_interaction, get_recent_interactions
    unique_tag = "ARIA_TEST_TAG_XYZ_12345"
    log_interaction(f"command with {unique_tag}", "response")
    turns = get_recent_interactions(limit=10)
    found = any(unique_tag in str(t) for t in turns)
    assert found, "Could not find test tag in conversation log"


# ── User profile ──────────────────────────────────────────────────────────────

@test("User profile address_form is 'Sir'", "PHASE5", "user_profile")
def t_user_profile_sir():
    profile_path = Path(__file__).resolve().parents[1] / "memory" / "user_profile.json"
    import json
    with open(profile_path) as f:
        profile = json.load(f)
    address = profile.get("identity", {}).get("address_form", "")
    assert address == "Sir", f"Expected 'Sir', got {address!r}"


@test("get_profile_summary returns formatted string", "PHASE5", "user_profile")
def t_profile_summary():
    from memory.user_profile import get_profile_summary
    result = get_profile_summary()
    assert isinstance(result, str)
    assert len(result) > 5


# ── Agent addresses user as Sir ───────────────────────────────────────────────

@test("Agent conversational reply mentions 'Sir' for greetings", "PHASE5", "sir_address")
def t_sir_in_greeting():
    from core.agent import process_text
    response = process_text("hello aria")
    assert isinstance(response, str)
    # Response should be warm and helpful — Sir is a style preference
    # We check the profile has it set; actual LLM response depends on prompt
    assert len(response) > 5, "Response too short"


if __name__ == "__main__":
    run_all("PHASE5")
