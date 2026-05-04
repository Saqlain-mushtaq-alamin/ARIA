"""Speech-to-text using Whisper with simple silence detection."""

from __future__ import annotations

import os
import tempfile
import wave
from typing import Any, Optional, Protocol, cast
import difflib
import re

import numpy as np
import pyaudio


class _WhisperModel(Protocol):
    def transcribe(
        self,
        audio: Any,
        *,
        fp16: bool,
        language: str,
        initial_prompt: str | None,
    ) -> dict[str, Any]: ...


_WHISPER_MODELS: dict[str, _WhisperModel] = {}


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
    threshold: float | None = 500.0,
    input_device_index: int | None = None,
    pre_roll_seconds: float = 0.35,
    min_speech_seconds: float = 0.45,
    auto_threshold: bool = False,
    threshold_factor: float = 2.6,
    threshold_floor: float = 180.0,
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
    pre_roll: list[bytes] = []
    silence_limit = int(silence_seconds * rate / chunk)
    max_chunks = int(max_seconds * rate / chunk)
    pre_roll_limit = max(0, int(pre_roll_seconds * rate / chunk))
    min_speech_chunks = max(0, int(min_speech_seconds * rate / chunk))
    silence_chunks = 0
    started = False
    speech_chunks = 0

    if auto_threshold:
        # Calibrate on background noise before speech starts.
        calibrate_chunks = max(1, int(0.35 * rate / chunk))
        baseline: list[float] = []
        for _ in range(min(calibrate_chunks, max_chunks)):
            data = stream.read(chunk, exception_on_overflow=False)
            baseline.append(_rms(data))
            if pre_roll_limit:
                pre_roll.append(data)
                if len(pre_roll) > pre_roll_limit:
                    pre_roll = pre_roll[-pre_roll_limit:]
        noise = float(np.median(np.array(baseline, dtype=np.float32))) if baseline else 0.0
        threshold = max(threshold_floor, noise * float(threshold_factor))

    try:
        for _ in range(max_chunks):
            data = stream.read(chunk, exception_on_overflow=False)
            volume = _rms(data)

            if not started and pre_roll_limit:
                pre_roll.append(data)
                if len(pre_roll) > pre_roll_limit:
                    pre_roll = pre_roll[-pre_roll_limit:]

            active_threshold = float(threshold or 0.0)
            if volume > active_threshold:
                started = True
                if speech_chunks == 0 and pre_roll:
                    frames.extend(pre_roll)
                    pre_roll = []
                silence_chunks = 0
                frames.append(data)
                speech_chunks += 1
                continue

            if started:
                frames.append(data)
                speech_chunks += 1
                silence_chunks += 1
                if speech_chunks >= min_speech_chunks and silence_chunks >= silence_limit:
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


def _wav_duration_seconds(path: str) -> float:
    try:
        with wave.open(path, "rb") as wf:
            rate = wf.getframerate() or 16000
            frames = wf.getnframes() or 0
        return float(frames) / float(rate)
    except Exception:
        return 0.0


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
        loaded = whisper.load_model(model_name)
        model = cast(_WhisperModel, loaded)
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

        auto_threshold = os.getenv("VOICE_AUTO_THRESHOLD", "1") == "1"
        threshold = float(os.getenv("VOICE_SILENCE_THRESHOLD", "450"))
        silence_seconds = float(os.getenv("VOICE_SILENCE_SECONDS", "0.8"))
        max_seconds = float(os.getenv("VOICE_MAX_SECONDS", "12"))
        pre_roll_seconds = float(os.getenv("VOICE_PRE_ROLL_SECONDS", "0.35"))
        min_speech_seconds = float(os.getenv("VOICE_MIN_SPEECH_SECONDS", "0.45"))
        threshold_factor = float(os.getenv("VOICE_THRESHOLD_FACTOR", "2.6"))
        threshold_floor = float(os.getenv("VOICE_THRESHOLD_FLOOR", "180"))

        if os.getenv("VOICE_DEBUG") == "1":
            mode = "auto" if auto_threshold else "fixed"
            print(
                f"[voice] recording mode={mode} silence={silence_seconds}s max={max_seconds}s "
                f"pre_roll={pre_roll_seconds}s min_speech={min_speech_seconds}s "
                f"threshold={threshold if not auto_threshold else 'auto'}"
            )

        record_until_silence(
            temp_path,
            silence_seconds=silence_seconds,
            max_seconds=max_seconds,
            threshold=None if auto_threshold else threshold,
            input_device_index=input_device_index,
            pre_roll_seconds=pre_roll_seconds,
            min_speech_seconds=min_speech_seconds,
            auto_threshold=auto_threshold,
            threshold_factor=threshold_factor,
            threshold_floor=threshold_floor,
        )

        dur = _wav_duration_seconds(temp_path)
        if os.getenv("VOICE_DEBUG") == "1":
            print(f"[voice] recorded {dur:.2f}s")
        if dur < 0.25:
            return ""

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
