"""
voice/stt.py
============
Speech-to-text using Whisper, backed by voice.audio_utils.

Changes from original
---------------------
- Recording delegated entirely to audio_utils.record_speech()
- Whisper model cache is thread-safe
- _postprocess_command() preserved exactly
- listen_and_transcribe() respects mic_session so it never
  clashes with the wake-word listener
"""

from __future__ import annotations

import difflib
import os
import re
import tempfile
import threading
from typing import Any, Optional, Protocol, cast

import numpy as np

from voice.audio_utils import (
    record_speech,
    select_microphone,
    wav_duration,
    _env_bool,
)

# ---------------------------------------------------------------------------
# Whisper model cache (thread-safe)
# ---------------------------------------------------------------------------

class _WhisperModel(Protocol):
    def transcribe(
        self,
        audio: Any,
        *,
        fp16: bool,
        language: str,
        initial_prompt: str | None,
    ) -> dict[str, Any]: ...


_MODELS: dict[str, _WhisperModel] = {}
_MODEL_LOCK = threading.Lock()


def _get_whisper_model(model_name: str) -> _WhisperModel:
    with _MODEL_LOCK:
        if model_name in _MODELS:
            return _MODELS[model_name]
        try:
            import whisper
        except ImportError as exc:
            raise ImportError("Run: pip install openai-whisper") from exc

        model = cast(_WhisperModel, whisper.load_model(model_name))
        _MODELS[model_name] = model
        return model

# ---------------------------------------------------------------------------
# WAV helpers
# ---------------------------------------------------------------------------

def _load_wav_mono_16k(path: str) -> np.ndarray:
    import wave
    with wave.open(path, "rb") as wf:
        if wf.getnchannels() != 1:
            raise ValueError("Audio must be mono")
        if wf.getframerate() != 16_000:
            raise ValueError("Audio must be 16 kHz")
        frames = wf.readframes(wf.getnframes())
    samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32)
    return samples / 32_768.0

# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

_DEFAULT_PROMPT = (
    "You are a desktop assistant. Transcribe short commands like: "
    "open notepad, open settings, open calculator, open chrome, open firefox, "
    "open task manager, close notepad, set volume 10, search on google."
)


def transcribe_wav(
    path: str,
    model_name: str = "base",
    language: str = "en",
    initial_prompt: str | None = None,
) -> str:
    """Transcribe a 16 kHz mono WAV file using Whisper (no ffmpeg needed)."""
    audio  = _load_wav_mono_16k(path)
    model  = _get_whisper_model(model_name)
    result = model.transcribe(
        audio,
        fp16=False,
        language=language,
        initial_prompt=initial_prompt,
    )
    return str(result.get("text", "")).strip()

# ---------------------------------------------------------------------------
# Post-processing
# ---------------------------------------------------------------------------

