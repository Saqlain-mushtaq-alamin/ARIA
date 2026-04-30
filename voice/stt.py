"""Speech-to-text using Whisper with simple silence detection."""

from __future__ import annotations

import os
import tempfile
import wave
from typing import Optional

import numpy as np
import pyaudio


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
) -> None:
    """Record audio to a WAV file until silence is detected."""
    audio = pyaudio.PyAudio()
    stream = audio.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=rate,
        input=True,
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


def transcribe_wav(path: str, model_name: str = "base") -> str:
    """Transcribe a WAV file using Whisper."""
    try:
        import whisper
    except ImportError as exc:
        raise ImportError(
            "Whisper is not installed. Run: pip install openai-whisper"
        ) from exc

    model = whisper.load_model(model_name)
    result = model.transcribe(path, fp16=False)
    return str(result.get("text", "")).strip()


def listen_and_transcribe(model_name: str = "base") -> str:
    """Record until silence and return transcribed text."""
    temp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            temp_path = tmp.name
        record_until_silence(temp_path)
        return transcribe_wav(temp_path, model_name=model_name)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
