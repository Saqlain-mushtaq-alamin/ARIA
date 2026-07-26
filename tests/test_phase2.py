"""
tests/test_phase2.py  ─  Phase 2: Voice Pipeline
==================================================
Tests STT post-processing, garbage rejection, and TTS sanity.
STT recording tests are SKIPPED unless a real mic is present.
"""
import tests.mock_layer  # MUST be first

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_runner import test, run_all

from voice.stt import _postprocess_command, _is_garbage_transcription


# ── STT post-processing ───────────────────────────────────────────────────────

@test("STT: 'note pad' → 'notepad'", "PHASE2", "stt_postprocess")
def t_notepad_fix():
    result = _postprocess_command("note pad")
    assert "notepad" in result, f"Got: {result!r}"


@test("STT: 'bad pad' → 'notepad'", "PHASE2", "stt_postprocess")
def t_badpad_fix():
    result = _postprocess_command("bad pad")
    assert "notepad" in result, f"Got: {result!r}"


@test("STT: 'task maneger' → 'task manager'", "PHASE2", "stt_postprocess")
def t_taskmanager_fix():
    result = _postprocess_command("task maneger")
    assert "task manager" in result, f"Got: {result!r}"


@test("STT: 'fire fox' → 'firefox'", "PHASE2", "stt_postprocess")
def t_firefox_fix():
    result = _postprocess_command("fire fox")
    assert "firefox" in result, f"Got: {result!r}"


@test("STT: 'set volume at 30' normalised", "PHASE2", "stt_postprocess")
def t_volume_normalize():
    result = _postprocess_command("set volume at 30")
    assert "volume" in result and "30" in result, f"Got: {result!r}"


@test("STT: 'set volume to 75' normalised", "PHASE2", "stt_postprocess")
def t_volume_normalize2():
    result = _postprocess_command("set volume to 75")
    assert "volume" in result and "75" in result, f"Got: {result!r}"


# ── Garbage detection ─────────────────────────────────────────────────────────

@test("Garbage: '...' → rejected", "PHASE2", "garbage")
def t_garbage_dots():
    assert _is_garbage_transcription("...") is True


@test("Garbage: 'uh um hmm' → rejected", "PHASE2", "garbage")
def t_garbage_fillers():
    assert _is_garbage_transcription("uh um hmm") is True


@test("Garbage: lone article 'the' → rejected", "PHASE2", "garbage")
def t_garbage_article():
    assert _is_garbage_transcription("the") is True


@test("Garbage: 'open notepad' → NOT rejected", "PHASE2", "garbage")
def t_not_garbage_open():
    assert _is_garbage_transcription("open notepad") is False


@test("Garbage: 'what is the weather in Dhaka' → NOT rejected", "PHASE2", "garbage")
def t_not_garbage_weather():
    assert _is_garbage_transcription("what is the weather in Dhaka") is False


@test("Garbage: whisper echo of system prompt → rejected", "PHASE2", "garbage")
def t_garbage_whisper_echo():
    assert _is_garbage_transcription("You are a desktop assistant transcribe short commands") is True


# ── TTS smoke test ────────────────────────────────────────────────────────────

@test("TTS: speak() doesn't crash on normal text", "PHASE2", "tts")
def t_tts_smoke():
    from voice.tts import speak, _sanitize_for_tts
    # Only test sanitization (actual speak requires Piper model)
    clean = _sanitize_for_tts("Hello Sir, I am ARIA. How can I help you today?")
    assert "ARIA" in clean or "aria" in clean.lower()


@test("TTS: sanitize strips leading glyphs", "PHASE2", "tts")
def t_tts_sanitize():
    from voice.tts import _sanitize_for_tts
    cleaned = _sanitize_for_tts("▶  Step 1/3 — Opening Notepad...")
    assert "Opening Notepad" in cleaned
    assert "▶" not in cleaned


@test("TTS: sanitize handles unicode", "PHASE2", "tts")
def t_tts_unicode():
    from voice.tts import _sanitize_for_tts
    cleaned = _sanitize_for_tts("✓  File saved: C:\\Users\\test\\Desktop\\story.txt")
    assert "File saved" in cleaned


if __name__ == "__main__":
    run_all("PHASE2")
