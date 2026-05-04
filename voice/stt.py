"""Speech-to-text using Whisper with simple silence detection."""

from __future__ import annotations

import os
import tempfile
import wave
from typing import Optional
import difflib
import re

import numpy as np
import pyaudio


_WHISPER_MODELS: dict[str, object] = {}


def _rms(audio_chunk: bytes) -> float:
    samples = np.frombuffer(audio_chunk, dtype=np.int16).astype(np.float32)
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples * samples)))


def record_until_silence(
    output_path: str,
    rate: int = 16000,
    chunk: int = 1024,
    silence_seconds: float = 1.0,
    max_seconds: float = 15.0,
    threshold: float = 500.0,
    input_device_index: int | None = None,
) -> None:
    """Record audio to a WAV file until silence is detected."""
    audio = pyaudio.PyAudio()
    stream = audio.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=rate,
        input=True,
        input_device_index=input_device_index,
        frames_per_buffer=chunk,
    )

    frames: list[bytes] = []
    silence_limit = int(silence_seconds * rate / chunk)
    max_chunks = int(max_seconds * rate / chunk)
    silence_chunks = 0
    started = False

    try:
        for _ in range(max_chunks):
            data = stream.read(chunk, exception_on_overflow=False)
            volume = _rms(data)

            if volume > threshold:
                started = True
                silence_chunks = 0
                frames.append(data)
                continue

            if started:
                frames.append(data)
                silence_chunks += 1
                if silence_chunks >= silence_limit:
                    break
    finally:
        stream.stop_stream()
        stream.close()
        audio.terminate()

    with wave.open(output_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(audio.get_sample_size(pyaudio.paInt16))
        wf.setframerate(rate)
        wf.writeframes(b"".join(frames))


def _load_wav_mono_16k(path: str) -> np.ndarray:
    with wave.open(path, "rb") as wf:
        if wf.getnchannels() != 1:
            raise ValueError("Audio must be mono")
        if wf.getframerate() != 16000:
            raise ValueError("Audio must be 16kHz")
        frames = wf.readframes(wf.getnframes())

    samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32)
    return samples / 32768.0


def transcribe_wav(
    path: str,
    model_name: str = "base",
    language: str = "en",
    initial_prompt: str | None = None,
) -> str:
    """Transcribe a WAV file using Whisper without ffmpeg."""
    try:
        import whisper
    except ImportError as exc:
        raise ImportError(
            "Whisper is not installed. Run: pip install openai-whisper"
        ) from exc

    audio = _load_wav_mono_16k(path)
    model = _WHISPER_MODELS.get(model_name)
    if model is None:
        model = whisper.load_model(model_name)
        _WHISPER_MODELS[model_name] = model
    result = model.transcribe(
        audio,
        fp16=False,
        language=language,
        initial_prompt=initial_prompt,
    )
    return str(result.get("text", "")).strip()


def _postprocess_command(text: str) -> str:
    """Light cleanup for command-style speech.

    Focus: fix common mishears (e.g., "bad pad" -> "notepad") and
    improve app-name recognition in short commands.
    """
    cleaned = " ".join((text or "").strip().split())
    if not cleaned:
        return ""

    lower = cleaned.lower()

    # Normalize common command variants.
    lower = re.sub(r"\bset\s+volume\s+(?:at|to)\s+(\d{1,3})\b", r"set volume \1", lower)
    lower = re.sub(r"\bchange\s+volume\s+(?:at|to)\s+(\d{1,3})\b", r"set volume \1", lower)

    # Common STT confusions for short commands.
    replacements = {
        "bad pad": "notepad",
        "note pad": "notepad",
        "noteeped": "notepad",
        "task maneger": "task manager",
        "taskmanager": "task manager",
        "fire fox": "firefox",
        "google": "google",
    }
    for wrong, right in replacements.items():
        lower = re.sub(rf"\b{re.escape(wrong)}\b", right, lower)

    # Map a few common spoken web targets into URLs.
    lower = re.sub(r"^open\s+google\s+maps\b", "open https://maps.google.com", lower)
    lower = re.sub(r"^open\s+maps\b", "open https://maps.google.com", lower)
    lower = re.sub(r"^open\s+youtube\b", "open https://www.youtube.com", lower)

    # If the user said "open X" or "close X", fuzzy match X to known apps.
    try:
        from modules import system_control

        known = sorted(system_control.APP_ALIASES.keys())
    except Exception:
        known = []

    m = re.match(r"^(open|close)\s+(.+)$", lower)
    if m and known:
        verb = m.group(1)
        target = m.group(2).strip().rstrip(" .,!?:;")
        if target and target not in known and "." not in target:
            best = difflib.get_close_matches(target, known, n=1, cutoff=0.72)
            if best:
                return f"{verb} {best[0]}"

    return lower


def listen_and_transcribe(
    model_name: str = "small",
    input_device_index: int | None = None,
) -> str:
    """Record until silence and return transcribed text."""
    temp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            temp_path = tmp.name

        # A slightly higher silence threshold tends to help command-style speech.
        threshold = float(os.getenv("VOICE_SILENCE_THRESHOLD", "650"))
        record_until_silence(
            temp_path,
            threshold=threshold,
            input_device_index=input_device_index,
        )

        prompt = (
            "You are a desktop assistant. Transcribe short commands like: "
            "open notepad, open settings, open calculator, open chrome, open firefox, "
            "open task manager, close notepad, set volume 10, search on google."
        )
        raw = transcribe_wav(
            temp_path,
            model_name=model_name,
            language=os.getenv("VOICE_LANGUAGE", "en"),
            initial_prompt=os.getenv("VOICE_INITIAL_PROMPT") or prompt,
        )
        return _postprocess_command(raw)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
