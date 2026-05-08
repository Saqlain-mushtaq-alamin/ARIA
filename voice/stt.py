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

    lower = cleaned.lower()

    # Normalise volume commands.
    lower = re.sub(r"\bset\s+volume\s+(?:at|to)\s+(\d{1,3})\b",    r"set volume \1", lower)
    lower = re.sub(r"\bchange\s+volume\s+(?:at|to)\s+(\d{1,3})\b", r"set volume \1", lower)

    # Common STT confusions.
    replacements = {
        "bad pad":     "notepad",
        "note pad":    "notepad",
        "noteeped":    "notepad",
        "task maneger":"task manager",
        "taskmanager": "task manager",
        "fire fox":    "firefox",
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
        result = _postprocess_command(raw)
        if debug and result:
            print(f"[stt] heard: {result!r}")
        return result

    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