def _postprocess_command(text: str) -> str:
    """Light cleanup for command-style speech transcriptions."""
    cleaned = " ".join((text or "").strip().split())
    if not cleaned:
        return ""

    # ── Garbage / noise detection ──────────────────────────────────────
    # Reject transcriptions that are clearly not valid commands.
    if _is_garbage_transcription(cleaned):
        return ""

    lower = cleaned.lower()

    # Normalise volume commands.
    lower = re.sub(r"\bset\s+volume\s+(?:at|to)\s+(\d{1,3})\b",    r"set volume \1", lower)
    lower = re.sub(r"\bchange\s+volume\s+(?:at|to)\s+(\d{1,3})\b", r"set volume \1", lower)
    lower = re.sub(r"\bvolume\s+(?:at|to)\s+(\d{1,3})\b",          r"set volume \1", lower)
    lower = re.sub(r"\bturn\s+(?:the\s+)?volume\s+(?:up|down)\s+to\s+(\d{1,3})\b", r"set volume \1", lower)

    # Common STT confusions — app names and verbs
    replacements = {
        # Notepad variants
        "bad pad":      "notepad",
        "note pad":     "notepad",
        "noteeped":     "notepad",
        "node pad":     "notepad",
        "no pad":       "notepad",
        "not pad":      "notepad",
        "noted pad":    "notepad",
        "notepadd":     "notepad",
        "notes pad":    "notepad",
        # Task manager
        "task maneger": "task manager",
        "taskmanager":  "task manager",
        "task manger":  "task manager",
        "task manage":  "task manager",
        "task management": "task manager",
        # Firefox
        "fire fox":     "firefox",
        "fire-fox":     "firefox",
        "fire for":     "firefox",
        # Chrome
        "chrome browser": "chrome",
        "google crome": "chrome",
        "crome":        "chrome",
        # File Explorer
        "file explore": "file explorer",
        "file explores": "file explorer",
        "file explorer app": "file explorer",
        "fax plorer":   "file explorer",
        "fire explorer": "file explorer",
        # Calculator
        "calculated":   "calculator",
        "calculater":   "calculator",
        "calculates":   "calculator",
        # Settings
        "setting":      "settings",
        # Volume words
        "voulume":      "volume",
        "volum":        "volume",
        "vollume":      "volume",
        # Open variants
        "opan":         "open",
        "ope":          "open",
        "oben":         "open",
        # Close variants
        "clothes":      "close",
        "clause":       "close",
        # Search variants
        "such":         "search",
        "serch":        "search",
        "search for":   "search",
        # Turn on/off WiFi
        "turn off why fi": "turn off wifi",
        "turn on why fi":  "turn on wifi",
        "turn off wy fi":  "turn off wifi",
        "wi fi":        "wifi",
        "why fi":       "wifi",
        "wy fi":        "wifi",
        # Shutdown
        "shut down":    "shutdown",
        "shout down":   "shutdown",
        "shot down":    "shutdown",
        # Screenshot
        "screen shot":  "screenshot",
        "screen capture": "screenshot",
        # Spotify
        "spotifiy":     "spotify",
        "spot if i":    "spotify",
        # Discord
        "disc cord":    "discord",
        # Write / type
        "right":        "write",   # only when alone
        # Brightness
        "brightness to": "set brightness to",
        # "and then" normalisation
        "and den":      "and then",
        "and zen":      "and then",
    }
    for wrong, right in replacements.items():
        lower = re.sub(rf"\b{re.escape(wrong)}\b", right, lower)

    # Map spoken web targets to URLs.
    lower = re.sub(r"^open\s+google\s+maps\b", "open https://maps.google.com", lower)
    lower = re.sub(r"^open\s+maps\b",          "open https://maps.google.com", lower)
    lower = re.sub(r"^open\s+youtube\b",       "open https://www.youtube.com", lower)

    # Fuzzy-match app names.
    try:
        from modules import system_control
        known = sorted(system_control.APP_ALIASES.keys())
    except Exception:
        known = []

    m = re.match(r"^(open|close)\s+(.+)$", lower)
    if m and known:
        verb   = m.group(1)
        target = m.group(2).strip().rstrip(" .,!?:;")
        if target and target not in known and "." not in target:
            best = difflib.get_close_matches(target, known, n=1, cutoff=0.72)
            if best:
                return f"{verb} {best[0]}"

    return lower



def _is_garbage_transcription(text: str) -> bool:
    """Detect garbage/noise transcriptions that should be rejected.

    Whisper sometimes produces nonsensical output from background noise,
    keyboard clicks, or microphone artifacts. This function catches the most
    common patterns:
      - Very short or very long gibberish
      - High ratio of non-dictionary words
      - Known noise artifacts (repeated characters, no vowels, etc.)
    """
    if not text or len(text) < 2:
        return True

    lower = text.strip().lower()
    words = lower.split()

    # Single nonsense word (not a known command verb)
    command_verbs = {
        "open", "close", "set", "search", "play", "pause", "stop", "skip",
        "send", "read", "show", "help", "hey", "hi", "hello", "what",
        "who", "where", "when", "how", "why", "yes", "no", "ok", "cancel",
        "confirm", "delete", "create", "save", "type", "write", "move",
        "copy", "download", "schedule", "turn", "toggle", "shut", "lock",
        "sleep", "restart", "exit", "quit", "volume", "brightness", "mute",
    }
    if len(words) == 1 and words[0] not in command_verbs and len(words[0]) < 3:
        return True

    # Known noise patterns from Whisper
    noise_patterns = [
        r"^\.+$",                          # Just dots
        r"^\*+$",                          # Just asterisks
        r"^[\-\.\,\!\?\s]+$",             # Just punctuation
        r"^(uh|um|hmm|huh|ah|eh|oh|mm|er|like|you know)(\s+(uh|um|hmm|huh|ah|eh|oh|mm|er|like|you know))*\s*$",  # Filler sounds (with spaces)
        r"^(the|a|an|is|it|to|of|in)\s*$",  # Lone articles/prepositions
    ]
    for pattern in noise_patterns:
        if re.match(pattern, lower):
            return True

    # Too many non-English-looking words (high consonant density + uncommon)
    _COMMON_WORDS = {
        "the","be","to","of","and","a","in","that","have","i","it","for","not",
        "on","with","he","as","you","do","at","this","but","his","by","from",
        "they","we","say","her","she","or","an","will","my","one","all","would",
        "there","their","what","so","up","out","if","about","who","get","which",
        "go","me","when","make","can","like","time","no","just","him","know",
        "take","people","into","year","your","good","some","could","them","see",
        "other","than","then","now","look","only","come","its","over","think",
        "also","back","after","use","two","how","our","work","first","well",
        "way","even","new","want","because","any","these","give","day","most",
        "us","open","close","set","search","play","pause","stop","skip","send",
        "read","show","help","hey","hi","hello","delete","create","save","type",
        "write","move","copy","download","schedule","turn","toggle","shut","lock",
        "sleep","restart","exit","quit","volume","brightness","mute","file","app",
        "yes","no","ok","cancel","confirm","please","start","find","run","check",
        "notepad","chrome","firefox","spotify","weather","news","music","video",
    }
    def _looks_like_word(w: str) -> bool:
        """Check if a word looks remotely English."""
        w = re.sub(r"[^a-z]", "", w.lower())
        if not w or len(w) < 2:
            return False
        # Known common word
        if w in _COMMON_WORDS:
            return True
        vowels = sum(1 for c in w if c in "aeiou")
        if vowels == 0 and len(w) > 2:
            return False  # No vowels
        # Reject words with 4+ consecutive consonants (xroiency, ptensor)
        if re.search(r"[^aeiou]{4,}", w):
            return False
        # Reject very high consonant ratio
        if len(w) > 3 and vowels / len(w) < 0.2:
            return False
        return True

    if len(words) >= 3:
        real_words = sum(1 for w in words if _looks_like_word(w))
        ratio = real_words / len(words)
        if ratio < 0.5:
            return True  # More than 50% gibberish words

    # Whisper echo of system prompt
    if "desktop assistant" in lower or "transcribe short commands" in lower:
        return True

    return False

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def listen_and_transcribe(
    model_name: str = "base",
    input_device_index: Optional[int] = None,
) -> str:
    """
    Record speech from the microphone and return the transcribed text.

    Returns an empty string when no audible speech was detected.
    """
    debug      = _env_bool("VOICE_DEBUG", False)
    device_idx = select_microphone(input_device_index)
    language   = os.getenv("VOICE_LANGUAGE", "en")
    prompt     = os.getenv("VOICE_INITIAL_PROMPT") or _DEFAULT_PROMPT

    # Write to a temp file; always delete it when done.
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        record_speech(tmp_path, device_index=device_idx)

        dur = wav_duration(tmp_path)
        if debug:
            print(f"[stt] recorded {dur:.2f}s")
        if dur < 0.25:
            return ""  # Too short — almost certainly silence / noise.

        raw = transcribe_wav(
            tmp_path,
            model_name=model_name,
            language=language,
            initial_prompt=prompt,
        )
        if raw:
            raw_l = raw.strip().lower()
            if raw_l.startswith("you are a desktop assistant") or "transcribe short commands" in raw_l:
                return ""
        result = _postprocess_command(raw)
        if debug and result:
            print(f"[stt] heard: {result!r}")
        return result

    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
